from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from domain.customer_service import CustomerServiceCase, utc_now
from infrastructure.database.models import CustomerCaseEventRow, CustomerCaseRow, OutboxEventRow, SOPPlaybookRow

TRANSITIONS = {
    "open": {"acknowledged", "in_progress", "ready_to_send", "awaiting_approval", "policy_blocked", "dismissed"},
    "acknowledged": {"in_progress", "ready_to_send", "sent", "dismissed"},
    "in_progress": {"awaiting_approval", "ready_to_send", "policy_blocked", "sent", "dismissed"},
    "awaiting_approval": {"ready_to_send", "in_progress", "dismissed"},
    "ready_to_send": {"acknowledged", "in_progress", "sent", "dismissed"},
    "policy_blocked": {"in_progress", "dismissed"},
    "sent": {"resolved", "reopened"},
    "resolved": {"reopened"},
    "reopened": {"acknowledged", "in_progress", "awaiting_approval", "ready_to_send", "dismissed"},
    "dismissed": set(),
}


class CustomerCaseRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, case: CustomerServiceCase, *, actor_id: str) -> CustomerServiceCase:
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(CustomerCaseRow).where(
                    CustomerCaseRow.tenant_id == case.tenant_id,
                    CustomerCaseRow.source == case.source,
                    CustomerCaseRow.external_case_id == case.external_case_id,
                )
            )
            if existing is not None:
                return self._domain(existing)
            row = CustomerCaseRow(
                id=case.id,
                tenant_id=case.tenant_id,
                external_case_id=case.external_case_id,
                marketplace_id=case.marketplace_id,
                external_order_id=case.external_order_id,
                seller_sku=case.seller_sku,
                asin=case.asin,
                language=case.language,
                intent=case.intent,
                sentiment=case.sentiment,
                urgency=case.urgency,
                severity=case.severity,
                risk_level=case.risk_level,
                priority_score=case.priority_score,
                customer_message=case.customer_message,
                status=case.status,
                assignee_id=case.assignee_id,
                due_at=case.due_at,
                acknowledged_at=case.acknowledged_at,
                started_at=case.started_at,
                sent_at=case.sent_at,
                resolved_at=case.resolved_at,
                draft_reply=case.draft_reply,
                approved_reply=case.approved_reply,
                actual_reply=case.actual_reply,
                confidence=case.confidence,
                evidence=case.evidence,
                missing_data=list(case.missing_data),
                policy_checks=case.policy_checks,
                playbook_key=case.playbook_key,
                playbook_version=case.playbook_version,
                approval_request_id=case.approval_request_id,
                resolution_code=case.resolution_code,
                root_cause_code=case.root_cause_code,
                source=case.source,
                verified_amazon_data=case.verified_amazon_data,
                created_at=case.created_at,
                updated_at=case.updated_at,
            )
            session.add(row)
            session.flush()
            self._event(
                session,
                case.tenant_id,
                case.id,
                "case_created",
                actor_id,
                {"status": case.status, "intent": case.intent},
            )
            self._outbox(
                session,
                tenant_id=case.tenant_id,
                case_id=case.id,
                event_type="case.created",
                payload={
                    "status": case.status,
                    "intent": case.intent,
                    "marketplace_id": case.marketplace_id,
                    "seller_sku": case.seller_sku,
                    "source": case.source,
                    "verified_amazon_data": bool(case.verified_amazon_data),
                },
            )
            session.flush()
            return self._domain(row)

    def get(self, *, tenant_id: str, case_id: str) -> CustomerServiceCase | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(CustomerCaseRow).where(CustomerCaseRow.tenant_id == tenant_id, CustomerCaseRow.id == case_id)
            )
        return self._domain(row) if row else None

    def list(
        self, *, tenant_id: str, status: str | None = None, intent: str | None = None
    ) -> list[CustomerServiceCase]:
        statement = select(CustomerCaseRow).where(CustomerCaseRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(CustomerCaseRow.status == status)
        if intent:
            statement = statement.where(CustomerCaseRow.intent == intent)
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    statement.order_by(CustomerCaseRow.priority_score.desc(), CustomerCaseRow.created_at.desc())
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
        intent: str | None = None,
    ) -> tuple[int, list[CustomerServiceCase]]:
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be positive")
        statement = select(CustomerCaseRow).where(CustomerCaseRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(CustomerCaseRow.status == status)
        if intent:
            statement = statement.where(CustomerCaseRow.intent == intent)
        ordered = statement.order_by(
            CustomerCaseRow.priority_score.desc(),
            CustomerCaseRow.created_at.desc(),
            CustomerCaseRow.id,
        )
        with self.session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            rows = list(session.scalars(ordered.offset((page - 1) * page_size).limit(page_size)))
        return total, [self._domain(row) for row in rows]

    def transition(
        self, *, tenant_id: str, case_id: str, target_status: str, actor_id: str, note: str = ""
    ) -> CustomerServiceCase:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(CustomerCaseRow).where(CustomerCaseRow.tenant_id == tenant_id, CustomerCaseRow.id == case_id)
            )
            if row is None:
                raise LookupError("customer case not found")
            if target_status not in TRANSITIONS.get(row.status, set()):
                raise ValueError(f"invalid customer case transition: {row.status} -> {target_status}")
            previous = row.status
            row.status = target_status
            row.updated_at = utc_now()
            if target_status == "acknowledged":
                row.assignee_id = actor_id
                row.acknowledged_at = row.acknowledged_at or row.updated_at
            elif target_status == "in_progress":
                row.assignee_id = row.assignee_id or actor_id
                row.started_at = row.started_at or row.updated_at
            self._event(
                session,
                tenant_id,
                case_id,
                "status_changed",
                actor_id,
                {"from": previous, "to": target_status, "note": note},
            )
            session.flush()
            return self._domain(row)

    def attach_approval(
        self, *, tenant_id: str, case_id: str, approval_request_id: str, actor_id: str
    ) -> CustomerServiceCase:
        with self.session_factory.begin() as session:
            row = self._locked(session, tenant_id, case_id)
            row.approval_request_id = approval_request_id
            row.status = "awaiting_approval"
            row.updated_at = utc_now()
            self._event(
                session,
                tenant_id,
                case_id,
                "approval_requested",
                actor_id,
                {"approval_request_id": approval_request_id},
            )
            session.flush()
            return self._domain(row)

    def decide_approval(
        self, *, tenant_id: str, case_id: str, approved: bool, actor_id: str, reason: str
    ) -> CustomerServiceCase:
        with self.session_factory.begin() as session:
            row = self._locked(session, tenant_id, case_id)
            if row.status != "awaiting_approval":
                return self._domain(row)
            row.status = "ready_to_send" if approved else "in_progress"
            row.approved_reply = row.draft_reply if approved else None
            row.updated_at = utc_now()
            self._event(
                session, tenant_id, case_id, "approval_decided", actor_id, {"approved": approved, "reason": reason}
            )
            session.flush()
            return self._domain(row)

    def mark_sent(self, *, tenant_id: str, case_id: str, reply: str, actor_id: str, note: str) -> CustomerServiceCase:
        with self.session_factory.begin() as session:
            row = self._locked(session, tenant_id, case_id)
            if (
                row.status not in {"ready_to_send", "acknowledged", "in_progress"}
                or row.policy_checks.get("decision") == "block"
            ):
                raise ValueError("customer reply is not eligible for sending")
            if row.approval_request_id and row.approved_reply is not None and reply != row.approved_reply:
                raise ValueError("approved customer reply changed and requires a new approval")
            row.actual_reply = reply
            row.sent_at = utc_now()
            row.updated_at = row.sent_at
            row.status = "sent"
            self._event(
                session, tenant_id, case_id, "reply_sent_mock", actor_id, {"note": note, "reply_sha256": _sha256(reply)}
            )
            session.flush()
            return self._domain(row)

    def resolve(
        self, *, tenant_id: str, case_id: str, resolution_code: str, root_cause_code: str, actor_id: str, note: str
    ) -> CustomerServiceCase:
        with self.session_factory.begin() as session:
            row = self._locked(session, tenant_id, case_id)
            if row.status != "sent":
                raise ValueError("only sent customer cases can be resolved")
            row.status = "resolved"
            row.resolution_code = resolution_code
            row.root_cause_code = root_cause_code
            row.resolved_at = utc_now()
            row.updated_at = row.resolved_at
            self._event(
                session,
                tenant_id,
                case_id,
                "case_resolved",
                actor_id,
                {"resolution_code": resolution_code, "root_cause_code": root_cause_code, "note": note},
            )
            event_payload = {
                "resolution_code": resolution_code,
                "root_cause_code": root_cause_code,
                "seller_sku": row.seller_sku,
                "source": row.source,
                "verified_amazon_data": bool(row.verified_amazon_data),
            }
            lifecycle_key = row.resolved_at.isoformat()
            self._outbox(
                session,
                tenant_id=tenant_id,
                case_id=case_id,
                event_type="case.resolved",
                payload=event_payload,
                lifecycle_key=lifecycle_key,
            )
            self._outbox(
                session,
                tenant_id=tenant_id,
                case_id=case_id,
                event_type="case.root_cause_confirmed",
                payload=event_payload,
                lifecycle_key=lifecycle_key,
            )
            session.flush()
            return self._domain(row)

    def timeline(self, *, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(CustomerCaseEventRow)
                    .where(
                        CustomerCaseEventRow.tenant_id == tenant_id,
                        CustomerCaseEventRow.case_id == case_id,
                    )
                    .order_by(CustomerCaseEventRow.created_at)
                )
            )
        return [
            {
                "event_type": row.event_type,
                "actor_id": row.actor_id,
                "payload": row.payload,
                "created_at": _utc(row.created_at).isoformat(),
            }
            for row in rows
        ]

    @staticmethod
    def _locked(session: Session, tenant_id: str, case_id: str) -> CustomerCaseRow:
        row = session.scalar(
            select(CustomerCaseRow).where(CustomerCaseRow.tenant_id == tenant_id, CustomerCaseRow.id == case_id)
        )
        if row is None:
            raise LookupError("customer case not found")
        return row

    @staticmethod
    def _event(
        session: Session, tenant_id: str, case_id: str, event_type: str, actor_id: str, payload: dict[str, Any]
    ) -> None:
        session.add(
            CustomerCaseEventRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                case_id=case_id,
                event_type=event_type,
                actor_id=actor_id,
                payload=payload,
                created_at=utc_now(),
            )
        )

    @staticmethod
    def _outbox(
        session: Session,
        *,
        tenant_id: str,
        case_id: str,
        event_type: str,
        payload: dict[str, Any],
        lifecycle_key: str | None = None,
    ) -> None:
        session.add(
            OutboxEventRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                aggregate_type="customer_case",
                aggregate_id=case_id,
                event_type=event_type,
                dedupe_key=f"{event_type}:{case_id}" + (f":{lifecycle_key}" if lifecycle_key else ""),
                payload=payload,
                status="pending",
                attempts=0,
            )
        )

    @staticmethod
    def _domain(row: CustomerCaseRow) -> CustomerServiceCase:
        return CustomerServiceCase(
            id=row.id,
            tenant_id=row.tenant_id,
            external_case_id=row.external_case_id,
            marketplace_id=row.marketplace_id,
            external_order_id=row.external_order_id,
            seller_sku=row.seller_sku,
            asin=row.asin,
            language=row.language,
            intent=row.intent,
            sentiment=row.sentiment,
            urgency=row.urgency,
            severity=row.severity,
            risk_level=row.risk_level,
            priority_score=Decimal(str(row.priority_score)),
            customer_message=row.customer_message,
            status=row.status,
            assignee_id=row.assignee_id,
            due_at=_optional_utc(row.due_at),
            acknowledged_at=_optional_utc(row.acknowledged_at),
            started_at=_optional_utc(row.started_at),
            sent_at=_optional_utc(row.sent_at),
            resolved_at=_optional_utc(row.resolved_at),
            draft_reply=row.draft_reply,
            approved_reply=row.approved_reply,
            actual_reply=row.actual_reply,
            confidence=Decimal(str(row.confidence)),
            evidence=row.evidence or {},
            missing_data=tuple(row.missing_data or []),
            policy_checks=row.policy_checks or {},
            playbook_key=row.playbook_key,
            playbook_version=row.playbook_version,
            approval_request_id=row.approval_request_id,
            resolution_code=row.resolution_code,
            root_cause_code=row.root_cause_code,
            source=row.source,
            verified_amazon_data=row.verified_amazon_data,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )


class SOPPlaybookRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def ensure(
        self, *, tenant_id: str, playbook_key: str, version: str, config: dict[str, Any], actor_id: str
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(SOPPlaybookRow).where(
                    SOPPlaybookRow.tenant_id == tenant_id,
                    SOPPlaybookRow.playbook_key == playbook_key,
                    SOPPlaybookRow.version == version,
                )
            )
            if row is None:
                row = SOPPlaybookRow(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    playbook_key=playbook_key,
                    version=version,
                    active=True,
                    config=config,
                    created_by=actor_id,
                    created_at=utc_now(),
                )
                session.add(row)
            return dict(row.config or config)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)


def _sha256(value: str) -> str:
    from hashlib import sha256

    return sha256(value.encode("utf-8")).hexdigest()
