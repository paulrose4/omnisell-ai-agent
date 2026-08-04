from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from domain.models import InventoryHealthSnapshot, InventoryPolicy, utc_now


CALCULATION_VERSION = "inventory-health-v1"
RATE_QUANTUM = Decimal("0.0001")
QUANTITY_QUANTUM = Decimal("0.01")
INVENTORY_FRESHNESS_HOURS = 6


class InventoryCalculationError(ValueError):
    pass


@dataclass(frozen=True)
class InventoryHealthInput:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    available: int
    reserved: int
    inbound: int
    in_transit: int
    unsellable: int
    sales_7_days: int | None
    sales_14_days: int | None
    sales_30_days: int | None
    inventory_source: str
    inventory_collected_at: datetime
    available_is_net_of_reserved: bool = False
    calculated_at: datetime = field(default_factory=utc_now)
    seasonality_factor: Decimal = Decimal("1")
    promotion_factor: Decimal = Decimal("1")
    unit_revenue: Decimal | None = None
    unit_contribution_profit: Decimal | None = None
    estimated_extra_logistics_cost_per_unit: Decimal | None = None
    promotion_anomaly: bool = False
    sales_spike: bool = False
    stockout_distorted_sales: bool = False
    recent_price_change: bool = False
    recent_advertising_budget_change: bool = False

    def __post_init__(self) -> None:
        if self.inventory_collected_at.tzinfo is None or self.calculated_at.tzinfo is None:
            raise InventoryCalculationError("inventory timestamps must be timezone-aware")
        if self.inventory_collected_at > self.calculated_at:
            raise InventoryCalculationError("inventory collection time cannot be in the future")
        for name in ("available", "reserved", "inbound", "in_transit", "unsellable"):
            if getattr(self, name) < 0:
                raise InventoryCalculationError(f"{name} cannot be negative")
        for name in ("sales_7_days", "sales_14_days", "sales_30_days"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise InventoryCalculationError(f"{name} cannot be negative")
        for name in ("seasonality_factor", "promotion_factor"):
            value = Decimal(str(getattr(self, name)))
            if value <= 0 or value > 5:
                raise InventoryCalculationError(f"{name} must be greater than 0 and no more than 5")
            object.__setattr__(self, name, value)
        for name in ("unit_revenue", "unit_contribution_profit", "estimated_extra_logistics_cost_per_unit"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Decimal(str(value)))


class InventoryHealthCalculator:
    def calculate(self, *, policy: InventoryPolicy, input_data: InventoryHealthInput) -> InventoryHealthSnapshot:
        self._validate_scope(policy, input_data)
        missing_data: list[str] = []
        data_warnings: list[str] = []
        sales_windows = (
            (input_data.sales_7_days, Decimal("7"), policy.sales_weight_7d, "sales_7_days"),
            (input_data.sales_14_days, Decimal("14"), policy.sales_weight_14d, "sales_14_days"),
            (input_data.sales_30_days, Decimal("30"), policy.sales_weight_30d, "sales_30_days"),
        )
        weighted_sales = Decimal("0")
        available_weight = Decimal("0")
        for units, days, weight, name in sales_windows:
            if units is None:
                missing_data.append(name)
                continue
            weighted_sales += (Decimal(units) / days) * weight
            available_weight += weight
        if available_weight > 0 and available_weight != Decimal("1"):
            weighted_sales = weighted_sales / available_weight
        weighted_sales *= input_data.seasonality_factor * input_data.promotion_factor
        weighted_sales = weighted_sales.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)

        inventory_age = input_data.calculated_at - input_data.inventory_collected_at
        if inventory_age > timedelta(hours=INVENTORY_FRESHNESS_HOURS):
            missing_data.append("stale_inventory")
        signal_flags = {
            "promotion_anomaly": input_data.promotion_anomaly,
            "sales_spike": input_data.sales_spike,
            "stockout_distorted_sales": input_data.stockout_distorted_sales,
            "recent_price_change": input_data.recent_price_change,
            "recent_advertising_budget_change": input_data.recent_advertising_budget_change,
        }
        data_warnings.extend(name for name, active in signal_flags.items() if active)

        reserved_deduction = 0 if input_data.available_is_net_of_reserved else input_data.reserved
        inventory_position = max(0, input_data.available + input_data.inbound + input_data.in_transit - reserved_deduction - input_data.unsellable)
        total_lead_time = (
            policy.supplier_lead_time_days + policy.production_lead_time_days
            + policy.shipping_lead_time_days + policy.amazon_receiving_days
        )
        safety_stock = (weighted_sales * policy.safety_stock_days).quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)
        reorder_point = (weighted_sales * (total_lead_time + policy.safety_stock_days)).quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)

        days_of_supply = None
        stockout_date = None
        recommended_quantity = 0
        excess_units = 0
        if weighted_sales <= 0:
            missing_data.append("zero_sales_velocity")
            inventory_health = "dormant_inventory" if inventory_position > 0 else "no_inventory_no_velocity"
            stockout_risk = "unknown"
            excess_risk = "potential" if inventory_position > 0 else "none"
        else:
            days_of_supply = (Decimal(inventory_position) / weighted_sales).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            stockout_date = input_data.calculated_at + timedelta(days=float(days_of_supply))
            target_demand = weighted_sales * policy.target_coverage_days
            raw_reorder = max(Decimal("0"), target_demand - Decimal(inventory_position))
            recommended_quantity = self._round_order(raw_reorder, policy.minimum_order_quantity, policy.case_pack_quantity)
            excess_units = max(0, int((Decimal(inventory_position) - target_demand).to_integral_value(rounding=ROUND_CEILING)))
            if inventory_position == 0:
                inventory_health = "out_of_stock"
                stockout_risk = "critical"
            elif days_of_supply <= Decimal(total_lead_time):
                inventory_health = "critical_stockout_risk"
                stockout_risk = "critical"
            elif days_of_supply <= Decimal(total_lead_time + policy.safety_stock_days):
                inventory_health = "low_stock"
                stockout_risk = "high"
            elif days_of_supply > Decimal(str(policy.target_coverage_days)) * Decimal("1.5"):
                inventory_health = "excess_inventory"
                stockout_risk = "low"
            else:
                inventory_health = "healthy"
                stockout_risk = "low"
            excess_risk = "high" if inventory_health == "excess_inventory" else "none"

        confidence = self._confidence(input_data.inventory_source, available_weight, bool(data_warnings), "stale_inventory" in missing_data)
        negative_unit_profit = input_data.unit_contribution_profit is not None and input_data.unit_contribution_profit < 0
        unverified_source = input_data.inventory_source in {"mock", "system_estimate"}
        automation_eligible = (
            not missing_data and not data_warnings and confidence >= Decimal("0.70")
            and not unverified_source and not negative_unit_profit
        )
        if missing_data or data_warnings:
            degraded_reason = "inventory_inputs_not_automation_safe"
        elif negative_unit_profit:
            degraded_reason = "negative_unit_contribution_profit"
        elif unverified_source:
            degraded_reason = "unverified_inventory_source"
        elif confidence < Decimal("0.70"):
            degraded_reason = "low_confidence_inventory_source"
        else:
            degraded_reason = None

        lost_units = Decimal("0")
        if weighted_sales > 0 and days_of_supply is not None and days_of_supply < Decimal(total_lead_time):
            lost_units = (Decimal(total_lead_time) - days_of_supply) * weighted_sales
        revenue_risk = None if input_data.unit_revenue is None else self._money(lost_units * input_data.unit_revenue)
        profit_risk = None if input_data.unit_contribution_profit is None else self._money(lost_units * max(input_data.unit_contribution_profit, Decimal("0")))
        logistics_cost = None
        if input_data.estimated_extra_logistics_cost_per_unit is not None and recommended_quantity > 0:
            logistics_cost = self._money(Decimal(recommended_quantity) * input_data.estimated_extra_logistics_cost_per_unit)

        return InventoryHealthSnapshot(
            tenant_id=input_data.tenant_id, seller_sku=input_data.seller_sku, marketplace_id=input_data.marketplace_id,
            policy_id=policy.id, policy_version=policy.version, available=input_data.available, reserved=input_data.reserved,
            inbound=input_data.inbound, in_transit=input_data.in_transit, unsellable=input_data.unsellable,
            inventory_position=inventory_position, weighted_daily_sales=weighted_sales, days_of_supply=days_of_supply,
            expected_stockout_date=stockout_date, total_lead_time_days=total_lead_time, safety_stock_units=safety_stock,
            reorder_point=reorder_point, recommended_reorder_quantity=recommended_quantity, excess_units=excess_units,
            recommended_shipping_mode=policy.preferred_shipping_mode, inventory_health=inventory_health,
            stockout_risk=stockout_risk, excess_risk=excess_risk, forecast_confidence=confidence,
            missing_data=tuple(dict.fromkeys(missing_data)), data_warnings=tuple(data_warnings),
            degraded_reason=degraded_reason, automation_eligible=automation_eligible,
            inventory_source=input_data.inventory_source, inventory_collected_at=input_data.inventory_collected_at,
            calculated_at=input_data.calculated_at, calculation_version=CALCULATION_VERSION,
            input_manifest={
                "policy_id": policy.id, "policy_version": policy.version, "policy_source": policy.source,
                "sales_weights": {"7d": str(policy.sales_weight_7d), "14d": str(policy.sales_weight_14d), "30d": str(policy.sales_weight_30d)},
                "seasonality_factor": str(input_data.seasonality_factor), "promotion_factor": str(input_data.promotion_factor),
                "inventory_age_seconds": int(inventory_age.total_seconds()),
                "available_is_net_of_reserved": input_data.available_is_net_of_reserved,
                "unit_revenue": None if input_data.unit_revenue is None else str(input_data.unit_revenue),
                "unit_contribution_profit": None if input_data.unit_contribution_profit is None else str(input_data.unit_contribution_profit),
            },
            estimated_revenue_at_risk=revenue_risk, estimated_profit_at_risk=profit_risk,
            estimated_extra_logistics_cost=logistics_cost,
        )

    @staticmethod
    def _validate_scope(policy: InventoryPolicy, input_data: InventoryHealthInput) -> None:
        if (policy.tenant_id, policy.seller_sku, policy.marketplace_id) != (
            input_data.tenant_id, input_data.seller_sku, input_data.marketplace_id,
        ):
            raise InventoryCalculationError("inventory policy scope does not match calculation input")
        if input_data.calculated_at < policy.effective_from or (policy.effective_to and input_data.calculated_at >= policy.effective_to):
            raise InventoryCalculationError("inventory policy is not effective at calculation time")

    @staticmethod
    def _round_order(raw_quantity: Decimal, minimum_order_quantity: int, case_pack_quantity: int) -> int:
        if raw_quantity <= 0:
            return 0
        quantity = max(raw_quantity, Decimal(minimum_order_quantity))
        packs = (quantity / Decimal(case_pack_quantity)).to_integral_value(rounding=ROUND_CEILING)
        return int(packs * case_pack_quantity)

    @staticmethod
    def _confidence(source: str, available_weight: Decimal, has_warnings: bool, stale: bool) -> Decimal:
        source_score = {"amazon_api": Decimal("0.95"), "csv_import": Decimal("0.85"), "manual": Decimal("0.75"), "system_estimate": Decimal("0.55"), "mock": Decimal("0.20")}.get(source, Decimal("0.50"))
        score = source_score * available_weight
        if has_warnings:
            score *= Decimal("0.70")
        if stale:
            score *= Decimal("0.50")
        return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
