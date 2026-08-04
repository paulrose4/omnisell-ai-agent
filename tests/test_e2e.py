from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

import httpx

from api.config import Settings
from api.main import create_app
from api.routes import customer_routes, price_routes, product_routes


class FakeOAuthClient:
    async def get_token(self) -> str:
        return "fake-access-token"


def test_api_gateway_e2e_flow(monkeypatch: Any, tmp_path: Path) -> None:
    """健康检查 -> 商品同步 -> 多语言客服 -> 竞价 -> 调价拦截 -> 审计查询。"""
    asyncio.run(_run_e2e_flow(monkeypatch, tmp_path))


async def _run_e2e_flow(monkeypatch: Any, tmp_path: Path) -> None:
    settings = Settings(
        api_key="test-key",
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=1000,
        mock_platform_url="http://mock-platform.test",
        database_url=f"sqlite:///{tmp_path / 'gateway.sqlite'}", auto_create_schema=True,
    )
    app = create_app(settings)

    product = {
        "product_id": "P1001",
        "seller_id": "seller_001",
        "sku": "ELEC-ANC-001",
        "category": "Electronics",
        "title": "蓝牙降噪耳机 / Bluetooth ANC Earbuds",
        "description": "主动降噪、低延迟游戏模式，适合通勤和直播。",
        "price": 49.99,
        "currency": "USD",
        "stock": 128,
        "status": "LIVE",
    }

    async def fake_get_product_detail(product_id: str, access_token: str | None = None) -> dict[str, Any]:
        assert access_token == "fake-access-token"
        assert product_id == "P1001"
        return {"product": product}

    async def fake_update_product(**kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "product": {"product_id": kwargs["product_id"], **kwargs}}

    def fake_translate_text(text: str, target_language: str, source_language: str | None = None) -> str:
        if target_language == "en":
            return f"EN: {text}"
        if target_language == "th":
            return f"TH: {text}"
        return text

    def fake_answer_customer_question(
        question: str,
        target_language: str | None = None,
        category_filter: str | None = None,
        top_k: int = 3,
    ) -> dict[str, Any]:
        if target_language == "th" or "ค่าจัดส่ง" in question:
            return {
                "question": question,
                "detected_language": "th",
                "target_language": "th",
                "normalized_query": "运费如何计算",
                "answer": "ค่าจัดส่งจะคำนวณตามพื้นที่ น้ำหนัก และโปรโมชั่น",
                "context_used": [{"text": "运费会根据地区、重量和促销计算", "metadata": {"category": "物流"}, "score": 0.9}],
                "grounded": True,
            }
        return {
            "question": question,
            "detected_language": "zh",
            "target_language": "zh",
            "normalized_query": question,
            "answer": "进入订单详情页选择申请售后，填写原因、上传凭证并提交即可。具体以人工审核为准。",
            "context_used": [{"text": "退货 FAQ", "metadata": {"category": "退换货"}, "score": 0.92}],
            "grounded": True,
        }

    def fake_fetch_competitor_prices(
        product_id: str,
        current_price: float,
        currency: str = "USD",
        market: str = "SG",
        competitors: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {"product_id": product_id, "competitor": "A", "market": market, "price": 70.0, "currency": currency, "url": "https://example.com/a"},
            {"product_id": product_id, "competitor": "B", "market": market, "price": 72.0, "currency": currency, "url": "https://example.com/b"},
        ]

    monkeypatch.setattr(product_routes.platform_api, "get_product_detail", fake_get_product_detail)
    monkeypatch.setattr(product_routes.platform_api, "update_product", fake_update_product)
    monkeypatch.setattr(product_routes, "translate_text", fake_translate_text)
    monkeypatch.setattr(customer_routes, "answer_customer_question", fake_answer_customer_question)
    monkeypatch.setattr(price_routes, "fetch_competitor_prices", fake_fetch_competitor_prices)
    monkeypatch.setattr(price_routes.platform_api, "update_product", fake_update_product)

    for handler in app.router.on_startup:
        result = handler()
        if inspect.isawaitable(result):
            await result
    app.state.oauth_client = FakeOAuthClient()

    headers = {"X-API-Key": "test-key"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/api/admin/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        sync_prepare = await client.post(
            "/api/sync-product",
            headers=headers,
            json={"product_id": "P1001", "target_languages": ["en"], "dry_run": True},
        )
        assert sync_prepare.status_code == 200
        assert sync_prepare.json()["status"] == "prepared"
        assert sync_prepare.json()["original"]["product_id"] == "P1001"
        assert "fast charging" in sync_prepare.json()["translations"]["en"]["description"]

        sync_apply = await client.post(
            "/api/sync-product",
            headers=headers,
            json={"product_id": "P1001", "target_languages": ["en"], "dry_run": False, "confirm": True},
        )
        assert sync_apply.status_code == 200
        assert sync_apply.json()["status"] == "synced"

        cn_answer = await client.post(
            "/api/customer/ask",
            headers=headers,
            json={"question": "如何申请退货？", "target_language": "zh", "category_filter": "退换货"},
        )
        assert cn_answer.status_code == 200
        assert "订单详情页" in cn_answer.json()["answer"]

        th_answer = await client.post(
            "/api/customer/ask",
            headers=headers,
            json={"question": "ค่าจัดส่งคำนวณอย่างไร?", "target_language": "th", "category_filter": "物流"},
        )
        assert th_answer.status_code == 200
        assert th_answer.json()["detected_language"] == "th"

        price_monitor = await client.post(
            "/api/price/monitor",
            headers=headers,
            json={"product_id": "P1001", "current_price": 100.0, "currency": "USD", "market": "SG"},
        )
        assert price_monitor.status_code == 200
        price_payload = price_monitor.json()
        assert price_payload["recommendation"]["approval_required"] is True
        assert price_payload["approval_request"]["status"] == "pending"
        assert price_payload["approval_request"]["risk_level"] == "critical"

        recommendations = await client.get("/api/price/recommendations", headers=headers)
        assert recommendations.status_code == 200
        assert recommendations.json()["total"] == 1

        approvals = await client.get("/api/admin/approvals", headers=headers)
        assert approvals.status_code == 200
        assert approvals.json()["total"] == 1

        audit = await client.get("/api/admin/audit", headers=headers, params={"action": "price_monitor"})
        assert audit.status_code == 200
        assert audit.json()["total"] >= 1

    for handler in app.router.on_shutdown:
        result = handler()
        if inspect.isawaitable(result):
            await result
