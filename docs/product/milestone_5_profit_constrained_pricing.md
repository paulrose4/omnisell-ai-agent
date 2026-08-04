# Milestone 5 — Profit-Constrained Pricing

## Scope delivered

- Deterministic `PriceRecommendation` model and calculator.
- Required cost profile, SKU profit snapshot and inventory-health snapshot evidence.
- Decimal-only pricing calculations and ISO currency validation.
- Break-even hard block, minimum-margin approval, critical-stockout discount block and stale-data degradation.
- Policy Engine integration and action-hash-bound HITL approval.
- Tenant-scoped persistent price recommendations with Alembic migration and PostgreSQL RLS registration.
- Explicit pricing guardrail Eval dataset.
- Legacy `/api/price/monitor` compatibility with automatic platform writes disabled when profit evidence is absent.

## Deterministic projection

```text
fixed_unit_cost
= (landed cost + fulfillment fees + storage cost + advertising cost + other variable cost)
  / units

variable_rate
= actual referral fees / revenue
+ actual return loss / revenue
+ configured exchange-rate buffer

break_even_floor
= max(snapshot break-even price, fixed_unit_cost / (1 - variable_rate))

minimum_margin_floor
= fixed_unit_cost / (1 - variable_rate - minimum_margin_rate)

projected_unit_profit
= proposed price * (1 - variable_rate) - fixed_unit_cost

projected_margin
= projected_unit_profit / proposed price
```

The projection assumes current per-unit advertising and other fixed variable costs remain unchanged. This assumption is recorded as a product limitation and must not be presented as a guaranteed outcome.

## Policy rules

- Operator-requested price below break-even: permanently blocked; no approval request can override it.
- Price decrease during critical stockout risk: blocked.
- Projected margin below tenant minimum but above break-even: single-person approval.
- Missing, stale or degraded profit/inventory evidence: approval required and `automation_eligible=false`.
- A price change within 3% may be marked policy-allow only when confidence is at least 0.95 and all evidence is complete/fresh.
- The current API does not execute even policy-allow recommendations; it returns `executed=false` until controlled Amazon write rollout is implemented.

## API

- `POST /api/price/recommendations/calculate`
- `GET /api/price/recommendations/persistent`
- Legacy: `POST /api/price/monitor`
- Legacy: `GET /api/price/recommendations`

The new API requires persisted snapshot IDs. Cross-tenant snapshot IDs return not found. Approval is bound to the exact action hash; changing price, SKU, marketplace or profit constraints invalidates the approval.

## Eval

`evals/pricing_guardrails.json` covers:

- Below break-even.
- Below minimum margin.
- Critical-stockout discount.
- Stale/degraded evidence.
- Complete, fresh, small price change.

The Eval is executed by `tests/test_profit_constrained_pricing.py` and therefore runs in CI through the existing pytest job.

## Legacy monitor behavior

The competitor-only monitor remains available for compatibility, but every result now declares missing profit, inventory and cost evidence, sets `automation_eligible=false`, records a degraded reason and never calls the platform update path. It is not a production pricing workflow.

## Rollback

- Run `alembic downgrade 0005` before production reliance.
- This removes persistent price recommendations but preserves cost, profit, inventory, issues and daily briefs.
- Revert the new pricing service/routes and restore the previous Policy Engine branch if application rollback is required.
- No new endpoint executes an Amazon price write, so no external compensation is required.
