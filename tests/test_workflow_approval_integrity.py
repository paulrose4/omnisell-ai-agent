from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from domain.models import WorkflowStatus
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ApprovalRequestRow
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.hitl import SQLHITLManager
from safety.hitl import ApprovalRejectedError
from safety.policy_engine import canonical_action_hash
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
        "risk_level": "high",
        "reason": "Human approval required.",
    }
    payload = {
        "tenant_id": TENANT_ID,
        "resource_id": action.resource_id,
        "agent_run_id": RUN_ID,
        "action_index": 0,
        "action_hash": action_hash,
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
        asyncio.run(workflow.resume(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            actor_id="approver-1",
            correlation_id="corr-1",
        ))

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
        asyncio.run(workflow.resume(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            actor_id="approver-1",
            correlation_id="corr-1",
        ))

    assert executor.calls == 0
    assert store.status is WorkflowStatus.FAILED
    assert store.state["error_category"] == "ApprovalIntegrityError"
    assert store.state["retryable"] is False


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
