# Golden Mock Operations Agent Pilot

## Purpose

The Golden Mock tenant validates the OmniSell operating loop before a real Amazon Draft application is connected. All generated records are synthetic and must not be treated as verified Amazon data.

## Seeded scenarios

- Healthy inventory baseline.
- Critical stockout risk.
- Excess inventory.
- Negative contribution profit.
- Stale inventory evidence.
- Missing cost evidence.

The seed creates six cost profiles, six versioned inventory policies, deterministic profit and inventory snapshots, operational issues, recommended actions and a current daily operations brief.

## Operator workflow

1. Open the operations dashboard and confirm the Golden Mock warning is visible.
2. Review the non-zero issues and SKU health cards.
3. Open Agent monitoring and select **Generate today's operations recommendations**.
4. Review the persisted state transitions and evidence.
5. Use a different approver account to approve or reject high-risk recommendations.
6. Confirm the Agent Run resumes from `awaiting_approval` and reaches `succeeded` only after all required approvals are complete.
7. Treat every approved result as manual-only Seller Central work.

## Safety invariants

- `AMAZON_WRITES_ENABLED=false` remains required.
- `AMAZON_PILOT_READ_ONLY=true` remains required.
- The operations executor returns `amazon_write_performed=false` for every action.
- High-risk recommendations create tenant-scoped SQL HITL requests.
- The requester cannot approve their own request.
- Approval resumes the original persisted Agent Run using its action hash and action index.

## Local seed command

```bash
PYTHONPATH=/app python scripts/seed_golden_mock_tenant.py
```

The E2E migration container runs this command automatically after migrations, PostgreSQL RLS role configuration and tenant bootstrap.

## Remaining external verification

- A real LLM endpoint is not required for the deterministic pilot planner; model-backed planning still requires an available model gateway and evaluation suite.
- Real Amazon pagination, throttling, token revocation and Seller Central sampling remain Draft application verification gates.
- Golden Mock calculations validate the control flow, not real seller economics.
