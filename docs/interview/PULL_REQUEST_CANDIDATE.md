# OmniSell Interview Candidate Release Pull Request

## Base / Head
- Base branch: origin/main @ 822d6e0
- Head branch: codex/agent-production-hardening @ $(git rev-parse HEAD)
- Ahead of base by **26 commits** (8 new reviewable commits layered on the prior hardening series)
- Working tree: 0 untracked files, 0 modified tracked files

## Commits included in this candidate
- 3e1bd47 chore(repo): ignore frontend Playwright generated artefacts   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:38:58 +0800 - b4f1f42 test(agents): cover typed legacy payload adapters   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:38:30 +0800 - 9511647 docs(interview): refresh optimisation plan and demo runbook   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:38:19 +0800 - f555a54 feat(console): wire golden stockout demo through Playwright and sign-in   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:37:11 +0800 - 5b775ec feat(local): ship golden-demo stack with mock OIDC and worker coverage   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:36:54 +0800 - 1ec9f7d chore(audit): scan agent payload compatibility and persist migration fingerprint   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:36:33 +0800 - 0ae2c15 feat(agents): type agent contracts and tighten failover budget   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:36:15 +0800 - 9919c74 feat(console): expose release profiles and versioned route inventory   Author: paulrose4 <1513582348@qq.com>   Date: 2026-08-06 20:35:57 +0800

## How to review
1. Fetch and check out the branch:
   git fetch origin && git checkout codex/agent-production-hardening
2. Bootstrap the environment:
   .venv-clean\Scripts\python -m pip install -r requirements.runtime.txt -r requirements.dev.txt
3. Run static gates and the focused interview surface:
   uff check . && pyright agents api safety workflows application model_gateway
   pytest -q tests/test_release_profiles.py tests/test_operational_route_contracts.py tests/test_agent_contract_adapters.py tests/test_agent_contracts.py tests/test_migrations.py tests/test_postgres_rls.py
4. Reproduce the golden stockout loop end-to-end:
   pwsh scripts/start_local.ps1 -Profile golden_demo
   $env:PLAYWRIGHT_BROWSER_CHANNEL='chrome'; npm run test:e2e:golden

## Verification artefacts
- docs/evidence/openapi/full-development-v1.json
- docs/evidence/routes/route-inventory-v1.json
- docs/evidence/routes/bff-route-inventory-v1.json
- docs/evidence/agent-contracts/schema-size-v1-v2.json
- docs/evidence/agent-contracts/PAYLOAD_COMPATIBILITY_SCAN.md
- docs/evidence/migrations/0023_postgresql16_schema_rls_fingerprint.json
- docs/evidence/agent-evals/EVALUATION_REPORT.md

## External / non-production gaps explicitly retained
- Real Amazon SP-API writes remain locked_read_only_pilot per release profile.
- Real LLM provider quality, real OIDC providers and human operator adoption
  rate still require an external environment.
