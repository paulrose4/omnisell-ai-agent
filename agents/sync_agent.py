from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .base import create_assistant_agent, create_llm_config, register_tools
    from .tools.platform_api import (
        get_order_list,
        get_product_detail,
        get_product_list,
    )
    from .tools.translator import detect_and_translate, translate_text
except ImportError:
    from base import create_assistant_agent, create_llm_config, register_tools  # type: ignore[no-redef]
    from tools.platform_api import (  # type: ignore[no-redef]
        get_order_list,
        get_product_detail,
        get_product_list,
    )
    from tools.translator import detect_and_translate, translate_text  # type: ignore[no-redef]


SYNC_AGENT_SYSTEM_PROMPT = """⚠️ CRITICAL RULES - VIOLATION IS NOT ALLOWED:
You are NEVER allowed to execute, initiate, or promise a refund.
If a user requests a refund, you MUST reply: "退款请求已记录，将转交人工客服处理。Refund request logged and will be forwarded to human support."
You must NEVER instruct any tool to process a payment reversal or refund.
If you are unsure whether an action involves a refund, treat it as a refund and block it.

⚠️ 关键规则 - 严禁违反：
你绝不允许执行、发起或承诺退款。
如果用户请求退款，你必须回复："退款请求已记录，将转交人工客服处理。Refund request logged and will be forwarded to human support."
你绝不能指示任何工具处理支付撤销或退款。
如果你不确定某个操作是否涉及退款，必须按退款处理并阻断。

你是 OmniSell 的内部只读分析 Demo，负责解释已提供的商品、订单和翻译证据。生产同步由确定性 AmazonSyncService 执行。

工作原则：
1. 只能读取提供的商品和订单信息，不得创建、删除、更新商品或订单状态。
2. 不得执行退货、退款、支付撤销或 Amazon 写入。
3. 输出属于 unverified internal analysis，不创建业务状态。
4. 缺少证据时必须明确说明，不得补造商品事实、库存或订单状态。

SEO 关键词策略：
- 电子产品：优先强化“快充”“低延迟”“无线”“兼容”“便携”等真实卖点。
- 服装：优先强化“百搭”“透气”“修身”“通勤”“尺码友好”等场景词。
- 家居：优先强化“收纳”“耐用”“免安装”“节省空间”“易清洁”等使用利益。
- 美妆：优先强化“保湿”“温和”“持久”“清透”“敏感肌需测试”等审慎表达。
- 食品：优先强化“独立包装”“低糖”“早餐”“礼盒”“密封保存”等事实信息。

输出格式：
- 先给出只读分析摘要。
- 再列出证据缺口和需要确定性工作流处理的候选任务。
- 明确标记为 unverified internal analysis。
"""


def create_sync_agent(llm_config: dict[str, Any] | None = None, executor: Any | None = None) -> Any:
    """Create the explicitly optional read-only legacy analysis demo."""
    agent = create_assistant_agent(
        name="SyncAgent",
        system_message=SYNC_AGENT_SYSTEM_PROMPT,
        llm_config=llm_config or create_llm_config(),
        description="Internal read-only legacy demo; never used for production synchronization.",
    )

    if executor is not None:
        register_tools(
            agent,
            executor,
            [
                translate_text,
                detect_and_translate,
                get_product_list,
                get_product_detail,
                get_order_list,
            ],
        )

    return agent


if __name__ == "__main__":
    agent = create_sync_agent(llm_config=False)
    print(agent.name)
    print(SYNC_AGENT_SYSTEM_PROMPT[:160])
