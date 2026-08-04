from __future__ import annotations

from typing import Any

import pytest

from agents.tools import rag_tool
from agents.tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
from legacy.autogen_demo.base import LegacyAgentRuntimeUnavailable
from legacy.autogen_demo.group_chat import create_omnisell_team
from legacy.autogen_demo.sync_agent import SYNC_AGENT_SYSTEM_PROMPT


class FakeTranslator:
    def __init__(self, detected_language: str = "zh") -> None:
        self.detected_language = detected_language

    def detect_language(self, text: str) -> str:
        if "ค่าจัดส่ง" in text or "อย่างไร" in text:
            return "th"
        return self.detected_language

    def translate_query(self, question: str) -> str:
        if self.detect_language(question) == "th":
            return "运费如何计算"
        return question

    def translate_answer(self, answer: str, target_language: str, source_language: str = "zh") -> str:
        if target_language == "th":
            return "ค่าจัดส่งจะคำนวณตามพื้นที่ น้ำหนัก และโปรโมชั่น โดยดูได้ก่อนชำระเงิน"
        return answer


def test_legacy_sync_group_chat_is_not_a_production_runtime() -> None:
    """Product sync is deterministic; the optional legacy GroupChat cannot silently simulate production."""
    with pytest.raises(LegacyAgentRuntimeUnavailable):
        create_omnisell_team(llm_config=False)

    assert "生产同步由确定性 AmazonSyncService 执行" in SYNC_AGENT_SYSTEM_PROMPT
    assert "不得创建、删除、更新商品或订单状态" in SYNC_AGENT_SYSTEM_PROMPT


def test_chinese_customer_return_question_uses_rag(monkeypatch: Any) -> None:
    """中文客服退换货问答场景：必须基于 RAG 内容回答并保留审核边界。"""
    monkeypatch.setattr(rag_tool, "get_translator", lambda: FakeTranslator("zh"))
    monkeypatch.setattr(
        rag_tool,
        "search_knowledge_base",
        lambda **kwargs: [
            {
                "text": "Question: 如何申请退货？\nAnswer: 进入订单详情页选择申请售后，填写原因、上传凭证并提交即可。",
                "metadata": {"category": "退换货", "source_type": "faq"},
                "score": 0.91,
                "distance": 0.09,
            }
        ],
    )

    result = rag_tool.answer_customer_question("如何申请退货？", target_language="zh", category_filter="退换货")

    assert result["grounded"] is True
    assert "订单详情页" in result["answer"]
    assert "人工审核" in result["answer"]


def test_thai_shipping_fee_question_translates_back(monkeypatch: Any) -> None:
    """泰语运费问答场景：问题转中文检索，答案翻译回泰语。"""
    monkeypatch.setattr(rag_tool, "get_translator", lambda: FakeTranslator("th"))

    def fake_search(**kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["query"] == "运费如何计算"
        assert kwargs["language"] == "zh"
        return [
            {
                "text": "Question: 运费如何计算？\nAnswer: 运费会根据收货地区、商品重量、体积和促销活动自动计算，并在付款前展示。",
                "metadata": {"category": "物流", "source_type": "faq"},
                "score": 0.88,
                "distance": 0.12,
            }
        ]

    monkeypatch.setattr(rag_tool, "search_knowledge_base", fake_search)

    result = rag_tool.answer_customer_question("ค่าจัดส่งคำนวณอย่างไร?", target_language="th", category_filter="物流")

    assert result["detected_language"] == "th"
    assert result["normalized_query"] == "运费如何计算"
    assert "ค่าจัดส่ง" in result["answer"]


def test_price_monitoring_is_deterministic_and_actionable() -> None:
    """价格监控与分析场景：Mock 竞品价格稳定，建议包含行动和审批字段。"""
    first = fetch_competitor_prices("P1001", current_price=49.99, market="SG")
    second = fetch_competitor_prices("P1001", current_price=49.99, market="SG")
    first_without_time = [{key: value for key, value in row.items() if key != "fetched_at"} for row in first]
    second_without_time = [{key: value for key, value in row.items() if key != "fetched_at"} for row in second]

    recommendation = generate_price_recommendation("P1001", current_price=49.99, competitor_prices=first)

    assert first_without_time == second_without_time
    assert recommendation["action"] in {"auto_update", "notify_operator", "require_human_approval"}
    assert "approval_required" in recommendation
    assert "recommended_price" in recommendation


def test_price_drop_above_10_percent_blocks_without_hitl() -> None:
    """>10% 降价触发 HITL 审批阻断测试。"""
    competitor_prices = [
        {"competitor": "A", "price": 70.0, "currency": "USD", "market": "SG"},
        {"competitor": "B", "price": 72.0, "currency": "USD", "market": "SG"},
    ]

    recommendation = generate_price_recommendation(
        product_id="P-HITL",
        current_price=100.0,
        competitor_prices=competitor_prices,
        currency="USD",
        market="SG",
    )

    assert recommendation["change_percent"] < -10
    assert recommendation["approval_required"] is True
    assert recommendation["action"] == "require_human_approval"
    assert "HITL" in recommendation["approval_reason"]


def test_rag_backend_failure_emits_structured_warning_before_fallback(monkeypatch: Any, caplog: Any) -> None:
    class BrokenRetriever:
        def retrieve_with_score(self, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("vector backend unavailable")

    monkeypatch.setattr(rag_tool, "get_retriever", lambda: BrokenRetriever())
    caplog.set_level("WARNING", logger="omnisell.observability")

    results = rag_tool.search_knowledge_base(query="如何申请退货", language="zh", source_type="faq")

    assert results
    assert any('"event": "rag_retrieval_failed"' in record.message for record in caplog.records)
    assert any('"error_type": "RuntimeError"' in record.message for record in caplog.records)
    assert all("如何申请退货" not in record.message for record in caplog.records)
