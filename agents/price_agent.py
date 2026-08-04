from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .base import create_assistant_agent, create_llm_config, register_tools
    from .tools.platform_api import get_product_detail, get_product_price
    from .tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
except ImportError:
    from base import create_assistant_agent, create_llm_config, register_tools  # type: ignore[no-redef]
    from tools.platform_api import get_product_detail, get_product_price  # type: ignore[no-redef]
    from tools.price_scraper import fetch_competitor_prices, generate_price_recommendation  # type: ignore[no-redef]


PRICE_AGENT_SYSTEM_PROMPT = """⚠️ CRITICAL RULES - VIOLATION IS NOT ALLOWED:
You are NEVER allowed to execute, initiate, or promise a refund.
If a user requests a refund, you MUST reply: "退款请求已记录，将转交人工客服处理。Refund request logged and will be forwarded to human support."
You must NEVER instruct any tool to process a payment reversal or refund.
If you are unsure whether an action involves a refund, treat it as a refund and block it.

⚠️ 关键规则 - 严禁违反：
你绝不允许执行、发起或承诺退款。
如果用户请求退款，你必须回复："退款请求已记录，将转交人工客服处理。Refund request logged and will be forwarded to human support."
你绝不能指示任何工具处理支付撤销或退款。
如果你不确定某个操作是否涉及退款，必须按退款处理并阻断。

你是 OmniSell 的内部只读价格解释 Demo，只能解释确定性价格证据和建议，不负责计算、审批或执行。

强制边界：
1. 不得计算价格、利润、费用、变化比例或业务优先级。
2. 不得更新商品价格或执行任何 Amazon 写入。
3. 不得决定策略是否允许执行，也不得审批自己的建议。
4. 输出属于 unverified internal analysis，生产决策由确定性服务、Policy 和 HITL 负责。

分析要求：
- 总结最低竞品价、平均竞品价、当前价、建议价和变化比例。
- 给出 action：auto_update、notify_operator 或 require_human_approval。
- 只能引用工具返回的确定性结果并说明证据缺口，不得调用写入工具。
"""


def create_price_agent(llm_config: dict[str, Any] | None = None, executor: Any | None = None) -> Any:
    """Create the explicitly optional read-only legacy price analysis demo."""
    agent = create_assistant_agent(
        name="PriceRiskAgent",
        system_message=PRICE_AGENT_SYSTEM_PROMPT,
        llm_config=llm_config or create_llm_config(),
        description="Internal read-only legacy demo for explaining deterministic pricing evidence.",
    )

    if executor is not None:
        register_tools(
            agent,
            executor,
            [
                fetch_competitor_prices,
                generate_price_recommendation,
                get_product_detail,
                get_product_price,
            ],
        )

    return agent


if __name__ == "__main__":
    agent = create_price_agent(llm_config=False)
    print(agent.name)
    print(PRICE_AGENT_SYSTEM_PROMPT[:160])
