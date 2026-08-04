from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from application.celery_app import execute_action_task
from infrastructure.repositories.tasks import DurableTaskRepository


@dataclass
class DurableTaskRecord:
    task_id: str
    queue_name: str
    action: str
    status: str
    created_at: float
    updated_at: float
    request_id: str | None
    result: Any | None = None
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 5
    replayed_from_task_id: str | None = None
    replayed_to_task_id: str | None = None
    closed_by: str | None = None
    close_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class CeleryTaskQueue:
    """Persistent task facade: PostgreSQL is source of truth, Redis/Celery performs delivery."""

    def __init__(self, repository: DurableTaskRepository) -> None:
        self.repository = repository

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def enqueue_action(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        queue_name: str,
        action: str,
        payload: dict[str, Any],
        request_id: str | None,
        idempotency_key: str | None = None,
    ) -> DurableTaskRecord:
        correlation_id = request_id or str(uuid4())
        row = self.repository.create(
            tenant_id=tenant_id,
            actor_id=actor_id,
            queue_name=queue_name,
            action=action,
            payload=payload,
            idempotency_key=idempotency_key or str(uuid4()),
            correlation_id=correlation_id,
        )
        if row.status == "pending":
            execute_action_task.apply_async(args=[row.id, tenant_id, action, payload], queue=queue_name, task_id=row.id)
        return self._to_record(row)

    def enqueue(self, **_: Any) -> DurableTaskRecord:
        raise RuntimeError("Celery backend requires enqueue_action with serializable payload")

    def get(self, task_id: str, *, tenant_id: str = "default") -> DurableTaskRecord | None:
        row = self.repository.get(tenant_id=tenant_id, task_id=task_id)
        return None if row is None else self._to_record(row)

    def list_tasks(
        self, *, tenant_id: str = "default", limit: int = 50, status: Any | None = None
    ) -> list[DurableTaskRecord]:
        records = [self._to_record(row) for row in self.repository.list(tenant_id=tenant_id, limit=limit)]
        if status is not None:
            expected = getattr(status, "value", status)
            records = [record for record in records if record.status == expected]
        return records

    def queue_lengths(self) -> dict[str, int]:
        return {}

    @staticmethod
    def _timestamp(value: datetime | None) -> float | None:
        return None if value is None else value.timestamp()

    def _to_record(self, row: Any) -> DurableTaskRecord:
        return DurableTaskRecord(
            task_id=row.id,
            queue_name=row.queue_name,
            action=row.action,
            status=row.status,
            created_at=self._timestamp(row.created_at) or time.time(),
            updated_at=self._timestamp(row.updated_at) or time.time(),
            request_id=row.correlation_id,
            result=row.result,
            error=row.error_message,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            replayed_from_task_id=row.replayed_from_task_id,
            replayed_to_task_id=row.replayed_to_task_id,
            closed_by=row.closed_by,
            close_reason=row.close_reason,
        )
