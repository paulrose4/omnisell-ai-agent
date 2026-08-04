from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.security import require_api_key
from application.demo_reset import reset_mock_demo_tenant
from application.high_fidelity_mock import HighFidelityStoreConfig, generate_high_fidelity_store
from commerce.adapters.high_fidelity_mock_intelligence import HighFidelityMockOperationsSource
from commerce.adapters.mock_seller_central import HttpMockSellerCentralAdapter
from commerce.adapters.mock_operations_intelligence import MockOperationsIntelligenceSource
from commerce.customer_messaging import BuyerMessage
from infrastructure.database.session import tenant_context


router = APIRouter(prefix="/api/mock-scenarios", tags=["mock-operations-drill"], dependencies=[Depends(require_api_key)])
seller_central_router = APIRouter(prefix="/api/mock-seller-central", tags=["mock-seller-central"], dependencies=[Depends(require_api_key)])
manual_execution_router = APIRouter(prefix="/api/manual-executions", tags=["manual-executions"], dependencies=[Depends(require_api_key)])


class ScenarioRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)


class ManualExecutionRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)
    issue_id: str = Field(min_length=1, max_length=100)
    recommended_action_id: str | None = Field(default=None, max_length=100)
    action_type: Literal["create_replenishment_plan", "update_listing_title", "adjust_fbm_inventory", "update_price", "mark_not_executed"]
    changes: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)
    execution_evidence: dict[str, Any] = Field(default_factory=dict)


def _default_high_fidelity_reference_time() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class HighFidelitySeedRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)
    seed: int = 20260722
    days: int = Field(default=30, ge=1, le=90)
    sku_count: int = Field(default=120, ge=10, le=500)
    order_count: int = Field(default=12000, ge=100, le=100000)
    campaign_count: int = Field(default=60, ge=1, le=500)
    search_term_count: int = Field(default=3500, ge=10, le=50000)
    return_count: int = Field(default=700, ge=1, le=10000)
    message_count: int = Field(default=1000, ge=1, le=10000)
    review_count: int = Field(default=400, ge=1, le=10000)
    customer_case_count: int = Field(default=350, ge=1, le=5000)
    business_context_count: int = Field(default=75, ge=1, le=5000)
    reference_time: datetime = Field(default_factory=_default_high_fidelity_reference_time)


@router.get("")
async def list_scenarios(request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_read(request)
    result = await _port(request).list_scenarios()
    return {**result, "data_source": "mock_amazon", "verified_amazon_data": False}


@router.post("/operations-learning/seed")
async def seed_operations_learning(request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_operator(request)
    principal = request.state.principal
    result = request.app.state.intelligence_ingestion_service.ingest(
        source=MockOperationsIntelligenceSource(),
        tenant_id=principal.tenant_id,
        marketplace_id=request.app.state.settings.amazon_marketplaces[0],
    )
    request.app.state.audit_logger.log_operation(
        action="mock_operations_learning_seeded", status="success",
        details={"source": "mock", "verified_amazon_data": False, **result},
        actor=principal.user_id, risk_level="low",
    )
    return result


@router.post("/high-fidelity/seed", status_code=202)
async def seed_high_fidelity_store(
    payload: HighFidelitySeedRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, Any]:
    """Submit a durable seed job without holding one HTTP request open."""
    _require_enabled(request)
    _require_admin(request)
    principal = request.state.principal
    connection = _connection(request, payload.connection_id)
    config_payload = payload.model_dump(exclude={"connection_id", "reference_time"})
    try:
        config = HighFidelityStoreConfig(**config_payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_high_fidelity_config", "message": str(exc)}) from exc
    reference_time = payload.reference_time.astimezone(UTC)
    dataset = generate_high_fidelity_store(config=config, reference_time=reference_time)
    task, created = request.app.state.task_repository.create_once(
        tenant_id=principal.tenant_id, actor_id=principal.user_id,
        queue_name="operations", action="mock.high_fidelity.seed",
        payload={
            "connection_id": connection.id,
            "config": config_payload,
            "reference_time": reference_time.isoformat(),
            "dataset_fingerprint": dataset.fingerprint,
        },
        idempotency_key=f"high-fidelity-seed:{connection.id}:{dataset.fingerprint}",
        correlation_id=f"high-fidelity:{dataset.fingerprint[:16]}", max_attempts=1,
    )
    if created:
        background_tasks.add_task(
            _run_high_fidelity_seed,
            request.app, principal.tenant_id, principal.user_id, task.id, connection.id,
            config, reference_time, dataset,
        )
    return {
        "task_id": task.id,
        "status": task.status,
        "dataset_fingerprint": dataset.fingerprint,
        "source": "mock",
        "verified_amazon_data": False,
        "amazon_write_performed": False,
    }


@router.get("/high-fidelity/tasks/{task_id}")
async def high_fidelity_seed_status(task_id: str, request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_read(request)
    principal = request.state.principal
    task = request.app.state.task_repository.get(tenant_id=principal.tenant_id, task_id=task_id)
    if task is None or task.action != "mock.high_fidelity.seed":
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "High-fidelity seed task not found."})
    return {
        "task_id": task.id,
        "status": task.status,
        "result": task.result,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


async def _run_high_fidelity_seed(
    app,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    connection_id: str,
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    dataset,
) -> None:
    with tenant_context(tenant_id):
        app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="running")
        try:
            mock_manifest = await HttpMockSellerCentralAdapter(
                app.state.settings.amazon_sp_api_endpoint,
            ).load_high_fidelity({
                **config.__dict__, "reference_time": reference_time.isoformat(),
            })
            intelligence = await asyncio.to_thread(
                app.state.intelligence_ingestion_service.ingest,
                source=HighFidelityMockOperationsSource(dataset),
                tenant_id=tenant_id,
                marketplace_id=app.state.settings.amazon_marketplaces[0],
            )

            sync_task = app.state.task_repository.create(
                tenant_id=tenant_id, actor_id=actor_id, queue_name="sync", action="amazon.sync",
                payload={"connection_id": connection_id, "resources": ["orders", "inventory", "listings"]},
                idempotency_key=f"high-fidelity-sync:{connection_id}:{dataset.fingerprint}",
                correlation_id=f"high-fidelity:{dataset.fingerprint[:16]}", max_attempts=1,
            )
            if sync_task.status == "pending":
                from api.routes.sync_routes import _run_sync_job
                await _run_sync_job(app, tenant_id, sync_task.id, connection_id, ("orders", "inventory", "listings"))
            sync_task = app.state.task_repository.get(tenant_id=tenant_id, task_id=sync_task.id)
            if sync_task is None or sync_task.status != "succeeded":
                detail = sync_task.error_message if sync_task else "sync task disappeared"
                raise RuntimeError(f"High-fidelity Amazon read sync failed: {detail}")

            created_case_ids = await asyncio.to_thread(
                _create_high_fidelity_cases,
                app, tenant_id, actor_id, dataset, config.customer_case_count,
            )
            result = {
                "dataset": dataset.manifest(),
                "mock_cloud": mock_manifest,
                "intelligence": intelligence,
                "sync_task_id": sync_task.id,
                "sync_status": sync_task.status,
                "customer_cases": len(created_case_ids),
                "sample_case_ids": created_case_ids[:10],
                "source": "mock",
                "verified_amazon_data": False,
                "amazon_write_performed": False,
            }
            app.state.audit_logger.log_operation(
                action="high_fidelity_mock_store_seeded", status="success",
                details={
                    "tenant_id": tenant_id, "connection_id": connection_id,
                    "fingerprint": dataset.fingerprint, "counts": dataset.counts,
                    "source": "mock", "verified_amazon_data": False,
                    "amazon_write_performed": False,
                }, actor=actor_id, risk_level="medium",
            )
            app.state.task_repository.update_status(
                tenant_id=tenant_id, task_id=task_id, status="succeeded", result=result,
            )
        except Exception as exc:
            app.state.task_repository.update_status(
                tenant_id=tenant_id, task_id=task_id, status="dead_letter",
                error_code=type(exc).__name__, error_message=str(exc)[:1000],
            )


def _create_high_fidelity_cases(app, tenant_id: str, actor_id: str, dataset, limit: int) -> list[str]:
    created_case_ids: list[str] = []
    for item in dataset.customer_messages[:limit]:
        case = app.state.customer_service_workflow.intake(
            tenant_id=tenant_id,
            actor_id=actor_id,
            message=BuyerMessage(
                external_case_id=f"hf-case-{item['external_message_id']}",
                scenario_id=item["scenario_id"], external_order_id=item["external_order_id"],
                marketplace_id=item["marketplace_id"], language=item["language"],
                message=item["message"],
                received_at=datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")),
            ),
        )
        created_case_ids.append(case.id)
    return created_case_ids


@router.post("/reset")
async def reset_scenarios(request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_admin(request)
    result = await _port(request).reset()
    database_reset = reset_mock_demo_tenant(
        session_factory=request.app.state.session_factory,
        tenant_id=request.state.principal.tenant_id,
    )
    request.app.state.audit_logger.log_operation(
        action="mock_amazon_scenarios_reset", status="success",
        details={"tenant_id": request.state.principal.tenant_id, "verified_amazon_data": False, "database_reset": True},
        actor=request.state.principal.user_id, risk_level="medium",
    )
    return {**result, **database_reset}


@router.post("/{scenario_id}/trigger")
async def trigger_scenario(scenario_id: str, payload: ScenarioRequest, background_tasks: BackgroundTasks, request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_operator(request)
    connection = _connection(request, payload.connection_id)
    result = await _port(request).trigger(scenario_id)
    if scenario_id == "inventory_mismatch":
        accepted = False
        if request.app.state.settings.task_backend == "celery":
            from application.celery_app import amazon_reconciliation_task
            bucket = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            task = request.app.state.task_repository.create(
                tenant_id=connection.tenant_id, actor_id=request.state.principal.user_id,
                queue_name="sync", action="amazon.reconciliation",
                payload={"connection_id": connection.id},
                idempotency_key=f"mock-reconciliation:{connection.id}:{bucket}",
                correlation_id=getattr(request.state, "request_id", bucket), max_attempts=5,
            )
            amazon_reconciliation_task.delay(task.id, connection.tenant_id, connection.id)
        else:
            background_tasks.add_task(_run_mock_reconciliation, request.app, connection.tenant_id, connection.id)
    else:
        accepted = await _accept_and_schedule_event(request, background_tasks, connection, result.get("event"))
    request.app.state.audit_logger.log_operation(
        action="mock_amazon_scenario_triggered", status="success",
        details={
            "tenant_id": request.state.principal.tenant_id, "scenario_id": scenario_id,
            "connection_id": connection.id, "event_accepted": accepted,
            "data_source": "mock_amazon", "verified_amazon_data": False,
        }, actor=request.state.principal.user_id, risk_level="medium",
    )
    return {**result, "event_accepted": accepted, "data_source": "mock_amazon", "verified_amazon_data": False}


@seller_central_router.post("/execute")
async def execute_manual_action(payload: ManualExecutionRequest, background_tasks: BackgroundTasks, request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_operator(request)
    principal = request.state.principal
    connection = _connection(request, payload.connection_id)
    issue = request.app.state.operational_issue_repository.get(tenant_id=principal.tenant_id, issue_id=payload.issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Operational issue not found."})
    if issue.status != "approved_for_manual_execution" and payload.action_type != "mark_not_executed":
        raise HTTPException(status_code=409, detail={"error": "approval_required", "message": "Issue must be approved for manual execution."})
    if issue.seller_sku == "ACCOUNT":
        raise HTTPException(status_code=422, detail={"error": "unsupported_issue", "message": "Account-level issues cannot be executed in Mock Seller Central."})

    action = None
    if payload.recommended_action_id:
        action = next((item for item in request.app.state.recommended_action_repository.list_actions(
            tenant_id=principal.tenant_id, issue_id=issue.id,
        ) if item.id == payload.recommended_action_id), None)
        if action is None:
            raise HTTPException(status_code=404, detail={"error": "action_not_found", "message": "Recommended action not found for this issue."})

    execution_started_at = datetime.now(UTC)
    result = await _port(request).execute(seller_sku=issue.seller_sku, action_type=payload.action_type, changes=payload.changes)
    execution = request.app.state.manual_execution_repository.create(
        tenant_id=principal.tenant_id, issue_id=issue.id,
        recommended_action_id=payload.recommended_action_id, action_type=payload.action_type,
        before_snapshot=dict(result.get("before") or {}),
        approved_change=dict(action.parameters if action else payload.changes),
        actual_change=dict(result.get("after") or payload.changes),
        execution_evidence={**payload.execution_evidence, "mock_seller_central": True, "verified_amazon_data": False},
        executed_by=principal.user_id, notes=payload.notes or None, executed_at=execution_started_at,
    )
    if payload.action_type == "mark_not_executed":
        updated_issue = request.app.state.operational_issue_repository.transition(
            tenant_id=principal.tenant_id, issue_id=issue.id, target_status="in_progress",
            actor_id=principal.user_id, note=payload.notes or "Operator chose not to execute the approved recommendation.",
        )
    else:
        updated_issue = request.app.state.operational_issue_repository.attach_execution(
            tenant_id=principal.tenant_id, issue_id=issue.id,
            execution_result_id=execution.id, actor_id=principal.user_id,
        )
    accepted = await _accept_and_schedule_event(request, background_tasks, connection, result.get("event"))
    request.app.state.audit_logger.log_operation(
        action="mock_seller_central_manual_execution", status="success",
        details={
            "tenant_id": principal.tenant_id, "connection_id": connection.id, "issue_id": issue.id,
            "execution_result_id": execution.id, "action_type": payload.action_type,
            "event_accepted": accepted, "before_snapshot": result.get("before"), "actual_change": result.get("after"),
            "data_source": "mock_amazon", "verified_amazon_data": False,
        }, actor=principal.user_id, risk_level="high",
    )
    return {
        "execution_id": execution.id, "issue_status": updated_issue.status,
        "event_accepted": accepted, "before": result.get("before"), "after": result.get("after"),
        "data_source": "mock_amazon", "verified_amazon_data": False,
    }


@manual_execution_router.get("/{execution_id}")
async def get_manual_execution(execution_id: str, request: Request) -> dict[str, Any]:
    _require_read(request)
    row = request.app.state.manual_execution_repository.get(
        tenant_id=request.state.principal.tenant_id, execution_id=execution_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Manual execution result not found."})
    return {
        "id": row.id, "issue_id": row.issue_id, "recommended_action_id": row.recommended_action_id,
        "action_type": row.action_type, "status": row.status, "before_snapshot": row.before_snapshot,
        "approved_change": row.approved_change, "actual_change": row.actual_change,
        "execution_evidence": row.execution_evidence, "executed_by": row.executed_by,
        "executed_at": row.executed_at, "verification_status": row.verification_status,
        "verification_details": row.verification_details, "verified_at": row.verified_at, "notes": row.notes,
    }


async def _run_mock_reconciliation(app, tenant_id: str, connection_id: str) -> None:
    from api.amazon_factory import create_amazon_adapter
    from application.reconciliation import AmazonReconciliationService
    from infrastructure.repositories.commerce_sync import CommerceSyncRepository
    from infrastructure.repositories.reconciliation import ReconciliationRepository

    connection = app.state.tenant_repository.get_connection(tenant_id=tenant_id, connection_id=connection_id)
    if connection is None:
        return
    adapter = create_amazon_adapter(
        settings=app.state.settings, connection=connection, secret_store=app.state.secret_store,
        tenant_repository=app.state.tenant_repository,
    )
    try:
        await AmazonReconciliationService(
            adapter=adapter, connection=connection,
            commerce_repository=CommerceSyncRepository(app.state.session_factory),
            reconciliation_repository=ReconciliationRepository(app.state.session_factory),
            issue_repository=app.state.operational_issue_repository,
        ).run()
    finally:
        await adapter.close()


async def _accept_and_schedule_event(request: Request, background_tasks: BackgroundTasks, connection, event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    payload = {**event, "_connection_id": connection.id}
    accepted = request.app.state.event_repository.accept_inbox(
        tenant_id=connection.tenant_id, provider="mock_amazon",
        external_event_id=str(event["external_event_id"]), event_type=str(event["event_type"]), payload=payload,
    )
    if not accepted:
        return False
    if request.app.state.settings.task_backend == "celery":
        from application.celery_app import consume_amazon_notifications
        consume_amazon_notifications.delay()
    else:
        resources = _resources_for_event(str(event["event_type"]))
        task = request.app.state.task_repository.create(
            tenant_id=connection.tenant_id, actor_id="mock-amazon-notification", queue_name="sync", action="amazon.sync",
            payload={"connection_id": connection.id, "resources": resources},
            idempotency_key=f"mock-notification:{event['external_event_id']}:{','.join(resources)}",
            correlation_id=str(event["external_event_id"]), max_attempts=5,
        )
        from api.routes.sync_routes import _run_sync_job
        background_tasks.add_task(_run_sync_job, request.app, connection.tenant_id, task.id, connection.id, tuple(resources))
    return True


def _resources_for_event(event_type: str) -> list[str]:
    normalized = event_type.upper()
    if "LISTING" in normalized or "OFFER" in normalized:
        return ["listings"]
    if "INVENTORY" in normalized:
        return ["inventory"]
    if "ORDER" in normalized or "FULFILLMENT" in normalized:
        return ["orders"]
    raise HTTPException(status_code=422, detail={"error": "unknown_mock_event", "message": f"No sync mapping for {event_type}."})


def _port(request: Request) -> HttpMockSellerCentralAdapter:
    return HttpMockSellerCentralAdapter(request.app.state.settings.amazon_sp_api_endpoint)


def _connection(request: Request, connection_id: str):
    connection = request.app.state.tenant_repository.get_connection(
        tenant_id=request.state.principal.tenant_id, connection_id=connection_id,
    )
    if connection is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Mock Amazon connection not found."})
    return connection


def _require_enabled(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.mock_drill_enabled:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Mock operations drill is disabled."})
    if settings.amazon_writes_enabled or not settings.amazon_pilot_read_only:
        raise HTTPException(status_code=409, detail={"error": "unsafe_mock_configuration", "message": "Mock drill requires Amazon writes disabled and pilot read-only enabled."})


def _require_read(request: Request) -> None:
    if "read" not in request.state.principal.scopes and "admin" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})


def _require_operator(request: Request) -> None:
    if request.state.principal.role not in {"owner", "admin", "operator"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Operator role is required."})
    if "write" not in request.state.principal.scopes and "admin" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "write scope is required."})


def _require_admin(request: Request) -> None:
    if request.state.principal.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Owner or admin role is required."})
