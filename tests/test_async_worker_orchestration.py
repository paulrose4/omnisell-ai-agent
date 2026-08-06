from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from api import durable_task_queue
from application import agent_worker
from application.customer_case_agent import CustomerCaseAgentService
from application.listing_optimization_agent import ListingOptimizationAgentService
from application.operations_brief_agent import OperationsBriefAgentService
from application.reconciliation_agent import ReconciliationAgentService
from domain.models import WorkflowStatus


def _task_row(*, status: str = "pending", task_id: str = "task-1") -> SimpleNamespace:
    now = datetime(2026, 8, 5, 8, 30, tzinfo=UTC)
    return SimpleNamespace(
        id=task_id,
        queue_name="agent",
        action="agent.run",
        status=status,
        created_at=now,
        updated_at=now,
        correlation_id="request-1",
        result={"ok": True},
        error_message=None,
        attempts=1,
        max_attempts=5,
        replayed_from_task_id=None,
        replayed_to_task_id=None,
        closed_by=None,
        close_reason=None,
    )


@pytest.mark.asyncio
async def test_celery_task_queue_persists_dispatches_and_filters_records(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_task_row(), _task_row(status="succeeded", task_id="task-2")]

    class Repository:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            self.created = kwargs
            return rows[0]

        def get(self, **kwargs: Any) -> SimpleNamespace | None:
            return rows[0] if kwargs["task_id"] == "task-1" else None

        def list(self, **_: Any) -> list[SimpleNamespace]:
            return rows

    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(
        durable_task_queue.execute_action_task,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )
    repository = Repository()
    queue = durable_task_queue.CeleryTaskQueue(repository)  # type: ignore[arg-type]

    await queue.start()
    record = queue.enqueue_action(
        tenant_id="tenant-1",
        actor_id="operator-1",
        queue_name="agent",
        action="agent.run",
        payload={"run_id": "run-1"},
        request_id="request-1",
        idempotency_key="idem-1",
    )
    await queue.shutdown()

    assert record.to_dict()["task_id"] == "task-1"
    assert repository.created["correlation_id"] == "request-1"
    assert dispatched == [
        {
            "args": ["task-1", "tenant-1", "agent.run", {"run_id": "run-1"}],
            "queue": "agent",
            "task_id": "task-1",
        }
    ]
    assert queue.get("task-1", tenant_id="tenant-1") is not None
    assert queue.get("missing", tenant_id="tenant-1") is None
    assert [item.task_id for item in queue.list_tasks(tenant_id="tenant-1")] == ["task-1", "task-2"]
    assert [item.task_id for item in queue.list_tasks(tenant_id="tenant-1", status="succeeded")] == ["task-2"]
    assert queue.queue_lengths() == {}
    with pytest.raises(RuntimeError, match="enqueue_action"):
        queue.enqueue()


def test_celery_task_queue_does_not_redeliver_existing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    class Repository:
        def create(self, **_: Any) -> SimpleNamespace:
            return _task_row(status="succeeded")

    monkeypatch.setattr(
        durable_task_queue.execute_action_task,
        "apply_async",
        lambda **_: pytest.fail("existing durable tasks must not be delivered again"),
    )
    queue = durable_task_queue.CeleryTaskQueue(Repository())  # type: ignore[arg-type]
    record = queue.enqueue_action(
        tenant_id="tenant-1",
        actor_id="operator-1",
        queue_name="agent",
        action="agent.run",
        payload={},
        request_id=None,
    )
    assert record.status == "succeeded"
    assert record.created_at > 0


def test_execute_agent_run_action_uses_nonblocking_tenant_scoped_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Lock:
        def acquire(self, *, blocking: bool) -> bool:
            events.append(f"acquire:{blocking}")
            return True

        def release(self) -> None:
            events.append("release")

    class RedisClient:
        def lock(self, name: str, *, timeout: int) -> Lock:
            assert name.startswith("omnisell:agent-run-lock:")
            assert name.endswith(":run-1")
            assert timeout >= 600
            return Lock()

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(
        agent_worker, "get_settings", lambda: SimpleNamespace(redis_url="redis://test", model_timeout_seconds=10)
    )
    monkeypatch.setattr(agent_worker.Redis, "from_url", lambda *_, **__: RedisClient())

    def run(coroutine: Any) -> dict[str, str]:
        coroutine.close()
        events.append("run")
        return {"status": "succeeded"}

    monkeypatch.setattr(agent_worker.asyncio, "run", run)

    assert agent_worker.execute_agent_run_action(run_id="run-1", tenant_id="tenant-1") == {"status": "succeeded"}
    assert events == ["acquire:False", "run", "release", "close"]


def test_execute_agent_run_action_rejects_parallel_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class RedisClient:
        def lock(self, *_: Any, **__: Any) -> SimpleNamespace:
            return SimpleNamespace(acquire=lambda **_: False)

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        agent_worker, "get_settings", lambda: SimpleNamespace(redis_url="redis://test", model_timeout_seconds=10)
    )
    monkeypatch.setattr(agent_worker.Redis, "from_url", lambda *_, **__: RedisClient())

    with pytest.raises(TimeoutError, match="already executing"):
        agent_worker.execute_agent_run_action(run_id="run-1", tenant_id="tenant-1")
    assert closed == [True]


@pytest.mark.asyncio
async def test_execute_agent_run_returns_idempotent_terminal_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(status=WorkflowStatus.SUCCEEDED.value)

    class Store:
        def __init__(self, _: Any) -> None:
            pass

        def get_run(self, **_: Any) -> SimpleNamespace:
            return row

    monkeypatch.setattr(agent_worker, "get_settings", lambda: SimpleNamespace(database_url="sqlite://"))
    monkeypatch.setattr(agent_worker, "create_session_factory", lambda _: object())
    monkeypatch.setattr(agent_worker, "SQLWorkflowStore", Store)

    result = await agent_worker._execute_agent_run(run_id="run-1", tenant_id="tenant-1")
    assert result == {"run_id": "run-1", "status": "succeeded", "idempotent_replay": True}


@pytest.mark.asyncio
async def test_execute_agent_run_resumes_active_workflow_and_closes_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    row = SimpleNamespace(
        status=WorkflowStatus.RECEIVED.value,
        workflow_type=OperationsBriefAgentService.WORKFLOW_TYPE,
        actor_id="operator-1",
        correlation_id="correlation-1",
        state={"request": {"marketplace_id": "ATVPDKIKX0DER"}},
    )

    class Store:
        def __init__(self, _: Any) -> None:
            pass

        def get_run(self, **_: Any) -> SimpleNamespace:
            return row

        def reset_active_run_for_recovery(self, **_: Any) -> None:
            events.append("reset")

    class Closable:
        async def close(self) -> None:
            events.append("close")

    class QuotaFactory:
        @classmethod
        def from_url(cls, **_: Any) -> Closable:
            return Closable()

    class Workflow:
        def __init__(self, **_: Any) -> None:
            pass

        async def run_advisory(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["reserved_run_id"] == "run-1"
            assert kwargs["request"] == {"marketplace_id": "ATVPDKIKX0DER"}
            events.append("run")
            return {"run_id": "run-1", "status": "awaiting_approval"}

    settings = SimpleNamespace(
        database_url="sqlite://",
        redis_url="redis://test",
        agent_tenant_quota_per_minute=10,
        agent_tenant_quota_per_day=100,
    )
    monkeypatch.setattr(agent_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_worker, "create_session_factory", lambda _: object())
    monkeypatch.setattr(agent_worker, "SQLWorkflowStore", Store)
    monkeypatch.setattr(agent_worker, "create_configured_gateway", lambda _: Closable())
    monkeypatch.setattr(agent_worker, "RedisAgentQuotaLimiter", QuotaFactory)
    monkeypatch.setattr(agent_worker, "SQLAuditLogger", lambda _: object())
    monkeypatch.setattr(agent_worker, "_build_advisor_and_collector", lambda **_: ("advisor", "collector"))
    monkeypatch.setattr(agent_worker, "PersistentAgentWorkflow", Workflow)

    result = await agent_worker._execute_agent_run(run_id="run-1", tenant_id="tenant-1")

    assert result["status"] == "awaiting_approval"
    assert events == ["reset", "run", "close", "close"]


@pytest.mark.asyncio
async def test_execute_agent_run_rejects_unknown_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        def __init__(self, _: Any) -> None:
            pass

        def get_run(self, **_: Any) -> None:
            return None

    monkeypatch.setattr(agent_worker, "get_settings", lambda: SimpleNamespace(database_url="sqlite://"))
    monkeypatch.setattr(agent_worker, "create_session_factory", lambda _: object())
    monkeypatch.setattr(agent_worker, "SQLWorkflowStore", Store)

    with pytest.raises(KeyError, match="Agent run not found"):
        await agent_worker._execute_agent_run(run_id="missing", tenant_id="tenant-1")


@pytest.mark.parametrize(
    "workflow_type",
    [
        OperationsBriefAgentService.WORKFLOW_TYPE,
        CustomerCaseAgentService.WORKFLOW_TYPE,
        ListingOptimizationAgentService.WORKFLOW_TYPE,
        ReconciliationAgentService.WORKFLOW_TYPE,
    ],
)
def test_agent_worker_builds_each_supported_advisor(workflow_type: str) -> None:
    advisor, collector = agent_worker._build_advisor_and_collector(
        workflow_type=workflow_type,
        gateway=object(),
        session_factory=lambda: None,
        audit_logger=object(),
    )
    assert advisor is not None
    assert collector is not None


def test_agent_worker_rejects_unsupported_workflow() -> None:
    with pytest.raises(ValueError, match="Unsupported Agent workflow type"):
        agent_worker._build_advisor_and_collector(
            workflow_type="unknown",
            gateway=object(),
            session_factory=lambda: None,
            audit_logger=object(),
        )
