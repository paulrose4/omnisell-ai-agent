from __future__ import annotations

from agents.contracts import ListingOptimizationAnalysisEnvelope
from agents.prompts.listing_optimization_v4 import (
    LISTING_OPTIMIZATION_PROMPT_VERSION,
    LISTING_OPTIMIZATION_SYSTEM_PROMPT,
)
from agents.runtime import StructuredBoundedAdvisor
from agents.validators.evidence import EvidenceValidator
from model_gateway.gateway import ModelGateway


def create_listing_optimization_advisor(*, gateway: ModelGateway, model: str | None = None) -> StructuredBoundedAdvisor:
    return StructuredBoundedAdvisor(
        agent_type="listing_optimization_advisor",
        prompt_version=LISTING_OPTIMIZATION_PROMPT_VERSION,
        system_prompt=LISTING_OPTIMIZATION_SYSTEM_PROMPT,
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
        model=model,
        output_schema=ListingOptimizationAnalysisEnvelope,
    )
