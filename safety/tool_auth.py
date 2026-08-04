from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, get_type_hints

from pydantic import ConfigDict, TypeAdapter, ValidationError

from .audit import AuditLogger
from .tool_acl import ToolACL, load_tool_acls

ROLE_IMPLIED_SCOPES: dict[str, frozenset[str]] = {
    "admin": frozenset({"read", "write", "admin"}),
    "operator": frozenset({"read", "write"}),
    "agent": frozenset({"read"}),
    "viewer": frozenset({"read"}),
}

TENANT_KEYS = {
    "tenant_id",
    "target_tenant_id",
    "shop_tenant_id",
    "merchant_tenant_id",
    "owner_tenant_id",
}

PARAMETER_CONSTRAINTS: dict[str, dict[str, dict[str, Any]]] = {
    "get_product_list": {"offset": {"ge": 0}, "page_size": {"ge": 1, "le": 100}},
    "get_order_list": {"offset": {"ge": 0}, "page_size": {"ge": 1, "le": 100}},
    "get_product_detail": {"product_id": {"non_empty": True}},
    "get_product_price": {"product_id": {"non_empty": True}},
    "update_product": {
        "product_id": {"non_empty": True},
        "title": {"max_length": 200},
        "description": {"max_length": 5000},
        "price": {"gt": 0},
        "stock": {"ge": 0},
    },
    "create_product": {
        "title": {"non_empty": True, "max_length": 200},
        "description": {"non_empty": True, "max_length": 5000},
        "price": {"gt": 0},
        "currency": {"non_empty": True, "min_length": 3, "max_length": 3},
        "stock": {"ge": 0},
        "images": {"min_length": 1, "max_length": 20},
        "category": {"non_empty": True},
    },
    "delete_product": {"product_id": {"non_empty": True}},
    "update_order_status": {
        "order_id": {"non_empty": True},
        "status": {"allowed": {"READY_TO_SHIP", "SHIPPED", "DELIVERED", "CANCELLED", "RETURNED"}},
    },
    "approve_return": {"return_id": {"non_empty": True}},
    "fetch_competitor_prices": {
        "product_id": {"non_empty": True},
        "current_price": {"gt": 0},
        "currency": {"non_empty": True, "min_length": 3, "max_length": 3},
        "market": {"non_empty": True, "min_length": 2, "max_length": 8},
        "competitors": {"max_length": 20},
    },
    "generate_price_recommendation": {
        "product_id": {"non_empty": True},
        "current_price": {"gt": 0},
        "currency": {"non_empty": True, "min_length": 3, "max_length": 3},
        "market": {"non_empty": True, "min_length": 2, "max_length": 8},
        "min_margin_rate": {"ge": 0, "lt": 1},
    },
    "search_knowledge_base": {
        "query": {"non_empty": True},
        "top_k": {"ge": 1, "le": 20},
    },
    "answer_customer_question": {
        "question": {"non_empty": True},
        "top_k": {"ge": 1, "le": 20},
    },
    "translate_text": {"text": {"non_empty": True}, "target_language": {"non_empty": True, "max_length": 16}},
    "detect_and_translate": {"text": {"non_empty": True}, "target_language": {"non_empty": True, "max_length": 16}},
    "normalize_image_base64": {
        "image_base64": {"non_empty": True},
        "max_image_bytes": {"gt": 0, "le": 50 * 1024 * 1024},
    },
    "image_file_to_base64": {
        "image_path": {"non_empty": True},
        "max_image_bytes": {"gt": 0, "le": 50 * 1024 * 1024},
    },
    "analyze_customer_image": {
        "question": {"non_empty": True},
        "timeout_seconds": {"gt": 0, "le": 600},
        "max_image_bytes": {"gt": 0, "le": 50 * 1024 * 1024},
    },
    "synthesize_visual_customer_answer": {
        "question": {"non_empty": True},
        "vision_analysis": {"non_empty": True},
        "rag_answer": {"max_length": 20000},
    },
    "answer_visual_customer_question": {
        "question": {"non_empty": True},
        "top_k": {"ge": 1, "le": 20},
    },
}

EXACTLY_ONE_PARAMETER_GROUPS: dict[str, tuple[str, ...]] = {
    "analyze_customer_image": ("image_base64", "image_path"),
    "answer_visual_customer_question": ("image_base64", "image_path"),
}


@dataclass(frozen=True)
class ToolCallerContext:
    user_id: str = "system"
    role: str = "admin"
    tenant_id: str | None = None
    scopes: frozenset[str] | None = None
    is_system: bool = False


_TOOL_CALLER_CONTEXT: ContextVar[ToolCallerContext | None] = ContextVar("tool_caller_context", default=None)


def _normalize_scopes(scopes: Any) -> frozenset[str] | None:
    if scopes is None:
        return None
    if isinstance(scopes, str):
        values = [scope.strip() for scope in scopes.replace(",", " ").split()]
    else:
        values = [str(scope).strip() for scope in scopes]
    return frozenset(scope.lower() for scope in values if scope)


def normalize_caller_context(caller_context: Any | None = None) -> ToolCallerContext:
    if caller_context is None:
        current = _TOOL_CALLER_CONTEXT.get()
        if current is not None:
            return current
        return ToolCallerContext(is_system=True)

    if isinstance(caller_context, ToolCallerContext):
        return caller_context

    if isinstance(caller_context, dict):
        return ToolCallerContext(
            user_id=str(caller_context.get("user_id") or caller_context.get("actor") or "unknown"),
            role=str(caller_context.get("role") or "viewer").lower(),
            tenant_id=str(caller_context["tenant_id"]) if caller_context.get("tenant_id") is not None else None,
            scopes=_normalize_scopes(caller_context.get("scopes") or caller_context.get("permissions")),
            is_system=bool(caller_context.get("is_system", False)),
        )

    return ToolCallerContext(
        user_id=str(getattr(caller_context, "user_id", getattr(caller_context, "actor", "unknown"))),
        role=str(getattr(caller_context, "role", "viewer")).lower(),
        tenant_id=str(caller_context.tenant_id) if getattr(caller_context, "tenant_id", None) is not None else None,
        scopes=_normalize_scopes(getattr(caller_context, "scopes", getattr(caller_context, "permissions", None))),
        is_system=bool(getattr(caller_context, "is_system", False)),
    )


def set_tool_caller_context(caller_context: ToolCallerContext | dict[str, Any]) -> Token[ToolCallerContext | None]:
    return _TOOL_CALLER_CONTEXT.set(normalize_caller_context(caller_context))


def reset_tool_caller_context(token: Token[ToolCallerContext | None]) -> None:
    _TOOL_CALLER_CONTEXT.reset(token)


def get_tool_caller_context() -> ToolCallerContext:
    return normalize_caller_context(None)


@contextmanager
def tool_caller_context(caller_context: ToolCallerContext | dict[str, Any]) -> Iterator[ToolCallerContext]:
    token = set_tool_caller_context(caller_context)
    try:
        yield get_tool_caller_context()
    finally:
        reset_tool_caller_context(token)


class PermissionDeniedError(RuntimeError):
    """Raised when a tool call fails permission gateway validation."""

    def __init__(self, error_type: str, message: str, *, status_code: int = 403, details: Any | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.details = details

    def to_response(self) -> dict[str, Any]:
        content: dict[str, Any] = {
            "error": self.error_type,
            "message": str(self),
        }
        if self.status_code == 422 and self.details is not None:
            content["details"] = self.details
        return content


class ToolPermissionValidator:
    """Validate tool parameters, caller ACLs, and tenant boundaries."""

    def __init__(
        self,
        *,
        acls: dict[str, ToolACL] | None = None,
        audit_logger: AuditLogger | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self.acls = acls or load_tool_acls(config_path)
        self.audit_logger = audit_logger or AuditLogger()
        self._tools: dict[str, Callable[..., Any]] = {}

    def register_tool(self, tool: Callable[..., Any], name: str | None = None) -> None:
        self._tools[name or tool.__name__] = tool

    def validate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        caller_context: Any | None = None,
        target_resource: Any | None = None,
    ) -> None:
        caller = normalize_caller_context(caller_context)
        target = arguments if target_resource is None else target_resource
        self.validate_params(tool_name, arguments, caller)
        self.validate_caller(tool_name, caller)
        self.validate_tenant(tool_name, caller, target)

    def validate_params(self, tool_name: str, arguments: dict[str, Any], caller_context: Any | None = None) -> None:
        errors: list[dict[str, str]] = []
        tool = self._tools.get(tool_name)
        if tool is not None:
            errors.extend(self._signature_errors(tool, arguments))
        errors.extend(self._constraint_errors(tool_name, arguments))
        errors.extend(self._cross_field_errors(tool_name, arguments))

        if errors:
            self._deny(
                tool_name,
                normalize_caller_context(caller_context),
                "parameter_validation_failed",
                status_code=422,
                public_message="Tool parameter validation failed.",
                public_details=errors,
                audit_details={"fields": errors},
            )

    def validate_caller(self, tool_name: str, caller_context: Any | None = None) -> None:
        caller = normalize_caller_context(caller_context)
        acl = self.acls.get(tool_name)
        if acl is None:
            self._deny(
                tool_name,
                caller,
                "permission_denied",
                public_message="Permission denied.",
                audit_details={"reason": "missing_acl", "role": caller.role, "tenant_id": caller.tenant_id},
            )

        if caller.role not in acl.required_roles:
            self._deny(
                tool_name,
                caller,
                "permission_denied",
                public_message="Permission denied.",
                audit_details={"reason": "role_denied", "role": caller.role, "tenant_id": caller.tenant_id},
            )

        scopes = caller.scopes or ROLE_IMPLIED_SCOPES.get(caller.role, frozenset())
        if acl.required_scope not in scopes:
            self._deny(
                tool_name,
                caller,
                "permission_denied",
                public_message="Permission denied.",
                audit_details={"reason": "scope_denied", "role": caller.role, "tenant_id": caller.tenant_id},
            )

    def validate_tenant(self, tool_name: str, caller_context: Any | None, target_resource: Any | None) -> None:
        caller = normalize_caller_context(caller_context)
        acl = self.acls.get(tool_name)
        if acl is None or acl.tenant_isolation != "tenant":
            return

        if not caller.tenant_id and not caller.is_system:
            self._deny(
                tool_name,
                caller,
                "permission_denied",
                public_message="Permission denied.",
                audit_details={"reason": "missing_tenant", "role": caller.role},
            )

        target_tenants = _extract_tenant_ids(target_resource)
        if caller.tenant_id and target_tenants and any(tenant != caller.tenant_id for tenant in target_tenants):
            self._deny(
                tool_name,
                caller,
                "permission_denied",
                public_message="Permission denied.",
                audit_details={"reason": "tenant_denied", "role": caller.role, "tenant_id": caller.tenant_id},
            )

    def _signature_errors(self, tool: Callable[..., Any], arguments: dict[str, Any]) -> list[dict[str, str]]:
        signature = inspect.signature(tool)
        parameters = signature.parameters
        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        errors: list[dict[str, str]] = []

        if not accepts_kwargs:
            for field in arguments:
                if field not in parameters:
                    errors.append({"field": field, "message": "Unexpected field."})

        for name, parameter in parameters.items():
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            if parameter.default is inspect.Parameter.empty and name not in arguments:
                errors.append({"field": name, "message": "Field is required."})

        type_hints = _safe_type_hints(tool)
        for name, value in arguments.items():
            if name not in parameters or value is None:
                continue
            annotation = type_hints.get(name, parameters[name].annotation)
            if annotation is inspect.Parameter.empty or annotation is Any:
                continue
            try:
                TypeAdapter(annotation, config=ConfigDict(strict=True, arbitrary_types_allowed=True)).validate_python(
                    value
                )
            except (ValidationError, TypeError, ValueError):
                errors.append({"field": name, "message": "Invalid type."})

        return errors

    def _constraint_errors(self, tool_name: str, arguments: dict[str, Any]) -> list[dict[str, str]]:
        constraints = PARAMETER_CONSTRAINTS.get(tool_name, {})
        errors: list[dict[str, str]] = []
        for field, rules in constraints.items():
            if field not in arguments or arguments[field] is None:
                continue
            value = arguments[field]
            if rules.get("non_empty") and _is_empty(value):
                errors.append({"field": field, "message": "Field cannot be empty."})
            for operator, message in (
                ("gt", "Value is too small."),
                ("ge", "Value is too small."),
                ("lt", "Value is too large."),
                ("le", "Value is too large."),
            ):
                if operator in rules and not _compare(value, operator, rules[operator]):
                    errors.append({"field": field, "message": message})
            if "min_length" in rules and hasattr(value, "__len__") and len(value) < int(rules["min_length"]):
                errors.append({"field": field, "message": "Value is too short."})
            if "max_length" in rules and hasattr(value, "__len__") and len(value) > int(rules["max_length"]):
                errors.append({"field": field, "message": "Value is too long."})
            if "allowed" in rules and value not in rules["allowed"]:
                errors.append({"field": field, "message": "Value is not allowed."})
        return errors

    def _cross_field_errors(self, tool_name: str, arguments: dict[str, Any]) -> list[dict[str, str]]:
        fields = EXACTLY_ONE_PARAMETER_GROUPS.get(tool_name)
        if not fields:
            return []
        present = [field for field in fields if not _is_empty(arguments.get(field))]
        if len(present) == 1:
            return []
        return [{"field": ",".join(fields), "message": "Provide exactly one field from this group."}]

    def _deny(
        self,
        tool_name: str,
        caller: ToolCallerContext,
        error_type: str,
        *,
        status_code: int = 403,
        public_message: str,
        public_details: Any | None = None,
        audit_details: dict[str, Any] | None = None,
    ) -> NoReturn:
        self.audit_logger.log(
            event_type="permission_denied",
            action=tool_name,
            status=error_type,
            details=audit_details or {},
            actor=caller.user_id,
            risk_level="medium",
        )
        raise PermissionDeniedError(error_type, public_message, status_code=status_code, details=public_details)


def _safe_type_hints(tool: Callable[..., Any]) -> dict[str, Any]:
    try:
        return get_type_hints(tool)
    except Exception:
        return {}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _compare(value: Any, operator: str, expected: Any) -> bool:
    try:
        if operator == "gt":
            return value > expected
        if operator == "ge":
            return value >= expected
        if operator == "lt":
            return value < expected
        if operator == "le":
            return value <= expected
    except TypeError:
        return True
    return True


def _extract_tenant_ids(value: Any) -> set[str]:
    tenants: set[str] = set()
    if value is None:
        return tenants
    if isinstance(value, dict):
        for key, item in value.items():
            if key in TENANT_KEYS and item is not None:
                tenants.add(str(item))
            elif isinstance(item, (dict, list, tuple, set)):
                tenants.update(_extract_tenant_ids(item))
        return tenants
    if isinstance(value, (list, tuple, set)):
        for item in value:
            tenants.update(_extract_tenant_ids(item))
        return tenants
    for key in TENANT_KEYS:
        item = getattr(value, key, None)
        if item is not None:
            tenants.add(str(item))
    return tenants


__all__ = [
    "PermissionDeniedError",
    "ToolCallerContext",
    "ToolPermissionValidator",
    "get_tool_caller_context",
    "reset_tool_caller_context",
    "set_tool_caller_context",
    "tool_caller_context",
]
