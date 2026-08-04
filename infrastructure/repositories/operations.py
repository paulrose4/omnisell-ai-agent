from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from domain.models import OperationalIssue, RecommendedAction, utc_now
from infrastructure.database.models import OperationalIssueRow, OperationalWorkItemRow, RecommendedActionRow
from domain.work_items import sla_due_at, work_item_scope, work_item_status
from safety.policy_engine import canonical_action_hash


_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ISSUE_TRANSITIONS = {
    "open": {"acknowledged", "in_progress", "resolved", "dismissed"},
    "reopened": {"acknowledged", "in_progress", "resolved", "dismissed"},
    "acknowledged": {"in_progress", "awaiting_approval", "resolved", "dismissed"},
    "in_progress": {"awaiting_approval", "approved_for_manual_execution", "verifying", "resolved", "dismissed"},
    "awaiting_approval": {"approved_for_manual_execution", "in_progress", "dismissed"},
    "approved_for_manual_execution": {"verifying", "in_progress", "dismissed"},
    "verifying": {"resolved", "verification_failed"},
    "verification_failed": {"in_progress", "verifying", "dismissed"},
    "resolved": {"reopened"},
    "dismissed": set(),
}
_ACTION_TRANSITIONS = {
    "proposed": {"accepted", "rejected", "canceled"},
    "accepted": {"completed", "canceled"},
    "rejected": set(),
    "completed": set(),
    "canceled": set(),
}


class OperationalIssueRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def upsert(self, issue: OperationalIssue) -> OperationalIssue:
        with self.session_factory.begin() as session:
            row = session.scalar(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == issue.tenant_id,
                OperationalIssueRow.dedupe_key == issue.dedupe_key,
            ))
            if row is None:
                row = OperationalIssueRow(
                    id=issue.id, tenant_id=issue.tenant_id, seller_sku=issue.seller_sku,
                    marketplace_id=issue.marketplace_id, issue_type=issue.issue_type, severity=issue.severity,
                    title=issue.title, summary=issue.summary, status=issue.status, dedupe_key=issue.dedupe_key,
                    confidence=issue.confidence, data_freshness_at=issue.data_freshness_at,
                    evidence=list(issue.evidence), missing_data=list(issue.missing_data), currency=issue.currency,
                    estimated_revenue_impact=issue.estimated_revenue_impact,
                    estimated_profit_impact=issue.estimated_profit_impact,
                    estimated_cost_impact=issue.estimated_cost_impact, degraded_reason=issue.degraded_reason,
                    occurrence_count=issue.occurrence_count, resolution_note=issue.resolution_note,
                    detected_at=issue.detected_at, last_detected_at=issue.last_detected_at,
                    created_at=issue.created_at, updated_at=issue.updated_at,
                    assignee_id=issue.assignee_id, due_at=issue.due_at, acknowledged_at=issue.acknowledged_at,
                    started_at=issue.started_at, resolved_at=issue.resolved_at, resolution_code=issue.resolution_code,
                    execution_result_id=issue.execution_result_id, reopened_count=issue.reopened_count,
                    priority_score=issue.priority_score or _priority(issue),
                )
                session.add(row)
            else:
                row.severity = issue.severity
                row.title = issue.title
                row.summary = issue.summary
                row.confidence = issue.confidence
                row.data_freshness_at = issue.data_freshness_at
                row.evidence = list(issue.evidence)
                row.missing_data = list(issue.missing_data)
                row.currency = issue.currency
                row.estimated_revenue_impact = issue.estimated_revenue_impact
                row.estimated_profit_impact = issue.estimated_profit_impact
                row.estimated_cost_impact = issue.estimated_cost_impact
                row.degraded_reason = issue.degraded_reason
                row.occurrence_count += 1
                row.last_detected_at = issue.last_detected_at
                row.updated_at = issue.updated_at
                if row.status in {"resolved", "dismissed"}:
                    row.status = "reopened"
                    row.reopened_count = (row.reopened_count or 0) + 1
                    row.resolution_note = None
                    row.resolved_at = None
                    row.resolution_code = None
                row.priority_score = _priority(issue)
            session.flush()
            result = self._domain(row)
        return result

    def get(self, *, tenant_id: str, issue_id: str) -> OperationalIssue | None:
        with self.session_factory() as session:
            row = session.scalar(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == tenant_id, OperationalIssueRow.id == issue_id,
            ))
        return self._domain(row) if row else None

    def list_issues(
        self, *, tenant_id: str, status: str | None = None, severity: str | None = None,
        issue_type: str | None = None, marketplace_id: str | None = None, seller_sku: str | None = None,
        active_only: bool = False,
    ) -> list[OperationalIssue]:
        statement = select(OperationalIssueRow).where(OperationalIssueRow.tenant_id == tenant_id)
        if active_only:
            statement = statement.where(OperationalIssueRow.status.in_((
                "open", "reopened", "acknowledged", "in_progress", "awaiting_approval",
                "approved_for_manual_execution", "verifying", "verification_failed",
            )))
        if status:
            statement = statement.where(OperationalIssueRow.status == status)
        if severity:
            statement = statement.where(OperationalIssueRow.severity == severity)
        if issue_type:
            statement = statement.where(OperationalIssueRow.issue_type == issue_type)
        if marketplace_id:
            statement = statement.where(OperationalIssueRow.marketplace_id == marketplace_id)
        if seller_sku:
            statement = statement.where(OperationalIssueRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(
                OperationalIssueRow.last_detected_at.desc(), OperationalIssueRow.created_at.desc(),
            )))
        results = [self._domain(row) for row in rows]
        results.sort(key=lambda item: (-_SEVERITY_RANK[item.severity], -_impact(item), -item.last_detected_at.timestamp()))
        return results

    def transition(self, *, tenant_id: str, issue_id: str, target_status: str, actor_id: str, note: str) -> OperationalIssue:
        with self.session_factory.begin() as session:
            row = session.scalar(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == tenant_id, OperationalIssueRow.id == issue_id,
            ))
            if row is None:
                raise LookupError("operational issue not found")
            if target_status not in _ISSUE_TRANSITIONS[row.status]:
                raise ValueError(f"invalid issue transition: {row.status} -> {target_status}")
            row.status = target_status
            row.resolution_note = note or None
            now = utc_now()
            if target_status == "acknowledged":
                row.assignee_id = actor_id
                row.acknowledged_at = now
            elif target_status == "in_progress":
                row.assignee_id = row.assignee_id or actor_id
                row.started_at = row.started_at or now
            elif target_status == "resolved":
                row.resolved_at = now
                row.resolution_code = note or "verified_resolved"
            row.updated_at = now
            _sync_issue_work_items(session, row)
            session.flush()
            result = self._domain(row)
        return result

    def attach_execution(self, *, tenant_id: str, issue_id: str, execution_result_id: str, actor_id: str) -> OperationalIssue:
        with self.session_factory.begin() as session:
            row = session.scalar(select(OperationalIssueRow).where(OperationalIssueRow.tenant_id == tenant_id, OperationalIssueRow.id == issue_id))
            if row is None:
                raise LookupError("operational issue not found")
            row.execution_result_id = execution_result_id
            row.assignee_id = row.assignee_id or actor_id
            row.status = "verifying"
            row.updated_at = utc_now()
            _sync_issue_work_items(session, row)
            session.flush()
            return self._domain(row)

    def resolve_absent(
        self, *, tenant_id: str, marketplace_id: str, seller_skus: list[str],
        active_dedupe_keys: set[str], verification_eligible_issue_ids: set[str] | None = None,
    ) -> list[str]:
        if not seller_skus:
            return []
        resolved: list[str] = []
        with self.session_factory.begin() as session:
            rows = list(session.scalars(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == tenant_id,
                OperationalIssueRow.marketplace_id == marketplace_id,
                OperationalIssueRow.seller_sku.in_(seller_skus),
                OperationalIssueRow.status.in_(["verifying", "verification_failed"]),
            )))
            for row in rows:
                if verification_eligible_issue_ids is not None and row.id not in verification_eligible_issue_ids:
                    continue
                if row.dedupe_key in active_dedupe_keys:
                    if row.status == "verifying":
                        row.status = "verification_failed"
                    continue
                row.status = "resolved"
                row.resolved_at = utc_now()
                row.resolution_code = "post_sync_verified"
                row.resolution_note = "Resolved after deterministic post-execution synchronization and recalculation."
                _sync_issue_work_items(session, row)
                resolved.append(row.id)
        return resolved

    def resolve_complete_scan_absent(
        self,
        *,
        tenant_id: str,
        complete_scopes: set[tuple[str, str, str]],
        active_dedupe_keys: set[str],
        resolved_at: datetime,
    ) -> list[str]:
        if not complete_scopes:
            return []
        marketplaces = {scope[0] for scope in complete_scopes}
        seller_skus = {scope[1] for scope in complete_scopes}
        resolvable_statuses = {"open", "reopened", "acknowledged", "in_progress"}
        resolved: list[str] = []
        with self.session_factory.begin() as session:
            rows = list(session.scalars(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == tenant_id,
                OperationalIssueRow.marketplace_id.in_(marketplaces),
                OperationalIssueRow.seller_sku.in_(seller_skus),
                OperationalIssueRow.status.in_(resolvable_statuses),
            ).with_for_update()))
            for row in rows:
                scope = (row.marketplace_id, row.seller_sku, _issue_domain(row.issue_type))
                if scope not in complete_scopes or row.dedupe_key in active_dedupe_keys:
                    continue
                row.status = "resolved"
                row.resolved_at = resolved_at
                row.resolution_code = "condition_absent_from_complete_scan"
                row.resolution_note = "The condition was absent from the latest complete deterministic scan for this SKU and evidence domain."
                row.updated_at = resolved_at
                resolved.append(row.id)
                actions = list(session.scalars(select(RecommendedActionRow).where(
                    RecommendedActionRow.tenant_id == tenant_id,
                    RecommendedActionRow.issue_id == row.id,
                    RecommendedActionRow.status == "proposed",
                )))
                for action in actions:
                    action.status = "canceled"
                    action.rejection_reason = "Source issue resolved by a newer complete scan."
                    action.updated_at = resolved_at
                _sync_issue_work_items(session, row)
        return resolved

    @staticmethod
    def _domain(row: OperationalIssueRow) -> OperationalIssue:
        return OperationalIssue(
            id=row.id, tenant_id=row.tenant_id, seller_sku=row.seller_sku, marketplace_id=row.marketplace_id,
            issue_type=row.issue_type, severity=row.severity, title=row.title, summary=row.summary,
            status=row.status, dedupe_key=row.dedupe_key, confidence=Decimal(str(row.confidence)),
            data_freshness_at=_as_utc(row.data_freshness_at), evidence=tuple(row.evidence or []),
            missing_data=tuple(row.missing_data or []), currency=row.currency,
            estimated_revenue_impact=row.estimated_revenue_impact,
            estimated_profit_impact=row.estimated_profit_impact, estimated_cost_impact=row.estimated_cost_impact,
            degraded_reason=row.degraded_reason, occurrence_count=row.occurrence_count,
            resolution_note=row.resolution_note, detected_at=_as_utc(row.detected_at),
            last_detected_at=_as_utc(row.last_detected_at), created_at=_as_utc(row.created_at), updated_at=_as_utc(row.updated_at),
            assignee_id=row.assignee_id, due_at=_optional_utc(row.due_at), acknowledged_at=_optional_utc(row.acknowledged_at),
            started_at=_optional_utc(row.started_at), resolved_at=_optional_utc(row.resolved_at), resolution_code=row.resolution_code,
            execution_result_id=row.execution_result_id, reopened_count=row.reopened_count or 0,
            priority_score=Decimal(str(row.priority_score or 0)),
        )


class RecommendedActionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def upsert(self, action: RecommendedAction, *, canonical_issue_id: str) -> RecommendedAction:
        with self.session_factory.begin() as session:
            row = session.scalar(select(RecommendedActionRow).where(
                RecommendedActionRow.tenant_id == action.tenant_id,
                RecommendedActionRow.dedupe_key == action.dedupe_key,
            ))
            if row is None:
                row = RecommendedActionRow(
                    id=action.id, tenant_id=action.tenant_id, issue_id=canonical_issue_id,
                    action_type=action.action_type, title=action.title, description=action.description,
                    execution_mode=action.execution_mode, risk_level=action.risk_level, status=action.status,
                    parameters=action.parameters, evidence=list(action.evidence), expected_outcome=action.expected_outcome,
                    confidence=action.confidence, automation_eligible=action.automation_eligible,
                    dedupe_key=action.dedupe_key, currency=action.currency,
                    expected_revenue_impact=action.expected_revenue_impact,
                    expected_profit_impact=action.expected_profit_impact,
                    expected_cost_impact=action.expected_cost_impact,
                    rejection_reason=action.rejection_reason, created_at=action.created_at, updated_at=action.updated_at,
                )
                session.add(row)
            else:
                row.issue_id = canonical_issue_id
                row.title = action.title
                row.description = action.description
                row.execution_mode = action.execution_mode
                row.risk_level = action.risk_level
                row.parameters = action.parameters
                row.evidence = list(action.evidence)
                row.expected_outcome = action.expected_outcome
                row.confidence = action.confidence
                row.automation_eligible = action.automation_eligible
                row.currency = action.currency
                row.expected_revenue_impact = action.expected_revenue_impact
                row.expected_profit_impact = action.expected_profit_impact
                row.expected_cost_impact = action.expected_cost_impact
                row.updated_at = action.updated_at
                if row.status in {"rejected", "completed", "canceled"}:
                    row.status = "proposed"
                    row.rejection_reason = None
            issue_row = session.scalar(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == action.tenant_id,
                OperationalIssueRow.id == canonical_issue_id,
            ))
            if issue_row is None:
                raise LookupError("operational issue not found")
            self._sync_work_item(session, action_row=row, issue_row=issue_row)
            session.flush()
            result = self._domain(row)
        return result

    def list_actions(self, *, tenant_id: str, issue_id: str | None = None, status: str | None = None) -> list[RecommendedAction]:
        statement = select(RecommendedActionRow).where(RecommendedActionRow.tenant_id == tenant_id)
        if issue_id:
            statement = statement.where(RecommendedActionRow.issue_id == issue_id)
        if status:
            statement = statement.where(RecommendedActionRow.status == status)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(RecommendedActionRow.created_at.desc())))
        return [self._domain(row) for row in rows]

    def get(self, *, tenant_id: str, action_id: str) -> RecommendedAction | None:
        with self.session_factory() as session:
            row = session.scalar(select(RecommendedActionRow).where(
                RecommendedActionRow.tenant_id == tenant_id,
                RecommendedActionRow.id == action_id,
            ))
        return None if row is None else self._domain(row)

    def transition(self, *, tenant_id: str, action_id: str, target_status: str, reason: str | None = None) -> RecommendedAction:
        with self.session_factory.begin() as session:
            row = session.scalar(select(RecommendedActionRow).where(
                RecommendedActionRow.tenant_id == tenant_id, RecommendedActionRow.id == action_id,
            ))
            if row is None:
                raise LookupError("recommended action not found")
            if target_status not in _ACTION_TRANSITIONS[row.status]:
                raise ValueError(f"invalid action transition: {row.status} -> {target_status}")
            row.status = target_status
            row.rejection_reason = reason if target_status == "rejected" else None
            row.updated_at = utc_now()
            session.flush()
            result = self._domain(row)
        return result

    @staticmethod
    def _domain(row: RecommendedActionRow) -> RecommendedAction:
        return RecommendedAction(
            id=row.id, tenant_id=row.tenant_id, issue_id=row.issue_id, action_type=row.action_type,
            title=row.title, description=row.description, execution_mode=row.execution_mode,
            risk_level=row.risk_level, status=row.status, parameters=row.parameters or {}, evidence=tuple(row.evidence or []),
            expected_outcome=row.expected_outcome, confidence=Decimal(str(row.confidence)),
            automation_eligible=row.automation_eligible, dedupe_key=row.dedupe_key, currency=row.currency,
            expected_revenue_impact=row.expected_revenue_impact, expected_profit_impact=row.expected_profit_impact,
            expected_cost_impact=row.expected_cost_impact, rejection_reason=row.rejection_reason,
            created_at=_as_utc(row.created_at), updated_at=_as_utc(row.updated_at),
        )

    @staticmethod
    def _sync_work_item(session: Session, *, action_row: RecommendedActionRow, issue_row: OperationalIssueRow) -> None:
        work_item = session.scalar(select(OperationalWorkItemRow).where(
            OperationalWorkItemRow.tenant_id == action_row.tenant_id,
            OperationalWorkItemRow.source_type == "recommended_action",
            OperationalWorkItemRow.source_id == action_row.id,
        ))
        domain, team = work_item_scope(issue_row.issue_type, action_row.action_type)
        status = work_item_status(issue_row.status, action_row.status, issue_row.assignee_id)
        action_hash = canonical_action_hash(
            action=action_row.action_type,
            tenant_id=action_row.tenant_id,
            resource_id=f"{issue_row.marketplace_id}:{issue_row.seller_sku}:{issue_row.id}",
            arguments=action_row.parameters or {},
        )
        baseline_metrics = {
            "currency": action_row.currency,
            "expected_revenue_impact": None if action_row.expected_revenue_impact is None else str(action_row.expected_revenue_impact),
            "expected_profit_impact": None if action_row.expected_profit_impact is None else str(action_row.expected_profit_impact),
            "expected_cost_impact": None if action_row.expected_cost_impact is None else str(action_row.expected_cost_impact),
            "confidence": str(action_row.confidence),
        }
        if work_item is None:
            work_item = OperationalWorkItemRow(
                id=str(uuid4()), tenant_id=action_row.tenant_id,
                source_type="recommended_action", source_id=action_row.id,
                operational_issue_id=issue_row.id, recommended_action_id=action_row.id,
                domain=domain, team=team, seller_sku=issue_row.seller_sku,
                marketplace_id=issue_row.marketplace_id, assignee_id=issue_row.assignee_id,
                severity=issue_row.severity, priority_score=issue_row.priority_score or Decimal("0"),
                title=action_row.title, description=action_row.description, status=status,
                due_at=sla_due_at(issue_row.severity, action_row.created_at),
                acknowledged_at=issue_row.acknowledged_at, sla_policy_version="ops-sla-v1",
                escalation_level=0, expected_outcome=action_row.expected_outcome,
                baseline_metrics=baseline_metrics, result_metrics={}, verification_status="pending",
                verification_attempts=0, evidence_manifest=list(action_row.evidence or []),
                action_hash=action_hash, created_at=action_row.created_at, updated_at=action_row.updated_at,
                completed_at=issue_row.resolved_at if status == "completed" else None,
            )
            session.add(work_item)
            return
        work_item.operational_issue_id = issue_row.id
        work_item.domain = domain
        work_item.team = team
        work_item.seller_sku = issue_row.seller_sku
        work_item.marketplace_id = issue_row.marketplace_id
        work_item.severity = issue_row.severity
        work_item.priority_score = issue_row.priority_score or Decimal("0")
        work_item.title = action_row.title
        work_item.description = action_row.description
        work_item.expected_outcome = action_row.expected_outcome
        work_item.baseline_metrics = baseline_metrics
        work_item.evidence_manifest = list(action_row.evidence or [])
        work_item.action_hash = action_hash
        if status in {"completed", "canceled"}:
            work_item.status = status
        elif work_item.status not in {"blocked", "in_progress"}:
            work_item.status = status
        work_item.assignee_id = work_item.assignee_id or issue_row.assignee_id
        work_item.acknowledged_at = work_item.acknowledged_at or issue_row.acknowledged_at
        work_item.updated_at = max(_as_utc(action_row.updated_at), _as_utc(issue_row.updated_at))
        if status in {"completed", "canceled"}:
            work_item.completed_at = issue_row.resolved_at or action_row.updated_at


def _sync_issue_work_items(session: Session, issue_row: OperationalIssueRow) -> None:
    actions = list(session.scalars(select(RecommendedActionRow).where(
        RecommendedActionRow.tenant_id == issue_row.tenant_id,
        RecommendedActionRow.issue_id == issue_row.id,
    )))
    for action_row in actions:
        RecommendedActionRepository._sync_work_item(session, action_row=action_row, issue_row=issue_row)


def _impact(issue: OperationalIssue) -> Decimal:
    return sum((abs(value) for value in (issue.estimated_revenue_impact, issue.estimated_profit_impact, issue.estimated_cost_impact) if value is not None), Decimal("0"))


def _priority(issue: OperationalIssue) -> Decimal:
    return (Decimal(_SEVERITY_RANK[issue.severity] * 1000) + _impact(issue)).quantize(Decimal("0.01"))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)


def _issue_domain(issue_type: str) -> str:
    if issue_type.startswith("inventory_") or issue_type == "missing_inventory_policy":
        return "inventory"
    if issue_type.startswith("profit_") or issue_type == "sku_contribution_loss":
        return "profit"
    if issue_type == "listing_basic_quality":
        return "listing_basic"
    return "other"
