from __future__ import annotations

from agents.contracts import CustomerCaseAnalysisEnvelope
from agents.prompts.customer_case_v3 import CUSTOMER_CASE_PROMPT_VERSION, CUSTOMER_CASE_SYSTEM_PROMPT
from agents.runtime import StructuredBoundedAdvisor
from agents.validators.evidence import EvidenceValidator
from model_gateway.gateway import ModelGateway


def create_customer_case_advisor(*, gateway: ModelGateway, model: str | None = None) -> StructuredBoundedAdvisor:
    return StructuredBoundedAdvisor(
        agent_type="customer_case_advisor",
        prompt_version=CUSTOMER_CASE_PROMPT_VERSION,
        system_prompt=CUSTOMER_CASE_SYSTEM_PROMPT,
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
        model=model,
        output_schema=CustomerCaseAnalysisEnvelope,
    )
