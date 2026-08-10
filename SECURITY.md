# Security Policy

## Supported security posture

OmniSell is currently published as an interview-oriented `read_only_pilot`. Real Amazon write operations are not enabled. Refunds, payment reversals, autonomous cancellation, and cross-tenant operations remain blocked by product policy.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or include credentials, customer data, tenant identifiers, provider responses, or other sensitive evidence in a public report.

Report the issue privately through GitHub Security Advisories for this repository. Include:

- the affected commit or release;
- the tenant, authorization, approval, execution, or data boundary involved;
- minimal reproduction steps using synthetic data;
- the expected and observed behavior;
- any evidence that the issue can cross a tenant or alter an approved action.

## Security invariants

- Every business query and record is tenant-scoped.
- PostgreSQL RLS is exercised with a non-owner application role.
- LLM output is advisory and cannot select a tenant, lower risk, approve an action, or provide execution parameters.
- Side effects require authorization, policy evaluation, an immutable approved plan, action hash, idempotency, audit, and recovery semantics.
- Money uses `Decimal` and ISO 4217 currency codes; timestamps are timezone-aware UTC.
- Golden Mock and imported data must never be presented as verified Amazon data.

See [`docs/security/THREAT_MODEL.md`](docs/security/THREAT_MODEL.md) for trust boundaries, controls, and residual risks.
