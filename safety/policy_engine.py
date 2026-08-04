from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from observability.telemetry import log_structured, policy_decisions, trace_attributes, tracer


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


POLICY_RULESET_VERSION = "policy-engine-v1"


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    risk_level: str
    reason: str
    policy_version: str = POLICY_RULESET_VERSION


BLOCKED_ACTIONS = {
    "refund",
    "payment_reversal",
    "reverse_payment",
    "issue_compensation",
    "cancel_order",
    "autonomous_cancellation",
}
CRITICAL_ACTIONS = {"delete_listing", "delete_product", "bulk_update"}
APPROVAL_ACTIONS = {
    "update_price",
    "update_product",
    "create_product",
    "adjust_inventory",
    "publish_listing",
    "update_order_status",
    "approve_return",
    "request_manual_execution",
}
AUTO_ACTIONS = {
    "read_catalog",
    "read_orders",
    "read_inventory",
    "translate",
    "generate_draft",
    "search_knowledge",
    "get_product_list",
    "get_product_detail",
    "get_product_price",
    "get_order_list",
    "fetch_competitor_prices",
    "generate_price_recommendation",
    "search_knowledge_base",
    "answer_customer_question",
    "translate_text",
    "detect_and_translate",
    "analyze_customer_image",
    "answer_visual_customer_question",
}
ACTION_INTENT_KEYS = {"action", "action_type", "requested_action", "operation", "tool", "tool_name"}
ACTION_INTENT_CONTAINERS = {"arguments", "execution_plan", "plan"}


def normalize_action_name(action: str) -> str:
    return action.strip().lower().replace("-", "_").replace(" ", "_")


def permanently_prohibited_intent(action: str, arguments: dict[str, Any] | None = None) -> str | None:
    for candidate in (action, *_structured_action_intents(arguments or {})):
        normalized = normalize_action_name(candidate)
        if (
            normalized in BLOCKED_ACTIONS
            or "refund" in normalized
            or "payment_reversal" in normalized
            or "reverse_payment" in normalized
            or "cancel_order" in normalized
            or "order_cancel" in normalized
            or "cancel_request" in normalized
            or "autonomous_cancellation" in normalized
            or "compensation" in normalized
        ):
            return normalized
    return None


def _structured_action_intents(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = normalize_action_name(str(key))
            if normalized_key in ACTION_INTENT_KEYS and isinstance(item, str):
                found.append(item)
            elif normalized_key in ACTION_INTENT_CONTAINERS:
                found.extend(_structured_action_intents(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_structured_action_intents(item))
    return tuple(found)


def canonical_action_hash(*, action: str, tenant_id: str, resource_id: str | None, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "tenant_id": tenant_id, "resource_id": resource_id, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PolicyEngine:
    def evaluate(self, *, action: str, arguments: dict[str, Any], confidence: float = 1.0) -> PolicyResult:
        with tracer.start_as_current_span("policy.evaluate") as span:
            span.set_attributes(trace_attributes(action=action, confidence=confidence, argument_keys=sorted(arguments)))
            result = self._evaluate(action=action, arguments=arguments, confidence=confidence)
            span.set_attributes(
                trace_attributes(decision=result.decision.value, risk_level=result.risk_level, reason=result.reason)
            )
            policy_decisions.add(
                1, trace_attributes(action=action.lower(), decision=result.decision.value, risk_level=result.risk_level)
            )
            if result.decision == PolicyDecision.BLOCK:
                log_structured(
                    logging.WARNING,
                    "policy_action_blocked",
                    action=action,
                    confidence=confidence,
                    risk_level=result.risk_level,
                    reason=result.reason,
                    argument_keys=sorted(arguments),
                    arguments_sha256=hashlib.sha256(
                        json.dumps(arguments, sort_keys=True, default=str).encode()
                    ).hexdigest(),
                )
                span.add_event(
                    "dangerous_action_blocked",
                    trace_attributes(action=action, reason=result.reason, risk_level=result.risk_level),
                )
            return result

    def _evaluate(self, *, action: str, arguments: dict[str, Any], confidence: float) -> PolicyResult:
        normalized = normalize_action_name(action)
        if normalized == "send_customer_reply":
            violations = list(arguments.get("compliance_violations") or [])
            if violations:
                return PolicyResult(
                    PolicyDecision.BLOCK,
                    "critical",
                    f"Customer reply violates communication policy: {', '.join(violations)}",
                )
            case_risk = str(arguments.get("case_risk_level") or "low").lower()
            if case_risk in {"high", "critical"} or confidence < 0.90:
                return PolicyResult(
                    PolicyDecision.REQUIRE_APPROVAL,
                    case_risk if case_risk in {"high", "critical"} else "high",
                    "Sensitive customer communication requires independent review.",
                )
            return PolicyResult(
                PolicyDecision.ALLOW, "low", "Factual customer-service draft may proceed to operator send review."
            )
        if permanently_prohibited_intent(action, arguments) is not None:
            return PolicyResult(
                PolicyDecision.BLOCK,
                "critical",
                "Refund, payment reversal, compensation, and autonomous order cancellation actions are prohibited for agents.",
            )
        if normalized in CRITICAL_ACTIONS:
            return PolicyResult(
                PolicyDecision.REQUIRE_APPROVAL, "critical", "Critical commerce write requires explicit approval."
            )
        if normalized in APPROVAL_ACTIONS:
            if normalized == "request_manual_execution":
                return PolicyResult(
                    PolicyDecision.REQUIRE_APPROVAL,
                    "high",
                    "High-risk operations advice requires independent human review before manual execution.",
                )
            if normalized == "update_price":
                required_evidence = {
                    "current_price",
                    "new_price",
                    "currency",
                    "break_even_price",
                    "projected_margin",
                    "minimum_margin_rate",
                    "inventory_stockout_risk",
                    "data_complete",
                }
                missing_evidence = sorted(
                    name for name in required_evidence if name not in arguments or arguments[name] is None
                )
                if missing_evidence:
                    return PolicyResult(
                        PolicyDecision.REQUIRE_APPROVAL,
                        "high",
                        f"Pricing evidence is incomplete: {', '.join(missing_evidence)}.",
                    )
                currency = str(arguments["currency"])
                if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
                    return PolicyResult(
                        PolicyDecision.REQUIRE_APPROVAL,
                        "high",
                        "Pricing evidence contains an invalid ISO 4217 currency code.",
                    )
                try:
                    old_price = Decimal(str(arguments["current_price"]))
                    new_price = Decimal(str(arguments["new_price"]))
                    break_even_price = Decimal(str(arguments["break_even_price"]))
                    projected_margin = Decimal(str(arguments["projected_margin"]))
                    minimum_margin_rate = Decimal(str(arguments["minimum_margin_rate"]))
                except (InvalidOperation, TypeError, ValueError):
                    return PolicyResult(
                        PolicyDecision.REQUIRE_APPROVAL,
                        "high",
                        "Pricing evidence contains an invalid Decimal value.",
                    )
                numeric_evidence = (
                    old_price,
                    new_price,
                    break_even_price,
                    projected_margin,
                    minimum_margin_rate,
                )
                if (
                    not all(value.is_finite() for value in numeric_evidence)
                    or old_price <= 0
                    or new_price <= 0
                    or break_even_price < 0
                ):
                    return PolicyResult(
                        PolicyDecision.REQUIRE_APPROVAL,
                        "high",
                        "Pricing evidence contains non-finite or out-of-range values.",
                    )
                if break_even_price > 0 and new_price < break_even_price:
                    return PolicyResult(
                        PolicyDecision.BLOCK,
                        "critical",
                        "Price below the deterministic break-even floor is prohibited.",
                    )
                if arguments.get("inventory_stockout_risk") == "critical" and new_price < old_price:
                    return PolicyResult(
                        PolicyDecision.BLOCK,
                        "critical",
                        "Price decreases are prohibited during a critical stockout risk.",
                    )
                if arguments["data_complete"] is not True:
                    return PolicyResult(
                        PolicyDecision.REQUIRE_APPROVAL,
                        "high",
                        "Incomplete or stale pricing evidence requires human approval.",
                    )
                if projected_margin < minimum_margin_rate:
                    return PolicyResult(
                        PolicyDecision.REQUIRE_APPROVAL,
                        "high",
                        "Projected contribution margin is below the configured minimum.",
                    )
                if old_price > 0 and abs(new_price - old_price) / old_price <= Decimal("0.03") and confidence >= 0.95:
                    return PolicyResult(
                        PolicyDecision.ALLOW,
                        "low",
                        "High-confidence price change is within the configured 3% automation band.",
                    )
            return PolicyResult(PolicyDecision.REQUIRE_APPROVAL, "high", "Commerce write requires policy approval.")
        if normalized in AUTO_ACTIONS:
            return PolicyResult(PolicyDecision.ALLOW, "low", "Read-only or draft action is safe to automate.")
        return PolicyResult(PolicyDecision.REQUIRE_APPROVAL, "medium", "Unknown actions default to human approval.")
