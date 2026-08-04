from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from commerce.customer_messaging import BuyerMessage
from domain.customer_service import CustomerServiceCase, utc_now
from safety.hitl import RiskLevel
from safety.policy_engine import PolicyDecision, PolicyEngine


PLAYBOOKS: dict[str, dict[str, Any]] = {
    "where_is_my_order": {"version": "1.0", "risk_level": "low", "severity": "medium", "sla_minutes": 240, "required_evidence": ["order"]},
    "delivery_delay": {"version": "1.0", "risk_level": "low", "severity": "high", "sla_minutes": 120, "required_evidence": ["order"]},
    "cancel_request": {"version": "1.0", "risk_level": "critical", "severity": "critical", "sla_minutes": 60, "required_evidence": ["order"]},
    "refund_demand": {"version": "1.0", "risk_level": "critical", "severity": "critical", "sla_minutes": 60, "required_evidence": ["order"]},
    "damaged_item": {"version": "1.0", "risk_level": "high", "severity": "high", "sla_minutes": 120, "required_evidence": ["order", "damage_evidence"]},
    "wrong_item": {"version": "1.0", "risk_level": "high", "severity": "high", "sla_minutes": 120, "required_evidence": ["order", "item_evidence"]},
    "missing_accessory": {"version": "1.0", "risk_level": "medium", "severity": "medium", "sla_minutes": 240, "required_evidence": ["order", "package_contents"]},
    "usage_question": {"version": "1.0", "risk_level": "low", "severity": "low", "sla_minutes": 480, "required_evidence": ["product_knowledge"]},
    "invoice_request": {"version": "1.0", "risk_level": "medium", "severity": "medium", "sla_minutes": 240, "required_evidence": ["order"]},
    "negative_sentiment": {"version": "1.0", "risk_level": "high", "severity": "high", "sla_minutes": 60, "required_evidence": ["order", "message_history"]},
    "off_platform_contact": {"version": "1.0", "risk_level": "critical", "severity": "high", "sla_minutes": 120, "required_evidence": ["communication_policy"]},
    "review_incentive": {"version": "1.0", "risk_level": "critical", "severity": "critical", "sla_minutes": 60, "required_evidence": ["communication_policy"]},
}


class CustomerServiceWorkflow:
    def __init__(self, *, case_repository: Any, playbook_repository: Any, commerce_repository: Any, hitl_manager: Any, audit_logger: Any, policy_engine: PolicyEngine | None = None) -> None:
        self.cases = case_repository
        self.playbooks = playbook_repository
        self.commerce = commerce_repository
        self.hitl = hitl_manager
        self.audit = audit_logger
        self.policy = policy_engine or PolicyEngine()

    def intake(self, *, tenant_id: str, actor_id: str, message: BuyerMessage) -> CustomerServiceCase:
        playbook = dict(PLAYBOOKS[message.scenario_id])
        self.playbooks.ensure(
            tenant_id=tenant_id, playbook_key=message.scenario_id,
            version=str(playbook["version"]), config=playbook, actor_id=actor_id,
        )
        order = self.commerce.get_order_context(tenant_id=tenant_id, external_order_id=message.external_order_id)
        missing_data = [name for name in playbook["required_evidence"] if name == "order" and order is None]
        seller_sku = None
        if order and order.get("lines"):
            seller_sku = str(order["lines"][0].get("seller_sku") or "") or None
        confidence = Decimal("0.98") if not missing_data else Decimal("0.65")
        compliance_violations = self._compliance_violations(message.scenario_id, message.message)
        requested_action = self._requested_action(message.scenario_id)
        if requested_action in {"refund", "cancel_order", "issue_compensation"}:
            policy = self.policy.evaluate(action=requested_action, arguments={"customer_case": message.scenario_id}, confidence=float(confidence))
        else:
            policy = self.policy.evaluate(
                action="send_customer_reply",
                arguments={"case_risk_level": playbook["risk_level"], "compliance_violations": compliance_violations},
                confidence=float(confidence),
            )
        draft = self._draft(message.scenario_id, order)
        status = {
            PolicyDecision.ALLOW: "ready_to_send",
            PolicyDecision.REQUIRE_APPROVAL: "awaiting_approval",
            PolicyDecision.BLOCK: "policy_blocked",
        }[policy.decision]
        now = utc_now()
        case = self.cases.create(CustomerServiceCase(
            tenant_id=tenant_id, external_case_id=message.external_case_id,
            marketplace_id=message.marketplace_id, external_order_id=message.external_order_id,
            seller_sku=seller_sku, language=message.language, intent=message.scenario_id,
            sentiment="negative" if message.scenario_id in {"negative_sentiment", "refund_demand", "damaged_item", "wrong_item"} else "neutral",
            urgency="urgent" if playbook["severity"] in {"high", "critical"} else "normal",
            severity=str(playbook["severity"]), risk_level=str(playbook["risk_level"]),
            priority_score=self._priority(str(playbook["severity"]), str(playbook["risk_level"]), bool(missing_data)),
            customer_message=message.message, status=status, due_at=now + timedelta(minutes=int(playbook["sla_minutes"])),
            draft_reply=draft, confidence=confidence,
            evidence={
                "order": order,
                "message": {"received_at": message.received_at.isoformat(), "scenario_id": message.scenario_id},
                "data_source": "mock_buyer",
                "verified_amazon_data": False,
            },
            missing_data=tuple(missing_data),
            policy_checks={"decision": policy.decision.value, "risk_level": policy.risk_level, "reason": policy.reason, "violations": compliance_violations},
            playbook_key=message.scenario_id, playbook_version=str(playbook["version"]),
            source=message.source, verified_amazon_data=False, created_at=now, updated_at=now,
        ), actor_id=actor_id)
        if policy.decision == PolicyDecision.REQUIRE_APPROVAL:
            approval = self.hitl.submit_request(
                action="send_customer_reply",
                payload={
                    "tenant_id": tenant_id, "customer_case_id": case.id, "resource_id": case.id,
                    "risk_level": case.risk_level, "draft_reply": draft,
                    "before_snapshot": {"status": "open", "actual_reply": None},
                    "plan": {"action": "manual_send_customer_reply", "arguments": {"customer_case_id": case.id}},
                    "evidence": case.evidence,
                },
                requester=actor_id,
                risk_level=RiskLevel(case.risk_level),
                reason=policy.reason,
            )
            case = self.cases.attach_approval(
                tenant_id=tenant_id, case_id=case.id, approval_request_id=approval.request_id, actor_id=actor_id,
            )
        audit_action = "customer_policy_blocked" if policy.decision == PolicyDecision.BLOCK else "customer_case_created"
        self.audit.log_operation(
            action=audit_action, status="success",
            details={
                "tenant_id": tenant_id, "customer_case_id": case.id, "intent": case.intent,
                "policy_decision": policy.decision.value, "source": case.source,
                "verified_amazon_data": False,
            },
            actor=actor_id, risk_level=case.risk_level,
        )
        if audit_action != "customer_case_created":
            self.audit.log_operation(
                action="customer_case_created", status="success",
                details={"tenant_id": tenant_id, "customer_case_id": case.id, "intent": case.intent, "source": case.source, "verified_amazon_data": False},
                actor=actor_id, risk_level=case.risk_level,
            )
        return case

    def validate_reply_for_send(self, *, case: CustomerServiceCase, reply: str) -> None:
        violations = self._compliance_violations(case.intent, reply)
        policy = self.policy.evaluate(
            action="send_customer_reply",
            arguments={"case_risk_level": case.risk_level, "compliance_violations": violations},
            confidence=float(case.confidence),
        )
        if policy.decision == PolicyDecision.BLOCK:
            raise ValueError(policy.reason)
        if case.approval_request_id and case.approved_reply is not None and reply != case.approved_reply:
            raise ValueError("Approved customer reply changed after HITL approval and requires a new approval.")

    @staticmethod
    def _requested_action(intent: str) -> str:
        return {"refund_demand": "refund", "cancel_request": "cancel_order", "review_incentive": "issue_compensation"}.get(intent, "send_customer_reply")

    @staticmethod
    def _compliance_violations(intent: str, message: str) -> list[str]:
        violations: list[str] = []
        lowered = message.lower()
        if intent == "off_platform_contact" or "whatsapp" in lowered:
            violations.append("off_platform_contact")
        if intent == "review_incentive" or ("review" in lowered and "refund" in lowered):
            violations.append("review_incentive")
        if "http://" in lowered or "https://" in lowered:
            violations.append("external_link")
        if "guarantee compensation" in lowered or "guaranteed refund" in lowered:
            violations.append("unauthorized_compensation_promise")
        return violations

    @staticmethod
    def _priority(severity: str, risk_level: str, missing_data: bool) -> Decimal:
        rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        value = Decimal(rank[severity] * 1000 + rank[risk_level] * 100)
        if missing_data:
            value -= Decimal("50")
        return value

    @staticmethod
    def _draft(intent: str, order: dict[str, Any] | None) -> str:
        order_id = str(order.get("external_order_id")) if order else "the order referenced in your message"
        drafts = {
            "where_is_my_order": f"Thank you for contacting us. We reviewed {order_id}. It is currently marked as shipped. Please continue to use the Amazon tracking page for the latest carrier update.",
            "delivery_delay": f"We are sorry for the delay with {order_id}. The order is marked as shipped. We have documented the delay and recommend checking the latest delivery estimate in Amazon order tracking.",
            "cancel_request": f"We received your cancellation request for {order_id}. A support operator must review the current fulfillment status before any action is taken.",
            "refund_demand": f"We received your refund request for {order_id}. No refund has been issued. A human support operator must review the order and applicable policy.",
            "damaged_item": f"We are sorry that the item from {order_id} arrived damaged. Please provide a clear photo of the product and packaging so a support operator can review the next step.",
            "wrong_item": f"We are sorry that the item in {order_id} does not match your purchase. Please provide a photo of the received item and shipping label for manual review.",
            "missing_accessory": f"Thank you for reporting the missing accessory for {order_id}. Please tell us which part is missing so the package contents can be reviewed.",
            "usage_question": "Thank you for your question. A support operator will confirm the product instructions before providing usage guidance.",
            "invoice_request": f"We received your invoice request for {order_id}. A support operator will verify the available invoice process for this marketplace.",
            "negative_sentiment": f"We are sorry for the frustration related to {order_id}. Your case has been prioritized for human review.",
            "off_platform_contact": "For your security, we can only continue this order-related conversation through Amazon-approved communication channels.",
            "review_incentive": "Reviews and refunds are handled independently. We cannot offer compensation in exchange for changing or removing a review.",
        }
        return drafts[intent]
