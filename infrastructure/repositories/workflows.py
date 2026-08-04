from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from domain.models import WorkflowStatus
from infrastructure.database.models import AgentRunRow


class SQLWorkflowStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_run(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workflow_type: str,
        correlation_id: str,
        request: dict[str, Any],
        prompt_version: str = "v1",
        agent_type: str | None = None,
        schema_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        run_id, _ = self.create_run_once(
            tenant_id=tenant_id,
            actor_id=actor_id,
            workflow_type=workflow_type,
            correlation_id=correlation_id,
            request=request,
            prompt_version=prompt_version,
            agent_type=agent_type,
            schema_version=schema_version,
            idempotency_key=idempotency_key,
        )
        return run_id

    def create_run_once(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workflow_type: str,
        correlation_id: str,
        request: dict[str, Any],
        prompt_version: str = "v1",
        agent_type: str | None = None,
        schema_version: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, bool]:
        if idempotency_key:
            existing = self.find_by_idempotency(
                tenant_id=tenant_id, workflow_type=workflow_type, idempotency_key=idempotency_key
            )
            if existing is not None:
                return existing.id, False
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        try:
            with self.session_factory.begin() as session:
                session.add(
                    AgentRunRow(
                        id=run_id,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        workflow_type=workflow_type,
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                        status=WorkflowStatus.RECEIVED.value,
                        prompt_version=prompt_version,
                        agent_type=agent_type,
                        schema_version=schema_version,
                        state={
                            "request": request,
                            "agent_type": agent_type,
                            "schema_version": schema_version,
                            "history": [{"status": WorkflowStatus.RECEIVED.value, "at": now}],
                        },
                    )
                )
        except IntegrityError:
            if not idempotency_key:
                raise
            existing = self.find_by_idempotency(
                tenant_id=tenant_id, workflow_type=workflow_type, idempotency_key=idempotency_key
            )
            if existing is None:
                raise
            return existing.id, False
        return run_id, True

    def find_by_idempotency(self, *, tenant_id: str, workflow_type: str, idempotency_key: str) -> AgentRunRow | None:
        with self.session_factory() as session:
            return session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.tenant_id == tenant_id,
                    AgentRunRow.workflow_type == workflow_type,
                    AgentRunRow.idempotency_key == idempotency_key,
                )
            )

    def transition(self, run_id: str, status: WorkflowStatus, state: dict[str, Any], *, tenant_id: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(AgentRunRow).where(AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id)
            )
            if row is None:
                raise KeyError(f"Agent run not found: {run_id}")
            current = WorkflowStatus(row.status)
            if status not in _ALLOWED_TRANSITIONS[current]:
                raise ValueError(f"Illegal workflow transition: {current.value} -> {status.value}")
            previous_state = dict(row.state or {})
            history = list(previous_state.get("history", []))
            history.append({"status": status.value, "at": datetime.now(UTC).isoformat()})
            row.status = status.value
            row.state = {**previous_state, **state, "history": history}
            if state.get("agent_type"):
                row.agent_type = str(state["agent_type"])
            if state.get("schema_version"):
                row.schema_version = str(state["schema_version"])
            if "evidence_manifest" in state:
                row.evidence_manifest = state["evidence_manifest"]
            if "analysis_result" in state:
                row.analysis_result = state["analysis_result"]
                usage = state["analysis_result"].get("model_usage") or {}
                row.model_usage = usage
                row.model = usage.get("model")
                row.token_usage = int(usage.get("total_tokens") or 0)
                row.estimated_cost = Decimal(str(usage.get("estimated_cost") or "0"))
                row.schema_version = str(state["analysis_result"].get("schema_version") or row.schema_version or "1.0")
            if state.get("error_category"):
                row.error_category = str(state["error_category"])

    def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunRow | None:
        with self.session_factory() as session:
            return session.scalar(
                select(AgentRunRow).where(AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id)
            )

    def reset_active_run_for_recovery(self, *, tenant_id: str, run_id: str) -> bool:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(AgentRunRow)
                .where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError(f"Agent run not found: {run_id}")
            current = WorkflowStatus(row.status)
            if current not in _ACTIVE:
                return False
            if current != WorkflowStatus.RECEIVED:
                previous_state = dict(row.state or {})
                history = list(previous_state.get("history", []))
                now = datetime.now(UTC).isoformat()
                history.append(
                    {
                        "status": WorkflowStatus.RECEIVED.value,
                        "at": now,
                        "recovered_from": current.value,
                    }
                )
                row.status = WorkflowStatus.RECEIVED.value
                row.state = {
                    **previous_state,
                    "history": history,
                    "recovery": {"recovered_from": current.value, "at": now},
                }
                row.error_category = None
            return True

    def set_dispatch_task(self, *, tenant_id: str, run_id: str, task_id: str, task_status: str) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.tenant_id == tenant_id,
                )
            )
            if row is None:
                raise KeyError(f"Agent run not found: {run_id}")
            previous_state = dict(row.state or {})
            row.state = {
                **previous_state,
                "dispatch": {
                    "task_id": task_id,
                    "status": task_status,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            }

    def increment_retry(self, *, tenant_id: str, run_id: str) -> int:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(AgentRunRow).where(AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id)
            )
            if row is None:
                raise KeyError(f"Agent run not found: {run_id}")
            row.retry_count = int(row.retry_count or 0) + 1
            return row.retry_count

    def claim_retry_attempt(self, *, tenant_id: str, run_id: str) -> int:
        for _ in range(20):
            with self.session_factory.begin() as session:
                parent = session.scalar(
                    select(AgentRunRow).where(
                        AgentRunRow.id == run_id,
                        AgentRunRow.tenant_id == tenant_id,
                    )
                )
                if parent is None:
                    raise KeyError(f"Agent run not found: {run_id}")
                current_attempt = int(parent.retry_count or 0)
                if current_attempt > 0:
                    current_key = f"retry:{run_id}:{current_attempt}"
                    current_retry = session.scalar(
                        select(AgentRunRow).where(
                            AgentRunRow.tenant_id == tenant_id,
                            AgentRunRow.workflow_type == parent.workflow_type,
                            AgentRunRow.idempotency_key == current_key,
                        )
                    )
                    if current_retry is None or current_retry.status != WorkflowStatus.FAILED.value:
                        return current_attempt
                next_attempt = current_attempt + 1
                claimed = session.execute(
                    update(AgentRunRow)
                    .where(
                        AgentRunRow.id == run_id,
                        AgentRunRow.tenant_id == tenant_id,
                        AgentRunRow.retry_count == current_attempt,
                    )
                    .values(retry_count=next_attempt)
                )
                if claimed.rowcount == 1:
                    return next_attempt
        raise RuntimeError(f"Unable to claim retry attempt for Agent run: {run_id}")

    def list_runs(self, *, tenant_id: str, status: str | None = None, limit: int = 50) -> list[AgentRunRow]:
        statement = select(AgentRunRow).where(AgentRunRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(AgentRunRow.status == status)
        with self.session_factory() as session:
            return list(
                session.scalars(statement.order_by(AgentRunRow.created_at.desc()).limit(min(max(limit, 1), 200)))
            )


_ACTIVE = {
    WorkflowStatus.RECEIVED,
    WorkflowStatus.VALIDATED,
    WorkflowStatus.EVIDENCE_COLLECTED,
    WorkflowStatus.ANALYZED,
    WorkflowStatus.OUTPUT_VALIDATED,
    WorkflowStatus.PLANNED,
    WorkflowStatus.POLICY_EVALUATED,
    WorkflowStatus.AWAITING_APPROVAL,
    WorkflowStatus.APPROVED,
    WorkflowStatus.EXECUTING,
    WorkflowStatus.VERIFYING,
}
_ALLOWED_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.RECEIVED: {WorkflowStatus.VALIDATED, WorkflowStatus.FAILED, WorkflowStatus.CANCELED},
    WorkflowStatus.VALIDATED: {
        WorkflowStatus.EVIDENCE_COLLECTED,
        WorkflowStatus.PLANNED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELED,
    },
    WorkflowStatus.EVIDENCE_COLLECTED: {
        WorkflowStatus.ANALYZED,
        WorkflowStatus.PLANNED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELED,
    },
    WorkflowStatus.ANALYZED: {WorkflowStatus.OUTPUT_VALIDATED, WorkflowStatus.FAILED, WorkflowStatus.CANCELED},
    WorkflowStatus.OUTPUT_VALIDATED: {WorkflowStatus.POLICY_EVALUATED, WorkflowStatus.FAILED, WorkflowStatus.CANCELED},
    WorkflowStatus.PLANNED: {WorkflowStatus.POLICY_EVALUATED, WorkflowStatus.FAILED, WorkflowStatus.CANCELED},
    WorkflowStatus.POLICY_EVALUATED: {
        WorkflowStatus.AWAITING_APPROVAL,
        WorkflowStatus.EXECUTING,
        WorkflowStatus.VERIFYING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELED,
    },
    WorkflowStatus.AWAITING_APPROVAL: {WorkflowStatus.APPROVED, WorkflowStatus.FAILED, WorkflowStatus.CANCELED},
    WorkflowStatus.APPROVED: {WorkflowStatus.EXECUTING, WorkflowStatus.FAILED, WorkflowStatus.CANCELED},
    WorkflowStatus.EXECUTING: {
        WorkflowStatus.EXECUTING,
        WorkflowStatus.AWAITING_APPROVAL,
        WorkflowStatus.VERIFYING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELED,
    },
    WorkflowStatus.VERIFYING: {
        WorkflowStatus.SUCCEEDED,
        WorkflowStatus.DEGRADED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELED,
    },
    WorkflowStatus.SUCCEEDED: set(),
    WorkflowStatus.DEGRADED: set(),
    WorkflowStatus.FAILED: set(),
    WorkflowStatus.CANCELED: set(),
}
