from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from domain.models import SKUCostProfile, SKUProfitSnapshot, utc_now


MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.0001")
CALCULATION_VERSION = "sku-profit-v1"
_REQUIRED_COST_FIELDS = (
    "product_cost", "domestic_shipping_cost", "international_freight_cost", "duty_cost", "prep_cost", "packaging_cost",
    "estimated_referral_fee_rate", "estimated_fulfillment_fee", "estimated_storage_cost", "estimated_return_loss_rate", "exchange_rate_buffer_rate",
)


class ProfitCalculationError(ValueError):
    pass


@dataclass(frozen=True)
class ProfitCalculationInput:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    currency: str
    window_start: datetime
    window_end: datetime
    item_sales: Decimal
    units: int
    discounts: Decimal = Decimal("0")
    coupons: Decimal = Decimal("0")
    refunds: Decimal = Decimal("0")
    promotions: Decimal = Decimal("0")
    actual_referral_fees: Decimal | None = None
    actual_fulfillment_fees: Decimal | None = None
    actual_storage_cost: Decimal | None = None
    advertising_cost: Decimal = Decimal("0")
    advertising_attributed_sales: Decimal | None = None
    actual_return_loss: Decimal | None = None
    other_variable_cost: Decimal = Decimal("0")
    calculated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ProfitCalculationError("profit window timestamps must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ProfitCalculationError("window_end must be after window_start")
        if self.units < 0:
            raise ProfitCalculationError("units cannot be negative")
        object.__setattr__(self, "currency", self.currency.upper())
        for name in (
            "item_sales", "discounts", "coupons", "refunds", "promotions", "advertising_cost", "other_variable_cost",
            "actual_referral_fees", "actual_fulfillment_fees", "actual_storage_cost", "advertising_attributed_sales", "actual_return_loss",
        ):
            value = getattr(self, name)
            if value is not None:
                decimal_value = Decimal(str(value))
                if decimal_value < 0:
                    raise ProfitCalculationError(f"{name} cannot be negative")
                object.__setattr__(self, name, decimal_value)


class ProfitCalculator:
    def calculate(self, *, profile: SKUCostProfile, input_data: ProfitCalculationInput) -> SKUProfitSnapshot:
        self._validate_scope(profile, input_data)
        missing_inputs = tuple(name for name in _REQUIRED_COST_FIELDS if getattr(profile, name) is None)
        gross_revenue = self._money(
            input_data.item_sales - input_data.discounts - input_data.coupons - input_data.refunds - input_data.promotions
        )
        input_manifest = {
            "profile_id": profile.id,
            "profile_version": profile.version,
            "profile_source": profile.source,
            "profile_effective_from": profile.effective_from.isoformat(),
            "window_start": input_data.window_start.isoformat(),
            "window_end": input_data.window_end.isoformat(),
            "actual_referral_fees": input_data.actual_referral_fees is not None,
            "actual_fulfillment_fees": input_data.actual_fulfillment_fees is not None,
            "actual_storage_cost": input_data.actual_storage_cost is not None,
            "actual_return_loss": input_data.actual_return_loss is not None,
        }
        if missing_inputs:
            return SKUProfitSnapshot(
                tenant_id=input_data.tenant_id, seller_sku=input_data.seller_sku, marketplace_id=input_data.marketplace_id,
                currency=input_data.currency, window_start=input_data.window_start, window_end=input_data.window_end,
                revenue=gross_revenue, units=input_data.units, refunds=self._money(input_data.refunds), promotions=self._money(input_data.promotions),
                advertising_cost=self._money(input_data.advertising_cost), calculated_at=input_data.calculated_at,
                calculation_version=CALCULATION_VERSION, missing_inputs=missing_inputs,
                confidence=self._confidence(profile, len(missing_inputs)), degraded_reason="required_profit_inputs_missing",
                input_manifest=input_manifest, estimated=True,
            )

        landed_unit_cost = sum((getattr(profile, name) for name in _REQUIRED_COST_FIELDS[:6]), Decimal("0"))
        landed_cost = landed_unit_cost * input_data.units
        referral_fees = input_data.actual_referral_fees
        if referral_fees is None:
            referral_fees = gross_revenue * profile.estimated_referral_fee_rate
        fulfillment_fees = input_data.actual_fulfillment_fees
        if fulfillment_fees is None:
            fulfillment_fees = profile.estimated_fulfillment_fee * input_data.units
        storage_cost = input_data.actual_storage_cost
        if storage_cost is None:
            storage_cost = profile.estimated_storage_cost * input_data.units
        return_loss = input_data.actual_return_loss
        if return_loss is None:
            return_loss = gross_revenue * profile.estimated_return_loss_rate
        variable_cost = landed_cost + referral_fees + fulfillment_fees + storage_cost + input_data.advertising_cost + return_loss + input_data.other_variable_cost
        contribution_profit = gross_revenue - variable_cost
        contribution_margin = None if gross_revenue <= 0 else self._rate(contribution_profit / gross_revenue)

        denominator = Decimal("1") - profile.estimated_referral_fee_rate - profile.estimated_return_loss_rate - profile.exchange_rate_buffer_rate
        break_even_price = None
        if denominator > 0:
            break_even_price = self._money((landed_unit_cost + profile.estimated_fulfillment_fee + profile.estimated_storage_cost) / denominator)
        advertising_pre_spend_profit = contribution_profit + input_data.advertising_cost
        break_even_acos = None
        if input_data.advertising_attributed_sales is not None and input_data.advertising_attributed_sales > 0:
            break_even_acos = self._rate(advertising_pre_spend_profit / input_data.advertising_attributed_sales)

        estimated = any(
            value is None
            for value in (
                input_data.actual_referral_fees, input_data.actual_fulfillment_fees,
                input_data.actual_storage_cost, input_data.actual_return_loss,
            )
        )
        return SKUProfitSnapshot(
            tenant_id=input_data.tenant_id, seller_sku=input_data.seller_sku, marketplace_id=input_data.marketplace_id,
            currency=input_data.currency, window_start=input_data.window_start, window_end=input_data.window_end,
            revenue=gross_revenue, units=input_data.units, refunds=self._money(input_data.refunds), promotions=self._money(input_data.promotions),
            referral_fees=self._money(referral_fees), fulfillment_fees=self._money(fulfillment_fees), storage_cost=self._money(storage_cost),
            advertising_cost=self._money(input_data.advertising_cost), landed_cost=self._money(landed_cost), return_loss=self._money(return_loss),
            other_variable_cost=self._money(input_data.other_variable_cost), contribution_profit=self._money(contribution_profit),
            contribution_margin=contribution_margin, break_even_price=break_even_price, break_even_acos=break_even_acos,
            calculated_at=input_data.calculated_at, calculation_version=CALCULATION_VERSION, missing_inputs=(),
            confidence=self._confidence(profile, 0), degraded_reason=None, input_manifest=input_manifest, estimated=estimated,
        )

    @staticmethod
    def _validate_scope(profile: SKUCostProfile, input_data: ProfitCalculationInput) -> None:
        if (profile.tenant_id, profile.seller_sku, profile.marketplace_id) != (
            input_data.tenant_id, input_data.seller_sku, input_data.marketplace_id,
        ):
            raise ProfitCalculationError("cost profile scope does not match calculation input")
        if profile.currency != input_data.currency:
            raise ProfitCalculationError("currency mismatch requires an explicit exchange rate")
        if input_data.window_end < profile.effective_from or (profile.effective_to and input_data.window_start >= profile.effective_to):
            raise ProfitCalculationError("cost profile is not effective for the requested window")

    @staticmethod
    def _confidence(profile: SKUCostProfile, missing_count: int) -> Decimal:
        source_score = {"amazon_api": Decimal("0.95"), "csv_import": Decimal("0.85"), "manual": Decimal("0.75"), "system_estimate": Decimal("0.55"), "mock": Decimal("0.20")}.get(profile.source, Decimal("0.50"))
        completeness = Decimal(len(_REQUIRED_COST_FIELDS) - missing_count) / Decimal(len(_REQUIRED_COST_FIELDS))
        return (source_score * completeness).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    @staticmethod
    def _rate(value: Decimal) -> Decimal:
        return Decimal(value).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
