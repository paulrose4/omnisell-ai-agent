from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from agents.registry import AdvisorRegistry
from application.agent_services import AgentRunService
from domain.models import WorkflowStatus
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.workflows import SQLWorkflowStore
from workflows.engine import PersistentAgentWorkflow


def test_agent_run_idempotency_is_tenant_and_workflow_scoped(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'agent-idempotency.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    store = SQLWorkflowStore(create_session_factory(url))

    first = store.create_run(
        tenant_id="tenant-a",
        actor_id="operator-1",
        workflow_type="operations_brief_analysis",
        correlation_id="corr-1",
        idempotency_key="idem-1",
        request={"brief_id": "brief-1"},
    )
    duplicate = store.create_run(
        tenant_id="tenant-a",
        actor_id="operator-2",
        workflow_type="operations_brief_analysis",
        correlation_id="corr-2",
        idempotency_key="idem-1",
        request={"brief_id": "brief-1"},
    )
    other_workflow = store.create_run(
        tenant_id="tenant-a",
        actor_id="operator-1",
        workflow_type="customer_case_analysis",
        correlation_id="corr-3",
        idempotency_key="idem-1",
        request={"case_id": "case-1"},
    )

    assert duplicate == first
    assert other_workflow != first
    assert (
        store.find_by_idempotency(
            tenant_id="tenant-a", workflow_type="operations_brief_analysis", idempotency_key="idem-1"
        ).id
        == first
    )


def test_concurrent_create_run_claims_single_idempotent_owner(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'agent-idempotency-concurrent.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    store = SQLWorkflowStore(create_session_factory(url))

    def create() -> tuple[str, bool]:
        return store.create_run_once(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-concurrent",
            idempotency_key="idem-concurrent",
            request={"brief_id": "brief-1"},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: create(), range(8)))

    assert len({run_id for run_id, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1


class ConcurrentCollector:
    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()

    async def collect(self, *, tenant_id: str, request: dict):
        from datetime import UTC, datetime

        from agents.contracts import EvidenceBundle

        self.calls += 1
        await self.release.wait()
        return EvidenceBundle(tenant_id=tenant_id, collected_at=datetime.now(UTC), evidence=())


class ConcurrentAdvisor:
    agent_type = "operations_brief_analyst"
    prompt_version = "operations-brief-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, *, context, evidence):
        from datetime import UTC, datetime
        from decimal import Decimal

        from agents.contracts import AgentAnalysisEnvelope

        self.calls += 1
        return AgentAnalysisEnvelope(
            schema_version="1.0",
            agent_type=self.agent_type,
            prompt_version=self.prompt_version,
            tenant_id=context.tenant_id,
            workflow_run_id=context.workflow_run_id,
            generated_at=datetime.now(UTC),
            executive_summary="Concurrent analysis",
            evidence=(),
            hypotheses=(),
            missing_data=(),
            proposed_actions=(),
            prohibited_actions=(),
            confidence=Decimal("0.50"),
            degraded_reason=None,
            model_usage=None,
            warnings=(),
        )


def test_concurrent_advisory_submission_executes_model_once(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'agent-workflow-concurrent.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    store = SQLWorkflowStore(create_session_factory(url))
    collector = ConcurrentCollector()
    advisor = ConcurrentAdvisor()
    workflow = PersistentAgentWorkflow(store=store)

    async def scenario():
        first = asyncio.create_task(
            workflow.run_advisory(
                tenant_id="tenant-a",
                actor_id="operator-1",
                workflow_type="operations_brief_analysis",
                correlation_id="corr-1",
                request={"brief_id": "brief-1", "idempotency_key": "same-key"},
                evidence_collector=collector,
                advisor=advisor,
            )
        )
        while collector.calls == 0:
            await asyncio.sleep(0)
        duplicate = asyncio.create_task(
            workflow.run_advisory(
                tenant_id="tenant-a",
                actor_id="operator-2",
                workflow_type="operations_brief_analysis",
                correlation_id="corr-2",
                request={"brief_id": "brief-1", "idempotency_key": "same-key"},
                evidence_collector=collector,
                advisor=advisor,
            )
        )
        await asyncio.sleep(0)
        collector.release.set()
        return await asyncio.gather(first, duplicate)

    first_result, duplicate_result = asyncio.run(scenario())

    assert first_result["run_id"] == duplicate_result["run_id"]
    assert duplicate_result["idempotent_replay"] is True
    assert collector.calls == 1
    assert advisor.calls == 1


class PausedRetryWorkflow:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.release = asyncio.Event()

    async def run_advisory(self, **kwargs):
        self.requests.append(kwargs["request"])
        await self.release.wait()
        return {"run_id": "retry-run", "status": "succeeded", "analysis": {}}


def test_concurrent_resume_reuses_single_retry_attempt(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'agent-resume-concurrent.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    store = SQLWorkflowStore(create_session_factory(url))
    parent_run_id = store.create_run(
        tenant_id="tenant-a",
        actor_id="operator-1",
        workflow_type="customer_case_analysis",
        correlation_id="corr-parent",
        idempotency_key="parent-key",
        request={"case_id": "case-1"},
    )
    store.transition(parent_run_id, WorkflowStatus.FAILED, {"error_category": "ModelTimeout"}, tenant_id="tenant-a")
    workflow = PausedRetryWorkflow()
    service = AgentRunService(
        store=store,
        workflow=workflow,
        registry=AdvisorRegistry({"customer_case_analysis": object()}),
        evidence_collectors={"customer_case_analysis": object()},
    )

    async def scenario():
        first = asyncio.create_task(
            service.resume(
                tenant_id="tenant-a", run_id=parent_run_id, actor_id="operator-2", correlation_id="corr-retry-1"
            )
        )
        while not workflow.requests:
            await asyncio.sleep(0)
        second = asyncio.create_task(
            service.resume(
                tenant_id="tenant-a", run_id=parent_run_id, actor_id="operator-3", correlation_id="corr-retry-2"
            )
        )
        while len(workflow.requests) < 2:
            await asyncio.sleep(0)
        workflow.release.set()
        return await asyncio.gather(first, second)

    asyncio.run(scenario())

    assert [request["idempotency_key"] for request in workflow.requests] == [
        f"retry:{parent_run_id}:1",
        f"retry:{parent_run_id}:1",
    ]
