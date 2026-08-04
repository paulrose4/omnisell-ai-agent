from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from domain.work_items import WORK_ITEM_ACTIVE_STATUSES, OperationalWorkItem
from infrastructure.database.models import OperationalWorkItemRow


class OperationalWorkItemRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get(self, *, tenant_id: str, work_item_id: str) -> OperationalWorkItem | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == work_item_id,
                )
            )
        return None if row is None else self._domain(row)

    def get_by_source(self, *, tenant_id: str, source_type: str, source_id: str) -> OperationalWorkItem | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.source_type == source_type,
                    OperationalWorkItemRow.source_id == source_id,
                )
            )
        return None if row is None else self._domain(row)

    def list(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        domain: str | None = None,
        team: str | None = None,
        assignee_id: str | None = None,
        active_only: bool = False,
    ) -> list[OperationalWorkItem]:
        statement = select(OperationalWorkItemRow).where(OperationalWorkItemRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(OperationalWorkItemRow.status == status)
        if domain:
            statement = statement.where(OperationalWorkItemRow.domain == domain)
        if team:
            statement = statement.where(OperationalWorkItemRow.team == team)
        if assignee_id:
            statement = statement.where(OperationalWorkItemRow.assignee_id == assignee_id)
        if active_only:
            statement = statement.where(OperationalWorkItemRow.status.in_(WORK_ITEM_ACTIVE_STATUSES))
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    statement.order_by(
                        OperationalWorkItemRow.escalation_level.desc(),
                        OperationalWorkItemRow.priority_score.desc(),
                        OperationalWorkItemRow.due_at,
                    )
                )
            )
        return [self._domain(row) for row in rows]

    def list_page(
        self,
        *,
        tenant_id: str,
        page: int,
        page_size: int,
        status: str | None = None,
        statuses: tuple[str, ...] = (),
        domain: str | None = None,
        team: str | None = None,
        assignee_id: str | None = None,
        active_only: bool = False,
        unassigned_only: bool = False,
        verification_statuses: tuple[str, ...] = (),
        status_or_verification: bool = False,
        due_at_from: datetime | None = None,
        due_at_before: datetime | None = None,
    ) -> tuple[int, list[OperationalWorkItem]]:
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be positive")
        statement = select(OperationalWorkItemRow).where(OperationalWorkItemRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(OperationalWorkItemRow.status == status)
        if statuses and verification_statuses and status_or_verification:
            statement = statement.where(
                or_(
                    OperationalWorkItemRow.status.in_(statuses),
                    OperationalWorkItemRow.verification_status.in_(verification_statuses),
                )
            )
        else:
            if statuses:
                statement = statement.where(OperationalWorkItemRow.status.in_(statuses))
            if verification_statuses:
                statement = statement.where(OperationalWorkItemRow.verification_status.in_(verification_statuses))
        if domain:
            statement = statement.where(OperationalWorkItemRow.domain == domain)
        if team:
            statement = statement.where(OperationalWorkItemRow.team == team)
        if assignee_id:
            statement = statement.where(OperationalWorkItemRow.assignee_id == assignee_id)
        if unassigned_only:
            statement = statement.where(OperationalWorkItemRow.assignee_id.is_(None))
        if active_only:
            statement = statement.where(OperationalWorkItemRow.status.in_(WORK_ITEM_ACTIVE_STATUSES))
        if due_at_from is not None:
            statement = statement.where(OperationalWorkItemRow.due_at >= due_at_from)
        if due_at_before is not None:
            statement = statement.where(OperationalWorkItemRow.due_at < due_at_before)

        ordered = statement.order_by(
            OperationalWorkItemRow.escalation_level.desc(),
            OperationalWorkItemRow.priority_score.desc(),
            OperationalWorkItemRow.due_at,
            OperationalWorkItemRow.id,
        )
        with self.session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            rows = list(session.scalars(ordered.offset((page - 1) * page_size).limit(page_size)))
        return total, [self._domain(row) for row in rows]

    def bulk_update(
        self,
        *,
        tenant_id: str,
        work_item_ids: list[str],
        operation: str,
        actor_id: str,
        assignee_id: str | None = None,
        due_at: datetime | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> list[OperationalWorkItem]:
        allowed = {"assign", "set_due_at", "acknowledge", "start", "block", "unblock", "handoff"}
        if operation not in allowed:
            raise ValueError("bulk operation is not collaboration-safe")
        effective_now = now or datetime.now(UTC)
        if effective_now.tzinfo is None or (due_at is not None and due_at.tzinfo is None):
            raise ValueError("work item timestamps must be timezone-aware")
        with self.session_factory.begin() as session:
            rows = list(
                session.scalars(
                    select(OperationalWorkItemRow)
                    .where(
                        OperationalWorkItemRow.tenant_id == tenant_id,
                        OperationalWorkItemRow.id.in_(work_item_ids),
                    )
                    .with_for_update()
                )
            )
            if len(rows) != len(set(work_item_ids)):
                raise LookupError("one or more work items were not found")
            for row in rows:
                if row.status in {"completed", "canceled"}:
                    raise ValueError("completed work items cannot be changed in bulk")
                if operation == "assign":
                    if not assignee_id:
                        raise ValueError("assignee_id is required")
                    row.assignee_id = assignee_id
                    if row.status == "open":
                        row.status = "assigned"
                elif operation == "set_due_at":
                    if due_at is None:
                        raise ValueError("due_at is required")
                    row.due_at = due_at
                elif operation == "acknowledge":
                    row.assignee_id = row.assignee_id or actor_id
                    row.acknowledged_at = row.acknowledged_at or effective_now
                    if row.status == "open":
                        row.status = "assigned"
                elif operation == "start":
                    if row.status in {"blocked", "awaiting_approval", "verifying"}:
                        raise ValueError("blocked, approval, or verification work cannot be started in bulk")
                    row.assignee_id = row.assignee_id or actor_id
                    row.acknowledged_at = row.acknowledged_at or effective_now
                    row.status = "in_progress"
                elif operation == "block":
                    if not note:
                        raise ValueError("blocked_reason is required")
                    row.status = "blocked"
                    row.blocked_reason = note
                elif operation == "unblock":
                    if row.status != "blocked":
                        raise ValueError("only blocked work items can be unblocked")
                    row.status = "assigned" if row.assignee_id else "open"
                    row.blocked_reason = None
                elif operation == "handoff":
                    if not assignee_id or not note:
                        raise ValueError("handoff requires assignee_id and note")
                    row.assignee_id = assignee_id
                    row.handoff_note = note
                    row.last_handoff_at = effective_now
                    if row.status in {"open", "assigned", "in_progress"}:
                        row.status = "assigned"
                row.updated_at = effective_now
            session.flush()
            return [self._domain(row) for row in rows]

    def escalate_due(self, *, tenant_id: str, now: datetime | None = None) -> list[OperationalWorkItem]:
        effective_now = now or datetime.now(UTC)
        if effective_now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with self.session_factory.begin() as session:
            rows = list(
                session.scalars(
                    select(OperationalWorkItemRow)
                    .where(
                        OperationalWorkItemRow.tenant_id == tenant_id,
                        OperationalWorkItemRow.status.in_(WORK_ITEM_ACTIVE_STATUSES),
                        OperationalWorkItemRow.due_at < effective_now,
                    )
                    .with_for_update()
                )
            )
            changed = []
            for row in rows:
                target_level = 2 if row.verification_status == "failed" and row.verification_attempts >= 2 else 1
                if row.escalation_level >= target_level:
                    continue
                row.escalation_level = target_level
                row.updated_at = effective_now
                changed.append(row)
            session.flush()
            return [self._domain(row) for row in changed]

    def workload(self, *, tenant_id: str, now: datetime | None = None) -> dict[str, object]:
        effective_now = now or datetime.now(UTC)
        items = self.list(tenant_id=tenant_id, active_only=True)
        by_assignee: dict[str, dict[str, object]] = {}
        for item in items:
            key = item.assignee_id or "unassigned"
            summary = by_assignee.setdefault(
                key,
                {
                    "assignee_id": key,
                    "total": 0,
                    "critical": 0,
                    "overdue": 0,
                    "awaiting_approval": 0,
                    "blocked": 0,
                },
            )
            summary["total"] = int(summary["total"]) + 1
            if item.severity == "critical":
                summary["critical"] = int(summary["critical"]) + 1
            if item.due_at < effective_now:
                summary["overdue"] = int(summary["overdue"]) + 1
            if item.status == "awaiting_approval":
                summary["awaiting_approval"] = int(summary["awaiting_approval"]) + 1
            if item.status == "blocked":
                summary["blocked"] = int(summary["blocked"]) + 1
        return {
            "total_active": len(items),
            "unassigned": sum(1 for item in items if item.assignee_id is None),
            "overdue": sum(1 for item in items if item.due_at < effective_now),
            "assignees": sorted(
                by_assignee.values(),
                key=lambda item: (-int(item["critical"]), -int(item["total"]), str(item["assignee_id"])),
            ),
        }

    @staticmethod
    def _domain(row: OperationalWorkItemRow) -> OperationalWorkItem:
        return OperationalWorkItem(
            id=row.id,
            tenant_id=row.tenant_id,
            source_type=row.source_type,
            source_id=row.source_id,
            operational_issue_id=row.operational_issue_id,
            recommended_action_id=row.recommended_action_id,
            domain=row.domain,
            team=row.team,
            seller_sku=row.seller_sku,
            marketplace_id=row.marketplace_id,
            brand=row.brand,
            assignee_id=row.assignee_id,
            severity=row.severity,
            priority_score=Decimal(str(row.priority_score)),
            title=row.title,
            description=row.description,
            status=row.status,
            due_at=_utc(row.due_at),
            acknowledged_at=_optional_utc(row.acknowledged_at),
            sla_policy_version=row.sla_policy_version,
            escalation_level=row.escalation_level,
            expected_outcome=row.expected_outcome,
            baseline_metrics=row.baseline_metrics or {},
            result_metrics=row.result_metrics or {},
            verification_status=row.verification_status,
            verification_attempts=row.verification_attempts,
            evidence_manifest=list(row.evidence_manifest or []),
            action_hash=row.action_hash,
            handoff_note=row.handoff_note,
            blocked_reason=row.blocked_reason,
            last_handoff_at=_optional_utc(row.last_handoff_at),
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            completed_at=_optional_utc(row.completed_at),
        )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)
