from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from redis import Redis

from agents.advisors.customer_case import create_customer_case_advisor
from agents.advisors.listing_optimization import create_listing_optimization_advisor
from agents.advisors.operations_brief import create_operations_brief_advisor
from agents.advisors.reconciliation import create_reconciliation_investigator
from api.config import get_settings
from application.customer_case_agent import CustomerCaseAgentService, CustomerCaseEvidenceCollector
from application.listing_optimization_agent import ListingOptimizationAgentService, ListingOptimizationEvidenceCollector
from application.operations_brief_agent import OperationsBriefAgentService, OperationsBriefEvidenceCollector
from application.reconciliation_agent import ReconciliationAgentService, ReconciliationEvidenceCollector
from domain.models import WorkflowStatus
from infrastructure.agent_quota import RedisAgentQuotaLimiter
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.briefing import DailyOperationsBriefRepository
from infrastructure.repositories.customer_service import CustomerCaseRepository
from infrastructure.repositories.listing_quality import ListingExperimentRepository, ListingQualitySnapshotRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.reconciliation import ReconciliationRepository
from infrastructure.repositories.tasks import DurableTaskRepository, EventRepository
from infrastructure.repositories.tenants import TenantRepository
from infrastructure.repositories.workflows import SQLWorkflowStore
from model_gateway.factory import create_configured_gateway
from workflows.engine import PersistentAgentWorkflow


def execute_agent_run_action(*, run_id: str, tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    tenant_digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
    lock = redis.lock(
        f"omnisell:agent-run-lock:{tenant_digest}:{run_id}",
        timeout=max(600, int(settings.model_timeout_seconds * 3)),
    )
    if not lock.acquire(blocking=False):
        redis.close()
        raise TimeoutError("Agent run is already executing")
    try:
        return asyncio.run(_execute_agent_run(run_id=run_id, tenant_id=tenant_id))
    finally:
        try:
            lock.release()
        finally:
            redis.close()


async def _execute_agent_run(*, run_id: str, tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    store = SQLWorkflowStore(session_factory)
    row = store.get_run(tenant_id=tenant_id, run_id=run_id)
    if row is None:
        raise KeyError(f"Agent run not found: {run_id}")
    status = WorkflowStatus(row.status)
    if status not in {
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
    }:
        return {"run_id": run_id, "status": status.value, "idempotent_replay": True}
    store.reset_active_run_for_recovery(tenant_id=tenant_id, run_id=run_id)

    gateway = create_configured_gateway(settings)
    quota = RedisAgentQuotaLimiter.from_url(
        redis_url=settings.redis_url,
        per_minute=settings.agent_tenant_quota_per_minute,
        per_day=settings.agent_tenant_quota_per_day,
    )
    audit_logger = SQLAuditLogger(session_factory)
    workflow_type = row.workflow_type
    advisor, collector = _build_advisor_and_collector(
        workflow_type=workflow_type,
        gateway=gateway,
        session_factory=session_factory,
        audit_logger=audit_logger,
    )
    workflow = PersistentAgentWorkflow(
        store=store,
        audit_logger=audit_logger,
        quota_limiter=quota,
    )
    request = dict((row.state or {}).get("request") or {})
    try:
        return await workflow.run_advisory(
            tenant_id=tenant_id,
            actor_id=row.actor_id,
            workflow_type=workflow_type,
            correlation_id=row.correlation_id,
            request=request,
            evidence_collector=collector,
            advisor=advisor,
            reserved_run_id=run_id,
        )
    finally:
        await gateway.close()
        await quota.close()


def _build_advisor_and_collector(
    *, workflow_type: str, gateway: Any, session_factory: Any, audit_logger: Any
) -> tuple[Any, Any]:
    if workflow_type == OperationsBriefAgentService.WORKFLOW_TYPE:
        return (
            create_operations_brief_advisor(gateway=gateway),
            OperationsBriefEvidenceCollector(
                brief_repository=DailyOperationsBriefRepository(session_factory),
                issue_repository=OperationalIssueRepository(session_factory),
                action_repository=RecommendedActionRepository(session_factory),
            ),
        )
    if workflow_type == CustomerCaseAgentService.WORKFLOW_TYPE:
        return (
            create_customer_case_advisor(gateway=gateway),
            CustomerCaseEvidenceCollector(case_repository=CustomerCaseRepository(session_factory)),
        )
    if workflow_type == ListingOptimizationAgentService.WORKFLOW_TYPE:
        return (
            create_listing_optimization_advisor(gateway=gateway),
            ListingOptimizationEvidenceCollector(
                snapshot_repository=ListingQualitySnapshotRepository(session_factory),
                experiment_repository=ListingExperimentRepository(session_factory),
            ),
        )
    if workflow_type == ReconciliationAgentService.WORKFLOW_TYPE:
        return (
            create_reconciliation_investigator(gateway=gateway),
            ReconciliationEvidenceCollector(
                reconciliation_repository=ReconciliationRepository(session_factory),
                connection_repository=TenantRepository(session_factory),
                task_repository=DurableTaskRepository(session_factory),
                event_repository=EventRepository(session_factory),
                audit_logger=audit_logger,
            ),
        )
    raise ValueError(f"Unsupported Agent workflow type: {workflow_type}")
