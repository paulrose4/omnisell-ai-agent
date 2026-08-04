from __future__ import annotations

import asyncio
import base64
import inspect
from pathlib import Path
from typing import Any

import httpx

from agents.tools import vision_tool
from api.config import Settings
from api.main import create_app
from api.routes import customer_routes

_ONE_PIXEL_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


class FakeTranslator:
    def detect_language(self, text: str) -> str:
        return "zh"

    def translate_query(self, question: str) -> str:
        return question

    def translate_answer(self, answer: str, target_language: str, source_language: str = "zh") -> str:
        return answer


def test_vision_tool_normalizes_data_url_and_uses_llava(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_post(base_url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        captured["base_url"] = base_url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {"response": "场景判断：订单截图\n可见关键信息：订单号 A1001，金额 49.99。"}

    monkeypatch.setattr(vision_tool, "_post_ollama_generate", fake_post)

    result = vision_tool.analyze_customer_image(
        question="请帮我看一下这张截图",
        image_base64=f"data:image/png;base64,{_ONE_PIXEL_PNG}",
        target_language="zh",
    )

    assert result["vision_model"] == "llava:7b"
    assert result["vision_base_url"] == "http://localhost:11434"
    assert "订单截图" in result["analysis"]
    assert captured["payload"]["model"] == "llava:7b"
    assert captured["payload"]["images"] == [_ONE_PIXEL_PNG]
    assert "Customer question" in captured["payload"]["prompt"]


def test_visual_customer_question_combines_vision_and_rag(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        vision_tool,
        "analyze_customer_image",
        lambda **kwargs: {
            "question": kwargs["question"],
            "vision_model": "llava:7b",
            "vision_base_url": "http://localhost:11434",
            "analysis": "场景判断：售后凭证。可见关键信息：耳机外壳破损。",
            "task_hint": "售后凭证",
        },
    )
    monkeypatch.setattr(
        vision_tool,
        "answer_customer_question",
        lambda **kwargs: {
            "question": kwargs["question"],
            "detected_language": "zh",
            "target_language": "zh",
            "normalized_query": kwargs["question"],
            "answer": "请在订单详情页提交售后申请并上传凭证，最终以人工审核为准。",
            "context_used": [{"text": "退货政策", "metadata": {"category": "退换货"}, "score": 0.9}],
            "grounded": True,
        },
    )
    monkeypatch.setattr(vision_tool, "synthesize_visual_customer_answer", lambda **kwargs: "")

    result = vision_tool.answer_visual_customer_question(
        question="这个能退吗？",
        image_base64=_ONE_PIXEL_PNG,
        target_language="zh",
        category_filter="退换货",
    )

    assert result["grounded"] is True
    assert "耳机外壳破损" in result["answer"]
    assert "提交售后申请" in result["answer"]
    assert result["vision"]["vision_model"] == "llava:7b"


def test_customer_image_route_uses_stable_advisor_name() -> None:
    assert customer_routes.CUSTOMER_ADVISOR_NAME == "CustomerCaseAdvisor"


def test_customer_image_api_route(monkeypatch: Any, tmp_path: Path) -> None:
    asyncio.run(_run_customer_image_api_route(monkeypatch, tmp_path))


async def _run_customer_image_api_route(monkeypatch: Any, tmp_path: Path) -> None:
    settings = Settings(
        api_key="test-key",
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=1000,
    )
    app = create_app(settings)

    def fake_answer_visual_customer_question(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["question"] == "这张图里的商品能退吗？"
        assert kwargs["image_base64"] == _ONE_PIXEL_PNG
        return {
            "question": kwargs["question"],
            "detected_language": "zh",
            "target_language": "zh",
            "normalized_query": kwargs["question"],
            "answer": "图片显示商品疑似破损，请提交售后申请并等待人工审核。",
            "context_used": [{"text": "售后政策", "metadata": {"category": "退换货"}, "score": 0.91}],
            "grounded": True,
            "vision": {
                "question": kwargs["question"],
                "vision_model": "llava:7b",
                "vision_base_url": "http://localhost:11434",
                "analysis": "场景判断：售后凭证。",
                "task_hint": "auto",
            },
        }

    monkeypatch.setattr(customer_routes, "answer_visual_customer_question", fake_answer_visual_customer_question)

    for handler in app.router.on_startup:
        result = handler()
        if inspect.isawaitable(result):
            await result

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/customer/ask-image",
            headers={"X-API-Key": "test-key"},
            json={
                "question": "这张图里的商品能退吗？",
                "image_base64": _ONE_PIXEL_PNG,
                "target_language": "zh",
                "category_filter": "退换货",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["grounded"] is True
    assert payload["vision"]["vision_model"] == "llava:7b"
    assert "人工审核" in payload["answer"]

    for handler in app.router.on_shutdown:
        result = handler()
        if inspect.isawaitable(result):
            await result
