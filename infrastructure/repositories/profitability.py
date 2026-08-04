from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from domain.models import SKUCostProfile, SKUProfitSnapshot
from infrastructure.database.models import DataImportJobRow, SKUCostProfileRow, SKUProfitSnapshotRow


class ImportIdempotencyConflict(ValueError):
    pass


class SKUCostProfileRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save_profiles(self, *, tenant_id: str, profiles: list[SKUCostProfile], import_job_id: str | None = None) -> list[SKUCostProfile]:
        saved: list[SKUCostProfile] = []
        with self.session_factory.begin() as session:
            for profile in profiles:
                if profile.tenant_id != tenant_id:
                    raise ValueError("profile tenant does not match repository tenant")
                max_version = session.scalar(
                    select(func.max(SKUCostProfileRow.version)).where(
                        SKUCostProfileRow.tenant_id == tenant_id,
                        SKUCostProfileRow.marketplace_id == profile.marketplace_id,
                        SKUCostProfileRow.seller_sku == profile.seller_sku,
                    )
                ) or 0
                active = session.scalar(
                    select(SKUCostProfileRow).where(
                        SKUCostProfileRow.tenant_id == tenant_id,
                        SKUCostProfileRow.marketplace_id == profile.marketplace_id,
                        SKUCostProfileRow.seller_sku == profile.seller_sku,
                        SKUCostProfileRow.effective_to.is_(None),
                    ).order_by(SKUCostProfileRow.effective_from.desc()).limit(1)
                )
                if active is not None:
                    if active.effective_from >= profile.effective_from:
                        raise ValueError("new cost profile must start after the active profile")
                    active.effective_to = profile.effective_from
                profile.version = int(max_version) + 1
                session.add(self._row(profile, import_job_id))
                saved.append(profile)
        return saved

    def get_active(self, *, tenant_id: str, marketplace_id: str, seller_sku: str, effective_at: datetime) -> SKUCostProfile | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(SKUCostProfileRow).where(
                    SKUCostProfileRow.tenant_id == tenant_id,
                    SKUCostProfileRow.marketplace_id == marketplace_id,
                    SKUCostProfileRow.seller_sku == seller_sku,
                    SKUCostProfileRow.effective_from <= effective_at,
                    (SKUCostProfileRow.effective_to.is_(None) | (SKUCostProfileRow.effective_to > effective_at)),
                ).order_by(SKUCostProfileRow.version.desc()).limit(1)
            )
        return self._domain(row) if row else None

    def list_profiles(self, *, tenant_id: str, marketplace_id: str | None = None, seller_sku: str | None = None) -> list[SKUCostProfile]:
        statement = select(SKUCostProfileRow).where(SKUCostProfileRow.tenant_id == tenant_id)
        if marketplace_id:
            statement = statement.where(SKUCostProfileRow.marketplace_id == marketplace_id)
        if seller_sku:
            statement = statement.where(SKUCostProfileRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(SKUCostProfileRow.seller_sku, SKUCostProfileRow.version.desc())))
        return [self._domain(row) for row in rows]

    @staticmethod
    def _row(profile: SKUCostProfile, import_job_id: str | None) -> SKUCostProfileRow:
        return SKUCostProfileRow(
            id=profile.id, tenant_id=profile.tenant_id, seller_sku=profile.seller_sku, marketplace_id=profile.marketplace_id,
            currency=profile.currency, product_cost=profile.product_cost, domestic_shipping_cost=profile.domestic_shipping_cost,
            international_freight_cost=profile.international_freight_cost, duty_cost=profile.duty_cost, prep_cost=profile.prep_cost,
            packaging_cost=profile.packaging_cost, estimated_referral_fee_rate=profile.estimated_referral_fee_rate,
            estimated_fulfillment_fee=profile.estimated_fulfillment_fee, estimated_storage_cost=profile.estimated_storage_cost,
            estimated_return_loss_rate=profile.estimated_return_loss_rate, exchange_rate_buffer_rate=profile.exchange_rate_buffer_rate,
            target_margin_rate=profile.target_margin_rate, minimum_margin_rate=profile.minimum_margin_rate,
            effective_from=profile.effective_from, effective_to=profile.effective_to, source=profile.source, version=profile.version,
            import_job_id=import_job_id, collected_at=profile.collected_at, created_at=profile.created_at,
        )

    @staticmethod
    def _domain(row: SKUCostProfileRow) -> SKUCostProfile:
        return SKUCostProfile(
            id=row.id, tenant_id=row.tenant_id, seller_sku=row.seller_sku, marketplace_id=row.marketplace_id, currency=row.currency,
            product_cost=row.product_cost, domestic_shipping_cost=row.domestic_shipping_cost,
            international_freight_cost=row.international_freight_cost, duty_cost=row.duty_cost, prep_cost=row.prep_cost,
            packaging_cost=row.packaging_cost, estimated_referral_fee_rate=row.estimated_referral_fee_rate,
            estimated_fulfillment_fee=row.estimated_fulfillment_fee, estimated_storage_cost=row.estimated_storage_cost,
            estimated_return_loss_rate=row.estimated_return_loss_rate, exchange_rate_buffer_rate=row.exchange_rate_buffer_rate,
            target_margin_rate=row.target_margin_rate, minimum_margin_rate=row.minimum_margin_rate,
            effective_from=_as_utc(row.effective_from), effective_to=_as_utc(row.effective_to), source=row.source, version=row.version,
            collected_at=_as_utc(row.collected_at), created_at=_as_utc(row.created_at),
        )


class SKUProfitSnapshotRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, snapshot: SKUProfitSnapshot) -> SKUProfitSnapshot:
        with self.session_factory.begin() as session:
            session.add(SKUProfitSnapshotRow(
                id=snapshot.id, tenant_id=snapshot.tenant_id, seller_sku=snapshot.seller_sku, marketplace_id=snapshot.marketplace_id,
                currency=snapshot.currency, window_start=snapshot.window_start, window_end=snapshot.window_end,
                revenue=snapshot.revenue, units=snapshot.units, refunds=snapshot.refunds, promotions=snapshot.promotions,
                referral_fees=snapshot.referral_fees, fulfillment_fees=snapshot.fulfillment_fees, storage_cost=snapshot.storage_cost,
                advertising_cost=snapshot.advertising_cost, landed_cost=snapshot.landed_cost, return_loss=snapshot.return_loss,
                other_variable_cost=snapshot.other_variable_cost, contribution_profit=snapshot.contribution_profit,
                contribution_margin=snapshot.contribution_margin, break_even_price=snapshot.break_even_price,
                break_even_acos=snapshot.break_even_acos, calculated_at=snapshot.calculated_at,
                calculation_version=snapshot.calculation_version, missing_inputs=list(snapshot.missing_inputs),
                confidence=snapshot.confidence, degraded_reason=snapshot.degraded_reason,
                input_manifest=snapshot.input_manifest, estimated=snapshot.estimated,
            ))
        return snapshot

    def get(self, *, tenant_id: str, snapshot_id: str) -> SKUProfitSnapshot | None:
        with self.session_factory() as session:
            row = session.scalar(select(SKUProfitSnapshotRow).where(
                SKUProfitSnapshotRow.tenant_id == tenant_id,
                SKUProfitSnapshotRow.id == snapshot_id,
            ))
        return self._domain(row) if row else None

    @staticmethod
    def _domain(row: SKUProfitSnapshotRow) -> SKUProfitSnapshot:
        return SKUProfitSnapshot(
            id=row.id, tenant_id=row.tenant_id, seller_sku=row.seller_sku, marketplace_id=row.marketplace_id,
            currency=row.currency, window_start=_as_utc(row.window_start), window_end=_as_utc(row.window_end),
            revenue=row.revenue, units=row.units, refunds=row.refunds, promotions=row.promotions,
            referral_fees=row.referral_fees, fulfillment_fees=row.fulfillment_fees, storage_cost=row.storage_cost,
            advertising_cost=row.advertising_cost, landed_cost=row.landed_cost, return_loss=row.return_loss,
            other_variable_cost=row.other_variable_cost, contribution_profit=row.contribution_profit,
            contribution_margin=row.contribution_margin, break_even_price=row.break_even_price,
            break_even_acos=row.break_even_acos, calculated_at=_as_utc(row.calculated_at),
            calculation_version=row.calculation_version, missing_inputs=tuple(row.missing_inputs or []),
            confidence=row.confidence, degraded_reason=row.degraded_reason, input_manifest=row.input_manifest or {},
            estimated=row.estimated,
        )


class DataImportJobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get_by_idempotency(self, *, tenant_id: str, import_type: str, idempotency_key: str) -> DataImportJobRow | None:
        with self.session_factory() as session:
            return session.scalar(select(DataImportJobRow).where(
                DataImportJobRow.tenant_id == tenant_id, DataImportJobRow.import_type == import_type,
                DataImportJobRow.idempotency_key == idempotency_key,
            ))

    def create(self, *, tenant_id: str, actor_id: str, import_type: str, idempotency_key: str, content_sha256: str, dry_run: bool, total_rows: int, valid_rows: int, errors: list[dict[str, Any]]) -> str:
        job_id = str(uuid4())
        with self.session_factory.begin() as session:
            session.add(DataImportJobRow(
                id=job_id, tenant_id=tenant_id, actor_id=actor_id, import_type=import_type,
                idempotency_key=idempotency_key, content_sha256=content_sha256,
                status="validated" if not errors else "validation_failed", dry_run=dry_run,
                total_rows=total_rows, valid_rows=valid_rows, committed_rows=0, error_count=len(errors), errors=errors,
            ))
        return job_id

    def mark_committed(self, *, job_id: str, committed_rows: int, completed_at: datetime) -> None:
        with self.session_factory.begin() as session:
            row = session.get(DataImportJobRow, job_id)
            if row is None:
                raise KeyError(job_id)
            row.status = "completed"
            row.committed_rows = committed_rows
            row.completed_at = completed_at


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
