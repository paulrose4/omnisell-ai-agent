# OmniSell Interview Package Index

| Artefact | Path | Purpose |
| --- | --- | --- |
| Start here | `docs/interview/START_HERE.md` | 90-second pitch, 5-minute demo, 15-minute architecture path, and mandatory disclosures. |
| One-page project overview | `docs/interview/ONE_PAGE_PROJECT_OVERVIEW.md` | Elevator pitch, who / what / why / success criteria. |
| API + demo scope | `docs/interview/API_AND_DEMO_SCOPE.md` | Which routes / pages the candidate owns, which are out of scope. |
| Schema repair latency incident | `docs/interview/INCIDENT_01_SCHEMA_REPAIR_LATENCY.md` | Real-provider schema repair latency and the resulting no-go boundary. |
| Approval execution binding incident | `docs/interview/INCIDENT_02_APPROVAL_EXECUTION_BINDING.md` | Walkthrough of the immutability + replay defence. |
| Failover 504 incident | `docs/interview/incidents/INCIDENT_01_PROVIDER_504_RECOVERY.md` | Recovery from a real provider outage using bounded failover. |
| Schema repair backlog incident | `docs/interview/incidents/INCIDENT_03_SCHEMA_REPAIR_BACKLOG.md` | Honest accounting of remaining provider-quality work. |
| Personal contribution matrix | `docs/interview/CONTRIBUTION_MATRIX.md` | What the candidate designed and how it was verified. |
| Threat model | `docs/security/THREAT_MODEL.md` | Assets, trust boundaries, attack paths, controls, residual risk. |
| Decision records | `docs/decisions/0001-no-autogen-groupchat.md`, `0002-deterministic-business-math.md`, `0003-read-only-pilot-profile.md` | Three ADRs with context / decision / alternatives / consequences. |
| Demo runbook | `docs/INTERVIEW_DEMO_RUNBOOK.md` | Step-by-step `golden_demo` walkthrough, four Mermaid diagrams. |
| Schema fingerprint evidence | `docs/evidence/migrations/0023_postgresql16_schema_rls_fingerprint.md` (and `.json`) | PostgreSQL 16 roundtrip + RLS authoritative evidence. |
| OpenAPI snapshot | `docs/evidence/openapi/full-development-v1.json` | Versioned OpenAPI for the current release profile. |
| Route inventory | `docs/evidence/routes/route-inventory-v1.json`, `bff-route-inventory-v1.json` | Authoritative list of backend and BFF endpoints. |
| Agent evaluation report | `docs/evidence/agent-evals/EVALUATION_REPORT.md` (and `evaluation-report-v1.json`) | Mock / deterministic / real-provider metrics. |
| Agent contract evidence | `docs/evidence/agent-contracts/schema-size-v1-v2.json`, `PAYLOAD_COMPATIBILITY_SCAN.md` | Contract evolution + payload audit fixture. |
| Operator review template | `docs/evidence/agent-evals/operator-review-template-v1.csv` | Spreadsheet for the planned human reviewer sign-off. |
| Dated verification summary | `docs/evidence/verification/verification-summary-2026-08-10.md` | Public source of truth for 770+ tests and current external gates. |
| Interview release notes | `docs/releases/v0.1.0-interview.md` | Candidate release scope, verification, and known boundaries. |
| Optimisation plan | `docs/PROJECT_INTERVIEW_OPTIMIZATION_PLAN.md` | Master tracking document, refreshed after every PR. |

## Versioned evidence and external gaps

- Agent evaluation: [`../evidence/agent-evals/EVALUATION_REPORT.md`](../evidence/agent-evals/EVALUATION_REPORT.md).
- Golden demo: [`../INTERVIEW_DEMO_RUNBOOK.md`](../INTERVIEW_DEMO_RUNBOOK.md).
- PostgreSQL 16 migration and non-owner RLS evidence is versioned under `docs/evidence/migrations/`.
- 真实 Amazon 写入仍未执行，当前发布姿态保持 `read_only_pilot`。
- 真实模型质量、延迟、repair rate 与成本仍需重新完成受控评测。
- 真实 OIDC、具名 operator 签字和 2 至 4 周只读 Pilot 仍属于外部门禁。

## How to review in 30 minutes
1. Open `docs/interview/START_HERE.md`, then `docs/interview/ONE_PAGE_PROJECT_OVERVIEW.md`.
2. Skim `docs/INTERVIEW_DEMO_RUNBOOK.md` for the demo flow.
3. Run `pwsh scripts/start_local.ps1 -Profile golden_demo` and reproduce the
   golden stockout loop via `npm run test:e2e:golden`.
4. Read `docs/security/THREAT_MODEL.md` and the three ADRs.
5. Open `docs/interview/CONTRIBUTION_MATRIX.md` and the three incident writeups.
