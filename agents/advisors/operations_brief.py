from __future__ import annotations

from agents.contracts import OperationsBriefAnalysisEnvelope
from agents.prompts.operations_brief_v7 import OPERATIONS_BRIEF_PROMPT_VERSION, OPERATIONS_BRIEF_SYSTEM_PROMPT
from agents.runtime import StructuredBoundedAdvisor
from agents.validators.evidence import EvidenceValidator
from model_gateway.gateway import ModelGateway


def create_operations_brief_advisor(*, gateway: ModelGateway, model: str | None = None) -> StructuredBoundedAdvisor:
    return StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version=OPERATIONS_BRIEF_PROMPT_VERSION,
        system_prompt=OPERATIONS_BRIEF_SYSTEM_PROMPT,
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
        model=model,
        output_schema=OperationsBriefAnalysisEnvelope,
    )
