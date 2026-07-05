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
        approve_return,
        create_product,
        delete_product,
        get_order_list,
        get_product_detail,
        get_product_list,
        update_order_status,
        update_product,
    )
    from .tools.translator import detect_and_translate, translate_text
except ImportError:
    from base import create_assistant_agent, create_llm_config, register_tools  # type: ignore[no-redef]
    from tools.platform_api import (  # type: ignore[no-redef]
        approve_return,
        create_product,
        delete_product,
        get_order_list,
        get_product_detail,
        get_product_list,
        update_order_status,
        update_product,
    )
    from tools.translator import detect_and_translate, translate_text  # type: ignore[no-redef]

try:
    from safety.refund_constraint import AGENT_REFUND_CONSTRAINT_PROMPT
except ImportError:
    AGENT_REFUND_CONSTRAINT_PROMPT = ""

SYNC_AGENT_SYSTEM_PROMPT = """你是 OmniSell 的运营 Agent，负责跨平台商品同步、多语言内容生成和 SEO 优化。
工作原则：
1. 在创建、删除或更新任何平台商品之前，必须先总结拟执行操作，并明确询问用户确认。
2. 你可以读取商品列表和详情，但写操作必须等待明确授权，除非用户已经在同一任务中给出清晰批准。
3. 商品标题和描述需要遵守多语言 SEO 策略，避免堆砌关键词、夸大承诺和虚假库存。
4. 同步商品时必须保留核心事实：价格、库存、材质、尺寸、保养说明、兼容性和售后限制。

SEO 关键词策略：
- 电子产品：优先强化"快充""低延迟""无线""兼容""便携"等真实卖点。
- 服装：优先强化"百搭""透气""修身""通勤""尺码友好"等场景词。
- 家居：优先强化"收纳""耐用""免安装""节省空间""易清洁"等使用利益。
- 美妆：优先强化"保湿""温和""持久""清透""敏感肌需测试"等审慎表达。
- 食品：优先强化"独立包装""低糖""早餐""礼盒""密封保存"等事实信息。

输出格式：
- 先给出操作摘要。
- 再列出需要用户确认的写操作。
- 获得确认后再调用平台写入工具。
""" + "\n\n" + AGENT_REFUND_CONSTRAINT_PROMPT


def create_sync_agent(llm_config: dict[str, Any] | None = None, executor: Any | None = None) -> Any:
    """Create the OmniSell operations agent and register translation/platform tools."""
    agent = create_assistant_agent(
        name="SyncAgent",
        system_message=SYNC_AGENT_SYSTEM_PROMPT,
        llm_config=llm_config or create_llm_config(),
        description="Operations agent for product sync and multilingual SEO optimization.",
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
                update_product,
                create_product,
                delete_product,
                get_order_list,
                update_order_status,
                approve_return,
            ],
        )

    return agent


if __name__ == "__main__":
    agent = create_sync_agent(llm_config=False)
    print(agent.name)
    print(SYNC_AGENT_SYSTEM_PROMPT[:160])
