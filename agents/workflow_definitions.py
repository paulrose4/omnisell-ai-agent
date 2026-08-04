from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


WorkflowClass = Literal[
    "deterministic_analysis",
    "bounded_advisory",
    "read_only_investigation",
    "approval_gated_action",
]
GenerationMethod = Literal["deterministic", "model"]


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_type: str
    display_name: str
    workflow_class: WorkflowClass
    generation_method: GenerationMethod
    amazon_write_capability: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


WORKFLOW_DEFINITIONS = {
    item.workflow_type: item
    for item in (
        WorkflowDefinition(
            workflow_type="operations_brief_analysis",
            display_name="运营简报 Advisor",
            workflow_class="bounded_advisory",
            generation_method="model",
        ),
        WorkflowDefinition(
            workflow_type="customer_case_analysis",
            display_name="客户案例 Advisor",
            workflow_class="bounded_advisory",
            generation_method="model",
        ),
        WorkflowDefinition(
            workflow_type="listing_optimization_analysis",
            display_name="Listing 优化 Advisor",
            workflow_class="bounded_advisory",
            generation_method="model",
        ),
        WorkflowDefinition(
            workflow_type="reconciliation_investigation",
            display_name="对账调查 Advisor",
            workflow_class="read_only_investigation",
            generation_method="model",
        ),
        WorkflowDefinition(
            workflow_type="operations_recommendations",
            display_name="待审批运营动作",
            workflow_class="approval_gated_action",
            generation_method="deterministic",
        ),
    )
}


def workflow_definition(workflow_type: str) -> WorkflowDefinition:
    return WORKFLOW_DEFINITIONS.get(
        workflow_type,
        WorkflowDefinition(
            workflow_type=workflow_type,
            display_name=workflow_type.replace("_", " ").title(),
            workflow_class="deterministic_analysis",
            generation_method="deterministic",
        ),
    )
