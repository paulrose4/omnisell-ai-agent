from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from application import celery_app as celery_module


class RecordingTasks:
    def __init__(self, *_: Any) -> None:
        self.updates: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.create_rows: list[SimpleNamespace] = []

    def update_status(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        if self.create_rows:
            return self.create_rows.pop(0)
        return SimpleNamespace(id=f"task-{len(self.created)}", status="pending")

    def create_once(self, **kwargs: Any) -> tuple[SimpleNamespace, bool]:
        self.created.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.created)}"), kwargs.get("connection_id") != "duplicate"


def _connection(*, connection_id: str = "connection-1", marketplaces: tuple[str, ...] = ("US",)) -> SimpleNamespace:
    return SimpleNamespace(id=connection_id, tenant_id="tenant-1", marketplace_ids=marketplaces)


@pytest.fixture
def isolated_tenant_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(celery_module, "tenant_context", lambda _: nullcontext())
    monkeypatch.setattr(celery_module, "create_session_factory", lambda _: object())


def test_execute_action_task_records_success_and_agent_dispatch(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    tasks = RecordingTasks()
    dispatches: list[dict[str, Any]] = []
    store = SimpleNamespace(set_dispatch_task=lambda **kwargs: dispatches.append(kwargs))
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(celery_module, "SQLWorkflowStore", lambda _: store)
    monkeypatch.setattr(celery_module, "execute_action", lambda action, payload: {"action": action, **payload})

    result = celery_module.execute_action_task.run(
        "task-1",
        "tenant-1",
        "agent.run",
        {"tenant_id": "tenant-1", "run_id": "run-1"},
    )

    assert result["action"] == "agent.run"
    assert [item["status"] for item in tasks.updates] == ["running", "succeeded"]
    assert [item["task_status"] for item in dispatches] == ["running", "succeeded"]


def test_execute_action_task_records_failure_without_dispatch_for_other_actions(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    tasks = RecordingTasks()
    store = SimpleNamespace(
        set_dispatch_task=lambda **_: pytest.fail("non-agent actions must not update agent dispatch")
    )
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(celery_module, "SQLWorkflowStore", lambda _: store)
    monkeypatch.setattr(celery_module, "execute_action", lambda *_: (_ for _ in ()).throw(ValueError("invalid")))

    with pytest.raises(ValueError, match="invalid"):
        celery_module.execute_action_task.run("task-1", "tenant-1", "inventory.refresh", {})

    assert [item["status"] for item in tasks.updates] == ["running", "failed"]
    assert tasks.updates[-1]["error_code"] == "ValueError"


def test_amazon_sync_marks_missing_connection_dead_letter(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    tasks = RecordingTasks()
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(
        celery_module,
        "TenantRepository",
        lambda _: SimpleNamespace(get_connection=lambda **_: None),
    )

    result = celery_module.amazon_sync_task.run("task-1", "tenant-1", "missing", ["orders"])

    assert result == {"status": "dead_letter"}
    assert tasks.updates[-1]["error_code"] == "ConnectionNotFound"


@pytest.mark.parametrize(
    ("sync_result", "expected_status", "refresh_count"),
    [
        ({"orders": {"status": "succeeded"}}, "succeeded", 1),
        ({"orders": {"status": "failed"}}, "failed", 0),
    ],
)
def test_amazon_sync_closes_adapter_and_refreshes_only_after_full_success(
    monkeypatch: pytest.MonkeyPatch,
    isolated_tenant_context: None,
    sync_result: dict[str, dict[str, str]],
    expected_status: str,
    refresh_count: int,
) -> None:
    tasks = RecordingTasks()
    connection = _connection()
    events: list[str] = []

    class Adapter:
        async def close(self) -> None:
            events.append("closed")

    class SyncService:
        def __init__(self, **_: Any) -> None:
            pass

        async def sync(self, resources: tuple[str, ...]) -> dict[str, dict[str, str]]:
            assert resources == ("orders",)
            return sync_result

    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(
        celery_module,
        "TenantRepository",
        lambda _: SimpleNamespace(get_connection=lambda **_: connection),
    )
    monkeypatch.setattr(celery_module, "create_secret_store", lambda *_: object())
    monkeypatch.setattr(celery_module, "create_amazon_adapter", lambda **_: Adapter())
    monkeypatch.setattr(celery_module, "AmazonReadSyncService", SyncService)
    monkeypatch.setattr(celery_module, "CommerceSyncRepository", lambda _: object())
    monkeypatch.setattr(celery_module, "SyncCursorRepository", lambda _: object())
    refreshes: list[dict[str, Any]] = []
    monkeypatch.setattr(celery_module, "_enqueue_operations_refresh", lambda **kwargs: refreshes.append(kwargs) or 1)

    result = celery_module._run_amazon_sync_task("task-1", "tenant-1", "connection-1", ["orders"])

    assert result == sync_result
    assert tasks.updates[-1]["status"] == expected_status
    assert events == ["closed"]
    assert len(refreshes) == refresh_count


def test_enqueue_operations_refresh_is_idempotent_per_marketplace(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = RecordingTasks()
    tasks.create_rows = [
        SimpleNamespace(id="task-us", status="pending"),
        SimpleNamespace(id="task-ca", status="succeeded"),
    ]
    delayed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(celery_module.operations_refresh_task, "delay", lambda *args: delayed.append(args))

    dispatched = celery_module._enqueue_operations_refresh(
        task_repository=tasks,  # type: ignore[arg-type]
        connection=_connection(marketplaces=("US", "CA")),
        correlation_id="sync-1",
        actor_id="amazon-sync",
        seller_skus=["SKU-1"],
    )

    assert dispatched == 1
    assert len(tasks.created) == 2
    assert delayed == [("task-us", "tenant-1", "connection-1", "US", ["SKU-1"])]


def test_operations_refresh_handles_success_missing_connection_and_domain_failure(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    tasks = RecordingTasks()
    connection = _connection()
    current = {"connection": connection}
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(
        celery_module,
        "TenantRepository",
        lambda _: SimpleNamespace(get_connection=lambda **_: current["connection"]),
    )
    monkeypatch.setattr(
        celery_module,
        "build_operations_refresh_service",
        lambda _: SimpleNamespace(refresh=lambda **_: {"resolved": 1}),
    )

    assert celery_module.operations_refresh_task.run("task-1", "tenant-1", "connection-1", "US") == {"resolved": 1}

    current["connection"] = None
    assert celery_module.operations_refresh_task.run("task-2", "tenant-1", "connection-1", "US") == {
        "status": "dead_letter"
    }

    current["connection"] = connection
    monkeypatch.setattr(
        celery_module,
        "build_operations_refresh_service",
        lambda _: SimpleNamespace(refresh=lambda **_: (_ for _ in ()).throw(ValueError("bad snapshot"))),
    )
    with pytest.raises(ValueError, match="bad snapshot"):
        celery_module.operations_refresh_task.run("task-3", "tenant-1", "connection-1", "US")
    assert tasks.updates[-1]["status"] == "dead_letter"


def test_scheduled_operations_and_work_item_jobs_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    connections = {
        "tenant-1": [_connection()],
        "tenant-2": [SimpleNamespace(id="connection-2", tenant_id="tenant-2", marketplace_ids=("CA",))],
    }
    tenants = SimpleNamespace(
        list_tenant_ids=lambda: ["tenant-1", "tenant-2"],
        list_active_connections=lambda *, tenant_id: connections[tenant_id],
    )
    monkeypatch.setattr(celery_module, "TenantRepository", lambda _: tenants)
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: RecordingTasks())
    monkeypatch.setattr(celery_module, "_enqueue_operations_refresh", lambda **_: 1)

    assert celery_module.dispatch_operations_refresh.run() == {"dispatched": 2}

    changed = {"tenant-1": ["work-1", "work-2"], "tenant-2": []}
    monkeypatch.setattr(
        celery_module,
        "OperationalWorkItemRepository",
        lambda _: SimpleNamespace(escalate_due=lambda *, tenant_id: changed[tenant_id]),
    )
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(
        celery_module,
        "SQLAuditLogger",
        lambda _: SimpleNamespace(log_operation=lambda **kwargs: audits.append(kwargs)),
    )

    assert celery_module.escalate_operational_work_items.run() == {"tenants_scanned": 2, "escalated": 2}
    assert audits[0]["details"]["work_item_count"] == 2


def test_case_operations_jobs_aggregate_and_reconcile_all_tenants(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    monkeypatch.setattr(
        celery_module,
        "TenantRepository",
        lambda _: SimpleNamespace(list_tenant_ids=lambda: ["tenant-1", "tenant-2"]),
    )

    class Bridge:
        def process_pending_outbox(self, *, tenant_id: str) -> dict[str, int]:
            return {
                "claimed": 1,
                "processed": 1 if tenant_id == "tenant-1" else 0,
                "failed": 0 if tenant_id == "tenant-1" else 1,
                "case_events_propagated": 1,
            }

        def propagate_completed(self, *, tenant_id: str) -> None:
            assert tenant_id in {"tenant-1", "tenant-2"}

        def reconcile(self, *, tenant_id: str) -> dict[str, int]:
            return {"repaired": 1 if tenant_id == "tenant-2" else 0}

    monkeypatch.setattr(celery_module, "_case_operations_bridge", lambda _: Bridge())

    assert celery_module.consume_case_operations_outbox.run() == {
        "tenants_scanned": 2,
        "claimed": 2,
        "processed": 1,
        "failed": 1,
        "case_events_propagated": 2,
    }
    reconciliation = celery_module.reconcile_case_operations.run()
    assert reconciliation["tenants_scanned"] == 2
    assert reconciliation["results"]["tenant-2"] == {"repaired": 1}


def test_case_operations_bridge_uses_configured_cluster_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        celery_module,
        "CustomerCaseOperationsBridge",
        lambda session_factory, **kwargs: captured.update(session_factory=session_factory, **kwargs) or "bridge",
    )
    monkeypatch.setattr(celery_module.settings, "customer_case_cluster_minimum_samples", 4)
    monkeypatch.setattr(celery_module.settings, "customer_case_cluster_window_days", 14)

    assert celery_module._case_operations_bridge("factory") == "bridge"
    assert captured == {"session_factory": "factory", "minimum_samples": 4, "window_days": 14}


def test_scheduled_sync_dispatches_only_new_durable_tasks(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    tasks = RecordingTasks()
    tasks.create_rows = [
        SimpleNamespace(id="new", status="pending"),
        SimpleNamespace(id="existing", status="succeeded"),
    ]
    connections = [_connection(connection_id="connection-1"), _connection(connection_id="connection-2")]
    monkeypatch.setattr(
        celery_module,
        "TenantRepository",
        lambda _: SimpleNamespace(
            list_tenant_ids=lambda: ["tenant-1"],
            list_active_connections=lambda **_: connections,
        ),
    )
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    delayed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(celery_module.amazon_sync_task, "delay", lambda *args: delayed.append(args))

    result = celery_module.dispatch_amazon_sync.run(["orders", "inventory"])

    assert result["dispatched"] == 1
    assert delayed == [("new", "tenant-1", "connection-1", ["orders", "inventory"])]


def test_notification_consumer_maps_known_events_and_isolates_failures(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    from infrastructure.repositories import tasks as task_repositories

    known = SimpleNamespace(
        id="event-1",
        tenant_id="tenant-1",
        event_type="ORDER_CHANGE",
        payload={"_connection_id": "connection-1"},
        external_event_id="external-1",
    )
    unknown = SimpleNamespace(
        id="event-2",
        tenant_id="tenant-1",
        event_type="UNMAPPED",
        payload={},
        external_event_id="external-2",
    )
    broken = SimpleNamespace(
        id="event-3",
        tenant_id="tenant-1",
        event_type=None,
        payload={},
        external_event_id="external-3",
    )

    class Events:
        def __init__(self, *_: Any) -> None:
            self.processed: list[str] = []
            self.dead: list[str] = []
            self.retried: list[str] = []

        def claim_pending(self, **_: Any) -> list[SimpleNamespace]:
            return [known, unknown, broken]

        def mark_processed(self, **kwargs: Any) -> None:
            self.processed.append(kwargs["event_id"])

        def mark_dead_letter(self, **kwargs: Any) -> None:
            self.dead.append(kwargs["event_id"])

        def mark_retry(self, **kwargs: Any) -> None:
            self.retried.append(kwargs["event_id"])

    events = Events()
    tasks = RecordingTasks()
    monkeypatch.setattr(task_repositories, "EventRepository", lambda _: events)
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(
        celery_module,
        "TenantRepository",
        lambda _: SimpleNamespace(list_tenant_ids=lambda: ["tenant-1"]),
    )
    delayed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(celery_module.amazon_sync_task, "delay", lambda *args: delayed.append(args))

    result = celery_module.consume_amazon_notifications.run()

    assert result == {"processed": 1, "failed": 2}
    assert events.processed == ["event-1"]
    assert events.dead == ["event-2"]
    assert events.retried == ["event-3"]
    assert delayed == [("task-1", "tenant-1", "connection-1", ["orders"])]


def test_reconciliation_tasks_handle_missing_connection_and_dispatch_once(
    monkeypatch: pytest.MonkeyPatch, isolated_tenant_context: None
) -> None:
    tasks = RecordingTasks()
    connection = _connection()
    current = {"connection": None}
    tenants = SimpleNamespace(
        get_connection=lambda **_: current["connection"],
        list_tenant_ids=lambda: ["tenant-1"],
        list_active_connections=lambda **_: [connection],
    )
    monkeypatch.setattr(celery_module, "TenantRepository", lambda _: tenants)
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)

    assert celery_module.amazon_reconciliation_task.run("task-1", "tenant-1", "missing") == {
        "status": "connection_not_found"
    }

    delayed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(celery_module.amazon_reconciliation_task, "delay", lambda *args: delayed.append(args))
    result = celery_module.dispatch_amazon_reconciliation.run()
    assert result == {"dispatched": 1}
    assert delayed == [("task-1", "tenant-1", "connection-1")]
