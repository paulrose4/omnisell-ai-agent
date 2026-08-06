from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from api.release_profiles import ReleaseProfile, normalize_release_profile
from api.route_catalog import build_route_inventory, route_groups_for


def test_release_profile_normalization_is_typed_and_fail_closed() -> None:
    assert normalize_release_profile(ReleaseProfile.GOLDEN_DEMO) is ReleaseProfile.GOLDEN_DEMO
    assert normalize_release_profile(" READ_ONLY_PILOT ") is ReleaseProfile.READ_ONLY_PILOT

    with pytest.warns(DeprecationWarning, match="removed in 2.0.0"):
        assert normalize_release_profile("interview") is ReleaseProfile.GOLDEN_DEMO

    with pytest.raises(ValueError, match="full_development, golden_demo, read_only_pilot"):
        normalize_release_profile("unclassified")


def test_route_inventory_classifies_every_profiled_route() -> None:
    inventory = build_route_inventory()

    assert inventory
    required_fields = {
        "group",
        "method",
        "path",
        "owner",
        "auth_scope",
        "tenant_scope",
        "risk",
        "stability",
        "data_provenance",
        "write_capability",
        "side_effect_class",
        "idempotency",
        "policy_required",
        "approval_required",
        "audit_required",
        "recovery_strategy",
        "external_write_status",
        "control_source",
        "profiles",
    }
    seen: set[tuple[str, str, str]] = set()
    for item in inventory:
        assert required_fields <= item.keys()
        assert item["profiles"]
        for profile in item["profiles"]:
            key = (profile, item["method"], item["path"])
            assert key not in seen
            seen.add(key)


def test_route_controls_are_fail_closed_for_read_and_manual_execution_paths() -> None:
    inventory = build_route_inventory()
    by_key = {(item["method"], item["path"]): item for item in inventory}

    for item in inventory:
        if item["method"] in {"GET", "HEAD", "OPTIONS"}:
            assert item["side_effect_class"] == "none"
            assert item["recovery_strategy"] == "none"
            assert item["external_write_status"] == "none"
        else:
            assert item["audit_required"] is True

    mock_execution = by_key[("POST", "/api/mock-seller-central/execute")]
    assert mock_execution["side_effect_class"] == "manual_external_record"
    assert mock_execution["idempotency"] == "required_header"
    assert mock_execution["policy_required"] is True
    assert mock_execution["approval_required"] is True
    assert mock_execution["recovery_strategy"] == "manual_reconciliation"
    assert mock_execution["external_write_status"] == "mock_only"
    assert mock_execution["control_source"] == "route_override"

    listing_execution = by_key[("POST", "/api/listing-quality/experiments/{experiment_id}/execution")]
    assert listing_execution["side_effect_class"] == "manual_external_record"
    assert listing_execution["idempotency"] == "application_managed"
    assert listing_execution["policy_required"] is True
    assert listing_execution["approval_required"] is True
    assert listing_execution["external_write_status"] == "manual_record_only"


def test_versioned_route_inventory_matches_registered_catalog() -> None:
    evidence_path = Path("docs/evidence/routes/route-inventory-v1.json")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert payload["profiles"] == [profile.value for profile in ReleaseProfile]
    assert payload["route_count"] == len(payload["routes"])
    assert payload["routes"] == build_route_inventory()


def test_versioned_bff_inventory_maps_every_proxy_to_a_registered_backend_route() -> None:
    evidence_path = Path("docs/evidence/routes/bff-route-inventory-v1.json")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    backend_routes = {
        (str(item["method"]), _normalize_route_path(str(item["path"]))) for item in build_route_inventory()
    }

    route_files = tuple(Path("frontend/app/api").rglob("route.ts"))
    assert payload["route_file_count"] == len(route_files)
    assert payload["route_count"] == len(payload["routes"])
    assert {item["file"] for item in payload["routes"]} == {path.as_posix() for path in route_files}

    for item in payload["routes"]:
        if item["kind"] == "nextauth":
            assert item["auth_propagation"] == "nextauth_session"
            assert item["backend_method"] is None
            assert item["backend_path"] is None
            continue
        assert item["kind"] == "backend_proxy"
        assert item["auth_propagation"] == "oidc_bearer"
        assert item["tenant_propagation"] == "backend_token_claim"
        backend_key = (item["backend_method"], _normalize_route_path(item["backend_path"]))
        assert backend_key in backend_routes


def test_frontend_middleware_enforces_release_profiles_for_bff_routes() -> None:
    middleware = Path("frontend/middleware.ts").read_text(encoding="utf-8")
    manifest_path = Path("frontend/generated/bff-profile-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert 'from "@/lib/api/release-profile-routes"' in middleware
    assert "isBffRouteAllowed" in middleware
    assert manifest["schema_version"] == "1.0"
    routes = {(item["method"], item["path"]): set(item["profiles"]) for item in manifest["routes"]}
    assert "golden_demo" in routes[("GET", "/api/approvals")]
    assert "golden_demo" not in routes[("GET", "/api/advertising/diagnostics")]


def test_versioned_openapi_and_frontend_types_cover_the_full_route_catalog() -> None:
    openapi_path = Path("docs/evidence/openapi/full-development-v1.json")
    types_path = Path("frontend/generated/api-schema.d.ts")
    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))
    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))

    openapi_routes = {
        (method.upper(), _normalize_route_path(path))
        for path, operations in openapi["paths"].items()
        for method in operations
        if method.upper() in {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
    }
    catalog_routes = {
        (str(item["method"]), _normalize_route_path(str(item["path"]))) for item in build_route_inventory()
    }
    assert openapi_routes == catalog_routes
    assert "export interface paths" in types_path.read_text(encoding="utf-8")
    assert package["scripts"]["check:api-types"].endswith("--check")


def test_golden_demo_profile_is_an_explicit_core_allowlist() -> None:
    groups = {
        item.name
        for item in route_groups_for(
            profile=ReleaseProfile.GOLDEN_DEMO,
            environment="development",
            mock_drill_enabled=True,
        )
    }

    assert {
        "platform_admin",
        "agent_monitoring",
        "inventory",
        "operations",
        "mock_stockout_drill",
        "manual_execution_evidence",
        "work_items",
        "sync",
    } <= groups
    assert "legacy_customer_demo" not in groups
    assert "customer_cases" not in groups
    assert "listing_quality" not in groups
    assert "reconciliation" not in groups


def test_read_only_pilot_profile_never_registers_mock_or_legacy_routes() -> None:
    groups = {
        item.name
        for item in route_groups_for(
            profile=ReleaseProfile.READ_ONLY_PILOT,
            environment="production",
            mock_drill_enabled=False,
        )
    }

    assert "amazon_connections" in groups
    assert "customer_cases" in groups
    assert "mock_stockout_drill" not in groups
    assert "mock_customer_cases" not in groups
    assert "legacy_customer_demo" not in groups


def test_golden_demo_app_does_not_expose_extension_apis(tmp_path) -> None:
    from api.config import Settings
    from api.main import create_app

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'golden-profile.sqlite'}",
            release_profile="golden_demo",
            mock_drill_enabled=True,
        )
    )
    paths = {route.path for route in app.routes}

    assert "/api/operations/issues" in paths
    assert "/api/admin/approvals" in paths
    assert "/api/mock-seller-central/execute" in paths
    assert "/api/customer-cases" not in paths
    assert "/api/listing-quality/snapshots" not in paths
    assert "/api/reconciliation/runs" not in paths


def _normalize_route_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{param}", path.split("?", maxsplit=1)[0])
