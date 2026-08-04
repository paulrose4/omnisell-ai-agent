from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models import OperationalIssue, RecommendedAction, utc_now
from domain.operations import OperationalIssueDetector
from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.profitability import SKUProfitSnapshotRepository


class SourceSnapshotNotFound(LookupError):
    pass


@dataclass(frozen=True)
class PersistedDetectionResult:
    issues: tuple[OperationalIssue, ...]
    actions: tuple[RecommendedAction, ...]
    resolved_issue_ids: tuple[str, ...] = ()


class OperationalIssueService:
    def __init__(
        self,
        *,
        inventory_repository: InventoryHealthSnapshotRepository,
        profit_repository: SKUProfitSnapshotRepository,
        issue_repository: OperationalIssueRepository,
        action_repository: RecommendedActionRepository,
        detector: OperationalIssueDetector | None = None,
    ) -> None:
        self.inventory_repository = inventory_repository
        self.profit_repository = profit_repository
        self.issue_repository = issue_repository
        self.action_repository = action_repository
        self.detector = detector or OperationalIssueDetector()

    def detect(
        self,
        *,
        tenant_id: str,
        inventory_snapshot_ids: list[str],
        profit_snapshot_ids: list[str],
        complete_scan: bool = False,
        detected_at: datetime | None = None,
    ) -> PersistedDetectionResult:
        inventory_snapshots = []
        for snapshot_id in inventory_snapshot_ids:
            snapshot = self.inventory_repository.get(tenant_id=tenant_id, snapshot_id=snapshot_id)
            if snapshot is None:
                raise SourceSnapshotNotFound(f"inventory snapshot not found: {snapshot_id}")
            inventory_snapshots.append(snapshot)
        profit_snapshots = []
        for snapshot_id in profit_snapshot_ids:
            snapshot = self.profit_repository.get(tenant_id=tenant_id, snapshot_id=snapshot_id)
            if snapshot is None:
                raise SourceSnapshotNotFound(f"profit snapshot not found: {snapshot_id}")
            profit_snapshots.append(snapshot)
        inventory_snapshots = self._latest_by_sku(inventory_snapshots)
        profit_snapshots = self._latest_by_sku(profit_snapshots)
        effective_detected_at = detected_at or utc_now()
        result = self.detector.detect(
            tenant_id=tenant_id,
            inventory_snapshots=inventory_snapshots,
            profit_snapshots=profit_snapshots,
            detected_at=effective_detected_at,
        )
        canonical_issue_ids: dict[str, str] = {}
        saved_issues: list[OperationalIssue] = []
        for issue in result.issues:
            saved = self.issue_repository.upsert(issue)
            canonical_issue_ids[issue.id] = saved.id
            saved_issues.append(saved)
        saved_actions = [
            self.action_repository.upsert(action, canonical_issue_id=canonical_issue_ids[action.issue_id])
            for action in result.actions
        ]
        resolved_issue_ids: tuple[str, ...] = ()
        if complete_scan:
            complete_scopes = {
                (snapshot.marketplace_id, snapshot.seller_sku, "inventory")
                for snapshot in inventory_snapshots
            } | {
                (snapshot.marketplace_id, snapshot.seller_sku, "profit")
                for snapshot in profit_snapshots
            }
            resolved_issue_ids = tuple(self.issue_repository.resolve_complete_scan_absent(
                tenant_id=tenant_id,
                complete_scopes=complete_scopes,
                active_dedupe_keys={issue.dedupe_key for issue in saved_issues},
                resolved_at=effective_detected_at,
            ))
        return PersistedDetectionResult(
            issues=tuple(saved_issues), actions=tuple(saved_actions), resolved_issue_ids=resolved_issue_ids,
        )

    @staticmethod
    def _latest_by_sku(snapshots: list) -> list:
        latest: dict[tuple[str, str], object] = {}
        for snapshot in snapshots:
            key = (snapshot.marketplace_id, snapshot.seller_sku)
            current = latest.get(key)
            if current is None or (snapshot.calculated_at, snapshot.id) > (current.calculated_at, current.id):
                latest[key] = snapshot
        return [latest[key] for key in sorted(latest)]
