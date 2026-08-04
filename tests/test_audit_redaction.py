from __future__ import annotations

import json

from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.audit import SQLAuditLogger
from safety.audit import AuditLogger


SENSITIVE_VALUE = "buyer-secret-content-should-never-be-audited"


def _details() -> dict:
    return {
        "tenant_id": "tenant-a",
        "action_hash": "a" * 64,
        "access_token": "secret-access-token",
        "payload": {
            "draft_reply": SENSITIVE_VALUE,
            "evidence": {
                "order": {
                    "external_order_id": "ORDER-PRIVATE-1",
                    "shipping_address": "1 Private Street",
                    "buyer_message": SENSITIVE_VALUE,
                }
            },
            "headers": {"Authorization": "Bearer secret-token"},
        },
    }


def _assert_redacted(details: dict) -> None:
    serialized = json.dumps(details, ensure_ascii=False)
    assert details["tenant_id"] == "tenant-a"
    assert details["action_hash"] == "a" * 64
    assert SENSITIVE_VALUE not in serialized
    assert "secret-access-token" not in serialized
    assert "secret-token" not in serialized
    assert "1 Private Street" not in serialized
    assert "ORDER-PRIVATE-1" not in serialized
    assert "[REDACTED]" in serialized


def test_jsonl_audit_logger_redacts_sensitive_nested_payloads(tmp_path) -> None:
    logger = AuditLogger(tmp_path / "audit.jsonl")

    record = logger.log_approval("send_customer_reply", "pending", _details())

    _assert_redacted(record.details)
    _assert_redacted(logger.query(limit=1)[0]["details"])


def test_sql_audit_logger_redacts_sensitive_nested_payloads(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'audit.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    logger = SQLAuditLogger(create_session_factory(database_url))

    record = logger.log_approval("send_customer_reply", "pending", _details())

    _assert_redacted(record.details)
    _assert_redacted(logger.query(tenant_id="tenant-a", limit=1)[0]["details"])
