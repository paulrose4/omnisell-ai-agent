# ADR 0003: Read-Only Pilot is the Default Production Profile

## Status
Accepted (2026-08-05). Wired through `api/release_profiles.py`.

## Context
Until an operator signs off the real Amazon SP-API integration, the production
profile must make destructive platform writes (refunds, payment reversals,
autonomous cancellation, cross-tenant operations) impossible. Mock provider flows
must not leak into production, and we need a deterministic gate that any operator
can audit.

## Decision
`ReleaseProfile.read_only_pilot` is the canonical production profile. It mounts
only the read-only and human-execution API surface, refuses refund / cross-tenant
operations and requires two-person approval for any change that would touch
Amazon-side state. `full_development` and `golden_demo` are explicitly non-production
profiles that mount mock and auxiliary routes.

## Alternatives considered
- *Feature flag per route*: rejected because the blast radius is the whole control
  tower, not a single endpoint.
- *Sandbox Amazon account*: useful for staging but does not protect production
  tenants if the wrong env variable is set.

## Consequences
- The release profile is asserted at startup; mismatched profiles fail closed.
- `tests/test_release_profiles.py` and
  `tests/test_production_quality_gates.py` enforce the profile contract.
- Operator sign-off (`docs/OPERATOR_PRODUCTION_ACCEPTANCE.md`) gates promotion to
  `full_writes_pilot`.
