from __future__ import annotations

import asyncio
from decimal import Decimal

from api.task_queue import MetricsRegistry
from observability.telemetry import trace_attributes


def test_agent_metrics_include_status_latency_usage_and_safety_counters() -> None:
    metrics = MetricsRegistry()

    async def scenario() -> dict:
        await metrics.record_agent_run(
            workflow_type="operations_brief_analysis",
            status="succeeded",
            latency_ms=100,
            total_tokens=120,
            estimated_cost=Decimal("0.0123"),
            repair_attempted=True,
            first_pass_schema_success=False,
            schema_failure_categories=("missing", "literal_error"),
            model_initial_latency_ms=70,
            model_repair_latency_ms=30,
            fallback_used=True,
            retry=False,
        )
        await metrics.record_agent_run(
            workflow_type="customer_case_analysis",
            status="failed",
            latency_ms=300,
            total_tokens=20,
            estimated_cost=Decimal("0.0010"),
            repair_attempted=False,
            first_pass_schema_success=True,
            model_initial_latency_ms=250,
            model_repair_latency_ms=0,
            evidence_validation_failed=True,
            retry=True,
        )
        return await metrics.snapshot()

    snapshot = asyncio.run(scenario())

    assert snapshot["agent_runs_by_status"] == {"succeeded": 1, "failed": 1}
    assert snapshot["agent_runs_by_workflow"] == {
        "operations_brief_analysis": 1,
        "customer_case_analysis": 1,
    }
    assert snapshot["agent_latency_p50_ms"] == 100
    assert snapshot["agent_latency_p95_ms"] == 300
    assert snapshot["agent_total_tokens"] == 140
    assert snapshot["agent_estimated_cost"] == "0.0133"
    assert snapshot["agent_schema_repairs"] == 1
    assert snapshot["agent_first_pass_schema_successes"] == 1
    assert snapshot["agent_first_pass_schema_success_rate"] == "0.5000"
    assert snapshot["agent_schema_failure_categories"] == {"literal_error": 1, "missing": 1}
    assert snapshot["agent_model_initial_latency_p95_ms"] == 250
    assert snapshot["agent_model_repair_latency_p95_ms"] == 30
    assert snapshot["agent_evidence_validation_failures"] == 1
    assert snapshot["agent_retries"] == 1
    assert snapshot["agent_failovers"] == 1


def test_trace_attributes_drop_high_cardinality_business_identifiers() -> None:
    attributes = trace_attributes(
        tenant_id="tenant-a",
        actor_id="operator-1",
        run_id="run-1",
        correlation_id="corr-1",
        resource_id="ORDER-1",
        selling_partner_id="SELLER-1",
        seller_sku="SKU-1",
        idempotency_key="secret-replay-key",
        http_route="/listings/items/SELLER-1/SKU-1",
        workflow_type="listing_optimization_analysis",
        agent_type="listing_optimization_advisor",
        model="gpt-compatible",
        status="succeeded",
        risk_level="low",
        retry_count=0,
    )

    assert attributes == {
        "workflow_type": "listing_optimization_analysis",
        "agent_type": "listing_optimization_advisor",
        "model": "gpt-compatible",
        "status": "succeeded",
        "risk_level": "low",
        "retry_count": 0,
    }
