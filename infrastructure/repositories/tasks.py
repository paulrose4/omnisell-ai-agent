from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.database.models import DurableTaskRow, InboxEventRow, OutboxEventRow


class DurableTaskRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        queue_name: str,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
        correlation_id: str,
        max_attempts: int = 5,
    ) -> DurableTaskRow:
        row, _ = self.create_once(
            tenant_id=tenant_id,
            actor_id=actor_id,
            queue_name=queue_name,
            action=action,
            payload=payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            max_attempts=max_attempts,
        )
        return row

    def create_once(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        queue_name: str,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
        correlation_id: str,
        max_attempts: int = 5,
    ) -> tuple[DurableTaskRow, bool]:
        session = self.session_factory()
        try:
            row = DurableTaskRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                actor_id=actor_id,
                queue_name=queue_name,
                action=action,
                status="pending",
                payload=payload,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                max_attempts=max_attempts,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row, True
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(DurableTaskRow).where(
                    DurableTaskRow.tenant_id == tenant_id, DurableTaskRow.idempotency_key == idempotency_key
                )
            )
            if existing is None:
                raise
            return existing, False
        finally:
            session.close()

    def get(self, *, tenant_id: str, task_id: str) -> DurableTaskRow | None:
        with self.session_factory() as session:
            return session.scalar(
                select(DurableTaskRow).where(DurableTaskRow.tenant_id == tenant_id, DurableTaskRow.id == task_id)
            )

    def list(self, *, tenant_id: str, limit: int = 100) -> list[DurableTaskRow]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(DurableTaskRow)
                    .where(DurableTaskRow.tenant_id == tenant_id)
                    .order_by(DurableTaskRow.created_at.desc())
                    .limit(limit)
                )
            )

    def replay(self, *, tenant_id: str, task_id: str, actor_id: str) -> DurableTaskRow:
        original = self.get(tenant_id=tenant_id, task_id=task_id)
        if original is None:
            raise LookupError("task not found")
        if original.status not in {"failed", "dead_letter"}:
            raise ValueError("only failed or dead-letter tasks can be replayed")
        replay = self.create(
            tenant_id=tenant_id,
            actor_id=actor_id,
            queue_name=original.queue_name,
            action=original.action,
            payload=original.payload,
            idempotency_key=f"replay:{original.id}:{uuid4()}",
            correlation_id=original.correlation_id,
            max_attempts=original.max_attempts,
        )
        with self.session_factory.begin() as session:
            source = session.get(DurableTaskRow, original.id)
            target = session.get(DurableTaskRow, replay.id)
            if source is not None:
                source.replayed_to_task_id = replay.id
            if target is not None:
                target.replayed_from_task_id = original.id
        return self.get(tenant_id=tenant_id, task_id=replay.id) or replay

    def close(self, *, tenant_id: str, task_id: str, actor_id: str, reason: str) -> DurableTaskRow:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(DurableTaskRow).where(DurableTaskRow.tenant_id == tenant_id, DurableTaskRow.id == task_id)
            )
            if row is None:
                raise LookupError("task not found")
            if row.status not in {"failed", "dead_letter"}:
                raise ValueError("only failed or dead-letter tasks can be closed")
            row.status = "closed"
            row.closed_at = datetime.now(UTC)
            row.closed_by = actor_id
            row.close_reason = reason
        return self.get(tenant_id=tenant_id, task_id=task_id)

    def update_status(
        self,
        *,
        tenant_id: str,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(DurableTaskRow).where(DurableTaskRow.id == task_id, DurableTaskRow.tenant_id == tenant_id)
            )
            if row is None:
                return
            now = datetime.now(UTC)
            row.status = status
            row.updated_at = now
            if status == "running":
                row.started_at = now
                row.attempts += 1
            if status in {"succeeded", "failed", "dead_letter"}:
                row.finished_at = now
            row.result = result
            row.error_code = error_code
            row.error_message = error_message


class EventRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def accept_inbox(
        self, *, tenant_id: str, provider: str, external_event_id: str, event_type: str, payload: dict[str, Any]
    ) -> bool:
        try:
            with self.session_factory.begin() as session:
                session.add(
                    InboxEventRow(
                        id=str(uuid4()),
                        tenant_id=tenant_id,
                        provider=provider,
                        external_event_id=external_event_id,
                        event_type=event_type,
                        payload=payload,
                    )
                )
            return True
        except IntegrityError:
            return False

    def claim_pending(self, *, tenant_id: str, limit: int = 100, lease_seconds: int = 120) -> list[InboxEventRow]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            session.query(InboxEventRow).filter(
                InboxEventRow.tenant_id == tenant_id,
                InboxEventRow.status == "processing",
                InboxEventRow.lease_expires_at < now,
            ).update({"status": "retry_pending", "lease_expires_at": None})
            statement = (
                select(InboxEventRow)
                .where(
                    InboxEventRow.tenant_id == tenant_id,
                    InboxEventRow.status.in_(["received", "retry_pending"]),
                    or_(InboxEventRow.next_retry_at.is_(None), InboxEventRow.next_retry_at <= now),
                )
                .order_by(InboxEventRow.received_at)
                .limit(limit)
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list(session.scalars(statement))
            for row in rows:
                row.status = "processing"
                row.attempts = (row.attempts or 0) + 1
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                session.flush()
                session.expunge(row)
            return rows

    def mark_processed(self, *, tenant_id: str, event_id: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(InboxEventRow).where(InboxEventRow.id == event_id, InboxEventRow.tenant_id == tenant_id)
            )
            if row:
                row.status = "processed"
                row.processed_at = datetime.now(UTC)
                row.lease_expires_at = None
                row.error_code = None
                row.error_message = None

    def mark_retry(self, *, tenant_id: str, event_id: str, error: Exception, max_attempts: int = 5) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(InboxEventRow).where(InboxEventRow.id == event_id, InboxEventRow.tenant_id == tenant_id)
            )
            if row:
                row.error_code = type(error).__name__
                row.error_message = str(error)[:1000]
                row.lease_expires_at = None
                if row.attempts >= max_attempts:
                    row.status = "dead_letter"
                else:
                    row.status = "retry_pending"
                    row.next_retry_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**row.attempts))

    def mark_dead_letter(self, *, tenant_id: str, event_id: str, error_code: str, error_message: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(InboxEventRow).where(InboxEventRow.id == event_id, InboxEventRow.tenant_id == tenant_id)
            )
            if row:
                row.status = "dead_letter"
                row.lease_expires_at = None
                row.error_code = error_code
                row.error_message = error_message[:1000]

    def list_inbox_failures(self, *, tenant_id: str) -> list[InboxEventRow]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(InboxEventRow)
                    .where(
                        InboxEventRow.tenant_id == tenant_id,
                        InboxEventRow.status.in_(["retry_pending", "dead_letter"]),
                    )
                    .order_by(InboxEventRow.received_at.desc())
                )
            )

    def add_outbox(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
    ) -> str:
        event_id = str(uuid4())
        try:
            with self.session_factory.begin() as session:
                session.add(
                    OutboxEventRow(
                        id=event_id,
                        tenant_id=tenant_id,
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        event_type=event_type,
                        dedupe_key=dedupe_key or f"{event_type}:{aggregate_type}:{aggregate_id}",
                        payload=payload,
                        status="pending",
                        attempts=0,
                    )
                )
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(OutboxEventRow).where(
                        OutboxEventRow.tenant_id == tenant_id,
                        OutboxEventRow.dedupe_key == (dedupe_key or f"{event_type}:{aggregate_type}:{aggregate_id}"),
                    )
                )
                if existing is None:
                    raise
                return existing.id
        return event_id

    def claim_outbox(self, *, tenant_id: str, limit: int = 100, lease_seconds: int = 120) -> list[OutboxEventRow]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            session.query(OutboxEventRow).filter(
                OutboxEventRow.tenant_id == tenant_id,
                OutboxEventRow.status == "processing",
                OutboxEventRow.lease_expires_at < now,
            ).update({"status": "retry_pending", "lease_expires_at": None})
            statement = (
                select(OutboxEventRow)
                .where(
                    OutboxEventRow.tenant_id == tenant_id,
                    OutboxEventRow.status.in_(["pending", "retry_pending"]),
                    or_(OutboxEventRow.next_retry_at.is_(None), OutboxEventRow.next_retry_at <= now),
                )
                .order_by(OutboxEventRow.created_at, OutboxEventRow.id)
                .limit(limit)
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list(session.scalars(statement))
            for row in rows:
                row.status = "processing"
                row.attempts = (row.attempts or 0) + 1
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                session.flush()
                session.expunge(row)
            return rows

    def mark_outbox_processed(self, *, tenant_id: str, event_id: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.tenant_id == tenant_id,
                    OutboxEventRow.id == event_id,
                )
            )
            if row is not None:
                now = datetime.now(UTC)
                row.status = "processed"
                row.processed_at = now
                row.published_at = now
                row.lease_expires_at = None
                row.error_code = None
                row.error_message = None

    def mark_outbox_retry(self, *, tenant_id: str, event_id: str, error: Exception, max_attempts: int = 5) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.tenant_id == tenant_id,
                    OutboxEventRow.id == event_id,
                )
            )
            if row is None:
                return
            row.error_code = type(error).__name__
            row.error_message = str(error)[:1000]
            row.lease_expires_at = None
            if row.attempts >= max_attempts:
                row.status = "dead_letter"
                row.next_retry_at = None
            else:
                row.status = "retry_pending"
                row.next_retry_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**row.attempts))
