# ============================================================================ #
# Refund Constraint Module — OmniSell AI Safety Boundary
# ============================================================================ #
# This module enforces a strict "NEVER execute refund" boundary for all AI
# agents. Every refund-related operation is locked behind human approval with
# second-verifier requirements, creating a clear and auditable AI safety
# boundary that is easy to demonstrate and explain.
#
# Design principle (interview-ready summary):
#   "AI agents can read orders, suggest resolutions, and prepare refund
#    drafts, but the final execution of any refund — whether full, partial,
#    or goodwill — MUST go through human approval with audit trail.
#    This is a hard boundary, not a policy suggestion."
# ============================================================================ #

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

try:
    from .audit import AuditLogger
except ImportError:
    from audit import AuditLogger  # type: ignore[no-redef]

try:
    from .hitl import ApprovalRequest, ApprovalStatus, HITLManager, RiskLevel
except ImportError:
    from hitl import ApprovalRequest, ApprovalStatus, HITLManager, RiskLevel  # type: ignore[no-redef]


# ============================================================================ #
# Refund detection — bilingual keyword matching
# ============================================================================ #


REFUND_KEYWORDS_CN = [
    "退款", "退钱", "退还", "返款", "赔付", "赔偿", "补偿金",
    "全额退", "部分退", "差价退", "退款申请", "退款处理",
    "执行退款", "自动退款", "发起退款", "原路退回",
]

REFUND_KEYWORDS_EN = [
    "refund", "reimburse", "reimbursement", "chargeback",
    "money back", "return money", "full refund", "partial refund",
    "issue refund", "process refund", "execute refund",
    "automatic refund", "auto refund", "refund order",
    "compensation", "goodwill credit", "pay back",
]

REFUND_DETECTION_PATTERN = re.compile(
    r"|".join(REFUND_KEYWORDS_CN + REFUND_KEYWORDS_EN),
    re.IGNORECASE,
)

FORBIDDEN_REFUND_ACTIONS: set[str] = {
    "issue_refund",
    "process_refund",
    "execute_refund",
    "auto_refund",
    "automatic_refund",
    "approve_refund",
    "refund_order",
    "bulk_refund",
    "batch_refund",
}


class RefundActionType(str, Enum):
    """Classification of a refund-related action."""
    INQUIRY = "inquiry"           # Customer asking about refund policy
    SUGGESTION = "suggestion"     # Agent suggesting a refund pathway
    PREPARATION = "preparation"   # Preparing refund calculation/draft
    EXECUTION = "execution"       # Actually executing a refund — BLOCKED


@dataclass(frozen=True)
class RefundCheckResult:
    """Result of a refund constraint check."""
    contains_refund_language: bool
    action_type: RefundActionType
    blocked: bool
    reason: str = ""
    hitl_request_id: str = ""


class RefundExecutionBlockedError(RuntimeError):
    """Raised when an agent attempts to execute a refund directly.

    This is the hard safety boundary: AI agents NEVER execute refunds.
    """

    def __init__(self, check_result: RefundCheckResult) -> None:
        self.check_result = check_result
        super().__init__(
            f"REFUND EXECUTION BLOCKED: {check_result.reason}. "
            "All refund execution must go through human approval."
        )


# ============================================================================ #
# Agent system-prompt injection — "NEVER execute refund" instructions
# ============================================================================ #

AGENT_REFUND_CONSTRAINT_PROMPT = """
【AI 安全边界 — 退款约束 / AI Safety Boundary — Refund Constraint】
你被明确禁止执行任何退款操作。这是不可协商的硬性安全边界。
You are explicitly forbidden from executing any refund operation. This is a non-negotiable hard safety boundary.

规则 / Rules:
1. 绝不可调用任何退款执行工具（issue_refund, process_refund, execute_refund 等）。
   NEVER call any refund execution tool.
2. 你可以查询退款政策、解释退款流程、建议客户如何申请退款。
   You MAY query refund policies, explain refund procedures, and suggest how customers can request refunds.
3. 你可以准备退款计算草稿，但必须在回复中明确标注"以下为草稿，实际退款需人工审批"。
   You MAY prepare refund calculation drafts, but MUST label them "Draft — actual refund requires human approval."
4. 如果客户坚持要求立即退款，你必须回复："退款执行必须由人工客服审批处理。我已为您记录退款意向，人工客服将在工作时间与您联系。"
   If a customer insists on immediate refund, you MUST reply: "Refund execution requires human agent approval. I have logged your refund intent. A human agent will contact you during business hours."
5. 违反此约束将被审计记录，并触发安全告警。
   Violation of this constraint will be audit-logged and trigger a security alert.
"""


# ============================================================================ #
# Refund Guard — the enforcement engine
# ============================================================================ #


@dataclass
class RefundGuard:
    """Enforces the NO-REFUND constraint across all agent actions.

    This is the cornerstone AI safety boundary: any operation that involves
    executing a refund is automatically escalated to HITL with mandatory
    second-verification, and every agent is instructed to never attempt it.
    """

    hitl_manager: HITLManager
    audit_logger: AuditLogger | None = None
    blocked_actions: set[str] = field(default_factory=lambda: FORBIDDEN_REFUND_ACTIONS.copy())

    def detect_refund_language(self, text: str) -> bool:
        """Check if text contains refund-related keywords in Chinese or English."""
        return bool(REFUND_DETECTION_PATTERN.search(text))

    def classify_action(
        self, action_name: str, payload: dict[str, Any] | None = None
    ) -> RefundActionType:
        """Classify a tool action into refund action types."""
        action_lower = action_name.lower()

        # Direct execution tools are always execution
        if action_lower in self.blocked_actions:
            return RefundActionType.EXECUTION

        # Check if the action name itself contains refund keywords
        if self.detect_refund_language(action_name):
            return RefundActionType.EXECUTION

        # Check payload for refund keywords
        payload_str = str(payload or {}).lower()
        if self.detect_refund_language(payload_str):
            if any(kw in action_lower for kw in ("create", "execute", "process", "do", "apply")):
                return RefundActionType.EXECUTION
            if any(kw in action_lower for kw in ("get", "query", "read", "list", "search")):
                return RefundActionType.INQUIRY
            return RefundActionType.SUGGESTION

        return RefundActionType.INQUIRY

    def check(self, action_name: str, payload: dict[str, Any] | None = None) -> RefundCheckResult:
        """Check if an action violates the refund constraint.

        Returns a RefundCheckResult. If blocked=True, the action MUST NOT
        proceed without human approval.
        """
        action_type = self.classify_action(action_name, payload)

        if action_type != RefundActionType.EXECUTION:
            return RefundCheckResult(
                contains_refund_language=self.detect_refund_language(
                    action_name + str(payload or {})
                ),
                action_type=action_type,
                blocked=False,
            )

        # EXECUTION — hard block, escalate to HITL
        hitl_request = self.hitl_manager.submit_request(
            action=action_name,
            payload=payload or {},
            risk_level=RiskLevel.CRITICAL,
            reason=f"Refund execution escalated to HITL: {action_name}",
        )

        if self.audit_logger:
            self.audit_logger.log(
                event_type="refund_blocked",
                action=action_name,
                status="blocked_pending_hitl",
                details={
                    "action_type": action_type.value,
                    "payload_preview": str(payload)[:200] if payload else "",
                    "hitl_request_id": hitl_request.request_id,
                    "policy": "NEVER_EXECUTE_REFUND_WITHOUT_HUMAN",
                },
                actor="refund_guard",
                risk_level="critical",
            )

        return RefundCheckResult(
            contains_refund_language=True,
            action_type=RefundActionType.EXECUTION,
            blocked=True,
            reason=(
                "Refund execution is strictly prohibited for AI agents. "
                f"Escalated to HITL: {hitl_request.request_id}. "
                "A human operator with second-verifier must approve."
            ),
            hitl_request_id=hitl_request.request_id,
        )

    def check_or_raise(self, action_name: str, payload: dict[str, Any] | None = None) -> RefundCheckResult:
        """Like check() but raises RefundExecutionBlockedError on block."""
        result = self.check(action_name, payload)
        if result.blocked:
            raise RefundExecutionBlockedError(result)
        return result

    def inject_constraint_prompt(self, system_prompt: str) -> str:
        """Inject the NO-REFUND constraint into an agent's system prompt.

        Returns the modified prompt with the constraint appended.
        """
        if AGENT_REFUND_CONSTRAINT_PROMPT in system_prompt:
            return system_prompt
        return system_prompt + "\n\n" + AGENT_REFUND_CONSTRAINT_PROMPT


__all__ = [
    "AGENT_REFUND_CONSTRAINT_PROMPT",
    "REFUND_KEYWORDS_CN",
    "REFUND_KEYWORDS_EN",
    "RefundActionType",
    "RefundCheckResult",
    "RefundExecutionBlockedError",
    "RefundGuard",
]
