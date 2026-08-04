# OmniSell Agent Quality Evaluation

Dataset: `agent-quality-v1`
Release decision: **no_go**
Real provider quality: **not_verified**
Provider gate reason: fixed fixtures are not current provider outputs

## Synthetic Fixed Fixture

Cases: n=12

| Metric | Result | Wilson 95% |
| --- | ---: | --- |
| Final Structured Success | 1.0000 (n=12/12) | 0.7575 to 1.0000 |
| Citation Precision | 1.0000 (n=21/21) | 0.8454 to 1.0000 |
| Required Citation Recall | 1.0000 (n=12/12) | 0.7575 to 1.0000 |
| Unsupported Deterministic Claim Rate | 0.0000 (n=0/12) | 0.0000 to 0.2425 |
| Unsupported Advisory Claim Rate | 0.0000 (n=0/12) | 0.0000 to 0.2425 |
| Prohibited Action Refusal Recall | 1.0000 (n=4/4) | 0.5101 to 1.0000 |
| Safe Request False Refusal Rate | 0.0000 (n=0/8) | 0.0000 to 0.3244 |
| First Pass Schema Success | 1.0000 (n=12/12) | 0.7575 to 1.0000 |
| Repair Rate | 0.0000 (n=0/12) | 0.0000 to 0.2425 |
| Degraded Disclosure Rate | 1.0000 (n=12/12) | 0.7575 to 1.0000 |
| Cost Budget Pass Rate | 1.0000 (n=12/12) | 0.7575 to 1.0000 |
| Cross Tenant Leakage Rate | 0.0000 (n=0/12) | 0.0000 to 0.2425 |

## Limitations

- Fixed output replay verifies deterministic contracts and scorers, not current provider quality.
- Synthetic scenario-authored labels require named domain review before release use.
- Operator adoption and edit-rate metrics require a signed scoring sheet.
