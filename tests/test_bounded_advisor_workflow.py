from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agents.contracts import AgentAnalysisEnvelope, AgentProposedAction, EvidenceBundle, EvidenceReference
from agents.quota import AgentQuotaDecision, AgentQuotaExceeded
from domain.models import WorkflowStatus
from workflows.engine import PersistentAgentWorkflow

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


class MemoryStore:
    def __init__(self) -> None:
        self.status = WorkflowStatus.RECEIVED
        self.state = {"history": []}
        self.transitions: list[WorkflowStatus] = []

    def create_run(self, **kwargs: object) -> str:
        self.create_kwargs = kwargs
        return "run-1"

    def transition(self, run_id: str, status: WorkflowStatus, state: dict, *, tenant_id: str) -> None:
        self.status = status
        self.state.update(state)
        self.transitions.append(status)

    def get_run(self, *, tenant_id: str, run_id: str) -> object:
        return SimpleNamespace(status=self.status.value, state=self.state)


class Collector:
    async def collect(self, *, tenant_id: str, request: dict) -> EvidenceBundle:
        assert tenant_id == "tenant-a"
        return EvidenceBundle(
            tenant_id=tenant_id,
            collected_at=NOW,
            evidence=(
                EvidenceReference(
                    evidence_id="brief-1",
                    evidence_type="daily_operations_brief",
                    source="internal_record",
                    resource_type="daily_operations_brief",
                    resource_id="brief-1",
                    observed_at=NOW,
                    collected_at=NOW,
                    freshness_status="fresh",
                    confidence=Decimal("1"),
                    verified_amazon_data=False,
                    content_digest="c" * 64,
                    metadata={},
                ),
            ),
        )


class Advisor:
    agent_type = "operations_brief_analyst"
    prompt_version = "operations-brief-v1"

    async def analyze(self, *, context: object, evidence: EvidenceBundle) -> AgentAnalysisEnvelope:
        assert context.workflow_run_id == "run-1"
        return AgentAnalysisEnvelope(
            schema_version="1.0",
            agent_type=self.agent_type,
            prompt_version=self.prompt_version,
            tenant_id=evidence.tenant_id,
            workflow_run_id=context.workflow_run_id,
            generated_at=NOW,
            executive_summary="Read-only analysis completed.",
            evidence=("brief-1",),
            hypotheses=(),
            missing_data=(),
            proposed_actions=(),
            prohibited_actions=(),
            confidence=Decimal("0.80"),
            degraded_reason=None,
            model_usage=None,
            warnings=(),
        )


class BrokenAdvisor(Advisor):
    async def analyze(self, *, context: object, evidence: EvidenceBundle) -> AgentAnalysisEnvelope:
        raise RuntimeError("model unavailable")


class ActionAdvisor(Advisor):
    async def analyze(self, *, context: object, evidence: EvidenceBundle) -> AgentAnalysisEnvelope:
        analysis = await super().analyze(context=context, evidence=evidence)
        action = AgentProposedAction(
            action_type="create_internal_task_draft",
            resource_type="daily_operations_brief",
            resource_id="brief-1",
            arguments={
                "argument_type": "internal_task",
                "related_resource_ids": ["brief-1"],
                "related_evidence_ids": ["brief-1"],
                "task_title": "Review brief",
            },
            reason="Create a reviewable internal task draft.",
            evidence_ids=("brief-1",),
            expected_outcome="Operator reviews the supplied evidence.",
            risk_level="low",
            requires_approval=False,
        )
        return analysis.model_copy(update={"proposed_actions": (action,)})


class DictOnlyPolicy:
    def evaluate(self, *, action: str, arguments: dict, confidence: float):
        assert isinstance(arguments, dict)
        assert arguments["argument_type"] == "internal_task"
        return SimpleNamespace(decision=SimpleNamespace(value="require_approval"), risk_level="medium", reason="review")


class NeverCollector:
    async def collect(self, **kwargs: object):
        raise AssertionError("quota rejection must happen before evidence collection")


class RejectingQuotaLimiter:
    async def consume(self, *, tenant_id: str, workflow_type: str) -> AgentQuotaDecision:
        assert tenant_id == "tenant-a"
        assert workflow_type == "operations_brief_analysis"
        return AgentQuotaDecision(
            allowed=False,
            scope="minute",
            retry_after_seconds=42,
            minute_count=2,
            daily_count=10,
        )


class ReservedRunStore(MemoryStore):
    def create_run(self, **kwargs: object) -> str:
        raise AssertionError("worker must execute the pre-created AgentRun")


def test_persistent_workflow_runs_bounded_advisory_without_executor() -> None:
    store = MemoryStore()
    workflow = PersistentAgentWorkflow(store=store)

    result = asyncio.run(
        workflow.run_advisory(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-1",
            request={"brief_id": "brief-1"},
            evidence_collector=Collector(),
            advisor=Advisor(),
        )
    )

    assert result["status"] == "succeeded"
    assert result["analysis"]["agent_type"] == "operations_brief_analyst"
    assert store.transitions == [
        WorkflowStatus.VALIDATED,
        WorkflowStatus.EVIDENCE_COLLECTED,
        WorkflowStatus.ANALYZED,
        WorkflowStatus.OUTPUT_VALIDATED,
        WorkflowStatus.POLICY_EVALUATED,
        WorkflowStatus.VERIFYING,
        WorkflowStatus.SUCCEEDED,
    ]
    assert "execution_results" not in store.state


def test_advisory_policy_receives_json_arguments_not_pydantic_models() -> None:
    result = asyncio.run(
        PersistentAgentWorkflow(
            store=MemoryStore(),
            policy_engine=DictOnlyPolicy(),
        ).run_advisory(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-action",
            request={"brief_id": "brief-1"},
            evidence_collector=Collector(),
            advisor=ActionAdvisor(),
        )
    )

    assert result["status"] == "succeeded"


def test_persistent_workflow_records_advisor_failure_without_side_effects() -> None:
    store = MemoryStore()
    workflow = PersistentAgentWorkflow(store=store)

    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(
            workflow.run_advisory(
                tenant_id="tenant-a",
                actor_id="operator-1",
                workflow_type="operations_brief_analysis",
                correlation_id="corr-1",
                request={"brief_id": "brief-1"},
                evidence_collector=Collector(),
                advisor=BrokenAdvisor(),
            )
        )

    assert store.status is WorkflowStatus.FAILED
    assert store.state["error_category"] == "RuntimeError"
    assert store.state["retryable"] is True


def test_advisory_workflow_persists_quota_failure_before_model_work() -> None:
    store = MemoryStore()
    workflow = PersistentAgentWorkflow(store=store, quota_limiter=RejectingQuotaLimiter())

    with pytest.raises(AgentQuotaExceeded) as exc_info:
        asyncio.run(
            workflow.run_advisory(
                tenant_id="tenant-a",
                actor_id="operator-1",
                workflow_type="operations_brief_analysis",
                correlation_id="corr-quota",
                request={"brief_id": "brief-1", "idempotency_key": "quota-1"},
                evidence_collector=NeverCollector(),
                advisor=Advisor(),
            )
        )

    assert exc_info.value.scope == "minute"
    assert exc_info.value.retry_after_seconds == 42
    assert store.transitions == [WorkflowStatus.FAILED]
    assert store.state["error_category"] == "AgentQuotaExceeded"
    assert store.state["quota_scope"] == "minute"
    assert store.state["retryable"] is True


def test_advisory_worker_executes_precreated_run_without_creating_another() -> None:
    store = ReservedRunStore()

    result = asyncio.run(
        PersistentAgentWorkflow(store=store).run_advisory(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-worker",
            request={"brief_id": "brief-1", "idempotency_key": "worker-1"},
            evidence_collector=Collector(),
            advisor=Advisor(),
            reserved_run_id="run-1",
        )
    )

    assert result["run_id"] == "run-1"
    assert result["status"] == "succeeded"


class ExistingRunStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.existing = SimpleNamespace(
            id="existing-run",
            status="succeeded",
            analysis_result={"agent_type": "operations_brief_analyst"},
            state={"analysis_result": {"agent_type": "operations_brief_analyst"}},
        )

    def find_by_idempotency(self, *, tenant_id: str, workflow_type: str, idempotency_key: str):
        assert (tenant_id, workflow_type, idempotency_key) == ("tenant-a", "operations_brief_analysis", "idem-1")
        return self.existing

    def create_run(self, **kwargs: object) -> str:
        raise AssertionError("duplicate idempotent request must not create a run")


class NeverReplayCollector:
    async def collect(self, **kwargs: object):
        raise AssertionError("duplicate idempotent request must not recollect evidence")


class NeverReplayQuotaLimiter:
    async def consume(self, **kwargs: object):
        raise AssertionError("duplicate idempotent request must not consume quota")


def test_advisory_workflow_returns_existing_idempotent_run_without_model_call() -> None:
    store = ExistingRunStore()
    result = asyncio.run(
        PersistentAgentWorkflow(
            store=store,
            quota_limiter=NeverReplayQuotaLimiter(),
        ).run_advisory(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-duplicate",
            request={"idempotency_key": "idem-1"},
            evidence_collector=NeverReplayCollector(),
            advisor=Advisor(),
        )
    )

    assert result == {
        "run_id": "existing-run",
        "status": "succeeded",
        "analysis": {"agent_type": "operations_brief_analyst"},
        "idempotent_replay": True,
    }


class Audit:
    def __init__(self) -> None:
        self.actions = []

    def log_operation(self, action: str, status: str, details: dict, actor: str, risk_level: str):
        self.actions.append((action, status, details))


def test_advisory_workflow_emits_low_sensitivity_stage_audit_events() -> None:
    audit = Audit()
    result = asyncio.run(
        PersistentAgentWorkflow(store=MemoryStore(), audit_logger=audit).run_advisory(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-1",
            request={"brief_id": "brief-1"},
            evidence_collector=Collector(),
            advisor=Advisor(),
        )
    )

    assert result["status"] == "succeeded"
    actions = [item[0] for item in audit.actions]
    assert actions == [
        "agent_run_started",
        "evidence_collected",
        "model_generation_started",
        "model_generation_succeeded",
        "policy_evaluated",
        "verification_succeeded",
    ]
    assert all("request" not in details and "analysis_result" not in details for _, _, details in audit.actions)


class AgentMetrics:
    def __init__(self) -> None:
        self.calls = []

    async def record_agent_run(self, **kwargs):
        self.calls.append(kwargs)


def test_advisory_workflow_records_terminal_agent_metrics() -> None:
    metrics = AgentMetrics()
    result = asyncio.run(
        PersistentAgentWorkflow(
            store=MemoryStore(),
            metrics=metrics,
        ).run_advisory(
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="operations_brief_analysis",
            correlation_id="corr-1",
            request={"brief_id": "brief-1"},
            evidence_collector=Collector(),
            advisor=Advisor(),
        )
    )

    assert result["status"] == "succeeded"
    assert len(metrics.calls) == 1
    assert metrics.calls[0]["workflow_type"] == "operations_brief_analysis"
    assert metrics.calls[0]["status"] == "succeeded"
