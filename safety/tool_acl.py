from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal


ToolRole = Literal["admin", "operator", "agent", "viewer"]
ToolScope = Literal["read", "write", "admin"]
TenantIsolation = Literal["none", "shared", "tenant"]

VALID_ROLES: frozenset[str] = frozenset({"admin", "operator", "agent", "viewer"})
VALID_SCOPES: frozenset[str] = frozenset({"read", "write", "admin"})
VALID_TENANT_ISOLATION: frozenset[str] = frozenset({"none", "shared", "tenant"})

OMNISELL_TOOL_ACL_JSON_ENV = "OMNISELL_TOOL_ACL_JSON"
OMNISELL_TOOL_ACL_PATH_ENV = "OMNISELL_TOOL_ACL_PATH"


@dataclass(frozen=True)
class ToolACL:
    """Access control settings for one tool."""

    name: str
    required_roles: frozenset[ToolRole]
    required_scope: ToolScope
    tenant_isolation: TenantIsolation

    @classmethod
    def from_mapping(cls, name: str, data: dict[str, Any]) -> "ToolACL":
        roles = data.get("required_roles", data.get("roles", ()))
        if isinstance(roles, str):
            roles = [roles]
        role_set = frozenset(str(role).strip().lower() for role in roles if str(role).strip())
        if not role_set:
            raise ValueError(f"Tool ACL for {name} must include required_roles.")
        unknown_roles = role_set - VALID_ROLES
        if unknown_roles:
            raise ValueError(f"Tool ACL for {name} has invalid roles: {sorted(unknown_roles)}.")

        scope = str(data.get("required_scope", data.get("scope", "read"))).strip().lower()
        if scope not in VALID_SCOPES:
            raise ValueError(f"Tool ACL for {name} has invalid scope: {scope}.")

        tenant_isolation = str(data.get("tenant_isolation", data.get("tenant", "none"))).strip().lower()
        if tenant_isolation not in VALID_TENANT_ISOLATION:
            raise ValueError(f"Tool ACL for {name} has invalid tenant isolation: {tenant_isolation}.")

        return cls(
            name=name,
            required_roles=frozenset(role_set),  # type: ignore[arg-type]
            required_scope=scope,  # type: ignore[arg-type]
            tenant_isolation=tenant_isolation,  # type: ignore[arg-type]
        )

    def apply_override(self, data: dict[str, Any]) -> "ToolACL":
        merged = {
            "required_roles": self.required_roles,
            "required_scope": self.required_scope,
            "tenant_isolation": self.tenant_isolation,
        }
        merged.update(data)
        return self.from_mapping(self.name, merged)


READ_ALL: frozenset[ToolRole] = frozenset({"admin", "operator", "agent", "viewer"})
AGENT_READ: frozenset[ToolRole] = frozenset({"admin", "operator", "agent"})
OPERATOR_WRITE: frozenset[ToolRole] = frozenset({"admin", "operator"})
ADMIN_ONLY: frozenset[ToolRole] = frozenset({"admin"})


DEFAULT_TOOL_ACLS: dict[str, ToolACL] = {
    "get_platform_base_url": ToolACL("get_platform_base_url", ADMIN_ONLY, "admin", "none"),
    "get_oauth_credentials": ToolACL("get_oauth_credentials", ADMIN_ONLY, "admin", "none"),
    "get_access_token": ToolACL("get_access_token", OPERATOR_WRITE, "write", "tenant"),
    "get_product_list": ToolACL("get_product_list", READ_ALL, "read", "tenant"),
    "get_product_detail": ToolACL("get_product_detail", READ_ALL, "read", "tenant"),
    "get_product_price": ToolACL("get_product_price", READ_ALL, "read", "tenant"),
    "update_product": ToolACL("update_product", OPERATOR_WRITE, "write", "tenant"),
    "create_product": ToolACL("create_product", OPERATOR_WRITE, "write", "tenant"),
    "delete_product": ToolACL("delete_product", OPERATOR_WRITE, "write", "tenant"),
    "get_order_list": ToolACL("get_order_list", AGENT_READ, "read", "tenant"),
    "update_order_status": ToolACL("update_order_status", OPERATOR_WRITE, "write", "tenant"),
    "approve_return": ToolACL("approve_return", OPERATOR_WRITE, "write", "tenant"),
    "fetch_competitor_prices": ToolACL("fetch_competitor_prices", AGENT_READ, "read", "shared"),
    "generate_price_recommendation": ToolACL("generate_price_recommendation", AGENT_READ, "read", "shared"),
    "get_retriever": ToolACL("get_retriever", ADMIN_ONLY, "admin", "none"),
    "search_knowledge_base": ToolACL("search_knowledge_base", READ_ALL, "read", "shared"),
    "answer_customer_question": ToolACL("answer_customer_question", AGENT_READ, "read", "shared"),
    "get_translator": ToolACL("get_translator", ADMIN_ONLY, "admin", "none"),
    "translate_text": ToolACL("translate_text", READ_ALL, "read", "shared"),
    "detect_and_translate": ToolACL("detect_and_translate", READ_ALL, "read", "shared"),
    "normalize_image_base64": ToolACL("normalize_image_base64", AGENT_READ, "read", "shared"),
    "image_file_to_base64": ToolACL("image_file_to_base64", ADMIN_ONLY, "admin", "none"),
    "analyze_customer_image": ToolACL("analyze_customer_image", AGENT_READ, "read", "tenant"),
    "synthesize_visual_customer_answer": ToolACL("synthesize_visual_customer_answer", AGENT_READ, "read", "shared"),
    "answer_visual_customer_question": ToolACL("answer_visual_customer_question", AGENT_READ, "read", "tenant"),
}


def _load_json_mapping(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Tool ACL configuration must be a JSON object.")
    return parsed


def _load_acl_overrides(config_path: str | Path | None = None) -> dict[str, Any]:
    raw_json = os.getenv(OMNISELL_TOOL_ACL_JSON_ENV)
    if raw_json:
        return _load_json_mapping(raw_json)

    path_value = config_path or os.getenv(OMNISELL_TOOL_ACL_PATH_ENV)
    if not path_value:
        return {}

    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Tool ACL configuration file not found: {path}")
    return _load_json_mapping(path.read_text(encoding="utf-8"))


def load_tool_acls(config_path: str | Path | None = None) -> dict[str, ToolACL]:
    """Return default ACLs with optional JSON/env overrides applied."""
    resolved = {name: replace(acl) for name, acl in DEFAULT_TOOL_ACLS.items()}
    overrides = _load_acl_overrides(config_path)

    for name, data in overrides.items():
        if not isinstance(data, dict):
            raise ValueError(f"Tool ACL override for {name} must be an object.")
        if name in resolved:
            resolved[name] = resolved[name].apply_override(data)
        else:
            resolved[name] = ToolACL.from_mapping(name, data)
    return resolved
