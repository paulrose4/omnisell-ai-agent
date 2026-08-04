from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from domain.briefing import ListingBasicSignal
from domain.models import DailyOperationsBrief, SKUHealthSummary
from infrastructure.database.models import DailyOperationsBriefRow, ListingRow


class DailyOperationsBriefRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, brief: DailyOperationsBrief) -> DailyOperationsBrief:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(DailyOperationsBriefRow).where(
                    DailyOperationsBriefRow.tenant_id == brief.tenant_id,
                    DailyOperationsBriefRow.brief_date == brief.brief_date,
                    DailyOperationsBriefRow.timezone == brief.timezone,
                )
            )
            values = {
                "generated_at": brief.generated_at,
                "issue_counts": brief.issue_counts,
                "domain_counts": brief.domain_counts,
                "total_open_issues": brief.total_open_issues,
                "data_quality_issue_count": brief.data_quality_issue_count,
                "impact_by_currency": brief.impact_by_currency,
                "unallocated_impact_issue_count": brief.unallocated_impact_issue_count,
                "priority_issue_ids": list(brief.priority_issue_ids),
                "priority_action_ids": list(brief.priority_action_ids),
                "sku_health": [self._serialize_sku(item) for item in brief.sku_health],
                "source_manifest": brief.source_manifest,
            }
            if row is None:
                row = DailyOperationsBriefRow(
                    id=brief.id,
                    tenant_id=brief.tenant_id,
                    brief_date=brief.brief_date,
                    timezone=brief.timezone,
                    **values,
                )
                session.add(row)
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            session.flush()
            result = self._domain(row)
        return result

    def get_latest(self, *, tenant_id: str) -> DailyOperationsBrief | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(DailyOperationsBriefRow)
                .where(
                    DailyOperationsBriefRow.tenant_id == tenant_id,
                )
                .order_by(DailyOperationsBriefRow.generated_at.desc())
                .limit(1)
            )
        return self._domain(row) if row else None

    @staticmethod
    def _serialize_sku(item: SKUHealthSummary) -> dict:
        return {
            "tenant_id": item.tenant_id,
            "seller_sku": item.seller_sku,
            "marketplace_id": item.marketplace_id,
            "health_status": item.health_status,
            "highest_severity": item.highest_severity,
            "open_issue_count": item.open_issue_count,
            "issue_types": list(item.issue_types),
            "recommended_action_ids": list(item.recommended_action_ids),
            "impact_by_currency": item.impact_by_currency,
            "has_data_quality_risk": item.has_data_quality_risk,
            "last_detected_at": item.last_detected_at.isoformat(),
        }

    @staticmethod
    def _domain(row: DailyOperationsBriefRow) -> DailyOperationsBrief:
        return DailyOperationsBrief(
            id=row.id,
            tenant_id=row.tenant_id,
            brief_date=row.brief_date,
            timezone=row.timezone,
            generated_at=_as_utc(row.generated_at),
            issue_counts=row.issue_counts or {},
            domain_counts=row.domain_counts or {},
            total_open_issues=row.total_open_issues,
            data_quality_issue_count=row.data_quality_issue_count,
            impact_by_currency=row.impact_by_currency or {},
            unallocated_impact_issue_count=row.unallocated_impact_issue_count,
            priority_issue_ids=tuple(row.priority_issue_ids or []),
            priority_action_ids=tuple(row.priority_action_ids or []),
            sku_health=tuple(
                SKUHealthSummary(
                    tenant_id=item["tenant_id"],
                    seller_sku=item["seller_sku"],
                    marketplace_id=item["marketplace_id"],
                    health_status=item["health_status"],
                    highest_severity=item["highest_severity"],
                    open_issue_count=item["open_issue_count"],
                    issue_types=tuple(item.get("issue_types", [])),
                    recommended_action_ids=tuple(item.get("recommended_action_ids", [])),
                    impact_by_currency=item.get("impact_by_currency", {}),
                    has_data_quality_risk=item.get("has_data_quality_risk", False),
                    last_detected_at=datetime.fromisoformat(item["last_detected_at"]),
                )
                for item in (row.sku_health or [])
            ),
            source_manifest=row.source_manifest or {},
        )


class ListingBasicSignalRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def list_signals(self, *, tenant_id: str) -> list[ListingBasicSignal]:
        with self.session_factory() as session:
            rows = list(session.scalars(select(ListingRow).where(ListingRow.tenant_id == tenant_id)))
        signals: list[ListingBasicSignal] = []
        for row in rows:
            observed_at = _parse_datetime((row.platform_extensions or {}).get("observed_at"))
            signals.append(
                ListingBasicSignal(
                    tenant_id=row.tenant_id,
                    listing_id=row.id,
                    seller_sku=row.seller_sku,
                    marketplace_id=row.marketplace_id,
                    status=row.status,
                    title=row.title,
                    price_amount=Decimal(str(row.price_amount)) if row.price_amount is not None else None,
                    price_currency=row.price_currency,
                    external_product_id=row.external_product_id,
                    observed_at=observed_at,
                )
            )
        return signals


def _parse_datetime(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
