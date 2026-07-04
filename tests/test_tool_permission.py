from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.base import safe_tool_execution
from agents.tools.platform_api import get_product_detail, get_product_list, update_product
from agents.tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
from agents.tools.vision_tool import analyze_customer_image
from safety.audit import AuditLogger
from safety.circuit_breaker import CircuitBreaker
from safety.hitl import HITLManager
from safety.tool_acl import load_tool_acls
from safety.tool_auth import PermissionDeniedError, ToolPermissionValidator, tool_caller_context


def _validator(tmp_path: Path, *tools: Any) -> tuple[ToolPermissionValidator, AuditLogger]:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    validator = ToolPermissionValidator(audit_logger=logger)
    for tool in tools:
        validator.register_tool(tool)
    return validator, logger


def _caller(role: str = "agent", tenant_id: str = "tenant-a", scopes: set[str] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"user_id": f"{role}-user", "role": role, "tenant_id": tenant_id}
    if scopes is not None:
        data["scopes"] = scopes
    return data


def _assert_permission_audit(logger: AuditLogger, action: str) -> None:
    records = logger.query(event_type="permission_denied", action=action)
    assert records
    assert records[-1]["event_type"] == "permission_denied"


def test_params_missing_required_field_returns_422_and_audits(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, get_product_detail)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("get_product_detail", {}, _caller("viewer"))

    assert exc.value.status_code == 422
    assert exc.value.error_type == "parameter_validation_failed"
    assert exc.value.details == [{"field": "product_id", "message": "Field is required."}]
    _assert_permission_audit(logger, "get_product_detail")


def test_params_invalid_type_returns_field_error(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, get_product_list)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("get_product_list", {"offset": 0, "page_size": "20"}, _caller("viewer"))

    assert exc.value.status_code == 422
    assert {"field": "page_size", "message": "Invalid type."} in exc.value.details
    _assert_permission_audit(logger, "get_product_list")


def test_params_range_violation_is_rejected(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, generate_price_recommendation)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate(
            "generate_price_recommendation",
            {"product_id": "P1001", "current_price": 0.0, "min_margin_rate": 0.2},
            _caller("agent"),
        )

    assert exc.value.status_code == 422
    assert {"field": "current_price", "message": "Value is too small."} in exc.value.details
    _assert_permission_audit(logger, "generate_price_recommendation")


def test_params_unknown_field_is_rejected(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, get_product_list)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("get_product_list", {"offset": 0, "page_size": 20, "debug": True}, _caller("viewer"))

    assert exc.value.status_code == 422
    assert {"field": "debug", "message": "Unexpected field."} in exc.value.details
    _assert_permission_audit(logger, "get_product_list")


def test_params_exactly_one_image_source_required(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, analyze_customer_image)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("analyze_customer_image", {"question": "what is this?"}, _caller("agent"))

    assert exc.value.status_code == 422
    assert {"field": "image_base64,image_path", "message": "Provide exactly one field from this group."} in exc.value.details
    _assert_permission_audit(logger, "analyze_customer_image")


def test_params_exactly_one_image_source_rejects_both(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, analyze_customer_image)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate(
            "analyze_customer_image",
            {"question": "what is this?", "image_base64": "abc", "image_path": "x.png"},
            _caller("agent"),
        )

    assert exc.value.status_code == 422
    assert {"field": "image_base64,image_path", "message": "Provide exactly one field from this group."} in exc.value.details
    _assert_permission_audit(logger, "analyze_customer_image")


def test_caller_role_denied_returns_403_without_acl_details(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, update_product)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("update_product", {"product_id": "P1001", "price": 12.5}, _caller("viewer"))

    assert exc.value.status_code == 403
    assert exc.value.to_response() == {"error": "permission_denied", "message": "Permission denied."}
    _assert_permission_audit(logger, "update_product")


def test_caller_operator_can_write_allowed_tool(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, update_product)

    validator.validate("update_product", {"product_id": "P1001", "price": 12.5}, _caller("operator"))

    assert not logger.query(event_type="permission_denied")


def test_caller_explicit_scope_can_reduce_access(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, update_product)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("update_product", {"product_id": "P1001", "price": 12.5}, _caller("operator", scopes={"read"}))

    assert exc.value.status_code == 403
    _assert_permission_audit(logger, "update_product")


def test_missing_acl_fails_closed_and_audits(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("unregistered_tool", {"tenant_id": "tenant-a"}, _caller("admin"))

    assert exc.value.status_code == 403
    _assert_permission_audit(logger, "unregistered_tool")


def test_cross_tenant_target_is_denied_without_existence_hint(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, get_product_detail)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate(
            "get_product_detail",
            {"product_id": "P1001"},
            _caller("viewer", tenant_id="tenant-a"),
            target_resource={"tenant_id": "tenant-b"},
        )

    assert exc.value.status_code == 403
    assert "tenant-b" not in json.dumps(exc.value.to_response())
    _assert_permission_audit(logger, "get_product_detail")


def test_same_tenant_target_is_allowed(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, get_product_detail)

    validator.validate(
        "get_product_detail",
        {"product_id": "P1001"},
        _caller("viewer", tenant_id="tenant-a"),
        target_resource={"tenant_id": "tenant-a"},
    )

    assert not logger.query(event_type="permission_denied")


def test_missing_tenant_for_explicit_context_is_denied(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, get_product_detail)

    with pytest.raises(PermissionDeniedError) as exc:
        validator.validate("get_product_detail", {"product_id": "P1001"}, {"user_id": "u1", "role": "viewer"})

    assert exc.value.status_code == 403
    _assert_permission_audit(logger, "get_product_detail")


def test_shared_tool_ignores_target_tenant(tmp_path: Path) -> None:
    validator, logger = _validator(tmp_path, fetch_competitor_prices)

    validator.validate(
        "fetch_competitor_prices",
        {"product_id": "P1001", "current_price": 10.0},
        _caller("agent", tenant_id="tenant-a"),
        target_resource={"tenant_id": "tenant-b"},
    )

    assert not logger.query(event_type="permission_denied")


def test_safe_tool_execution_allows_valid_context(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    validator = ToolPermissionValidator(audit_logger=logger)

    @safe_tool_execution(
        hitl_manager=HITLManager(audit_logger=logger),
        audit_logger=logger,
        circuit_breaker=CircuitBreaker(),
        permission_validator=validator,
    )
    def get_product_list(offset: int = 0, page_size: int = 20) -> dict[str, int]:
        return {"offset": offset, "page_size": page_size}

    with tool_caller_context(_caller("viewer")):
        assert get_product_list(page_size=5) == {"offset": 0, "page_size": 5}

    assert logger.query(action="get_product_list", status="success")


def test_safe_tool_execution_blocks_before_tool_body(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    validator = ToolPermissionValidator(audit_logger=logger)
    called = False

    @safe_tool_execution(
        hitl_manager=HITLManager(audit_logger=logger),
        audit_logger=logger,
        circuit_breaker=CircuitBreaker(),
        permission_validator=validator,
    )
    def update_product(product_id: str, price: float | None = None) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"product_id": product_id, "price": price}

    with tool_caller_context(_caller("viewer")):
        with pytest.raises(PermissionDeniedError):
            update_product(product_id="P1001", price=12.5)

    assert called is False
    _assert_permission_audit(logger, "update_product")
    assert not logger.query(action="update_product", status="success")


def test_acl_env_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OMNISELL_TOOL_ACL_JSON",
        json.dumps({"update_product": {"required_roles": ["viewer"], "required_scope": "read", "tenant_isolation": "tenant"}}),
    )

    acls = load_tool_acls()

    assert acls["update_product"].required_roles == frozenset({"viewer"})
    assert acls["update_product"].required_scope == "read"


def test_acl_file_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNISELL_TOOL_ACL_JSON", raising=False)
    config_path = tmp_path / "tool-acl.json"
    config_path.write_text(
        json.dumps({"custom_tool": {"required_roles": ["admin"], "required_scope": "admin", "tenant_isolation": "none"}}),
        encoding="utf-8",
    )

    acls = load_tool_acls(config_path)

    assert acls["custom_tool"].required_roles == frozenset({"admin"})
    assert acls["custom_tool"].tenant_isolation == "none"
