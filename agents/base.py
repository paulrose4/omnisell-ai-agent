from __future__ import annotations

import inspect
import os
import time
from functools import wraps
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from autogen import AssistantAgent, GroupChat, GroupChatManager, UserProxyAgent, register_function
except Exception:
    AssistantAgent = None  # type: ignore[assignment]
    UserProxyAgent = None  # type: ignore[assignment]
    GroupChat = None  # type: ignore[assignment]
    GroupChatManager = None  # type: ignore[assignment]
    register_function = None  # type: ignore[assignment]


DEFAULT_VLLM_BASE_URL = "http://localhost:11434/v1"
DEFAULT_VLLM_MODEL_NAME = "qwen2.5:3b"


try:
    from safety.audit import AuditLogger
    from safety.circuit_breaker import CircuitBreaker
    from safety.hitl import ApprovalRequiredError, ApprovalStatus, HITLManager, RiskLevel
except Exception:
    AuditLogger = None  # type: ignore[assignment]
    CircuitBreaker = None  # type: ignore[assignment]
    HITLManager = None  # type: ignore[assignment]
    ApprovalRequiredError = RuntimeError  # type: ignore[assignment]
    ApprovalStatus = None  # type: ignore[assignment]
    RiskLevel = None  # type: ignore[assignment]

try:
    from safety.tool_permissions import (
        CallerRole,
        PermissionDeniedError,
        PermissionRegistry,
        register_standard_permissions,
    )
except Exception:
    CallerRole = None  # type: ignore[assignment]
    PermissionDeniedError = RuntimeError  # type: ignore[assignment]
    PermissionRegistry = None  # type: ignore[assignment]
    register_standard_permissions = None  # type: ignore[assignment]

try:
    from safety.refund_constraint import RefundExecutionBlockedError, RefundGuard
except Exception:
    RefundExecutionBlockedError = RuntimeError  # type: ignore[assignment]
    RefundGuard = None  # type: ignore[assignment]


_AUDIT_LOGGER: Any | None = None
_HITL_MANAGER: Any | None = None
_CIRCUIT_BREAKERS: dict[str, Any] = {}
_PERMISSION_REGISTRY: Any | None = None
_REFUND_GUARD: Any | None = None


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
    if name not in _CIRCUIT_BREAKERS:
        if CircuitBreaker is None:
            raise RuntimeError("safety.circuit_breaker.CircuitBreaker is unavailable.")
        _CIRCUIT_BREAKERS[name] = CircuitBreaker(name=name)
    return _CIRCUIT_BREAKERS[name]


def get_permission_registry() -> Any:
    """Return the process-wide tool permission registry."""
    global _PERMISSION_REGISTRY
    if _PERMISSION_REGISTRY is None:
        if PermissionRegistry is None:
            raise RuntimeError("safety.tool_permissions.PermissionRegistry is unavailable.")
        _PERMISSION_REGISTRY = PermissionRegistry(audit_logger=get_audit_logger())
        _PERMISSION_REGISTRY.set_hitl_manager(get_hitl_manager())
        if register_standard_permissions is not None:
            register_standard_permissions(_PERMISSION_REGISTRY)
    return _PERMISSION_REGISTRY


def get_refund_guard() -> Any:
    """Return the process-wide refund constraint guard."""
    global _REFUND_GUARD
    if _REFUND_GUARD is None:
        if RefundGuard is None:
            raise RuntimeError("safety.refund_constraint.RefundGuard is unavailable.")
        _REFUND_GUARD = RefundGuard(
            hitl_manager=get_hitl_manager(),
            audit_logger=get_audit_logger(),
        )
    return _REFUND_GUARD


def create_llm_config(
    base_url: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.2,
    timeout: int = 120,
) -> dict[str, Any]:
    """Create an AutoGen OpenAI-compatible LLM config for the local vLLM server."""
    resolved_base_url = base_url or os.getenv("VLLM_BASE_URL", DEFAULT_VLLM_BASE_URL)
    resolved_model_name = model_name or os.getenv("VLLM_MODEL_NAME", DEFAULT_VLLM_MODEL_NAME)
    resolved_api_key = api_key or os.getenv("VLLM_API_KEY", "not-needed")

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
    permission_registry: Any | None = None,
    refund_guard: Any | None = None,
    caller_role: str | None = None,
    wait_for_approval: bool = False,
    approval_poll_interval: float = 1.0,
    approval_wait_timeout: float = 30.0,
) -> Callable[..., Any]:
    """Wrap a tool with risk assessment, HITL, circuit breaker, and audit logging.

    Optional safety layers (opt-in):
      - permission_registry: if provided, validates caller/params/tenant
      - refund_guard: if provided, hard-blocks refund execution
    """

    def decorator(tool: Callable[..., Any]) -> Callable[..., Any]:
        manager = hitl_manager or get_hitl_manager()
        logger = audit_logger or get_audit_logger()
        breaker = circuit_breaker or get_circuit_breaker(tool.__name__)
        # Permission/refund are opt-in: only enforced when explicitly passed
        perms = permission_registry
        guard = refund_guard
        resolved_caller = caller_role or "system"

        @wraps(tool)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            payload = _build_tool_payload(tool, args, kwargs)

            # --- Layer 1: Permission check (opt-in) ---
            if perms is not None:
                if CallerRole is not None and caller_role:
                    caller_enum = CallerRole(resolved_caller)
                else:
                    caller_enum = CallerRole.SYSTEM if CallerRole is not None else resolved_caller
                perm_result = perms.check(
                    tool_name=tool.__name__,
                    caller=caller_enum,
                    params=kwargs,
                )
                if not perm_result.allowed:
                    logger.log_operation(
                        action=tool.__name__,
                        status="blocked_permission",
                        details={"violations": perm_result.violations, "caller": resolved_caller},
                        risk_level="high",
                    )
                    raise PermissionDeniedError(perm_result) if PermissionDeniedError is not RuntimeError else RuntimeError(
                        f"Permission denied: {'; '.join(perm_result.violations)}"
                    )

            # --- Layer 2: Refund guard (opt-in) ---
            if guard is not None:
                refund_result = guard.check(tool.__name__, payload)
                if refund_result.blocked:
                    raise RefundExecutionBlockedError(refund_result) if RefundExecutionBlockedError is not RuntimeError else RuntimeError(
                        f"REFUND BLOCKED: {refund_result.reason}"
                    )

            # --- Layer 3: Risk assessment & HITL ---
            risk, risk_reason = manager.assess_risk(tool.__name__, payload)
            if risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                approval = manager.submit_request(
                    action=tool.__name__,
                    payload=payload,
                    risk_level=risk,
                    reason=risk_reason,
                )
                if approval.status != ApprovalStatus.AUTO_APPROVED:
                    if wait_for_approval:
                        _wait_for_approval(manager, approval.request_id, approval_poll_interval, approval_wait_timeout)
                    else:
                        logger.log_operation(
                            action=tool.__name__,
                            status="blocked_pending_approval",
                            details={"request_id": approval.request_id, "risk_level": risk.value},
                            risk_level=risk.value,
                        )
                        raise ApprovalRequiredError(approval)

            # --- Layer 4: Circuit breaker + execution ---
            try:
                result = breaker.call(tool, *args, **kwargs)
                if inspect.isawaitable(result):
                    # The circuit breaker returned an unresolved awaitable
                    # (happens when there is a running event loop).
                    # Return it wrapped so the caller (asyncio.run / await) resolves it
                    # with audit logging applied.
                    async def _execute_awaitable(aw: Any) -> Any:
                        try:
                            resolved = await aw
                            logger.log_operation(tool.__name__, "success", payload, risk_level=risk.value)
                            return resolved
                        except Exception as exc:
                            if isinstance(exc, (ApprovalRequiredError, PermissionDeniedError, RefundExecutionBlockedError)):
                                raise
                            logger.log_operation(
                                tool.__name__, "failure",
                                {**payload, "error": str(exc)}, risk_level=risk.value,
                            )
                            raise
                    return _execute_awaitable(result)

                logger.log_operation(tool.__name__, "success", payload, risk_level=risk.value)
                return result
            except Exception as exc:
                if isinstance(exc, (ApprovalRequiredError, PermissionDeniedError, RefundExecutionBlockedError)):
                    raise
                logger.log_operation(
                    tool.__name__,
                    "failure",
                    {**payload, "error": str(exc)},
                    risk_level=risk.value,
                )
                raise

        wrapper.__signature__ = inspect.signature(tool)  # type: ignore[attr-defined]
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
    """Create a real AutoGen AssistantAgent, or a local fallback agent for tests."""
    config = llm_config if llm_config is not None else create_llm_config()
    if AssistantAgent is None:
        return LocalAgent(name=name, system_message=system_message, llm_config=config, description=description)

    kwargs: dict[str, Any] = {
        "name": name,
        "system_message": system_message,
        "llm_config": config,
    }
    if description:
        kwargs["description"] = description
    return AssistantAgent(**kwargs)


def create_user_proxy_agent(name: str = "UserProxy", human_input_mode: str = "TERMINATE") -> Any:
    """Create a real AutoGen UserProxyAgent, or a local fallback agent for tests."""
    if UserProxyAgent is None:
        return LocalAgent(
            name=name,
            human_input_mode=human_input_mode,
            llm_config=False,
            code_execution_config=False,
            description="Human operator proxy.",
        )

    return UserProxyAgent(
        name=name,
        human_input_mode=human_input_mode,
        code_execution_config=False,
        llm_config=False,
    )


def create_group_chat(agents: list[Any], max_round: int = 12) -> Any:
    """Create an AutoGen GroupChat, or a local fallback group chat."""
    if GroupChat is None:
        return LocalGroupChat(agents=agents, max_round=max_round)
    return GroupChat(agents=agents, messages=[], max_round=max_round, speaker_selection_method="auto")


def create_group_chat_manager(groupchat: Any, llm_config: dict[str, Any] | None = None) -> Any:
    """Create an AutoGen GroupChatManager, or a local fallback manager."""
    config = llm_config if llm_config is not None else create_llm_config()
    if GroupChatManager is None:
        return LocalGroupChatManager(groupchat=groupchat, llm_config=config)
    return GroupChatManager(groupchat=groupchat, llm_config=config)


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
