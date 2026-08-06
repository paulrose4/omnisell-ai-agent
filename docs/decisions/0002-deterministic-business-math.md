# ADR 0002: Deterministic Code Owns Money, Fees, Inventory, State

## Status
Accepted (2026-08-05).

## Context
Money, fees, referral rates, FBA fees, storage costs, profit, inventory, listing
status and approval state have legal and contractual consequences. Allowing an LLM to
emit these values risks hallucinated numbers silently changing Amazon-side business
state, even if downstream validators catch them after the fact.

## Decision
All quantitative business math lives in `domain/*.py` and is computed by deterministic
Python functions with `Decimal` arithmetic, tenant-scoped lookups and explicit ISO
4217 currency codes. The LLM may only emit *evidence references* and *natural language
explanations*; it cannot emit money, percentages, inventory counts or approval status
that override domain calculations. Validation
(`agents/validators/evidence.py`, `safety/policy_engine.py`) re-checks the LLM output
against the deterministic source and rejects unsupported claims.

## Alternatives considered
- *End-to-end LLM agent*: rejected because compliance review boards need reproducible
  math for any Amazon-side change.
- *LLM suggestions + human confirm*: rejected because humans do not want to
  re-verify referral fees and storage costs for every recommendation.

## Consequences
- Slightly larger code surface (deterministic modules) but with `Decimal` and timezone
  guarantees.
- LLM adapters must convert suggestions into evidence references; tests in
  `tests/test_bounded_advisor_workflow.py` enforce this.
- Operators can trust KPI dashboards because numbers are reproducible from inputs.
