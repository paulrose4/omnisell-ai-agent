from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from domain.models import WorkflowStatus
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ApprovalRequestRow
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.hitl import SQLHITLManager
from safety.hitl import ApprovalRejectedError
from safety.policy_engine import PolicyDecision, PolicyEngine, canonical_action_hash
from workflows.engine import ApprovalIntegrityError, PersistentAgentWorkflow, PlannedAction, WorkflowPlan

TENANT_ID = "tenant-a"
RUN_ID = "run-approval-1"


class ApprovalStore:
    def __init__(self, state: dict) -> None:
        self.status = WorkflowStatus.AWAITING_APPROVAL
        self.state = state
        self.transitions: list[WorkflowStatus] = []

    def get_run(self, *, tenant_id: str, run_id: str):
        assert tenant_id == TENANT_ID
        assert run_id == RUN_ID
        return SimpleNamespace(status=self.status.value, state=self.state)

    def transition(self, run_id: str, status: WorkflowStatus, state: dict, *, tenant_id: str) -> None:
        self.status = status
        self.state = {**self.state, **state}
        self.transitions.append(status)


class ApprovalManager:
    def __init__(self, record: SimpleNamespace) -> None:
        self.record = record

    def get_request(self, request_id: str, tenant_id: str | None = None):
        assert request_id == "approval-1"
        assert tenant_id == TENANT_ID
        return self.record


class NeverExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("approval integrity failure must happen before execution")


class FailingExecutor:
    async def execute(self, *args, **kwargs):
        raise RuntimeError("provider write failed")


def _fixture() -> tuple[dict, SimpleNamespace]:
    action = PlannedAction(
        action="request_manual_execution",
        resource_id="action-1",
        arguments={"seller_sku": "SKU-1", "manual_execution_required": True},
        evidence=[{"evidence_id": "issue-1", "digest": "a" * 64}],
        expected_change={"status": "approved_for_manual_execution"},
        confidence=0.85,
    )
    plan = WorkflowPlan(summary="Manual review", actions=[action]).model_dump(mode="json")
    action_hash = canonical_action_hash(
        action=action.action,
        tenant_id=TENANT_ID,
        resource_id=action.resource_id,
        arguments=action.arguments,
    )
    approval = {
        "request_id": "approval-1",
        "action_index": 0,
        "action_hash": action_hash,
        "action": action.model_dump(mode="json"),
        "policy_decision": PolicyDecision.REQUIRE_APPROVAL.value,
        "policy_version": "policy-engine-v1",
        "risk_level": "high",
        "reason": "Human approval required.",
    }
    payload = {
        "tenant_id": TENANT_ID,
        "resource_id": action.resource_id,
        "agent_run_id": RUN_ID,
        "action_index": 0,
        "action_hash": action_hash,
        "policy_decision": PolicyDecision.REQUIRE_APPROVAL.value,
        "policy_version": "policy-engine-v1",
        "plan": action.model_dump(mode="json"),
        "evidence": deepcopy(action.evidence),
        "proposed_changes": deepcopy(action.expected_change),
    }
    record = SimpleNamespace(
        action=action.action,
        payload=payload,
        requester="operator-1",
        decided_by="approver-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        status=SimpleNamespace(value="approved"),
        policy_decision=PolicyDecision.REQUIRE_APPROVAL,
        policy_version="policy-engine-v1",
    )
    return {"plan": plan, "approvals": [approval], "results": []}, record


@pytest.mark.parametrize("mutation", ["arguments", "evidence", "expected_change", "approval_hash"])
def test_resume_rejects_approval_payload_drift_before_execution(mutation: str) -> None:
    state, record = _fixture()
    if mutation == "arguments":
        state["plan"]["actions"][0]["arguments"]["seller_sku"] = "SKU-TAMPERED"
    elif mutation == "evidence":
        state["plan"]["actions"][0]["evidence"][0]["digest"] = "b" * 64
    elif mutation == "expected_change":
        state["plan"]["actions"][0]["expected_change"]["status"] = "published"
    else:
        state["approvals"][0]["action_hash"] = "f" * 64

    executor = NeverExecutor()
    store = ApprovalStore(state)
    workflow = PersistentAgentWorkflow(
        store=store,
        executor=executor,
        approval_manager=ApprovalManager(record),
    )

    with pytest.raises(ApprovalIntegrityError):
        asyncio.run(
            workflow.resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="approver-1",
                correlation_id="corr-1",
            )
        )

    assert executor.calls == 0
    assert store.status is WorkflowStatus.FAILED
    assert store.state["error_category"] == "ApprovalIntegrityError"
    assert store.state["retryable"] is False


def test_resume_rejects_approval_after_expiration() -> None:
    state, record = _fixture()
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    executor = NeverExecutor()
    store = ApprovalStore(state)
    workflow = PersistentAgentWorkflow(
        store=store,
        executor=executor,
        approval_manager=ApprovalManager(record),
    )

    with pytest.raises(ApprovalIntegrityError, match="expired"):
        asyncio.run(
            workflow.resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="approver-1",
                correlation_id="corr-1",
            )
        )

    assert executor.calls == 0
    assert store.status is WorkflowStatus.FAILED
    assert store.state["error_category"] == "ApprovalIntegrityError"
    assert store.state["retryable"] is False


def test_resume_does_not_accept_auto_approved_for_require_approval_action() -> None:
    state, record = _fixture()
    record.status = SimpleNamespace(value="auto_approved")
    executor = NeverExecutor()
    store = ApprovalStore(state)
    workflow = PersistentAgentWorkflow(
        store=store,
        executor=executor,
        approval_manager=ApprovalManager(record),
    )

    result = asyncio.run(
        workflow.resume(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            actor_id="approver-1",
            correlation_id="corr-1",
        )
    )

    assert result["status"] == WorkflowStatus.AWAITING_APPROVAL.value
    assert executor.calls == 0
    assert store.status is WorkflowStatus.AWAITING_APPROVAL


def test_workflow_run_requires_a_planner_before_persisting_state() -> None:
    workflow = PersistentAgentWorkflow(store=object())

    with pytest.raises(RuntimeError, match="Planner is required"):
        asyncio.run(
            workflow.run(
                tenant_id=TENANT_ID,
                actor_id="operator-1",
                workflow_type="operations_recommendations",
                correlation_id="corr-1",
                request={"issue_id": "issue-1"},
            )
        )


def test_resume_rejects_missing_run_and_returns_terminal_state_without_side_effects() -> None:
    missing_store = SimpleNamespace(get_run=lambda **_: None)
    missing = PersistentAgentWorkflow(store=missing_store)
    with pytest.raises(KeyError, match="not found"):
        asyncio.run(
            missing.resume(
                tenant_id=TENANT_ID,
                run_id="missing-run",
                actor_id="operator-1",
                correlation_id="corr-1",
            )
        )

    state, _ = _fixture()
    terminal_store = ApprovalStore(state)
    terminal_store.status = WorkflowStatus.SUCCEEDED
    terminal = PersistentAgentWorkflow(store=terminal_store)
    result = asyncio.run(
        terminal.resume(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            actor_id="operator-1",
            correlation_id="corr-1",
        )
    )

    assert result["status"] == WorkflowStatus.SUCCEEDED.value
    assert terminal_store.transitions == []


def test_resume_fails_closed_for_missing_approval_dependencies() -> None:
    state, record = _fixture()
    no_manager = PersistentAgentWorkflow(store=ApprovalStore(deepcopy(state)))
    with pytest.raises(RuntimeError, match="Approval manager"):
        asyncio.run(
            no_manager.resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="operator-1",
                correlation_id="corr-1",
            )
        )

    missing_references = deepcopy(state)
    missing_references["approvals"] = []
    with pytest.raises(ApprovalIntegrityError, match="references are missing"):
        asyncio.run(
            PersistentAgentWorkflow(
                store=ApprovalStore(missing_references),
                approval_manager=ApprovalManager(record),
            ).resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="operator-1",
                correlation_id="corr-1",
            )
        )

    with pytest.raises(RuntimeError, match="approval record"):
        asyncio.run(
            PersistentAgentWorkflow(
                store=ApprovalStore(deepcopy(state)),
                approval_manager=ApprovalManager(None),
            ).resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="operator-1",
                correlation_id="corr-1",
            )
        )


def test_resume_transitions_rejected_approval_to_failed() -> None:
    state, record = _fixture()
    record.status = SimpleNamespace(value="rejected")
    store = ApprovalStore(state)

    result = asyncio.run(
        PersistentAgentWorkflow(store=store, approval_manager=ApprovalManager(record)).resume(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            actor_id="operator-1",
            correlation_id="corr-1",
        )
    )

    assert result["status"] == WorkflowStatus.FAILED.value
    assert store.status is WorkflowStatus.FAILED
    assert store.state["failure_reason"] == "approval_rejected"


def test_resume_requires_executor_and_persists_execution_failure() -> None:
    state, record = _fixture()
    without_executor = ApprovalStore(deepcopy(state))
    with pytest.raises(RuntimeError, match="Executor is required"):
        asyncio.run(
            PersistentAgentWorkflow(
                store=without_executor,
                approval_manager=ApprovalManager(record),
            ).resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="approver-1",
                correlation_id="corr-1",
            )
        )

    state, record = _fixture()
    failing_store = ApprovalStore(state)
    with pytest.raises(RuntimeError, match="provider write failed"):
        asyncio.run(
            PersistentAgentWorkflow(
                store=failing_store,
                approval_manager=ApprovalManager(record),
                executor=FailingExecutor(),
            ).resume(
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                actor_id="approver-1",
                correlation_id="corr-1",
            )
        )

    assert failing_store.status is WorkflowStatus.FAILED
    assert failing_store.state["error_category"] == "RuntimeError"
    assert failing_store.state["retryable"] is True


def test_sql_hitl_approve_rejects_and_persists_expired_request(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'expired-approval.sqlite'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    manager = SQLHITLManager(session_factory, SQLAuditLogger(session_factory))
    approval = manager.submit_request(
        "request_manual_execution",
        {"tenant_id": "default", "resource_id": "action-1"},
        requester="operator-1",
        risk_level="high",
    )
    with session_factory.begin() as session:
        row = session.get(ApprovalRequestRow, approval.request_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(ApprovalRejectedError, match="expired"):
        manager.approve(
            approval.request_id,
            approver="approver-1",
            tenant_id="default",
        )

    reloaded = manager.get_request(approval.request_id, tenant_id="default")
    assert reloaded is not None
    assert reloaded.status.value == "expired"
    assert reloaded.expires_at.tzinfo is not None


def test_sql_hitl_persists_medium_require_approval_as_pending(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'medium-policy-approval.sqlite'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    manager = SQLHITLManager(session_factory, SQLAuditLogger(session_factory))
    policy = PolicyEngine().evaluate(
        action="previously_unknown_business_action",
        arguments={"resource_id": "action-1"},
        confidence=0.99,
    )

    approval = manager.submit_request(
        "previously_unknown_business_action",
        {"tenant_id": "default", "resource_id": "action-1"},
        requester="operator-1",
        risk_level=policy.risk_level,
        reason=policy.reason,
        policy_decision=policy.decision,
    )
    reloaded = manager.get_request(approval.request_id, tenant_id="default")

    assert policy.decision == PolicyDecision.REQUIRE_APPROVAL
    assert policy.risk_level == "medium"
    assert approval.status.value == "pending"
    assert reloaded is not None
    assert reloaded.status.value == "pending"
    assert reloaded.policy_decision == PolicyDecision.REQUIRE_APPROVAL
    assert reloaded.payload["policy_decision"] == PolicyDecision.REQUIRE_APPROVAL.value
    with session_factory() as session:
        row = session.scalar(select(ApprovalRequestRow).where(ApprovalRequestRow.id == approval.request_id))
        assert row is not None
        assert row.policy_decision == PolicyDecision.REQUIRE_APPROVAL.value
        assert row.policy_version == "policy-engine-v1"
        assert row.policy_reason == policy.reason
        assert row.required_approval_count == 1
    assert reloaded.to_dict()["approved_count"] == 0


def test_approval_database_rejects_require_approval_auto_approval_state(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'approval-policy-constraint.sqlite'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(
            ApprovalRequestRow(
                id="invalid-auto-approval",
                tenant_id="default",
                actor_id="operator-1",
                action="unknown_action",
                action_hash="a" * 64,
                policy_decision=PolicyDecision.REQUIRE_APPROVAL.value,
                policy_version="policy-engine-v1",
                policy_reason="Human approval required.",
                required_approval_count=1,
                risk_level="medium",
                before_snapshot={},
                proposed_changes={},
                status="auto_approved",
                created_at=datetime.now(UTC),
            )
        )


def test_sql_hitl_rejects_policy_snapshot_drift(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'approval-policy-drift.sqlite'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    manager = SQLHITLManager(session_factory, SQLAuditLogger(session_factory))
    approval = manager.submit_request(
        "unknown_action",
        {"tenant_id": "default", "resource_id": "action-1"},
        requester="operator-1",
        risk_level="medium",
    )

    with session_factory.begin() as session:
        row = session.get(ApprovalRequestRow, approval.request_id)
        assert row is not None
        row.proposed_changes = {**row.proposed_changes, "policy_decision": "allow"}

    with pytest.raises(ApprovalRejectedError, match="policy snapshot"):
        manager.get_request(approval.request_id, tenant_id="default")
