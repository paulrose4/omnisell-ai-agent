from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.tools.rag_tool import answer_customer_question
from agents.tools.vision_tool import answer_visual_customer_question
from application.agent_worker import execute_agent_run_action


ActionHandler = Callable[..., dict[str, Any]]


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "customer.ask": answer_customer_question,
    "customer.ask_image": answer_visual_customer_question,
    "customer.ask_image_upload": answer_visual_customer_question,
    "agent.run": execute_agent_run_action,
}


def execute_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise ValueError(f"Unsupported durable action: {action}")
    result = handler(**payload)
    if not isinstance(result, dict):
        return {"value": result}
    return result
