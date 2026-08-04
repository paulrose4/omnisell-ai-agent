from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.database.models import ManualExecutionResultRow


class ManualExecutionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, *, tenant_id: str, issue_id: str, recommended_action_id: str | None, action_type: str, before_snapshot: dict[str, Any], approved_change: dict[str, Any], actual_change: dict[str, Any], execution_evidence: dict[str, Any], executed_by: str, notes: str | None = None, executed_at: datetime | None = None) -> ManualExecutionResultRow:
        row = ManualExecutionResultRow(
            id=str(uuid4()), tenant_id=tenant_id, issue_id=issue_id,
            recommended_action_id=recommended_action_id, action_type=action_type, status="executed",
            before_snapshot=before_snapshot, approved_change=approved_change, actual_change=actual_change,
            execution_evidence=execution_evidence, executed_by=executed_by, executed_at=executed_at or datetime.now(UTC),
            verification_status="pending", verification_details={}, notes=notes,
        )
        with self.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.expunge(row)
        return row

    def get(self, *, tenant_id: str, execution_id: str) -> ManualExecutionResultRow | None:
        with self.session_factory() as session:
            return session.scalar(select(ManualExecutionResultRow).where(ManualExecutionResultRow.tenant_id == tenant_id, ManualExecutionResultRow.id == execution_id))

    def mark_verified(self, *, tenant_id: str, execution_id: str, status: str, details: dict[str, Any]) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(select(ManualExecutionResultRow).where(ManualExecutionResultRow.tenant_id == tenant_id, ManualExecutionResultRow.id == execution_id))
            if row:
                row.verification_status = status
                row.verification_details = details
                row.verified_at = datetime.now(UTC)
