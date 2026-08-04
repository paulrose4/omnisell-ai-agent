from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, sessionmaker

from domain.operations_learning import normalize_voc_reason, route_voc_reason
from domain.work_items import sla_due_at
from infrastructure.database.models import (
    CustomerCaseClusterMemberRow,
    CustomerCaseClusterRow,
    CustomerCaseEventRow,
    CustomerCaseRow,
    OperationalIssueRow,
    OperationalWorkItemRow,
    OutboxEventRow,
    RecommendedActionRow,
)
from safety.policy_engine import canonical_action_hash


_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class CustomerCaseOperationsBridge:
    """Deterministically groups confirmed Case root causes into governed work items."""

    def __init__(self, session_factory: sessionmaker[Session], *, minimum_samples: int = 3, window_days: int = 7) -> None:
        if minimum_samples < 2 or window_days < 1:
            raise ValueError("case cluster policy must require at least two samples and a positive window")
        self.session_factory = session_factory
        self.minimum_samples = minimum_samples
        self.window_days = window_days

    def process_case(self, *, tenant_id: str, case_id: str, actor_id: str = "case-operations-bridge") -> dict[str, Any]:
        with self.session_factory.begin() as session:
            case = session.scalar(select(CustomerCaseRow).where(
                CustomerCaseRow.tenant_id == tenant_id,
                CustomerCaseRow.id == case_id,
            ).with_for_update())
            if case is None:
                raise LookupError("customer case not found")
            reason = normalize_voc_reason(case.root_cause_code)
            if not case.seller_sku or reason == "unknown":
                return {"status": "not_clusterable", "reason": "seller_sku_or_supported_root_cause_missing", "cluster": None, "work_item": None}

            member = session.scalar(select(CustomerCaseClusterMemberRow).where(
                CustomerCaseClusterMemberRow.tenant_id == tenant_id,
                CustomerCaseClusterMemberRow.case_id == case.id,
            ))
            if member is None:
                cluster = self._get_or_create_cluster(session, case=case, reason=reason)
                session.add(CustomerCaseClusterMemberRow(
                    id=str(uuid4()), tenant_id=tenant_id, cluster_id=cluster.id,
                    case_id=case.id, linked_at=_now(),
                ))
                cluster.sample_count += 1
                cluster.severity = _max_severity(cluster.severity, case.severity)
                cluster.verified_amazon_data = bool(cluster.verified_amazon_data and case.verified_amazon_data)
                evidence = list(cluster.evidence_manifest or [])
                evidence.append({
                    "case_id": case.id,
                    "seller_sku": case.seller_sku,
                    "root_cause_code": reason,
                    "source": case.source,
                    "verified_amazon_data": bool(case.verified_amazon_data),
                    "observed_at": _utc(case.created_at).isoformat(),
                })
                cluster.evidence_manifest = evidence
                cluster.updated_at = _now()
                self._case_event(session, tenant_id=tenant_id, case_id=case.id, event_type="root_cause_cluster_linked", actor_id=actor_id, payload={
                    "cluster_id": cluster.id,
                    "root_cause_code": reason,
                    "sample_count": cluster.sample_count,
                    "minimum_samples": cluster.minimum_samples,
                })
            else:
                cluster = session.scalar(select(CustomerCaseClusterRow).where(
                    CustomerCaseClusterRow.tenant_id == tenant_id,
                    CustomerCaseClusterRow.id == member.cluster_id,
                ))
                if cluster is None:
                    raise RuntimeError("customer case cluster member points to a missing cluster")

            if cluster.sample_count >= cluster.minimum_samples:
                self._ensure_operational_chain(session, cluster=cluster, actor_id=actor_id)
            session.flush()
            cluster_id = cluster.id

        return self.case_context(tenant_id=tenant_id, case_id=case_id) or {
            "status": "linked", "cluster": {"id": cluster_id}, "work_item": None,
        }

    def process_pending_outbox(self, *, tenant_id: str, limit: int = 100, actor_id: str = "case-operations-bridge") -> dict[str, int]:
        from infrastructure.repositories.tasks import EventRepository

        events = EventRepository(self.session_factory)
        claimed = events.claim_outbox(tenant_id=tenant_id, limit=limit)
        processed = 0
        failed = 0
        for event in claimed:
            try:
                if event.event_type in {"case.created", "case.resolved", "case.root_cause_confirmed"}:
                    self.process_case(tenant_id=tenant_id, case_id=event.aggregate_id, actor_id=actor_id)
                events.mark_outbox_processed(tenant_id=tenant_id, event_id=event.id)
                processed += 1
            except Exception as exc:
                events.mark_outbox_retry(tenant_id=tenant_id, event_id=event.id, error=exc)
                failed += 1
        propagated = self.propagate_completed(tenant_id=tenant_id, actor_id=actor_id)
        return {"claimed": len(claimed), "processed": processed, "failed": failed, "case_events_propagated": propagated}

    def case_context(self, *, tenant_id: str, case_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            member = session.scalar(select(CustomerCaseClusterMemberRow).where(
                CustomerCaseClusterMemberRow.tenant_id == tenant_id,
                CustomerCaseClusterMemberRow.case_id == case_id,
            ))
            if member is None:
                return None
            cluster = session.scalar(select(CustomerCaseClusterRow).where(
                CustomerCaseClusterRow.tenant_id == tenant_id,
                CustomerCaseClusterRow.id == member.cluster_id,
            ))
            if cluster is None:
                return None
            work_item = None
            if cluster.work_item_id:
                work_item = session.scalar(select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == cluster.work_item_id,
                ))
            related = list(session.execute(
                select(CustomerCaseRow.id, CustomerCaseRow.status, CustomerCaseRow.severity, CustomerCaseRow.created_at)
                .join(CustomerCaseClusterMemberRow, and_(
                    CustomerCaseClusterMemberRow.tenant_id == CustomerCaseRow.tenant_id,
                    CustomerCaseClusterMemberRow.case_id == CustomerCaseRow.id,
                ))
                .where(
                    CustomerCaseClusterMemberRow.tenant_id == tenant_id,
                    CustomerCaseClusterMemberRow.cluster_id == cluster.id,
                ).order_by(CustomerCaseRow.created_at)
            ))
            return {
                "status": cluster.status,
                "cluster": _cluster_payload(cluster),
                "work_item": None if work_item is None else _work_item_payload(work_item),
                "related_cases": [
                    {"case_id": row.id, "status": row.status, "severity": row.severity, "created_at": _utc(row.created_at).isoformat()}
                    for row in related
                ],
            }

    def work_item_context(self, *, tenant_id: str, work_item_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            cluster = session.scalar(select(CustomerCaseClusterRow).where(
                CustomerCaseClusterRow.tenant_id == tenant_id,
                CustomerCaseClusterRow.work_item_id == work_item_id,
            ))
            if cluster is None:
                return None
            case_ids = list(session.scalars(select(CustomerCaseClusterMemberRow.case_id).where(
                CustomerCaseClusterMemberRow.tenant_id == tenant_id,
                CustomerCaseClusterMemberRow.cluster_id == cluster.id,
            )))
            return {"cluster": _cluster_payload(cluster), "case_ids": case_ids, "related_case_count": len(case_ids)}

    def propagate_completed(self, *, tenant_id: str, actor_id: str = "case-operations-bridge") -> int:
        emitted = 0
        with self.session_factory.begin() as session:
            clusters = list(session.scalars(
                select(CustomerCaseClusterRow)
                .join(OperationalWorkItemRow, and_(
                    OperationalWorkItemRow.tenant_id == CustomerCaseClusterRow.tenant_id,
                    OperationalWorkItemRow.id == CustomerCaseClusterRow.work_item_id,
                ))
                .where(
                    CustomerCaseClusterRow.tenant_id == tenant_id,
                    CustomerCaseClusterRow.status != "resolved",
                    OperationalWorkItemRow.status == "completed",
                ).with_for_update()
            ))
            for cluster in clusters:
                work_item = session.scalar(select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == cluster.work_item_id,
                ))
                members = list(session.scalars(select(CustomerCaseClusterMemberRow).where(
                    CustomerCaseClusterMemberRow.tenant_id == tenant_id,
                    CustomerCaseClusterMemberRow.cluster_id == cluster.id,
                )))
                for member in members:
                    exists = session.scalar(select(CustomerCaseEventRow.id).where(
                        CustomerCaseEventRow.tenant_id == tenant_id,
                        CustomerCaseEventRow.case_id == member.case_id,
                        CustomerCaseEventRow.event_type == "root_cause_remediation_completed",
                    ))
                    if exists:
                        continue
                    self._case_event(session, tenant_id=tenant_id, case_id=member.case_id, event_type="root_cause_remediation_completed", actor_id=actor_id, payload={
                        "cluster_id": cluster.id,
                        "work_item_id": cluster.work_item_id,
                        "result_metrics": {} if work_item is None else dict(work_item.result_metrics or {}),
                        "case_status_unchanged": True,
                    })
                    emitted += 1
                cluster.status = "resolved"
                cluster.resolved_at = _now()
                cluster.updated_at = cluster.resolved_at
                self._outbox(session, tenant_id=tenant_id, aggregate_id=cluster.id, event_type="case_cluster.remediation_completed", payload={
                    "work_item_id": cluster.work_item_id,
                    "related_case_count": len(members),
                })
        return emitted

    def reconcile(self, *, tenant_id: str) -> dict[str, int]:
        with self.session_factory() as session:
            orphan_members = int(session.scalar(
                select(func.count()).select_from(CustomerCaseClusterMemberRow)
                .outerjoin(CustomerCaseRow, and_(
                    CustomerCaseRow.tenant_id == CustomerCaseClusterMemberRow.tenant_id,
                    CustomerCaseRow.id == CustomerCaseClusterMemberRow.case_id,
                ))
                .where(CustomerCaseClusterMemberRow.tenant_id == tenant_id, CustomerCaseRow.id.is_(None))
            ) or 0)
            missing_work_items = int(session.scalar(select(func.count()).select_from(CustomerCaseClusterRow).where(
                CustomerCaseClusterRow.tenant_id == tenant_id,
                CustomerCaseClusterRow.sample_count >= CustomerCaseClusterRow.minimum_samples,
                CustomerCaseClusterRow.work_item_id.is_(None),
            )) or 0)
            counts = dict(session.execute(
                select(OutboxEventRow.status, func.count()).where(
                    OutboxEventRow.tenant_id == tenant_id,
                    OutboxEventRow.aggregate_type.in_(["customer_case", "customer_case_cluster"]),
                ).group_by(OutboxEventRow.status)
            ).all())
            return {
                "orphan_case_members": orphan_members,
                "actionable_clusters_without_work_items": missing_work_items,
                "outbox_pending": int(counts.get("pending", 0)),
                "outbox_processing": int(counts.get("processing", 0)),
                "outbox_retry_pending": int(counts.get("retry_pending", 0)),
                "outbox_dead_letter": int(counts.get("dead_letter", 0)),
            }

    def dashboard_summary(self, *, tenant_id: str) -> dict[str, int]:
        reconciliation = self.reconcile(tenant_id=tenant_id)
        with self.session_factory() as session:
            open_cases = int(session.scalar(select(func.count()).select_from(CustomerCaseRow).where(
                CustomerCaseRow.tenant_id == tenant_id,
                CustomerCaseRow.status.not_in(["resolved", "dismissed"]),
            )) or 0)
            actionable = int(session.scalar(select(func.count()).select_from(CustomerCaseClusterRow).where(
                CustomerCaseClusterRow.tenant_id == tenant_id,
                CustomerCaseClusterRow.sample_count >= CustomerCaseClusterRow.minimum_samples,
                CustomerCaseClusterRow.status != "resolved",
            )) or 0)
        inconsistencies = (
            reconciliation["orphan_case_members"]
            + reconciliation["actionable_clusters_without_work_items"]
            + reconciliation["outbox_dead_letter"]
        )
        return {
            "open_customer_cases": open_cases,
            "actionable_case_clusters": actionable,
            "unlinked_actionable_case_clusters": reconciliation["actionable_clusters_without_work_items"],
            "case_flow_inconsistencies": inconsistencies,
        }

    def _get_or_create_cluster(self, session: Session, *, case: CustomerCaseRow, reason: str) -> CustomerCaseClusterRow:
        window_start = _window_start(_utc(case.created_at), self.window_days)
        window_end = window_start + timedelta(days=self.window_days)
        dedupe_key = sha256(
            f"{case.tenant_id}:{case.marketplace_id}:{case.seller_sku}:{reason}:{window_start.isoformat()}".encode("utf-8")
        ).hexdigest()
        cluster = session.scalar(select(CustomerCaseClusterRow).where(
            CustomerCaseClusterRow.tenant_id == case.tenant_id,
            CustomerCaseClusterRow.dedupe_key == dedupe_key,
        ).with_for_update())
        if cluster is not None:
            return cluster
        route = route_voc_reason(reason)
        now = _now()
        cluster = CustomerCaseClusterRow(
            id=str(uuid4()), tenant_id=case.tenant_id, marketplace_id=case.marketplace_id,
            seller_sku=case.seller_sku, root_cause_code=reason,
            window_start=window_start, window_end=window_end,
            minimum_samples=self.minimum_samples, sample_count=0, status="collecting",
            severity=case.severity, domain=route["domain"], team=route["team"],
            evidence_manifest=[], verified_amazon_data=bool(case.verified_amazon_data),
            dedupe_key=dedupe_key, created_at=now, updated_at=now,
        )
        session.add(cluster)
        session.flush()
        return cluster

    def _ensure_operational_chain(self, session: Session, *, cluster: CustomerCaseClusterRow, actor_id: str) -> None:
        now = _now()
        if cluster.operational_issue_id:
            issue = session.scalar(select(OperationalIssueRow).where(
                OperationalIssueRow.tenant_id == cluster.tenant_id,
                OperationalIssueRow.id == cluster.operational_issue_id,
            ))
        else:
            issue = None
        if issue is None:
            issue_id = str(uuid4())
            issue = OperationalIssueRow(
                id=issue_id, tenant_id=cluster.tenant_id, seller_sku=cluster.seller_sku,
                marketplace_id=cluster.marketplace_id, issue_type=f"customer_case_root_cause_{cluster.root_cause_code}",
                severity=cluster.severity, title=f"{cluster.seller_sku} 客诉根因达到整改门槛",
                summary=f"{cluster.window_start.date().isoformat()} 至 {cluster.window_end.date().isoformat()} 已确认 {cluster.sample_count} 条 {cluster.root_cause_code} Case。",
                status="open", dedupe_key=cluster.dedupe_key, confidence=Decimal("1"),
                data_freshness_at=now, evidence=list(cluster.evidence_manifest or []), missing_data=[],
                occurrence_count=cluster.sample_count, detected_at=now, last_detected_at=now,
                created_at=now, updated_at=now, priority_score=Decimal(_SEVERITY_RANK[cluster.severity] * 100 + cluster.sample_count),
            )
            session.add(issue)
            session.flush()
            cluster.operational_issue_id = issue.id
        else:
            issue.occurrence_count = cluster.sample_count
            issue.evidence = list(cluster.evidence_manifest or [])
            issue.summary = f"当前已确认 {cluster.sample_count} 条同 SKU、同根因 Case。"
            issue.last_detected_at = now
            issue.updated_at = now

        action = None
        if cluster.recommended_action_id:
            action = session.scalar(select(RecommendedActionRow).where(
                RecommendedActionRow.tenant_id == cluster.tenant_id,
                RecommendedActionRow.id == cluster.recommended_action_id,
            ))
        if action is None:
            action = RecommendedActionRow(
                id=str(uuid4()), tenant_id=cluster.tenant_id, issue_id=issue.id,
                action_type="investigate_customer_case_root_cause",
                title=f"调查并整改 {cluster.root_cause_code}",
                description="复核关联 Case、退货与 VOC 证据，确认根因后由责任团队整改；不得自动关闭买家 Case。",
                execution_mode="single_approval", risk_level="high", status="proposed",
                parameters={
                    "cluster_id": cluster.id, "reason_code": cluster.root_cause_code,
                    "seller_sku": cluster.seller_sku, "domain": cluster.domain, "team": cluster.team,
                },
                evidence=list(cluster.evidence_manifest or []),
                expected_outcome="降低该 SKU 同根因投诉率，并在观察窗口内验证结果。",
                confidence=Decimal("1"), automation_eligible=False,
                dedupe_key=sha256(f"case-cluster-action:{cluster.dedupe_key}".encode("utf-8")).hexdigest(),
                created_at=now, updated_at=now,
            )
            session.add(action)
            session.flush()
            cluster.recommended_action_id = action.id
        else:
            action.evidence = list(cluster.evidence_manifest or [])
            action.updated_at = now

        work_item = None
        if cluster.work_item_id:
            work_item = session.scalar(select(OperationalWorkItemRow).where(
                OperationalWorkItemRow.tenant_id == cluster.tenant_id,
                OperationalWorkItemRow.id == cluster.work_item_id,
            ))
        baseline = {
            "related_case_count": cluster.sample_count,
            "minimum_samples": cluster.minimum_samples,
            "root_cause_code": cluster.root_cause_code,
            "window_start": cluster.window_start.isoformat(),
            "window_end": cluster.window_end.isoformat(),
            "verified_amazon_data": bool(cluster.verified_amazon_data),
        }
        if work_item is None:
            action_hash = canonical_action_hash(
                action=action.action_type, tenant_id=cluster.tenant_id,
                resource_id=f"{cluster.marketplace_id}:{cluster.seller_sku}:{cluster.id}",
                arguments=action.parameters or {},
            )
            work_item = OperationalWorkItemRow(
                id=str(uuid4()), tenant_id=cluster.tenant_id,
                source_type="customer_case_cluster", source_id=cluster.id,
                operational_issue_id=issue.id, recommended_action_id=action.id,
                domain=cluster.domain, team=cluster.team, seller_sku=cluster.seller_sku,
                marketplace_id=cluster.marketplace_id, severity=cluster.severity,
                priority_score=issue.priority_score, title=action.title, description=action.description,
                status="open", due_at=sla_due_at(cluster.severity, now),
                sla_policy_version="case-root-cause-v1", escalation_level=0,
                expected_outcome=action.expected_outcome, baseline_metrics=baseline,
                result_metrics={}, verification_status="pending", verification_attempts=0,
                evidence_manifest=list(cluster.evidence_manifest or []), action_hash=action_hash,
                created_at=now, updated_at=now,
            )
            session.add(work_item)
            session.flush()
            cluster.work_item_id = work_item.id
            cluster.actionable_at = now
            self._outbox(session, tenant_id=cluster.tenant_id, aggregate_id=cluster.id, event_type="case_cluster.actionable", payload={
                "work_item_id": work_item.id,
                "root_cause_code": cluster.root_cause_code,
                "related_case_count": cluster.sample_count,
                "verified_amazon_data": bool(cluster.verified_amazon_data),
            })
        else:
            work_item.baseline_metrics = baseline
            work_item.evidence_manifest = list(cluster.evidence_manifest or [])
            work_item.updated_at = now
        cluster.status = "linked"
        cluster.updated_at = now

    @staticmethod
    def _case_event(session: Session, *, tenant_id: str, case_id: str, event_type: str, actor_id: str, payload: dict[str, Any]) -> None:
        session.add(CustomerCaseEventRow(
            id=str(uuid4()), tenant_id=tenant_id, case_id=case_id, event_type=event_type,
            actor_id=actor_id, payload=payload, created_at=_now(),
        ))

    @staticmethod
    def _outbox(session: Session, *, tenant_id: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> None:
        session.add(OutboxEventRow(
            id=str(uuid4()), tenant_id=tenant_id, aggregate_type="customer_case_cluster",
            aggregate_id=aggregate_id, event_type=event_type,
            dedupe_key=f"{event_type}:{aggregate_id}", payload=payload, status="pending", attempts=0,
        ))


def _window_start(value: datetime, window_days: int) -> datetime:
    day = value.astimezone(UTC).date()
    ordinal = day.toordinal()
    start = day.fromordinal(ordinal - (ordinal % window_days))
    return datetime(start.year, start.month, start.day, tzinfo=UTC)


def _max_severity(left: str, right: str) -> str:
    return left if _SEVERITY_RANK[left] >= _SEVERITY_RANK[right] else right


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cluster_payload(row: CustomerCaseClusterRow) -> dict[str, Any]:
    return {
        "id": row.id, "status": row.status, "marketplace_id": row.marketplace_id,
        "seller_sku": row.seller_sku, "root_cause_code": row.root_cause_code,
        "window_start": _utc(row.window_start).isoformat(), "window_end": _utc(row.window_end).isoformat(),
        "minimum_samples": row.minimum_samples, "sample_count": row.sample_count,
        "severity": row.severity, "domain": row.domain, "team": row.team,
        "operational_issue_id": row.operational_issue_id, "recommended_action_id": row.recommended_action_id,
        "work_item_id": row.work_item_id, "evidence_manifest": list(row.evidence_manifest or []),
        "verified_amazon_data": bool(row.verified_amazon_data),
    }


def _work_item_payload(row: OperationalWorkItemRow) -> dict[str, Any]:
    return {
        "id": row.id, "source_type": row.source_type, "source_id": row.source_id,
        "operational_issue_id": row.operational_issue_id, "domain": row.domain, "team": row.team,
        "seller_sku": row.seller_sku, "marketplace_id": row.marketplace_id,
        "severity": row.severity, "title": row.title, "status": row.status,
        "baseline_metrics": dict(row.baseline_metrics or {}), "result_metrics": dict(row.result_metrics or {}),
        "verification_status": row.verification_status,
    }
