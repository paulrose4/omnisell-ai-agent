from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from domain.listing_quality import ListingOptimizationExperiment, ListingQualitySnapshot
from infrastructure.database.models import ListingOptimizationExperimentRow, ListingQualitySnapshotRow


class ListingQualitySnapshotRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, snapshot: ListingQualitySnapshot) -> ListingQualitySnapshot:
        with self.session_factory.begin() as session:
            session.add(
                ListingQualitySnapshotRow(
                    id=snapshot.id,
                    tenant_id=snapshot.tenant_id,
                    listing_id=snapshot.listing_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    content_version=snapshot.content_version,
                    title=snapshot.title,
                    bullet_points=list(snapshot.bullet_points),
                    description=snapshot.description,
                    image_count=snapshot.image_count,
                    search_terms=list(snapshot.search_terms),
                    listing_status=snapshot.listing_status,
                    quality_score=snapshot.quality_score,
                    quality_status=snapshot.quality_status,
                    section_scores=snapshot.section_scores,
                    quality_issues=list(snapshot.quality_issues),
                    recommendations=list(snapshot.recommendations),
                    evidence=list(snapshot.evidence),
                    confidence=snapshot.confidence,
                    missing_data=list(snapshot.missing_data),
                    degraded_reason=snapshot.degraded_reason,
                    automation_eligible=snapshot.automation_eligible,
                    source=snapshot.source,
                    source_observed_at=snapshot.source_observed_at,
                    calculated_at=snapshot.calculated_at,
                    calculation_version=snapshot.calculation_version,
                    baseline_metrics=snapshot.baseline_metrics,
                )
            )
        return snapshot

    def get(self, *, tenant_id: str, snapshot_id: str) -> ListingQualitySnapshot | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ListingQualitySnapshotRow).where(
                    ListingQualitySnapshotRow.tenant_id == tenant_id,
                    ListingQualitySnapshotRow.id == snapshot_id,
                )
            )
        return self._domain(row) if row else None

    def list_snapshots(self, *, tenant_id: str, seller_sku: str | None = None) -> list[ListingQualitySnapshot]:
        statement = select(ListingQualitySnapshotRow).where(ListingQualitySnapshotRow.tenant_id == tenant_id)
        if seller_sku:
            statement = statement.where(ListingQualitySnapshotRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(ListingQualitySnapshotRow.calculated_at.desc())))
        return [self._domain(row) for row in rows]

    @staticmethod
    def _domain(row: ListingQualitySnapshotRow) -> ListingQualitySnapshot:
        return ListingQualitySnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            listing_id=row.listing_id,
            seller_sku=row.seller_sku,
            marketplace_id=row.marketplace_id,
            content_version=row.content_version,
            title=row.title,
            bullet_points=tuple(row.bullet_points or []),
            description=row.description,
            image_count=row.image_count,
            search_terms=tuple(row.search_terms or []),
            listing_status=row.listing_status,
            quality_score=Decimal(str(row.quality_score)),
            quality_status=row.quality_status,
            section_scores=row.section_scores or {},
            quality_issues=tuple(row.quality_issues or []),
            recommendations=tuple(row.recommendations or []),
            evidence=tuple(row.evidence or []),
            confidence=Decimal(str(row.confidence)),
            missing_data=tuple(row.missing_data or []),
            degraded_reason=row.degraded_reason,
            automation_eligible=row.automation_eligible,
            source=row.source,
            source_observed_at=_as_utc(row.source_observed_at),
            calculated_at=_as_utc(row.calculated_at),
            calculation_version=row.calculation_version,
            baseline_metrics=row.baseline_metrics or {},
        )


class ListingExperimentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, experiment: ListingOptimizationExperiment) -> ListingOptimizationExperiment:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ListingOptimizationExperimentRow).where(
                    ListingOptimizationExperimentRow.tenant_id == experiment.tenant_id,
                    ListingOptimizationExperimentRow.action_hash == experiment.action_hash,
                )
            )
            values = {
                "listing_id": experiment.listing_id,
                "seller_sku": experiment.seller_sku,
                "marketplace_id": experiment.marketplace_id,
                "before_snapshot_id": experiment.before_snapshot_id,
                "after_snapshot_id": experiment.after_snapshot_id,
                "proposed_changes": experiment.proposed_changes,
                "baseline_metrics": experiment.baseline_metrics,
                "observation_start": experiment.observation_start,
                "observation_end": experiment.observation_end,
                "expected_outcome": experiment.expected_outcome,
                "status": experiment.status,
                "approval_request_id": experiment.approval_request_id,
                "effect_metrics": experiment.effect_metrics,
                "evaluation_reasons": list(experiment.evaluation_reasons),
                "rollback_recommended": experiment.rollback_recommended,
                "evaluated_at": experiment.evaluated_at,
                "execution_status": experiment.execution_status,
                "actual_changes": experiment.actual_changes,
                "execution_evidence": experiment.execution_evidence,
                "executed_by": experiment.executed_by,
                "executed_at": experiment.executed_at,
                "execution_notes": experiment.execution_notes,
                "decision": experiment.decision,
                "decision_by": experiment.decision_by,
                "decision_at": experiment.decision_at,
                "decision_reason": experiment.decision_reason,
                "updated_at": experiment.updated_at,
            }
            if row is None:
                row = ListingOptimizationExperimentRow(
                    id=experiment.id,
                    tenant_id=experiment.tenant_id,
                    action_hash=experiment.action_hash,
                    created_at=experiment.created_at,
                    **values,
                )
                session.add(row)
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            session.flush()
            result = self._domain(row)
        return result

    def get(self, *, tenant_id: str, experiment_id: str) -> ListingOptimizationExperiment | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ListingOptimizationExperimentRow).where(
                    ListingOptimizationExperimentRow.tenant_id == tenant_id,
                    ListingOptimizationExperimentRow.id == experiment_id,
                )
            )
        return self._domain(row) if row else None

    def list_experiments(self, *, tenant_id: str, status: str | None = None) -> list[ListingOptimizationExperiment]:
        statement = select(ListingOptimizationExperimentRow).where(
            ListingOptimizationExperimentRow.tenant_id == tenant_id
        )
        if status:
            statement = statement.where(ListingOptimizationExperimentRow.status == status)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(ListingOptimizationExperimentRow.created_at.desc())))
        return [self._domain(row) for row in rows]

    @staticmethod
    def _domain(row: ListingOptimizationExperimentRow) -> ListingOptimizationExperiment:
        return ListingOptimizationExperiment(
            id=row.id,
            tenant_id=row.tenant_id,
            listing_id=row.listing_id,
            seller_sku=row.seller_sku,
            marketplace_id=row.marketplace_id,
            before_snapshot_id=row.before_snapshot_id,
            after_snapshot_id=row.after_snapshot_id,
            proposed_changes=row.proposed_changes or {},
            baseline_metrics=row.baseline_metrics or {},
            observation_start=_as_utc(row.observation_start),
            observation_end=_as_utc(row.observation_end),
            expected_outcome=row.expected_outcome,
            action_hash=row.action_hash,
            status=row.status,
            approval_request_id=row.approval_request_id,
            effect_metrics=row.effect_metrics or {},
            evaluation_reasons=tuple(row.evaluation_reasons or []),
            rollback_recommended=row.rollback_recommended,
            evaluated_at=_as_utc(row.evaluated_at),
            execution_status=row.execution_status or "not_executed",
            actual_changes=row.actual_changes or {},
            execution_evidence=row.execution_evidence or {},
            executed_by=row.executed_by,
            executed_at=_as_utc(row.executed_at),
            execution_notes=row.execution_notes,
            decision=row.decision,
            decision_by=row.decision_by,
            decision_at=_as_utc(row.decision_at),
            decision_reason=row.decision_reason,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
