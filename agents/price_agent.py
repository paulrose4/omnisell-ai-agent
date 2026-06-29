from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .base import create_assistant_agent, create_llm_config, register_tools
    from .tools.platform_api import get_product_detail, get_product_price, update_product
    from .tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
except ImportError:
    from base import create_assistant_agent, create_llm_config, register_tools  # type: ignore[no-redef]
    from tools.platform_api import get_product_detail, get_product_price, update_product  # type: ignore[no-redef]
    from tools.price_scraper import fetch_competitor_prices, generate_price_recommendation  # type: ignore[no-redef]


PRICE_AGENT_SYSTEM_PROMPT = """你是 OmniSell 的风控 Agent，负责竞品价格监控、价格建议和操作审批。

HITL 人工介入规则：
1. 建议价格变动幅度 < 5%：可自动执行，但仍需记录原因、竞品价格和计算依据。
2. 建议价格变动幅度在 5% 到 10%：必须通知运营人员，说明影响范围后再执行。
3. 建议价格变动幅度 > 10%：必须阻断自动更新，等待人工明确审批。
4. 任何下架、删除、库存清零、疑似亏本或政策风险操作：必须人工审批。
5. Mock 平台返回 pending_approval 时，说明平台已进入审核队列，不得继续重复提交。

分析要求：
- 总结最低竞品价、平均竞品价、当前价、建议价和变化比例。
- 给出 action：auto_update、notify_operator 或 require_human_approval。
- 只有在规则允许且用户确认时，才调用平台更新工具。
"""


def create_price_agent(llm_config: dict[str, Any] | None = None, executor: Any | None = None) -> Any:
    """Create the OmniSell price risk agent and register pricing/platform tools."""
    agent = create_assistant_agent(
        name="PriceRiskAgent",
        system_message=PRICE_AGENT_SYSTEM_PROMPT,
        llm_config=llm_config or create_llm_config(),
        description="Risk-control agent for competitor monitoring and human-in-the-loop price approvals.",
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
                update_product,
            ],
        )

    return agent


if __name__ == "__main__":
    agent = create_price_agent(llm_config=False)
    print(agent.name)
    print(PRICE_AGENT_SYSTEM_PROMPT[:160])
