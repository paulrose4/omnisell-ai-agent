from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from celery import Celery

from api.config import get_settings
from api.amazon_factory import create_amazon_adapter, create_secret_store
from application.amazon_sync import AmazonReadSyncService
from application.operations_runtime import build_operations_refresh_service
from infrastructure.repositories.commerce_sync import CommerceSyncRepository, SyncCursorRepository
from infrastructure.repositories.tenants import TenantRepository
from application.actions import execute_action
from infrastructure.database.session import create_session_factory, tenant_context
from infrastructure.repositories.tasks import DurableTaskRepository
from infrastructure.repositories.workflows import SQLWorkflowStore
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.work_items import OperationalWorkItemRepository
from infrastructure.repositories.customer_case_operations import CustomerCaseOperationsBridge


settings = get_settings()
celery_app = Celery("omnisell", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_task_always_eager,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "sync-amazon-orders": {"task": "omnisell.dispatch_amazon_sync", "schedule": 300.0, "args": (["orders"],)},
        "sync-amazon-inventory": {"task": "omnisell.dispatch_amazon_sync", "schedule": 900.0, "args": (["inventory"],)},
        "sync-amazon-listings": {"task": "omnisell.dispatch_amazon_sync", "schedule": 21600.0, "args": (["listings"],)},
        "consume-amazon-notifications": {"task": "omnisell.consume_amazon_notifications", "schedule": 60.0},
        "refresh-operations-hourly": {"task": "omnisell.dispatch_operations_refresh", "schedule": 3600.0},
        "escalate-operational-work-items": {"task": "omnisell.escalate_operational_work_items", "schedule": 300.0},
        "reconcile-amazon-daily": {"task": "omnisell.dispatch_amazon_reconciliation", "schedule": 86400.0},
        "consume-case-operations-outbox": {"task": "omnisell.consume_case_operations_outbox", "schedule": 30.0},
        "reconcile-case-operations-daily": {"task": "omnisell.reconcile_case_operations", "schedule": 86400.0},
    },
    task_routes={
        "omnisell.amazon_sync": {"queue": "sync"},
        "omnisell.dispatch_amazon_sync": {"queue": "sync"},
        "omnisell.operations_refresh": {"queue": "operations"},
        "omnisell.dispatch_operations_refresh": {"queue": "operations"},
        "omnisell.escalate_operational_work_items": {"queue": "operations"},
        "omnisell.consume_amazon_notifications": {"queue": "sync"},
        "omnisell.amazon_reconciliation": {"queue": "sync"},
        "omnisell.dispatch_amazon_reconciliation": {"queue": "sync"},
        "omnisell.consume_case_operations_outbox": {"queue": "operations"},
        "omnisell.reconcile_case_operations": {"queue": "operations"},
    },
)


@celery_app.task(bind=True, name="omnisell.execute_action", autoretry_for=(TimeoutError, ConnectionError), retry_backoff=True, retry_jitter=True, max_retries=5)
def execute_action_task(self, task_id: str, tenant_id: str, action: str, payload: dict) -> dict:
    with tenant_context(tenant_id):
        session_factory = create_session_factory(settings.database_url)
        repository = DurableTaskRepository(session_factory)
        workflow_store = SQLWorkflowStore(session_factory)
        repository.update_status(tenant_id=tenant_id, task_id=task_id, status="running")
        _update_agent_dispatch(
            store=workflow_store,
            action=action,
            payload=payload,
            task_id=task_id,
            status="running",
        )
        try:
            result = execute_action(action, payload)
            repository.update_status(tenant_id=tenant_id, task_id=task_id, status="succeeded", result=result)
            _update_agent_dispatch(
                store=workflow_store,
                action=action,
                payload=payload,
                task_id=task_id,
                status="succeeded",
            )
            return result
        except Exception as exc:
            final = self.request.retries >= self.max_retries
            task_status = "dead_letter" if final else "failed"
            repository.update_status(
                tenant_id=tenant_id, task_id=task_id,
                status=task_status,
                error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )
            _update_agent_dispatch(
                store=workflow_store,
                action=action,
                payload=payload,
                task_id=task_id,
                status=task_status,
            )
            raise


def _update_agent_dispatch(*, store, action: str, payload: dict, task_id: str, status: str) -> None:
    if action != "agent.run":
        return
    store.set_dispatch_task(
        tenant_id=str(payload["tenant_id"]),
        run_id=str(payload["run_id"]),
        task_id=task_id,
        task_status=status,
    )


@celery_app.task(name="omnisell.amazon_sync", autoretry_for=(TimeoutError, ConnectionError), retry_backoff=True, retry_jitter=True, max_retries=5)
def amazon_sync_task(task_id: str, tenant_id: str, connection_id: str, resources: list[str]) -> dict:
    with tenant_context(tenant_id):
        return _run_amazon_sync_task(task_id, tenant_id, connection_id, resources)


def _run_amazon_sync_task(task_id: str, tenant_id: str, connection_id: str, resources: list[str]) -> dict:
    session_factory = create_session_factory(settings.database_url)
    task_repository = DurableTaskRepository(session_factory)
    tenant_repository = TenantRepository(session_factory)
    connection = tenant_repository.get_connection(tenant_id=tenant_id, connection_id=connection_id)
    if connection is None:
        task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter", error_code="ConnectionNotFound", error_message="Amazon connection no longer exists.")
        return {"status": "dead_letter"}
    task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="running")
    adapter = create_amazon_adapter(
        settings=settings, connection=connection, secret_store=create_secret_store(settings, session_factory),
        tenant_repository=tenant_repository,
    )

    async def run() -> dict:
        try:
            return await AmazonReadSyncService(
                adapter=adapter, connection=connection,
                commerce_repository=CommerceSyncRepository(session_factory),
                cursor_repository=SyncCursorRepository(session_factory),
            ).sync(tuple(resources))
        finally:
            await adapter.close()

    result = asyncio.run(run())
    failed = [name for name, value in result.items() if value["status"] == "failed"]
    task_repository.update_status(
        tenant_id=tenant_id, task_id=task_id, status="failed" if failed else "succeeded", result=result,
        error_code="PartialSyncFailure" if failed else None,
        error_message=f"Failed resources: {', '.join(failed)}" if failed else None,
    )
    if not failed:
        _enqueue_operations_refresh(
            task_repository=task_repository,
            connection=connection,
            correlation_id=f"post-sync:{task_id}",
            actor_id="amazon-sync",
            seller_skus=None,
        )
    return result


def _enqueue_operations_refresh(*, task_repository: DurableTaskRepository, connection, correlation_id: str, actor_id: str, seller_skus: list[str] | None) -> int:
    dispatched = 0
    for marketplace_id in connection.marketplace_ids:
        payload = {"connection_id": connection.id, "marketplace_id": marketplace_id, "seller_skus": seller_skus}
        task = task_repository.create(
            tenant_id=connection.tenant_id, actor_id=actor_id, queue_name="operations", action="operations.refresh",
            payload=payload, idempotency_key=f"operations-refresh:{connection.id}:{marketplace_id}:{correlation_id}",
            correlation_id=correlation_id, max_attempts=5,
        )
        if task.status == "pending":
            operations_refresh_task.delay(task.id, connection.tenant_id, connection.id, marketplace_id, seller_skus)
            dispatched += 1
    return dispatched


@celery_app.task(bind=True, name="omnisell.operations_refresh", max_retries=5)
def operations_refresh_task(self, task_id: str, tenant_id: str, connection_id: str, marketplace_id: str, seller_skus: list[str] | None = None) -> dict:
    with tenant_context(tenant_id):
        session_factory = create_session_factory(settings.database_url)
        tasks = DurableTaskRepository(session_factory)
        tenants = TenantRepository(session_factory)
        connection = tenants.get_connection(tenant_id=tenant_id, connection_id=connection_id)
        if connection is None or marketplace_id not in connection.marketplace_ids:
            tasks.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter", error_code="ConnectionNotFound", error_message="Amazon connection or marketplace no longer exists.")
            return {"status": "dead_letter"}
        tasks.update_status(tenant_id=tenant_id, task_id=task_id, status="running")
        try:
            result = build_operations_refresh_service(session_factory).refresh(
                tenant_id=tenant_id, connection_id=connection_id, marketplace_id=marketplace_id, seller_skus=seller_skus,
            )
            tasks.update_status(tenant_id=tenant_id, task_id=task_id, status="succeeded", result=result)
            return result
        except (TimeoutError, ConnectionError) as exc:
            final = self.request.retries >= self.max_retries
            tasks.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter" if final else "failed", error_code=type(exc).__name__, error_message=str(exc)[:1000])
            if final:
                raise
            raise self.retry(exc=exc, countdown=min(300, 2 ** (self.request.retries + 1)))
        except Exception as exc:
            tasks.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter", error_code=type(exc).__name__, error_message=str(exc)[:1000])
            raise


@celery_app.task(name="omnisell.dispatch_operations_refresh")
def dispatch_operations_refresh() -> dict:
    session_factory = create_session_factory(settings.database_url)
    tenants = TenantRepository(session_factory)
    tasks = DurableTaskRepository(session_factory)
    dispatched = 0
    bucket = datetime.now(UTC).strftime("%Y%m%d%H")
    for tenant_id in tenants.list_tenant_ids():
        with tenant_context(tenant_id):
            for connection in tenants.list_active_connections(tenant_id=tenant_id):
                dispatched += _enqueue_operations_refresh(
                    task_repository=tasks, connection=connection, correlation_id=f"scheduled-operations-refresh:{bucket}",
                    actor_id="scheduler", seller_skus=None,
                )
    return {"dispatched": dispatched}


@celery_app.task(name="omnisell.escalate_operational_work_items")
def escalate_operational_work_items() -> dict:
    session_factory = create_session_factory(settings.database_url)
    tenants = TenantRepository(session_factory)
    work_items = OperationalWorkItemRepository(session_factory)
    audit = SQLAuditLogger(session_factory)
    scanned = 0
    escalated = 0
    for tenant_id in tenants.list_tenant_ids():
        scanned += 1
        with tenant_context(tenant_id):
            changed = work_items.escalate_due(tenant_id=tenant_id)
            if changed:
                escalated += len(changed)
                audit.log_operation(
                    action="work_items_sla_escalated",
                    status="success",
                    details={"tenant_id": tenant_id, "work_item_count": len(changed)},
                    actor="scheduler",
                    risk_level="medium",
                )
    return {"tenants_scanned": scanned, "escalated": escalated}


def _case_operations_bridge(session_factory) -> CustomerCaseOperationsBridge:
    return CustomerCaseOperationsBridge(
        session_factory,
        minimum_samples=settings.customer_case_cluster_minimum_samples,
        window_days=settings.customer_case_cluster_window_days,
    )


@celery_app.task(name="omnisell.consume_case_operations_outbox")
def consume_case_operations_outbox() -> dict:
    session_factory = create_session_factory(settings.database_url)
    tenants = TenantRepository(session_factory)
    totals = {"tenants_scanned": 0, "claimed": 0, "processed": 0, "failed": 0, "case_events_propagated": 0}
    bridge = _case_operations_bridge(session_factory)
    for tenant_id in tenants.list_tenant_ids():
        totals["tenants_scanned"] += 1
        with tenant_context(tenant_id):
            result = bridge.process_pending_outbox(tenant_id=tenant_id)
        for key in ("claimed", "processed", "failed", "case_events_propagated"):
            totals[key] += result[key]
    return totals


@celery_app.task(name="omnisell.reconcile_case_operations")
def reconcile_case_operations() -> dict:
    session_factory = create_session_factory(settings.database_url)
    tenants = TenantRepository(session_factory)
    bridge = _case_operations_bridge(session_factory)
    results: dict[str, dict[str, int]] = {}
    for tenant_id in tenants.list_tenant_ids():
        with tenant_context(tenant_id):
            bridge.propagate_completed(tenant_id=tenant_id)
            results[tenant_id] = bridge.reconcile(tenant_id=tenant_id)
    return {"tenants_scanned": len(results), "results": results}


@celery_app.task(name="omnisell.dispatch_amazon_sync")
def dispatch_amazon_sync(resources: list[str]) -> dict:
    session_factory = create_session_factory(settings.database_url)
    tenant_repository = TenantRepository(session_factory)
    task_repository = DurableTaskRepository(session_factory)
    dispatched = 0
    bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
    for tenant_id in tenant_repository.list_tenant_ids():
        with tenant_context(tenant_id):
            for connection in tenant_repository.list_active_connections(tenant_id=tenant_id):
                task = task_repository.create(
                    tenant_id=connection.tenant_id, actor_id="scheduler", queue_name="sync", action="amazon.sync",
                    payload={"connection_id": connection.id, "resources": resources},
                    idempotency_key=f"scheduled-sync:{connection.id}:{','.join(resources)}:{bucket}",
                    correlation_id=f"scheduler:{bucket}", max_attempts=5,
                )
                if task.status == "pending":
                    amazon_sync_task.delay(task.id, connection.tenant_id, connection.id, resources)
                    dispatched += 1
    return {"dispatched": dispatched, "resources": resources}

_NOTIFICATION_RESOURCE_MAP = {
    "ORDER_CHANGE": ["orders"],
    "ANY_OFFER_CHANGED": ["listings"],
    "LISTINGS_ITEM_STATUS_CHANGE": ["listings"],
    "FBA_INVENTORY_AVAILABILITY_CHANGES": ["inventory"],
    "FULFILLMENT_ORDER_STATUS": ["orders"],
}


@celery_app.task(name="omnisell.consume_amazon_notifications")
def consume_amazon_notifications() -> dict:
    from infrastructure.repositories.tasks import EventRepository

    session_factory = create_session_factory(settings.database_url)
    events = EventRepository(session_factory)
    tasks = DurableTaskRepository(session_factory)
    processed = 0
    failed = 0
    tenant_repository = TenantRepository(session_factory)
    for tenant_id in tenant_repository.list_tenant_ids():
        with tenant_context(tenant_id):
            for event in events.claim_pending(tenant_id=tenant_id, limit=100):
                try:
                    event_type = event.event_type.upper()
                    resources = next((value for key, value in _NOTIFICATION_RESOURCE_MAP.items() if key in event_type), None)
                    connection_id = str((event.payload or {}).get("_connection_id") or "")
                    if not resources or not connection_id:
                        events.mark_dead_letter(tenant_id=tenant_id, event_id=event.id, error_code="UnknownNotificationType", error_message=f"No deterministic sync mapping for {event.event_type}")
                        failed += 1
                        continue
                    task = tasks.create(
                        tenant_id=event.tenant_id, actor_id="amazon-notification", queue_name="sync", action="amazon.sync",
                        payload={"connection_id": connection_id, "resources": resources},
                        idempotency_key=f"notification:{event.external_event_id}:{','.join(resources)}",
                        correlation_id=event.external_event_id, max_attempts=5,
                    )
                    if task.status == "pending":
                        amazon_sync_task.delay(task.id, event.tenant_id, connection_id, resources)
                    events.mark_processed(tenant_id=tenant_id, event_id=event.id)
                    processed += 1
                except Exception as exc:
                    events.mark_retry(tenant_id=tenant_id, event_id=event.id, error=exc)
                    failed += 1
    return {"processed": processed, "failed": failed}

@celery_app.task(name="omnisell.amazon_reconciliation")
def amazon_reconciliation_task(task_id: str, tenant_id: str, connection_id: str) -> dict:
    from application.reconciliation import AmazonReconciliationService
    from infrastructure.repositories.operations import OperationalIssueRepository
    from infrastructure.repositories.reconciliation import ReconciliationRepository
    with tenant_context(tenant_id):
        session_factory=create_session_factory(settings.database_url)
        tasks=DurableTaskRepository(session_factory)
        tasks.update_status(tenant_id=tenant_id,task_id=task_id,status="running")
        tenants=TenantRepository(session_factory)
        connection=tenants.get_connection(tenant_id=tenant_id,connection_id=connection_id)
        if connection is None:
            result={"status":"connection_not_found"}
            tasks.update_status(tenant_id=tenant_id,task_id=task_id,status="dead_letter",result=result,error_code="ConnectionNotFound",error_message="Amazon connection not found")
            return result
        adapter=create_amazon_adapter(settings=settings,connection=connection,secret_store=create_secret_store(settings,session_factory),tenant_repository=tenants)
        async def run():
            try:
                return await AmazonReconciliationService(adapter=adapter,connection=connection,commerce_repository=CommerceSyncRepository(session_factory),reconciliation_repository=ReconciliationRepository(session_factory),issue_repository=OperationalIssueRepository(session_factory)).run()
            finally: await adapter.close()
        try:
            result=asyncio.run(run())
            payload={"run_id":result.id,"status":result.status,"summary":result.summary}
            tasks.update_status(tenant_id=tenant_id,task_id=task_id,status="succeeded",result=payload)
            return payload
        except Exception as exc:
            tasks.update_status(tenant_id=tenant_id,task_id=task_id,status="failed",error_code=type(exc).__name__,error_message=str(exc)[:1000])
            raise

@celery_app.task(name="omnisell.dispatch_amazon_reconciliation")
def dispatch_amazon_reconciliation() -> dict:
    session_factory=create_session_factory(settings.database_url); tenants=TenantRepository(session_factory); tasks=DurableTaskRepository(session_factory); count=0; bucket=datetime.now(UTC).strftime("%Y%m%d")
    for tenant_id in tenants.list_tenant_ids():
        with tenant_context(tenant_id):
            for connection in tenants.list_active_connections(tenant_id=tenant_id):
                task,created=tasks.create_once(tenant_id=connection.tenant_id,actor_id="scheduler",queue_name="sync",action="amazon.reconciliation",payload={"connection_id":connection.id},idempotency_key=f"scheduled-reconciliation:{connection.id}:{bucket}",correlation_id=f"scheduler:{bucket}",max_attempts=5)
                if created: amazon_reconciliation_task.delay(task.id,connection.tenant_id,connection.id); count+=1
    return {"dispatched":count}
