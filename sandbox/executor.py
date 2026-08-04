from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

try:
    import docker
except Exception:
    docker = None  # type: ignore[assignment]

try:
    from .policy import SandboxPolicyError, validate_code
except ImportError:
    from policy import SandboxPolicyError, validate_code  # type: ignore[no-redef]


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable execution result."""
        return asdict(self)


class SandboxExecutor:
    """Execute Python code inside a locked-down Docker container."""

    def __init__(
        self,
        image: str = "omnisell-sandbox:1.0.0",
        timeout_seconds: int = 30,
        memory_limit: str = "256m",
        cpu_quota: int = 50_000,
        cpu_period: int = 100_000,
        client: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")

        self.image = image
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.cpu_quota = cpu_quota
        self.cpu_period = cpu_period
        self.client = client

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        if docker is None:
            raise RuntimeError("Docker SDK is not installed. Install the `docker` Python package.")
        self.client = docker.from_env()
        return self.client

    def execute(self, code: str) -> ExecutionResult:
        """Validate and execute Python code in a network-disabled, read-only Docker container."""
        started_at = time.monotonic()

        try:
            validate_code(code)
        except SandboxPolicyError as exc:
            return ExecutionResult(stdout="", stderr=f"Sandbox policy violation: {exc}", exit_code=126)

        container = None
        try:
            container = self._client().containers.run(
                image=self.image,
                command=["python", "-c", code],
                detach=True,
                read_only=True,
                network_mode="none",
                mem_limit=self.memory_limit,
                cpu_period=self.cpu_period,
                cpu_quota=self.cpu_quota,
                privileged=False,
                user="sandbox_user",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                tmpfs={"/tmp": "rw,noexec,nosuid,size=32m"},
                stdout=True,
                stderr=True,
            )

            deadline = started_at + self.timeout_seconds
            while True:
                container.reload()
                status = getattr(container, "status", None)
                if status == "exited":
                    break
                if time.monotonic() >= deadline:
                    container.kill()
                    stdout, stderr = self._read_logs(container)
                    return ExecutionResult(
                        stdout=stdout,
                        stderr=(stderr + "\nExecution timed out after 30 seconds.").strip(),
                        exit_code=124,
                        timed_out=True,
                        duration_seconds=round(time.monotonic() - started_at, 4),
                    )
                time.sleep(0.1)

            wait_result = container.wait()
            exit_code = int(wait_result.get("StatusCode", 1)) if isinstance(wait_result, dict) else int(wait_result)
            stdout, stderr = self._read_logs(container)
            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=False,
                duration_seconds=round(time.monotonic() - started_at, 4),
            )
        except Exception as exc:
            return ExecutionResult(
                stdout="",
                stderr=f"Sandbox execution failed: {exc}",
                exit_code=1,
                timed_out=False,
                duration_seconds=round(time.monotonic() - started_at, 4),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _read_logs(self, container: Any) -> tuple[str, str]:
        stdout = container.logs(stdout=True, stderr=False)
        stderr = container.logs(stdout=False, stderr=True)
        return self._decode(stdout), self._decode(stderr)

    @staticmethod
    def _decode(payload: bytes | str | None) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        return payload.decode("utf-8", errors="replace")


if __name__ == "__main__":
    result = SandboxExecutor().execute("print('hello from sandbox')")
    print(result.to_dict())
