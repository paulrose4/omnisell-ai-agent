from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agents.base import safe_tool_execution
from agents.cs_agent import CS_AGENT_SYSTEM_PROMPT
from agents.price_agent import PRICE_AGENT_SYSTEM_PROMPT
from agents.sync_agent import SYNC_AGENT_SYSTEM_PROMPT
from safety.audit import AuditLogger
from safety.circuit_breaker import CircuitBreaker
from safety.hitl import ApprovalRejectedError, ApprovalRequest, ApprovalStatus, HITLManager, RiskLevel
from safety.refund_gate import REFUND_HANDOFF_REPLY, RefundBlockedError, RefundGate
from safety.tool_auth import ToolPermissionValidator, tool_caller_context


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "audit.jsonl")


def _caller() -> dict[str, Any]:
    return {"user_id": "operator-1", "role": "operator", "tenant_id": "tenant-a"}


def test_cs_agent_prompt_contains_refund_handoff_reply_at_top() -> None:
    assert CS_AGENT_SYSTEM_PROMPT.startswith("⚠️ CRITICAL RULES - VIOLATION IS NOT ALLOWED:")
    assert REFUND_HANDOFF_REPLY in CS_AGENT_SYSTEM_PROMPT
    assert "You are NEVER allowed to execute, initiate, or promise a refund." in CS_AGENT_SYSTEM_PROMPT
    assert "你绝不允许执行、发起或承诺退款。" in CS_AGENT_SYSTEM_PROMPT


def test_sync_agent_prompt_contains_refund_handoff_reply_at_top() -> None:
    assert SYNC_AGENT_SYSTEM_PROMPT.startswith("⚠️ CRITICAL RULES - VIOLATION IS NOT ALLOWED:")
    assert REFUND_HANDOFF_REPLY in SYNC_AGENT_SYSTEM_PROMPT
    assert "你绝不能指示任何工具处理支付撤销或退款。" in SYNC_AGENT_SYSTEM_PROMPT


def test_price_agent_prompt_contains_refund_handoff_reply_at_top() -> None:
    assert PRICE_AGENT_SYSTEM_PROMPT.startswith("⚠️ CRITICAL RULES - VIOLATION IS NOT ALLOWED:")
    assert REFUND_HANDOFF_REPLY in PRICE_AGENT_SYSTEM_PROMPT
    assert "If you are unsure whether an action involves a refund, treat it as a refund and block it." in PRICE_AGENT_SYSTEM_PROMPT


def test_legacy_demo_prompts_retain_hard_refund_boundary() -> None:
    for system_message in (CS_AGENT_SYSTEM_PROMPT, SYNC_AGENT_SYSTEM_PROMPT, PRICE_AGENT_SYSTEM_PROMPT):
        assert REFUND_HANDOFF_REPLY in system_message
        assert system_message.startswith("⚠️ CRITICAL RULES - VIOLATION IS NOT ALLOWED:")
        assert system_message.index(REFUND_HANDOFF_REPLY) < 1000


def test_refund_gate_detects_action_keyword() -> None:
    gate = RefundGate()

    risky, reason = gate.check_refund_risk("process_refund", {"order_id": "O1001"})

    assert risky is True
    assert "REFUND_RISK" in reason


def test_refund_gate_detects_nested_chinese_payload_keyword() -> None:
    gate = RefundGate()

    risky, reason = gate.check_refund_risk("update_order_status", {"notes": {"customer": "客户要求退差价"}})

    assert risky is True
    assert "退差价" in reason


def test_refund_gate_blocks_and_writes_refund_audit(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    gate = RefundGate(audit_logger=logger)

    with pytest.raises(RefundBlockedError):
        gate.enforce("customer_service_note", {"message": "Please reimburse the buyer."}, actor="agent-a")

    records = logger.query(event_type="refund_blocked", action="customer_service_note")
    assert records
    assert records[-1]["status"] == "blocked"
    assert records[-1]["risk_level"] == "critical"


def test_safe_tool_execution_blocks_refund_before_tool_body(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    called = False

    @safe_tool_execution(
        hitl_manager=HITLManager(audit_logger=logger),
        audit_logger=logger,
        circuit_breaker=CircuitBreaker(),
        permission_validator=ToolPermissionValidator(audit_logger=logger),
        refund_gate=RefundGate(audit_logger=logger),
    )
    def update_product(product_id: str, description: str | None = None) -> dict[str, str | None]:
        nonlocal called
        called = True
        return {"product_id": product_id, "description": description}

    with tool_caller_context(_caller()):
        with pytest.raises(RefundBlockedError):
            update_product(product_id="P1001", description="承诺补偿并退款")

    assert called is False
    assert logger.query(event_type="refund_blocked", action="update_product")
    assert not logger.query(action="update_product", status="success")


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("approve_refund", {"order_id": "O1001"}),
        ("payment_reversal", {"order_id": "O1001"}),
        ("cancel_order", {"order_id": "O1001"}),
        ("autonomous_cancellation", {"order_id": "O1001"}),
        ("manual_review", {"action_type": "issue_refund", "order_id": "O1001"}),
        ("manual_review", {"plan": {"action": "cancel_order"}, "order_id": "O1001"}),
    ],
)
def test_hitl_permanently_prohibited_action_never_enters_approval(
    tmp_path: Path,
    action: str,
    payload: dict[str, Any],
) -> None:
    logger = _logger(tmp_path)
    manager = HITLManager(audit_logger=logger)

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request(action, payload, requester="operator")

    assert manager.get_pending() == []
    assert logger.query(action=action, status="blocked")


def test_hitl_rejects_legacy_pending_refund_instead_of_approving(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    manager = HITLManager(audit_logger=logger)
    legacy = ApprovalRequest(
        request_id="legacy-refund",
        action="approve_refund",
        risk_level=RiskLevel.CRITICAL,
        payload={"order_id": "O1001"},
        requester="legacy-requester",
        status=ApprovalStatus.PENDING,
    )
    manager._requests[legacy.request_id] = legacy

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.approve(legacy.request_id, approver="lead")

    assert legacy.status == ApprovalStatus.REJECTED
    assert logger.query(action="approve_refund", status="blocked")


def test_hitl_refund_is_critical_and_requires_double_verification(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    risk, reason = manager.assess_risk("approve_refund", {"order_id": "O1001"})

    assert risk == RiskLevel.CRITICAL
    assert "REFUND_RISK" in reason
    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request("approve_refund", {"order_id": "O1001"}, requester="agent")
    assert manager.get_pending() == []


def test_hitl_refund_never_receives_an_approval_timeout(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request("issue_refund", {"amount": 10.0})
    assert manager.get_pending() == []


def test_hitl_refund_explicit_low_risk_cannot_override_policy(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request(
            "manual_review",
            {"risk_level": "low", "action_type": "issue_refund", "note": "review requested"},
        )
    assert manager.get_pending() == []


def test_hitl_refund_single_approver_is_rejected(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request("refund_review", {"order_id": "O1001"})
    assert manager.get_pending() == []


def test_hitl_refund_double_approver_cannot_override_permanent_block(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request("refund_review", {"order_id": "O1001"})
    assert manager.get_pending() == []


def test_normal_product_query_is_not_blocked_by_refund_gate(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    gate = RefundGate(audit_logger=logger)

    risky, reason = gate.check_refund_risk("get_product_detail", {"product_id": "P1001", "title": "Bluetooth earbuds"})
    gate.enforce("get_product_detail", {"product_id": "P1001", "title": "Bluetooth earbuds"})

    assert risky is False
    assert reason == ""
    assert not logger.query(event_type="refund_blocked")


def test_normal_product_query_runs_through_safe_tool_execution(tmp_path: Path) -> None:
    logger = _logger(tmp_path)

    @safe_tool_execution(
        hitl_manager=HITLManager(audit_logger=logger),
        audit_logger=logger,
        circuit_breaker=CircuitBreaker(),
        permission_validator=ToolPermissionValidator(audit_logger=logger),
        refund_gate=RefundGate(audit_logger=logger),
    )
    def get_product_detail(product_id: str) -> dict[str, str]:
        return {"product_id": product_id}

    with tool_caller_context({"user_id": "viewer-1", "role": "viewer", "tenant_id": "tenant-a"}):
        assert get_product_detail("P1001") == {"product_id": "P1001"}

    assert logger.query(action="get_product_detail", status="success")
    assert not logger.query(event_type="refund_blocked")
