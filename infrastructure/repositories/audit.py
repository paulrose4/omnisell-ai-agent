from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.database.models import AuditEventRow
from infrastructure.database.session import tenant_context
from safety.audit import AuditRecord, sanitize_audit_details


class SQLAuditLogger:
    """Database-backed audit logger compatible with the existing AuditLogger interface."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def log(self, event_type: str, action: str, status: str, details: dict[str, Any] | None = None, actor: str = "system", risk_level: str = "low") -> AuditRecord:
        details = details or {}
        tenant_id = str(details.get("tenant_id") or "system")
        details = sanitize_audit_details(details)
        now = datetime.now(UTC).replace(microsecond=0)
        record = AuditRecord(timestamp=now.isoformat().replace("+00:00", "Z"), event_type=event_type, actor=actor, action=action, risk_level=risk_level, status=status, details=details)
        with tenant_context(tenant_id):
            with self.session_factory.begin() as session:
                session.add(AuditEventRow(id=str(uuid4()), tenant_id=tenant_id, event_type=event_type, actor=actor, action=action, risk_level=risk_level, status=status, details=details, created_at=now))
        return record

    def log_operation(self, action: str, status: str, details: dict[str, Any] | None = None, actor: str = "agent", risk_level: str = "low") -> AuditRecord:
        return self.log("operation", action, status, details, actor, risk_level)

    def log_approval(self, action: str, status: str, details: dict[str, Any] | None = None, actor: str = "hitl", risk_level: str = "high") -> AuditRecord:
        return self.log("approval", action, status, details, actor, risk_level)

    def log_sandbox(self, action: str, status: str, details: dict[str, Any] | None = None, actor: str = "sandbox", risk_level: str = "medium") -> AuditRecord:
        return self.log("sandbox", action, status, details, actor, risk_level)

    def query(self, event_type: str | None = None, status: str | None = None, risk_level: str | None = None, action: str | None = None, limit: int | None = None, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if not tenant_id:
            raise ValueError("tenant_id is required for persistent audit queries")
        statement = select(AuditEventRow).order_by(AuditEventRow.created_at.desc())
        statement = statement.where(AuditEventRow.tenant_id == tenant_id)
        if event_type:
            statement = statement.where(AuditEventRow.event_type == event_type)
        if status:
            statement = statement.where(AuditEventRow.status == status)
        if risk_level:
            statement = statement.where(AuditEventRow.risk_level == risk_level)
        if action:
            statement = statement.where(AuditEventRow.action == action)
        if limit:
            statement = statement.limit(limit)
        with self.session_factory() as session:
            rows = list(session.scalars(statement))
        return [
            {"timestamp": row.created_at.isoformat().replace("+00:00", "Z"), "event_type": row.event_type, "actor": row.actor, "action": row.action, "risk_level": row.risk_level, "status": row.status, "details": row.details}
            for row in reversed(rows)
        ]
