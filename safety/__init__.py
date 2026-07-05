"""Safety primitives for OmniSell AI agents.

Layered safety architecture:
  1. DomainGuardrail   — bilingual keyword-tree input filtering (first line)
  2. ToolPermissions   — parameter, caller, tenant scoped access control
  3. RefundGuard       — hard "NEVER execute refund" AI safety boundary
  4. HITLManager       — risk-level-based human-in-the-loop approval
  5. CircuitBreaker    — failure-count-based tool circuit breaking
  6. AuditLogger       — append-only JSONL audit trail
"""

from .audit import AuditLogger
from .circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from .domain_guardrail import (
    DEFAULT_DOMAIN_TREE,
    DomainBlockedError,
    DomainGuardrail,
    DomainKeywordTree,
    GuardrailResult,
    GuardrailStrategy,
    preprocess,
)
from .hitl import (
    ApprovalRequest,
    ApprovalRequiredError,
    ApprovalStatus,
    HITLManager,
    RiskLevel,
)
from .refund_constraint import (
    AGENT_REFUND_CONSTRAINT_PROMPT,
    RefundActionType,
    RefundCheckResult,
    RefundExecutionBlockedError,
    RefundGuard,
)
from .tool_permissions import (
    CallerRole,
    OperationType,
    ParamSpec,
    PermissionCheckResult,
    PermissionDeniedError,
    PermissionRegistry,
    STANDARD_TOOL_PERMISSIONS,
    TenantScope,
    ToolPermission,
    register_standard_permissions,
)

__all__ = [
    # Audit
    "AuditLogger",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    # Domain guardrail
    "DEFAULT_DOMAIN_TREE",
    "DomainBlockedError",
    "DomainGuardrail",
    "DomainKeywordTree",
    "GuardrailResult",
    "GuardrailStrategy",
    "preprocess",
    # HITL
    "ApprovalRequest",
    "ApprovalRequiredError",
    "ApprovalStatus",
    "HITLManager",
    "RiskLevel",
    # Refund constraint
    "AGENT_REFUND_CONSTRAINT_PROMPT",
    "RefundActionType",
    "RefundCheckResult",
    "RefundExecutionBlockedError",
    "RefundGuard",
    # Tool permissions
    "CallerRole",
    "OperationType",
    "ParamSpec",
    "PermissionCheckResult",
    "PermissionDeniedError",
    "PermissionRegistry",
    "STANDARD_TOOL_PERMISSIONS",
    "TenantScope",
    "ToolPermission",
    "register_standard_permissions",
]
