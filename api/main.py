from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import Settings, get_settings
from api.amazon_factory import create_secret_store
from api.oauth_state import OAuthStateManager
from api.middleware import RateLimiterMiddleware, RequestTelemetryMiddleware
from api.oauth_client import OAuthClient
from api.rag_service import RAGService
from api.routes import admin_routes, agent_run_routes, amazon_routes, briefing_routes, customer_case_routes, customer_routes, intelligence_routes, inventory_routes, learning_routes, listing_quality_routes, mock_scenario_routes, operations_routes, price_routes, product_routes, profitability_routes, rag_routes, reconciliation_routes, sync_routes, task_routes, work_item_routes
from api.task_queue import InMemoryTaskQueue, MetricsRegistry
from agents.advisors.operations_brief import create_operations_brief_advisor
from agents.advisors.customer_case import create_customer_case_advisor
from agents.advisors.listing_optimization import create_listing_optimization_advisor
from agents.advisors.reconciliation import create_reconciliation_investigator
from agents.quota import InMemoryAgentQuotaLimiter
from agents.registry import AdvisorRegistry
from infrastructure.agent_quota import RedisAgentQuotaLimiter
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.tasks import DurableTaskRepository, EventRepository
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.briefing import DailyOperationsBriefRepository, ListingBasicSignalRepository
from infrastructure.repositories.commerce_sync import CommerceSyncRepository, SyncCursorRepository
from infrastructure.repositories.customer_service import CustomerCaseRepository, SOPPlaybookRepository
from infrastructure.repositories.customer_case_operations import CustomerCaseOperationsBridge
from infrastructure.repositories.hitl import SQLHITLManager
from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository, InventoryPolicyRepository
from infrastructure.repositories.listing_quality import ListingExperimentRepository, ListingQualitySnapshotRepository
from infrastructure.repositories.operational_intelligence import AdvertisingRepository, AmazonOperationalSignalRepository, VoCRepository
from infrastructure.repositories.operations_learning import OperationsLearningRepository
from infrastructure.repositories.manual_execution import ManualExecutionRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.pricing import PriceRecommendationRepository
from infrastructure.repositories.profitability import SKUCostProfileRepository, SKUProfitSnapshotRepository
from infrastructure.repositories.reconciliation import ReconciliationRepository
from infrastructure.repositories.tenants import TenantRepository
from infrastructure.repositories.workflows import SQLWorkflowStore
from infrastructure.repositories.work_items import OperationalWorkItemRepository
from application.agent_services import AgentRunService
from application.agent_dispatch import QueuedAgentWorkflow
from application.operations_agent import OperationsAgentService, OperationsRecommendationPlanner, ReadOnlyOperationsToolExecutor
from application.operations_brief_agent import OperationsBriefAgentService, OperationsBriefEvidenceCollector
from application.briefing import DailyOperationsBriefService
from application.customer_service import CustomerServiceWorkflow
from application.customer_case_agent import CustomerCaseAgentService, CustomerCaseEvidenceCollector
from application.listing_quality import ListingQualityService
from application.listing_optimization_agent import ListingOptimizationAgentService, ListingOptimizationEvidenceCollector
from application.operations import OperationalIssueService
from application.intelligence_ingestion import OperationsIntelligenceIngestionService
from application.operations_runtime import build_operations_refresh_service
from application.pricing import PriceRecommendationService
from application.reconciliation_agent import ReconciliationAgentService, ReconciliationEvidenceCollector
from safety.audit import AuditLogger
from safety.hitl import HITLManager
from safety.tool_auth import PermissionDeniedError
from observability.telemetry import configure_telemetry
from model_gateway.factory import create_configured_gateway
from workflows.engine import PersistentAgentWorkflow
from commerce.customer_messaging import MockBuyerMessagingAdapter


def _create_task_queue(settings: Settings, metrics: MetricsRegistry, task_repository: DurableTaskRepository):
    if settings.task_backend == "celery":
        from api.durable_task_queue import CeleryTaskQueue
        return CeleryTaskQueue(task_repository)
    return InMemoryTaskQueue(
        metrics=metrics,
        workers={"text": settings.text_queue_workers, "vision": settings.vision_queue_workers},
    )


def _create_agent_quota_limiter(settings: Settings):
    quota_arguments = {
        "per_minute": settings.agent_tenant_quota_per_minute,
        "per_day": settings.agent_tenant_quota_per_day,
    }
    if settings.task_backend == "celery":
        return RedisAgentQuotaLimiter.from_url(redis_url=settings.redis_url, **quota_arguments)
    return InMemoryAgentQuotaLimiter(**quota_arguments)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the OmniSell modular-monolith API application."""
    resolved_settings = settings or get_settings()
    Path("tmp").mkdir(exist_ok=True)
    app = FastAPI(
        title=resolved_settings.app_name,
        description="Multi-tenant commerce gateway for OmniSell agents and channel adapters.",
        version="0.2.0",
    )

    configure_telemetry(resolved_settings, app)

    app.state.settings = resolved_settings
    app.state.price_recommendations = {}
    app.state.metrics = MetricsRegistry()
    session_factory = create_session_factory(resolved_settings.database_url)
    app.state.session_factory = session_factory
    app.state.tenant_repository = TenantRepository(session_factory)
    app.state.task_repository = DurableTaskRepository(session_factory)
    app.state.event_repository = EventRepository(session_factory)
    app.state.commerce_sync_repository = CommerceSyncRepository(session_factory)
    app.state.customer_case_repository = CustomerCaseRepository(session_factory)
    app.state.customer_case_operations_bridge = CustomerCaseOperationsBridge(
        session_factory,
        minimum_samples=resolved_settings.customer_case_cluster_minimum_samples,
        window_days=resolved_settings.customer_case_cluster_window_days,
    )
    app.state.sop_playbook_repository = SOPPlaybookRepository(session_factory)
    app.state.mock_buyer_messaging = MockBuyerMessagingAdapter()
    app.state.sync_cursor_repository = SyncCursorRepository(session_factory)
    app.state.reconciliation_repository = ReconciliationRepository(session_factory)
    app.state.cost_profile_repository = SKUCostProfileRepository(session_factory)
    app.state.profit_snapshot_repository = SKUProfitSnapshotRepository(session_factory)
    app.state.inventory_policy_repository = InventoryPolicyRepository(session_factory)
    app.state.inventory_health_repository = InventoryHealthSnapshotRepository(session_factory)
    app.state.price_recommendation_repository = PriceRecommendationRepository(session_factory)
    app.state.price_recommendation_service = PriceRecommendationService(
        cost_repository=app.state.cost_profile_repository,
        profit_repository=app.state.profit_snapshot_repository,
        inventory_repository=app.state.inventory_health_repository,
        recommendation_repository=app.state.price_recommendation_repository,
    )
    app.state.operational_issue_repository = OperationalIssueRepository(session_factory)
    app.state.work_item_repository = OperationalWorkItemRepository(session_factory)
    app.state.amazon_signal_repository = AmazonOperationalSignalRepository(session_factory)
    app.state.advertising_repository = AdvertisingRepository(session_factory)
    app.state.voc_repository = VoCRepository(session_factory)
    app.state.operations_learning_repository = OperationsLearningRepository(session_factory)
    app.state.intelligence_ingestion_service = OperationsIntelligenceIngestionService(
        advertising_repository=app.state.advertising_repository,
        voc_repository=app.state.voc_repository,
        learning_repository=app.state.operations_learning_repository,
    )
    app.state.manual_execution_repository = ManualExecutionRepository(session_factory)
    app.state.recommended_action_repository = RecommendedActionRepository(session_factory)
    app.state.operational_issue_service = OperationalIssueService(
        inventory_repository=app.state.inventory_health_repository,
        profit_repository=app.state.profit_snapshot_repository,
        issue_repository=app.state.operational_issue_repository,
        action_repository=app.state.recommended_action_repository,
    )
    app.state.operations_refresh_service = build_operations_refresh_service(session_factory)
    app.state.listing_quality_snapshot_repository = ListingQualitySnapshotRepository(session_factory)
    app.state.listing_experiment_repository = ListingExperimentRepository(session_factory)
    app.state.listing_quality_service = ListingQualityService(
        snapshot_repository=app.state.listing_quality_snapshot_repository,
        experiment_repository=app.state.listing_experiment_repository,
        issue_repository=app.state.operational_issue_repository,
        action_repository=app.state.recommended_action_repository,
    )
    app.state.daily_brief_repository = DailyOperationsBriefRepository(session_factory)
    app.state.listing_signal_repository = ListingBasicSignalRepository(session_factory)
    app.state.daily_brief_service = DailyOperationsBriefService(
        brief_repository=app.state.daily_brief_repository,
        listing_repository=app.state.listing_signal_repository,
        issue_repository=app.state.operational_issue_repository,
        action_repository=app.state.recommended_action_repository,
    )
    app.state.workflow_store = SQLWorkflowStore(session_factory)
    app.state.secret_store = create_secret_store(resolved_settings, session_factory)
    state_secret = resolved_settings.secret_encryption_key or resolved_settings.api_key or "development-only-state-secret"
    app.state.amazon_oauth_state = OAuthStateManager(
        state_secret,
        redis_url=resolved_settings.redis_url if resolved_settings.task_backend == "celery" else None,
    )
    app.state.task_queue = _create_task_queue(resolved_settings, app.state.metrics, app.state.task_repository)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-API-Key", "X-Request-ID", "X-Tenant-ID", "X-User-ID", "X-User-Role", "X-Permission-Scopes"],
    )
    app.add_middleware(RateLimiterMiddleware, requests_per_minute=resolved_settings.rate_limit_per_minute, redis_url=resolved_settings.redis_url if resolved_settings.task_backend == "celery" else None)
    app.add_middleware(RequestTelemetryMiddleware)

    async def startup() -> None:
        if resolved_settings.auto_create_schema:
            initialize_database(resolved_settings.database_url)
        audit_logger = SQLAuditLogger(session_factory)
        hitl_manager = SQLHITLManager(session_factory, audit_logger)
        app.state.audit_logger = audit_logger
        app.state.hitl_manager = hitl_manager
        app.state.agent_quota_limiter = _create_agent_quota_limiter(resolved_settings)
        app.state.customer_service_workflow = CustomerServiceWorkflow(
            case_repository=app.state.customer_case_repository,
            playbook_repository=app.state.sop_playbook_repository,
            commerce_repository=app.state.commerce_sync_repository,
            hitl_manager=hitl_manager,
            audit_logger=audit_logger,
        )
        app.state.operations_agent_service = OperationsAgentService(
            workflow=PersistentAgentWorkflow(
                store=app.state.workflow_store,
                planner=OperationsRecommendationPlanner(),
                executor=ReadOnlyOperationsToolExecutor(),
                approval_manager=hitl_manager,
                audit_logger=audit_logger,
            ),
            workflow_store=app.state.workflow_store,
            issue_repository=app.state.operational_issue_repository,
            action_repository=app.state.recommended_action_repository,
        )
        app.state.model_gateway = create_configured_gateway(resolved_settings)
        operations_brief_advisor = create_operations_brief_advisor(
            gateway=app.state.model_gateway, model=resolved_settings.vllm_model_name
        )
        customer_case_advisor = create_customer_case_advisor(
            gateway=app.state.model_gateway, model=resolved_settings.vllm_model_name
        )
        listing_optimization_advisor = create_listing_optimization_advisor(
            gateway=app.state.model_gateway, model=resolved_settings.vllm_model_name
        )
        reconciliation_investigator = create_reconciliation_investigator(
            gateway=app.state.model_gateway, model=resolved_settings.vllm_model_name
        )
        app.state.advisor_registry = AdvisorRegistry({
            OperationsBriefAgentService.WORKFLOW_TYPE: operations_brief_advisor,
            CustomerCaseAgentService.WORKFLOW_TYPE: customer_case_advisor,
            ListingOptimizationAgentService.WORKFLOW_TYPE: listing_optimization_advisor,
            ReconciliationAgentService.WORKFLOW_TYPE: reconciliation_investigator,
        })
        app.state.agent_execution_workflow = PersistentAgentWorkflow(
            store=app.state.workflow_store,
            approval_manager=hitl_manager,
            audit_logger=audit_logger,
            metrics=app.state.metrics,
            quota_limiter=app.state.agent_quota_limiter,
        )
        app.state.agent_submission_workflow = QueuedAgentWorkflow(
            store=app.state.workflow_store,
            task_queue=app.state.task_queue,
            execution_workflow=app.state.agent_execution_workflow,
        )
        app.state.operations_brief_agent_service = OperationsBriefAgentService(
            workflow=app.state.agent_submission_workflow,
            registry=app.state.advisor_registry,
            evidence_collector=OperationsBriefEvidenceCollector(
                brief_repository=app.state.daily_brief_repository,
                issue_repository=app.state.operational_issue_repository,
                action_repository=app.state.recommended_action_repository,
            ),
            budget=resolved_settings.agent_budget_profiles[OperationsBriefAgentService.WORKFLOW_TYPE],
        )
        app.state.customer_case_agent_service = CustomerCaseAgentService(
            workflow=app.state.agent_submission_workflow,
            registry=app.state.advisor_registry,
            evidence_collector=CustomerCaseEvidenceCollector(
                case_repository=app.state.customer_case_repository,
            ),
            budget=resolved_settings.agent_budget_profiles[CustomerCaseAgentService.WORKFLOW_TYPE],
        )
        app.state.listing_optimization_agent_service = ListingOptimizationAgentService(
            workflow=app.state.agent_submission_workflow,
            registry=app.state.advisor_registry,
            evidence_collector=ListingOptimizationEvidenceCollector(
                snapshot_repository=app.state.listing_quality_snapshot_repository,
                experiment_repository=app.state.listing_experiment_repository,
            ),
            budget=resolved_settings.agent_budget_profiles[ListingOptimizationAgentService.WORKFLOW_TYPE],
        )
        app.state.reconciliation_agent_service = ReconciliationAgentService(
            workflow=app.state.agent_submission_workflow,
            registry=app.state.advisor_registry,
            evidence_collector=ReconciliationEvidenceCollector(
                reconciliation_repository=app.state.reconciliation_repository,
                connection_repository=app.state.tenant_repository,
                task_repository=app.state.task_repository,
                event_repository=app.state.event_repository,
                audit_logger=audit_logger,
            ),
            budget=resolved_settings.agent_budget_profiles[ReconciliationAgentService.WORKFLOW_TYPE],
        )
        app.state.agent_run_service = AgentRunService(
            store=app.state.workflow_store,
            workflow=app.state.agent_submission_workflow,
            registry=app.state.advisor_registry,
            evidence_collectors={
                OperationsBriefAgentService.WORKFLOW_TYPE: app.state.operations_brief_agent_service.evidence_collector,
                CustomerCaseAgentService.WORKFLOW_TYPE: app.state.customer_case_agent_service.evidence_collector,
                ListingOptimizationAgentService.WORKFLOW_TYPE: app.state.listing_optimization_agent_service.evidence_collector,
                ReconciliationAgentService.WORKFLOW_TYPE: app.state.reconciliation_agent_service.evidence_collector,
            },
            action_resume=app.state.operations_agent_service.resume,
        )
        app.state.oauth_client = OAuthClient(resolved_settings)
        app.state.rag_service = RAGService()
        await app.state.task_queue.start()
        app.state.audit_logger.log_operation(
            action="api_startup",
            status="success",
            details={
                "environment": resolved_settings.environment,
                "database": resolved_settings.database_url.split("@")[-1],
                "task_backend": resolved_settings.task_backend,
                "auth_mode": resolved_settings.auth_mode,
            },
            actor="api",
            risk_level="low",
        )

    async def shutdown() -> None:
        await app.state.task_queue.shutdown()
        model_gateway = getattr(app.state, "model_gateway", None)
        close_gateway = getattr(model_gateway, "close", None)
        if close_gateway is not None:
            await close_gateway()
        quota_limiter = getattr(app.state, "agent_quota_limiter", None)
        close = getattr(quota_limiter, "close", None)
        if close is not None:
            await close()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await startup()
        try:
            yield
        finally:
            await shutdown()

    app.router.lifespan_context = lifespan
    app.router.on_startup.append(startup)
    app.router.on_shutdown.append(shutdown)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        content = detail if isinstance(detail, dict) else {"error": "http_error", "message": str(detail)}
        content.setdefault("path", request.url.path)
        content.setdefault("correlation_id", getattr(request.state, "request_id", None))
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "Request validation failed.",
                "details": exc.errors(),
                "path": request.url.path,
                "correlation_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(PermissionDeniedError)
    async def permission_denied_exception_handler(request: Request, exc: PermissionDeniedError) -> JSONResponse:
        content = exc.to_response()
        content.setdefault("path", request.url.path)
        content.setdefault("correlation_id", getattr(request.state, "request_id", None))
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        audit_logger = getattr(request.app.state, "audit_logger", None)
        correlation_id = getattr(request.state, "request_id", None)
        if audit_logger is not None:
            audit_logger.log_operation(
                action="unhandled_exception", status="failure",
                details={"correlation_id": correlation_id, "exception_type": type(exc).__name__},
                actor="api", risk_level="high",
            )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "message": "An unexpected error occurred.", "path": request.url.path, "correlation_id": correlation_id},
        )

    app.include_router(admin_routes.router)
    app.include_router(admin_routes.protected_router)
    app.include_router(agent_run_routes.router)
    app.include_router(amazon_routes.callback_router)
    app.include_router(amazon_routes.router)
    app.include_router(product_routes.router)
    if resolved_settings.environment != "production":
        app.include_router(customer_routes.router)
        app.include_router(rag_routes.router)
    app.include_router(customer_case_routes.router)
    if resolved_settings.environment != "production" and resolved_settings.mock_drill_enabled:
        app.include_router(customer_case_routes.mock_router)
    app.include_router(price_routes.router)
    app.include_router(profitability_routes.router)
    app.include_router(inventory_routes.router)
    app.include_router(learning_routes.router)
    app.include_router(listing_quality_routes.router)
    app.include_router(operations_routes.router)
    if resolved_settings.environment != "production" and resolved_settings.mock_drill_enabled:
        app.include_router(mock_scenario_routes.router)
        app.include_router(mock_scenario_routes.seller_central_router)
    app.include_router(mock_scenario_routes.manual_execution_router)
    app.include_router(briefing_routes.router)
    app.include_router(task_routes.router)
    app.include_router(work_item_routes.router)
    app.include_router(intelligence_routes.router)
    app.include_router(reconciliation_routes.router)
    app.include_router(sync_routes.router)
    app.include_router(sync_routes.data_quality_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
