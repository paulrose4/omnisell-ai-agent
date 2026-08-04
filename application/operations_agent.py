from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from workflows.engine import PersistentAgentWorkflow, PlannedAction, WorkflowPlan


class OperationsRecommendationPlanner:
    agent_type = "operations_recommendation_planner"
    prompt_version = "operations-recommendations-deterministic-v1"

    async def plan(self, *, request: dict[str, Any], schema: type[WorkflowPlan]) -> WorkflowPlan:
        actions = []
        for candidate in request.get("candidates", []):
            requires_review = candidate["risk_level"] in {"high", "critical"}
            actions.append(
                PlannedAction(
                    action="request_manual_execution" if requires_review else "generate_draft",
                    resource_id=candidate["recommended_action_id"],
                    arguments={
                        "issue_id": candidate["issue_id"],
                        "recommended_action_id": candidate["recommended_action_id"],
                        "seller_sku": candidate["seller_sku"],
                        "marketplace_id": candidate["marketplace_id"],
                        "title": candidate["title"],
                        "description": candidate["description"],
                        "parameters": candidate["parameters"],
                        "manual_execution_required": True,
                    },
                    evidence=candidate["evidence"],
                    expected_change={
                        "recommended_action_status": "approved_for_manual_execution"
                        if requires_review
                        else "draft_ready",
                        "amazon_write_performed": False,
                    },
                    confidence=float(candidate["confidence"]),
                )
            )
        return schema(summary=f"Prepared {len(actions)} tenant-scoped operations recommendations.", actions=actions)


class ReadOnlyOperationsToolExecutor:
    async def execute(
        self, action: PlannedAction, *, tenant_id: str, actor_id: str, correlation_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        if action.action not in {"generate_draft", "request_manual_execution"}:
            raise ValueError(f"Unsupported read-only operations action: {action.action}")
        return {
            "tenant_id": tenant_id,
            "recommended_action_id": action.arguments.get("recommended_action_id"),
            "status": "approved_for_manual_execution" if action.action == "request_manual_execution" else "draft_ready",
            "execution_mode": "manual_only",
            "amazon_write_performed": False,
            "seller_central_required": True,
            "idempotency_key": idempotency_key,
            "evidence": action.evidence,
            "expected_change": action.expected_change,
        }


class OperationsAgentService:
    def __init__(
        self, *, workflow: PersistentAgentWorkflow, workflow_store: Any, issue_repository: Any, action_repository: Any
    ) -> None:
        self.workflow = workflow
        self.workflow_store = workflow_store
        self.issue_repository = issue_repository
        self.action_repository = action_repository

    async def run_recommendations(
        self, *, tenant_id: str, actor_id: str, correlation_id: str, max_issues: int = 20
    ) -> dict[str, Any]:
        actionable_statuses = {"open", "acknowledged", "in_progress"}
        issues = [
            item
            for item in self.issue_repository.list_issues(tenant_id=tenant_id)
            if item.status in actionable_statuses
        ][:max_issues]
        actions_by_issue = {
            item.issue_id: item for item in self.action_repository.list_actions(tenant_id=tenant_id, status="proposed")
        }
        candidates = []
        for issue in issues:
            recommendation = actions_by_issue.get(issue.id)
            if recommendation is None:
                continue
            candidates.append(
                {
                    "issue_id": issue.id,
                    "recommended_action_id": recommendation.id,
                    "seller_sku": issue.seller_sku,
                    "marketplace_id": issue.marketplace_id,
                    "risk_level": recommendation.risk_level,
                    "title": recommendation.title,
                    "description": recommendation.description,
                    "parameters": _json_safe(recommendation.parameters),
                    "evidence": _json_safe(list(recommendation.evidence)),
                    "confidence": str(recommendation.confidence),
                    "missing_data": list(issue.missing_data),
                    "data_freshness_at": issue.data_freshness_at.isoformat(),
                }
            )
        return await self.workflow.run(
            tenant_id=tenant_id,
            actor_id=actor_id,
            workflow_type="operations_recommendations",
            correlation_id=correlation_id,
            request={"mode": "read_only_pilot", "candidates": candidates, "amazon_writes_enabled": False},
        )

    async def resume(self, *, tenant_id: str, run_id: str, actor_id: str, correlation_id: str) -> dict[str, Any]:
        return await self.workflow.resume(
            tenant_id=tenant_id,
            run_id=run_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    def get_run(self, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.workflow_store.get_run(tenant_id=tenant_id, run_id=run_id)
        return None if row is None else _serialize_run(row)

    def list_runs(self, *, tenant_id: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [
            _serialize_run(row)
            for row in self.workflow_store.list_runs(tenant_id=tenant_id, status=status, limit=limit)
        ]


def _serialize_run(row: Any) -> dict[str, Any]:
    return {
        "run_id": row.id,
        "tenant_id": row.tenant_id,
        "actor_id": row.actor_id,
        "workflow_type": row.workflow_type,
        "correlation_id": row.correlation_id,
        "status": row.status,
        "prompt_version": row.prompt_version,
        "model": row.model,
        "token_usage": row.token_usage,
        "estimated_cost": str(row.estimated_cost or Decimal("0")),
        "state": _json_safe(row.state or {}),
        "created_at": _iso_utc(row.created_at),
        "updated_at": _iso_utc(row.updated_at),
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    return str(value)


def _iso_utc(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()
