from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.database.models import ManualExecutionResultRow


class ManualExecutionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def reserve(
        self,
        *,
        tenant_id: str,
        issue_id: str,
        approval_request_id: str,
        recommended_action_id: str,
        action_hash: str,
        execution_contract_version: str,
        idempotency_key: str,
        action_type: str,
        approved_change: dict[str, Any],
        executed_by: str,
        notes: str | None = None,
        executed_at: datetime | None = None,
    ) -> tuple[ManualExecutionResultRow, bool]:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        with self.session_factory() as session:
            existing = self._find_existing(
                session=session,
                tenant_id=tenant_id,
                approval_request_id=approval_request_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return _normalize_timestamps(existing), False
        row = ManualExecutionResultRow(
            id=str(uuid4()),
            tenant_id=tenant_id,
            issue_id=issue_id,
            approval_request_id=approval_request_id,
            recommended_action_id=recommended_action_id,
            action_hash=action_hash,
            execution_contract_version=execution_contract_version,
            idempotency_key=idempotency_key,
            action_type=action_type,
            status="executing",
            before_snapshot={},
            approved_change=approved_change,
            actual_change={},
            execution_evidence={},
            executed_by=executed_by,
            executed_at=executed_at or datetime.now(UTC),
            verification_status="pending",
            verification_details={},
            notes=notes,
        )
        try:
            with self.session_factory.begin() as session:
                session.add(row)
                session.flush()
                session.expunge(row)
            return _normalize_timestamps(row), True
        except IntegrityError:
            with self.session_factory() as session:
                existing = self._find_existing(
                    session=session,
                    tenant_id=tenant_id,
                    approval_request_id=approval_request_id,
                    idempotency_key=idempotency_key,
                )
                if existing is None:
                    raise
                return _normalize_timestamps(existing), False

    def complete(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        before_snapshot: dict[str, Any],
        actual_change: dict[str, Any],
        execution_evidence: dict[str, Any],
    ) -> ManualExecutionResultRow:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ManualExecutionResultRow)
                .where(
                    ManualExecutionResultRow.tenant_id == tenant_id,
                    ManualExecutionResultRow.id == execution_id,
                )
                .with_for_update()
            )
            if row is None:
                raise LookupError("manual execution reservation not found")
            if row.status != "executing":
                raise ValueError("manual execution reservation is not executing")
            row.status = "executed"
            row.before_snapshot = before_snapshot
            row.actual_change = actual_change
            row.execution_evidence = execution_evidence
            row.completed_at = datetime.now(UTC)
            session.flush()
            session.expunge(row)
            return _normalize_timestamps(row)

    def mark_failed(self, *, tenant_id: str, execution_id: str, reason: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ManualExecutionResultRow)
                .where(
                    ManualExecutionResultRow.tenant_id == tenant_id,
                    ManualExecutionResultRow.id == execution_id,
                )
                .with_for_update()
            )
            if row is not None and row.status == "executing":
                row.status = "failed"
                row.failure_reason = reason[:2000]
                row.completed_at = datetime.now(UTC)

    def get(self, *, tenant_id: str, execution_id: str) -> ManualExecutionResultRow | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ManualExecutionResultRow).where(
                    ManualExecutionResultRow.tenant_id == tenant_id,
                    ManualExecutionResultRow.id == execution_id,
                )
            )
            return None if row is None else _normalize_timestamps(row)

    def get_by_approval(self, *, tenant_id: str, approval_request_id: str) -> ManualExecutionResultRow | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ManualExecutionResultRow).where(
                    ManualExecutionResultRow.tenant_id == tenant_id,
                    ManualExecutionResultRow.approval_request_id == approval_request_id,
                )
            )
            return None if row is None else _normalize_timestamps(row)

    def get_by_idempotency(self, *, tenant_id: str, idempotency_key: str) -> ManualExecutionResultRow | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ManualExecutionResultRow).where(
                    ManualExecutionResultRow.tenant_id == tenant_id,
                    ManualExecutionResultRow.idempotency_key == idempotency_key,
                )
            )
            return None if row is None else _normalize_timestamps(row)

    def mark_verified(self, *, tenant_id: str, execution_id: str, status: str, details: dict[str, Any]) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ManualExecutionResultRow).where(
                    ManualExecutionResultRow.tenant_id == tenant_id, ManualExecutionResultRow.id == execution_id
                )
            )
            if row:
                row.verification_status = status
                row.verification_details = details
                row.verified_at = datetime.now(UTC)

    @staticmethod
    def _find_existing(
        *,
        session: Session,
        tenant_id: str,
        approval_request_id: str,
        idempotency_key: str,
    ) -> ManualExecutionResultRow | None:
        return session.scalar(
            select(ManualExecutionResultRow).where(
                ManualExecutionResultRow.tenant_id == tenant_id,
                (
                    (ManualExecutionResultRow.approval_request_id == approval_request_id)
                    | (ManualExecutionResultRow.idempotency_key == idempotency_key)
                ),
            )
        )


def _normalize_timestamps(row: ManualExecutionResultRow) -> ManualExecutionResultRow:
    for field_name in ("executed_at", "completed_at", "verified_at"):
        value = getattr(row, field_name)
        if value is None:
            continue
        if value.tzinfo is None:
            setattr(row, field_name, value.replace(tzinfo=UTC))
        else:
            setattr(row, field_name, value.astimezone(UTC))
    return row
