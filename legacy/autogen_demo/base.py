from __future__ import annotations

import inspect
import os
import time
from functools import wraps
from dataclasses import dataclass, field
from typing import Any, Callable

from observability.telemetry import instrument_openai_calls

try:
    from autogen import AssistantAgent, GroupChat, GroupChatManager, UserProxyAgent, register_function
except Exception:
    AssistantAgent = None  # type: ignore[assignment]
    UserProxyAgent = None  # type: ignore[assignment]
    GroupChat = None  # type: ignore[assignment]
    GroupChatManager = None  # type: ignore[assignment]
    register_function = None  # type: ignore[assignment]


DEFAULT_MODEL_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL_NAME = "qwen2.5:3b"


class LegacyAgentRuntimeUnavailable(RuntimeError):
    """Raised when the explicitly optional Legacy AutoGen demo runtime is unavailable."""


def _require_legacy_runtime(component: str, runtime: Any) -> Any:
    if runtime is None:
        raise LegacyAgentRuntimeUnavailable(
            f"Legacy AutoGen runtime is unavailable for {component}. "
            "Production workflows use PersistentAgentWorkflow and bounded advisors; "
            "install a compatible optional legacy runtime only for the internal demo."
        )
    return runtime


try:
    from safety.audit import AuditLogger
    from safety.circuit_breaker import CircuitBreaker, get_circuit_breaker as get_registered_circuit_breaker
    from safety.hitl import ApprovalRequiredError, ApprovalStatus, HITLManager, RiskLevel
    from safety.policy_engine import PolicyDecision, PolicyEngine
    from safety.refund_gate import RefundGate
    from safety.tool_auth import ToolPermissionValidator
except Exception:
    AuditLogger = None  # type: ignore[assignment]
    CircuitBreaker = None  # type: ignore[assignment]
    get_registered_circuit_breaker = None  # type: ignore[assignment]
    HITLManager = None  # type: ignore[assignment]
    ApprovalRequiredError = RuntimeError  # type: ignore[assignment]
    ApprovalStatus = None  # type: ignore[assignment]
    RiskLevel = None  # type: ignore[assignment]
    PolicyDecision = None  # type: ignore[assignment]
    PolicyEngine = None  # type: ignore[assignment]
    RefundGate = None  # type: ignore[assignment]
    ToolPermissionValidator = None  # type: ignore[assignment]


_AUDIT_LOGGER: Any | None = None
_HITL_MANAGER: Any | None = None


def get_audit_logger() -> Any:
    """Return the process-wide audit logger."""
    global _AUDIT_LOGGER
    if _AUDIT_LOGGER is None:
        if AuditLogger is None:
            raise RuntimeError("safety.audit.AuditLogger is unavailable.")
        _AUDIT_LOGGER = AuditLogger()
    return _AUDIT_LOGGER


def get_hitl_manager() -> Any:
    """Return the process-wide HITL manager."""
    global _HITL_MANAGER
    if _HITL_MANAGER is None:
        if HITLManager is None:
            raise RuntimeError("safety.hitl.HITLManager is unavailable.")
        _HITL_MANAGER = HITLManager(audit_logger=get_audit_logger())
    return _HITL_MANAGER


def get_circuit_breaker(name: str) -> Any:
    """Return a per-tool circuit breaker."""
    if get_registered_circuit_breaker is None:
        raise RuntimeError("safety.circuit_breaker.get_circuit_breaker is unavailable.")
    return get_registered_circuit_breaker(name)


def create_llm_config(
    base_url: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.2,
    timeout: int = 120,
) -> dict[str, Any]:
    """Create a demo-only AutoGen config for an OpenAI-compatible endpoint."""
    instrument_openai_calls()
    resolved_base_url = base_url or os.getenv("MODEL_BASE_URL") or os.getenv("VLLM_BASE_URL", DEFAULT_MODEL_BASE_URL)
    resolved_model_name = model_name or os.getenv("MODEL_NAME") or os.getenv("VLLM_MODEL_NAME", DEFAULT_MODEL_NAME)
    resolved_api_key = api_key or os.getenv("MODEL_API_KEY") or os.getenv("VLLM_API_KEY", "not-needed")

    return {
        "config_list": [
            {
                "model": resolved_model_name,
                "base_url": resolved_base_url,
                "api_key": resolved_api_key,
            }
        ],
        "temperature": temperature,
        "timeout": timeout,
    }


def _build_tool_payload(func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(func)
    try:
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except TypeError:
        return {"args": args, "kwargs": kwargs}


def _wait_for_approval(manager: Any, request_id: str, poll_interval: float, timeout_seconds: float) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        request = manager.get_request(request_id)
        if request is None:
            raise RuntimeError(f"Approval request not found: {request_id}")
        if request.status == ApprovalStatus.APPROVED:
            return request
        if request.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}:
            raise RuntimeError(f"Approval request {request_id} ended with status {request.status.value}.")
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for approval request {request_id}.")


def safe_tool_execution(
    func: Callable[..., Any] | None = None,
    *,
    hitl_manager: Any | None = None,
    audit_logger: Any | None = None,
    circuit_breaker: Any | None = None,
    permission_validator: Any | None = None,
    refund_gate: Any | None = None,
    wait_for_approval: bool = False,
    approval_poll_interval: float = 1.0,
    approval_wait_timeout: float = 30.0,
) -> Callable[..., Any]:
    """Wrap a tool with auth checks, HITL, circuit breaker protection, and audit logging."""

    def decorator(tool: Callable[..., Any]) -> Callable[..., Any]:
        manager = hitl_manager or get_hitl_manager()
        logger = audit_logger or get_audit_logger()
        breaker = circuit_breaker or get_circuit_breaker(tool.__name__)
        gate = refund_gate
        if gate is None:
            if RefundGate is None:
                raise RuntimeError("safety.refund_gate.RefundGate is unavailable.")
            gate = RefundGate(audit_logger=logger)
        validator = permission_validator
        if validator is None:
            if ToolPermissionValidator is None:
                raise RuntimeError("safety.tool_auth.ToolPermissionValidator is unavailable.")
            validator = ToolPermissionValidator(audit_logger=logger)
        if hasattr(validator, "register_tool"):
            validator.register_tool(tool)
        signature = inspect.signature(tool)

        async def _prepare_or_block(payload: dict[str, Any]) -> Any:
            if PolicyEngine is None:
                raise RuntimeError("safety.policy_engine.PolicyEngine is unavailable.")
            policy = PolicyEngine().evaluate(action=tool.__name__, arguments=payload)
            request = manager.submit_request(
                action=tool.__name__, payload=payload, requester="agent_tool",
                risk_level=policy.risk_level, reason=policy.reason, policy_decision=policy.decision,
                policy_version=policy.policy_version,
            )
            if request.status == ApprovalStatus.PENDING:
                logger.log_operation(
                    action=tool.__name__,
                    status="blocked_pending_approval",
                    details={"approval_request": request.to_dict()},
                    actor="agent_tool",
                    risk_level=request.risk_level.value,
                )
                if not wait_for_approval:
                    raise ApprovalRequiredError(request)
                _wait_for_approval(manager, request.request_id, approval_poll_interval, approval_wait_timeout)
            return request

        def _prepare_or_block_sync(payload: dict[str, Any]) -> Any:
            if PolicyEngine is None:
                raise RuntimeError("safety.policy_engine.PolicyEngine is unavailable.")
            policy = PolicyEngine().evaluate(action=tool.__name__, arguments=payload)
            request = manager.submit_request(
                action=tool.__name__, payload=payload, requester="agent_tool",
                risk_level=policy.risk_level, reason=policy.reason, policy_decision=policy.decision,
                policy_version=policy.policy_version,
            )
            if request.status == ApprovalStatus.PENDING:
                logger.log_operation(
                    action=tool.__name__,
                    status="blocked_pending_approval",
                    details={"approval_request": request.to_dict()},
                    actor="agent_tool",
                    risk_level=request.risk_level.value,
                )
                if not wait_for_approval:
                    raise ApprovalRequiredError(request)
                _wait_for_approval(manager, request.request_id, approval_poll_interval, approval_wait_timeout)
            return request

        if inspect.iscoroutinefunction(tool):

            @wraps(tool)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                payload = _build_tool_payload(tool, args, kwargs)
                validator.validate(tool.__name__, payload)
                gate.enforce(tool.__name__, payload)
                request = await _prepare_or_block(payload)
                try:
                    result = await breaker.acall(tool, *args, **kwargs)
                    logger.log_operation(
                        action=tool.__name__,
                        status="success",
                        details={"approval_request": request.to_dict(), "result": result},
                        actor="agent_tool",
                        risk_level=request.risk_level.value,
                    )
                    return result
                except Exception as exc:
                    logger.log_operation(
                        action=tool.__name__,
                        status="failed",
                        details={"approval_request": request.to_dict(), "error": str(exc)},
                        actor="agent_tool",
                        risk_level=request.risk_level.value,
                    )
                    raise

            async_wrapper.__signature__ = signature  # type: ignore[attr-defined]
            return async_wrapper

        @wraps(tool)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            payload = _build_tool_payload(tool, args, kwargs)
            validator.validate(tool.__name__, payload)
            gate.enforce(tool.__name__, payload)
            request = _prepare_or_block_sync(payload)
            try:
                result = breaker.call(tool, *args, **kwargs)
                logger.log_operation(
                    action=tool.__name__,
                    status="success",
                    details={"approval_request": request.to_dict(), "result": result},
                    actor="agent_tool",
                    risk_level=request.risk_level.value,
                )
                return result
            except Exception as exc:
                logger.log_operation(
                    action=tool.__name__,
                    status="failed",
                    details={"approval_request": request.to_dict(), "error": str(exc)},
                    actor="agent_tool",
                    risk_level=request.risk_level.value,
                )
                raise

        wrapper.__signature__ = signature  # type: ignore[attr-defined]
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


@dataclass
class LocalAgent:
    """Small fallback agent used when pyautogen is not installed."""

    name: str
    system_message: str = ""
    llm_config: dict[str, Any] | bool | None = None
    human_input_mode: str | None = None
    code_execution_config: dict[str, Any] | bool | None = False
    description: str | None = None
    registered_tools: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register_for_llm(self, name: str | None = None, description: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.registered_tools[name or func.__name__] = func
            return func

        return decorator

    def register_for_execution(self, name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.registered_tools[name or func.__name__] = func
            return func

        return decorator

    def initiate_chat(self, recipient: Any, message: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "sender": self.name,
            "recipient": getattr(recipient, "name", recipient.__class__.__name__),
            "message": message,
            "tools": sorted(self.registered_tools),
            "kwargs": kwargs,
        }


@dataclass
class LocalGroupChat:
    agents: list[Any]
    messages: list[dict[str, Any]] = field(default_factory=list)
    max_round: int = 12
    speaker_selection_method: str = "auto"


@dataclass
class LocalGroupChatManager:
    groupchat: LocalGroupChat
    llm_config: dict[str, Any] | bool | None = None
    name: str = "OmniSellGroupChatManager"


def create_assistant_agent(
    name: str,
    system_message: str,
    llm_config: dict[str, Any] | None = None,
    description: str | None = None,
) -> Any:
    """Create an explicitly optional Legacy AutoGen AssistantAgent."""
    config = llm_config if llm_config is not None else create_llm_config()
    assistant_agent = _require_legacy_runtime("AssistantAgent", AssistantAgent)

    kwargs: dict[str, Any] = {
        "name": name,
        "system_message": system_message,
        "llm_config": config,
    }
    if description:
        kwargs["description"] = description
    return assistant_agent(**kwargs)


def create_user_proxy_agent(name: str = "UserProxy", human_input_mode: str = "TERMINATE") -> Any:
    """Create an explicitly optional Legacy AutoGen UserProxyAgent."""
    user_proxy_agent = _require_legacy_runtime("UserProxyAgent", UserProxyAgent)
    return user_proxy_agent(
        name=name,
        human_input_mode=human_input_mode,
        code_execution_config=False,
        llm_config=False,
    )


def create_group_chat(agents: list[Any], max_round: int = 12) -> Any:
    """Create an explicitly optional Legacy AutoGen GroupChat."""
    group_chat = _require_legacy_runtime("GroupChat", GroupChat)
    return group_chat(agents=agents, messages=[], max_round=max_round, speaker_selection_method="auto")


def create_group_chat_manager(groupchat: Any, llm_config: dict[str, Any] | None = None) -> Any:
    """Create an explicitly optional Legacy AutoGen GroupChatManager."""
    config = llm_config if llm_config is not None else create_llm_config()
    group_chat_manager = _require_legacy_runtime("GroupChatManager", GroupChatManager)
    return group_chat_manager(groupchat=groupchat, llm_config=config)


def register_tools(agent: Any, executor: Any, tools: list[Callable[..., Any]]) -> None:
    """Register functions for AutoGen LLM calling and execution."""
    for tool in tools:
        safe_tool = safe_tool_execution(tool)
        description = inspect.getdoc(tool) or f"Tool function {tool.__name__}."

        if register_function is not None:
            register_function(
                safe_tool,
                caller=agent,
                executor=executor,
                name=tool.__name__,
                description=description,
            )
            continue

        if hasattr(agent, "register_for_llm"):
            agent.register_for_llm(name=tool.__name__, description=description)(safe_tool)
        if hasattr(executor, "register_for_execution"):
            executor.register_for_execution(name=tool.__name__)(safe_tool)
