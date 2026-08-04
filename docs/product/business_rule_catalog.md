# Business Rule Catalog

| Rule ID | Domain | Condition | Result | Safety rationale |
|---|---|---|---|---|
| PROF-001 | Profit | Required cost input missing | Snapshot degraded; list `missing_inputs`; no trusted margin | Missing must not become zero |
| PROF-002 | Profit | Currency mismatch without explicit FX rate | Block calculation | Prevent false profit |
| PROF-003 | Profit | Gross revenue <= 0 | Margin and Break-even ACoS are null | Avoid division and misleading ratios |
| PROF-004 | Profit | Suggested price < break-even price | Permanently block price action | Guaranteed negative contribution under assumptions |
| PROF-005 | Profit | Suggested margin < tenant minimum | Require approval | Business guardrail |
| PROF-006 | Profit | Cost profile stale/incomplete | Recommendation only; no automatic pricing | Evidence quality guardrail |
| COST-001 | Cost | Overlapping effective profile for same tenant/marketplace/SKU | Reject or explicitly supersede | Deterministic version selection |
| COST-002 | Cost | Negative cost or rate outside [0,1) | Reject row | Invalid economics |
| COST-003 | Cost import | Duplicate idempotency key with same checksum | Return previous result | Retry safety |
| COST-004 | Cost import | Duplicate idempotency key with different checksum | Conflict | Prevent hidden replacement |
| COST-005 | Cost import | CSV formula prefix in textual field | Reject row | CSV injection defense |
| DATA-001 | Data | Source is mock | Mark `mock`; exclude from production verification | Truthfulness |
| DATA-002 | Data | Source age exceeds threshold | Mark stale and reduce confidence | Freshness transparency |
| TENANT-001 | Security | Tenant attempts cross-tenant read/write | Deny and audit | SaaS isolation |
| POLICY-001 | Execution | Refund/payment reversal | Permanent block | Existing hard safety boundary |
| POLICY-002 | Execution | Bulk price/Listing or policy threshold change | Two-person approval | High blast radius |
| ACTION-001 | Execution | Approved action parameters change | Invalidate approval | Action-hash binding |
| ACTION-002 | Outcome | Guardrail metric worsens materially | Stop rollout and propose rollback | Controlled experimentation |

Rules are versioned. Thresholds not explicitly confirmed by operators remain configuration defaults and must not be described as Amazon standards.

## Inventory rules

| Rule ID | Domain | Condition | Result | Safety rationale |
|---|---|---|---|---|
| INV-001 | Inventory | Amazon fulfillable quantity used | Require `available_is_net_of_reserved=true` | Prevent double subtraction of reserved stock |
| INV-002 | Forecast | Any sales window missing | Normalize available weights; degrade and disable automation | Preserve visibility without overstating confidence |
| INV-003 | Forecast | Sales velocity is zero | No stockout date or reorder; classify dormant/unknown | New or suppressed demand cannot be inferred safely |
| INV-004 | Inventory | Snapshot older than 6 hours | Mark stale; disable automation | Avoid ordering on old stock |
| INV-005 | Forecast | Promotion/spike/stockout distortion/recent price or ad change | Add warning and reduce confidence | Moving average is not representative |
| INV-006 | Replenishment | Raw demand > 0 | Apply MOQ then round up to case pack | Supplier feasibility |
| INV-007 | Policy | Existing inventory policy changes | Require different-user HITL approval bound to action hash | Supply-chain blast-radius control |
| INV-008 | Execution | Replenishment recommendation produced | Recommendation only | No supplier/order integration in Milestone 2 |

## Operational issue rules

| Rule ID | Domain | Condition | Result | Safety rationale |
|---|---|---|---|---|
| OPS-001 | Issue | Same tenant/type/marketplace/SKU detected repeatedly | Update canonical issue and increment occurrence count | Prevent alert floods and duplicate work |
| OPS-002 | Issue | Resolved/dismissed condition is detected again | Reopen canonical issue with current evidence | Do not hide recurrence |
| OPS-003 | Severity | Inventory coverage <= 7 days or contribution margin <= -10% | Rank critical | Initial prioritization default; requires operator confirmation |
| OPS-004 | Evidence | Source snapshot is missing, stale or degraded | Create data-quality issue and keep recommendation non-automatic | Data weakness must be visible |
| OPS-005 | Action | Milestone 3 recommendation generated | `recommendation_only`, `automation_eligible=false` | No platform/supplier execution contract exists yet |
| OPS-006 | Tenant | Source snapshot belongs to another tenant | Return not found and create no issue | Avoid cross-tenant existence disclosure |
| OPS-007 | State | Requested issue/action transition is not allowed | Reject with conflict | Preserve auditable lifecycle semantics |

## Daily operations brief rules

| Rule ID | Domain | Condition | Result | Safety rationale |
|---|---|---|---|---|
| BRIEF-001 | Priority | Multiple active issues | Sort by severity, quantified impact, then latest detection | Deterministic queue ordering |
| BRIEF-002 | Currency | Issues use different currencies | Aggregate separately by ISO currency | Prevent invalid cross-currency totals |
| BRIEF-003 | Currency | Impact exists without known currency | Count as unallocated; do not assign USD | Truthful financial reporting |
| BRIEF-004 | Listing | Listing source observation time missing | Confidence 0.40 and freshness-unknown degradation | Assessment time is not platform freshness |
| BRIEF-005 | Listing | Suppressed/error status or required field missing | Create Listing quality issue and recommendation-only action | No automatic publication from incomplete evidence |
| BRIEF-006 | Dashboard | No persisted brief exists | Return not found and request explicit generation | Do not fabricate an empty healthy store |
| BRIEF-007 | Execution | Brief or dashboard is generated/read | No platform side effect | Reporting must not bypass policy or approval |

## Profit-constrained pricing rules

| Rule ID | Domain | Condition | Result | Safety rationale |
|---|---|---|---|---|
| PRICE-001 | Price | Proposed price < deterministic break-even floor | Permanently block | Approval cannot make a guaranteed-loss price safe |
| PRICE-002 | Price | Projected margin < tenant minimum but price >= break-even | Require different-user HITL approval | Controlled business exception |
| PRICE-003 | Inventory | Critical stockout and proposed price decreases | Block | Discounting worsens availability and lost-sales risk |
| PRICE-004 | Evidence | Profit snapshot older than 24h or inventory older than 6h | Disable automation and require approval | Pricing must use fresh evidence |
| PRICE-005 | Evidence | Snapshot degraded or required inputs missing | Disable automation and require approval | Missing values cannot silently become zero |
| PRICE-006 | Policy | Complete/fresh change <= 3% with confidence >= 0.95 | Policy may allow; API remains non-executing | Separate eligibility from rollout feature flag |
| PRICE-007 | Approval | Approved parameters or evidence change | Reject approval hash | Prevent post-approval mutation |
| PRICE-008 | Legacy | Competitor-only monitor has no profit evidence | Mark degraded and prohibit automatic write | Preserve compatibility without unsafe execution |

## Listing quality and experiment rules

| Rule ID | Domain | Condition | Result | Safety rationale |
|---|---|---|---|---|
| LIST-001 | Listing quality | Deterministic score below 80 or section issue exists | Open tenant-scoped quality issue and propose structured experiment | Quality advice remains inspectable and evidence-based |
| LIST-002 | Listing source | Source observation timestamp missing | Reduce confidence; automation remains disabled | Unknown freshness cannot authorize a write |
| LIST-003 | Listing experiment | Exact proposed changes have no approved action hash | Keep `awaiting_approval` | Prevent unreviewed publication |
| LIST-004 | Listing experiment | Evaluation requested before observation end | Block evaluation | Prevent premature conclusions |
| LIST-005 | Listing experiment | Relative conversion decline <= -10% | Recommend rollback | Protect commercial outcome |
| LIST-006 | Listing experiment | Return-rate increase >= 0.02 | Recommend rollback | Protect customer and cost outcome |
| LIST-007 | Listing experiment | Before/after outcome metrics incomplete | Mark `inconclusive` | Missing evidence must not become success |
| LIST-008 | Listing execution | Experiment approved in Milestone 6 | Persist approved observation only; do not call Amazon | Separate approval from controlled platform execution |