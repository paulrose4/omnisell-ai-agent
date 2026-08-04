from __future__ import annotations

from typing import Any

from domain.models import WorkflowStatus


class QueuedAgentWorkflow:
    def __init__(self, *, store: Any, task_queue: Any, execution_workflow: Any | None = None) -> None:
        self.store = store
        self.task_queue = task_queue
        self.execution_workflow = execution_workflow
        self._in_memory_tasks_by_run: dict[str, Any] = {}

    async def run_advisory(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workflow_type: str,
        correlation_id: str,
        request: dict[str, Any],
        evidence_collector: Any,
        advisor: Any,
    ) -> dict[str, Any]:
        idempotency_key = request.get("idempotency_key")
        run_id, created = self.store.create_run_once(
            tenant_id=tenant_id,
            actor_id=actor_id,
            workflow_type=workflow_type,
            correlation_id=correlation_id,
            request=request,
            prompt_version=advisor.prompt_version,
            agent_type=advisor.agent_type,
            schema_version="2.0",
            idempotency_key=str(idempotency_key) if idempotency_key else None,
        )
        row = self.store.get_run(tenant_id=tenant_id, run_id=run_id)
        status = getattr(row, "status", WorkflowStatus.RECEIVED.value)
        task = self._enqueue(
            run_id=run_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            workflow_type=workflow_type,
            request=request,
            evidence_collector=evidence_collector,
            advisor=advisor,
        )
        task_status = getattr(task, "status", "pending")
        task_status = getattr(task_status, "value", task_status)
        if hasattr(self.store, "set_dispatch_task"):
            self.store.set_dispatch_task(
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=str(getattr(task, "task_id", "")),
                task_status=str(task_status),
            )
        return {
            "run_id": run_id,
            "status": status,
            "task_id": getattr(task, "task_id", None),
            "task_status": task_status,
            "idempotent_replay": not created,
        }

    def _enqueue(
        self,
        *,
        run_id: str,
        tenant_id: str,
        actor_id: str,
        correlation_id: str,
        workflow_type: str,
        request: dict[str, Any],
        evidence_collector: Any,
        advisor: Any,
    ) -> Any:
        if hasattr(self.task_queue, "enqueue_action"):
            return self.task_queue.enqueue_action(
                tenant_id=tenant_id,
                actor_id=actor_id,
                queue_name="text",
                action="agent.run",
                payload={"run_id": run_id, "tenant_id": tenant_id},
                request_id=correlation_id,
                idempotency_key=f"agent-run:{run_id}",
            )
        existing = self._in_memory_tasks_by_run.get(run_id)
        if existing is not None:
            return existing
        if self.execution_workflow is None:
            raise RuntimeError("In-memory Agent dispatch requires an execution workflow")

        async def execute() -> dict[str, Any]:
            return await self.execution_workflow.run_advisory(
                tenant_id=tenant_id,
                actor_id=actor_id,
                workflow_type=workflow_type,
                correlation_id=correlation_id,
                request=request,
                evidence_collector=evidence_collector,
                advisor=advisor,
                reserved_run_id=run_id,
            )

        task = self.task_queue.enqueue(
            queue_name="text",
            action="agent.run",
            handler=execute,
            request_id=correlation_id,
        )
        self._in_memory_tasks_by_run[run_id] = task
        return task
