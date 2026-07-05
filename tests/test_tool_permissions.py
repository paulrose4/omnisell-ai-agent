from __future__ import annotations

from pathlib import Path

import pytest

from safety.audit import AuditLogger
from safety.hitl import ApprovalStatus, HITLManager, RiskLevel
from safety.tool_permissions import (
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


# ============================================================================ #
# Registry basics
# ============================================================================ #


def test_registry_allows_unregistered_tool_backward_compat() -> None:
    """Unregistered tools pass through — backward compatible."""
    registry = PermissionRegistry()
    result = registry.check("unknown_tool", caller=CallerRole.CS_AGENT)
    assert result.allowed is True
    assert "backward compatible" in result.reason.lower()


def test_register_and_lookup() -> None:
    registry = PermissionRegistry()
    perm = ToolPermission(
        name="test_tool",
        description="A test tool",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        tenant_scope=TenantScope.GLOBAL,
    )
    registry.register(perm)
    assert registry.get("test_tool") is perm
    assert registry.get("nonexistent") is None


def test_register_standard_permissions() -> None:
    registry = PermissionRegistry()
    register_standard_permissions(registry)
    # Should register all standard permissions
    assert registry.get("get_product_list") is not None
    assert registry.get("update_product") is not None
    assert registry.get("delete_product") is not None
    assert registry.get("search_knowledge_base") is not None


# ============================================================================ #
# Caller role checks
# ============================================================================ #


def test_caller_not_allowed_denied() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="restricted_tool",
        description="Only human operators",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.HIGH,
        allowed_callers=[CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.GLOBAL,
    ))

    result = registry.check("restricted_tool", caller=CallerRole.CS_AGENT)
    assert result.allowed is False
    assert any("caller_not_allowed" in v for v in result.violations)


def test_caller_allowed_passes() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="open_tool",
        description="Anyone can use",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
    ))

    for role in CallerRole:
        result = registry.check("open_tool", caller=role)
        assert result.allowed is True, f"Failed for {role}"


# ============================================================================ #
# Tenant scope checks
# ============================================================================ #


def test_single_tenant_requires_tenant_id() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="tenant_scoped_tool",
        description="Scoped to single tenant",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.SINGLE_TENANT,
    ))

    # Without tenant_id -> denied
    result = registry.check("tenant_scoped_tool", caller=CallerRole.CS_AGENT)
    assert result.allowed is False
    assert any("tenant_required" in v for v in result.violations)

    # With tenant_id -> allowed
    result = registry.check("tenant_scoped_tool", caller=CallerRole.CS_AGENT, tenant_id="T001")
    assert result.allowed is True


def test_global_tenant_no_tenant_required() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="global_tool",
        description="Global scope",
        operation_type=OperationType.READ,
        risk_level=RiskLevel.LOW,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
    ))

    result = registry.check("global_tool", caller=CallerRole.CS_AGENT)
    assert result.allowed is True


# ============================================================================ #
# Parameter validation
# ============================================================================ #


def test_required_param_missing_denied() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="param_tool",
        description="Has required params",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
        params=[ParamSpec("product_id", str, required=True)],
    ))

    result = registry.check("param_tool", caller=CallerRole.SYNC_AGENT)
    assert result.allowed is False
    assert any("missing_required_param" in v for v in result.violations)


def test_param_type_mismatch_denied() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="type_tool",
        description="Expects int",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
        params=[ParamSpec("count", int, required=True)],
    ))

    result = registry.check("type_tool", caller=CallerRole.SYNC_AGENT, params={"count": "not_an_int"})
    assert result.allowed is False
    assert any("param_type_mismatch" in v for v in result.violations)


def test_param_range_validation() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="range_tool",
        description="Has range params",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
        params=[ParamSpec("page_size", int, required=True, min_value=1, max_value=100)],
    ))

    result = registry.check("range_tool", caller=CallerRole.SYNC_AGENT, params={"page_size": 200})
    assert result.allowed is False
    assert any("param_out_of_range" in v for v in result.violations)

    result = registry.check("range_tool", caller=CallerRole.SYNC_AGENT, params={"page_size": 50})
    assert result.allowed is True


def test_param_allowed_values_validation() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="enum_tool",
        description="Has enum params",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
        params=[ParamSpec("status", str, required=True, allowed_values=["active", "inactive", "deleted"])],
    ))

    result = registry.check("enum_tool", caller=CallerRole.SYNC_AGENT, params={"status": "hacked"})
    assert result.allowed is False
    assert any("param_invalid_value" in v for v in result.violations)

    result = registry.check("enum_tool", caller=CallerRole.SYNC_AGENT, params={"status": "active"})
    assert result.allowed is True


def test_param_pattern_validation() -> None:
    registry = PermissionRegistry()
    registry.register(ToolPermission(
        name="pattern_tool",
        description="Has pattern params",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.MEDIUM,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
        params=[ParamSpec("product_id", str, required=True, regex_pattern=r"^P\d{4}$")],
    ))

    result = registry.check("pattern_tool", caller=CallerRole.SYNC_AGENT, params={"product_id": "invalid"})
    assert result.allowed is False
    assert any("param_pattern_mismatch" in v for v in result.violations)

    result = registry.check("pattern_tool", caller=CallerRole.SYNC_AGENT, params={"product_id": "P1001"})
    assert result.allowed is True


# ============================================================================ #
# HITL escalation
# ============================================================================ #


def test_high_risk_tool_escalates_to_hitl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    hitl = HITLManager(audit_logger=logger)
    registry = PermissionRegistry(audit_logger=logger)
    registry.set_hitl_manager(hitl)

    registry.register(ToolPermission(
        name="dangerous_tool",
        description="High risk operation",
        operation_type=OperationType.DELETE,
        risk_level=RiskLevel.HIGH,
        allowed_callers=[CallerRole.HUMAN_OPERATOR],
        tenant_scope=TenantScope.GLOBAL,
        requires_hitl=True,
    ))

    result = registry.check("dangerous_tool", caller=CallerRole.HUMAN_OPERATOR)
    assert result.allowed is False
    assert result.escalated_to_hitl is True
    assert result.hitl_request_id != ""

    # Verify the HITL request was created
    pending = hitl.get_pending()
    assert len(pending) == 1
    assert pending[0].action == "dangerous_tool"
    assert pending[0].risk_level == RiskLevel.HIGH


def test_refund_related_tool_always_hitl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    hitl = HITLManager(audit_logger=logger)
    registry = PermissionRegistry(audit_logger=logger)
    registry.set_hitl_manager(hitl)

    registry.register(ToolPermission(
        name="refund_tool",
        description="A refund-related tool",
        operation_type=OperationType.WRITE,
        risk_level=RiskLevel.CRITICAL,
        allowed_callers=list(CallerRole),
        tenant_scope=TenantScope.GLOBAL,
        refund_related=True,
        requires_second_verification=True,
    ))

    result = registry.check("refund_tool", caller=CallerRole.CS_AGENT)
    assert result.allowed is False
    assert result.escalated_to_hitl is True


# ============================================================================ #
# Audit logging on denial
# ============================================================================ #


def test_permission_denied_audit_record(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    registry = PermissionRegistry(audit_logger=logger)

    registry.register(ToolPermission(
        name="admin_tool",
        description="Admin only",
        operation_type=OperationType.ADMIN,
        risk_level=RiskLevel.CRITICAL,
        allowed_callers=[CallerRole.ADMIN_API],
        tenant_scope=TenantScope.GLOBAL,
    ))

    registry.check("admin_tool", caller=CallerRole.CS_AGENT)

    records = logger.query(event_type="permission_denied")
    assert len(records) == 1
    assert records[0]["action"] == "admin_tool"
    assert records[0]["status"] == "denied"


# ============================================================================ #
# Standard permissions coverage
# ============================================================================ #


def test_all_standard_permissions_registered() -> None:
    registry = PermissionRegistry()
    register_standard_permissions(registry)
    assert len(STANDARD_TOOL_PERMISSIONS) > 10
    for perm in STANDARD_TOOL_PERMISSIONS:
        assert registry.get(perm.name) is not None


def test_standard_permissions_read_tools_low_risk() -> None:
    registry = PermissionRegistry()
    register_standard_permissions(registry)
    read_tools = ["get_product_list", "get_product_detail", "get_order_list", "search_knowledge_base"]
    for tool_name in read_tools:
        perm = registry.get(tool_name)
        assert perm is not None
        assert perm.operation_type == OperationType.READ
        assert perm.risk_level == RiskLevel.LOW


def test_standard_permissions_delete_high_risk() -> None:
    registry = PermissionRegistry()
    register_standard_permissions(registry)
    perm = registry.get("delete_product")
    assert perm is not None
    assert perm.operation_type == OperationType.DELETE
    assert perm.risk_level == RiskLevel.HIGH
    assert perm.requires_hitl is True
    assert CallerRole.HUMAN_OPERATOR in perm.allowed_callers


# ============================================================================ #
# Guard decorator
# ============================================================================ #


def test_guard_decorator_allows_valid_call() -> None:
    registry = PermissionRegistry()

    @registry.guard(
        permission=ToolPermission(
            name="guarded_func",
            description="A guarded function",
            operation_type=OperationType.READ,
            risk_level=RiskLevel.LOW,
            allowed_callers=[CallerRole.SYSTEM],
            tenant_scope=TenantScope.GLOBAL,
        ),
        caller_override=CallerRole.SYSTEM,
    )
    def guarded_func() -> str:
        return "ok"

    result = guarded_func()
    assert result == "ok"


def test_guard_decorator_denies_invalid_caller() -> None:
    registry = PermissionRegistry()

    @registry.guard(
        permission=ToolPermission(
            name="admin_func",
            description="Admin only",
            operation_type=OperationType.ADMIN,
            risk_level=RiskLevel.CRITICAL,
            allowed_callers=[CallerRole.ADMIN_API],
            tenant_scope=TenantScope.GLOBAL,
        ),
        caller_override=CallerRole.CS_AGENT,
    )
    def admin_func() -> str:
        return "should not reach"

    with pytest.raises(PermissionDeniedError) as exc:
        admin_func()
    assert "caller_not_allowed" in str(exc.value)


# ============================================================================ #
# PermissionCheckResult properties
# ============================================================================ #


def test_permission_check_result_properties() -> None:
    result = PermissionCheckResult(
        allowed=True,
        reason="All checks passed",
    )
    assert result.allowed is True
    assert result.violations == []
    assert result.escalated_to_hitl is False

    result = PermissionCheckResult(
        allowed=False,
        reason="Permission denied",
        violations=["caller_not_allowed", "missing_required_param"],
        escalated_to_hitl=True,
        hitl_request_id="req-123",
    )
    assert result.allowed is False
    assert len(result.violations) == 2
    assert result.escalated_to_hitl is True
    assert result.hitl_request_id == "req-123"


def test_permission_denied_error_contains_result() -> None:
    result = PermissionCheckResult(
        allowed=False,
        reason="test",
        violations=["v1"],
    )
    error = PermissionDeniedError(result)
    assert error.result is result
    assert "v1" in str(error)
