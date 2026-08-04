from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from agents.contracts import AgentAnalysisEnvelope, AgentProposedActionBase, EvidenceBundle, EvidenceReference


class EvidenceValidationError(RuntimeError):
    pass


class EvidenceValidator:
    HIGH_CONFIDENCE_THRESHOLD = Decimal("0.90")
    STRUCTURED_CONFIDENCE_THRESHOLD = Decimal("0.75")
    BLOCKING_MISSING_DATA_CAP = Decimal("0.65")
    STRUCTURED_SOURCES = {"amazon_api", "advertising_api", "internal_record"}
    PERMANENTLY_PROHIBITED_ACTIONS = {
        "refund",
        "issue_refund",
        "payment_reversal",
        "reverse_payment",
        "autonomous_cancellation",
        "cancel_order",
        "approval_bypass",
        "cross_tenant_action",
        "arbitrary_amazon_write",
    }
    DETERMINISTIC_FIELD_MARKERS = {
        "amount",
        "price",
        "cost",
        "fee",
        "profit",
        "margin",
        "inventory",
        "quantity",
        "stock",
        "score",
        "rate",
        "tax",
        "currency",
        "acos",
        "coverage",
        "reorder",
        "replenish",
        "replenishment",
        "velocity",
        "units",
    }

    def validate(self, *, bundle: EvidenceBundle, analysis: AgentAnalysisEnvelope) -> AgentAnalysisEnvelope:
        if bundle.tenant_id != analysis.tenant_id:
            raise EvidenceValidationError("analysis tenant does not match the evidence bundle tenant")

        evidence_by_id = bundle.by_id()
        referenced_ids = self._referenced_evidence_ids(analysis)
        unknown_ids = sorted(referenced_ids - set(evidence_by_id))
        if unknown_ids:
            raise EvidenceValidationError(f"Agent referenced evidence not present in bundle: {', '.join(unknown_ids)}")

        referenced_evidence = [evidence_by_id[evidence_id] for evidence_id in referenced_ids]
        if (
            any(item.blocking for item in analysis.missing_data)
            and analysis.confidence > self.BLOCKING_MISSING_DATA_CAP
        ):
            raise EvidenceValidationError(f"blocking missing data caps confidence at {self.BLOCKING_MISSING_DATA_CAP}")
        if analysis.confidence >= self.HIGH_CONFIDENCE_THRESHOLD and (
            not referenced_evidence or any(item.freshness_status != "fresh" for item in referenced_evidence)
        ):
            raise EvidenceValidationError("confidence >= 0.90 requires fresh referenced evidence")
        if analysis.confidence >= self.STRUCTURED_CONFIDENCE_THRESHOLD and not any(
            item.source in self.STRUCTURED_SOURCES for item in referenced_evidence
        ):
            raise EvidenceValidationError("confidence >= 0.75 requires structured internal or platform evidence")

        resource_ids = {item.resource_id for item in referenced_evidence}
        for action in (*analysis.proposed_actions, *analysis.prohibited_actions):
            if action.resource_id not in resource_ids:
                raise EvidenceValidationError(
                    f"Agent referenced resource not present in evidence: {action.resource_type}/{action.resource_id}"
                )
            self._validate_action_boundary(action)
            self._validate_deterministic_values(action, evidence_by_id)
        return analysis

    def _referenced_evidence_ids(self, analysis: AgentAnalysisEnvelope) -> set[str]:
        evidence_ids = set(analysis.evidence)
        for hypothesis in analysis.hypotheses:
            evidence_ids.update(hypothesis.supporting_evidence_ids)
            evidence_ids.update(hypothesis.contradicting_evidence_ids)
        for action in (*analysis.proposed_actions, *analysis.prohibited_actions):
            evidence_ids.update(action.evidence_ids)
        return evidence_ids

    def _validate_action_boundary(self, action: AgentProposedActionBase) -> None:
        normalized = re.sub(r"[^a-z0-9]+", "_", action.action_type.strip().lower()).strip("_")
        permanently_prohibited = (
            normalized in self.PERMANENTLY_PROHIBITED_ACTIONS
            or "refund" in normalized
            or "payment_reversal" in normalized
            or "reverse_payment" in normalized
            or "cancel_order" in normalized
            or "order_cancellation" in normalized
            or "autonomous_cancellation" in normalized
            or "approval_bypass" in normalized
            or "cross_tenant" in normalized
            or "arbitrary_amazon_write" in normalized
        )
        if permanently_prohibited and not action.prohibited:
            raise EvidenceValidationError(f"permanently prohibited action must be marked prohibited: {normalized}")

    def _validate_deterministic_values(
        self,
        action: AgentProposedActionBase,
        evidence_by_id: dict[str, EvidenceReference],
    ) -> None:
        expected: dict[str, Any] = {}
        for evidence_id in action.evidence_ids:
            deterministic_values = evidence_by_id[evidence_id].metadata.get("deterministic_values", {})
            if isinstance(deterministic_values, dict):
                for field_path, value in self._flatten(deterministic_values).items():
                    if field_path in expected and expected[field_path] != value:
                        raise EvidenceValidationError(
                            f"conflicting deterministic values for {field_path} in referenced evidence"
                        )
                    expected[field_path] = value
        for field_path, value in self._flatten(action.arguments).items():
            if not self._is_deterministic_path(field_path):
                continue
            if field_path not in expected:
                raise EvidenceValidationError(
                    f"Agent introduced deterministic value not present in deterministic evidence: {field_path}"
                )
            if value != expected[field_path]:
                raise EvidenceValidationError(
                    f"Agent changed deterministic value for {field_path}: expected {expected[field_path]!r}"
                )

    @classmethod
    def _is_deterministic_path(cls, field_path: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", field_path.lower())
        return bool(set(normalized.split("_")) & cls.DETERMINISTIC_FIELD_MARKERS)

    @classmethod
    def _flatten(cls, value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        if isinstance(value, dict):
            flattened: dict[str, Any] = {}
            for key, item in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                flattened.update(cls._flatten(item, path))
            return flattened
        if isinstance(value, (list, tuple)):
            flattened = {}
            for index, item in enumerate(value):
                path = f"{prefix}.{index}" if prefix else str(index)
                flattened.update(cls._flatten(item, path))
            return flattened
        return {prefix: value}
