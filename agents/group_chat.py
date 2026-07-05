from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .base import create_group_chat, create_group_chat_manager, create_llm_config, create_user_proxy_agent
    from .cs_agent import create_cs_agent
    from .price_agent import create_price_agent
    from .sync_agent import create_sync_agent
except ImportError:
    from base import create_group_chat, create_group_chat_manager, create_llm_config, create_user_proxy_agent  # type: ignore[no-redef]
    from cs_agent import create_cs_agent  # type: ignore[no-redef]
    from price_agent import create_price_agent  # type: ignore[no-redef]
    from sync_agent import create_sync_agent  # type: ignore[no-redef]


try:
    from .base import get_audit_logger
except Exception:  # pragma: no cover - logger optional in tests
    get_audit_logger = None  # type: ignore[assignment]

try:
    from safety.domain_guardrail import DomainGuardrail, DomainBlockedError, GuardrailStrategy
except Exception:  # pragma: no cover - degrade gracefully if unavailable
    DomainGuardrail = None  # type: ignore[assignment]
    DomainBlockedError = RuntimeError  # type: ignore[assignment,misc]
    GuardrailStrategy = None  # type: ignore[assignment]


@dataclass
class OmniSellTeam:
    """Container for the OmniSell AutoGen team."""

    user_proxy: Any
    sync_agent: Any
    cs_agent: Any
    price_agent: Any
    group_chat: Any
    manager: Any


def create_omnisell_team(llm_config: dict[str, Any] | None = None, max_round: int = 12) -> OmniSellTeam:
    """Create the OmniSell multi-agent team with three AssistantAgents and one UserProxyAgent."""
    config = llm_config if llm_config is not None else create_llm_config()
    user_proxy = create_user_proxy_agent(name="OmniSellUserProxy", human_input_mode="TERMINATE")
    sync_agent = create_sync_agent(llm_config=config, executor=user_proxy)
    cs_agent = create_cs_agent(llm_config=config, executor=user_proxy)
    price_agent = create_price_agent(llm_config=config, executor=user_proxy)

    group_chat = create_group_chat(
        agents=[user_proxy, sync_agent, cs_agent, price_agent],
        max_round=max_round,
    )
    manager = create_group_chat_manager(group_chat, llm_config=config)

    return OmniSellTeam(
        user_proxy=user_proxy,
        sync_agent=sync_agent,
        cs_agent=cs_agent,
        price_agent=price_agent,
        group_chat=group_chat,
        manager=manager,
    )


def start_chat(task_description: str, max_round: int = 12) -> Any:
    """Start a GroupChat workflow for a given OmniSell task description.

    The request first passes through the domain guardrail so that prompts
    outside the e-commerce operation scope (jailbreak attempts, ad injection,
    off-topic chitchat) are blocked before reaching the multi-agent runtime.
    Any block is recorded in the audit log as a ``domain_blocked`` event.
    """
    guardrail = _build_guardrail()
    if guardrail is not None:
        guardrail.check_or_raise(task_description)
    team = create_omnisell_team(max_round=max_round)
    return team.user_proxy.initiate_chat(team.manager, message=task_description)


def _build_guardrail() -> Any:
    """Construct a DomainGuardrail wired to the shared audit logger.

    Returns None if the guardrail module is unavailable so the chat entry
    point degrades gracefully instead of hard-failing.
    """
    if DomainGuardrail is None:
        return None
    audit_logger = None
    if get_audit_logger is not None:
        try:
            audit_logger = get_audit_logger()
        except Exception:  # pragma: no cover - logger optional
            audit_logger = None
    strategy = os.getenv("OMNISELL_GUARDRAIL_STRATEGY", GuardrailStrategy.MODERATE.value)
    return DomainGuardrail(strategy=strategy, audit_logger=audit_logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the OmniSell AutoGen multi-agent team.")
    parser.add_argument("task_description", nargs="?", default="请分析今日商品同步、客服和价格风控任务。")
    parser.add_argument("--max-round", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    result = start_chat(args.task_description, max_round=args.max_round)
    if result is not None:
        print(result)


if __name__ == "__main__":
    main()
