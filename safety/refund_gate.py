from __future__ import annotations

import json
from typing import Any

from .audit import AuditLogger

REFUND_KEYWORDS: tuple[str, ...] = (
    "refund",
    "退款",
    "退钱",
    "退费",
    "退差价",
    "chargeback",
    "逆向交易",
    "补偿",
    "赔付",
    "reimburse",
)

REFUND_BLOCK_MESSAGE = "Refund-related tool calls are blocked and require human support."
REFUND_HANDOFF_REPLY = (
    "退款请求已记录，将转交人工客服处理。Refund request logged and will be forwarded to human support."
)


class RefundBlockedError(RuntimeError):
    """Raised when a tool call appears to involve refund processing."""

    def __init__(self, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(REFUND_BLOCK_MESSAGE)


class RefundGate:
    """Detect and block refund-like tool actions before execution."""

    def __init__(self, audit_logger: AuditLogger | None = None, keywords: tuple[str, ...] = REFUND_KEYWORDS) -> None:
        self.audit_logger = audit_logger or AuditLogger()
        self.keywords = tuple(keyword.lower() for keyword in keywords)

    def check_refund_risk(self, action: str, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        """Return whether an action/payload contains refund-risk language."""
        haystack = f"{action}\n{_flatten_for_scan(payload or {})}".lower()
        for keyword in self.keywords:
            if keyword in haystack:
                return True, f"REFUND_RISK keyword={keyword}"
        return False, ""

    def enforce(self, action: str, payload: dict[str, Any] | None = None, actor: str = "agent_tool") -> None:
        risky, reason = self.check_refund_risk(action, payload)
        if not risky:
            return
        self.audit_logger.log(
            event_type="refund_blocked",
            action=action,
            status="blocked",
            details={
                "reason": reason,
                "payload_keys": sorted((payload or {}).keys()),
            },
            actor=actor,
            risk_level="critical",
        )
        raise RefundBlockedError(action, reason)


def contains_refund_risk(action: str, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
    return RefundGate().check_refund_risk(action, payload)


def _flatten_for_scan(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return "\n".join(f"{key}:{_flatten_for_scan(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_flatten_for_scan(item) for item in value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


__all__ = [
    "REFUND_BLOCK_MESSAGE",
    "REFUND_HANDOFF_REPLY",
    "REFUND_KEYWORDS",
    "RefundBlockedError",
    "RefundGate",
    "contains_refund_risk",
]
