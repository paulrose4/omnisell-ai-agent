from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from api.release_profiles import ReleaseProfile
from api.routes import (
    admin_routes,
    agent_run_routes,
    amazon_routes,
    briefing_routes,
    customer_case_routes,
    customer_routes,
    intelligence_routes,
    inventory_routes,
    learning_routes,
    listing_quality_routes,
    mock_scenario_routes,
    operations_routes,
    price_routes,
    product_routes,
    profitability_routes,
    rag_routes,
    reconciliation_routes,
    sync_routes,
    task_routes,
    work_item_routes,
)

Risk = Literal["low", "medium", "high", "critical"]
Stability = Literal["candidate", "experimental", "legacy"]
DataProvenance = Literal["internal", "amazon", "mixed", "mock"]
WriteCapability = Literal["read_only", "internal_state", "manual_external_record", "blocked_external_write"]
SideEffectClass = Literal[
    "none", "advisory_compute", "internal_state", "manual_external_record", "external_connection_control"
]
IdempotencyControl = Literal["required_header", "optional_header", "application_managed", "not_declared"]
RecoveryStrategy = Literal["none", "idempotent_replay", "durable_retry", "manual_reconciliation", "operator_retry"]
ExternalWriteStatus = Literal["none", "blocked", "mock_only", "manual_record_only"]

ALL_PROFILES = frozenset(ReleaseProfile)
EXTENDED_PROFILES = frozenset({ReleaseProfile.FULL_DEVELOPMENT, ReleaseProfile.READ_ONLY_PILOT})
DEMO_PROFILES = frozenset({ReleaseProfile.FULL_DEVELOPMENT, ReleaseProfile.GOLDEN_DEMO})
FULL_ONLY = frozenset({ReleaseProfile.FULL_DEVELOPMENT})


@dataclass(frozen=True, slots=True)
class RouteGroup:
    name: str
    routers: tuple[APIRouter, ...]
    profiles: frozenset[ReleaseProfile]
    owner: str
    auth_scope: str
    tenant_scope: str
    risk: Risk
    stability: Stability
    data_provenance: DataProvenance
    write_capability: WriteCapability
    allow_in_production: bool = True
    requires_mock_drill: bool = False


@dataclass(frozen=True, slots=True)
class RouteControl:
    side_effect_class: SideEffectClass
    idempotency: IdempotencyControl
    policy_required: bool
    approval_required: bool
    audit_required: bool
    recovery_strategy: RecoveryStrategy
    external_write_status: ExternalWriteStatus
    control_source: Literal["derived", "route_override"] = "derived"


@dataclass(frozen=True, slots=True)
class RouteControlOverride:
    side_effect_class: SideEffectClass | None = None
    idempotency: IdempotencyControl | None = None
    policy_required: bool | None = None
    approval_required: bool | None = None
    audit_required: bool | None = None
    recovery_strategy: RecoveryStrategy | None = None
    external_write_status: ExternalWriteStatus | None = None


ROUTE_CONTROL_OVERRIDES: dict[tuple[str, str], RouteControlOverride] = {
    ("POST", "/api/admin/approvals/{request_id}/approve"): RouteControlOverride(policy_required=True),
    ("POST", "/api/admin/approvals/{request_id}/reject"): RouteControlOverride(policy_required=True),
    ("POST", "/api/customer-cases/{case_id}/send"): RouteControlOverride(
        side_effect_class="manual_external_record",
        policy_required=True,
        approval_required=True,
        recovery_strategy="operator_retry",
        external_write_status="mock_only",
    ),
    ("POST", "/api/learning/execution-authorizations"): RouteControlOverride(
        policy_required=True,
        approval_required=True,
    ),
    ("POST", "/api/learning/execution-authorizations/{authorization_id}/approve"): RouteControlOverride(
        policy_required=True,
    ),
    ("POST", "/api/learning/executions"): RouteControlOverride(
        policy_required=True,
        approval_required=True,
    ),
    ("POST", "/api/listing-quality/experiments"): RouteControlOverride(
        policy_required=True,
        approval_required=True,
    ),
    ("POST", "/api/listing-quality/experiments/{experiment_id}/execution"): RouteControlOverride(
        side_effect_class="manual_external_record",
        idempotency="application_managed",
        policy_required=True,
        approval_required=True,
        recovery_strategy="manual_reconciliation",
        external_write_status="manual_record_only",
    ),
    ("POST", "/api/mock-seller-central/execute"): RouteControlOverride(
        side_effect_class="manual_external_record",
        policy_required=True,
        approval_required=True,
        recovery_strategy="manual_reconciliation",
        external_write_status="mock_only",
    ),
    ("POST", "/api/operations/issues/{issue_id}/approval-requests"): RouteControlOverride(
        policy_required=True,
        approval_required=True,
    ),
}


ROUTE_CATALOG: tuple[RouteGroup, ...] = (
    RouteGroup(
        "platform_admin",
        (admin_routes.router, admin_routes.protected_router),
        ALL_PROFILES,
        "platform",
        "admin_or_authenticated_operator",
        "tenant_required",
        "critical",
        "candidate",
        "internal",
        "internal_state",
    ),
    RouteGroup(
        "agent_monitoring",
        (agent_run_routes.router,),
        ALL_PROFILES,
        "agent_runtime",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "internal",
        "internal_state",
    ),
    RouteGroup(
        "amazon_callback",
        (amazon_routes.callback_router,),
        ALL_PROFILES,
        "commerce_platform",
        "signed_oauth_state",
        "tenant_from_oauth_state",
        "high",
        "candidate",
        "amazon",
        "internal_state",
    ),
    RouteGroup(
        "amazon_connections",
        (amazon_routes.router,),
        ALL_PROFILES,
        "commerce_platform",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "amazon",
        "blocked_external_write",
    ),
    RouteGroup(
        "products",
        (product_routes.router,),
        ALL_PROFILES,
        "commerce_platform",
        "authenticated_operator",
        "tenant_required",
        "medium",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "legacy_customer_demo",
        (customer_routes.router, rag_routes.router),
        FULL_ONLY,
        "legacy_demo",
        "authenticated_operator",
        "tenant_required",
        "medium",
        "legacy",
        "internal",
        "read_only",
        allow_in_production=False,
    ),
    RouteGroup(
        "customer_cases",
        (customer_case_routes.router,),
        EXTENDED_PROFILES,
        "customer_operations",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "mock_customer_cases",
        (customer_case_routes.mock_router,),
        DEMO_PROFILES,
        "customer_operations",
        "authenticated_operator",
        "tenant_required",
        "medium",
        "experimental",
        "mock",
        "internal_state",
        allow_in_production=False,
        requires_mock_drill=True,
    ),
    RouteGroup(
        "pricing",
        (price_routes.router,),
        ALL_PROFILES,
        "deterministic_domain",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "profitability",
        (profitability_routes.router,),
        ALL_PROFILES,
        "deterministic_domain",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "inventory",
        (inventory_routes.router,),
        ALL_PROFILES,
        "deterministic_domain",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "operations_learning",
        (learning_routes.router,),
        EXTENDED_PROFILES,
        "operations_learning",
        "authenticated_operator",
        "tenant_required",
        "critical",
        "experimental",
        "internal",
        "internal_state",
    ),
    RouteGroup(
        "listing_quality",
        (listing_quality_routes.router,),
        EXTENDED_PROFILES,
        "listing_operations",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "operations",
        (operations_routes.router,),
        ALL_PROFILES,
        "operations_control",
        "authenticated_operator",
        "tenant_required",
        "critical",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "mock_stockout_drill",
        (mock_scenario_routes.router, mock_scenario_routes.seller_central_router),
        DEMO_PROFILES,
        "demo_uat",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mock",
        "manual_external_record",
        allow_in_production=False,
        requires_mock_drill=True,
    ),
    RouteGroup(
        "manual_execution_evidence",
        (mock_scenario_routes.manual_execution_router,),
        ALL_PROFILES,
        "operations_control",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "internal",
        "read_only",
    ),
    RouteGroup(
        "operations_briefing",
        (briefing_routes.router,),
        ALL_PROFILES,
        "operations_control",
        "authenticated_operator",
        "tenant_required",
        "medium",
        "candidate",
        "internal",
        "internal_state",
    ),
    RouteGroup(
        "tasks",
        (task_routes.router,),
        ALL_PROFILES,
        "platform",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "internal",
        "internal_state",
    ),
    RouteGroup(
        "work_items",
        (work_item_routes.router,),
        ALL_PROFILES,
        "operations_control",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "internal",
        "internal_state",
    ),
    RouteGroup(
        "operational_intelligence",
        (intelligence_routes.router,),
        EXTENDED_PROFILES,
        "operations_intelligence",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "reconciliation",
        (reconciliation_routes.router,),
        EXTENDED_PROFILES,
        "finance_operations",
        "authenticated_operator",
        "tenant_required",
        "critical",
        "candidate",
        "mixed",
        "internal_state",
    ),
    RouteGroup(
        "sync",
        (sync_routes.router, sync_routes.data_quality_router),
        ALL_PROFILES,
        "commerce_platform",
        "authenticated_operator",
        "tenant_required",
        "high",
        "candidate",
        "mixed",
        "internal_state",
    ),
)


def route_groups_for(*, profile: ReleaseProfile, environment: str, mock_drill_enabled: bool) -> tuple[RouteGroup, ...]:
    production = environment.lower() == "production"
    return tuple(
        group
        for group in ROUTE_CATALOG
        if profile in group.profiles
        and (group.allow_in_production or not production)
        and (not group.requires_mock_drill or (mock_drill_enabled and not production))
    )


def register_profile_routes(
    app: FastAPI, *, profile: ReleaseProfile, environment: str, mock_drill_enabled: bool
) -> None:
    for group in route_groups_for(
        profile=profile,
        environment=environment,
        mock_drill_enabled=mock_drill_enabled,
    ):
        for router in group.routers:
            app.include_router(router)


def _idempotency_control(route: APIRoute) -> IdempotencyControl:
    for header in route.dependant.header_params:
        if str(header.alias).lower() != "idempotency-key":
            continue
        return "required_header" if header.field_info.is_required() else "optional_header"
    return "not_declared"


def _derived_route_control(*, group: RouteGroup, route: APIRoute, method: str) -> RouteControl:
    if method in {"GET", "HEAD", "OPTIONS"}:
        return RouteControl(
            side_effect_class="none",
            idempotency="not_declared",
            policy_required=False,
            approval_required=False,
            audit_required=False,
            recovery_strategy="none",
            external_write_status="none",
        )

    side_effect_by_capability: dict[WriteCapability, SideEffectClass] = {
        "read_only": "advisory_compute",
        "internal_state": "internal_state",
        "manual_external_record": "manual_external_record",
        "blocked_external_write": "external_connection_control",
    }
    side_effect = side_effect_by_capability[group.write_capability]
    idempotency = _idempotency_control(route)
    if side_effect == "manual_external_record":
        recovery: RecoveryStrategy = "manual_reconciliation"
    elif idempotency == "required_header":
        recovery = "idempotent_replay"
    elif group.name in {"agent_monitoring", "sync", "tasks"}:
        recovery = "durable_retry"
    else:
        recovery = "operator_retry"

    if group.write_capability == "blocked_external_write":
        external_write_status: ExternalWriteStatus = "blocked"
    elif group.write_capability == "manual_external_record":
        external_write_status = "mock_only" if group.data_provenance == "mock" else "manual_record_only"
    else:
        external_write_status = "none"
    return RouteControl(
        side_effect_class=side_effect,
        idempotency=idempotency,
        policy_required=False,
        approval_required=False,
        audit_required=True,
        recovery_strategy=recovery,
        external_write_status=external_write_status,
    )


def _route_control(*, group: RouteGroup, route: APIRoute, method: str) -> RouteControl:
    control = _derived_route_control(group=group, route=route, method=method)
    override = ROUTE_CONTROL_OVERRIDES.get((method, route.path))
    if override is None:
        return control
    changes = {field: value for field, value in asdict(override).items() if value is not None}
    return replace(control, **changes, control_source="route_override")


def build_route_inventory() -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for group in ROUTE_CATALOG:
        profiles = sorted(profile.value for profile in group.profiles)
        environments = (
            ["development", "test"] if not group.allow_in_production else ["development", "test", "production"]
        )
        for router in group.routers:
            for route in router.routes:
                if not isinstance(route, APIRoute):
                    continue
                for method in sorted(route.methods):
                    control = _route_control(group=group, route=route, method=method)
                    inventory.append(
                        {
                            "group": group.name,
                            "method": method,
                            "path": route.path,
                            "owner": group.owner,
                            "auth_scope": group.auth_scope,
                            "tenant_scope": group.tenant_scope,
                            "risk": group.risk,
                            "stability": group.stability,
                            "data_provenance": group.data_provenance,
                            "write_capability": group.write_capability,
                            "profiles": profiles,
                            "environments": environments,
                            "requires_mock_drill": group.requires_mock_drill,
                            **asdict(control),
                        }
                    )
    return sorted(inventory, key=lambda item: (str(item["path"]), str(item["method"]), str(item["group"])))
