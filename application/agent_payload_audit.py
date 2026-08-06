from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from agents.contract_adapters import LegacyAgentPayloadError, adapt_persisted_analysis
from infrastructure.database.models import AgentRunRow

AGENT_PAYLOAD_SCAN_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class AgentPayloadRecord:
    record_id: str
    tenant_id: str
    workflow_type: str
    stored_schema_version: str | None
    analysis_result: Any


def _tenant_ref(tenant_id: str) -> str:
    return hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]


def _reason_code(message: str) -> str:
    if message.startswith("Unsupported workflow_type"):
        return "unsupported_workflow"
    if message.startswith("Unsupported agent schema_version"):
        return "unsupported_schema_version"
    if "non-JSON-compatible" in message or "finite JSON number" in message:
        return "non_json_value"
    if any(
        marker in message
        for marker in (
            "maximum depth",
            "length exceeds",
            "collection size exceeds",
            "total nodes",
            "too many nodes",
            "too many items",
            "too long",
        )
    ):
        return "resource_budget_exceeded"
    return "invalid_payload"


def scan_agent_payload_records(
    records: Iterable[AgentPayloadRecord],
    *,
    generated_at: datetime | None = None,
    evidence_class: str = "deterministic_fixture",
) -> dict[str, Any]:
    generated = generated_at or datetime.now(UTC)
    totals = {
        "records_scanned": 0,
        "payload_records": 0,
        "compatible_records": 0,
        "adapted_v1_records": 0,
        "validated_v2_records": 0,
        "empty_payload_records": 0,
        "rejected_records": 0,
    }
    rejection_counts: Counter[str] = Counter()
    manual_review_records: list[dict[str, Any]] = []
    tenant_refs: set[str] = set()

    for record in records:
        totals["records_scanned"] += 1
        tenant_refs.add(_tenant_ref(record.tenant_id))
        payload = record.analysis_result
        if payload in (None, {}):
            totals["empty_payload_records"] += 1
            continue
        totals["payload_records"] += 1
        if not isinstance(payload, dict):
            error = LegacyAgentPayloadError("Persisted agent analysis payload must be a non-empty object")
        else:
            try:
                source_version = str(payload.get("schema_version") or "1.0")
                adapt_persisted_analysis(workflow_type=record.workflow_type, payload=payload)
                totals["compatible_records"] += 1
                if source_version == "1.0":
                    totals["adapted_v1_records"] += 1
                else:
                    totals["validated_v2_records"] += 1
                continue
            except LegacyAgentPayloadError as exc:
                error = exc

        reason_code = _reason_code(str(error))
        rejection_counts[reason_code] += 1
        totals["rejected_records"] += 1
        source_version = (
            str(payload.get("schema_version") or record.stored_schema_version or "1.0")
            if isinstance(payload, dict)
            else str(record.stored_schema_version or "unknown")
        )
        manual_review_records.append(
            {
                "record_id": record.record_id,
                "tenant_ref": _tenant_ref(record.tenant_id),
                "workflow_type": record.workflow_type,
                "source_schema_version": source_version,
                "reason_code": reason_code,
            }
        )

    manual_review_records.sort(key=lambda item: (item["reason_code"], item["record_id"]))
    return {
        "schema_version": AGENT_PAYLOAD_SCAN_SCHEMA_VERSION,
        "scan_mode": "read_only",
        "evidence_class": evidence_class,
        "generated_at": generated.astimezone(UTC).isoformat(),
        "tenant_scope": {
            "tenant_count": len(tenant_refs),
            "tenant_refs": sorted(tenant_refs),
            "raw_tenant_ids_included": False,
        },
        "totals": totals,
        "rejections_by_reason": [
            {"reason_code": reason_code, "count": rejection_counts[reason_code]}
            for reason_code in sorted(rejection_counts)
        ],
        "manual_review_records": manual_review_records,
        "payload_content_included": False,
    }


def _database_records(
    session_factory: sessionmaker[Session],
    *,
    tenant_ids: Sequence[str],
) -> Iterable[AgentPayloadRecord]:
    normalized_tenant_ids = tuple(sorted({tenant_id.strip() for tenant_id in tenant_ids if tenant_id.strip()}))
    if not normalized_tenant_ids:
        raise ValueError("At least one explicit tenant_id is required for the payload compatibility scan")

    for tenant_id in normalized_tenant_ids:
        session = session_factory()
        try:
            if session.bind and session.bind.dialect.name == "postgresql":
                session.execute(text("SET TRANSACTION READ ONLY"))
                session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
            statement = (
                select(
                    AgentRunRow.id,
                    AgentRunRow.tenant_id,
                    AgentRunRow.workflow_type,
                    AgentRunRow.schema_version,
                    AgentRunRow.analysis_result,
                )
                .where(AgentRunRow.tenant_id == tenant_id)
                .order_by(AgentRunRow.created_at, AgentRunRow.id)
                .execution_options(yield_per=500)
            )
            for row in session.execute(statement):
                yield AgentPayloadRecord(
                    record_id=str(row.id),
                    tenant_id=str(row.tenant_id),
                    workflow_type=str(row.workflow_type),
                    stored_schema_version=None if row.schema_version is None else str(row.schema_version),
                    analysis_result=row.analysis_result,
                )
        finally:
            session.rollback()
            session.close()


def scan_agent_payload_database(
    session_factory: sessionmaker[Session],
    *,
    tenant_ids: Sequence[str],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    return scan_agent_payload_records(
        _database_records(session_factory, tenant_ids=tenant_ids),
        generated_at=generated_at,
        evidence_class="persisted_database",
    )


def render_agent_payload_scan_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
