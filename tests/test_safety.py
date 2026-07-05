from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.base import safe_tool_execution
from safety.audit import AuditLogger
from safety.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from safety.hitl import ApprovalRejectedError, ApprovalRequiredError, ApprovalStatus, HITLManager, RiskLevel
from sandbox.executor import SandboxExecutor
from sandbox.policy import SandboxPolicyError, validate_code


class FakeContainer:
    def __init__(self, stdout: bytes = b"ok\n", stderr: bytes = b"", exit_code: int = 0) -> None:
        self.status = "exited"
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.killed = False
        self.removed = False

    def reload(self) -> None:
        return None

    def wait(self) -> dict[str, int]:
        return {"StatusCode": self.exit_code}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        if stdout and not stderr:
            return self.stdout
        if stderr and not stdout:
            return self.stderr
        return self.stdout + self.stderr

    def kill(self) -> None:
        self.killed = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.container = FakeContainer()

    def run(self, **kwargs: Any) -> FakeContainer:
        self.kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()


def test_sandbox_policy_blocks_dangerous_code() -> None:
    validate_code("import json\nprint(json.dumps({'ok': True}))")

    with pytest.raises(SandboxPolicyError):
        validate_code("eval('1 + 1')")

    with pytest.raises(SandboxPolicyError):
        validate_code("import os\nprint(os.getcwd())")


def test_sandbox_executor_uses_locked_down_docker_options() -> None:
    fake_client = FakeDockerClient()
    executor = SandboxExecutor(client=fake_client)

    result = executor.execute("print('ok')")

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    kwargs = fake_client.containers.kwargs
    assert kwargs is not None
    assert kwargs["read_only"] is True
    assert kwargs["network_mode"] == "none"
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["cpu_period"] == 100_000
    assert kwargs["cpu_quota"] == 50_000
    assert kwargs["privileged"] is False
    assert kwargs["user"] == "sandbox_user"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]


def test_circuit_breaker_state_transitions() -> None:
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0, half_open_max_calls=1)

    def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        breaker.call(fail)
    assert breaker.state == CircuitState.CLOSED

    with pytest.raises(RuntimeError):
        breaker.call(fail)
    assert breaker.state == CircuitState.OPEN

    assert breaker.call(lambda: "recovered") == "recovered"
    assert breaker.state == CircuitState.CLOSED


def test_circuit_breaker_blocks_while_open() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "blocked")


def test_hitl_rules_auto_pending_critical_and_expiry(tmp_path: Path) -> None:
    manager = HITLManager(audit_logger=AuditLogger(tmp_path / "audit.jsonl"))

    low = manager.submit_request("get_product_list", {})
    medium = manager.submit_request("update_product", {"current_price": 100, "price": 94})
    high = manager.submit_request("delete_product", {"product_id": "P1001"})
    critical = manager.submit_request("update_product", {"current_price": 100, "price": 70})

    assert low.status == ApprovalStatus.AUTO_APPROVED
    assert low.risk_level == RiskLevel.LOW
    assert medium.status == ApprovalStatus.AUTO_APPROVED
    assert medium.risk_level == RiskLevel.MEDIUM
    assert high.status == ApprovalStatus.PENDING
    assert high.risk_level == RiskLevel.HIGH
    assert critical.status == ApprovalStatus.PENDING
    assert critical.risk_level == RiskLevel.CRITICAL
    assert critical.requires_second_verification is True

    with pytest.raises(ApprovalRejectedError):
        manager.approve(critical.request_id, approver="lead")

    approved = manager.approve(critical.request_id, approver="lead", second_verifier="owner")
    assert approved.status == ApprovalStatus.APPROVED

    high.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    manager.expire_overdue()
    assert high.status == ApprovalStatus.EXPIRED


def test_audit_logger_writes_and_queries_jsonl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    logger.log_operation("tool_a", "success", {"value": 1}, risk_level="low")
    logger.log_approval("tool_b", "pending", {"value": 2}, risk_level="high")

    assert len(logger.query()) == 2
    assert logger.query(event_type="approval")[0]["action"] == "tool_b"
    assert logger.query(status="success")[0]["details"]["value"] == 1


def test_safe_tool_execution_allows_low_risk_and_audits(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    manager = HITLManager(audit_logger=logger)
    breaker = CircuitBreaker()

    @safe_tool_execution(hitl_manager=manager, audit_logger=logger, circuit_breaker=breaker)
    def get_product_list(page_size: int = 20) -> dict[str, int]:
        """Read products."""
        return {"page_size": page_size}

    assert get_product_list(page_size=5) == {"page_size": 5}
    records = logger.query(action="get_product_list")
    assert any(record["status"] == "success" for record in records)


def test_safe_tool_execution_blocks_high_risk(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    manager = HITLManager(audit_logger=logger)
    breaker = CircuitBreaker()

    @safe_tool_execution(hitl_manager=manager, audit_logger=logger, circuit_breaker=breaker)
    def delete_product(product_id: str) -> dict[str, str]:
        """Delete a product."""
        return {"product_id": product_id}

    with pytest.raises(ApprovalRequiredError) as exc:
        delete_product("P1001")

    assert exc.value.request.status == ApprovalStatus.PENDING
    assert manager.get_pending()[0].action == "delete_product"
    assert logger.query(status="blocked_pending_approval")


def test_safe_tool_execution_supports_async_tools(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")
    manager = HITLManager(audit_logger=logger)
    breaker = CircuitBreaker()

    @safe_tool_execution(hitl_manager=manager, audit_logger=logger, circuit_breaker=breaker)
    async def get_order_list(page_size: int = 20) -> dict[str, int]:
        """Read orders."""
        return {"page_size": page_size}

    # The decorated wrapper is sync; call it directly
    result = get_order_list(page_size=7)

    assert result == {"page_size": 7}
    assert logger.query(action="get_order_list", status="success")
