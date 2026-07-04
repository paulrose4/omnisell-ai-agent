"""Safety primitives for OmniSell AI agents."""

from .audit import AuditLogger
from .circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from .hitl import ApprovalRequest, ApprovalRequiredError, ApprovalStatus, HITLManager, RiskLevel
from .refund_gate import RefundBlockedError, RefundGate
from .tool_auth import (
    PermissionDeniedError,
    ToolCallerContext,
    ToolPermissionValidator,
    get_tool_caller_context,
    reset_tool_caller_context,
    set_tool_caller_context,
    tool_caller_context,
)

__all__ = [
    "AuditLogger",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "ApprovalRequest",
    "ApprovalRequiredError",
    "ApprovalStatus",
    "HITLManager",
    "RiskLevel",
    "RefundBlockedError",
    "RefundGate",
    "PermissionDeniedError",
    "ToolCallerContext",
    "ToolPermissionValidator",
    "get_tool_caller_context",
    "reset_tool_caller_context",
    "set_tool_caller_context",
    "tool_caller_context",
]
