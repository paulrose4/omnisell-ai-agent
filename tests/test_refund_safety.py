from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.base import safe_tool_execution
from agents.cs_agent import CS_AGENT_SYSTEM_PROMPT, create_cs_agent
from agents.price_agent import PRICE_AGENT_SYSTEM_PROMPT, create_price_agent
from agents.sync_agent import SYNC_AGENT_SYSTEM_PROMPT, create_sync_agent
from safety.audit import AuditLogger
from safety.circuit_breaker import CircuitBreaker
from safety.hitl import ApprovalRejectedError, ApprovalStatus, HITLManager, RiskLevel
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


def test_created_agents_receive_hard_refund_system_message() -> None:
    for agent in (create_cs_agent(llm_config=False), create_sync_agent(llm_config=False), create_price_agent(llm_config=False)):
        assert REFUND_HANDOFF_REPLY in agent.system_message
        assert agent.system_message.index(REFUND_HANDOFF_REPLY) < agent.system_message.index("Agent")


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


def test_hitl_refund_is_critical_and_requires_double_verification(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    risk, reason = manager.assess_risk("approve_refund", {"order_id": "O1001"})
    request = manager.submit_request("approve_refund", {"order_id": "O1001"}, requester="agent")

    assert risk == RiskLevel.CRITICAL
    assert "REFUND_RISK" in reason
    assert request.status == ApprovalStatus.PENDING
    assert request.risk_level == RiskLevel.CRITICAL
    assert request.requires_second_verification is True


def test_hitl_refund_timeout_is_72_hours(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    request = manager.submit_request("issue_refund", {"amount": 10.0})

    assert request.expires_at - request.created_at == timedelta(hours=72)


def test_hitl_refund_explicit_low_risk_cannot_override_policy(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))

    request = manager.submit_request("manual_review", {"risk_level": "low", "note": "退款给客户"})

    assert request.risk_level == RiskLevel.CRITICAL
    assert request.status == ApprovalStatus.PENDING
    assert request.requires_second_verification is True


def test_hitl_refund_single_approver_is_rejected(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))
    request = manager.submit_request("refund_review", {"order_id": "O1001"})

    with pytest.raises(ApprovalRejectedError):
        manager.approve(request.request_id, approver="lead")

    assert manager.get_request(request.request_id).status == ApprovalStatus.PENDING  # type: ignore[union-attr]


def test_hitl_refund_double_approver_succeeds(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=_logger(tmp_path))
    request = manager.submit_request("refund_review", {"order_id": "O1001"})

    approved = manager.approve(request.request_id, approver="lead", second_verifier="owner")

    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decided_by == "lead+owner"


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
