from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from agents.contracts import EvidenceBundle, EvidenceReference
from agents.registry import AdvisorRegistry


class ReconciliationEvidenceCollector:
    def __init__(
        self,
        *,
        reconciliation_repository: Any,
        connection_repository: Any | None = None,
        task_repository: Any | None = None,
        event_repository: Any | None = None,
        audit_logger: Any | None = None,
        fresh_for: timedelta = timedelta(hours=24),
    ) -> None:
        self.reconciliation_repository = reconciliation_repository
        self.connection_repository = connection_repository
        self.task_repository = task_repository
        self.event_repository = event_repository
        self.audit_logger = audit_logger
        self.fresh_for = fresh_for

    async def collect(self, *, tenant_id: str, request: dict[str, Any]) -> EvidenceBundle:
        run_id = str(request.get("reconciliation_run_id") or "")
        if not run_id:
            raise ValueError("reconciliation_run_id is required")
        run = next(
            (
                item
                for item in self.reconciliation_repository.list_runs(tenant_id=tenant_id, limit=100)
                if item.id == run_id and item.tenant_id == tenant_id
            ),
            None,
        )
        if run is None:
            raise LookupError("Reconciliation run not found")
        collected_at = datetime.now(UTC)
        references: list[EvidenceReference] = []
        references.append(
            _reference(
                run.id,
                "reconciliation_run",
                "internal_record",
                "reconciliation_run",
                run.id,
                run.completed_at or run.started_at,
                collected_at,
                _json_safe(
                    {
                        "channel_connection_id": run.channel_connection_id,
                        "status": run.status,
                        "summary": run.summary,
                        "started_at": run.started_at,
                        "completed_at": run.completed_at,
                        "amazon_collection_window": {"started_at": run.started_at, "completed_at": run.completed_at},
                        "deterministic_values": {"status": run.status, "summary": run.summary},
                    }
                ),
                self.fresh_for,
            )
        )
        for difference in self.reconciliation_repository.differences(tenant_id=tenant_id, run_id=run.id):
            if difference.tenant_id != tenant_id:
                continue
            references.append(
                _reference(
                    difference.id,
                    "reconciliation_difference",
                    "internal_record",
                    difference.resource_type,
                    difference.resource_id,
                    _aware(difference.created_at),
                    collected_at,
                    _json_safe(
                        {
                            "run_id": difference.run_id,
                            "resource_type": difference.resource_type,
                            "resource_id": difference.resource_id,
                            "difference_type": difference.difference_type,
                            "amazon_value": difference.amazon_value,
                            "internal_value": difference.internal_value,
                            "deterministic_values": {
                                "difference_type": difference.difference_type,
                                "amazon_value": difference.amazon_value,
                                "internal_value": difference.internal_value,
                            },
                        }
                    ),
                    self.fresh_for,
                )
            )
        if self.connection_repository is not None:
            connection = self.connection_repository.get_connection(
                tenant_id=tenant_id, connection_id=run.channel_connection_id
            )
            if connection is not None:
                references.append(
                    _reference(
                        f"connection:{connection.id}",
                        "connection_metadata",
                        "internal_record",
                        "channel_connection",
                        connection.id,
                        _aware(connection.updated_at),
                        collected_at,
                        _json_safe(
                            {
                                "provider": _enum_value(connection.provider),
                                "region": connection.region,
                                "marketplace_ids": connection.marketplace_ids,
                                "status": _enum_value(connection.status),
                                "version": connection.version,
                            }
                        ),
                        self.fresh_for,
                    )
                )
        if self.task_repository is not None:
            for task in self.task_repository.list(tenant_id=tenant_id, limit=50):
                action = str(task.action).lower()
                if not any(token in action for token in ("amazon", "sync", "reconcil")):
                    continue
                references.append(
                    _reference(
                        f"task:{task.id}",
                        "sync_task",
                        "internal_record",
                        "durable_task",
                        task.id,
                        _aware(task.updated_at or task.created_at),
                        collected_at,
                        _json_safe(
                            {
                                "action": task.action,
                                "status": task.status,
                                "attempts": task.attempts,
                                "error_code": task.error_code,
                                "result_summary": task.result,
                                "untrusted_content": True,
                            }
                        ),
                        self.fresh_for,
                    )
                )
        if self.event_repository is not None:
            for event in self.event_repository.list_inbox_failures(tenant_id=tenant_id)[:50]:
                references.append(
                    _reference(
                        f"inbox:{event.id}",
                        "inbox_event_failure",
                        "internal_record",
                        "inbox_event",
                        event.id,
                        _aware(event.received_at),
                        collected_at,
                        _json_safe(
                            {
                                "provider": event.provider,
                                "external_event_id": event.external_event_id,
                                "event_type": event.event_type,
                                "status": event.status,
                                "attempts": event.attempts,
                                "error_code": event.error_code,
                                "untrusted_content": True,
                            }
                        ),
                        self.fresh_for,
                    )
                )
        if self.audit_logger is not None:
            for index, event in enumerate(self.audit_logger.query(tenant_id=tenant_id, limit=50)):
                observed_at = _parse_time(event.get("timestamp")) or collected_at
                references.append(
                    _reference(
                        f"audit:{index}",
                        "audit_event",
                        "internal_record",
                        "audit_event",
                        f"{run.id}:{index}",
                        observed_at,
                        collected_at,
                        _json_safe(
                            {
                                "event_type": event.get("event_type"),
                                "action": event.get("action"),
                                "status": event.get("status"),
                                "risk_level": event.get("risk_level"),
                                "details": event.get("details"),
                                "untrusted_content": True,
                            }
                        ),
                        self.fresh_for,
                    )
                )
        return EvidenceBundle(tenant_id=tenant_id, collected_at=collected_at, evidence=tuple(references))


class ReconciliationAgentService:
    WORKFLOW_TYPE = "reconciliation_investigation"

    def __init__(
        self,
        *,
        workflow: Any,
        registry: AdvisorRegistry,
        evidence_collector: ReconciliationEvidenceCollector,
        budget: dict[str, Any] | None = None,
    ) -> None:
        self.workflow = workflow
        self.registry = registry
        self.evidence_collector = evidence_collector
        self.budget = dict(budget or {})

    async def investigate(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        correlation_id: str,
        reconciliation_run_id: str,
        locale: str = "en-US",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self.workflow.run_advisory(
            tenant_id=tenant_id,
            actor_id=actor_id,
            workflow_type=self.WORKFLOW_TYPE,
            correlation_id=correlation_id,
            request={
                **self.budget,
                "reconciliation_run_id": reconciliation_run_id,
                "locale": locale,
                "idempotency_key": idempotency_key,
                "allowed_capabilities": ["read_evidence", "draft_internal_investigation_task"],
            },
            evidence_collector=self.evidence_collector,
            advisor=self.registry.get(self.WORKFLOW_TYPE),
        )


def _reference(
    evidence_id: str,
    evidence_type: str,
    source: str,
    resource_type: str,
    resource_id: str,
    observed_at: datetime,
    collected_at: datetime,
    metadata: dict[str, Any],
    fresh_for: timedelta,
) -> EvidenceReference:
    observed_at = _aware(observed_at)
    digest = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return EvidenceReference(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source=source,
        resource_type=resource_type,
        resource_id=resource_id,
        observed_at=observed_at,
        collected_at=collected_at,
        freshness_status="fresh" if collected_at - observed_at <= fresh_for else "stale",
        confidence=Decimal("1"),
        verified_amazon_data=False,
        content_digest=digest,
        metadata=metadata,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)
