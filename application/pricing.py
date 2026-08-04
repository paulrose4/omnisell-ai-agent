from __future__ import annotations

from domain.pricing import PriceRecommendationCalculator, PriceRecommendationInput
from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository
from infrastructure.repositories.pricing import PriceRecommendationRepository
from infrastructure.repositories.profitability import SKUCostProfileRepository, SKUProfitSnapshotRepository


class PricingEvidenceNotFound(LookupError):
    pass


class PriceRecommendationService:
    def __init__(
        self,
        *,
        cost_repository: SKUCostProfileRepository,
        profit_repository: SKUProfitSnapshotRepository,
        inventory_repository: InventoryHealthSnapshotRepository,
        recommendation_repository: PriceRecommendationRepository,
        calculator: PriceRecommendationCalculator | None = None,
    ) -> None:
        self.cost_repository = cost_repository
        self.profit_repository = profit_repository
        self.inventory_repository = inventory_repository
        self.recommendation_repository = recommendation_repository
        self.calculator = calculator or PriceRecommendationCalculator()

    def calculate(
        self,
        *,
        tenant_id: str,
        profit_snapshot_id: str,
        inventory_snapshot_id: str,
        input_data: PriceRecommendationInput,
    ):
        profit = self.profit_repository.get(tenant_id=tenant_id, snapshot_id=profit_snapshot_id)
        if profit is None:
            raise PricingEvidenceNotFound("profit snapshot not found")
        inventory = self.inventory_repository.get(tenant_id=tenant_id, snapshot_id=inventory_snapshot_id)
        if inventory is None:
            raise PricingEvidenceNotFound("inventory snapshot not found")
        profile = self.cost_repository.get_active(
            tenant_id=tenant_id,
            marketplace_id=input_data.marketplace_id,
            seller_sku=input_data.seller_sku,
            effective_at=profit.window_end,
        )
        if profile is None:
            raise PricingEvidenceNotFound("effective cost profile not found")
        return self.calculator.calculate(
            input_data=input_data,
            profile=profile,
            profit_snapshot=profit,
            inventory_snapshot=inventory,
        )

    def save(self, recommendation):
        return self.recommendation_repository.upsert(recommendation)
