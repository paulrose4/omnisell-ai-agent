from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "logs" / "audit.jsonl"
_SECRET_KEY_MARKERS = ("token", "secret", "password", "authorization", "api_key", "credential")
_SENSITIVE_CONTENT_KEYS = frozenset(
    {
        "evidence",
        "order",
        "order_context",
        "external_order_id",
        "customer_message",
        "buyer_message",
        "message_history",
        "draft_reply",
        "approved_reply",
        "actual_reply",
        "reply",
        "prompt",
        "messages",
        "content",
        "address",
        "shipping_address",
        "billing_address",
        "buyer_email",
        "buyer_name",
        "phone",
    }
)


@dataclass
class AuditRecord:
    timestamp: str
    event_type: str
    actor: str
    action: str
    risk_level: str
    status: str
    details: dict[str, Any]


class AuditLogger:
    """Append-only JSONL audit logger with a small query API."""

    def __init__(self, path: str | Path = DEFAULT_AUDIT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def log(
        self,
        event_type: str,
        action: str,
        status: str,
        details: dict[str, Any] | None = None,
        actor: str = "system",
        risk_level: str = "low",
    ) -> AuditRecord:
        """Append a single audit record and return it."""
        sanitized_details = sanitize_audit_details(details or {})
        record = AuditRecord(
            timestamp=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            event_type=event_type,
            actor=actor,
            action=action,
            risk_level=risk_level,
            status=status,
            details=sanitized_details,
        )
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
        return record

    def log_operation(
        self,
        action: str,
        status: str,
        details: dict[str, Any] | None = None,
        actor: str = "agent",
        risk_level: str = "low",
    ) -> AuditRecord:
        """Log a regular tool or business operation."""
        return self.log("operation", action, status, details, actor=actor, risk_level=risk_level)

    def log_approval(
        self,
        action: str,
        status: str,
        details: dict[str, Any] | None = None,
        actor: str = "hitl",
        risk_level: str = "high",
    ) -> AuditRecord:
        """Log a HITL approval decision or state change."""
        return self.log("approval", action, status, details, actor=actor, risk_level=risk_level)

    def log_sandbox(
        self,
        action: str,
        status: str,
        details: dict[str, Any] | None = None,
        actor: str = "sandbox",
        risk_level: str = "medium",
    ) -> AuditRecord:
        """Log a sandbox execution result."""
        return self.log("sandbox", action, status, details, actor=actor, risk_level=risk_level)

    def query(
        self,
        event_type: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        action: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query audit records from the JSONL log file."""
        if not self.path.exists():
            return []

        records: list[dict[str, Any]] = []
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if event_type and item.get("event_type") != event_type:
                    continue
                if status and item.get("status") != status:
                    continue
                if risk_level and item.get("risk_level") != risk_level:
                    continue
                if action and item.get("action") != action:
                    continue
                records.append(item)

        if limit is not None:
            return records[-limit:]
        return records


def sanitize_audit_details(details: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_audit_value(value, key=str(key).lower()) for key, value in details.items()}


def _sanitize_audit_value(value: Any, *, key: str) -> Any:
    normalized_key = key.replace("-", "_").lower()
    if any(marker in normalized_key for marker in _SECRET_KEY_MARKERS):
        return "[REDACTED]"
    if normalized_key in _SENSITIVE_CONTENT_KEYS:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return {
            "redacted": "[REDACTED]",
            "length": len(serialized),
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        }
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_audit_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_audit_value(item, key=normalized_key) for item in value]
    return value


if __name__ == "__main__":
    logger = AuditLogger()
    logger.log_operation("audit_smoke_test", "success", {"ok": True})
    print(logger.query(action="audit_smoke_test", limit=1))
