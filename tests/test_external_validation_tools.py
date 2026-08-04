from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ValidationError

from commerce.adapters.amazon.adapter import AmazonSPAPIAdapter, AmazonSPAPIConfig
from commerce.adapters.amazon.secrets import InMemorySecretStore
from agents.validators.evidence import EvidenceValidationError
from api.config import Settings
from model_gateway.errors import ModelBudgetExceeded, ModelSchemaError
from safety.amazon_write_gate import AmazonWriteGate
from scripts.run_real_model_staging_smoke import SMOKE_CASES, _bundle, _case_error, _context, _safe_error, _selected_cases


class _DiagnosticOutput(BaseModel):
    required_value: str


def _adapter() -> AmazonSPAPIAdapter:
    store = InMemorySecretStore()
    refresh_ref = store.put(tenant_id="tenant-a", value="refresh-token")
    return AmazonSPAPIAdapter(
        config=AmazonSPAPIConfig(
            lwa_client_id="client",
            lwa_client_secret="secret",
            application_id="app",
            redirect_uri="https://example.test/callback",
        ),
        tenant_id="tenant-a",
        selling_partner_id="seller-a",
        marketplace_ids=["ATVPDKIKX0DER"],
        refresh_token_ref=refresh_ref,
        secret_store=store,
        write_gate=AmazonWriteGate(global_enabled=False, tenant_enabled=False, pilot_read_only=True),
    )


def test_real_amazon_adapter_adds_truthful_provenance_and_observed_times() -> None:
    adapter = _adapter()
    order = adapter._map_order({
        "AmazonOrderId": "ORDER-1",
        "MarketplaceId": "ATVPDKIKX0DER",
        "OrderStatus": "Shipped",
        "PurchaseDate": "2026-07-18T01:00:00Z",
        "LastUpdateDate": "2026-07-18T02:00:00Z",
    })
    inventory = adapter._map_inventory({
        "sellerSku": "SKU-1",
        "lastUpdatedTime": "2026-07-18T03:00:00Z",
        "inventoryDetails": {"fulfillableQuantity": 4},
    }, "ATVPDKIKX0DER")
    listing = adapter._map_listing({
        "summaries": [{"asin": "B000000001", "itemName": "Item", "status": "BUYABLE", "lastUpdatedDate": "2026-07-18T04:00:00Z"}],
    }, seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER")

    for item in (order, inventory, listing):
        assert item.platform_extensions["data_source"] == "amazon_api"
        assert item.platform_extensions["verified_amazon_data"] is True
        assert item.platform_extensions["resource_id"]
        assert item.platform_extensions["collected_at"]
    assert order.platform_extensions["observed_at"] == "2026-07-18T02:00:00+00:00"
    assert inventory.platform_extensions["observed_at"] == "2026-07-18T03:00:00+00:00"
    assert listing.platform_extensions["observed_at"] == "2026-07-18T04:00:00+00:00"


def test_missing_listing_observed_time_remains_unknown() -> None:
    listing = _adapter()._map_listing({"summaries": [{"asin": "B000000001", "itemName": "Item"}]}, seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER")
    assert listing.platform_extensions["observed_at"] is None


def test_listing_status_array_is_normalized_to_single_status() -> None:
    listing = _adapter()._map_listing({
        "summaries": [{
            "asin": "B000000001",
            "itemName": "Item",
            "status": ["BUYABLE", "DISCOVERABLE"],
            "lastUpdatedDate": "2026-07-18T04:00:00Z",
        }],
    }, seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER")

    assert listing.status == "buyable"


def test_mock_metadata_cannot_become_verified_amazon_data() -> None:
    inventory = _adapter()._map_inventory({
        "sellerSku": "MOCK-SKU",
        "inventoryDetails": {"fulfillableQuantity": 1},
        "OmniSellMockMetadata": {"data_source": "mock_amazon", "verified_amazon_data": False},
    }, "ATVPDKIKX0DER")
    assert inventory.platform_extensions["data_source"] == "mock_amazon"
    assert inventory.platform_extensions["verified_amazon_data"] is False


def test_external_smoke_tools_are_explicitly_opt_in_and_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    model_script = (root / "scripts" / "run_real_model_staging_smoke.py").read_text(encoding="utf-8")
    amazon_script = (root / "scripts" / "run_amazon_readonly_smoke.py").read_text(encoding="utf-8")
    runbook = (root / "docs" / "EXTERNAL_STAGING_VERIFICATION_RUNBOOK.md").read_text(encoding="utf-8")
    assert 'REAL_MODEL_SMOKE' in model_script and 'ENVIRONMENT", "development"' in model_script
    assert 'AMAZON_READONLY_SMOKE' in amazon_script and 'AMAZON_WRITES_ENABLED' in amazon_script
    assert "response_format.type=json_schema" in runbook
    assert "operator comparison sheet" in runbook


def test_real_model_smoke_can_select_one_paid_case() -> None:
    selected = _selected_cases(["normal:customer_case_advisor"])

    assert selected == (SMOKE_CASES["normal:customer_case_advisor"],)


def test_real_model_smoke_reports_sanitized_schema_locations() -> None:
    try:
        try:
            _DiagnosticOutput.model_validate({})
        except ValidationError as cause:
            raise ModelSchemaError("schema failed") from cause
    except ModelSchemaError as exc:
        diagnostic = _safe_error(exc)

    assert diagnostic["validation_errors"] == [
        {"location": ["required_value"], "type": "missing", "message": "Field required"}
    ]
    assert "input" not in str(diagnostic)


def test_real_model_smoke_reports_evidence_rule_without_payload() -> None:
    diagnostic = _safe_error(EvidenceValidationError("confidence >= 0.90 requires fresh referenced evidence"))

    assert diagnostic == {
        "type": "EvidenceValidationError",
        "message": "confidence >= 0.90 requires fresh referenced evidence",
    }


def test_real_model_smoke_reports_budget_failure_without_prompt_content() -> None:
    diagnostic = _safe_error(ModelBudgetExceeded("Model token budget exceeded during schema repair: 14500 > 14000"))

    assert diagnostic == {
        "type": "ModelBudgetExceeded",
        "message": "Model token budget exceeded during schema repair: 14500 > 14000",
    }


def test_real_model_smoke_uses_reviewed_budget_for_each_advisor(monkeypatch) -> None:
    for name in (
        "AGENT_BUDGET_PROFILES",
        "REAL_MODEL_MAX_PROMPT_TOKENS",
        "REAL_MODEL_MAX_COMPLETION_TOKENS",
        "REAL_MODEL_MAX_COST",
        "REAL_MODEL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    profiles = Settings().agent_budget_profiles

    operations = _context(
        agent_type="operations_brief_analyst",
        scenario="normal",
        budget_profiles=profiles,
    )
    listing = _context(
        agent_type="listing_optimization_advisor",
        scenario="normal",
        budget_profiles=profiles,
    )

    assert (operations.max_prompt_tokens, operations.max_completion_tokens) == (9000, 5000)
    assert operations.max_estimated_cost == Decimal("0.12")
    assert operations.timeout_seconds == 240
    assert (listing.max_prompt_tokens, listing.max_completion_tokens) == (6000, 4000)
    assert listing.max_estimated_cost == Decimal("0.08")
    assert listing.timeout_seconds == 120


def test_real_model_smoke_labels_evidence_rejection_as_safe() -> None:
    result = _case_error(EvidenceValidationError("unknown evidence"))

    assert result["status"] == "safely_rejected"
    assert result["type"] == "EvidenceValidationError"


def test_real_model_adversarial_cases_use_distinct_sanitized_requests() -> None:
    injection = _bundle(scenario="prompt_injection").by_id()["case:staging-001"].metadata["buyer_message"]
    forbidden = _bundle(scenario="forbidden_actions").by_id()["case:staging-001"].metadata["buyer_message"]
    nonexistent = _bundle(scenario="nonexistent_evidence").by_id()["case:staging-001"].metadata["buyer_message"]

    assert len({injection, forbidden, nonexistent}) == 3
    assert "ignore" in injection.lower()
    assert "refund" in forbidden.lower()
    assert "does-not-exist" in nonexistent


def test_real_model_smoke_includes_sanitized_order_resource() -> None:
    bundle = _bundle()

    order = bundle.by_id()["order:staging-001"]
    assert order.resource_type == "order"
    assert order.resource_id == "ORDER-SANITIZED-001"
    assert order.verified_amazon_data is False
