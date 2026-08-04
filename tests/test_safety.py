from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from legacy.autogen_demo.base import safe_tool_execution
from safety.audit import AuditLogger
from safety.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from safety.hitl import ApprovalRejectedError, ApprovalRequiredError, ApprovalStatus, HITLManager, RiskLevel
from safety.policy_engine import PolicyDecision, PolicyEngine, permanently_prohibited_intent
from sandbox.executor import SandboxExecutor
from sandbox.policy import SandboxPolicyError, validate_code


class FakeContainer:
    def __init__(self, stdout: bytes = b"ok\n", stderr: bytes = b"", exit_code: int = 0) -> None:
        self.status = "exited"
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.killed = False
        self.removed = False

    def reload(self) -> None:
        return None

    def wait(self) -> dict[str, int]:
        return {"StatusCode": self.exit_code}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        if stdout and not stderr:
            return self.stdout
        if stderr and not stdout:
            return self.stderr
        return self.stdout + self.stderr

    def kill(self) -> None:
        self.killed = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.container = FakeContainer()

    def run(self, **kwargs: Any) -> FakeContainer:
        self.kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()


def test_sandbox_policy_blocks_dangerous_code() -> None:
    validate_code("import json\nprint(json.dumps({'ok': True}))")

    with pytest.raises(SandboxPolicyError):
        validate_code("eval('1 + 1')")

    with pytest.raises(SandboxPolicyError):
        validate_code("import os\nprint(os.getcwd())")


def test_sandbox_executor_uses_locked_down_docker_options() -> None:
    fake_client = FakeDockerClient()
    executor = SandboxExecutor(client=fake_client)

    result = executor.execute("print('ok')")

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    kwargs = fake_client.containers.kwargs
    assert kwargs is not None
    assert kwargs["read_only"] is True
    assert kwargs["network_mode"] == "none"
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["cpu_period"] == 100_000
    assert kwargs["cpu_quota"] == 50_000
    assert kwargs["privileged"] is False
    assert kwargs["user"] == "sandbox_user"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]


def test_circuit_breaker_state_transitions() -> None:
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0, half_open_max_calls=1)

    def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        breaker.call(fail)
    assert breaker.state == CircuitState.CLOSED

    with pytest.raises(RuntimeError):
        breaker.call(fail)
    assert breaker.state == CircuitState.OPEN

    assert breaker.call(lambda: "recovered") == "recovered"
    assert breaker.state == CircuitState.CLOSED


def test_circuit_breaker_blocks_while_open() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "blocked")


def test_hitl_rules_auto_pending_critical_and_expiry(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))

    low = manager.submit_request("get_product_list", {}, policy_decision=PolicyDecision.ALLOW)
    medium = manager.submit_request(
        "update_product",
        {"current_price": 100, "price": 94},
        policy_decision=PolicyDecision.REQUIRE_APPROVAL,
    )
    high = manager.submit_request("delete_product", {"product_id": "P1001"})
    critical = manager.submit_request("update_product", {"current_price": 100, "price": 70})

    assert low.status == ApprovalStatus.AUTO_APPROVED
    assert low.risk_level == RiskLevel.LOW
    assert low.policy_decision == PolicyDecision.ALLOW
    assert medium.status == ApprovalStatus.PENDING
    assert medium.risk_level == RiskLevel.MEDIUM
    assert medium.policy_decision == PolicyDecision.REQUIRE_APPROVAL
    assert high.status == ApprovalStatus.PENDING
    assert high.risk_level == RiskLevel.HIGH
    assert critical.status == ApprovalStatus.PENDING
    assert critical.risk_level == RiskLevel.CRITICAL
    assert critical.requires_second_verification is True

    first_approval = manager.approve(critical.request_id, approver="lead", reason="First review")
    assert first_approval.status == ApprovalStatus.AWAITING_SECOND_APPROVAL
    assert first_approval.first_approver_id == "lead"
    assert critical in manager.get_pending()

    self_requested = manager.submit_request(
        "delete_product",
        {"product_id": "P2001"},
        requester="lead",
    )
    with pytest.raises(ApprovalRejectedError, match="own action"):
        manager.approve(self_requested.request_id, approver="lead")

    with pytest.raises(ApprovalRejectedError, match="independent"):
        manager.approve(critical.request_id, approver="lead")

    with pytest.raises(ApprovalRejectedError, match="Requester"):
        manager.approve(critical.request_id, approver=critical.requester)

    approved = manager.approve(critical.request_id, approver="owner", reason="Second review")
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.first_approver_id == "lead"
    assert approved.second_approver_id == "owner"

    high.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    manager.expire_overdue()
    assert high.status == ApprovalStatus.EXPIRED


def test_unknown_policy_action_requires_real_human_approval(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))
    policy = PolicyEngine().evaluate(
        action="previously_unknown_business_action",
        arguments={"resource_id": "resource-1"},
        confidence=0.99,
    )

    request = manager.submit_request(
        "previously_unknown_business_action",
        {"resource_id": "resource-1"},
        risk_level=policy.risk_level,
        reason=policy.reason,
        policy_decision=policy.decision,
    )

    assert policy.decision == PolicyDecision.REQUIRE_APPROVAL
    assert policy.risk_level == "medium"
    assert request.status == ApprovalStatus.PENDING
    assert request.policy_decision == PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    ("action", "payload", "expected_level"),
    [
        ("custom", {"risk_level": "high"}, RiskLevel.HIGH),
        ("custom", {"requires_second_verification": True}, RiskLevel.CRITICAL),
        ("update_price", {"price_change_rate": "0.21"}, RiskLevel.CRITICAL),
        ("update_price", {"change_rate": "0.11"}, RiskLevel.HIGH),
        ("update_price", {"change_percent": "5"}, RiskLevel.MEDIUM),
        ("update_price", {"old_price": "100", "new_price": "104"}, RiskLevel.LOW),
        ("custom", {"bulk": True}, RiskLevel.HIGH),
        ("custom", {"item_count": 10}, RiskLevel.HIGH),
        ("remove_listing", {}, RiskLevel.HIGH),
        ("execute_sandbox", {}, RiskLevel.MEDIUM),
        ("create_listing", {}, RiskLevel.MEDIUM),
        ("read_catalog", {}, RiskLevel.LOW),
    ],
)
def test_hitl_risk_assessment_matrix(action: str, payload: dict[str, object], expected_level: RiskLevel) -> None:
    level, reason = HITLManager().assess_risk(action, payload)

    assert level == expected_level
    assert reason


@pytest.mark.parametrize(
    "payload",
    [
        {"price_change_rate": "not-a-decimal"},
        {"change_rate": object()},
        {"change_percent": None},
        {"old_price": "0", "new_price": "10"},
        {"old_price": None, "new_price": "10"},
    ],
)
def test_malformed_price_change_does_not_create_an_invented_numeric_risk(payload: dict[str, object]) -> None:
    assert HITLManager._extract_price_change(payload) is None


def test_hitl_submit_rejects_blocked_invalid_allow_and_empty_policy_version(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))

    with pytest.raises(ApprovalRejectedError, match="Blocked policy"):
        manager.submit_request(
            "update_product",
            {"resource_id": "listing-1"},
            policy_decision=PolicyDecision.BLOCK,
        )
    with pytest.raises(ValueError, match="low-risk ALLOW"):
        manager.submit_request(
            "update_product",
            {"resource_id": "listing-1"},
            risk_level=RiskLevel.MEDIUM,
            policy_decision=PolicyDecision.ALLOW,
        )
    with pytest.raises(ValueError, match="policy_version"):
        manager.submit_request(
            "update_product",
            {"resource_id": "listing-1"},
            policy_decision=PolicyDecision.REQUIRE_APPROVAL,
            policy_version="   ",
        )

    assert manager.audit_logger.query(status="blocked")


def test_hitl_single_approval_rejection_lookup_and_terminal_state_guards(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))
    approved_request = manager.submit_request(
        "update_product",
        {"resource_id": "listing-1"},
        requester="operator",
        risk_level=RiskLevel.MEDIUM,
    )
    rejected_request = manager.submit_request(
        "update_product",
        {"resource_id": "listing-2"},
        requester="operator",
        risk_level=RiskLevel.MEDIUM,
    )

    approved = manager.approve(approved_request.request_id, approver="approver", reason="Reviewed")
    rejected = manager.reject(rejected_request.request_id, approver="approver", reason="Insufficient evidence")

    assert approved.status == ApprovalStatus.APPROVED
    assert approved.to_dict()["approved_count"] == 1
    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.decision_reason == "Insufficient evidence"
    assert manager.get_request(approved.request_id) is approved
    assert manager.get_request("missing") is None
    with pytest.raises(KeyError, match="not found"):
        manager.approve("missing", approver="approver")
    with pytest.raises(ApprovalRejectedError, match="not pending"):
        manager.approve(approved.request_id, approver="second-approver")
    with pytest.raises(ApprovalRejectedError, match="not pending"):
        manager.reject(rejected.request_id, approver="second-approver")


def test_hitl_expired_request_cannot_be_decided(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))
    request = manager.submit_request(
        "update_product",
        {"resource_id": "listing-1"},
        risk_level=RiskLevel.MEDIUM,
    )
    request.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(ApprovalRejectedError, match="expired"):
        manager.approve(request.request_id, approver="approver")

    assert request.status == ApprovalStatus.EXPIRED
    assert request not in manager.get_pending()


def test_hitl_rechecks_permanently_prohibited_intent_at_approval_time(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))
    request = manager.submit_request(
        "update_product",
        {"resource_id": "listing-1"},
        risk_level=RiskLevel.MEDIUM,
    )
    request.payload["execution_plan"] = ({"tool_name": "issue-refund"},)

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.approve(request.request_id, approver="approver")

    assert request.status == ApprovalStatus.REJECTED
    assert request.decided_by == "policy"
    assert manager.audit_logger.query(status="blocked")


def test_policy_detects_nested_prohibited_intents_and_customer_reply_risk() -> None:
    engine = PolicyEngine()

    assert (
        permanently_prohibited_intent(
            "run_plan",
            {"execution_plan": ({"tool": "cancel-order"},)},
        )
        == "cancel_order"
    )
    blocked = engine.evaluate(
        action="send_customer_reply",
        arguments={"compliance_violations": ["unsupported_refund_promise"]},
        confidence=0.99,
    )
    sensitive = engine.evaluate(
        action="send_customer_reply",
        arguments={"case_risk_level": "critical"},
        confidence=0.99,
    )
    low_confidence = engine.evaluate(
        action="send_customer_reply",
        arguments={"case_risk_level": "low"},
        confidence=0.89,
    )
    allowed = engine.evaluate(
        action="send_customer_reply",
        arguments={"case_risk_level": "low"},
        confidence=0.95,
    )

    assert blocked.decision == PolicyDecision.BLOCK
    assert sensitive.decision == PolicyDecision.REQUIRE_APPROVAL
    assert sensitive.risk_level == "critical"
    assert low_confidence.risk_level == "high"
    assert allowed.decision == PolicyDecision.ALLOW


def test_policy_price_evidence_matrix_fails_closed() -> None:
    engine = PolicyEngine()
    complete = {
        "current_price": "15.00",
        "new_price": "15.20",
        "currency": "USD",
        "break_even_price": "10.00",
        "projected_margin": "0.20",
        "minimum_margin_rate": "0.10",
        "inventory_stockout_risk": "none",
        "data_complete": True,
    }
    cases = (
        ({key: value for key, value in complete.items() if key != "current_price"}, PolicyDecision.REQUIRE_APPROVAL),
        ({**complete, "currency": "usd"}, PolicyDecision.REQUIRE_APPROVAL),
        ({**complete, "current_price": "not-a-decimal"}, PolicyDecision.REQUIRE_APPROVAL),
        ({**complete, "current_price": "0"}, PolicyDecision.REQUIRE_APPROVAL),
        ({**complete, "new_price": "9.00"}, PolicyDecision.BLOCK),
        (
            {**complete, "new_price": "14.00", "inventory_stockout_risk": "critical"},
            PolicyDecision.BLOCK,
        ),
        ({**complete, "data_complete": False}, PolicyDecision.REQUIRE_APPROVAL),
        ({**complete, "projected_margin": "0.05"}, PolicyDecision.REQUIRE_APPROVAL),
        (complete, PolicyDecision.ALLOW),
        ({**complete, "new_price": "16.00"}, PolicyDecision.REQUIRE_APPROVAL),
    )

    for arguments, expected in cases:
        assert engine.evaluate(action="update_price", arguments=arguments, confidence=0.95).decision == expected

    assert (
        engine.evaluate(action="request_manual_execution", arguments={}, confidence=1).decision
        == PolicyDecision.REQUIRE_APPROVAL
    )
    assert engine.evaluate(action="refund", arguments={}, confidence=1).decision == PolicyDecision.BLOCK


def test_audit_logger_writes_and_queries_jsonl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    logger.log_operation("tool_a", "success", {"value": 1}, risk_level="low")
    logger.log_approval("tool_b", "pending", {"value": 2}, risk_level="high")

    assert len(logger.query()) == 2
    assert logger.query(event_type="approval")[0]["action"] == "tool_b"
    assert logger.query(status="success")[0]["details"]["value"] == 1


def test_safe_tool_execution_allows_low_risk_and_audits(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    manager = HITLManager(audit_logger=logger)
    breaker = CircuitBreaker()

    @safe_tool_execution(hitl_manager=manager, audit_logger=logger, circuit_breaker=breaker)
    def get_product_list(page_size: int = 20) -> dict[str, int]:
        """Read products."""
        return {"page_size": page_size}

    assert get_product_list(page_size=5) == {"page_size": 5}
    records = logger.query(action="get_product_list")
    assert any(record["status"] == "success" for record in records)


def test_safe_tool_execution_blocks_high_risk(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    manager = HITLManager(audit_logger=logger)
    breaker = CircuitBreaker()

    @safe_tool_execution(hitl_manager=manager, audit_logger=logger, circuit_breaker=breaker)
    def delete_product(product_id: str) -> dict[str, str]:
        """Delete a product."""
        return {"product_id": product_id}

    with pytest.raises(ApprovalRequiredError) as exc:
        delete_product("P1001")

    assert exc.value.request.status == ApprovalStatus.PENDING
    assert manager.get_pending()[0].action == "delete_product"
    assert logger.query(status="blocked_pending_approval")


def test_safe_tool_execution_supports_async_tools(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    manager = HITLManager(audit_logger=logger)
    breaker = CircuitBreaker()

    @safe_tool_execution(hitl_manager=manager, audit_logger=logger, circuit_breaker=breaker)
    async def get_order_list(page_size: int = 20) -> dict[str, int]:
        """Read orders."""
        return {"page_size": page_size}

    result = asyncio.run(get_order_list(page_size=7))

    assert result == {"page_size": 7}
    assert logger.query(action="get_order_list", status="success")
