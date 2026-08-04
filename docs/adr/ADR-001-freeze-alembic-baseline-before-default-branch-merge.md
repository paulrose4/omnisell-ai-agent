# ADR-001: Freeze the Alembic Baseline Before Default-Branch Merge

- Status: Accepted
- Date: 2026-08-05
- Owners: Platform and application architecture
- Decision scope: Alembic revisions `0001` through `0023`

## Context

The original migration chain was suitable for local prototyping but not for a production-facing repository. Revision `0001` called `Base.metadata.create_all()`, and most later revisions imported the current application models or used `checkfirst=True`. Replaying an old revision therefore depended on the latest ORM instead of immutable historical DDL.

The branch has not been merged into `origin/main`, has no release tag, and contains no tracked database, dump, backup, or production deployment record. Repository documentation describes disposable local SQLite and Compose environments only. The product is explicitly presented as a production-oriented pilot rather than an already deployed Amazon write system.

## Decision

Use the pre-release squash path:

1. Preserve `0023` as the public head revision identifier.
2. Replace the active `0001`-`0023` chain with one explicit `0023_frozen_baseline.py` whose `down_revision` is `None`.
3. Keep every table, column, foreign key, unique constraint, check constraint, and index as literal Alembic operations.
4. Keep the PostgreSQL RLS registry as a literal tuple in the migration. Every table containing `tenant_id` must appear exactly once.
5. Persist approval `policy_decision` and `policy_version` as structured columns and enforce the allowed policy, risk, and status combinations in the database.
6. Forbid active migration files from importing application ORM modules, calling metadata `create_all` or `drop_all`, or using conditional `checkfirst` schema mutation.

The deleted prototype revisions remain reviewable in Git history. They are no longer an executable compatibility contract.

## Alternatives Considered

### Preserve the mutable chain and add revision `0024`

Rejected. A corrective revision could repair the latest schema but could not make a fresh replay of `0001`-`0023` independent from future ORM changes.

### Rewrite all 23 revisions as immutable historical steps

Rejected for this pre-release branch. It would add substantial review surface without preserving any real deployed upgrade path.

### Start a new revision namespace

Rejected. Keeping head `0023` minimizes changes to tests, runbooks, and release evidence while the branch is still outside the default branch.

## Consequences

- Any local database already stamped at an old `0001`-`0023` revision must be discarded and recreated before using this branch.
- No in-place upgrade from the prototype chain is supported or claimed.
- After this baseline is merged or used by a persistent shared environment, every schema change must be a new immutable revision after `0023`; the baseline must not be rewritten.
- SQLite remains the portable empty-database smoke target. PostgreSQL is the authoritative target for RLS, policy inspection, non-owner isolation, and concurrent approval behavior.

## Verification

Required before merge:

- `alembic heads` reports the single head `0023`.
- An empty SQLite database upgrades to `0023`, passes `alembic check`, downgrades to `base`, and upgrades again.
- Static tests prove the active migration is self-contained and that its RLS registry matches every current tenant-scoped ORM table.
- The approval table rejects `require_approval` combined with `auto_approved`.
- An empty PostgreSQL database upgrades to `0023`.
- A non-owner PostgreSQL application role cannot read or write another tenant's rows.
- PostgreSQL catalogs show RLS enabled and the expected policy on every table in `FROZEN_RLS_TABLES`.

The PostgreSQL and container checks remain a release blocker when Docker is unavailable; they must be recorded as pending rather than represented as passed.
