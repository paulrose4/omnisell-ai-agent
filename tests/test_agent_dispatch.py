from __future__ import annotations

import asyncio
from types import SimpleNamespace

from application.actions import ACTION_HANDLERS
from application.agent_dispatch import QueuedAgentWorkflow
from application.celery_app import _update_agent_dispatch


class Store:
    def __init__(self) -> None:
        self.rows = {}
        self.create_calls = []
        self.dispatches = []

    def create_run_once(self, **kwargs):
        self.create_calls.append(kwargs)
        key = (kwargs["tenant_id"], kwargs["workflow_type"], kwargs["idempotency_key"])
        if key in self.rows:
            return self.rows[key].id, False
        row = SimpleNamespace(
            id="run-1",
            status="received",
            state={"request": kwargs["request"]},
        )
        self.rows[key] = row
        return row.id, True

    def get_run(self, *, tenant_id: str, run_id: str):
        return next((row for key, row in self.rows.items() if key[0] == tenant_id and row.id == run_id), None)

    def set_dispatch_task(self, *, tenant_id: str, run_id: str, task_id: str, task_status: str) -> None:
        self.dispatches.append((tenant_id, run_id, task_id, task_status))


class DurableQueue:
    def __init__(self) -> None:
        self.calls = []
        self.records = {}

    def enqueue_action(self, **kwargs):
        self.calls.append(kwargs)
        key = (kwargs["tenant_id"], kwargs["idempotency_key"])
        return self.records.setdefault(key, SimpleNamespace(task_id="task-1", status="pending"))


class NeverCollector:
    async def collect(self, **kwargs):
        raise AssertionError("submission must not collect evidence")


class NeverAdvisor:
    agent_type = "operations_brief_analyst"
    prompt_version = "operations-brief-v4"

    async def analyze(self, **kwargs):
        raise AssertionError("submission must not call the model")


def test_queued_agent_submission_returns_before_evidence_or_model_work() -> None:
    store = Store()
    queue = DurableQueue()
    workflow = QueuedAgentWorkflow(store=store, task_queue=queue)

    result = asyncio.run(workflow.run_advisory(
        tenant_id="tenant-a",
        actor_id="operator-1",
        workflow_type="operations_brief_analysis",
        correlation_id="corr-1",
        request={"brief_id": "brief-1", "idempotency_key": "idem-1"},
        evidence_collector=NeverCollector(),
        advisor=NeverAdvisor(),
    ))

    assert result == {
        "run_id": "run-1",
        "status": "received",
        "task_id": "task-1",
        "task_status": "pending",
        "idempotent_replay": False,
    }
    assert queue.calls[0]["action"] == "agent.run"
    assert queue.calls[0]["queue_name"] == "text"
    assert queue.calls[0]["idempotency_key"] == "agent-run:run-1"
    assert queue.calls[0]["payload"] == {"run_id": "run-1", "tenant_id": "tenant-a"}
    assert store.dispatches == [("tenant-a", "run-1", "task-1", "pending")]


def test_queued_agent_submission_reuses_run_and_durable_task() -> None:
    store = Store()
    queue = DurableQueue()
    workflow = QueuedAgentWorkflow(store=store, task_queue=queue)
    arguments = dict(
        tenant_id="tenant-a",
        actor_id="operator-1",
        workflow_type="operations_brief_analysis",
        correlation_id="corr-1",
        request={"brief_id": "brief-1", "idempotency_key": "idem-1"},
        evidence_collector=NeverCollector(),
        advisor=NeverAdvisor(),
    )

    first = asyncio.run(workflow.run_advisory(**arguments))
    replay = asyncio.run(workflow.run_advisory(**arguments))

    assert replay["run_id"] == first["run_id"]
    assert replay["task_id"] == first["task_id"]
    assert replay["idempotent_replay"] is True
    assert len(store.rows) == 1
    assert len(queue.records) == 1


def test_agent_run_is_registered_as_durable_action() -> None:
    assert "agent.run" in ACTION_HANDLERS


def test_celery_status_updates_agent_run_dispatch_linkage() -> None:
    store = Store()

    _update_agent_dispatch(
        store=store,
        action="agent.run",
        payload={"run_id": "run-1", "tenant_id": "tenant-a"},
        task_id="task-1",
        status="running",
    )

    assert store.dispatches == [("tenant-a", "run-1", "task-1", "running")]
