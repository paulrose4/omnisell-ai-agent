from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    SUPPORT = "support"
    ANALYST = "analyst"
    APPROVER = "approver"
    VIEWER = "viewer"
    SERVICE_ACCOUNT = "service_account"


class ChannelProvider(str, Enum):
    AMAZON = "amazon"
    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"
    MOCK = "mock"


class ConnectionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"
    REVOKED = "revoked"
    ERROR = "error"


class FulfillmentMode(str, Enum):
    FBA = "fba"
    FBM = "fbm"


class OrderStatus(str, Enum):
    PENDING = "pending"
    UNSHIPPED = "unshipped"
    PARTIALLY_SHIPPED = "partially_shipped"
    SHIPPED = "shipped"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class WorkflowStatus(str, Enum):
    RECEIVED = "received"
    VALIDATED = "validated"
    EVIDENCE_COLLECTED = "evidence_collected"
    ANALYZED = "analyzed"
    OUTPUT_VALIDATED = "output_validated"
    PLANNED = "planned"
    POLICY_EVALUATED = "policy_evaluated"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        normalized = self.currency.upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO 4217 code")
        object.__setattr__(self, "currency", normalized)
        object.__setattr__(self, "amount", Decimal(str(self.amount)))


@dataclass
class Tenant:
    name: str
    slug: str
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class User:
    email: str
    display_name: str
    id: str = field(default_factory=new_id)
    oidc_subject: str | None = None


@dataclass
class Membership:
    tenant_id: str
    user_id: str
    role: Role
    id: str = field(default_factory=new_id)


@dataclass
class ChannelConnection:
    tenant_id: str
    provider: ChannelProvider
    selling_partner_id: str
    region: str
    marketplace_ids: tuple[str, ...]
    secret_ref: str
    id: str = field(default_factory=new_id)
    status: ConnectionStatus = ConnectionStatus.PENDING
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class Product:
    tenant_id: str
    name: str
    brand: str | None = None
    id: str = field(default_factory=new_id)
    version: int = 1


@dataclass
class Variant:
    tenant_id: str
    product_id: str
    sku: str
    attributes: dict[str, Any] = field(default_factory=dict)
    barcode: str | None = None
    cost: Money | None = None
    id: str = field(default_factory=new_id)


@dataclass
class Listing:
    tenant_id: str
    channel_connection_id: str
    variant_id: str
    marketplace_id: str
    seller_sku: str
    id: str = field(default_factory=new_id)
    external_product_id: str | None = None
    title: str | None = None
    price: Money | None = None
    status: str = "draft"
    content_version: int = 1
    platform_extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class InventoryPosition:
    tenant_id: str
    channel_connection_id: str
    seller_sku: str
    marketplace_id: str
    fulfillment_mode: FulfillmentMode
    available: int = 0
    reserved: int = 0
    inbound: int = 0
    unsellable: int = 0
    warehouse_code: str | None = None
    synced_at: datetime = field(default_factory=utc_now)
    id: str = field(default_factory=new_id)
    platform_extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderLine:
    seller_sku: str
    quantity: int
    title: str | None = None
    unit_price: Money | None = None
    external_line_id: str | None = None


@dataclass
class Order:
    tenant_id: str
    channel_connection_id: str
    external_order_id: str
    marketplace_id: str
    status: OrderStatus
    fulfillment_mode: FulfillmentMode
    lines: list[OrderLine] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    purchased_at: datetime | None = None
    last_updated_at: datetime | None = None
    platform_status: str | None = None
    raw_object_key: str | None = None
    version: int = 1
    platform_extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fulfillment:
    tenant_id: str
    order_id: str
    mode: FulfillmentMode
    id: str = field(default_factory=new_id)
    carrier: str | None = None
    tracking_number: str | None = None
    status: str = "pending"


@dataclass
class CustomerCase:
    tenant_id: str
    language: str
    question: str
    id: str = field(default_factory=new_id)
    order_id: str | None = None
    suggested_reply: str | None = None
    confidence: float | None = None
    status: str = "open"


@dataclass
class ApprovalRequest:
    tenant_id: str
    actor_id: str
    action: str
    action_hash: str
    risk_level: str
    before_snapshot: dict[str, Any]
    proposed_changes: dict[str, Any]
    id: str = field(default_factory=new_id)
    status: str = "pending"
    approver_id: str | None = None
    first_approver_id: str | None = None
    first_approved_at: datetime | None = None
    first_approval_reason: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    expires_at: datetime | None = None


@dataclass
class AgentRun:
    tenant_id: str
    actor_id: str
    workflow_type: str
    correlation_id: str
    id: str = field(default_factory=new_id)
    status: WorkflowStatus = WorkflowStatus.RECEIVED
    prompt_version: str = "v1"
    model: str | None = None
    token_usage: int = 0
    estimated_cost: Decimal = Decimal("0")
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolInvocation:
    tenant_id: str
    agent_run_id: str
    tool_name: str
    arguments: dict[str, Any]
    idempotency_key: str
    correlation_id: str
    id: str = field(default_factory=new_id)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    expected_change: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None


@dataclass
class SyncCursor:
    tenant_id: str
    channel_connection_id: str
    resource_type: str
    cursor: str | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class InboxEvent:
    tenant_id: str
    provider: ChannelProvider
    external_event_id: str
    event_type: str
    payload: dict[str, Any]
    id: str = field(default_factory=new_id)
    received_at: datetime = field(default_factory=utc_now)
    processed_at: datetime | None = None


@dataclass
class OutboxEvent:
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any]
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    published_at: datetime | None = None


class DataSource(str, Enum):
    AMAZON_API = "amazon_api"
    ADVERTISING_API = "advertising_api"
    CSV_IMPORT = "csv_import"
    MANUAL = "manual"
    SYSTEM_ESTIMATE = "system_estimate"
    MOCK = "mock"


@dataclass
class SKUCostProfile:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    currency: str
    product_cost: Decimal | None
    domestic_shipping_cost: Decimal | None
    international_freight_cost: Decimal | None
    duty_cost: Decimal | None
    prep_cost: Decimal | None
    packaging_cost: Decimal | None
    estimated_referral_fee_rate: Decimal | None
    estimated_fulfillment_fee: Decimal | None
    estimated_storage_cost: Decimal | None
    estimated_return_loss_rate: Decimal | None
    exchange_rate_buffer_rate: Decimal | None
    target_margin_rate: Decimal | None
    minimum_margin_rate: Decimal | None
    effective_from: datetime
    source: str
    id: str = field(default_factory=new_id)
    effective_to: datetime | None = None
    version: int = 1
    collected_at: datetime = field(default_factory=utc_now)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.currency = self.currency.upper()
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a three-letter ISO 4217 code")
        if self.effective_from.tzinfo is None or (self.effective_to and self.effective_to.tzinfo is None):
            raise ValueError("cost profile timestamps must be timezone-aware")
        for name in (
            "product_cost",
            "domestic_shipping_cost",
            "international_freight_cost",
            "duty_cost",
            "prep_cost",
            "packaging_cost",
            "estimated_fulfillment_fee",
            "estimated_storage_cost",
        ):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(str(value))
                if value < 0:
                    raise ValueError(f"{name} cannot be negative")
                setattr(self, name, value)
        for name in (
            "estimated_referral_fee_rate",
            "estimated_return_loss_rate",
            "exchange_rate_buffer_rate",
            "target_margin_rate",
            "minimum_margin_rate",
        ):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(str(value))
                if value < 0 or value >= 1:
                    raise ValueError(f"{name} must be between 0 and 1")
                setattr(self, name, value)


@dataclass
class SKUProfitSnapshot:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    currency: str
    window_start: datetime
    window_end: datetime
    revenue: Decimal
    units: int
    refunds: Decimal
    promotions: Decimal
    calculated_at: datetime
    calculation_version: str
    missing_inputs: tuple[str, ...]
    confidence: Decimal
    degraded_reason: str | None
    input_manifest: dict[str, Any]
    estimated: bool
    id: str = field(default_factory=new_id)
    referral_fees: Decimal | None = None
    fulfillment_fees: Decimal | None = None
    storage_cost: Decimal | None = None
    advertising_cost: Decimal | None = None
    landed_cost: Decimal | None = None
    return_loss: Decimal | None = None
    other_variable_cost: Decimal | None = None
    contribution_profit: Decimal | None = None
    contribution_margin: Decimal | None = None
    break_even_price: Decimal | None = None
    break_even_acos: Decimal | None = None

    @property
    def gross_revenue(self) -> Decimal:
        return self.revenue


@dataclass
class InventoryPolicy:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    supplier_lead_time_days: int
    production_lead_time_days: int
    shipping_lead_time_days: int
    amazon_receiving_days: int
    safety_stock_days: int
    target_coverage_days: int
    minimum_order_quantity: int
    case_pack_quantity: int
    preferred_shipping_mode: str
    sales_weight_7d: Decimal
    sales_weight_14d: Decimal
    sales_weight_30d: Decimal
    source: str
    effective_from: datetime
    id: str = field(default_factory=new_id)
    effective_to: datetime | None = None
    version: int = 1
    collected_at: datetime = field(default_factory=utc_now)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.effective_from.tzinfo is None or (self.effective_to and self.effective_to.tzinfo is None):
            raise ValueError("inventory policy timestamps must be timezone-aware")
        for name in (
            "supplier_lead_time_days",
            "production_lead_time_days",
            "shipping_lead_time_days",
            "amazon_receiving_days",
            "safety_stock_days",
            "target_coverage_days",
            "minimum_order_quantity",
            "case_pack_quantity",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.target_coverage_days <= 0:
            raise ValueError("target_coverage_days must be positive")
        if self.case_pack_quantity <= 0:
            raise ValueError("case_pack_quantity must be positive")
        for name in ("sales_weight_7d", "sales_weight_14d", "sales_weight_30d"):
            value = Decimal(str(getattr(self, name)))
            if value < 0 or value > 1:
                raise ValueError(f"{name} must be between 0 and 1")
            setattr(self, name, value)
        total_weight = self.sales_weight_7d + self.sales_weight_14d + self.sales_weight_30d
        if total_weight != Decimal("1"):
            raise ValueError("inventory sales weights must sum to 1")


@dataclass
class InventoryHealthSnapshot:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    policy_id: str
    policy_version: int
    available: int
    reserved: int
    inbound: int
    in_transit: int
    unsellable: int
    inventory_position: int
    weighted_daily_sales: Decimal
    days_of_supply: Decimal | None
    expected_stockout_date: datetime | None
    total_lead_time_days: int
    safety_stock_units: Decimal
    reorder_point: Decimal
    recommended_reorder_quantity: int
    excess_units: int
    recommended_shipping_mode: str
    inventory_health: str
    stockout_risk: str
    excess_risk: str
    forecast_confidence: Decimal
    missing_data: tuple[str, ...]
    data_warnings: tuple[str, ...]
    degraded_reason: str | None
    automation_eligible: bool
    inventory_source: str
    inventory_collected_at: datetime
    calculated_at: datetime
    calculation_version: str
    input_manifest: dict[str, Any]
    id: str = field(default_factory=new_id)
    estimated_revenue_at_risk: Decimal | None = None
    estimated_profit_at_risk: Decimal | None = None
    estimated_extra_logistics_cost: Decimal | None = None


@dataclass
class OperationalIssue:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    issue_type: str
    severity: str
    title: str
    summary: str
    status: str
    dedupe_key: str
    confidence: Decimal
    data_freshness_at: datetime
    evidence: tuple[dict[str, Any], ...]
    missing_data: tuple[str, ...]
    detected_at: datetime
    last_detected_at: datetime
    id: str = field(default_factory=new_id)
    currency: str | None = None
    estimated_revenue_impact: Decimal | None = None
    estimated_profit_impact: Decimal | None = None
    estimated_cost_impact: Decimal | None = None
    degraded_reason: str | None = None
    occurrence_count: int = 1
    resolution_note: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    assignee_id: str | None = None
    due_at: datetime | None = None
    acknowledged_at: datetime | None = None
    started_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_code: str | None = None
    execution_result_id: str | None = None
    reopened_count: int = 0
    priority_score: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.severity not in {"critical", "high", "medium", "low"}:
            raise ValueError("unsupported issue severity")
        if self.status not in {
            "open",
            "acknowledged",
            "in_progress",
            "awaiting_approval",
            "approved_for_manual_execution",
            "verifying",
            "verification_failed",
            "resolved",
            "dismissed",
            "reopened",
        }:
            raise ValueError("unsupported issue status")
        self.priority_score = Decimal(str(self.priority_score))
        if (
            self.detected_at.tzinfo is None
            or self.last_detected_at.tzinfo is None
            or self.data_freshness_at.tzinfo is None
        ):
            raise ValueError("issue timestamps must be timezone-aware")
        self.confidence = Decimal(str(self.confidence))
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.currency is not None:
            self.currency = self.currency.upper()
            if len(self.currency) != 3 or not self.currency.isalpha():
                raise ValueError("currency must be a three-letter ISO 4217 code")
        for name in ("estimated_revenue_impact", "estimated_profit_impact", "estimated_cost_impact"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, Decimal(str(value)))


@dataclass
class RecommendedAction:
    tenant_id: str
    issue_id: str
    action_type: str
    title: str
    description: str
    execution_mode: str
    risk_level: str
    status: str
    parameters: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    expected_outcome: str
    confidence: Decimal
    automation_eligible: bool
    dedupe_key: str
    created_at: datetime
    updated_at: datetime
    id: str = field(default_factory=new_id)
    currency: str | None = None
    expected_revenue_impact: Decimal | None = None
    expected_profit_impact: Decimal | None = None
    expected_cost_impact: Decimal | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if self.execution_mode not in {
            "recommendation_only",
            "automatic",
            "single_approval",
            "dual_approval",
            "prohibited",
        }:
            raise ValueError("unsupported execution mode")
        if self.risk_level not in {"critical", "high", "medium", "low"}:
            raise ValueError("unsupported action risk level")
        if self.status not in {"proposed", "accepted", "rejected", "completed", "canceled"}:
            raise ValueError("unsupported action status")
        self.confidence = Decimal(str(self.confidence))
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.currency is not None:
            self.currency = self.currency.upper()
            if len(self.currency) != 3 or not self.currency.isalpha():
                raise ValueError("currency must be a three-letter ISO 4217 code")
        for name in ("expected_revenue_impact", "expected_profit_impact", "expected_cost_impact"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, Decimal(str(value)))


@dataclass
class SKUHealthSummary:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    health_status: str
    highest_severity: str
    open_issue_count: int
    issue_types: tuple[str, ...]
    recommended_action_ids: tuple[str, ...]
    impact_by_currency: dict[str, dict[str, str]]
    has_data_quality_risk: bool
    last_detected_at: datetime


@dataclass
class DailyOperationsBrief:
    tenant_id: str
    brief_date: date
    timezone: str
    generated_at: datetime
    issue_counts: dict[str, int]
    domain_counts: dict[str, int]
    total_open_issues: int
    data_quality_issue_count: int
    impact_by_currency: dict[str, dict[str, str]]
    unallocated_impact_issue_count: int
    priority_issue_ids: tuple[str, ...]
    priority_action_ids: tuple[str, ...]
    sku_health: tuple[SKUHealthSummary, ...]
    source_manifest: dict[str, Any]
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("brief generated_at must be timezone-aware")
        if not self.timezone:
            raise ValueError("brief timezone is required")


@dataclass
class PriceRecommendation:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    currency: str
    profit_snapshot_id: str
    inventory_snapshot_id: str
    cost_profile_id: str
    current_price: Decimal
    recommended_price: Decimal
    candidate_source: str
    break_even_floor: Decimal
    minimum_margin_floor: Decimal
    minimum_margin_rate: Decimal
    projected_unit_profit: Decimal
    projected_margin: Decimal
    change_rate: Decimal
    policy_decision: str
    policy_reasons: tuple[str, ...]
    risk_level: str
    status: str
    confidence: Decimal
    automation_eligible: bool
    action_hash: str
    evidence: tuple[dict[str, Any], ...]
    missing_data: tuple[str, ...]
    calculated_at: datetime
    id: str = field(default_factory=new_id)
    degraded_reason: str | None = None
    approval_request_id: str | None = None
    occurrence_count: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.currency = self.currency.upper()
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a three-letter ISO 4217 code")
        for name in (
            "current_price",
            "recommended_price",
            "break_even_floor",
            "minimum_margin_floor",
            "minimum_margin_rate",
            "projected_unit_profit",
            "projected_margin",
            "change_rate",
            "confidence",
        ):
            setattr(self, name, Decimal(str(getattr(self, name))))
        if self.policy_decision not in {"allow", "require_approval", "block"}:
            raise ValueError("unsupported price policy decision")
        if self.status not in {"ready", "awaiting_approval", "approved", "rejected", "blocked", "executed", "canceled"}:
            raise ValueError("unsupported price recommendation status")
        if self.calculated_at.tzinfo is None:
            raise ValueError("price recommendation timestamp must be timezone-aware")
