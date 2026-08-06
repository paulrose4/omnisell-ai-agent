from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agents.quota import AgentQuotaBackendUnavailable, AgentQuotaExceeded, AgentQuotaLimiter
from agents.runtime import AgentExecutionContext
from domain.models import WorkflowStatus
from observability.telemetry import (
    bind_agent_run_context,
    reset_agent_run_context,
    trace_attributes,
    tracer,
    workflow_transition_duration,
)
from safety.policy_engine import PolicyDecision, PolicyEngine, canonical_action_hash


class PlannedAction(BaseModel):
    action: str
    resource_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    expected_change: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)


class WorkflowPlan(BaseModel):
    summary: str
    actions: list[PlannedAction]


class WorkflowStore(Protocol):
    def create_run(self, **kwargs: Any) -> str: ...
    def create_run_once(self, **kwargs: Any) -> tuple[str, bool]: ...
    def find_by_idempotency(self, *, tenant_id: str, workflow_type: str, idempotency_key: str) -> Any: ...
    def transition(self, run_id: str, status: WorkflowStatus, state: dict[str, Any], *, tenant_id: str) -> None: ...
    def get_run(self, *, tenant_id: str, run_id: str) -> Any: ...


class ToolExecutor(Protocol):
    async def execute(
        self, action: PlannedAction, *, tenant_id: str, actor_id: str, correlation_id: str, idempotency_key: str
    ) -> dict[str, Any]: ...


class ApprovalManager(Protocol):
    def submit_request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        requester: str = "agent",
        risk_level: str | None = None,
        reason: str | None = None,
        policy_decision: PolicyDecision | str = PolicyDecision.REQUIRE_APPROVAL,
        policy_version: str = "policy-engine-v1",
    ) -> Any: ...
    def get_request(self, request_id: str, tenant_id: str | None = None) -> Any: ...


class ApprovalIntegrityError(RuntimeError):
    """Raised when approved workflow state no longer matches its immutable approval."""


class PersistentAgentWorkflow:
    """Deterministic workflow shell around model planning and tool execution."""

    def __init__(
        self,
        *,
        store: Any,
        planner: Any | None = None,
        executor: ToolExecutor | None = None,
        policy_engine: PolicyEngine | None = None,
        approval_manager: ApprovalManager | None = None,
        audit_logger: Any | None = None,
        metrics: Any | None = None,
        quota_limiter: AgentQuotaLimiter | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.executor = executor
        self.policy_engine = policy_engine or PolicyEngine()
        self.approval_manager = approval_manager
        self.audit_logger = audit_logger
        self.metrics = metrics
        self.quota_limiter = quota_limiter

    async def run(
        self, *, tenant_id: str, actor_id: str, workflow_type: str, correlation_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        planner = self.planner
        if planner is None:
            raise RuntimeError("Planner is required for deterministic workflow execution.")
        with tracer.start_as_current_span("agent.workflow.run") as workflow_span:
            workflow_span.set_attributes(
                trace_attributes(
                    tenant_id=tenant_id, actor_id=actor_id, workflow_type=workflow_type, correlation_id=correlation_id
                )
            )
            agent_type = str(getattr(self.planner, "agent_type", type(self.planner).__name__))
            prompt_version = str(getattr(self.planner, "prompt_version", "deterministic-v1"))
            run_id = self.store.create_run(
                tenant_id=tenant_id,
                actor_id=actor_id,
                workflow_type=workflow_type,
                correlation_id=correlation_id,
                request=request,
                agent_type=agent_type,
                prompt_version=prompt_version,
                schema_version="1.0",
            )
            self._audit(
                "agent_run_started",
                "success",
                tenant_id=tenant_id,
                actor_id=actor_id,
                run_id=run_id,
                workflow_type=workflow_type,
                agent_type=agent_type,
                prompt_version=prompt_version,
            )
            context_token = bind_agent_run_context(
                run_id=run_id, tenant_id=tenant_id, actor_id=actor_id, correlation_id=correlation_id
            )
            current_status = WorkflowStatus.RECEIVED
            state_started = time.perf_counter()
            try:
                current_status, state_started = self._transition(
                    tenant_id, run_id, current_status, state_started, WorkflowStatus.VALIDATED, {"request": request}
                )
                with tracer.start_as_current_span("agent.planner.plan") as planner_span:
                    planner_span.set_attributes(trace_attributes(run_id=run_id, workflow_type=workflow_type))
                    plan = await planner.plan(request=request, schema=WorkflowPlan)
                    planner_span.set_attribute("agent.plan.action_count", len(plan.actions))
                self._audit(
                    "evidence_collected",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=agent_type,
                    prompt_version=prompt_version,
                    evidence_count=len(request.get("candidates", [])),
                )
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.PLANNED,
                    {"plan": plan.model_dump()},
                )

                approvals: list[dict[str, Any]] = []
                results: list[dict[str, Any]] = []
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.POLICY_EVALUATED,
                    {"plan": plan.model_dump()},
                )
                for index, action in enumerate(plan.actions):
                    with tracer.start_as_current_span("agent.action") as action_span:
                        action_span.set_attributes(
                            trace_attributes(
                                run_id=run_id,
                                action=action.action,
                                action_index=index,
                                resource_id=action.resource_id,
                                confidence=action.confidence,
                            )
                        )
                        policy = self.policy_engine.evaluate(
                            action=action.action, arguments=action.arguments, confidence=action.confidence
                        )
                        action_hash = canonical_action_hash(
                            action=action.action,
                            tenant_id=tenant_id,
                            resource_id=action.resource_id,
                            arguments=action.arguments,
                        )
                        action_span.set_attributes(
                            trace_attributes(
                                policy_decision=policy.decision.value,
                                policy_risk_level=policy.risk_level,
                                action_hash=action_hash,
                            )
                        )
                        self._audit(
                            "policy_evaluated",
                            "success",
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            run_id=run_id,
                            workflow_type=workflow_type,
                            agent_type=agent_type,
                            prompt_version=prompt_version,
                            action_index=index,
                            policy_decision=policy.decision.value,
                            risk_level=policy.risk_level,
                        )
                        if policy.decision == PolicyDecision.BLOCK:
                            self._transition(
                                tenant_id,
                                run_id,
                                current_status,
                                state_started,
                                WorkflowStatus.FAILED,
                                {"blocked_action": action.action, "reason": policy.reason, "action_hash": action_hash},
                            )
                            return {"run_id": run_id, "status": "blocked", "reason": policy.reason}
                        if policy.decision == PolicyDecision.REQUIRE_APPROVAL:
                            approval_data = {
                                "action": action.model_dump(),
                                "action_hash": action_hash,
                                "policy_decision": policy.decision.value,
                                "policy_version": policy.policy_version,
                                "risk_level": policy.risk_level,
                                "reason": policy.reason,
                                "action_index": index,
                            }
                            if self.approval_manager is not None:
                                approval = self.approval_manager.submit_request(
                                    action=action.action,
                                    payload={
                                        "tenant_id": tenant_id,
                                        "resource_id": action.resource_id,
                                        "agent_run_id": run_id,
                                        "action_index": index,
                                        "action_hash": action_hash,
                                        "policy_decision": policy.decision.value,
                                        "policy_version": policy.policy_version,
                                        "plan": action.model_dump(),
                                        "evidence": action.evidence,
                                        "proposed_changes": action.expected_change,
                                    },
                                    requester=actor_id,
                                    risk_level=policy.risk_level,
                                    reason=policy.reason,
                                    policy_decision=policy.decision,
                                    policy_version=policy.policy_version,
                                )
                                approval_data["request_id"] = approval.request_id
                                self._audit(
                                    "approval_requested",
                                    "success",
                                    tenant_id=tenant_id,
                                    actor_id=actor_id,
                                    run_id=run_id,
                                    workflow_type=workflow_type,
                                    agent_type=agent_type,
                                    prompt_version=prompt_version,
                                    action_index=index,
                                    risk_level=policy.risk_level,
                                )
                            approvals.append(approval_data)
                            continue
                        current_status, state_started = self._transition(
                            tenant_id,
                            run_id,
                            current_status,
                            state_started,
                            WorkflowStatus.EXECUTING,
                            {"action_index": index, "action_hash": action_hash},
                        )
                        self._audit(
                            "execution_started",
                            "success",
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            run_id=run_id,
                            workflow_type=workflow_type,
                            agent_type=agent_type,
                            prompt_version=prompt_version,
                            action_index=index,
                        )
                        with tracer.start_as_current_span("agent.executor.execute") as tool_span:
                            tool_span.set_attributes(
                                trace_attributes(
                                    action=action.action,
                                    action_hash=action_hash,
                                    idempotency_key=f"{run_id}:{index}:{action_hash}",
                                )
                            )
                            executor = self.executor
                            if executor is None:
                                raise RuntimeError("Executor is required for an allowed workflow action.")
                            result = await executor.execute(
                                action,
                                tenant_id=tenant_id,
                                actor_id=actor_id,
                                correlation_id=correlation_id,
                                idempotency_key=f"{run_id}:{index}:{action_hash}",
                            )
                        results.append({"action": action.action, "result": result})
                        self._audit(
                            "execution_succeeded",
                            "success",
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            run_id=run_id,
                            workflow_type=workflow_type,
                            agent_type=agent_type,
                            prompt_version=prompt_version,
                            action_index=index,
                        )

                if approvals:
                    with tracer.start_as_current_span("agent.approval.wait"):
                        self._transition(
                            tenant_id,
                            run_id,
                            current_status,
                            state_started,
                            WorkflowStatus.AWAITING_APPROVAL,
                            {"approvals": approvals, "results": results},
                        )
                    return {"run_id": run_id, "status": "awaiting_approval", "approvals": approvals, "results": results}
                current_status, state_started = self._transition(
                    tenant_id, run_id, current_status, state_started, WorkflowStatus.VERIFYING, {"results": results}
                )
                self._transition(
                    tenant_id, run_id, current_status, state_started, WorkflowStatus.SUCCEEDED, {"results": results}
                )
                self._audit(
                    "verification_succeeded",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=agent_type,
                    prompt_version=prompt_version,
                )
                return {"run_id": run_id, "status": "succeeded", "results": results}
            except Exception as exc:
                workflow_span.record_exception(exc)
                self._audit(
                    "execution_failed" if current_status == WorkflowStatus.EXECUTING else "verification_failed",
                    "failure",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=agent_type,
                    prompt_version=prompt_version,
                    error_category=type(exc).__name__,
                )
                if current_status not in {WorkflowStatus.FAILED, WorkflowStatus.CANCELED, WorkflowStatus.SUCCEEDED}:
                    self.store.transition(
                        run_id,
                        WorkflowStatus.FAILED,
                        {"error_category": type(exc).__name__, "retryable": True},
                        tenant_id=tenant_id,
                    )
                raise
            finally:
                reset_agent_run_context(context_token)

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
        reserved_run_id: str | None = None,
    ) -> dict[str, Any]:
        idempotency_key = request.get("idempotency_key")
        if reserved_run_id is None and idempotency_key and hasattr(self.store, "find_by_idempotency"):
            existing = self.store.find_by_idempotency(
                tenant_id=tenant_id, workflow_type=workflow_type, idempotency_key=str(idempotency_key)
            )
            if existing is not None:
                existing_state = dict(getattr(existing, "state", {}) or {})
                analysis_result = (
                    getattr(existing, "analysis_result", None) or existing_state.get("analysis_result") or {}
                )
                return {
                    "run_id": existing.id,
                    "status": existing.status,
                    "analysis": analysis_result,
                    "idempotent_replay": True,
                }

        workflow_started = time.perf_counter()
        with tracer.start_as_current_span("agent.workflow") as workflow_span:
            workflow_span.set_attributes(
                trace_attributes(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                )
            )
            create_arguments = {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "workflow_type": workflow_type,
                "correlation_id": correlation_id,
                "request": request,
                "prompt_version": advisor.prompt_version,
                "agent_type": advisor.agent_type,
                "schema_version": "1.0",
                "idempotency_key": str(idempotency_key) if idempotency_key else None,
            }
            if reserved_run_id is not None:
                run_id = reserved_run_id
                created = True
            elif hasattr(self.store, "create_run_once"):
                run_id, created = self.store.create_run_once(**create_arguments)
            else:
                run_id = self.store.create_run(**create_arguments)
                created = True
            if not created:
                existing = self.store.get_run(tenant_id=tenant_id, run_id=run_id)
                existing_state = dict(getattr(existing, "state", {}) or {})
                analysis_result = (
                    getattr(existing, "analysis_result", None) or existing_state.get("analysis_result") or {}
                )
                return {
                    "run_id": run_id,
                    "status": getattr(existing, "status", WorkflowStatus.RECEIVED.value),
                    "analysis": analysis_result,
                    "idempotent_replay": True,
                }
            self._audit(
                "agent_run_started",
                "success",
                tenant_id=tenant_id,
                actor_id=actor_id,
                run_id=run_id,
                workflow_type=workflow_type,
                agent_type=advisor.agent_type,
                prompt_version=advisor.prompt_version,
            )
            context_token = bind_agent_run_context(
                run_id=run_id, tenant_id=tenant_id, actor_id=actor_id, correlation_id=correlation_id
            )
            current_status = WorkflowStatus.RECEIVED
            state_started = time.perf_counter()
            try:
                if self.quota_limiter is not None:
                    quota = await self.quota_limiter.consume(
                        tenant_id=tenant_id,
                        workflow_type=workflow_type,
                    )
                    workflow_span.set_attributes(
                        trace_attributes(
                            quota_allowed=quota.allowed,
                            quota_scope=quota.scope,
                        )
                    )
                    if not quota.allowed:
                        raise AgentQuotaExceeded(
                            scope=quota.scope or "minute",
                            retry_after_seconds=quota.retry_after_seconds,
                        )
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.VALIDATED,
                    {"request": request, "agent_type": advisor.agent_type, "prompt_version": advisor.prompt_version},
                )
                with tracer.start_as_current_span("agent.evidence.collect"):
                    evidence = await evidence_collector.collect(tenant_id=tenant_id, request=request)
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.EVIDENCE_COLLECTED,
                    {"evidence_manifest": evidence.model_dump(mode="json")},
                )
                self._audit(
                    "evidence_collected",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                    evidence_count=len(evidence.evidence),
                )
                execution_context = AgentExecutionContext(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    workflow_run_id=run_id,
                    correlation_id=correlation_id,
                    requested_at=datetime.now(UTC),
                    locale=str(request.get("locale") or "en-US"),
                    allowed_capabilities=frozenset(request.get("allowed_capabilities") or {"read_evidence"}),
                    max_prompt_tokens=int(request.get("max_prompt_tokens") or 4000),
                    max_completion_tokens=int(request.get("max_completion_tokens") or 2000),
                    max_estimated_cost=Decimal(str(request.get("max_estimated_cost") or "1.00")),
                    max_evidence_items=int(request.get("max_evidence_items") or 100),
                    max_evidence_chars=int(request.get("max_evidence_chars") or 12000),
                    max_total_evidence_chars=int(request.get("max_total_evidence_chars") or 48000),
                    max_request_bytes=int(request.get("max_request_bytes") or 128000),
                    timeout_seconds=int(request.get("timeout_seconds") or 30),
                )
                self._audit(
                    "model_generation_started",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                )
                with tracer.start_as_current_span("agent.model.generate"):
                    analysis = await advisor.analyze(context=execution_context, evidence=evidence)
                self._audit(
                    "model_generation_succeeded",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                    model=getattr(analysis.model_usage, "model", None),
                )
                analysis_payload = analysis.model_dump(mode="json")
                evidence_preparation = analysis_payload.get("evidence_preparation")
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.ANALYZED,
                    {"analysis_result": analysis_payload, "evidence_preparation": evidence_preparation},
                )
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.OUTPUT_VALIDATED,
                    {"output_validated": True},
                )
                policy_results = []
                with tracer.start_as_current_span("agent.policy.evaluate"):
                    for action in (*analysis.proposed_actions, *analysis.prohibited_actions):
                        policy = self.policy_engine.evaluate(
                            action=action.action_type,
                            arguments=action.arguments.model_dump(mode="json"),
                            confidence=float(analysis.confidence),
                        )
                        policy_results.append(
                            {
                                "action_type": action.action_type,
                                "decision": policy.decision.value,
                                "risk_level": policy.risk_level,
                                "reason": policy.reason,
                            }
                        )
                current_status, state_started = self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    WorkflowStatus.POLICY_EVALUATED,
                    {"policy_results": policy_results, "execution_allowed": False},
                )
                self._audit(
                    "policy_evaluated",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                    policy_result_count=len(policy_results),
                )
                with tracer.start_as_current_span("agent.verify"):
                    current_status, state_started = self._transition(
                        tenant_id,
                        run_id,
                        current_status,
                        state_started,
                        WorkflowStatus.VERIFYING,
                        {"verification": {"amazon_write_performed": False, "advisory_only": True}},
                    )
                final_status = WorkflowStatus.DEGRADED if analysis.degraded_reason else WorkflowStatus.SUCCEEDED
                self._transition(
                    tenant_id,
                    run_id,
                    current_status,
                    state_started,
                    final_status,
                    {"analysis_result": analysis_payload},
                )
                self._audit(
                    "verification_succeeded",
                    "success",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                    final_status=final_status.value,
                )
                usage = analysis.model_usage
                await self._record_agent_metrics(
                    workflow_type=workflow_type,
                    status=final_status.value,
                    latency_ms=(time.perf_counter() - workflow_started) * 1000,
                    total_tokens=usage.total_tokens if usage is not None else 0,
                    estimated_cost=usage.estimated_cost if usage is not None else Decimal("0"),
                    repair_attempted=usage.repair_attempted if usage is not None else False,
                    first_pass_schema_success=usage.first_pass_schema_success if usage is not None else True,
                    schema_failure_categories=usage.schema_failure_categories if usage is not None else (),
                    model_initial_latency_ms=float(usage.initial_latency_ms) if usage is not None else 0,
                    model_repair_latency_ms=float(usage.repair_latency_ms) if usage is not None else 0,
                    fallback_used=usage.fallback_used if usage is not None else False,
                    evidence_validation_failed=False,
                    retry=str(idempotency_key or "").startswith("retry:"),
                )
                return {"run_id": run_id, "status": final_status.value, "analysis": analysis_payload}
            except Exception as exc:
                workflow_span.record_exception(exc)
                quota_exceeded = isinstance(exc, AgentQuotaExceeded)
                quota_backend_failed = isinstance(exc, AgentQuotaBackendUnavailable)
                if quota_exceeded:
                    failure_action = "agent_quota_exceeded"
                elif quota_backend_failed:
                    failure_action = "agent_quota_backend_unavailable"
                else:
                    failure_action = (
                        "evidence_validation_failed" if "Evidence" in type(exc).__name__ else "model_generation_failed"
                    )
                failure_details = {
                    "error_category": type(exc).__name__,
                    "retryable": bool(
                        getattr(
                            exc,
                            "retryable",
                            type(exc).__name__
                            not in {
                                "AgentRuntimeError",
                                "EvidenceValidationError",
                                "ModelBudgetExceeded",
                                "ModelContextRejected",
                                "ModelSafetyRejected",
                                "ModelSchemaError",
                                "ModelUsageError",
                            },
                        )
                    ),
                }
                if quota_exceeded:
                    failure_details.update(
                        {
                            "quota_scope": exc.scope,
                            "retry_after_seconds": exc.retry_after_seconds,
                        }
                    )
                elif quota_backend_failed:
                    failure_details["retry_after_seconds"] = exc.retry_after_seconds
                self._audit(
                    failure_action,
                    "failure",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=advisor.agent_type,
                    prompt_version=advisor.prompt_version,
                    **failure_details,
                )
                if current_status not in {
                    WorkflowStatus.FAILED,
                    WorkflowStatus.CANCELED,
                    WorkflowStatus.SUCCEEDED,
                    WorkflowStatus.DEGRADED,
                }:
                    self.store.transition(
                        run_id,
                        WorkflowStatus.FAILED,
                        failure_details,
                        tenant_id=tenant_id,
                    )
                await self._record_agent_metrics(
                    workflow_type=workflow_type,
                    status=WorkflowStatus.FAILED.value,
                    latency_ms=(time.perf_counter() - workflow_started) * 1000,
                    evidence_validation_failed=failure_action == "evidence_validation_failed",
                    retry=str(idempotency_key or "").startswith("retry:"),
                )
                raise
            finally:
                reset_agent_run_context(context_token)

    async def resume(self, *, tenant_id: str, run_id: str, actor_id: str, correlation_id: str) -> dict[str, Any]:
        row = self.store.get_run(tenant_id=tenant_id, run_id=run_id)
        if row is None:
            raise KeyError(f"Agent run not found: {run_id}")
        current_status = WorkflowStatus(row.status)
        state = dict(row.state or {})
        workflow_type = str(getattr(row, "workflow_type", "operations_recommendations"))
        agent_type = str(getattr(row, "agent_type", None) or "operations_recommendation_planner")
        prompt_version = str(getattr(row, "prompt_version", None) or "operations-recommendations-deterministic-v1")
        if current_status != WorkflowStatus.AWAITING_APPROVAL:
            return {"run_id": run_id, "status": current_status.value, "state": state}
        if self.approval_manager is None:
            raise RuntimeError("Approval manager is required to resume an approval workflow.")

        approvals = list(state.get("approvals", []))
        if not approvals or any(not item.get("request_id") for item in approvals):
            raise ApprovalIntegrityError("Workflow approval references are missing.")
        approval_records = [
            self.approval_manager.get_request(item["request_id"], tenant_id=tenant_id) for item in approvals
        ]
        if any(record is None for record in approval_records):
            raise RuntimeError("An approval record required by the workflow is missing.")
        if any(record.status.value == "rejected" for record in approval_records):
            self.store.transition(
                run_id, WorkflowStatus.FAILED, {"failure_reason": "approval_rejected"}, tenant_id=tenant_id
            )
            return {"run_id": run_id, "status": WorkflowStatus.FAILED.value}
        if any(record.status.value != "approved" for record in approval_records):
            return {"run_id": run_id, "status": WorkflowStatus.AWAITING_APPROVAL.value, "approvals": approvals}

        state_started = time.perf_counter()
        plan = WorkflowPlan.model_validate(state["plan"])
        for approval, record in zip(approvals, approval_records, strict=True):
            try:
                self._validate_approval_integrity(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    plan=plan,
                    approval=approval,
                    record=record,
                )
            except ApprovalIntegrityError:
                self.store.transition(
                    run_id,
                    WorkflowStatus.FAILED,
                    {"error_category": "ApprovalIntegrityError", "retryable": False},
                    tenant_id=tenant_id,
                )
                self._audit(
                    "approval_resumed",
                    "failure",
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    run_id=run_id,
                    workflow_type=workflow_type,
                    agent_type=agent_type,
                    prompt_version=prompt_version,
                    error_category="ApprovalIntegrityError",
                )
                raise
        self._audit(
            "approval_resumed",
            "success",
            tenant_id=tenant_id,
            actor_id=actor_id,
            run_id=run_id,
            workflow_type=workflow_type,
            agent_type=agent_type,
            prompt_version=prompt_version,
            approval_count=len(approvals),
        )
        current_status, state_started = self._transition(
            tenant_id, run_id, current_status, state_started, WorkflowStatus.APPROVED, {"approved_by": actor_id}
        )
        results = list(state.get("results", []))
        executor = self.executor
        if executor is None:
            raise RuntimeError("Executor is required to resume an approved workflow.")
        for approval in approvals:
            index = int(approval["action_index"])
            action = plan.actions[index]
            action_hash = approval["action_hash"]
            current_status, state_started = self._transition(
                tenant_id,
                run_id,
                current_status,
                state_started,
                WorkflowStatus.EXECUTING,
                {"action_index": index, "action_hash": action_hash},
            )
            self._audit(
                "execution_started",
                "success",
                tenant_id=tenant_id,
                actor_id=actor_id,
                run_id=run_id,
                workflow_type=workflow_type,
                agent_type=agent_type,
                prompt_version=prompt_version,
                action_index=index,
            )
            with tracer.start_as_current_span("agent.executor.execute") as executor_span:
                executor_span.set_attributes(
                    trace_attributes(
                        action=action.action,
                        action_hash=action_hash,
                        idempotency_key=f"{run_id}:{index}:{action_hash}",
                    )
                )
                try:
                    result = await executor.execute(
                        action,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        correlation_id=correlation_id,
                        idempotency_key=f"{run_id}:{index}:{action_hash}",
                    )
                except Exception as exc:
                    self._audit(
                        "execution_failed",
                        "failure",
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        run_id=run_id,
                        workflow_type=workflow_type,
                        agent_type=agent_type,
                        prompt_version=prompt_version,
                        action_index=index,
                        error_category=type(exc).__name__,
                    )
                    self.store.transition(
                        run_id,
                        WorkflowStatus.FAILED,
                        {"error_category": type(exc).__name__, "retryable": True},
                        tenant_id=tenant_id,
                    )
                    raise
            results.append({"action": action.action, "result": result})
            self._audit(
                "execution_succeeded",
                "success",
                tenant_id=tenant_id,
                actor_id=actor_id,
                run_id=run_id,
                workflow_type=workflow_type,
                agent_type=agent_type,
                prompt_version=prompt_version,
                action_index=index,
            )
        current_status, state_started = self._transition(
            tenant_id, run_id, current_status, state_started, WorkflowStatus.VERIFYING, {"results": results}
        )
        self._transition(
            tenant_id, run_id, current_status, state_started, WorkflowStatus.SUCCEEDED, {"results": results}
        )
        self._audit(
            "verification_succeeded",
            "success",
            tenant_id=tenant_id,
            actor_id=actor_id,
            run_id=run_id,
            workflow_type=workflow_type,
            agent_type=agent_type,
            prompt_version=prompt_version,
        )
        return {"run_id": run_id, "status": WorkflowStatus.SUCCEEDED.value, "results": results}

    @staticmethod
    def _validate_approval_integrity(
        *,
        tenant_id: str,
        run_id: str,
        plan: WorkflowPlan,
        approval: dict[str, Any],
        record: Any,
    ) -> None:
        expires_at = getattr(record, "expires_at", None)
        if expires_at is None or expires_at.tzinfo is None or datetime.now(UTC) >= expires_at:
            raise ApprovalIntegrityError("Approved action has expired.")
        requester = str(getattr(record, "requester", ""))
        decided_by = str(getattr(record, "decided_by", ""))
        approvers = {item for item in decided_by.split("+") if item}
        if not approvers or requester in approvers:
            raise ApprovalIntegrityError("Approval is not independent from the requester.")

        payload = dict(getattr(record, "payload", {}) or {})
        try:
            index = int(approval["action_index"])
            action = plan.actions[index]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ApprovalIntegrityError("Approved action index is invalid.") from exc

        action_payload = action.model_dump(mode="json")
        expected_hash = canonical_action_hash(
            action=action.action,
            tenant_id=tenant_id,
            resource_id=action.resource_id,
            arguments=action.arguments,
        )
        invariants = {
            "approval action hash": approval.get("action_hash") == expected_hash,
            "approval action snapshot": approval.get("action") == action_payload,
            "record action hash": payload.get("action_hash") == expected_hash,
            "approval policy decision": approval.get("policy_decision") == PolicyDecision.REQUIRE_APPROVAL.value,
            "record policy decision": payload.get("policy_decision") == PolicyDecision.REQUIRE_APPROVAL.value,
            "loaded policy decision": getattr(record, "policy_decision", None) == PolicyDecision.REQUIRE_APPROVAL,
            "approval policy version": approval.get("policy_version") == getattr(record, "policy_version", None),
            "record policy version": payload.get("policy_version") == getattr(record, "policy_version", None),
            "record action snapshot": payload.get("plan") == action_payload,
            "record action type": getattr(record, "action", None) == action.action,
            "record tenant": payload.get("tenant_id") == tenant_id,
            "record workflow run": payload.get("agent_run_id") == run_id,
            "record action index": payload.get("action_index") == index,
            "record resource": payload.get("resource_id") == action.resource_id,
            "record evidence": payload.get("evidence") == action_payload["evidence"],
            "record expected change": payload.get("proposed_changes") == action_payload["expected_change"],
        }
        failed = [name for name, valid in invariants.items() if not valid]
        if failed:
            raise ApprovalIntegrityError(f"Approval integrity check failed: {', '.join(failed)}.")

    async def _record_agent_metrics(self, **values: Any) -> None:
        if self.metrics is None or not hasattr(self.metrics, "record_agent_run"):
            return
        try:
            await self.metrics.record_agent_run(**values)
        except Exception:
            return

    def _audit(
        self,
        action: str,
        status: str,
        *,
        tenant_id: str,
        actor_id: str,
        run_id: str,
        workflow_type: str,
        agent_type: str,
        prompt_version: str,
        **details: Any,
    ) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log_operation(
            action=action,
            status=status,
            details={
                "tenant_id": tenant_id,
                "run_id": run_id,
                "workflow_type": workflow_type,
                "agent_type": agent_type,
                "prompt_version": prompt_version,
                **details,
            },
            actor=actor_id,
            risk_level="low" if status == "success" else "medium",
        )

    def _transition(
        self,
        tenant_id: str,
        run_id: str,
        previous_status: WorkflowStatus,
        state_started: float,
        next_status: WorkflowStatus,
        state: dict[str, Any],
    ) -> tuple[WorkflowStatus, float]:
        now = time.perf_counter()
        state_duration_ms = (now - state_started) * 1000
        with tracer.start_as_current_span("agent.workflow.transition") as span:
            span.set_attributes(
                trace_attributes(
                    run_id=run_id,
                    previous_status=previous_status.value,
                    next_status=next_status.value,
                    state_duration_ms=state_duration_ms,
                )
            )
            self.store.transition(run_id, next_status, state, tenant_id=tenant_id)
        workflow_transition_duration.record(
            state_duration_ms,
            trace_attributes(previous_status=previous_status.value, next_status=next_status.value),
        )
        return next_status, time.perf_counter()
