# Dependency Maintenance

OmniSell uses grouped monthly version updates so routine maintenance remains reviewable for a solo operator. The normal
steady state is no more than five version-update pull requests: Python, frontend runtime, frontend tooling, Docker, and
GitHub Actions.

## Security updates

Dependabot alerts and repository security notifications are the source of truth for vulnerable dependencies. Security
updates are handled immediately and separately from the monthly maintenance batches. They must pass the same relevant
tests, migration checks, tenant-isolation checks, and supply-chain scans as any other production change.

The major-version ignore rules in `.github/dependabot.yml` apply to routine version maintenance. If GitHub cannot create
a security pull request because the available fix requires an ignored major version, the operator creates a dedicated
`fix/security-*` branch from `main`, records the alert and migration risk in the pull request, and resolves it without
waiting for the quarterly major-upgrade window. This is an explicit operator decision, not an automated exception.

Repository vulnerability alerts, the dependency graph, and Dependabot security updates must remain enabled in GitHub.
These GitHub-hosted settings cannot be verified from the repository alone and are checked during repository-settings
reviews.

## Routine updates

- Dependabot checks routine versions monthly in the `Asia/Shanghai` timezone.
- Patch and minor versions are grouped by ecosystem and dependency role.
- Automatic rebases are disabled to prevent repeated CI runs. Use `@dependabot rebase` only when a reviewed PR needs it.
- Every batch must pass focused validation and the applicable repository CI gates before squash merge.
- A failing batch is split until the incompatible dependency is isolated; it is never merged merely to clear the queue.

## Major upgrades

Major upgrades are planned quarterly and completed for one major dependency at a time. Create an issue that records the
current version, target version, migration guide, affected contracts, rollback plan, and external verification gaps.
Implement the upgrade on a short-lived `chore/*` branch and do not combine unrelated framework migrations.

## Branch lifecycle

`main` is the only long-lived branch. Use short-lived `feat/*`, `fix/*`, or `chore/*` branches, squash merge them after
required checks pass, and delete the head branch automatically. Releases are immutable `vX.Y.Z` tags created from
`main`; the project does not maintain `develop`, `staging`, or long-lived release branches.
