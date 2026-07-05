# ============================================================================ #
# Tool Permission Model for OmniSell AI
# ============================================================================ #
# Each tool is wrapped with a permission descriptor that validates:
#   1. Parameter schema  — type, range, required fields
#   2. Caller permissions — which agent roles / OAuth scopes are allowed
#   3. Tenant scope       — ensures cross-tenant data isolation
#   4. Guardrail binding  — ties into DomainGuardrail and HITL rules
#
# Secure-by-design but backward-compatible: tools not registered in the
# permission registry pass through without restriction, allowing incremental
# adoption. Only explicitly registered tools are enforced.
# ============================================================================ #

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

try:
    from .audit import AuditLogger
except ImportError:
    from audit import AuditLogger  # type: ignore[no-redef]

try:
    from .hitl import HITLManager, RiskLevel
except ImportError:
    from hitl import HITLManager, RiskLevel  # type: ignore[no-redef]


T = TypeVar("T")

# ============================================================================ #
# Permission scope primitives
# ============================================================================ #


class CallerRole(str, Enum):
    """Who is invoking the tool — used for permission resolution."""
    SYSTEM = "system"
    CS_AGENT = "cs_agent"
    SYNC_AGENT = "sync_agent"
    PRICE_AGENT = "price_agent"
    ADMIN_API = "admin_api"
    CUSTOMER_API = "customer_api"
    HUMAN_OPERATOR = "human_operator"


class TenantScope(str, Enum):
    """Scope of tenant data access for a tool invocation."""
    SINGLE_TENANT = "single_tenant"
    CROSS_TENANT_AGGREGATE = "cross_tenant_aggregate"
    GLOBAL = "global"


class OperationType(str, Enum):
    """The type of operation being performed."""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    ADMIN = "admin"


@dataclass(frozen=True)
class ParamSpec:
    """Specification for a single tool parameter."""
    name: str
    param_type: type
    required: bool = True
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[Any] | None = None
    regex_pattern: str | None = None
    description: str = ""


@dataclass(frozen=True)
class ToolPermission:
    """Permission descriptor attached to each tool."""
    name: str
    description: str
    operation_type: OperationType
    risk_level: RiskLevel
    allowed_callers: list[CallerRole] = field(default_factory=lambda: list(CallerRole))
    tenant_scope: TenantScope = TenantScope.SINGLE_TENANT
    params: list[ParamSpec] = field(default_factory=list)
    requires_hitl: bool = False
    requires_second_verification: bool = False
    refund_related: bool = False


# ============================================================================ #
# Permission validation result
# ============================================================================ #


@dataclass(frozen=True)
class PermissionCheckResult:
    """Result of a permission check on a tool invocation."""
    allowed: bool
    reason: str = ""
    violations: list[str] = field(default_factory=list)
    escalated_to_hitl: bool = False
    hitl_request_id: str = ""


class PermissionDeniedError(RuntimeError):
    """Raised when a tool invocation fails permission checks."""
    def __init__(self, result: PermissionCheckResult) -> None:
        self.result = result
        super().__init__(f"Permission denied: {'; '.join(result.violations)}")


# ============================================================================ #
# Permission Registry
# ============================================================================ #


class PermissionRegistry:
    """Central registry that maps tool names to their Permission descriptors.

    Backward compatible: tools NOT registered pass through without restriction.
    Only explicitly registered tools are subject to permission enforcement.
    This enables incremental adoption — register tools as their permissions
    are defined, without breaking existing unregistered tools.
    """

    def __init__(self, audit_logger: AuditLogger | None = None) -> None:
        self._permissions: dict[str, ToolPermission] = {}
        self._hitl_manager: HITLManager | None = None
        self._audit_logger = audit_logger

    def set_hitl_manager(self, hitl_manager: HITLManager) -> None:
        self._hitl_manager = hitl_manager

    def register(self, permission: ToolPermission) -> None:
        """Register a tool permission descriptor."""
        self._permissions[permission.name] = permission

    def get(self, tool_name: str) -> ToolPermission | None:
        """Look up a tool''s permission descriptor."""
        return self._permissions.get(tool_name)

    def check(
        self,
        tool_name: str,
        caller: CallerRole,
        tenant_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> PermissionCheckResult:
        """Validate a tool invocation against its permission descriptor.

        Tools NOT registered in the permission registry pass through
        without restriction (backward compatible).
        """
        perm = self._permissions.get(tool_name)
        if perm is None:
            # Backward compatible: unregistered tools pass through
            return PermissionCheckResult(
                allowed=True,
                reason=f"Tool '{tool_name}' is not registered — passing through (backward compatible).",
            )

        violations: list[str] = []

        # 1. Caller check
        if perm.allowed_callers and caller not in perm.allowed_callers:
            violations.append(
                f"caller_not_allowed: {caller.value} not in {[c.value for c in perm.allowed_callers]}"
            )

        # 2. Tenant scope check
        if perm.tenant_scope == TenantScope.SINGLE_TENANT and not tenant_id:
            violations.append("tenant_required: tool requires a tenant context")

        # 3. Parameter validation
        if perm.params:
            param_violations = self._validate_params(perm.params, params or {})
            violations.extend(param_violations)

        # 4. Refund constraint / HITL — always escalate for these
        needs_hitl = perm.requires_hitl or perm.refund_related

        if violations:
            self._audit_denied(perm, caller, violations)
            return PermissionCheckResult(
                allowed=False,
                reason="Permission check failed.",
                violations=violations,
            )

        # HITL escalation for high-risk operations
        if needs_hitl and perm.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            hitl_request_id = self._escalate_to_hitl(perm, caller, params or {})
            return PermissionCheckResult(
                allowed=False,
                reason=f"Operation requires human approval: {hitl_request_id}",
                violations=[f"hitl_required: {perm.name}"],
                escalated_to_hitl=True,
                hitl_request_id=hitl_request_id,
            )

        return PermissionCheckResult(allowed=True)

    def guard(
        self,
        permission: ToolPermission | None = None,
        *,
        caller_override: CallerRole | None = None,
        tenant_id: str = "",
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator that wraps a function with permission checks."""

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            nonlocal permission
            if permission is None:
                permission = ToolPermission(
                    name=func.__name__,
                    description=inspect.getdoc(func) or "",
                    operation_type=OperationType.READ,
                    risk_level=RiskLevel.LOW,
                    allowed_callers=list(CallerRole),
                    tenant_scope=TenantScope.GLOBAL,
                )
            self.register(permission)

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> T:
                result = self.check(
                    tool_name=permission.name,
                    caller=caller_override or CallerRole.SYSTEM,
                    tenant_id=tenant_id if tenant_id else None,
                    params=kwargs,
                )
                if not result.allowed:
                    raise PermissionDeniedError(result)
                return func(*args, **kwargs)

            return wrapper

        return decorator

    def _validate_params(
        self, specs: list[ParamSpec], params: dict[str, Any]
    ) -> list[str]:
        violations: list[str] = []
        for spec in specs:
            value = params.get(spec.name)
            if spec.required and value is None:
                violations.append(f"missing_required_param: {spec.name}")
                continue
            if value is None:
                continue
            if not isinstance(value, spec.param_type):
                violations.append(
                    f"param_type_mismatch: {spec.name} expected {spec.param_type.__name__}, got {type(value).__name__}"
                )
                continue
            if isinstance(value, (int, float)):
                if spec.min_value is not None and value < spec.min_value:
                    violations.append(f"param_out_of_range: {spec.name} < {spec.min_value}")
                if spec.max_value is not None and value > spec.max_value:
                    violations.append(f"param_out_of_range: {spec.name} > {spec.max_value}")
            if spec.allowed_values is not None and value not in spec.allowed_values:
                violations.append(
                    f"param_invalid_value: {spec.name}={value} not in {spec.allowed_values}"
                )
            if spec.regex_pattern is not None:
                import re
                if not re.match(spec.regex_pattern, str(value)):
                    violations.append(f"param_pattern_mismatch: {spec.name}")
        return violations

    def _escalate_to_hitl(
        self, perm: ToolPermission, caller: CallerRole, params: dict[str, Any]
    ) -> str:
        if self._hitl_manager is None:
            raise RuntimeError("HITLManager not configured for permission escalation.")
        approval = self._hitl_manager.submit_request(
            action=perm.name,
            payload={
                "caller": caller.value,
                "params": params,
                "risk_level": perm.risk_level.value,
                "operation_type": perm.operation_type.value,
                "requires_second_verification": perm.requires_second_verification,
            },
            risk_level=perm.risk_level,
            reason=f"Auto-escalated by permission model: {perm.name}",
        )
        return approval.request_id

    def _audit_denied(
        self, perm: ToolPermission, caller: CallerRole, violations: list[str]
    ) -> None:
        if self._audit_logger is None:
            return
        self._audit_logger.log(
            event_type="permission_denied",
            action=perm.name,
            status="denied",
            details={
                "caller": caller.value,
                "violations": violations,
                "operation_type": perm.operation_type.value,
                "risk_level": perm.risk_level.value,
            },
            actor="permission_registry",
            risk_level=perm.risk_level.value,
        )


# ============================================================================ #
# Pre-built permission descriptors for standard OmniSell tools
# ============================================================================ #


STANDARD_TOOL_PERMISSIONS: list[ToolPermission] = [
    # --- Product tools ---
    ToolPermission(
        name="get_product_list",
        description="Fetch paginated product list",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.SYNC_AGENT, CallerRole.ADMIN_API],
        tenant_scope=TenantScope.SINGLE_TENANT,
    ),
    ToolPermission(
        name="get_product_detail",
        description="Fetch product details by ID",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.SYNC_AGENT, CallerRole.ADMIN_API],
        tenant_scope=TenantScope.SINGLE_TENANT,
        params=[ParamSpec("product_id", str, required=True)],
    ),
    ToolPermission(
        name="update_product",
        description="Update product fields (price, stock, title, description)",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=[CallerRole.SYNC_AGENT, CallerRole.PRICE_AGENT, CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.SINGLE_TENANT,
        params=[ParamSpec("product_id", str, required=True)],
    ),
    ToolPermission(
        name="create_product",
        description="Create a new product",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=[CallerRole.SYNC_AGENT, CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.SINGLE_TENANT,
    ),
    ToolPermission(
        name="delete_product",
        description="Soft-delete a product",
        operation_type=OperationType.DELETE,
        risk_level=RiskLevel.HIGH,
        allowed_callers=[CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.SINGLE_TENANT,
        params=[ParamSpec("product_id", str, required=True)],
        requires_hitl=True,
    ),
    # --- Order tools ---
    ToolPermission(
        name="get_order_list",
        description="Fetch paginated order list",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.SYNC_AGENT, CallerRole.ADMIN_API],
        tenant_scope=TenantScope.SINGLE_TENANT,
    ),
    ToolPermission(
        name="update_order_status",
        description="Update an order status",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=[CallerRole.SYNC_AGENT, CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.SINGLE_TENANT,
        params=[
            ParamSpec("order_id", str, required=True),
            ParamSpec("status", str, required=True),
        ],
    ),
    ToolPermission(
        name="approve_return",
        description="Approve a pending return request",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=[CallerRole.SYNC_AGENT, CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.SINGLE_TENANT,
        params=[ParamSpec("return_id", str, required=True)],
    ),
    # --- Pricing tools ---
    ToolPermission(
        name="get_product_price",
        description="Fetch current product price",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.PRICE_AGENT, CallerRole.ADMIN_API],
        tenant_scope=TenantScope.SINGLE_TENANT,
        params=[ParamSpec("product_id", str, required=True)],
    ),
    ToolPermission(
        name="fetch_competitor_prices",
        description="Scrape competitor prices for a product",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.PRICE_AGENT, CallerRole.ADMIN_API],
        tenant_scope=TenantScope.CROSS_TENANT_AGGREGATE,
    ),
    # --- RAG / Knowledge tools ---
    ToolPermission(
        name="search_knowledge_base",
        description="Search the RAG knowledge base",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.SYNC_AGENT, CallerRole.CUSTOMER_API],
        tenant_scope=TenantScope.GLOBAL,
    ),
    ToolPermission(
        name="answer_customer_question",
        description="Answer customer question using RAG grounding",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.CUSTOMER_API],
        tenant_scope=TenantScope.GLOBAL,
    ),
    # --- Translation tools ---
    ToolPermission(
        name="translate_text",
        description="Translate text between languages",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
    ),
    ToolPermission(
        name="detect_and_translate",
        description="Detect language and translate",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
    ),
    # --- Vision tools ---
    ToolPermission(
        name="analyze_customer_image",
        description="Analyze a customer-uploaded image",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.CUSTOMER_API],
        tenant_scope=TenantScope.SINGLE_TENANT,
    ),
    ToolPermission(
        name="answer_visual_customer_question",
        description="Answer a visual customer question",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=[CallerRole.CS_AGENT, CallerRole.CUSTOMER_API],
        tenant_scope=TenantScope.SINGLE_TENANT,
    ),
]


def register_standard_permissions(registry: PermissionRegistry) -> None:
    """Register all standard OmniSell tool permissions into the registry."""
    for perm in STANDARD_TOOL_PERMISSIONS:
        registry.register(perm)


__all__ = [
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
