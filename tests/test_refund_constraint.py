from __future__ import annotations

from pathlib import Path

import pytest

from safety.audit import AuditLogger
from safety.hitl import HITLManager, RiskLevel
from safety.refund_constraint import (
    AGENT_REFUND_CONSTRAINT_PROMPT,
    RefundActionType,
    RefundCheckResult,
    RefundExecutionBlockedError,
    RefundGuard,
)


def test_detect_refund_language_chinese() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    assert guard.detect_refund_language("我要退款") is True
    assert guard.detect_refund_language("请问退款流程是什么") is True
    assert guard.detect_refund_language("什么时候退钱") is True
    assert guard.detect_refund_language("申请赔付") is True
    assert guard.detect_refund_language("补偿金到账了吗") is True


def test_detect_refund_language_english() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    assert guard.detect_refund_language("I want a refund") is True
    assert guard.detect_refund_language("Please process reimbursement") is True
    assert guard.detect_refund_language("full refund please") is True
    assert guard.detect_refund_language("issue a chargeback") is True


def test_detect_refund_language_no_refund() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    assert guard.detect_refund_language("how to update product price") is False
    assert guard.detect_refund_language("请帮我上架商品") is False
    assert guard.detect_refund_language("show me the order status") is False


def test_classify_inquiry_action() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.classify_action("search_knowledge_base", {"query": "refund policy"})
    assert result == RefundActionType.INQUIRY


def test_classify_suggestion_action() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.classify_action("answer_customer_question", {"question": "how do I get a refund?"})
    assert result == RefundActionType.SUGGESTION


def test_classify_execution_action_direct() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    # Direct execution tool name
    result = guard.classify_action("execute_refund", {})
    assert result == RefundActionType.EXECUTION

    result = guard.classify_action("process_refund", {"order_id": "O123"})
    assert result == RefundActionType.EXECUTION

    result = guard.classify_action("auto_refund", {})
    assert result == RefundActionType.EXECUTION


def test_classify_execution_action_in_payload() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.classify_action("create_transaction", {"type": "refund", "amount": 100})
    assert result == RefundActionType.EXECUTION


# ============================================================================ #
# Check — refund inquiries allowed
# ============================================================================ #


def test_refund_inquiry_allowed() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.check("search_knowledge_base", {"query": "refund policy"})
    assert result.blocked is False
    assert result.action_type == RefundActionType.INQUIRY


def test_refund_explanation_allowed() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.check("answer_customer_question", {"question": "how to refund"})
    assert result.blocked is False
    assert result.action_type in {RefundActionType.INQUIRY, RefundActionType.SUGGESTION}


# ============================================================================ #
# Check — refund execution blocked
# ============================================================================ #


def test_refund_execution_blocked_with_hitl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    hitl = HITLManager(audit_logger=logger)
    guard = RefundGuard(hitl_manager=hitl, audit_logger=logger)

    result = guard.check("execute_refund", {"order_id": "O-123", "amount": 99.99})

    assert result.blocked is True
    assert result.action_type == RefundActionType.EXECUTION
    assert "Refund execution is strictly prohibited" in result.reason
    assert result.hitl_request_id != ""

    # Verify HITL request created
    pending = hitl.get_pending()
    assert len(pending) == 1
    assert pending[0].action == "execute_refund"
    assert pending[0].risk_level == RiskLevel.CRITICAL


def test_refund_execution_audit_record(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    hitl = HITLManager(audit_logger=logger)
    guard = RefundGuard(hitl_manager=hitl, audit_logger=logger)

    guard.check("process_refund", {})

    records = logger.query(event_type="refund_blocked")
    assert len(records) == 1
    assert records[0]["action"] == "process_refund"
    assert records[0]["status"] == "blocked_pending_hitl"
    assert records[0]["risk_level"] == "critical"
    assert records[0]["details"]["policy"] == "NEVER_EXECUTE_REFUND_WITHOUT_HUMAN"


# ============================================================================ #
# Check or raise
# ============================================================================ #


def test_check_or_raise_allows_inquiry() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.check_or_raise("search_knowledge_base", {"query": "refund"})
    assert result.blocked is False


def test_check_or_raise_blocks_execution() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    with pytest.raises(RefundExecutionBlockedError) as exc:
        guard.check_or_raise("execute_refund", {})
    assert exc.value.check_result.blocked is True
    assert exc.value.check_result.action_type == RefundActionType.EXECUTION


# ============================================================================ #
# Constraint prompt injection
# ============================================================================ #


def test_constraint_prompt_injected() -> None:
    original = "Original system prompt."
    guard = RefundGuard(hitl_manager=HITLManager())
    result = guard.inject_constraint_prompt(original)
    assert AGENT_REFUND_CONSTRAINT_PROMPT in result
    assert original in result


def test_constraint_prompt_no_double_injection() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    once = guard.inject_constraint_prompt("Original")
    twice = guard.inject_constraint_prompt(once)
    assert twice == once


# ============================================================================ #
# Constraint prompt content
# ============================================================================ #


def test_constraint_prompt_contains_refund_instructions() -> None:
    prompt_lower = AGENT_REFUND_CONSTRAINT_PROMPT.lower()
    assert "退款" in AGENT_REFUND_CONSTRAINT_PROMPT
    assert "refund" in prompt_lower
    assert "forbidden" in prompt_lower or "never" in prompt_lower or "禁止" in AGENT_REFUND_CONSTRAINT_PROMPT
    assert "human" in prompt_lower
    assert "audit" in prompt_lower or "审计" in AGENT_REFUND_CONSTRAINT_PROMPT
    assert "人工" in AGENT_REFUND_CONSTRAINT_PROMPT


# ============================================================================ #
# RefundCheckResult properties
# ============================================================================ #


def test_check_result_allowed() -> None:
    result = RefundCheckResult(
        contains_refund_language=True,
        action_type=RefundActionType.INQUIRY,
        blocked=False,
    )
    assert result.blocked is False


def test_check_result_blocked() -> None:
    result = RefundCheckResult(
        contains_refund_language=True,
        action_type=RefundActionType.EXECUTION,
        blocked=True,
        reason="Blocked: refund execution requires human approval",
        hitl_request_id="req-456",
    )
    assert result.blocked is True
    assert result.hitl_request_id == "req-456"


# ============================================================================ #
# Bilingual coverage
# ============================================================================ #


def test_keyword_lists_are_nonempty() -> None:
    from safety.refund_constraint import REFUND_KEYWORDS_CN, REFUND_KEYWORDS_EN
    assert len(REFUND_KEYWORDS_CN) > 5
    assert len(REFUND_KEYWORDS_EN) > 5


def test_both_languages_covered() -> None:
    guard = RefundGuard(hitl_manager=HITLManager())
    assert guard.detect_refund_language("退款申请 处理 执行") is True
    assert guard.detect_refund_language("refund request processing execution") is True
    assert guard.detect_refund_language("process refund payment") is True
    assert guard.detect_refund_language("部分退款 全额退款") is True
