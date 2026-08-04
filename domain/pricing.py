from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from domain.models import InventoryHealthSnapshot, PriceRecommendation, SKUCostProfile, SKUProfitSnapshot
from safety.policy_engine import PolicyDecision, PolicyEngine, canonical_action_hash

MONEY = Decimal("0.01")
RATE = Decimal("0.0001")


@dataclass(frozen=True)
class PriceRecommendationInput:
    tenant_id: str
    seller_sku: str
    marketplace_id: str
    currency: str
    current_price: Decimal
    requested_price: Decimal | None
    competitor_prices: tuple[Decimal, ...]
    calculated_at: datetime

    def __post_init__(self) -> None:
        if self.calculated_at.tzinfo is None:
            raise ValueError("calculated_at must be timezone-aware")
        if Decimal(str(self.current_price)) <= 0:
            raise ValueError("current_price must be positive")
        if self.requested_price is not None and Decimal(str(self.requested_price)) <= 0:
            raise ValueError("requested_price must be positive")
        if any(Decimal(str(value)) <= 0 for value in self.competitor_prices):
            raise ValueError("competitor prices must be positive")


class PriceRecommendationCalculator:
    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()

    def calculate(
        self,
        *,
        input_data: PriceRecommendationInput,
        profile: SKUCostProfile,
        profit_snapshot: SKUProfitSnapshot,
        inventory_snapshot: InventoryHealthSnapshot,
    ) -> PriceRecommendation:
        self._validate_scope(input_data, profile, profit_snapshot, inventory_snapshot)
        if profit_snapshot.units <= 0 or profit_snapshot.revenue <= 0:
            raise ValueError("profit snapshot requires positive units and revenue for price projection")
        current_price = self._money(input_data.current_price)
        fixed_total = sum(
            (
                profit_snapshot.landed_cost or Decimal("0"),
                profit_snapshot.fulfillment_fees or Decimal("0"),
                profit_snapshot.storage_cost or Decimal("0"),
                profit_snapshot.advertising_cost or Decimal("0"),
                profit_snapshot.other_variable_cost or Decimal("0"),
            ),
            Decimal("0"),
        )
        fixed_unit_cost = fixed_total / Decimal(profit_snapshot.units)
        referral_rate = (profit_snapshot.referral_fees or Decimal("0")) / profit_snapshot.revenue
        return_rate = (profit_snapshot.return_loss or Decimal("0")) / profit_snapshot.revenue
        exchange_buffer = profile.exchange_rate_buffer_rate or Decimal("0")
        variable_rate = referral_rate + return_rate + exchange_buffer
        if variable_rate >= 1:
            raise ValueError("variable price-dependent rate must be below 1")
        break_even_floor = self._money(fixed_unit_cost / (Decimal("1") - variable_rate))
        if profit_snapshot.break_even_price is not None:
            break_even_floor = max(break_even_floor, self._money(profit_snapshot.break_even_price))
        minimum_margin_rate = profile.minimum_margin_rate or Decimal("0")
        minimum_denominator = Decimal("1") - variable_rate - minimum_margin_rate
        if minimum_denominator <= 0:
            raise ValueError("minimum margin configuration leaves no feasible price")
        minimum_margin_floor = self._money(fixed_unit_cost / minimum_denominator)

        if input_data.requested_price is not None:
            recommended_price = self._money(input_data.requested_price)
            candidate_source = "operator_requested"
        elif input_data.competitor_prices:
            market_target = min(Decimal(str(value)) for value in input_data.competitor_prices) * Decimal("0.99")
            recommended_price = max(self._money(market_target), minimum_margin_floor)
            candidate_source = "competitor_guarded"
        else:
            recommended_price = max(current_price, minimum_margin_floor)
            candidate_source = "profit_floor"

        projected_unit_profit = self._money(recommended_price * (Decimal("1") - variable_rate) - fixed_unit_cost)
        projected_margin = self._rate(projected_unit_profit / recommended_price)
        change_rate = self._rate((recommended_price - current_price) / current_price)
        confidence = min(profit_snapshot.confidence, inventory_snapshot.forecast_confidence)
        reasons: list[str] = []
        missing_data = list(dict.fromkeys((*profit_snapshot.missing_inputs, *inventory_snapshot.missing_data)))

        profit_stale = input_data.calculated_at - profit_snapshot.calculated_at > timedelta(hours=24)
        inventory_stale = input_data.calculated_at - inventory_snapshot.inventory_collected_at > timedelta(hours=6)
        if profit_snapshot.degraded_reason:
            reasons.append(profit_snapshot.degraded_reason)
        if profit_stale and "profit_snapshot_stale" not in reasons:
            reasons.append("profit_snapshot_stale")
        if inventory_snapshot.degraded_reason:
            reasons.append(inventory_snapshot.degraded_reason)
        if inventory_stale and "inventory_stale" not in reasons:
            reasons.append("inventory_stale")
        if recommended_price < break_even_floor:
            reasons.append("below_break_even_floor")
        elif projected_margin < minimum_margin_rate:
            reasons.append("below_minimum_margin")
        if inventory_snapshot.stockout_risk == "critical" and recommended_price < current_price:
            reasons.append("price_decrease_during_critical_stockout")

        policy_arguments = {
            "current_price": str(current_price),
            "new_price": str(recommended_price),
            "currency": input_data.currency.upper(),
            "break_even_price": str(break_even_floor),
            "projected_margin": str(projected_margin),
            "minimum_margin_rate": str(minimum_margin_rate),
            "inventory_stockout_risk": inventory_snapshot.stockout_risk,
            "data_complete": not missing_data and not reasons,
        }
        policy = self.policy_engine.evaluate(
            action="update_price", arguments=policy_arguments, confidence=float(confidence)
        )
        if "below_break_even_floor" in reasons or "price_decrease_during_critical_stockout" in reasons:
            decision = PolicyDecision.BLOCK
            risk_level = "critical"
        elif reasons or missing_data or not inventory_snapshot.automation_eligible:
            decision = PolicyDecision.REQUIRE_APPROVAL
            risk_level = "high"
        else:
            decision = policy.decision
            risk_level = policy.risk_level
        if decision == PolicyDecision.BLOCK:
            status = "blocked"
        elif decision == PolicyDecision.REQUIRE_APPROVAL:
            status = "awaiting_approval"
        else:
            status = "ready"
        automation_eligible = (
            decision == PolicyDecision.ALLOW
            and confidence >= Decimal("0.95")
            and not missing_data
            and not reasons
            and inventory_snapshot.automation_eligible
        )
        if policy.reason and policy.reason not in reasons:
            reasons.append(policy.reason)
        action_hash = canonical_action_hash(
            action="update_price",
            tenant_id=input_data.tenant_id,
            resource_id=f"{input_data.marketplace_id}:{input_data.seller_sku}",
            arguments=policy_arguments,
        )
        degraded_reason = None
        if (
            missing_data
            or profit_snapshot.degraded_reason
            or inventory_snapshot.degraded_reason
            or profit_stale
            or inventory_stale
        ):
            degraded_reason = "pricing_evidence_degraded"
        evidence = (
            {
                "profit_snapshot_id": profit_snapshot.id,
                "profit_calculation_version": profit_snapshot.calculation_version,
                "inventory_snapshot_id": inventory_snapshot.id,
                "inventory_calculation_version": inventory_snapshot.calculation_version,
                "cost_profile_id": profile.id,
                "cost_profile_version": profile.version,
                "fixed_unit_cost": str(self._money(fixed_unit_cost)),
                "variable_rate": str(self._rate(variable_rate)),
                "profit_calculated_at": profit_snapshot.calculated_at.isoformat(),
                "inventory_collected_at": inventory_snapshot.inventory_collected_at.isoformat(),
            },
        )
        return PriceRecommendation(
            tenant_id=input_data.tenant_id,
            seller_sku=input_data.seller_sku,
            marketplace_id=input_data.marketplace_id,
            currency=input_data.currency,
            profit_snapshot_id=profit_snapshot.id,
            inventory_snapshot_id=inventory_snapshot.id,
            cost_profile_id=profile.id,
            current_price=current_price,
            recommended_price=recommended_price,
            candidate_source=candidate_source,
            break_even_floor=break_even_floor,
            minimum_margin_floor=minimum_margin_floor,
            minimum_margin_rate=minimum_margin_rate,
            projected_unit_profit=projected_unit_profit,
            projected_margin=projected_margin,
            change_rate=change_rate,
            policy_decision=decision.value,
            policy_reasons=tuple(dict.fromkeys(reasons)),
            risk_level=risk_level,
            status=status,
            confidence=confidence,
            automation_eligible=automation_eligible,
            action_hash=action_hash,
            evidence=evidence,
            missing_data=tuple(missing_data),
            degraded_reason=degraded_reason,
            calculated_at=input_data.calculated_at,
        )

    @staticmethod
    def _validate_scope(
        input_data: PriceRecommendationInput,
        profile: SKUCostProfile,
        profit_snapshot: SKUProfitSnapshot,
        inventory_snapshot: InventoryHealthSnapshot,
    ) -> None:
        expected = (input_data.tenant_id, input_data.seller_sku, input_data.marketplace_id)
        if (profile.tenant_id, profile.seller_sku, profile.marketplace_id) != expected:
            raise ValueError("cost profile scope does not match price request")
        if (profit_snapshot.tenant_id, profit_snapshot.seller_sku, profit_snapshot.marketplace_id) != expected:
            raise ValueError("profit snapshot scope does not match price request")
        if (inventory_snapshot.tenant_id, inventory_snapshot.seller_sku, inventory_snapshot.marketplace_id) != expected:
            raise ValueError("inventory snapshot scope does not match price request")
        currency = input_data.currency.upper()
        if profile.currency != currency or profit_snapshot.currency != currency:
            raise ValueError("price recommendation currency does not match profit evidence")

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)

    @staticmethod
    def _rate(value: Decimal) -> Decimal:
        return Decimal(str(value)).quantize(RATE, rounding=ROUND_HALF_UP)
