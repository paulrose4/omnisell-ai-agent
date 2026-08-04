from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from domain.models import PriceRecommendation
from infrastructure.database.models import PriceRecommendationRow


class PriceRecommendationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def upsert(self, recommendation: PriceRecommendation) -> PriceRecommendation:
        with self.session_factory.begin() as session:
            row = session.scalar(select(PriceRecommendationRow).where(
                PriceRecommendationRow.tenant_id == recommendation.tenant_id,
                PriceRecommendationRow.action_hash == recommendation.action_hash,
            ))
            values = {
                "seller_sku": recommendation.seller_sku, "marketplace_id": recommendation.marketplace_id,
                "currency": recommendation.currency, "profit_snapshot_id": recommendation.profit_snapshot_id,
                "inventory_snapshot_id": recommendation.inventory_snapshot_id, "cost_profile_id": recommendation.cost_profile_id,
                "current_price": recommendation.current_price, "recommended_price": recommendation.recommended_price,
                "candidate_source": recommendation.candidate_source, "break_even_floor": recommendation.break_even_floor,
                "minimum_margin_floor": recommendation.minimum_margin_floor,
                "minimum_margin_rate": recommendation.minimum_margin_rate,
                "projected_unit_profit": recommendation.projected_unit_profit,
                "projected_margin": recommendation.projected_margin, "change_rate": recommendation.change_rate,
                "policy_decision": recommendation.policy_decision, "policy_reasons": list(recommendation.policy_reasons),
                "risk_level": recommendation.risk_level, "status": recommendation.status,
                "confidence": recommendation.confidence, "automation_eligible": recommendation.automation_eligible,
                "evidence": list(recommendation.evidence), "missing_data": list(recommendation.missing_data),
                "degraded_reason": recommendation.degraded_reason,
                "approval_request_id": recommendation.approval_request_id,
                "calculated_at": recommendation.calculated_at, "updated_at": recommendation.updated_at,
            }
            if row is None:
                row = PriceRecommendationRow(
                    id=recommendation.id, tenant_id=recommendation.tenant_id,
                    action_hash=recommendation.action_hash, occurrence_count=recommendation.occurrence_count,
                    created_at=recommendation.created_at, **values,
                )
                session.add(row)
            else:
                for name, value in values.items():
                    setattr(row, name, value)
                row.occurrence_count += 1
            session.flush()
            result = self._domain(row)
        return result

    def list_recommendations(
        self, *, tenant_id: str, seller_sku: str | None = None, status: str | None = None,
    ) -> list[PriceRecommendation]:
        statement = select(PriceRecommendationRow).where(PriceRecommendationRow.tenant_id == tenant_id)
        if seller_sku:
            statement = statement.where(PriceRecommendationRow.seller_sku == seller_sku)
        if status:
            statement = statement.where(PriceRecommendationRow.status == status)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(PriceRecommendationRow.calculated_at.desc())))
        return [self._domain(row) for row in rows]

    @staticmethod
    def _domain(row: PriceRecommendationRow) -> PriceRecommendation:
        return PriceRecommendation(
            id=row.id, tenant_id=row.tenant_id, seller_sku=row.seller_sku, marketplace_id=row.marketplace_id,
            currency=row.currency, profit_snapshot_id=row.profit_snapshot_id,
            inventory_snapshot_id=row.inventory_snapshot_id, cost_profile_id=row.cost_profile_id,
            current_price=row.current_price, recommended_price=row.recommended_price,
            candidate_source=row.candidate_source, break_even_floor=row.break_even_floor,
            minimum_margin_floor=row.minimum_margin_floor, minimum_margin_rate=row.minimum_margin_rate,
            projected_unit_profit=row.projected_unit_profit, projected_margin=row.projected_margin,
            change_rate=row.change_rate, policy_decision=row.policy_decision,
            policy_reasons=tuple(row.policy_reasons or []), risk_level=row.risk_level, status=row.status,
            confidence=row.confidence, automation_eligible=row.automation_eligible, action_hash=row.action_hash,
            evidence=tuple(row.evidence or []), missing_data=tuple(row.missing_data or []),
            degraded_reason=row.degraded_reason, approval_request_id=row.approval_request_id,
            occurrence_count=row.occurrence_count, calculated_at=_as_utc(row.calculated_at),
            created_at=_as_utc(row.created_at), updated_at=_as_utc(row.updated_at),
        )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
