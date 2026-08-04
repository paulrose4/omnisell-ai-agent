from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from domain.listing_quality import (
    ListingExperimentEvaluator,
    ListingOptimizationExperiment,
    ListingQualityEvaluator,
    ListingQualityInput,
)
from domain.models import OperationalIssue, RecommendedAction, new_id
from infrastructure.repositories.listing_quality import ListingExperimentRepository, ListingQualitySnapshotRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository


class ListingQualityNotFound(LookupError):
    pass


class ListingQualityService:
    def __init__(
        self,
        *,
        snapshot_repository: ListingQualitySnapshotRepository,
        experiment_repository: ListingExperimentRepository,
        issue_repository: OperationalIssueRepository,
        action_repository: RecommendedActionRepository,
        quality_evaluator: ListingQualityEvaluator | None = None,
        experiment_evaluator: ListingExperimentEvaluator | None = None,
    ) -> None:
        self.snapshot_repository = snapshot_repository
        self.experiment_repository = experiment_repository
        self.issue_repository = issue_repository
        self.action_repository = action_repository
        self.quality_evaluator = quality_evaluator or ListingQualityEvaluator()
        self.experiment_evaluator = experiment_evaluator or ListingExperimentEvaluator()

    def calculate_snapshot(self, input_data: ListingQualityInput):
        snapshot = self.snapshot_repository.save(self.quality_evaluator.evaluate(input_data))
        issue = None
        action = None
        if snapshot.quality_status != "good" or snapshot.quality_issues:
            issue, action = self._quality_issue(snapshot)
        else:
            for existing in self.issue_repository.list_issues(
                tenant_id=snapshot.tenant_id, issue_type="listing_quality_risk", seller_sku=snapshot.seller_sku,
            ):
                if existing.marketplace_id == snapshot.marketplace_id and existing.status not in {"resolved", "dismissed"}:
                    self.issue_repository.transition(
                        tenant_id=snapshot.tenant_id, issue_id=existing.id, target_status="resolved",
                        actor_id="listing_quality_service", note=f"Resolved by quality snapshot {snapshot.id}",
                    )
        return snapshot, issue, action

    def create_experiment(
        self,
        *,
        tenant_id: str,
        before_snapshot_id: str,
        proposed_changes: dict,
        observation_start: datetime,
        observation_end: datetime,
        created_at: datetime,
    ) -> ListingOptimizationExperiment:
        before = self.snapshot_repository.get(tenant_id=tenant_id, snapshot_id=before_snapshot_id)
        if before is None:
            raise ListingQualityNotFound("before Listing quality snapshot not found")
        return ListingOptimizationExperiment.create(
            tenant_id=tenant_id, listing_id=before.listing_id, seller_sku=before.seller_sku,
            marketplace_id=before.marketplace_id, before_snapshot=before,
            proposed_changes=proposed_changes, observation_start=observation_start,
            observation_end=observation_end, created_at=created_at,
        )

    def save_experiment(self, experiment: ListingOptimizationExperiment) -> ListingOptimizationExperiment:
        return self.experiment_repository.save(experiment)

    def evaluate_experiment(
        self, *, tenant_id: str, experiment_id: str, after_snapshot_id: str, evaluated_at: datetime,
    ) -> ListingOptimizationExperiment:
        experiment = self.experiment_repository.get(tenant_id=tenant_id, experiment_id=experiment_id)
        if experiment is None:
            raise ListingQualityNotFound("Listing optimization experiment not found")
        before = self.snapshot_repository.get(tenant_id=tenant_id, snapshot_id=experiment.before_snapshot_id)
        after = self.snapshot_repository.get(tenant_id=tenant_id, snapshot_id=after_snapshot_id)
        if before is None or after is None:
            raise ListingQualityNotFound("experiment quality snapshot not found")
        evaluated = self.experiment_evaluator.evaluate(
            experiment=experiment, before=before, after=after, evaluated_at=evaluated_at,
        )
        saved = self.experiment_repository.save(evaluated)
        if saved.rollback_recommended:
            self._rollback_issue(saved, after)
        return saved

    def _quality_issue(self, snapshot):
        severity = {"critical": "critical", "poor": "high", "fair": "medium"}.get(snapshot.quality_status, "low")
        issue = OperationalIssue(
            tenant_id=snapshot.tenant_id, seller_sku=snapshot.seller_sku,
            marketplace_id=snapshot.marketplace_id, issue_type="listing_quality_risk", severity=severity,
            title=f"{snapshot.seller_sku} Listing quality requires review",
            summary=f"Deterministic Listing quality score is {snapshot.quality_score} ({snapshot.quality_status}).",
            status="open", dedupe_key=_dedupe(snapshot.tenant_id, "listing_quality_risk", snapshot.marketplace_id, snapshot.seller_sku),
            confidence=snapshot.confidence, data_freshness_at=snapshot.source_observed_at or snapshot.calculated_at,
            evidence=({"snapshot_id": snapshot.id, "calculation_version": snapshot.calculation_version, "quality_score": str(snapshot.quality_score)},),
            missing_data=snapshot.missing_data, degraded_reason=snapshot.degraded_reason,
            detected_at=snapshot.calculated_at, last_detected_at=snapshot.calculated_at,
        )
        canonical = self.issue_repository.upsert(issue)
        action = RecommendedAction(
            tenant_id=snapshot.tenant_id, issue_id=canonical.id, action_type="prepare_listing_optimization_experiment",
            title="Prepare controlled Listing optimization experiment",
            description="Review structured content recommendations, approve exact changes and measure post-change business outcomes.",
            execution_mode="single_approval", risk_level="high" if severity in {"critical", "high"} else "medium",
            status="proposed", parameters={"listing_id": snapshot.listing_id, "snapshot_id": snapshot.id},
            evidence=canonical.evidence, expected_outcome="Improve Listing quality without worsening conversion or returns.",
            confidence=snapshot.confidence, automation_eligible=False,
            dedupe_key=_dedupe(snapshot.tenant_id, canonical.dedupe_key, "prepare_listing_optimization_experiment"),
            created_at=snapshot.calculated_at, updated_at=snapshot.calculated_at,
        )
        saved_action = self.action_repository.upsert(action, canonical_issue_id=canonical.id)
        return canonical, saved_action

    def _rollback_issue(self, experiment, after):
        issue = OperationalIssue(
            tenant_id=experiment.tenant_id, seller_sku=experiment.seller_sku,
            marketplace_id=experiment.marketplace_id, issue_type="listing_experiment_regression", severity="critical",
            title=f"{experiment.seller_sku} Listing experiment should be rolled back",
            summary="Observed conversion or return-rate guardrails worsened after the Listing experiment.",
            status="open", dedupe_key=_dedupe(experiment.tenant_id, "listing_experiment_regression", experiment.id),
            confidence=after.confidence, data_freshness_at=after.source_observed_at or after.calculated_at,
            evidence=({"experiment_id": experiment.id, "after_snapshot_id": after.id, "effect_metrics": experiment.effect_metrics},),
            missing_data=(), degraded_reason=None, detected_at=experiment.evaluated_at,
            last_detected_at=experiment.evaluated_at,
        )
        canonical = self.issue_repository.upsert(issue)
        action = RecommendedAction(
            tenant_id=experiment.tenant_id, issue_id=canonical.id, action_type="rollback_listing_experiment",
            title="Review rollback to previous Listing content",
            description="Restore the approved before-content only through the normal Listing approval and execution workflow.",
            execution_mode="single_approval", risk_level="critical", status="proposed",
            parameters={"experiment_id": experiment.id, "before_snapshot_id": experiment.before_snapshot_id},
            evidence=canonical.evidence, expected_outcome="Recover conversion and return-rate guardrails.",
            confidence=after.confidence, automation_eligible=False,
            dedupe_key=_dedupe(experiment.tenant_id, canonical.dedupe_key, "rollback_listing_experiment"),
            created_at=experiment.evaluated_at, updated_at=experiment.evaluated_at,
        )
        self.action_repository.upsert(action, canonical_issue_id=canonical.id)


def _dedupe(*parts: str) -> str:
    return sha256("|".join(parts).encode("utf-8")).hexdigest()
