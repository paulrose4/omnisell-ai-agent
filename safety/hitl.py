from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from threading import RLock
from typing import Any

try:
    from .audit import AuditLogger
    from .refund_gate import RefundGate
    from .policy_engine import permanently_prohibited_intent
except ImportError:
    from audit import AuditLogger  # type: ignore[no-redef]
    from refund_gate import RefundGate  # type: ignore[no-redef]
    from policy_engine import permanently_prohibited_intent  # type: ignore[no-redef]


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(str, Enum):
    AUTO_APPROVED = "auto_approved"
    PENDING = "pending"
    AWAITING_SECOND_APPROVAL = "awaiting_second_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    request_id: str
    action: str
    risk_level: RiskLevel
    payload: dict[str, Any]
    requester: str = "agent"
    status: ApprovalStatus = ApprovalStatus.PENDING
    reason: str = ""
    requires_second_verification: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=24))
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    first_approver_id: str | None = None
    first_approved_at: datetime | None = None
    first_approval_reason: str | None = None
    second_approver_id: str | None = None
    second_approved_at: datetime | None = None
    second_approval_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable approval request."""
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        data["status"] = self.status.value
        for key in ("created_at", "expires_at", "decided_at", "first_approved_at", "second_approved_at"):
            if data[key] is not None:
                data[key] = data[key].isoformat().replace("+00:00", "Z")
        return data


class ApprovalRequiredError(RuntimeError):
    """Raised when a high-risk operation requires human approval."""

    def __init__(self, request: ApprovalRequest) -> None:
        self.request = request
        super().__init__(f"Approval required for {request.action}: {request.request_id}")


class ApprovalRejectedError(RuntimeError):
    """Raised when an operation is rejected or expires in HITL."""


class HITLManager:
    """Human-in-the-loop approval manager with risk-level policies."""

    def __init__(self, audit_logger: AuditLogger | None = None) -> None:
        self.audit_logger = audit_logger or AuditLogger()
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = RLock()

    def assess_risk(self, action: str, payload: dict[str, Any] | None = None) -> tuple[RiskLevel, str]:
        """Assess risk level for an action and payload."""
        payload = payload or {}
        action_lower = action.lower()
        refund_risk, refund_reason = RefundGate(self.audit_logger).check_refund_risk(action, payload)
        if refund_risk:
            return RiskLevel.CRITICAL, f"{refund_reason}; refund operations are permanently prohibited."

        if payload.get("risk_level") in {item.value for item in RiskLevel}:
            level = RiskLevel(str(payload["risk_level"]))
            return level, f"Explicit risk_level={level.value}."

        if payload.get("requires_second_verification") is True:
            return RiskLevel.CRITICAL, "Operation explicitly requires second verification."

        price_change = self._extract_price_change(payload)
        if price_change is not None:
            abs_change = abs(price_change)
            if abs_change > Decimal("0.20"):
                return RiskLevel.CRITICAL, "Price change is greater than 20%."
            if abs_change > Decimal("0.10"):
                return RiskLevel.HIGH, "Price change is greater than 10%."
            if abs_change >= Decimal("0.05"):
                return RiskLevel.MEDIUM, "Price change is between 5% and 10%."
            return RiskLevel.LOW, "Price change is below 5%."

        if payload.get("bulk") is True or int(payload.get("item_count", 1) or 1) >= 10:
            return RiskLevel.HIGH, "Bulk operation requires human review."

        if any(keyword in action_lower for keyword in ("delete", "remove", "deactivate", "offline", "下架", "删除")):
            return RiskLevel.HIGH, "Deletion or offline operation requires human review."

        if any(keyword in action_lower for keyword in ("sandbox", "code", "execute")):
            return RiskLevel.MEDIUM, "Code execution is medium risk and requires notification."

        if any(keyword in action_lower for keyword in ("update", "create", "approve_return", "refund")):
            return RiskLevel.MEDIUM, "Business write operation requires notification."

        return RiskLevel.LOW, "Read-only or low-impact operation."

    def submit_request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        requester: str = "agent",
        risk_level: RiskLevel | str | None = None,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Submit an approval request and apply low/medium/high/critical policies."""
        payload = payload or {}
        self._raise_if_permanently_prohibited(action, payload, actor=requester)
        if risk_level is None:
            assessed_level, assessed_reason = self.assess_risk(action, payload)
        else:
            assessed_level = RiskLevel(risk_level)
            assessed_reason = reason or f"Explicit risk_level={assessed_level.value}."

        created_at = datetime.now(UTC)
        timeout_hours = 48 if assessed_level == RiskLevel.CRITICAL else 24
        status = ApprovalStatus.PENDING
        requires_second_verification = assessed_level == RiskLevel.CRITICAL
        if assessed_level in {RiskLevel.LOW, RiskLevel.MEDIUM}:
            status = ApprovalStatus.AUTO_APPROVED

        request = ApprovalRequest(
            request_id=f"APR-{uuid.uuid4().hex[:12].upper()}",
            action=action,
            risk_level=assessed_level,
            payload=payload,
            requester=requester,
            status=status,
            reason=reason or assessed_reason,
            requires_second_verification=requires_second_verification,
            created_at=created_at,
            expires_at=created_at + timedelta(hours=timeout_hours),
        )

        with self._lock:
            self._requests[request.request_id] = request

        if assessed_level == RiskLevel.LOW:
            audit_status = "auto_approved"
        elif assessed_level == RiskLevel.MEDIUM:
            audit_status = "auto_approved_notified"
        else:
            audit_status = "pending"

        self.audit_logger.log_approval(
            action=action,
            status=audit_status,
            details=request.to_dict(),
            actor=requester,
            risk_level=assessed_level.value,
        )
        return request

    def approve(
        self,
        request_id: str,
        approver: str,
        reason: str = "",
    ) -> ApprovalRequest:
        """Record one authenticated approval decision."""
        blocked_intent: str | None = None
        audit_status = "approved"
        with self._lock:
            request = self._get_pending_request(request_id)
            blocked_intent = permanently_prohibited_intent(request.action, request.payload)
            if blocked_intent is not None:
                request.status = ApprovalStatus.REJECTED
                request.decided_at = datetime.now(UTC)
                request.decided_by = "policy"
                request.decision_reason = "permanently prohibited actions cannot be approved."
            elif request.requester == approver:
                raise ApprovalRejectedError("Requester cannot approve their own action.")
            if blocked_intent is None:
                now = datetime.now(UTC)
                if request.requires_second_verification and request.status == ApprovalStatus.PENDING:
                    request.status = ApprovalStatus.AWAITING_SECOND_APPROVAL
                    request.first_approver_id = approver
                    request.first_approved_at = now
                    request.first_approval_reason = reason
                    audit_status = ApprovalStatus.AWAITING_SECOND_APPROVAL.value
                else:
                    if request.requires_second_verification and approver == request.first_approver_id:
                        raise ApprovalRejectedError("Critical approval requires two independent approvers.")
                    request.status = ApprovalStatus.APPROVED
                    request.decided_at = now
                    request.decided_by = approver
                    request.decision_reason = reason
                    if request.requires_second_verification:
                        request.second_approver_id = approver
                        request.second_approved_at = now
                        request.second_approval_reason = reason

        if blocked_intent is not None:
            self._log_permanently_prohibited(request.action, request.payload, actor=approver, blocked_intent=blocked_intent)
            raise ApprovalRejectedError("permanently prohibited action cannot be approved.")

        self.audit_logger.log_approval(request.action, audit_status, request.to_dict(), actor=approver, risk_level=request.risk_level.value)
        return request

    def reject(self, request_id: str, approver: str, reason: str = "") -> ApprovalRequest:
        """Reject a pending request."""
        with self._lock:
            request = self._get_pending_request(request_id)
            request.status = ApprovalStatus.REJECTED
            request.decided_at = datetime.now(UTC)
            request.decided_by = approver
            request.decision_reason = reason

        self.audit_logger.log_approval(request.action, "rejected", request.to_dict(), actor=approver, risk_level=request.risk_level.value)
        return request

    def get_pending(self) -> list[ApprovalRequest]:
        """Return non-expired pending approvals."""
        self.expire_overdue()
        with self._lock:
            return [
                request for request in self._requests.values()
                if request.status in {ApprovalStatus.PENDING, ApprovalStatus.AWAITING_SECOND_APPROVAL}
            ]

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Return an approval request by ID."""
        self.expire_overdue()
        with self._lock:
            return self._requests.get(request_id)

    def expire_overdue(self) -> None:
        """Reject pending approvals whose timeout has elapsed."""
        now = datetime.now(UTC)
        expired: list[ApprovalRequest] = []
        with self._lock:
            for request in self._requests.values():
                if request.status in {ApprovalStatus.PENDING, ApprovalStatus.AWAITING_SECOND_APPROVAL} and now >= request.expires_at:
                    request.status = ApprovalStatus.EXPIRED
                    request.decided_at = now
                    request.decision_reason = "Approval timed out."
                    expired.append(request)

        for request in expired:
            self.audit_logger.log_approval(request.action, "expired", request.to_dict(), actor="hitl", risk_level=request.risk_level.value)

    def _get_pending_request(self, request_id: str) -> ApprovalRequest:
        self.expire_overdue()
        request = self._requests.get(request_id)
        if request is None:
            raise KeyError(f"Approval request not found: {request_id}")
        if request.status == ApprovalStatus.EXPIRED:
            raise ApprovalRejectedError(f"Approval request expired: {request_id}")
        if request.status not in {ApprovalStatus.PENDING, ApprovalStatus.AWAITING_SECOND_APPROVAL}:
            raise ApprovalRejectedError(f"Approval request is not pending: {request.status.value}")
        return request

    def _raise_if_permanently_prohibited(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        actor: str,
    ) -> None:
        blocked_intent = permanently_prohibited_intent(action, payload)
        if blocked_intent is None:
            return
        self._log_permanently_prohibited(action, payload, actor=actor, blocked_intent=blocked_intent)
        raise ApprovalRejectedError("permanently prohibited action cannot enter HITL approval.")

    def _log_permanently_prohibited(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        actor: str,
        blocked_intent: str,
    ) -> None:
        self.audit_logger.log_approval(
            action,
            "blocked",
            {
                "blocked_intent": blocked_intent,
                "payload_keys": sorted(payload),
                "reason": "permanently_prohibited_action",
            },
            actor=actor,
            risk_level=RiskLevel.CRITICAL.value,
        )

    @staticmethod
    def _extract_price_change(payload: dict[str, Any]) -> Decimal | None:
        try:
            if "price_change_rate" in payload:
                return Decimal(str(payload["price_change_rate"]))
            if "change_rate" in payload:
                return Decimal(str(payload["change_rate"]))
            if "change_percent" in payload:
                return Decimal(str(payload["change_percent"])) / Decimal("100")
            old_price = payload.get("old_price") or payload.get("current_price")
            new_price = payload.get("new_price") or payload.get("price") or payload.get("recommended_price")
            if old_price is None or new_price is None:
                return None
            old = Decimal(str(old_price))
            new = Decimal(str(new_price))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if old <= 0:
            return None
        return (new - old) / old


if __name__ == "__main__":
    manager = HITLManager()
    request = manager.submit_request("update_product", {"current_price": 100, "price": 92})
    print(request.to_dict())
