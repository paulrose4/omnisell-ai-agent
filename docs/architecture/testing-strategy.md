# OmniSell AI — Testing Strategy

## Test Pyramid

```
           ┌─────────┐
           │   E2E   │  ~1 test: full API gateway flow
           │ (slow)  │  health→sync→customer→price→audit
           ├─────────┤
           │Integrat.│  ~15 tests: agent behavior, tool chains
           │(medium) │  safety guards, RAG pipeline, HITL
           ├─────────┤
           │  Unit   │  ~35 tests: individual functions
           │ (fast)  │  guardrail, permissions, policy
           └─────────┘
```

## Layer Breakdown

### Unit Tests (`tests/`)

Tests individual functions and classes in isolation.
No external dependencies; all integrations mocked.

| Test File | Coverage |
|-----------|----------|
| `tests/test_domain_guardrail.py` | `DomainGuardrail`, all 3 strategies, preprocessing, keyword matching, audit logging, confidence scoring |
| `tests/test_safety.py` | `AuditLogger`, `CircuitBreaker`, `HITLManager`, `SandboxPolicy`, `safe_tool_execution` |

**Unit test characteristics:**
- Fast (< 200ms each)
- No network, no Docker, no LLM
- Run on every git push
- Fail = code bug

Example unit test pattern:
```python
def test_strict_allows_chinese_ecommerce_request():
    guardrail = DomainGuardrail(strategy="strict")
    result = guardrail.check("请帮我上架这个商品并设置价格和库存")
    assert result.is_allowed is True
    assert result.confidence > 0.0
```

### Integration Tests (`tests/`)

Tests agent behavior with simulated dependencies.
Mock platform responses, fake translators, in-memory state.

| Test File | Coverage |
|-----------|----------|
| `tests/test_agents.py` | Agent tool registration, RAG pipeline (Chinese/Thai), price monitoring, HITL price-drop blocking |
| `tests/test_async_ops.py` | Async task queue, metrics registry |
| `tests/test_multimodal.py` | Customer image API route |

**Integration test characteristics:**
- Medium speed (< 1s each)
- Fake dependencies (no real Docker/LLM)
- Run on every PR
- Fail = integration contract broken

Example integration test pattern:
```python
def test_chinese_customer_return_question_uses_rag(monkeypatch):
    monkeypatch.setattr(rag_tool, "search_knowledge_base", fake_search)
    result = rag_tool.answer_customer_question("如何申请退货？")
    assert result["grounded"] is True
    assert "人工审核" in result["answer"]
```

### E2E Tests (`tests/test_e2e.py`)

Tests the entire API gateway flow end-to-end.
Uses FastAPI TestClient with ASGI transport.

| Test | Flow |
|------|------|
| `test_api_gateway_e2e_flow` | Health check → Product sync → Multilingual customer service → Price monitoring → Approval query → Audit query |

**E2E test characteristics:**
- Slowest (~2-3s)
- Fake OAuth, fake platform API responses
- Run on deployment
- Fail = production would break

## Test Infrastructure

### Fixtures and Helpers

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Project root in `sys.path` |

### Mocking Strategy

- **`monkeypatch`** (pytest built-in): Preferred for function/method replacement
- **Fake classes**: `FakeDockerClient`, `FakeContainers`, `FakeContainer` for sandbox
- **Fake functions**: `fake_translate_text`, `fake_answer_customer_question` for tools
- **`tmp_path`**: Isolated file system for audit log tests

### Running Tests

```bash
# All tests
pytest tests/ -v

# Unit only (fast)
pytest tests/test_domain_guardrail.py tests/test_safety.py -v

# Integration only
pytest tests/test_agents.py tests/test_async_ops.py -v

# E2E only
pytest tests/test_e2e.py -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

## Adding New Tests

### For a new safety module (e.g., `safety/refund_constraint.py`):

1. **Unit tests** — Test `RefundGuard.detect_refund_language()`, `classify_action()`, `check()` with various inputs
2. **Integration test** — Test `safe_tool_execution` with a tool that triggers refund guard
3. **Update conftest** — If new fixtures needed

### For a new agent tool:

1. **Unit test** — Test the tool function in isolation
2. **Integration test** — Test the tool registered in an agent with `monkeypatch` for dependencies
3. **E2E test** — If it's a new API route, add to `test_e2e.py`

## Test Naming Convention

- `test_{feature}_{scenario}_{expected_behavior}`
- Examples:
  - `test_strict_allows_chinese_ecommerce_request`
  - `test_hitl_rules_auto_pending_critical_and_expiry`
  - `test_price_drop_above_10_percent_blocks_without_hitl`
  - `test_refund_execution_blocked_escalates_to_hitl`
