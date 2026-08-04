from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

import httpx

from api.config import Settings
from api.main import create_app
from api.routes import customer_routes


class FakeOAuthClient:
    async def get_token(self) -> str:
        return "fake-access-token"


def test_async_tasks_metrics_and_rag_boundary(monkeypatch: Any, tmp_path: Path) -> None:
    asyncio.run(_run_async_tasks_metrics_and_rag_boundary(monkeypatch, tmp_path))


async def _run_async_tasks_metrics_and_rag_boundary(monkeypatch: Any, tmp_path: Path) -> None:
    settings = Settings(
        api_key="test-key",
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=1000,
        text_queue_workers=1,
        vision_queue_workers=1,
    )
    app = create_app(settings)

    def fake_answer_customer_question(**kwargs: Any) -> dict[str, Any]:
        return {
            "question": kwargs["question"],
            "detected_language": "zh",
            "target_language": kwargs.get("target_language") or "zh",
            "normalized_query": kwargs["question"],
            "answer": "Text async answer",
            "context_used": [{"text": "FAQ", "metadata": {"category": "returns"}, "score": 0.9}],
            "grounded": True,
        }

    def fake_answer_visual_customer_question(**kwargs: Any) -> dict[str, Any]:
        return {
            "question": kwargs["question"],
            "detected_language": "zh",
            "target_language": kwargs.get("target_language") or "zh",
            "normalized_query": kwargs["question"],
            "answer": "Vision async answer",
            "context_used": [{"text": "Vision FAQ", "metadata": {"category": "returns"}, "score": 0.91}],
            "grounded": True,
            "vision": {
                "question": kwargs["question"],
                "vision_model": "llava:7b",
                "vision_base_url": "http://localhost:11434",
                "analysis": "damaged product",
                "task_hint": "test",
            },
        }

    monkeypatch.setattr(customer_routes, "answer_customer_question", fake_answer_customer_question)
    monkeypatch.setattr(customer_routes, "answer_visual_customer_question", fake_answer_visual_customer_question)

    for handler in app.router.on_startup:
        result = handler()
        if inspect.isawaitable(result):
            await result
    app.state.oauth_client = FakeOAuthClient()
    app.state.rag_service.search = lambda **kwargs: {  # type: ignore[method-assign]
        "query": kwargs["query"],
        "top_k": kwargs["top_k"],
        "language": kwargs.get("language"),
        "category_filter": kwargs.get("category_filter"),
        "source_type": kwargs.get("source_type"),
        "total": 1,
        "latency_ms": 1.5,
        "items": [{"text": "FAQ", "metadata": {"source_type": "faq"}, "score": 0.9}],
    }

    headers = {
        "X-API-Key": "test-key", "X-Request-ID": "req-test-1",
        "X-User-Role": "admin", "X-Permission-Scopes": "read write admin",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        text_submit = await client.post(
            "/api/customer/ask-async",
            headers=headers,
            json={"question": "How do I return this?", "target_language": "zh", "category_filter": "returns"},
        )
        assert text_submit.status_code == 200
        assert text_submit.headers["X-Request-ID"] == "req-test-1"
        text_task_id = text_submit.json()["task_id"]
        assert text_submit.json()["queue_name"] == "text"
        assert text_submit.json()["status"] == "pending"

        vision_submit = await client.post(
            "/api/customer/ask-image-async",
            headers=headers,
            json={
                "question": "Can I return this damaged product?",
                "image_base64": "iVBORw0KGgo=",
                "target_language": "zh",
                "category_filter": "returns",
            },
        )
        assert vision_submit.status_code == 200
        vision_task_id = vision_submit.json()["task_id"]
        assert vision_submit.json()["queue_name"] == "vision"

        text_payload = await _wait_for_task(client, text_task_id, headers)
        vision_payload = await _wait_for_task(client, vision_task_id, headers)
        assert text_payload["status"] == "succeeded"
        assert text_payload["result"]["answer"] == "Text async answer"
        assert vision_payload["status"] == "succeeded"
        assert vision_payload["result"]["vision"]["vision_model"] == "llava:7b"

        listed = await client.get("/api/tasks", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] >= 2

        rag = await client.post(
            "/api/rag/search",
            headers=headers,
            json={"query": "returns", "top_k": 3, "language": "zh", "source_type": "faq"},
        )
        assert rag.status_code == 200
        assert rag.json()["total"] == 1
        assert rag.json()["items"][0]["metadata"]["source_type"] == "faq"

        metrics = await client.get("/api/admin/metrics", headers=headers)
        assert metrics.status_code == 200
        metric_payload = metrics.json()
        assert metric_payload["tasks_total"] >= 2
        assert metric_payload["tasks_succeeded"] >= 2
        assert "text" in metric_payload["queue_lengths"]
        assert "vision" in metric_payload["queue_lengths"]
        assert metric_payload["request_count_by_path"]["/api/customer/ask-async"] >= 1

        prometheus = await client.get("/api/admin/metrics/prometheus", headers=headers)
        assert prometheus.status_code == 200
        assert "omnisell_tasks_total" in prometheus.text
        assert 'omnisell_queue_length{queue="vision"}' in prometheus.text

        health = await client.get("/api/admin/health")
        assert health.status_code == 200
        assert health.json()["task_queue_ready"] is True
        assert health.json()["rag_service_ready"] is True

    for handler in app.router.on_shutdown:
        result = handler()
        if inspect.isawaitable(result):
            await result


async def _wait_for_task(client: httpx.AsyncClient, task_id: str, headers: dict[str, str]) -> dict[str, Any]:
    for _ in range(30):
        response = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError(f"Task did not finish: {task_id}")
