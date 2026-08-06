from __future__ import annotations

import asyncio
import importlib
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from agents import registry
from agents.tools import platform_api, vision_tool
from api.oauth_state import OAuthStateManager
from application import actions, agent_dispatch
from application import celery_app as celery_module

PROMPT_MODULES = [
    "customer_case_v1",
    "customer_case_v2",
    "customer_case_v3",
    "customer_case_v4",
    "listing_optimization_v1",
    "listing_optimization_v2",
    "listing_optimization_v3",
    "listing_optimization_v4",
    "operations_brief_v1",
    "operations_brief_v2",
    "operations_brief_v3",
    "operations_brief_v4",
    "operations_brief_v7",
    "reconciliation_investigation_v1",
    "reconciliation_investigation_v2",
    "reconciliation_investigation_v3",
    "reconciliation_investigation_v4",
    "reconciliation_investigation_v5",
    "reconciliation_investigation_v6",
]


@pytest.mark.parametrize("module_name", PROMPT_MODULES)
def test_versioned_prompt_modules_remain_importable_and_bounded(module_name: str) -> None:
    module = importlib.import_module(f"agents.prompts.{module_name}")
    versions = [value for name, value in vars(module).items() if name.endswith("_PROMPT_VERSION")]
    prompts = [value for name, value in vars(module).items() if name.endswith("_SYSTEM_PROMPT")]

    assert len(versions) == 1 and versions[0]
    assert len(prompts) == 1 and "evidence" in prompts[0].lower()


def test_action_registry_rejects_unknown_and_normalizes_scalar_result(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Unsupported durable action"):
        actions.execute_action("unknown", {})

    monkeypatch.setitem(actions.ACTION_HANDLERS, "test.scalar", lambda value: value)
    monkeypatch.setitem(actions.ACTION_HANDLERS, "test.mapping", lambda value: {"value": value})
    assert actions.execute_action("test.scalar", {"value": 7}) == {"value": 7}
    assert actions.execute_action("test.mapping", {"value": 8}) == {"value": 8}


def test_advisor_registry_validates_keys_duplicates_and_sorted_listing() -> None:
    advisors = registry.AdvisorRegistry({"zeta": "z"})
    with pytest.raises(ValueError, match="workflow_type is required"):
        advisors.register("", "invalid")
    advisors.register("alpha", "a")
    with pytest.raises(ValueError, match="already registered"):
        advisors.register("alpha", "duplicate")
    assert advisors.get("alpha") == "a"
    assert advisors.workflow_types() == ("alpha", "zeta")
    with pytest.raises(KeyError, match="Unsupported agent workflow_type"):
        advisors.get("missing")


def test_in_memory_agent_dispatch_executes_once_and_reuses_task() -> None:
    class Store:
        def create_run_once(self, **_: Any) -> tuple[str, bool]:
            return "run-1", True

        def get_run(self, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(status="received")

    class Queue:
        def __init__(self) -> None:
            self.handler: Any = None
            self.calls = 0

        def enqueue(self, **kwargs: Any) -> SimpleNamespace:
            self.calls += 1
            self.handler = kwargs["handler"]
            return SimpleNamespace(task_id="task-1", status=SimpleNamespace(value="pending"))

    class Execution:
        async def run_advisory(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["reserved_run_id"] == "run-1"
            return {"status": "succeeded"}

    queue = Queue()
    workflow = agent_dispatch.QueuedAgentWorkflow(store=Store(), task_queue=queue, execution_workflow=Execution())
    kwargs = {
        "run_id": "run-1",
        "tenant_id": "tenant-1",
        "actor_id": "operator-1",
        "correlation_id": "correlation-1",
        "workflow_type": "operations_brief",
        "request": {},
        "evidence_collector": object(),
        "advisor": object(),
    }
    first = workflow._enqueue(**kwargs)
    second = workflow._enqueue(**kwargs)

    assert first is second
    assert queue.calls == 1
    assert asyncio.run(queue.handler()) == {"status": "succeeded"}

    unavailable = agent_dispatch.QueuedAgentWorkflow(store=Store(), task_queue=Queue())
    with pytest.raises(RuntimeError, match="requires an execution workflow"):
        unavailable._enqueue(**kwargs)


def test_oauth_state_redis_single_use_and_invalid_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    class RedisClient:
        def __init__(self) -> None:
            self.accept = True
            self.calls: list[tuple[Any, ...]] = []

        def set(self, *args: Any, **kwargs: Any) -> bool:
            self.calls.append((*args, kwargs))
            accepted = self.accept
            self.accept = False
            return accepted

    redis = RedisClient()
    manager = OAuthStateManager("secret")
    manager.redis = redis  # type: ignore[assignment]
    value = manager.issue("tenant-1")
    assert manager.consume(value).tenant_id == "tenant-1"
    with pytest.raises(ValueError, match="already used"):
        manager.consume(value)
    assert redis.calls[0][-1]["nx"] is True

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        manager.consume("malformed")
    tampered = value.rsplit(".", 1)[0] + ".invalid"
    with pytest.raises(ValueError, match="Invalid or expired"):
        manager.consume(tampered)

    monkeypatch.setattr("api.oauth_state.time.time", lambda: 2_000_000_000)
    expired_manager = OAuthStateManager("secret", ttl_seconds=-1)
    expired = expired_manager.issue("tenant-1")
    with pytest.raises(ValueError, match="Invalid or expired"):
        expired_manager.consume(expired)


@pytest.mark.asyncio
async def test_platform_api_wrappers_build_bounded_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, "path": path, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(platform_api, "_request", request)
    with pytest.raises(ValueError, match="At least one"):
        await platform_api.update_product("product-1")
    await platform_api.get_product_list(status="NORMAL")
    await platform_api.get_order_list(status="READY_TO_SHIP")
    await platform_api.update_order_status("order-1", "SHIPPED")
    await platform_api.approve_return("return-1")
    assert [call["path"] for call in calls] == [
        "/api/v2/product/list",
        "/api/v2/order/list",
        "/api/v2/order/status",
        "/api/v2/order/return/approve",
    ]


def test_vision_environment_fallbacks_and_synthesis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_TIMEOUT_SECONDS", "invalid")
    monkeypatch.setenv("VISION_MAX_IMAGE_BYTES", "invalid")
    assert vision_tool._vision_timeout() == vision_tool.DEFAULT_VISION_TIMEOUT_SECONDS
    assert vision_tool._max_image_bytes() == vision_tool.DEFAULT_MAX_IMAGE_BYTES

    class BrokenOpenAI:
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(vision_tool, "OpenAI", BrokenOpenAI)
    monkeypatch.setattr(
        vision_tool,
        "get_settings",
        lambda: SimpleNamespace(model_base_url="http://model", model_name="m", model_api_key="k"),
    )
    assert (
        vision_tool.synthesize_visual_customer_answer(
            question="What is shown?",
            vision_analysis="Visible box",
            rag_answer="No policy match",
            grounded=False,
        )
        == ""
    )


def test_celery_reconciliation_closes_adapter_on_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from application import reconciliation as reconciliation_module
    from infrastructure.repositories import operations as operations_repository_module
    from infrastructure.repositories import reconciliation as reconciliation_repository_module

    updates: list[dict[str, Any]] = []
    tasks = SimpleNamespace(update_status=lambda **kwargs: updates.append(kwargs))
    connection = SimpleNamespace(id="connection-1")
    tenants = SimpleNamespace(get_connection=lambda **_: connection)
    closed: list[bool] = []

    class Adapter:
        async def close(self) -> None:
            closed.append(True)

    class Service:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self) -> SimpleNamespace:
            return SimpleNamespace(id="run-1", status="succeeded", summary={"differences": 0})

    monkeypatch.setattr(celery_module, "tenant_context", lambda _: nullcontext())
    monkeypatch.setattr(celery_module, "create_session_factory", lambda _: object())
    monkeypatch.setattr(celery_module, "DurableTaskRepository", lambda _: tasks)
    monkeypatch.setattr(celery_module, "TenantRepository", lambda _: tenants)
    monkeypatch.setattr(celery_module, "create_secret_store", lambda *_: object())
    monkeypatch.setattr(celery_module, "create_amazon_adapter", lambda **_: Adapter())
    monkeypatch.setattr(celery_module, "CommerceSyncRepository", lambda _: object())
    monkeypatch.setattr(reconciliation_repository_module, "ReconciliationRepository", lambda _: object())
    monkeypatch.setattr(operations_repository_module, "OperationalIssueRepository", lambda _: object())
    monkeypatch.setattr(reconciliation_module, "AmazonReconciliationService", Service)

    result = celery_module.amazon_reconciliation_task.run("task-1", "tenant-1", "connection-1")
    assert result == {"run_id": "run-1", "status": "succeeded", "summary": {"differences": 0}}
    assert updates[-1]["status"] == "succeeded"
    assert closed == [True]

    class FailingService(Service):
        async def run(self) -> Any:
            raise TimeoutError("provider timeout")

    monkeypatch.setattr(reconciliation_module, "AmazonReconciliationService", FailingService)
    with pytest.raises(TimeoutError, match="provider timeout"):
        celery_module.amazon_reconciliation_task.run("task-2", "tenant-1", "connection-1")
    assert updates[-1]["status"] == "failed"
    assert closed == [True, True]
