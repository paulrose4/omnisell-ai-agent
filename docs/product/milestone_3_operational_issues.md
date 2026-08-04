# Milestone 3 — Unified Operational Issues

## Scope delivered

- Deterministic `OperationalIssue` and `RecommendedAction` domain objects.
- Detection from persisted inventory-health and SKU-profit snapshots.
- Tenant-scoped issue/action repositories with stable deduplication keys.
- Severity ranking, quantified business impact, source evidence, confidence, freshness and degraded-data fields.
- Explicit issue and recommendation state transitions.
- PostgreSQL RLS registration and Alembic migration `0004`.
- Read/write APIs for issue detection, queues, detail, acknowledgement and internal recommendation disposition.
- Audit events for detection and state transitions.

## Detection rules

### Inventory

- `inventory_stockout_risk`: emitted when the health snapshot reports stockout risk. Critical when coverage is seven days or less; high when coverage is within total replenishment lead time.
- `inventory_excess`: emitted when excess units or excess risk are present.
- `inventory_data_quality`: emitted when inventory evidence is missing, stale, distorted or otherwise degraded.

### Profit

- `sku_contribution_loss`: emitted when deterministic contribution profit is below zero. A contribution margin at or below -10% is currently ranked critical; this is a product default requiring operator confirmation.
- `profit_data_quality`: emitted when required cost/fee inputs are missing or the profit snapshot is degraded.

## Deduplication

The stable deduplication scope is:

```text
sha256(tenant_id | issue_type | marketplace_id | seller_sku)
```

Repeated detection updates the same issue, increments `occurrence_count`, refreshes evidence and impact, and preserves the canonical issue ID. A previously resolved or dismissed issue is reopened if the same condition is detected again.

Recommended actions use the issue dedupe key plus `action_type`, preventing duplicate proposed actions during repeated detection.

## State transitions

Issue transitions:

```text
open -> acknowledged | in_progress | resolved | dismissed
acknowledged -> in_progress | resolved | dismissed
in_progress -> resolved | dismissed
```

Recommended action transitions:

```text
proposed -> accepted | rejected | canceled
accepted -> completed | canceled
```

Detection may reopen terminal issues/actions when current evidence proves the condition is active again. All transitions are tenant-scoped and API transitions are audited.

## API

- `POST /api/operations/issues/detect`
- `GET /api/operations/issues`
- `GET /api/operations/issues/{issue_id}`
- `PATCH /api/operations/issues/{issue_id}`
- `GET /api/operations/actions`
- `PATCH /api/operations/actions/{action_id}`

Detection accepts persisted inventory/profit snapshot IDs. A snapshot belonging to another tenant is returned as not found rather than disclosed.

## Execution boundary

All actions in this milestone use `recommendation_only` and `automation_eligible=false`. Accepting an internal recommendation does not call Amazon, change price, create a supplier order or bypass HITL. Milestone 9 will introduce accountable operational tasks and execution/outcome records.

## Known data-contract gap

Inventory-health snapshots can contain estimated revenue/profit impact but currently do not persist an ISO 4217 impact currency. Operational issues therefore preserve those numeric estimates without inventing USD or another currency, and expose `currency=null`. A later inventory schema migration must add an explicit impact currency before mixed-marketplace aggregation or financial reporting.

## Rollback

- Routes, services and tables are additive.
- Before production reliance, run `alembic downgrade 0003`.
- Rolling back drops operational issues and recommended actions; export records first if operators have already triaged them.
- No Amazon or supplier side effects need compensation because this milestone is recommendation-only.
