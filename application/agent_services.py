from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from agents.contract_adapters import LegacyAgentPayloadError, adapt_persisted_analysis
from agents.registry import AdvisorRegistry
from agents.workflow_definitions import workflow_definition


class AgentRunService:
    def __init__(
        self,
        *,
        store: Any,
        workflow: Any,
        registry: AdvisorRegistry,
        evidence_collectors: dict[str, Any],
        action_resume: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.store = store
        self.workflow = workflow
        self.registry = registry
        self.evidence_collectors = dict(evidence_collectors)
        self.action_resume = action_resume

    def get_run(self, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.store.get_run(tenant_id=tenant_id, run_id=run_id)
        return None if row is None else _serialize_run(row)

    def list_runs(self, *, tenant_id: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [_serialize_run(row) for row in self.store.list_runs(tenant_id=tenant_id, status=status, limit=limit)]

    async def resume(self, *, tenant_id: str, run_id: str, actor_id: str, correlation_id: str) -> dict[str, Any]:
        row = self.store.get_run(tenant_id=tenant_id, run_id=run_id)
        if row is None:
            raise KeyError(f"Agent run not found: {run_id}")
        if row.workflow_type in self.evidence_collectors:
            if row.status != "failed":
                return {"run_id": row.id, "status": row.status, "state": row.state or {}}
            if (row.state or {}).get("retryable") is False:
                return {
                    "run_id": row.id,
                    "status": row.status,
                    "retryable": False,
                    "error_category": getattr(row, "error_category", None),
                    "state": row.state or {},
                }
            retry_count = (
                self.store.claim_retry_attempt(tenant_id=tenant_id, run_id=run_id)
                if hasattr(self.store, "claim_retry_attempt")
                else self.store.increment_retry(tenant_id=tenant_id, run_id=run_id)
            )
            request = dict((row.state or {}).get("request") or {})
            request["idempotency_key"] = f"retry:{run_id}:{retry_count}"
            result = await self.workflow.run_advisory(
                tenant_id=tenant_id,
                actor_id=actor_id,
                workflow_type=row.workflow_type,
                correlation_id=correlation_id,
                request=request,
                evidence_collector=self.evidence_collectors[row.workflow_type],
                advisor=self.registry.get(row.workflow_type),
            )
            return {**result, "retried_from_run_id": run_id}
        if self.action_resume is None:
            return {"run_id": row.id, "status": row.status, "state": row.state or {}}
        return await self.action_resume(
            tenant_id=tenant_id, run_id=run_id, actor_id=actor_id, correlation_id=correlation_id
        )


def _serialize_run(row: Any) -> dict[str, Any]:
    state = row.state or {}
    dispatch = dict(state.get("dispatch") or {})
    definition = workflow_definition(row.workflow_type)
    raw_analysis = getattr(row, "analysis_result", {}) or {}
    source_schema_version = (
        str(raw_analysis.get("schema_version") or getattr(row, "schema_version", None) or "") or None
    )
    analysis_status = "empty"
    analysis_result: dict[str, Any] = {}
    if raw_analysis:
        try:
            analysis_result = adapt_persisted_analysis(
                workflow_type=row.workflow_type,
                payload=raw_analysis,
            )
            analysis_status = "adapted" if source_schema_version == "1.0" else "validated"
        except LegacyAgentPayloadError:
            analysis_status = "legacy_incompatible"
    return {
        "run_id": row.id,
        "tenant_id": row.tenant_id,
        "actor_id": row.actor_id,
        "workflow_type": row.workflow_type,
        "workflow_name": definition.display_name,
        "workflow_class": definition.workflow_class,
        "generation_method": definition.generation_method,
        "amazon_write_capability": definition.amazon_write_capability,
        "agent_type": getattr(row, "agent_type", None),
        "correlation_id": row.correlation_id,
        "idempotency_key": getattr(row, "idempotency_key", None),
        "status": row.status,
        "prompt_version": row.prompt_version,
        "schema_version": getattr(row, "schema_version", None),
        "model": row.model,
        "token_usage": row.token_usage,
        "estimated_cost": str(row.estimated_cost or Decimal("0")),
        "model_usage": getattr(row, "model_usage", {}) or {},
        "evidence_manifest": getattr(row, "evidence_manifest", {}) or {},
        "analysis_result": analysis_result,
        "analysis_result_status": analysis_status,
        "analysis_result_source_schema_version": source_schema_version,
        "error_category": getattr(row, "error_category", None),
        "retry_count": int(getattr(row, "retry_count", 0) or 0),
        "dispatch_task_id": dispatch.get("task_id"),
        "dispatch_task_status": dispatch.get("status"),
        "state": state,
        "created_at": _iso_utc(row.created_at),
        "updated_at": _iso_utc(row.updated_at),
    }


def _iso_utc(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()
