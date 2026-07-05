# OmniSell AI — Layered Architecture

## Overview

OmniSell AI follows a **layered, defense-in-depth architecture** designed for
production e-commerce AI agent operations. Every layer has a clear
responsibility, well-defined interfaces, and built-in safety controls.

```
┌─────────────────────────────────────────────────────────────────┐
│                     API Gateway (FastAPI)                       │
│  api/main.py, api/middleware.py, api/routes/*                   │
│  Rate limiting, CORS, OAuth, request telemetry                  │
├─────────────────────────────────────────────────────────────────┤
│                     Agent Orchestration                         │
│  agents/group_chat.py, agents/base.py                           │
│  Multi-agent team: SyncAgent, CS Agent, PriceAgent              │
├─────────────────────────────────────────────────────────────────┤
│                     Safety Layer (Defense-in-Depth)             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │DomainGuardrail│→│ToolPermissions│→│ RefundGuard  │            │
│  │ (Input filter)│ │(Access control)│ │(Hard boundary)│           │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│  ┌──────────────┐ ┌──────────────┐                             │
│  │ HITLManager  │ │CircuitBreaker│                             │
│  │ (Approval)   │ │ (Resilience) │                             │
│  └──────────────┘ └──────────────┘                             │
├─────────────────────────────────────────────────────────────────┤
│                     Domain Services                             │
│  agents/tools/ — platform_api, price_scraper, rag_tool,         │
│  translator, vision_tool                                        │
├─────────────────────────────────────────────────────────────────┤
│                     Infrastructure                              │
│  sandbox/ — Docker-based code execution, policy enforcement      │
│  rag/    — Chroma vector DB, embedding, retrieval               │
│  safety/ — audit logging, circuit breaker, HITL                 │
├─────────────────────────────────────────────────────────────────┤
│                     External Systems                            │
│  Mock Platform (mock_platform/), vLLM, ChromaDB                 │
└─────────────────────────────────────────────────────────────────┘
```

## Layer Responsibilities

### 1. API Gateway Layer (`api/`)

| Module | Responsibility |
|--------|---------------|
| `api/main.py` | App factory, middleware wiring, startup/shutdown hooks |
| `api/middleware.py` | Rate limiting, request telemetry |
| `api/security.py` | API key authentication |
| `api/oauth_client.py` | OAuth2 client for platform API |
| `api/routes/admin_routes.py` | Health check, audit query, approval management |
| `api/routes/product_routes.py` | Product sync, translation endpoints |
| `api/routes/customer_routes.py` | Customer Q&A via RAG |
| `api/routes/price_routes.py` | Price monitoring, competitor analysis |
| `api/routes/rag_routes.py` | Knowledge base management |
| `api/routes/task_routes.py` | Async task management |

**Design principle**: The API layer is a thin routing and authentication
layer. Business logic lives in agents and tools.

### 2. Agent Orchestration Layer (`agents/`)

| Module | Responsibility |
|--------|---------------|
| `agents/group_chat.py` | Multi-agent team creation, domain guardrail gating |
| `agents/base.py` | `safe_tool_execution` decorator with all safety layers |
| `agents/cs_agent.py` | Customer service agent (RAG-grounded, multilingual) |
| `agents/sync_agent.py` | Operations agent (product sync, SEO optimization) |
| `agents/price_agent.py` | Risk-control agent (competitor monitoring, HITL approvals) |

**Design principle**: Each agent has a focused role and system prompt.
Safety constraints are injected at agent creation time.

### 3. Safety Layer (`safety/`)

The safety layer implements **defense-in-depth** in a specific order:

```
User Input
    │
    ▼
┌─────────────────────┐
│ 1. DomainGuardrail   │ ← Bilingual keyword tree filters input
│    (pre-runtime)     │    before any agent processing
└────────┬────────────┘
         │ allowed
         ▼
┌─────────────────────┐
│ 2. ToolPermissions   │ ← Validates caller role, tenant scope,
│    (per-invocation)  │    parameter types, and required approvals
└────────┬────────────┘
         │ authorized
         ▼
┌─────────────────────┐
│ 3. RefundGuard       │ ← Hard block on refund execution —
│    (per-action)      │    the AI safety boundary
└────────┬────────────┘
         │ non-refund or refund inquiry
         ▼
┌─────────────────────┐
│ 4. HITLManager       │ ← Risk assessment, approval escalation
│    (risk-based)      │    LOW→auto, MEDIUM→notify, HIGH/CRITICAL→pending
└────────┬────────────┘
         │ approved
         ▼
┌─────────────────────┐
│ 5. CircuitBreaker    │ ← Failure counting, OPEN/HALF_OPEN/CLOSED
│    (resilience)      │    stops cascading failures
└────────┬────────────┘
         │ closed
         ▼
┌─────────────────────┐
│ 6. AuditLogger       │ ← Append-only JSONL audit trail
│    (observability)   │    queryable by event_type, status, risk_level
└─────────────────────┘
```

### 4. Domain Services Layer (`agents/tools/`)

| Module | Responsibility |
|--------|---------------|
| `platform_api.py` | Mock platform CRUD (products, orders, OAuth) |
| `price_scraper.py` | Competitor price collection and recommendation |
| `rag_tool.py` | Knowledge base search and customer Q&A |
| `translator.py` | Language detection and translation |
| `vision_tool.py` | Customer image analysis via multimodal model |

### 5. Infrastructure Layer

| Module | Responsibility |
|--------|---------------|
| `sandbox/executor.py` | Docker-based code execution |
| `sandbox/policy.py` | Sandbox policy validation (imports, patterns) |
| `rag/chroma_client.py` | ChromaDB vector store client |
| `rag/retriever.py` | Embedding-based document retrieval |
| `safety/audit.py` | Append-only JSONL audit logger |

## Data Flow Example: Customer Refund Inquiry

```
1. Customer sends "我要退款" via API
       │
2. DomainGuardrail.check("我要退款")
   → matches order/refund keywords → allowed
       │
3. CS Agent processes the query
   → calls answer_customer_question(...)
       │
4. ToolPermissions.check("answer_customer_question", caller=cs_agent)
   → READ operation, LOW risk → allowed
       │
5. RefundGuard.check("answer_customer_question", payload)
   → contains refund language but is INQUIRY (not execution) → allowed
       │
6. RAG tool retrieves refund policy, agent responds with process
   → "进入订单详情页选择申请售后，填写原因并提交。以人工审核为准。"
       │
7. AuditLogger writes: event_type=operation, action=answer_customer_question, status=success
```

If instead the agent attempts a direct refund execution:

```
5'. RefundGuard.check("execute_refund", {order_id: "O123"})
    → ACTION TYPE: EXECUTION → HARD BLOCK
    → Raises RefundExecutionBlockedError
    → Escalates to HITL with CRITICAL risk level
    → Audit record: event_type=refund_blocked, status=blocked_pending_hitl
```

## Configuration

Key environment variables controlling architecture behavior:

| Variable | Purpose | Default |
|----------|---------|---------|
| `OMNISELL_GUARDRAIL_STRATEGY` | Domain guardrail strictness | `moderate` |
| `VLLM_BASE_URL` | LLM serving endpoint | `http://localhost:11434/v1` |
| `VLLM_MODEL_NAME` | Model name | `qwen2.5:3b` |
| `MOCK_PLATFORM_URL` | Mock commerce platform | `http://localhost:8002` |
| `OMNISELL_API_KEY` | API gateway key | (required in production) |
| `AUDIT_LOG_PATH` | Audit trail location | `logs/audit.jsonl` |
