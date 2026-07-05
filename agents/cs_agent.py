from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .base import create_assistant_agent, create_llm_config, register_tools
    from .tools.rag_tool import answer_customer_question, search_knowledge_base
    from .tools.translator import detect_and_translate, translate_text
    from .tools.vision_tool import analyze_customer_image, answer_visual_customer_question
except ImportError:
    from base import create_assistant_agent, create_llm_config, register_tools  # type: ignore[no-redef]
    from tools.rag_tool import answer_customer_question, search_knowledge_base  # type: ignore[no-redef]
    from tools.translator import detect_and_translate, translate_text  # type: ignore[no-redef]
    from tools.vision_tool import analyze_customer_image, answer_visual_customer_question  # type: ignore[no-redef]

try:
    from safety.refund_constraint import AGENT_REFUND_CONSTRAINT_PROMPT
except ImportError:
    AGENT_REFUND_CONSTRAINT_PROMPT = ""

CS_AGENT_SYSTEM_PROMPT = """你是 OmniSell 的 24 小时智能客服 Agent，负责多语言售前售后问答。
强制规则：
1. 必须优先调用 RAG 知识库工具回答客户问题，不允许凭空编造政策、时效、补偿或承诺。
2. 如果知识库没有足够依据，必须说明"当前知识库信息不足"，并建议提供订单号、商品链接或转人工。
3. 多语言用户需要自动识别语言；内部检索统一转中文，回答再翻译回用户目标语言。
4. 对退款、退货、物流异常、支付异常等敏感问题，要提醒以订单详情、平台审核和人工复核为准。
5. 禁止承诺必定退款、必定赔付、必定发货或绕过平台规则。

回答风格：
- 简洁、准确、温和。
- 先直接回答，再给可执行步骤。
- 必要时列出需要用户补充的信息。
""" + "\n\n" + AGENT_REFUND_CONSTRAINT_PROMPT


def create_cs_agent(llm_config: dict[str, Any] | None = None, executor: Any | None = None) -> Any:
    """Create the OmniSell customer service agent and register RAG/translation tools."""
    agent = create_assistant_agent(
        name="CustomerServiceAgent",
        system_message=CS_AGENT_SYSTEM_PROMPT,
        llm_config=llm_config or create_llm_config(),
        description="Customer service agent that answers multilingual questions using RAG grounding.",
    )

    if executor is not None:
        register_tools(
            agent,
            executor,
            [
                search_knowledge_base,
                answer_customer_question,
                translate_text,
                detect_and_translate,
                analyze_customer_image,
                answer_visual_customer_question,
            ],
        )

    return agent


if __name__ == "__main__":
    agent = create_cs_agent(llm_config=False)
    print(agent.name)
    print(CS_AGENT_SYSTEM_PROMPT[:160])
