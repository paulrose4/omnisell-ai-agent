from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from starlette.requests import Request

from api.routes import reconciliation_routes, sync_routes, task_routes, work_item_routes


def _principal(
    *,
    role: str = "operator",
    scopes: frozenset[str] = frozenset({"read", "write"}),
) -> SimpleNamespace:
    return SimpleNamespace(user_id="operator-1", tenant_id="tenant-1", role=role, scopes=scopes)


def _request(
    *,
    state: Any,
    principal: Any | None = None,
    headers: dict[str, str] | None = None,
    request_id: str = "request-1",
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/test",
        "raw_path": b"/api/test",
        "query_string": b"",
        "headers": encoded_headers,
        "client": ("203.0.113.10", 443),
        "server": ("testserver", 443),
        "app": SimpleNamespace(state=state),
    }
    request = Request(scope)
    request.state.principal = principal or _principal()
    request.state.request_id = request_id
    return request


@pytest.mark.asyncio
async def test_reconciliation_run_enforces_role_connection_and_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = reconciliation_routes.RunRequest(connection_id="connection-1")
    response = Response()
    request = _request(
        state=SimpleNamespace(
            tenant_repository=SimpleNamespace(get_connection=lambda **_: None),
            task_repository=None,
            audit_logger=None,
        ),
        principal=_principal(role="viewer", scopes=frozenset({"read"})),
    )
    with pytest.raises(HTTPException) as forbidden:
        await reconciliation_routes.create_run(payload, request, response)
    assert forbidden.value.status_code == 403

    request.state.principal = _principal()
    with pytest.raises(HTTPException) as missing:
        await reconciliation_routes.create_run(payload, request, response)
    assert missing.value.status_code == 404

    connection = SimpleNamespace(id="connection-1")
    request.app.state.tenant_repository = SimpleNamespace(get_connection=lambda **_: connection)
    with pytest.raises(HTTPException) as idempotency:
        await reconciliation_routes.create_run(payload, request, response)
    assert idempotency.value.status_code == 400

    audits: list[dict[str, Any]] = []
    request = _request(
        state=SimpleNamespace(
            tenant_repository=SimpleNamespace(get_connection=lambda **_: connection),
            task_repository=SimpleNamespace(
                create_once=lambda **_: (SimpleNamespace(id="task-1", status="pending"), True)
            ),
            audit_logger=SimpleNamespace(log_operation=lambda **kwargs: audits.append(kwargs)),
        ),
        headers={"Idempotency-Key": "idem-1"},
    )
    delayed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(reconciliation_routes.amazon_reconciliation_task, "delay", lambda *args: delayed.append(args))
    result = await reconciliation_routes.create_run(payload, request, response)
    assert response.status_code == 202
    assert result["idempotent_replay"] is False
    assert delayed == [("task-1", "tenant-1", "connection-1")]
    assert audits[0]["action"] == "amazon_reconciliation_requested"


@pytest.mark.asyncio
async def test_reconciliation_list_serializes_differences_and_source_metadata() -> None:
    now = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    row = SimpleNamespace(
        id="run-1",
        channel_connection_id="connection-1",
        status="succeeded",
        summary={"data_source": "amazon_sp_api", "verified_amazon_data": True, "freshness_status": "fresh"},
        started_at=now,
        completed_at=None,
    )
    difference = SimpleNamespace(
        id="difference-1",
        resource_type="inventory",
        resource_id="SKU-1",
        difference_type="quantity_mismatch",
        amazon_value={"available": 1},
        internal_value={"available": 0},
        created_at=now,
    )
    repository = SimpleNamespace(
        list_runs=lambda **_: [row],
        differences=lambda **_: [difference],
    )
    request = _request(state=SimpleNamespace(reconciliation_repository=repository))

    result = await reconciliation_routes.list_runs(request, limit=10)

    assert result["total"] == 1
    assert result["items"][0]["verified_amazon_data"] is True
    assert result["items"][0]["completed_at"] is None
    assert result["items"][0]["differences"][0]["resource_id"] == "SKU-1"


@pytest.mark.asyncio
async def test_task_routes_support_legacy_queue_and_map_repository_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    record = SimpleNamespace(to_dict=lambda: {"task_id": "task-1"})

    class LegacyQueue:
        def get(self, task_id: str) -> Any:
            return record if task_id == "task-1" else None

        def list_tasks(self, *, limit: int, status: Any) -> list[Any]:
            assert limit == 5 and status == "failed"
            return [record]

    request = _request(state=SimpleNamespace(task_queue=LegacyQueue()))
    assert await task_routes.get_task("task-1", request) == {"task_id": "task-1"}
    with pytest.raises(HTTPException) as missing:
        await task_routes.get_task("missing", request)
    assert missing.value.status_code == 404
    assert (await task_routes.list_tasks(request, status="failed", limit=5))["total"] == 1

    request.state.principal = _principal(role="operator")
    with pytest.raises(HTTPException) as forbidden:
        await task_routes.replay_task("task-1", request)
    assert forbidden.value.status_code == 403

    request.state.principal = _principal(role="admin")
    request.app.state.task_repository = SimpleNamespace(
        replay=lambda **_: (_ for _ in ()).throw(LookupError("task not found"))
    )
    with pytest.raises(HTTPException) as not_found:
        await task_routes.replay_task("task-1", request)
    assert not_found.value.status_code == 404
    request.app.state.task_repository = SimpleNamespace(
        replay=lambda **_: (_ for _ in ()).throw(ValueError("not failed"))
    )
    with pytest.raises(HTTPException) as conflict:
        await task_routes.replay_task("task-1", request)
    assert conflict.value.status_code == 409

    from application import celery_app as celery_module

    delayed: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(celery_module.amazon_sync_task, "delay", lambda *args: delayed.append(("sync", args)))
    monkeypatch.setattr(
        celery_module.amazon_reconciliation_task,
        "delay",
        lambda *args: delayed.append(("reconciliation", args)),
    )
    monkeypatch.setattr(
        celery_module.execute_action_task,
        "apply_async",
        lambda **kwargs: delayed.append(("action", tuple(kwargs["args"]))),
    )
    audits: list[dict[str, Any]] = []
    request.app.state.audit_logger = SimpleNamespace(log_operation=lambda **kwargs: audits.append(kwargs))

    replay_rows = iter(
        [
            SimpleNamespace(
                id="sync-task",
                status="pending",
                action="amazon.sync",
                payload={"connection_id": "connection-1", "resources": ["orders"]},
                queue_name="sync",
            ),
            SimpleNamespace(
                id="reconciliation-task",
                status="pending",
                action="amazon.reconciliation",
                payload={"connection_id": "connection-1"},
                queue_name="sync",
            ),
            SimpleNamespace(
                id="agent-task",
                status="pending",
                action="agent.run",
                payload={"run_id": "run-1"},
                queue_name="text",
            ),
        ]
    )
    request.app.state.task_repository = SimpleNamespace(replay=lambda **_: next(replay_rows))
    for source in ("source-1", "source-2", "source-3"):
        assert (await task_routes.replay_task(source, request))["status"] == "pending"
    assert [kind for kind, _ in delayed] == ["sync", "reconciliation", "action"]


@pytest.mark.asyncio
async def test_task_close_enforces_admin_and_maps_failures() -> None:
    payload = task_routes.TaskCloseRequest(reason="operator verified external recovery")
    request = _request(
        state=SimpleNamespace(task_repository=None, audit_logger=None), principal=_principal(role="operator")
    )
    with pytest.raises(HTTPException) as forbidden:
        await task_routes.close_task("task-1", payload, request)
    assert forbidden.value.status_code == 403

    request.state.principal = _principal(role="owner")
    request.app.state.task_repository = SimpleNamespace(
        close=lambda **_: (_ for _ in ()).throw(LookupError("task not found"))
    )
    with pytest.raises(HTTPException) as missing:
        await task_routes.close_task("task-1", payload, request)
    assert missing.value.status_code == 404
    request.app.state.task_repository = SimpleNamespace(
        close=lambda **_: (_ for _ in ()).throw(ValueError("not closable"))
    )
    with pytest.raises(HTTPException) as conflict:
        await task_routes.close_task("task-1", payload, request)
    assert conflict.value.status_code == 409

    audits: list[dict[str, Any]] = []
    request.app.state.task_repository = SimpleNamespace(
        close=lambda **_: SimpleNamespace(
            id="task-1",
            status="closed",
            closed_by="operator-1",
            close_reason=payload.reason,
        )
    )
    request.app.state.audit_logger = SimpleNamespace(log_operation=lambda **kwargs: audits.append(kwargs))
    assert (await task_routes.close_task("task-1", payload, request))["status"] == "closed"
    assert audits[0]["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_sync_routes_validate_permissions_resources_and_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = sync_routes.SyncJobRequest(connection_id="connection-1", resources=["orders", "orders"])
    response = Response()
    request = _request(
        state=SimpleNamespace(
            tenant_repository=SimpleNamespace(get_connection=lambda **_: None),
            task_repository=None,
            settings=SimpleNamespace(task_backend="memory"),
        ),
        principal=_principal(role="viewer", scopes=frozenset({"read"})),
    )
    with pytest.raises(HTTPException) as forbidden:
        await sync_routes.create_sync_job(payload, BackgroundTasks(), request, response)
    assert forbidden.value.status_code == 403

    request.state.principal = _principal()
    with pytest.raises(HTTPException) as invalid:
        await sync_routes.create_sync_job(
            sync_routes.SyncJobRequest(connection_id="connection-1", resources=["refunds"]),
            BackgroundTasks(),
            request,
            response,
        )
    assert invalid.value.status_code == 422
    with pytest.raises(HTTPException) as missing:
        await sync_routes.create_sync_job(payload, BackgroundTasks(), request, response)
    assert missing.value.status_code == 404

    connection = SimpleNamespace(id="connection-1")
    task = SimpleNamespace(id="task-1", status="pending")
    request.app.state.tenant_repository = SimpleNamespace(get_connection=lambda **_: connection)
    request.app.state.task_repository = SimpleNamespace(create=lambda **_: task)
    background = BackgroundTasks()
    result = await sync_routes.create_sync_job(payload, background, request, response)
    assert result["resources"] == ["orders"]
    assert len(background.tasks) == 1

    request.app.state.settings.task_backend = "celery"
    delayed: list[tuple[Any, ...]] = []
    from application import celery_app as celery_module

    monkeypatch.setattr(celery_module.amazon_sync_task, "delay", lambda *args: delayed.append(args))
    assert (await sync_routes.create_sync_job(payload, BackgroundTasks(), request, response))["task_id"] == "task-1"
    assert delayed == [("task-1", "tenant-1", "connection-1", ["orders"])]


@pytest.mark.asyncio
async def test_sync_status_and_data_quality_expose_failures_without_claiming_verification() -> None:
    now = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    cursor = SimpleNamespace(
        id="cursor-1",
        channel_connection_id="connection-1",
        resource_type="orders",
        cursor="next",
        status="failed",
        item_count=10,
        failure_count=3,
        error_code="TimeoutError",
        error_message="timeout",
        last_started_at=now,
        last_succeeded_at=None,
        last_failed_at=now,
        updated_at=now,
    )
    event = SimpleNamespace(
        id="event-1",
        payload={"_connection_id": "connection-1"},
        status="dead_letter",
        attempts=5,
        error_code="UnknownNotificationType",
        error_message="unmapped",
        received_at=now,
    )
    state = SimpleNamespace(
        sync_cursor_repository=SimpleNamespace(list_status=lambda **_: [cursor]),
        event_repository=SimpleNamespace(list_inbox_failures=lambda **_: [event]),
    )
    request = _request(state=state, principal=_principal(scopes=frozenset()))
    with pytest.raises(HTTPException) as forbidden:
        await sync_routes.sync_status(request)
    assert forbidden.value.status_code == 403

    request.state.principal = _principal()
    status = await sync_routes.sync_status(request, connection_id="connection-1")
    assert status["items"][0]["last_started_at"].endswith("Z")
    quality = await sync_routes.data_quality_issues(request)
    assert quality["total"] == 2
    assert {item["severity"] for item in quality["items"]} == {"high"}
    assert sync_routes._iso(None) is None


@pytest.mark.asyncio
async def test_local_sync_job_isolates_refresh_failure_and_closes_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []
    created = iter(
        [SimpleNamespace(id="refresh-us", status="pending"), SimpleNamespace(id="refresh-ca", status="pending")]
    )
    tasks = SimpleNamespace(
        update_status=lambda **kwargs: updates.append(kwargs),
        create=lambda **_: next(created),
    )
    connection = SimpleNamespace(id="connection-1", marketplace_ids=("US", "CA"))
    events: list[str] = []

    class Adapter:
        async def close(self) -> None:
            events.append("closed")

    class SyncService:
        def __init__(self, **_: Any) -> None:
            pass

        async def sync(self, resources: tuple[str, ...]) -> dict[str, dict[str, str]]:
            assert resources == ("orders",)
            return {"orders": {"status": "succeeded"}}

    refresh_calls: list[str] = []

    def refresh(*, marketplace_id: str, **_: Any) -> dict[str, int]:
        refresh_calls.append(marketplace_id)
        if marketplace_id == "CA":
            raise RuntimeError("refresh failed")
        return {"resolved": 1}

    app = SimpleNamespace(
        state=SimpleNamespace(
            task_repository=tasks,
            tenant_repository=SimpleNamespace(get_connection=lambda **_: connection),
            settings=object(),
            secret_store=object(),
            commerce_sync_repository=object(),
            sync_cursor_repository=object(),
            operations_refresh_service=SimpleNamespace(refresh=refresh),
        )
    )
    monkeypatch.setattr(sync_routes, "tenant_context", lambda _: nullcontext())
    monkeypatch.setattr(sync_routes, "create_amazon_adapter", lambda **_: Adapter())
    monkeypatch.setattr(sync_routes, "AmazonReadSyncService", SyncService)

    await sync_routes._run_sync_job(app, "tenant-1", "task-1", "connection-1", ("orders",))

    assert refresh_calls == ["US", "CA"]
    assert any(item["task_id"] == "refresh-ca" and item["status"] == "dead_letter" for item in updates)
    assert events == ["closed"]


@pytest.mark.asyncio
async def test_local_sync_job_handles_disappeared_connection_and_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict[str, Any]] = []
    current: dict[str, Any] = {"connection": None}
    app = SimpleNamespace(
        state=SimpleNamespace(
            task_repository=SimpleNamespace(update_status=lambda **kwargs: updates.append(kwargs)),
            tenant_repository=SimpleNamespace(get_connection=lambda **_: current["connection"]),
            settings=object(),
            secret_store=object(),
            commerce_sync_repository=object(),
            sync_cursor_repository=object(),
        )
    )
    monkeypatch.setattr(sync_routes, "tenant_context", lambda _: nullcontext())
    await sync_routes._run_sync_job(app, "tenant-1", "task-1", "missing", ("orders",))
    assert updates[-1]["error_code"] == "ConnectionNotFound"

    class Adapter:
        async def close(self) -> None:
            pass

    class FailingSync:
        def __init__(self, **_: Any) -> None:
            pass

        async def sync(self, _: tuple[str, ...]) -> Any:
            raise TimeoutError("provider timeout")

    current["connection"] = SimpleNamespace(id="connection-1", marketplace_ids=("US",))
    monkeypatch.setattr(sync_routes, "create_amazon_adapter", lambda **_: Adapter())
    monkeypatch.setattr(sync_routes, "AmazonReadSyncService", FailingSync)
    await sync_routes._run_sync_job(app, "tenant-1", "task-2", "connection-1", ("orders",))
    assert updates[-1]["status"] == "dead_letter"
    assert updates[-1]["error_code"] == "TimeoutError"


@dataclass
class WorkItem:
    id: str
    amount: Decimal = Decimal("12.50")
    due_at: datetime = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    details: dict[str, Any] | None = None


@pytest.mark.asyncio
async def test_work_item_views_permissions_and_serialization() -> None:
    calls: list[dict[str, Any]] = []
    item = WorkItem(id="work-1", details={"tags": [Decimal("1.5")]})
    repository = SimpleNamespace(
        list_page=lambda **kwargs: calls.append(kwargs) or (1, [item]),
        workload=lambda **_: {"open": 1},
        list=lambda **_: [item],
        get=lambda **kwargs: item if kwargs["work_item_id"] == "work-1" else None,
    )
    bridge = SimpleNamespace(
        work_item_context=lambda **kwargs: None if kwargs["work_item_id"] == "missing" else {"cluster": {}}
    )
    request = _request(state=SimpleNamespace(work_item_repository=repository, customer_case_operations_bridge=bridge))

    for view in (
        "today",
        "waiting",
        "verifying",
        "mine",
        "unassigned",
        "awaiting_approval",
        "blocked",
        "verification_failed",
    ):
        result = await work_item_routes.list_work_items(  # type: ignore[arg-type]
            request,
            view=view,
            timezone="UTC",
            page=1,
            page_size=50,
        )
        assert result["items"][0]["amount"] == "12.50"
    with pytest.raises(HTTPException) as timezone_error:
        await work_item_routes.list_work_items(
            request,
            view="due_today",
            timezone="Mars/Olympus",
            page=1,
            page_size=50,
        )
    assert timezone_error.value.status_code == 422
    due_today = await work_item_routes.list_work_items(
        request,
        view="due_today",
        timezone="Asia/Shanghai",
        page=1,
        page_size=50,
    )
    assert due_today["total"] == 1

    conflict = await work_item_routes.list_work_items(
        request,
        view="mine",
        assignee_id="someone-else",
        timezone="UTC",
        page=1,
        page_size=50,
    )
    assert conflict["total"] == 0
    assert await work_item_routes.workload(request) == {"open": 1}
    assert (await work_item_routes.customer_case_context("missing", request))["cluster"] is None
    assert (await work_item_routes.customer_case_context("work-1", request))["cluster"] == {}
    assert (await work_item_routes.handoff_packet(request))["total"] == 1
    assert (await work_item_routes.get_work_item("work-1", request))["id"] == "work-1"
    with pytest.raises(HTTPException) as missing:
        await work_item_routes.get_work_item("missing", request)
    assert missing.value.status_code == 404

    request.state.principal = _principal(scopes=frozenset())
    with pytest.raises(HTTPException) as read_forbidden:
        await work_item_routes.workload(request)
    assert read_forbidden.value.status_code == 403


@pytest.mark.asyncio
async def test_work_item_bulk_and_escalation_map_authorization_and_repository_failures() -> None:
    item = WorkItem(id="work-1")
    audits: list[dict[str, Any]] = []
    repository = SimpleNamespace(
        bulk_update=lambda **_: [item],
        escalate_due=lambda **_: [item],
    )
    request = _request(
        state=SimpleNamespace(
            work_item_repository=repository,
            audit_logger=SimpleNamespace(log_operation=lambda **kwargs: audits.append(kwargs)),
        )
    )
    payload = work_item_routes.BulkWorkItemRequest(
        work_item_ids=["work-1"], operation="assign", assignee_id="operator-2"
    )
    with pytest.raises(HTTPException) as assignment:
        await work_item_routes.bulk_update(payload, request)
    assert assignment.value.status_code == 403

    request.state.principal = _principal(role="admin", scopes=frozenset({"admin"}))
    assert (await work_item_routes.bulk_update(payload, request))["total"] == 1
    repository.bulk_update = lambda **_: (_ for _ in ()).throw(LookupError("work item missing"))
    with pytest.raises(HTTPException) as missing:
        await work_item_routes.bulk_update(payload, request)
    assert missing.value.status_code == 404
    repository.bulk_update = lambda **_: (_ for _ in ()).throw(ValueError("invalid transition"))
    with pytest.raises(HTTPException) as conflict:
        await work_item_routes.bulk_update(payload, request)
    assert conflict.value.status_code == 409

    request.state.principal = _principal(role="operator")
    with pytest.raises(HTTPException) as escalate_forbidden:
        await work_item_routes.escalate_due(request)
    assert escalate_forbidden.value.status_code == 403
    request.state.principal = _principal(role="owner")
    repository.escalate_due = lambda **_: [item]
    assert (await work_item_routes.escalate_due(request))["total"] == 1
    assert any(call["action"] == "work_items_sla_escalated" for call in audits)


def test_work_item_contract_helpers_validate_timezone_and_json_types() -> None:
    with pytest.raises(ValueError, match="due_at must include timezone"):
        work_item_routes.BulkWorkItemRequest(
            work_item_ids=["work-1"],
            operation="set_due_at",
            due_at=datetime(2026, 8, 5, 8, 0),
        )
    aware = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    assert work_item_routes._json_safe(aware).endswith("Z")
    assert work_item_routes._json_safe([Decimal("1.25")]) == ["1.25"]
    assert work_item_routes._json_safe({"amount": Decimal("2.50")}) == {"amount": "2.50"}
