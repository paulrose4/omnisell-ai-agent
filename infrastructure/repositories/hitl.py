from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.database.models import ApprovalDecisionRow, ApprovalRequestRow
from safety.hitl import ApprovalRejectedError, ApprovalRequest, ApprovalStatus, HITLManager, RiskLevel
from safety.policy_engine import canonical_action_hash, permanently_prohibited_intent


class SQLHITLManager(HITLManager):
    """Persistent HITL manager with self-approval prevention and immutable action hashes."""

    def __init__(self, session_factory: sessionmaker[Session], audit_logger: Any) -> None:
        super().__init__(audit_logger=audit_logger)
        self.session_factory = session_factory

    def submit_request(self, action: str, payload: dict[str, Any] | None = None, requester: str = "agent", risk_level: RiskLevel | str | None = None, reason: str | None = None) -> ApprovalRequest:
        payload = _json_safe(payload or {})
        self._raise_if_permanently_prohibited(action, payload, actor=requester)
        assessed_level, assessed_reason = self.assess_risk(action, payload)
        if risk_level is not None:
            assessed_level = risk_level if isinstance(risk_level, RiskLevel) else RiskLevel(risk_level)
        status = ApprovalStatus.AUTO_APPROVED if assessed_level in {RiskLevel.LOW, RiskLevel.MEDIUM} else ApprovalStatus.PENDING
        timeout_hours = 1 if assessed_level == RiskLevel.CRITICAL else 24
        tenant_id = str(payload.get("tenant_id") or "default")
        resource_id = payload.get("resource_id") or payload.get("product_id") or payload.get("order_id")
        supplied_hash = payload.get("action_hash")
        hash_arguments = {key: value for key, value in payload.items() if key != "action_hash"}
        action_hash = str(supplied_hash) if supplied_hash else canonical_action_hash(action=action, tenant_id=tenant_id, resource_id=str(resource_id) if resource_id else None, arguments=hash_arguments)
        request = ApprovalRequest(
            request_id=str(uuid4()), action=action, risk_level=assessed_level, payload={**payload, "action_hash": action_hash},
            requester=requester, status=status, reason=reason or assessed_reason,
            requires_second_verification=assessed_level == RiskLevel.CRITICAL,
            expires_at=datetime.now(UTC) + timedelta(hours=timeout_hours),
        )
        with self.session_factory.begin() as session:
            session.add(ApprovalRequestRow(
                id=request.request_id, tenant_id=tenant_id, actor_id=requester, action=action, action_hash=action_hash,
                risk_level=assessed_level.value, before_snapshot=payload.get("before_snapshot", {}), proposed_changes=payload,
                status=status.value, expires_at=request.expires_at, created_at=request.created_at,
            ))
        self.audit_logger.log_approval(action, "pending" if status == ApprovalStatus.PENDING else "auto_approved", {**request.to_dict(), "tenant_id": tenant_id}, actor=requester, risk_level=assessed_level.value)
        return request

    def approve(self, request_id: str, approver: str, reason: str = "", tenant_id: str | None = None) -> ApprovalRequest:
        tenant_id = _required_tenant_id(tenant_id)
        expired_request: ApprovalRequest | None = None
        blocked_request: ApprovalRequest | None = None
        blocked_intent: str | None = None
        audit_status = ApprovalStatus.APPROVED.value
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(select(ApprovalRequestRow).where(
                ApprovalRequestRow.id == request_id,
                ApprovalRequestRow.tenant_id == tenant_id,
            ).with_for_update())
            if row is None or row.status not in {
                ApprovalStatus.PENDING.value,
                ApprovalStatus.AWAITING_SECOND_APPROVAL.value,
            }:
                raise ApprovalRejectedError("Approval request is not pending.")
            blocked_intent = permanently_prohibited_intent(row.action, row.proposed_changes)
            if blocked_intent is not None:
                row.status = ApprovalStatus.REJECTED.value
                row.approver_id = "policy"
                row.decided_at = now
                row.decision_reason = "permanently prohibited actions cannot be approved."
                self._append_decision(
                    session=session, row=row, stage="policy_block", decision="rejected",
                    actor_id="policy", reason=row.decision_reason, created_at=now,
                )
                blocked_request = self._from_row(row)
            elif row.expires_at is not None and _utc(row.expires_at) <= now:
                row.status = ApprovalStatus.EXPIRED.value
                row.decided_at = now
                row.decision_reason = "Approval timed out."
                self._append_decision(
                    session=session, row=row, stage="expiration", decision="expired",
                    actor_id="hitl", reason=row.decision_reason, created_at=now,
                )
                expired_request = self._from_row(row)
            elif row.actor_id == approver:
                raise ApprovalRejectedError("Requester cannot approve their own action.")
            elif row.risk_level == RiskLevel.CRITICAL.value and row.status == ApprovalStatus.PENDING.value:
                row.status = ApprovalStatus.AWAITING_SECOND_APPROVAL.value
                row.first_approver_id = approver
                row.first_approved_at = now
                row.first_approval_reason = reason
                self._append_decision(
                    session=session, row=row, stage="first_approval", decision="approved",
                    actor_id=approver, reason=reason, created_at=now,
                )
                audit_status = ApprovalStatus.AWAITING_SECOND_APPROVAL.value
                request = self._from_row(row)
            elif row.risk_level == RiskLevel.CRITICAL.value:
                if row.first_approver_id in {None, approver}:
                    raise ApprovalRejectedError("Critical approval requires two independent approvers.")
                row.status = ApprovalStatus.APPROVED.value
                row.approver_id = approver
                row.decided_at = now
                row.decision_reason = reason
                self._append_decision(
                    session=session, row=row, stage="second_approval", decision="approved",
                    actor_id=approver, reason=reason, created_at=now,
                )
                request = self._from_row(row)
            elif expired_request is None and blocked_request is None:
                row.status = ApprovalStatus.APPROVED.value
                row.approver_id = approver
                row.decided_at = now
                row.decision_reason = reason
                self._append_decision(
                    session=session, row=row, stage="approval", decision="approved",
                    actor_id=approver, reason=reason, created_at=now,
                )
                request = self._from_row(row)
        if blocked_request is not None and blocked_intent is not None:
            self._log_permanently_prohibited(
                blocked_request.action,
                blocked_request.payload,
                actor=approver,
                blocked_intent=blocked_intent,
            )
            raise ApprovalRejectedError("permanently prohibited action cannot be approved.")
        if expired_request is not None:
            self.audit_logger.log_approval(
                expired_request.action,
                "expired",
                expired_request.to_dict(),
                actor="hitl",
                risk_level=expired_request.risk_level.value,
            )
            raise ApprovalRejectedError(f"Approval request expired: {request_id}")
        self.audit_logger.log_approval(request.action, audit_status, request.to_dict(), actor=approver, risk_level=request.risk_level.value)
        return request

    def reject(self, request_id: str, approver: str, reason: str = "", tenant_id: str | None = None) -> ApprovalRequest:
        tenant_id = _required_tenant_id(tenant_id)
        with self.session_factory.begin() as session:
            row = session.scalar(select(ApprovalRequestRow).where(
                ApprovalRequestRow.id == request_id,
                ApprovalRequestRow.tenant_id == tenant_id,
            ))
            if row is None or row.status not in {
                ApprovalStatus.PENDING.value,
                ApprovalStatus.AWAITING_SECOND_APPROVAL.value,
            }:
                raise ApprovalRejectedError("Approval request is not pending.")
            now = datetime.now(UTC)
            row.status = ApprovalStatus.REJECTED.value
            row.approver_id = approver
            row.decided_at = now
            row.decision_reason = reason
            self._append_decision(
                session=session, row=row, stage="rejection", decision="rejected",
                actor_id=approver, reason=reason, created_at=now,
            )
            request = self._from_row(row)
        self.audit_logger.log_approval(request.action, "rejected", request.to_dict(), actor=approver, risk_level=request.risk_level.value)
        return request

    def get_pending(self, tenant_id: str | None = None) -> list[ApprovalRequest]:
        tenant_id = _required_tenant_id(tenant_id)
        self.expire_overdue(tenant_id=tenant_id)
        statement = select(ApprovalRequestRow).where(
            ApprovalRequestRow.status.in_((
                ApprovalStatus.PENDING.value,
                ApprovalStatus.AWAITING_SECOND_APPROVAL.value,
            )),
            ApprovalRequestRow.tenant_id == tenant_id,
        )
        statement = statement.order_by(ApprovalRequestRow.created_at.desc(), ApprovalRequestRow.id.desc())
        with self.session_factory() as session:
            return [self._from_row(row) for row in session.scalars(statement)]

    def get_request(self, request_id: str, tenant_id: str | None = None) -> ApprovalRequest | None:
        tenant_id = _required_tenant_id(tenant_id)
        self.expire_overdue(tenant_id=tenant_id)
        statement = select(ApprovalRequestRow).where(
            ApprovalRequestRow.id == request_id,
            ApprovalRequestRow.tenant_id == tenant_id,
        )
        with self.session_factory() as session:
            row = session.scalar(statement)
            return None if row is None else self._from_row(row)

    def expire_overdue(self, tenant_id: str | None = None) -> None:
        tenant_id = _required_tenant_id(tenant_id)
        now = datetime.now(UTC)
        statement = select(ApprovalRequestRow).where(
            ApprovalRequestRow.status.in_((
                ApprovalStatus.PENDING.value,
                ApprovalStatus.AWAITING_SECOND_APPROVAL.value,
            )),
            ApprovalRequestRow.expires_at <= now,
            ApprovalRequestRow.tenant_id == tenant_id,
        )
        with self.session_factory.begin() as session:
            rows = list(session.scalars(statement))
            for row in rows:
                row.status = ApprovalStatus.EXPIRED.value
                row.decided_at = now
                row.decision_reason = "Approval timed out."
                self._append_decision(
                    session=session, row=row, stage="expiration", decision="expired",
                    actor_id="hitl", reason=row.decision_reason, created_at=now,
                )

    @staticmethod
    def _from_row(row: ApprovalRequestRow) -> ApprovalRequest:
        requires_second_verification = row.risk_level == RiskLevel.CRITICAL.value
        second_approver_id = (
            row.approver_id
            if requires_second_verification and row.status == ApprovalStatus.APPROVED.value
            else None
        )
        return ApprovalRequest(
            request_id=row.id, action=row.action, risk_level=RiskLevel(row.risk_level), payload={**row.proposed_changes, "action_hash": row.action_hash},
            requester=row.actor_id, status=ApprovalStatus(row.status), requires_second_verification=requires_second_verification,
            created_at=_utc(row.created_at), expires_at=_utc(row.expires_at) if row.expires_at else datetime.now(UTC),
            decided_at=_utc(row.decided_at) if row.decided_at else None, decided_by=row.approver_id,
            decision_reason=row.decision_reason,
            first_approver_id=row.first_approver_id,
            first_approved_at=_utc(row.first_approved_at) if row.first_approved_at else None,
            first_approval_reason=row.first_approval_reason,
            second_approver_id=second_approver_id,
            second_approved_at=_utc(row.decided_at) if second_approver_id and row.decided_at else None,
            second_approval_reason=row.decision_reason if second_approver_id else None,
        )

    @staticmethod
    def _append_decision(
        *,
        session: Session,
        row: ApprovalRequestRow,
        stage: str,
        decision: str,
        actor_id: str,
        reason: str,
        created_at: datetime,
    ) -> None:
        sequence = int(session.scalar(
            select(func.coalesce(func.max(ApprovalDecisionRow.sequence), 0)).where(
                ApprovalDecisionRow.approval_request_id == row.id,
                ApprovalDecisionRow.tenant_id == row.tenant_id,
            )
        ) or 0) + 1
        session.add(ApprovalDecisionRow(
            id=str(uuid4()), tenant_id=row.tenant_id, approval_request_id=row.id,
            sequence=sequence, stage=stage, decision=decision, actor_id=actor_id,
            reason=reason, created_at=created_at,
        ))


def _json_safe(value: Any) -> Any:
    from datetime import datetime
    from decimal import Decimal
    from enum import Enum

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _required_tenant_id(tenant_id: str | None) -> str:
    if not tenant_id:
        raise ValueError("tenant_id is required for persistent approval operations")
    return tenant_id
