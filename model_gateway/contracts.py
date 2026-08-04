from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GatewayBudgetModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelBudgetRequest(GatewayBudgetModel):
    estimated_prompt_tokens: int = Field(gt=0)
    max_completion_tokens: int = Field(gt=0)
    requested_model: str | None = None
    reserve_schema_repair: bool = True
    repair_prompt_overhead_tokens: int = Field(default=512, ge=0)


class ModelAttemptBudget(GatewayBudgetModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    initial_prompt_tokens: int = Field(gt=0)
    initial_completion_tokens: int = Field(gt=0)
    repair_prompt_tokens: int = Field(ge=0)
    repair_completion_tokens: int = Field(ge=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_total_tokens: int = Field(gt=0)
    provider_token_limit: int = Field(gt=0)
    token_limit_satisfied: bool
    max_repair_calls: int = Field(ge=0, le=1)
    input_cost_per_million: Decimal = Field(ge=0)
    output_cost_per_million: Decimal = Field(ge=0)
    max_estimated_cost: Decimal = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    bill_on_unknown_failure: bool = True
    pricing_version: str = Field(min_length=1)
    pricing_effective_at: datetime

    @field_validator("pricing_effective_at")
    @classmethod
    def validate_pricing_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("pricing_effective_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_totals(self) -> ModelAttemptBudget:
        if self.max_input_tokens != self.initial_prompt_tokens + self.repair_prompt_tokens:
            raise ValueError("max_input_tokens does not match initial plus repair prompt tokens")
        if self.max_output_tokens != self.initial_completion_tokens + self.repair_completion_tokens:
            raise ValueError("max_output_tokens does not match initial plus repair completion tokens")
        if self.max_total_tokens != self.max_input_tokens + self.max_output_tokens:
            raise ValueError("max_total_tokens does not match input plus output tokens")
        if self.token_limit_satisfied != (self.max_total_tokens <= self.provider_token_limit):
            raise ValueError("token_limit_satisfied does not match provider token limit")
        return self


class ModelBudgetEstimate(GatewayBudgetModel):
    attempts: tuple[ModelAttemptBudget, ...]
    max_provider_attempts: int = Field(gt=0)
    worst_case_total_tokens: int = Field(gt=0)
    aggregate_provider_token_limit: int = Field(gt=0)
    worst_case_estimated_cost: Decimal = Field(ge=0)
    provider_limits_satisfied: bool
    includes_schema_repair: bool
    includes_failover: bool
    unknown_failure_may_be_billed: bool
    assumptions: tuple[str, ...]

    @model_validator(mode="after")
    def validate_aggregate(self) -> ModelBudgetEstimate:
        if not self.attempts:
            raise ValueError("budget estimate must include at least one provider attempt")
        if self.max_provider_attempts != len(self.attempts):
            raise ValueError("max_provider_attempts does not match attempts")
        if self.worst_case_total_tokens != sum(item.max_total_tokens for item in self.attempts):
            raise ValueError("worst_case_total_tokens does not match attempts")
        if self.aggregate_provider_token_limit != sum(item.provider_token_limit for item in self.attempts):
            raise ValueError("aggregate_provider_token_limit does not match attempts")
        if self.worst_case_estimated_cost != sum(
            (item.max_estimated_cost for item in self.attempts),
            start=Decimal("0"),
        ):
            raise ValueError("worst_case_estimated_cost does not match attempts")
        if self.provider_limits_satisfied != all(item.token_limit_satisfied for item in self.attempts):
            raise ValueError("provider_limits_satisfied does not match attempts")
        return self

    def cost_upper_bound_for(self, providers: tuple[str, ...]) -> Decimal:
        requested = set(providers)
        return sum(
            (attempt.max_estimated_cost for attempt in self.attempts if attempt.provider in requested),
            start=Decimal("0"),
        )


def estimate_single_provider_budget(
    *,
    request: ModelBudgetRequest,
    provider: str,
    model: str,
    provider_token_limit: int,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
    timeout_seconds: float,
    pricing_version: str,
    pricing_effective_at: datetime | None = None,
    bill_on_unknown_failure: bool = True,
) -> ModelBudgetEstimate:
    repair_prompt_tokens = (
        request.estimated_prompt_tokens + request.max_completion_tokens + request.repair_prompt_overhead_tokens
        if request.reserve_schema_repair
        else 0
    )
    repair_completion_tokens = request.max_completion_tokens if request.reserve_schema_repair else 0
    max_input_tokens = request.estimated_prompt_tokens + repair_prompt_tokens
    max_output_tokens = request.max_completion_tokens + repair_completion_tokens
    max_total_tokens = max_input_tokens + max_output_tokens
    input_rate = Decimal(str(input_cost_per_million))
    output_rate = Decimal(str(output_cost_per_million))
    max_estimated_cost = (Decimal(max_input_tokens) * input_rate + Decimal(max_output_tokens) * output_rate) / Decimal(
        "1000000"
    )
    attempt = ModelAttemptBudget(
        provider=provider,
        model=model,
        initial_prompt_tokens=request.estimated_prompt_tokens,
        initial_completion_tokens=request.max_completion_tokens,
        repair_prompt_tokens=repair_prompt_tokens,
        repair_completion_tokens=repair_completion_tokens,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_total_tokens=max_total_tokens,
        provider_token_limit=provider_token_limit,
        token_limit_satisfied=max_total_tokens <= provider_token_limit,
        max_repair_calls=1 if request.reserve_schema_repair else 0,
        input_cost_per_million=input_rate,
        output_cost_per_million=output_rate,
        max_estimated_cost=max_estimated_cost,
        timeout_seconds=timeout_seconds,
        bill_on_unknown_failure=bill_on_unknown_failure,
        pricing_version=pricing_version,
        pricing_effective_at=pricing_effective_at or datetime(1970, 1, 1, tzinfo=UTC),
    )
    return combine_model_budget_estimates((attempt,), includes_failover=False)


def combine_model_budget_estimates(
    attempts: tuple[ModelAttemptBudget, ...],
    *,
    includes_failover: bool,
) -> ModelBudgetEstimate:
    return ModelBudgetEstimate(
        attempts=attempts,
        max_provider_attempts=len(attempts),
        worst_case_total_tokens=sum(item.max_total_tokens for item in attempts),
        aggregate_provider_token_limit=sum(item.provider_token_limit for item in attempts),
        worst_case_estimated_cost=sum(
            (item.max_estimated_cost for item in attempts),
            start=Decimal("0"),
        ),
        provider_limits_satisfied=all(item.token_limit_satisfied for item in attempts),
        includes_schema_repair=any(item.max_repair_calls for item in attempts),
        includes_failover=includes_failover,
        unknown_failure_may_be_billed=any(item.bill_on_unknown_failure for item in attempts),
        assumptions=(
            "Each provider may consume one initial call and one schema-repair call.",
            "A sent request with unknown usage is reserved at its full configured upper bound.",
            "Fallback is reserved in addition to the primary attempt, not instead of it.",
        ),
    )
