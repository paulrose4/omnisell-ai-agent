from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.security import require_api_key
from application.demo_reset import reset_mock_demo_tenant
from application.high_fidelity_mock import HighFidelityStoreConfig, generate_high_fidelity_store
from application.manual_execution_contract import (
    APPROVAL_ACTION,
    validate_operational_approval_payload,
)
from commerce.adapters.high_fidelity_mock_intelligence import HighFidelityMockOperationsSource
from commerce.adapters.mock_operations_intelligence import MockOperationsIntelligenceSource
from commerce.adapters.mock_seller_central import HttpMockSellerCentralAdapter
from commerce.customer_messaging import BuyerMessage
from infrastructure.database.session import tenant_context
from safety.hitl import ApprovalRejectedError, ApprovalStatus
from safety.policy_engine import PolicyDecision

router = APIRouter(
    prefix="/api/mock-scenarios", tags=["mock-operations-drill"], dependencies=[Depends(require_api_key)]
)
seller_central_router = APIRouter(
    prefix="/api/mock-seller-central", tags=["mock-seller-central"], dependencies=[Depends(require_api_key)]
)
manual_execution_router = APIRouter(
    prefix="/api/manual-executions", tags=["manual-executions"], dependencies=[Depends(require_api_key)]
)


class ScenarioRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)


class ManualExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_request_id: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=2000)
    execution_evidence: dict[str, Any] = Field(default_factory=dict, max_length=20)


@seller_central_router.get("/approved-plans")
async def list_approved_manual_execution_plans(request: Request) -> dict[str, Any]:
    _require_enabled(request)
    _require_operator(request)
    tenant_id = request.state.principal.tenant_id
    try:
        approvals = request.app.state.hitl_manager.get_approved(
            tenant_id=tenant_id,
            action=APPROVAL_ACTION,
        )
    except ApprovalRejectedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "approved_plan_mismatch", "message": str(exc)},
        ) from exc

    items: list[dict[str, Any]] = []
    for approval in approvals:
        approval_payload = approval.payload
        issue = request.app.state.operational_issue_repository.get(
            tenant_id=tenant_id,
            issue_id=str(approval_payload.get("issue_id") or ""),
        )
        action = request.app.state.recommended_action_repository.get(
            tenant_id=tenant_id,
            action_id=str(approval_payload.get("recommended_action_id") or ""),
        )
        connection = request.app.state.tenant_repository.get_connection(
            tenant_id=tenant_id,
            connection_id=str(approval_payload.get("connection_id") or ""),
        )
        if issue is None or action is None or connection is None:
            continue
        if issue.status != "approved_for_manual_execution" or action.status != "accepted":
            continue
        if (
            request.app.state.manual_execution_repository.get_by_approval(
                tenant_id=tenant_id,
                approval_request_id=approval.request_id,
            )
            is not None
        ):
            continue
        try:
            contract = validate_operational_approval_payload(
                payload=approval_payload,
                issue=issue,
                action=action,
                connection=connection,
            )
        except ValueError:
            continue
        items.append(
            {
                "approval_request_id": approval.request_id,
                "action_hash": contract.action_hash,
                "execution_contract_version": contract.execution_plan.execution_contract_version,
                "execution_plan": contract.execution_plan.model_dump(mode="json"),
                "expires_at": approval.to_dict()["expires_at"],
                "issue": {
                    "id": issue.id,
                    "seller_sku": issue.seller_sku,
                    "marketplace_id": issue.marketplace_id,
                    "title": issue.title,
                    "summary": issue.summary,
                    "severity": issue.severity,
                    "status": issue.status,
                    "data_freshness_at": issue.data_freshness_at,
                },
                "recommended_action": {
                    "id": action.id,
                    "action_type": action.action_type,
                    "title": action.title,
                    "description": action.description,
                    "risk_level": action.risk_level,
                    "status": action.status,
                    "expected_outcome": action.expected_outcome,
                },
                "approvals": {
                    "requester": approval.requester,
                    "first_approver_id": approval.first_approver_id,
                    "first_approved_at": approval.first_approved_at,
                    "second_approver_id": approval.second_approver_id,
                    "second_approved_at": approval.second_approved_at,
                },
            }
        )
    return {"total": len(items), "items": items}


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
        action="mock_operations_learning_seeded",
        status="success",
        details={"source": "mock", "verified_amazon_data": False, **result},
        actor=principal.user_id,
        risk_level="low",
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
        raise HTTPException(
            status_code=422, detail={"error": "invalid_high_fidelity_config", "message": str(exc)}
        ) from exc
    reference_time = payload.reference_time.astimezone(UTC)
    dataset = generate_high_fidelity_store(config=config, reference_time=reference_time)
    task, created = request.app.state.task_repository.create_once(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        queue_name="operations",
        action="mock.high_fidelity.seed",
        payload={
            "connection_id": connection.id,
            "config": config_payload,
            "reference_time": reference_time.isoformat(),
            "dataset_fingerprint": dataset.fingerprint,
        },
        idempotency_key=f"high-fidelity-seed:{connection.id}:{dataset.fingerprint}",
        correlation_id=f"high-fidelity:{dataset.fingerprint[:16]}",
        max_attempts=1,
    )
    if created:
        background_tasks.add_task(
            _run_high_fidelity_seed,
            request.app,
            principal.tenant_id,
            principal.user_id,
            task.id,
            connection.id,
            config,
            reference_time,
            dataset,
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
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "High-fidelity seed task not found."}
        )
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
            ).load_high_fidelity(
                {
                    **config.__dict__,
                    "reference_time": reference_time.isoformat(),
                }
            )
            intelligence = await asyncio.to_thread(
                app.state.intelligence_ingestion_service.ingest,
                source=HighFidelityMockOperationsSource(dataset),
                tenant_id=tenant_id,
                marketplace_id=app.state.settings.amazon_marketplaces[0],
            )

            sync_task = app.state.task_repository.create(
                tenant_id=tenant_id,
                actor_id=actor_id,
                queue_name="sync",
                action="amazon.sync",
                payload={"connection_id": connection_id, "resources": ["orders", "inventory", "listings"]},
                idempotency_key=f"high-fidelity-sync:{connection_id}:{dataset.fingerprint}",
                correlation_id=f"high-fidelity:{dataset.fingerprint[:16]}",
                max_attempts=1,
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
                app,
                tenant_id,
                actor_id,
                dataset,
                config.customer_case_count,
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
                action="high_fidelity_mock_store_seeded",
                status="success",
                details={
                    "tenant_id": tenant_id,
                    "connection_id": connection_id,
                    "fingerprint": dataset.fingerprint,
                    "counts": dataset.counts,
                    "source": "mock",
                    "verified_amazon_data": False,
                    "amazon_write_performed": False,
                },
                actor=actor_id,
                risk_level="medium",
            )
            app.state.task_repository.update_status(
                tenant_id=tenant_id,
                task_id=task_id,
                status="succeeded",
                result=result,
            )
        except Exception as exc:
            app.state.task_repository.update_status(
                tenant_id=tenant_id,
                task_id=task_id,
                status="dead_letter",
                error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )


def _create_high_fidelity_cases(app, tenant_id: str, actor_id: str, dataset, limit: int) -> list[str]:
    created_case_ids: list[str] = []
    for item in dataset.customer_messages[:limit]:
        case = app.state.customer_service_workflow.intake(
            tenant_id=tenant_id,
            actor_id=actor_id,
            message=BuyerMessage(
                external_case_id=f"hf-case-{item['external_message_id']}",
                scenario_id=item["scenario_id"],
                external_order_id=item["external_order_id"],
                marketplace_id=item["marketplace_id"],
                language=item["language"],
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
        action="mock_amazon_scenarios_reset",
        status="success",
        details={"tenant_id": request.state.principal.tenant_id, "verified_amazon_data": False, "database_reset": True},
        actor=request.state.principal.user_id,
        risk_level="medium",
    )
    return {**result, **database_reset}


@router.post("/{scenario_id}/trigger")
async def trigger_scenario(
    scenario_id: str, payload: ScenarioRequest, background_tasks: BackgroundTasks, request: Request
) -> dict[str, Any]:
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
                tenant_id=connection.tenant_id,
                actor_id=request.state.principal.user_id,
                queue_name="sync",
                action="amazon.reconciliation",
                payload={"connection_id": connection.id},
                idempotency_key=f"mock-reconciliation:{connection.id}:{bucket}",
                correlation_id=getattr(request.state, "request_id", bucket),
                max_attempts=5,
            )
            amazon_reconciliation_task.delay(task.id, connection.tenant_id, connection.id)
        else:
            background_tasks.add_task(_run_mock_reconciliation, request.app, connection.tenant_id, connection.id)
    else:
        accepted = await _accept_and_schedule_event(request, background_tasks, connection, result.get("event"))
    request.app.state.audit_logger.log_operation(
        action="mock_amazon_scenario_triggered",
        status="success",
        details={
            "tenant_id": request.state.principal.tenant_id,
            "scenario_id": scenario_id,
            "connection_id": connection.id,
            "event_accepted": accepted,
            "data_source": "mock_amazon",
            "verified_amazon_data": False,
        },
        actor=request.state.principal.user_id,
        risk_level="medium",
    )
    return {**result, "event_accepted": accepted, "data_source": "mock_amazon", "verified_amazon_data": False}


@seller_central_router.post("/execute")
async def execute_manual_action(
    payload: ManualExecutionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> dict[str, Any]:
    _require_enabled(request)
    _require_operator(request)
    principal = request.state.principal
    existing_by_idempotency = request.app.state.manual_execution_repository.get_by_idempotency(
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    if existing_by_idempotency is not None:
        if existing_by_idempotency.approval_request_id != payload.approval_request_id:
            raise HTTPException(
                status_code=409,
                detail={"error": "idempotency_conflict", "message": "Idempotency key belongs to another approval."},
            )
        return _replay_manual_execution(request, existing_by_idempotency)

    try:
        approval = request.app.state.hitl_manager.get_request(
            payload.approval_request_id,
            tenant_id=principal.tenant_id,
        )
    except ApprovalRejectedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "approved_plan_mismatch", "message": str(exc)},
        ) from exc
    if approval is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Approved execution plan not found."}
        )
    if (
        approval.action != APPROVAL_ACTION
        or approval.policy_decision != PolicyDecision.REQUIRE_APPROVAL
        or approval.status != ApprovalStatus.APPROVED
    ):
        raise HTTPException(
            status_code=409,
            detail={"error": "approval_required", "message": "Execution requires a completed human approval."},
        )
    if approval.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=409,
            detail={"error": "approval_expired", "message": "The approved execution plan has expired."},
        )

    approval_payload = approval.payload
    issue_id = str(approval_payload.get("issue_id") or "")
    action_id = str(approval_payload.get("recommended_action_id") or "")
    connection_id = str(approval_payload.get("connection_id") or "")
    issue = request.app.state.operational_issue_repository.get(tenant_id=principal.tenant_id, issue_id=issue_id)
    action = request.app.state.recommended_action_repository.get(tenant_id=principal.tenant_id, action_id=action_id)
    connection = request.app.state.tenant_repository.get_connection(
        tenant_id=principal.tenant_id,
        connection_id=connection_id,
    )
    if issue is None or action is None or connection is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "stale_approval_context", "message": "An approved execution resource no longer exists."},
        )
    if issue.status != "approved_for_manual_execution" or action.status != "accepted":
        raise HTTPException(
            status_code=409,
            detail={"error": "stale_approval_context", "message": "Issue or action is no longer executable."},
        )
    if issue.seller_sku == "ACCOUNT":
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unsupported_issue",
                "message": "Account-level issues cannot be executed in Mock Seller Central.",
            },
        )
    try:
        contract = validate_operational_approval_payload(
            payload=approval_payload,
            issue=issue,
            action=action,
            connection=connection,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "approved_plan_mismatch", "message": str(exc)},
        ) from exc

    execution_plan = contract.execution_plan
    existing_by_approval = request.app.state.manual_execution_repository.get_by_approval(
        tenant_id=principal.tenant_id,
        approval_request_id=approval.request_id,
    )
    if existing_by_approval is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": "approval_already_executed", "message": "This approval already has an execution record."},
        )
    execution, created = request.app.state.manual_execution_repository.reserve(
        tenant_id=principal.tenant_id,
        issue_id=issue.id,
        approval_request_id=approval.request_id,
        recommended_action_id=action.id,
        action_hash=contract.action_hash,
        execution_contract_version=execution_plan.execution_contract_version,
        idempotency_key=idempotency_key,
        action_type=execution_plan.action_type,
        approved_change=execution_plan.changes.model_dump(mode="json"),
        executed_by=principal.user_id,
        notes=payload.notes or None,
    )
    if not created:
        if execution.idempotency_key == idempotency_key:
            return _replay_manual_execution(request, execution)
        raise HTTPException(
            status_code=409,
            detail={"error": "approval_already_executed", "message": "This approval already has an execution record."},
        )

    try:
        result = await _port(request).execute(
            seller_sku=issue.seller_sku,
            action_type=execution_plan.action_type,
            changes=execution_plan.changes.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        request.app.state.manual_execution_repository.mark_failed(
            tenant_id=principal.tenant_id,
            execution_id=execution.id,
            reason=f"{type(exc).__name__}: {exc}",
        )
        request.app.state.audit_logger.log_operation(
            action="mock_seller_central_manual_execution",
            status="failure",
            details={
                "tenant_id": principal.tenant_id,
                "approval_request_id": approval.request_id,
                "execution_result_id": execution.id,
                "action_hash": contract.action_hash,
                "error_type": type(exc).__name__,
                "data_source": "mock_amazon",
                "verified_amazon_data": False,
            },
            actor=principal.user_id,
            risk_level="high",
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "mock_execution_failed",
                "message": "Mock Seller Central execution failed; manual recovery is required.",
            },
        ) from exc

    execution = request.app.state.manual_execution_repository.complete(
        tenant_id=principal.tenant_id,
        execution_id=execution.id,
        before_snapshot=dict(result.get("before") or {}),
        actual_change=dict(result.get("after") or {}),
        execution_evidence={
            **payload.execution_evidence,
            "mock_seller_central": True,
            "verified_amazon_data": False,
            "approval_request_id": approval.request_id,
            "action_hash": contract.action_hash,
            "mock_event": result.get("event"),
        },
    )
    updated_issue = request.app.state.operational_issue_repository.attach_execution(
        tenant_id=principal.tenant_id,
        issue_id=issue.id,
        execution_result_id=execution.id,
        actor_id=principal.user_id,
    )
    accepted = await _accept_and_schedule_event(request, background_tasks, connection, result.get("event"))
    request.app.state.audit_logger.log_operation(
        action="mock_seller_central_manual_execution",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "connection_id": connection.id,
            "issue_id": issue.id,
            "approval_request_id": approval.request_id,
            "execution_result_id": execution.id,
            "action_type": execution.action_type,
            "action_hash": execution.action_hash,
            "execution_contract_version": execution.execution_contract_version,
            "idempotency_key": idempotency_key,
            "event_accepted": accepted,
            "before_snapshot": result.get("before"),
            "actual_change": result.get("after"),
            "data_source": "mock_amazon",
            "verified_amazon_data": False,
        },
        actor=principal.user_id,
        risk_level="high",
    )
    return {
        "execution_id": execution.id,
        "issue_status": updated_issue.status,
        "event_accepted": accepted,
        "before": result.get("before"),
        "after": result.get("after"),
        "replayed": False,
        "data_source": "mock_amazon",
        "verified_amazon_data": False,
    }


@manual_execution_router.get("/{execution_id}")
async def get_manual_execution(execution_id: str, request: Request) -> dict[str, Any]:
    _require_read(request)
    row = request.app.state.manual_execution_repository.get(
        tenant_id=request.state.principal.tenant_id,
        execution_id=execution_id,
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Manual execution result not found."}
        )
    return {
        "id": row.id,
        "issue_id": row.issue_id,
        "approval_request_id": row.approval_request_id,
        "recommended_action_id": row.recommended_action_id,
        "action_hash": row.action_hash,
        "execution_contract_version": row.execution_contract_version,
        "idempotency_key": row.idempotency_key,
        "action_type": row.action_type,
        "status": row.status,
        "before_snapshot": row.before_snapshot,
        "approved_change": row.approved_change,
        "actual_change": row.actual_change,
        "execution_evidence": row.execution_evidence,
        "executed_by": row.executed_by,
        "executed_at": row.executed_at,
        "completed_at": row.completed_at,
        "failure_reason": row.failure_reason,
        "verification_status": row.verification_status,
        "verification_details": row.verification_details,
        "verified_at": row.verified_at,
        "notes": row.notes,
    }


def _replay_manual_execution(request: Request, execution) -> dict[str, Any]:
    if execution.status == "executing":
        raise HTTPException(
            status_code=409,
            detail={"error": "execution_in_progress", "message": "The approved execution is already in progress."},
        )
    if execution.status == "failed":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "execution_recovery_required",
                "message": "The previous execution outcome is uncertain and requires manual recovery.",
            },
        )
    issue = request.app.state.operational_issue_repository.get(
        tenant_id=request.state.principal.tenant_id,
        issue_id=execution.issue_id,
    )
    if issue is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "execution_recovery_required",
                "message": "The execution exists but its issue is missing.",
            },
        )
    if issue.status == "approved_for_manual_execution" and not issue.execution_result_id:
        issue = request.app.state.operational_issue_repository.attach_execution(
            tenant_id=request.state.principal.tenant_id,
            issue_id=issue.id,
            execution_result_id=execution.id,
            actor_id=request.state.principal.user_id,
        )
    return {
        "execution_id": execution.id,
        "issue_status": issue.status,
        "event_accepted": bool((execution.execution_evidence or {}).get("mock_event")),
        "before": execution.before_snapshot,
        "after": execution.actual_change,
        "replayed": True,
        "data_source": "mock_amazon",
        "verified_amazon_data": False,
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
        settings=app.state.settings,
        connection=connection,
        secret_store=app.state.secret_store,
        tenant_repository=app.state.tenant_repository,
    )
    try:
        await AmazonReconciliationService(
            adapter=adapter,
            connection=connection,
            commerce_repository=CommerceSyncRepository(app.state.session_factory),
            reconciliation_repository=ReconciliationRepository(app.state.session_factory),
            issue_repository=app.state.operational_issue_repository,
        ).run()
    finally:
        await adapter.close()


async def _accept_and_schedule_event(
    request: Request, background_tasks: BackgroundTasks, connection, event: dict[str, Any] | None
) -> bool:
    if not event:
        return False
    payload = {**event, "_connection_id": connection.id}
    accepted = request.app.state.event_repository.accept_inbox(
        tenant_id=connection.tenant_id,
        provider="mock_amazon",
        external_event_id=str(event["external_event_id"]),
        event_type=str(event["event_type"]),
        payload=payload,
    )
    if not accepted:
        return False
    if request.app.state.settings.task_backend == "celery":
        from application.celery_app import consume_amazon_notifications

        consume_amazon_notifications.delay()
    else:
        background_tasks.add_task(_consume_mock_notifications, request.app, connection.tenant_id)
    return True


async def _consume_mock_notifications(app, tenant_id: str) -> None:
    from api.routes.sync_routes import _run_sync_job

    for event in app.state.event_repository.claim_pending(tenant_id=tenant_id, limit=100):
        try:
            resources = _resources_for_event(str(event.event_type))
            connection_id = str((event.payload or {}).get("_connection_id") or "")
            if not connection_id:
                app.state.event_repository.mark_dead_letter(
                    tenant_id=tenant_id,
                    event_id=event.id,
                    error_code="ConnectionNotFound",
                    error_message="Mock notification did not include a connection id.",
                )
                continue
            task = app.state.task_repository.create(
                tenant_id=tenant_id,
                actor_id="mock-amazon-notification",
                queue_name="sync",
                action="amazon.sync",
                payload={"connection_id": connection_id, "resources": resources},
                idempotency_key=f"mock-notification:{event.external_event_id}:{','.join(resources)}",
                correlation_id=str(event.external_event_id),
                max_attempts=5,
            )
            if task.status == "pending":
                await _run_sync_job(app, tenant_id, task.id, connection_id, tuple(resources))
            app.state.event_repository.mark_processed(tenant_id=tenant_id, event_id=event.id)
        except HTTPException as exc:
            app.state.event_repository.mark_dead_letter(
                tenant_id=tenant_id,
                event_id=event.id,
                error_code="UnknownNotificationType",
                error_message=str(exc.detail),
            )
        except Exception as exc:
            app.state.event_repository.mark_retry(
                tenant_id=tenant_id,
                event_id=event.id,
                error=exc,
                max_attempts=5,
            )


def _resources_for_event(event_type: str) -> list[str]:
    normalized = event_type.upper()
    if "LISTING" in normalized or "OFFER" in normalized:
        return ["listings"]
    if "INVENTORY" in normalized:
        return ["inventory"]
    if "ORDER" in normalized or "FULFILLMENT" in normalized:
        return ["orders"]
    raise HTTPException(
        status_code=422, detail={"error": "unknown_mock_event", "message": f"No sync mapping for {event_type}."}
    )


def _port(request: Request) -> HttpMockSellerCentralAdapter:
    return HttpMockSellerCentralAdapter(request.app.state.settings.amazon_sp_api_endpoint)


def _connection(request: Request, connection_id: str):
    connection = request.app.state.tenant_repository.get_connection(
        tenant_id=request.state.principal.tenant_id,
        connection_id=connection_id,
    )
    if connection is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Mock Amazon connection not found."}
        )
    return connection


def _require_enabled(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.mock_drill_enabled:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Mock operations drill is disabled."}
        )
    if settings.amazon_writes_enabled or not settings.amazon_pilot_read_only:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "unsafe_mock_configuration",
                "message": "Mock drill requires Amazon writes disabled and pilot read-only enabled.",
            },
        )


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
        raise HTTPException(
            status_code=403, detail={"error": "forbidden", "message": "Owner or admin role is required."}
        )
