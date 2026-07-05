# OmniSell AI — Policy Versioning

## Design Philosophy

Every safety-critical configuration in OmniSell AI is **versioned, auditable,
and testable**. This includes:

1. **Domain keyword trees** — which categories and keywords gate input
2. **Tool permission descriptors** — who can invoke what with which parameters
3. **Refund constraint rules** — what constitutes a refund execution
4. **HITL risk policies** — what risk level triggers human intervention
5. **Sandbox policies** — what code is allowed to execute
6. **Circuit breaker thresholds** — failure counts before tripping

## Why Policy Versioning Matters

In production AI systems, safety policies evolve over time. A security incident
post-mortem will ask: "What was the policy in effect at 14:32 UTC on July 5?"

Policy versioning answers this question with **audit-trail traceability**.

## Implementation Approach

### 1. Source-of-Truth in Code

All policies are defined as **Python dataclasses or constants** in versioned
source files. This means:

- Every policy change is a git commit with author, timestamp, and diff
- Policies can be code-reviewed like any other change
- `git blame` answers "who changed this and when"
- CI can run tests against the current policy state

```
git log --oneline safety/domain_guardrail.py
→ a1b2c3d "Expand keyword tree with payment and analytics categories"
→ e4f5g6h "Add refund bypass patterns to harmful patterns list"
→ i7j8k9l "Add SEA e-commerce categories to domain tree"
```

### 2. Audit Trail Binding

Every time a policy decision is made at runtime, the audit record includes
the relevant policy metadata:

```json
{
  "timestamp": "2026-07-05T06:32:00Z",
  "event_type": "domain_blocked",
  "details": {
    "strategy": "strict",
    "matched_paths": [],
    "harmful_hits": ["execute refund"]
  }
}
```

The `strategy` field tells us which guardrail tier was active. The
`matched_paths` and `harmful_hits` tell us exactly which policy rules
triggered. Combined with the git commit history, we have full traceability.

### 3. Policy Change Checklist

When modifying any safety policy, follow this checklist:

- [ ] Update the source file (e.g., `safety/domain_guardrail.py`)
- [ ] Add/update unit tests for the new policy behavior
- [ ] Update the module docstring with rationale
- [ ] If adding keywords, ensure both Chinese and English coverage
- [ ] Run the full test suite (`pytest tests/ -v`)
- [ ] Verify audit log format still valid
- [ ] Document the change in the commit message

### 4. Current Policy Versions

| Policy | File | Key Config | Default |
|--------|------|------------|---------|
| Domain Guardrail | `safety/domain_guardrail.py` | `GuardrailStrategy` | `moderate` |
| Domain Tree | `safety/domain_guardrail.py` | `DEFAULT_DOMAIN_TREE` | 7 categories, 20+ subcategories |
| General Business Terms | `safety/domain_guardrail.py` | `GENERAL_BUSINESS_TERMS` | 40 terms (CN+EN) |
| Harmful Patterns | `safety/domain_guardrail.py` | `HARMFUL_PATTERNS` | 28 patterns |
| Tool Permissions | `safety/tool_permissions.py` | `STANDARD_TOOL_PERMISSIONS` | 18 tool descriptors |
| Refund Keywords | `safety/refund_constraint.py` | `REFUND_KEYWORDS_CN`, `REFUND_KEYWORDS_EN` | 22 keywords |
| HITL Risk Levels | `safety/hitl.py` | `assess_risk()` | 4 tiers |
| Sandbox Policy | `sandbox/policy.py` | `ALLOWED_IMPORTS`, `BLOCKED_PATTERNS` | 21 allowed, 21 blocked |
| Circuit Breaker | `safety/circuit_breaker.py` | `failure_threshold=5`, `recovery_timeout=60` | - |

### 5. Environment-Specific Overrides

Some policies support runtime overrides via environment variables:

```bash
# Production: strict guardrail, longer recovery
OMNISELL_GUARDRAIL_STRATEGY=strict

# Staging: moderate, shorter recovery for faster iteration
OMNISELL_GUARDRAIL_STRATEGY=moderate

# Dev: permissive, fast fail for quick feedback
OMNISELL_GUARDRAIL_STRATEGY=permissive
```

These overrides are **not** a substitute for versioned policy changes — they
are deployment-level tuning knobs that should be documented in the deployment
configuration.

### 6. Migration Path

When changing policies, follow semantic versioning principles:

- **PATCH** (0.1.0 → 0.1.1): Add new keywords, expand allowed imports,
  increase circuit breaker thresholds (backward compatible)
- **MINOR** (0.1.0 → 0.2.0): Add new policy categories, introduce new
  risk levels, add new tool permission descriptors
- **MAJOR** (0.1.0 → 1.0.0): Change default strategy, remove allowed
  imports, tighten risk assessment boundaries (breaking changes)

### 7. Rollback Procedure

If a policy change causes unexpected blocking in production:

1. Set `OMNISELL_GUARDRAIL_STRATEGY=permissive` as immediate mitigation
2. Revert the git commit that introduced the change
3. Deploy the revert
4. Remove the environment override
5. Post-mortem: analyze blocked audit records to understand the impact

## Example: Adding a New Harmful Pattern

```python
# 1. Add to HARMFUL_PATTERNS in safety/domain_guardrail.py
HARMFUL_PATTERNS: list[str] = [
    ...existing patterns...
    "new attack vector",    # Added 2026-07-05: blocks novel injection technique
]

# 2. Add unit test
def test_permissive_blocks_new_attack_vector():
    guardrail = DomainGuardrail(strategy="permissive")
    result = guardrail.check("new attack vector targeting agent")
    assert result.is_allowed is False

# 3. Commit with descriptive message
# git commit -m "safety: add 'new attack vector' to harmful patterns"

# 4. Deploy, monitor audit logs for false positives
```

## Example: Adding a New Tool Permission

```python
# 1. Add to STANDARD_TOOL_PERMISSIONS in safety/tool_permissions.py
ToolPermission(
    name="new_tool",
    description="New tool for XYZ",
    operation_type=OperationType.WRITE,
    risk_level=RiskLevel.MEDIUM,
    allowed_callers=[CallerRole.SYNC_AGENT, CallerRole.HUMAN_OPERATOR],
    tenant_scope=TenantScope.SINGLE_TENANT,
    params=[ParamSpec("required_param", str, required=True)],
),

# 2. Add unit test for permission denial
def test_new_tool_blocked_for_cs_agent():
    registry = PermissionRegistry()
    register_standard_permissions(registry)
    result = registry.check("new_tool", caller=CallerRole.CS_AGENT, params={"required_param": "test"})
    assert result.allowed is False

# 3. Commit, deploy, monitor
```
