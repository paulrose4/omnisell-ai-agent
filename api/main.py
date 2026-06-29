from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agents import base as agent_base
from agents.group_chat import create_omnisell_team
from api.config import Settings, get_settings
from api.middleware import RateLimiterMiddleware
from api.oauth_client import OAuthClient
from api.routes import admin_routes, customer_routes, price_routes, product_routes
from safety.audit import AuditLogger
from safety.hitl import HITLManager


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the OmniSell API FastAPI application."""
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title=resolved_settings.app_name,
        description="Unified backend gateway for OmniSell AI agents, RAG, safety, and mock commerce APIs.",
        version="0.1.0",
    )

    app.state.settings = resolved_settings
    app.state.price_recommendations = []

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimiterMiddleware, requests_per_minute=resolved_settings.rate_limit_per_minute)

    @app.on_event("startup")
    async def startup() -> None:
        audit_logger = AuditLogger(resolved_settings.audit_log_path)
        hitl_manager = HITLManager(audit_logger=audit_logger)
        agent_base._AUDIT_LOGGER = audit_logger
        agent_base._HITL_MANAGER = hitl_manager

        app.state.audit_logger = audit_logger
        app.state.hitl_manager = hitl_manager
        app.state.oauth_client = OAuthClient(resolved_settings)
        llm_config = agent_base.create_llm_config(
            base_url=resolved_settings.vllm_base_url,
            model_name=resolved_settings.vllm_model_name,
            api_key=resolved_settings.vllm_api_key,
        )
        app.state.team = create_omnisell_team(llm_config=llm_config)
        app.state.audit_logger.log_operation(
            action="api_startup",
            status="success",
            details={
                "environment": resolved_settings.environment,
                "mock_platform_url": resolved_settings.mock_platform_url,
                "chroma": f"{resolved_settings.chroma_host}:{resolved_settings.chroma_port}",
            },
            actor="api",
            risk_level="low",
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        content = detail if isinstance(detail, dict) else {"error": "http_error", "message": str(detail)}
        content.setdefault("path", request.url.path)
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
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": str(exc),
                "path": request.url.path,
            },
        )

    app.include_router(admin_routes.router)
    app.include_router(admin_routes.protected_router)
    app.include_router(product_routes.router)
    app.include_router(customer_routes.router)
    app.include_router(price_routes.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
