from __future__ import annotations

from typing import Any

from agents.group_chat import create_omnisell_team
from agents.tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
from agents.tools import rag_tool


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


def test_product_translation_sync_agent_registers_tools_and_seo_rules() -> None:
    """商品翻译同步场景：运营 Agent 必须注册翻译和平台写入工具，并带 SEO 确认规则。"""
    team = create_omnisell_team(llm_config=False)
    sync_agent = team.sync_agent

    assert "快充" in sync_agent.system_message
    assert "百搭" in sync_agent.system_message
    assert "必须先总结拟执行操作" in sync_agent.system_message
    assert "translate_text" in sync_agent.registered_tools
    assert "create_product" in sync_agent.registered_tools
    assert "update_product" in sync_agent.registered_tools
    assert "get_order_list" in sync_agent.registered_tools
    assert "approve_return" in sync_agent.registered_tools
    assert team.user_proxy.human_input_mode == "TERMINATE"


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
