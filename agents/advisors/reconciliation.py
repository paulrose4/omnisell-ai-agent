from __future__ import annotations

from agents.contracts import ReconciliationInvestigationEnvelope
from agents.prompts.reconciliation_investigation_v6 import (
    RECONCILIATION_INVESTIGATION_PROMPT_VERSION,
    RECONCILIATION_INVESTIGATION_SYSTEM_PROMPT,
)
from agents.runtime import StructuredBoundedAdvisor
from agents.validators.evidence import EvidenceValidator
from model_gateway.gateway import ModelGateway


def create_reconciliation_investigator(*, gateway: ModelGateway, model: str | None = None) -> StructuredBoundedAdvisor:
    return StructuredBoundedAdvisor(
        agent_type="reconciliation_investigator",
        prompt_version=RECONCILIATION_INVESTIGATION_PROMPT_VERSION,
        system_prompt=RECONCILIATION_INVESTIGATION_SYSTEM_PROMPT,
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
        model=model,
        output_schema=ReconciliationInvestigationEnvelope,
    )
