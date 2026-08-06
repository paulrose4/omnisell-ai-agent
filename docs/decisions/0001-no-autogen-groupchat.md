# ADR 0001: No AutoGen GroupChat in Production

## Status
Accepted (2026-08-05). Drives production entry points; legacy code lives under `legacy/autogen_demo/` only.

## Context
AutoGen `GroupChat` provides flexible multi-agent conversations but it allows any
participant to mutate shared state and to issue tool calls without explicit approval
binding. Within an Amazon operations control tower we need every recommendation to:

1. Be tenant-scoped and reproducible.
2. Carry immutable evidence references and explicit confidence.
3. Be subject to deterministic policy evaluation and two-person approval.

## Decision
OmniSell does **not** import `autogen.GroupChat` in production code. Production entry
points (`agents.runtime`, `workflows.engine`, `application.actions`) implement a
fixed DAG: evidence collection -> planner -> policy evaluation -> approval -> typed
manual execution. AutoGen remains available under `legacy/autogen_demo/` for reference
and is excluded from the release profile.

## Alternatives considered
- *AutoGen GroupChat*: rejected because plan, policy and approval cannot be enforced
  in a graph that any participant can route.
- *LangGraph*: considered but adds a parallel DAG layer; kept available through
  bounded `ModelGateway`, not chosen as primary orchestrator.

## Consequences
- Lighter reasoning surface; each step is auditable.
- Migration cost: any prior AutoGen prototype is moved to `legacy/` and excluded from
  `ReleaseProfile.full_development` and `golden_demo` mounts.
- Coverage: `tests/test_legacy_agent_runtime_boundaries.py` asserts production code
  does not import `autogen`.
