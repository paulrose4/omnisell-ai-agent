# OmniSell AI Engineering Rules

## Scope
These rules apply to the entire repository.

## Product and domain
- OmniSell is an Amazon operations control tower, not a free-chat product.
- Deterministic code owns money, fees, inventory, permissions, policy, approval and state transitions.
- LLM output is advisory and must expose evidence, confidence, missing data and freshness.
- Never present mock, estimated or imported data as verified Amazon data.

## Data and security
- Every business record and repository query is tenant-scoped.
- Money uses `Decimal` and ISO 4217 currency codes; timestamps are timezone-aware UTC.
- New persistent business models require Alembic migrations, tenant indexes, reasonable unique constraints and PostgreSQL RLS.
- Side effects require authorization, policy evaluation, idempotency, action hash, audit and recovery semantics.
- Refunds, payment reversals, autonomous cancellation and cross-tenant operations remain blocked.

## Delivery
- Check `git status` before each milestone and do not overwrite unrelated uncommitted work.
- Add a failing regression test or eval before changing behavior.
- Run focused tests, full pytest, compileall, migration checks and applicable frontend validation.
- Document external verification gaps and operator decisions explicitly.
