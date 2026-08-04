from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from domain.models import OperationalIssue, RecommendedAction
from domain.operational_intelligence import AmazonOperationalSignal, calculate_advertising_metrics, diagnose_advertising, summarize_voc
from domain.operations_learning import evaluate_sponsored_products, normalize_voc_reason, route_voc_reason
from infrastructure.database.models import (
    AdvertisingBudgetDiagnosticRow,
    AdvertisingDailySnapshotRow,
    AmazonOperationalSignalRow,
    BuyerMessageTagRow,
    ReturnEventRow,
    ReviewObservationRow,
    SearchTermPerformanceSnapshotRow,
    SKUVoCSummaryRow,
)
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.work_items import OperationalWorkItemRepository


class AmazonOperationalSignalRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.issue_repository = OperationalIssueRepository(session_factory)
        self.action_repository = RecommendedActionRepository(session_factory)
        self.work_item_repository = OperationalWorkItemRepository(session_factory)

    def upsert(self, signal: AmazonOperationalSignal) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            row = session.scalar(select(AmazonOperationalSignalRow).where(
                AmazonOperationalSignalRow.tenant_id == signal.tenant_id,
                AmazonOperationalSignalRow.signal_type == signal.signal_type,
                AmazonOperationalSignalRow.resource_id == signal.resource_id,
                AmazonOperationalSignalRow.observed_at == signal.observed_at,
            ))
            if row is None:
                row = AmazonOperationalSignalRow(id=signal.id or str(uuid4()), tenant_id=signal.tenant_id, signal_type=signal.signal_type, resource_id=signal.resource_id)
                session.add(row)
            row.marketplace_id = signal.marketplace_id
            row.severity = signal.severity
            row.title = signal.title
            row.summary = signal.summary
            row.observed_at = signal.observed_at
            row.collected_at = signal.collected_at
            row.freshness_seconds = signal.freshness_seconds
            row.verified_amazon_data = signal.verified_amazon_data
            row.source = signal.source
            row.raw_status_code = signal.raw_status_code
            row.evidence_manifest = signal.evidence_manifest
            row.created_at = row.created_at or signal.collected_at
            row.updated_at = signal.collected_at
            session.flush()
            saved = _signal_dict(row)
        now = datetime.now(UTC)
        dedupe = sha256(f"{signal.tenant_id}:{signal.signal_type}:{signal.marketplace_id}:{signal.resource_id}".encode()).hexdigest()[:64]
        evidence = ({
            "signal_id": saved["id"], "resource_id": signal.resource_id,
            "observed_at": signal.observed_at.isoformat(), "collected_at": signal.collected_at.isoformat(),
            "freshness_seconds": signal.freshness_seconds, "verified_amazon_data": signal.verified_amazon_data,
            "source": signal.source, "raw_status_code": signal.raw_status_code,
        },)
        issue = self.issue_repository.upsert(OperationalIssue(
            tenant_id=signal.tenant_id, seller_sku=signal.resource_id, marketplace_id=signal.marketplace_id,
            issue_type=signal.signal_type, severity=signal.severity, title=signal.title, summary=signal.summary,
            status="open", dedupe_key=dedupe, confidence=Decimal("0.98") if signal.verified_amazon_data else Decimal("0.60"),
            data_freshness_at=signal.observed_at, evidence=evidence,
            missing_data=() if signal.verified_amazon_data else ("verified_amazon_data",),
            detected_at=now, last_detected_at=now, created_at=now, updated_at=now,
        ))
        action = self.action_repository.upsert(RecommendedAction(
            tenant_id=signal.tenant_id, issue_id=issue.id, action_type=f"review_{signal.signal_type}",
            title=f"复核并处理：{signal.title}", description="执行最小必要的只读检查，确认事实后再进入独立审批或人工 Seller Central 处理。",
            execution_mode="single_approval", risk_level=signal.severity, status="proposed",
            parameters={"resource_id": signal.resource_id, "signal_type": signal.signal_type}, evidence=evidence,
            expected_outcome="确认 Amazon 状态、归属负责人并记录可验证的处理结果。",
            confidence=Decimal("0.98") if signal.verified_amazon_data else Decimal("0.60"),
            automation_eligible=False, dedupe_key=dedupe, created_at=now, updated_at=now,
        ), canonical_issue_id=issue.id)
        item = self.work_item_repository.get_by_source(tenant_id=signal.tenant_id, source_type="recommended_action", source_id=action.id)
        saved["work_item_id"] = item.id if item else None
        return saved

    def list(self, *, tenant_id: str, signal_type: str | None = None, severity: str | None = None) -> list[dict[str, Any]]:
        statement = select(AmazonOperationalSignalRow).where(AmazonOperationalSignalRow.tenant_id == tenant_id)
        if signal_type:
            statement = statement.where(AmazonOperationalSignalRow.signal_type == signal_type)
        if severity:
            statement = statement.where(AmazonOperationalSignalRow.severity == severity)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(AmazonOperationalSignalRow.observed_at.desc())))
        return [_signal_dict(row) for row in rows]


class AdvertisingRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.issue_repository = OperationalIssueRepository(session_factory)
        self.action_repository = RecommendedActionRepository(session_factory)
        self.work_item_repository = OperationalWorkItemRepository(session_factory)

    def save_daily_snapshot(self, *, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        start = _datetime(payload["period_start"])
        end = _datetime(payload["period_end"])
        observed = _datetime(payload.get("observed_at") or end)
        collected = _datetime(payload.get("collected_at") or observed)
        source = str(payload.get("source") or "manual")
        verified = bool(payload.get("verified_amazon_data", False))
        _validate_source(source, verified)
        spend = _decimal(payload.get("spend", "0"))
        attributed_sales = _optional_decimal(payload.get("attributed_sales"))
        total_sales = _optional_decimal(payload.get("total_sales"))
        metrics = _json_safe({
            **payload.get("metrics", {}),
            "elapsed_days": payload.get("elapsed_days", "1"),
            "period_days": payload.get("period_days", "7"),
            "break_even_acos": payload.get("break_even_acos"),
            "contribution_profit": payload.get("contribution_profit"),
            "inventory_status": payload.get("inventory_status"),
        })
        calculated = _json_safe(calculate_advertising_metrics(
            spend=spend, attributed_sales=attributed_sales, total_sales=total_sales,
            impressions=payload.get("impressions"), clicks=payload.get("clicks"), orders=payload.get("orders"),
        ))
        metrics.update(calculated)
        with self.session_factory.begin() as session:
            row = session.scalar(select(AdvertisingDailySnapshotRow).where(
                AdvertisingDailySnapshotRow.tenant_id == tenant_id,
                AdvertisingDailySnapshotRow.entity_type == payload["entity_type"],
                AdvertisingDailySnapshotRow.entity_id == payload["entity_id"],
                AdvertisingDailySnapshotRow.period_start == start,
            ))
            if row is None:
                row = AdvertisingDailySnapshotRow(id=str(uuid4()), tenant_id=tenant_id, entity_type=payload["entity_type"], entity_id=payload["entity_id"], period_start=start)
                session.add(row)
            row.marketplace_id = payload["marketplace_id"]
            row.seller_sku = payload["seller_sku"]
            row.campaign_id = payload.get("campaign_id")
            row.ad_group_id = payload.get("ad_group_id")
            row.period_end = end
            row.currency = str(payload.get("currency", "USD")).upper()
            row.spend = spend
            row.attributed_sales = attributed_sales
            row.total_sales = total_sales
            row.budget = _optional_decimal(payload.get("budget"))
            row.impressions = payload.get("impressions")
            row.clicks = payload.get("clicks")
            row.orders = payload.get("orders")
            row.metrics = metrics
            row.source = source
            row.verified_amazon_data = verified
            row.observed_at = observed
            row.collected_at = collected
            row.created_at = row.created_at or collected
            row.updated_at = collected
            session.flush()
            return _ad_snapshot_dict(row)

    def save_search_term(self, *, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        start = _datetime(payload["period_start"])
        end = _datetime(payload["period_end"])
        source = str(payload.get("source") or "manual")
        verified = bool(payload.get("verified_amazon_data", False))
        _validate_source(source, verified)
        spend = _decimal(payload.get("spend", "0"))
        attributed_sales = _optional_decimal(payload.get("attributed_sales"))
        metrics = _json_safe(calculate_advertising_metrics(
            spend=spend,
            attributed_sales=attributed_sales,
            total_sales=None,
            impressions=payload.get("impressions"),
            clicks=payload.get("clicks"),
            orders=payload.get("orders"),
        ))
        metrics.update(_json_safe(payload.get("metrics", {})))
        with self.session_factory.begin() as session:
            row = session.scalar(select(SearchTermPerformanceSnapshotRow).where(
                SearchTermPerformanceSnapshotRow.tenant_id == tenant_id,
                SearchTermPerformanceSnapshotRow.campaign_id == payload["campaign_id"],
                SearchTermPerformanceSnapshotRow.search_term == payload["search_term"],
                SearchTermPerformanceSnapshotRow.period_start == start,
            ))
            if row is None:
                row = SearchTermPerformanceSnapshotRow(id=str(uuid4()), tenant_id=tenant_id, campaign_id=payload["campaign_id"], search_term=payload["search_term"], period_start=start)
                session.add(row)
            row.marketplace_id = payload["marketplace_id"]
            row.seller_sku = payload["seller_sku"]
            row.ad_group_id = payload.get("ad_group_id")
            row.period_end = end
            row.currency = str(payload.get("currency", "USD")).upper()
            row.spend = spend
            row.attributed_sales = attributed_sales
            row.impressions = payload.get("impressions")
            row.clicks = payload.get("clicks")
            row.orders = payload.get("orders")
            row.metrics = metrics
            row.source = source
            row.verified_amazon_data = verified
            row.observed_at = _datetime(payload.get("observed_at") or end)
            row.collected_at = _datetime(payload.get("collected_at") or row.observed_at)
            row.created_at = row.created_at or row.collected_at
            row.updated_at = row.collected_at
            session.flush()
            return _search_term_dict(row)

    def diagnose_daily_snapshot(self, *, tenant_id: str, snapshot_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            snapshot = session.scalar(select(AdvertisingDailySnapshotRow).where(
                AdvertisingDailySnapshotRow.tenant_id == tenant_id, AdvertisingDailySnapshotRow.id == snapshot_id,
            ))
        if snapshot is None:
            raise LookupError("advertising snapshot not found")
        observed_at = _as_utc(snapshot.observed_at)
        collected_at = _as_utc(snapshot.collected_at)
        metrics = snapshot.metrics or {}
        evaluation = evaluate_sponsored_products(
            seller_sku=snapshot.seller_sku, marketplace_id=snapshot.marketplace_id,
            campaign_id=snapshot.campaign_id or snapshot.entity_id, spend=snapshot.spend,
            attributed_sales=snapshot.attributed_sales, total_sales=snapshot.total_sales,
            impressions=snapshot.impressions, clicks=snapshot.clicks, orders=snapshot.orders,
            budget=snapshot.budget, elapsed_days=_decimal(metrics.get("elapsed_days", "1")),
            period_days=_decimal(metrics.get("period_days", "7")),
            break_even_acos=_optional_decimal(metrics.get("break_even_acos")),
            contribution_profit=_optional_decimal(metrics.get("contribution_profit")),
            inventory_status=metrics.get("inventory_status"),
        )
        findings = evaluation["findings"]
        candidates_by_code = {
            "click_waste_without_orders": "negative_keyword_candidate",
            "acos_above_break_even": "decrease_bid_candidate",
            "budget_projected_to_exhaust_early": "budget_reallocation_candidate",
            "low_acos_but_contribution_loss": "pause_candidate",
            "inventory_guard_blocks_growth": "review_inventory_guard",
        }
        results = []
        for finding in findings:
            action = self._ensure_work_item(
                tenant_id=tenant_id, seller_sku=snapshot.seller_sku, marketplace_id=snapshot.marketplace_id,
                issue_type=f"advertising_{finding['code']}", severity=finding["severity"], title=finding["title"],
                summary=f"Campaign {finding['campaign_id']} requires deterministic advertising review.",
                evidence=[{"snapshot_id": snapshot.id, "source": snapshot.source, "verified_amazon_data": snapshot.verified_amazon_data, **_json_safe(finding)}],
                action_type=candidates_by_code.get(finding["code"], f"review_{finding['code']}"), expected_outcome="Complete an operator-reviewed advertising change and verify the next reporting period.",
                parameters={
                    "campaign_id": finding["campaign_id"], "seller_sku": snapshot.seller_sku,
                    "automation_eligible": False, "execution_mode": "manual_only",
                    "growth_blocked": evaluation["guards"]["growth_blocked"],
                },
                freshness=observed_at,
            )
            with self.session_factory.begin() as session:
                row = session.scalar(select(AdvertisingBudgetDiagnosticRow).where(
                    AdvertisingBudgetDiagnosticRow.tenant_id == tenant_id,
                    AdvertisingBudgetDiagnosticRow.snapshot_id == snapshot.id,
                    AdvertisingBudgetDiagnosticRow.code == finding["code"],
                ))
                if row is None:
                    row = AdvertisingBudgetDiagnosticRow(id=str(uuid4()), tenant_id=tenant_id, snapshot_id=snapshot.id, code=finding["code"])
                    session.add(row)
                row.seller_sku = snapshot.seller_sku
                row.marketplace_id = snapshot.marketplace_id
                row.campaign_id = finding["campaign_id"]
                row.severity = finding["severity"]
                row.title = finding["title"]
                row.details = _json_safe(finding)
                row.status = "open"
                row.work_item_id = action["work_item_id"]
                row.created_at = row.created_at or collected_at
                row.updated_at = collected_at
                session.flush()
                results.append(_diagnostic_dict(row))
        return results

    def list_diagnostics(self, *, tenant_id: str, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(AdvertisingBudgetDiagnosticRow).where(AdvertisingBudgetDiagnosticRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(AdvertisingBudgetDiagnosticRow.status == status)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(AdvertisingBudgetDiagnosticRow.created_at.desc())))
        return [_diagnostic_dict(row) for row in rows]

    def list_diagnostics_page(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [AdvertisingBudgetDiagnosticRow.tenant_id == tenant_id]
        if status:
            filters.append(AdvertisingBudgetDiagnosticRow.status == status)
        offset = (page - 1) * page_size
        with self.session_factory() as session:
            total = int(session.scalar(
                select(func.count()).select_from(AdvertisingBudgetDiagnosticRow).where(*filters)
            ) or 0)
            rows = list(session.scalars(
                select(AdvertisingBudgetDiagnosticRow)
                .where(*filters)
                .order_by(
                    AdvertisingBudgetDiagnosticRow.created_at.desc(),
                    AdvertisingBudgetDiagnosticRow.id.desc(),
                )
                .offset(offset)
                .limit(page_size)
            ))
        return [_diagnostic_dict(row) for row in rows], total

    def list_snapshots(self, *, tenant_id: str, seller_sku: str | None = None) -> list[dict[str, Any]]:
        statement = select(AdvertisingDailySnapshotRow).where(AdvertisingDailySnapshotRow.tenant_id == tenant_id)
        if seller_sku:
            statement = statement.where(AdvertisingDailySnapshotRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(AdvertisingDailySnapshotRow.period_end.desc())))
        return [_ad_snapshot_dict(row) for row in rows]

    def list_search_terms(self, *, tenant_id: str, seller_sku: str | None = None) -> list[dict[str, Any]]:
        statement = select(SearchTermPerformanceSnapshotRow).where(SearchTermPerformanceSnapshotRow.tenant_id == tenant_id)
        if seller_sku:
            statement = statement.where(SearchTermPerformanceSnapshotRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(SearchTermPerformanceSnapshotRow.period_end.desc())))
        return [_search_term_dict(row) for row in rows]

    def list_search_terms_page(
        self,
        *,
        tenant_id: str,
        seller_sku: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [SearchTermPerformanceSnapshotRow.tenant_id == tenant_id]
        if seller_sku:
            filters.append(SearchTermPerformanceSnapshotRow.seller_sku == seller_sku)
        offset = (page - 1) * page_size
        with self.session_factory() as session:
            total = int(session.scalar(
                select(func.count()).select_from(SearchTermPerformanceSnapshotRow).where(*filters)
            ) or 0)
            rows = list(session.scalars(
                select(SearchTermPerformanceSnapshotRow)
                .where(*filters)
                .order_by(
                    SearchTermPerformanceSnapshotRow.period_end.desc(),
                    SearchTermPerformanceSnapshotRow.id.desc(),
                )
                .offset(offset)
                .limit(page_size)
            ))
        return [_search_term_dict(row) for row in rows], total

    def _ensure_work_item(self, *, tenant_id: str, seller_sku: str, marketplace_id: str, issue_type: str, severity: str, title: str, summary: str, evidence: list[dict[str, Any]], action_type: str, expected_outcome: str, parameters: dict[str, Any], freshness: datetime) -> dict[str, Any]:
        now = datetime.now(UTC)
        dedupe = sha256(f"{tenant_id}:{issue_type}:{marketplace_id}:{seller_sku}:{parameters}".encode()).hexdigest()[:64]
        issue = self.issue_repository.upsert(OperationalIssue(
            tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=marketplace_id,
            issue_type=issue_type, severity=severity, title=title, summary=summary, status="open",
            dedupe_key=dedupe, confidence=Decimal("0.95"), data_freshness_at=freshness,
            evidence=tuple(evidence), missing_data=(), detected_at=now, last_detected_at=now,
            created_at=now, updated_at=now, priority_score=Decimal("0"),
        ))
        action = self.action_repository.upsert(RecommendedAction(
            tenant_id=tenant_id, issue_id=issue.id, action_type=action_type, title=title,
            description=summary, execution_mode="single_approval", risk_level=severity, status="proposed",
            parameters=parameters, evidence=tuple(evidence), expected_outcome=expected_outcome,
            confidence=Decimal("0.95"), automation_eligible=False, dedupe_key=dedupe,
            created_at=now, updated_at=now,
        ), canonical_issue_id=issue.id)
        item = self.work_item_repository.get_by_source(tenant_id=tenant_id, source_type="recommended_action", source_id=action.id)
        return {"issue_id": issue.id, "action_id": action.id, "work_item_id": item.id if item else None}


class VoCRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.issue_repository = OperationalIssueRepository(session_factory)
        self.action_repository = RecommendedActionRepository(session_factory)
        self.work_item_repository = OperationalWorkItemRepository(session_factory)

    def save_return(self, *, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source") or "manual")
        verified = bool(payload.get("verified_amazon_data", False))
        _validate_source(source, verified)
        with self.session_factory.begin() as session:
            row = session.scalar(select(ReturnEventRow).where(ReturnEventRow.tenant_id == tenant_id, ReturnEventRow.external_return_id == payload["external_return_id"]))
            if row is None:
                row = ReturnEventRow(id=str(uuid4()), tenant_id=tenant_id, external_return_id=payload["external_return_id"])
                session.add(row)
            row.external_order_id = payload.get("external_order_id")
            row.marketplace_id = payload["marketplace_id"]
            row.seller_sku = payload["seller_sku"]
            row.reason_code = normalize_voc_reason(payload["reason_code"])
            row.quantity = int(payload.get("quantity", 1))
            row.return_amount = _optional_decimal(payload.get("return_amount"))
            row.currency = payload.get("currency")
            row.observed_at = _datetime(payload["observed_at"])
            row.source = source
            row.verified_amazon_data = verified
            row.evidence_manifest = _json_safe(payload.get("evidence_manifest", []))
            row.created_at = row.created_at or row.observed_at
            session.flush()
            return _return_dict(row)

    def save_review(self, *, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source") or "manual")
        verified = bool(payload.get("verified_amazon_data", False))
        _validate_source(source, verified)
        with self.session_factory.begin() as session:
            row = session.scalar(select(ReviewObservationRow).where(ReviewObservationRow.tenant_id == tenant_id, ReviewObservationRow.external_review_id == payload["external_review_id"]))
            if row is None:
                row = ReviewObservationRow(id=str(uuid4()), tenant_id=tenant_id, external_review_id=payload["external_review_id"])
                session.add(row)
            row.marketplace_id = payload["marketplace_id"]
            row.seller_sku = payload["seller_sku"]
            row.rating = int(payload["rating"])
            row.review_text = payload.get("review_text")
            row.reason_code = normalize_voc_reason(payload.get("reason_code"))
            row.observed_at = _datetime(payload["observed_at"])
            row.source = source
            row.verified_amazon_data = verified
            row.evidence_manifest = _json_safe(payload.get("evidence_manifest", []))
            row.created_at = row.created_at or row.observed_at
            session.flush()
            return _review_dict(row)

    def save_message_tag(self, *, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source") or "manual")
        verified = bool(payload.get("verified_amazon_data", False))
        _validate_source(source, verified)
        normalized_tag = normalize_voc_reason(payload["tag"])
        with self.session_factory.begin() as session:
            row = session.scalar(select(BuyerMessageTagRow).where(BuyerMessageTagRow.tenant_id == tenant_id, BuyerMessageTagRow.external_message_id == payload["external_message_id"], BuyerMessageTagRow.tag == normalized_tag))
            if row is None:
                row = BuyerMessageTagRow(id=str(uuid4()), tenant_id=tenant_id, external_message_id=payload["external_message_id"], tag=normalized_tag)
                session.add(row)
            row.marketplace_id = payload["marketplace_id"]
            row.seller_sku = payload["seller_sku"]
            row.confidence = _optional_decimal(payload.get("confidence"))
            row.observed_at = _datetime(payload["observed_at"])
            row.source = source
            row.verified_amazon_data = verified
            row.evidence_manifest = _json_safe(payload.get("evidence_manifest", []))
            row.created_at = row.created_at or row.observed_at
            session.flush()
            return _message_dict(row)

    def summarize(self, *, tenant_id: str, seller_sku: str, marketplace_id: str, period_start: datetime, period_end: datetime, min_sample_size: int = 5) -> dict[str, Any]:
        with self.session_factory() as session:
            returns = list(session.scalars(select(ReturnEventRow).where(ReturnEventRow.tenant_id == tenant_id, ReturnEventRow.seller_sku == seller_sku, ReturnEventRow.marketplace_id == marketplace_id, ReturnEventRow.observed_at >= period_start, ReturnEventRow.observed_at <= period_end)))
            reviews = list(session.scalars(select(ReviewObservationRow).where(ReviewObservationRow.tenant_id == tenant_id, ReviewObservationRow.seller_sku == seller_sku, ReviewObservationRow.marketplace_id == marketplace_id, ReviewObservationRow.observed_at >= period_start, ReviewObservationRow.observed_at <= period_end)))
            messages = list(session.scalars(select(BuyerMessageTagRow).where(BuyerMessageTagRow.tenant_id == tenant_id, BuyerMessageTagRow.seller_sku == seller_sku, BuyerMessageTagRow.marketplace_id == marketplace_id, BuyerMessageTagRow.observed_at >= period_start, BuyerMessageTagRow.observed_at <= period_end)))
        summary = summarize_voc(
            tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=marketplace_id,
            returns=[{"reason_code": row.reason_code, "quantity": row.quantity, "observed_at": row.observed_at.isoformat()} for row in returns],
            reviews=[{"rating": row.rating, "reason_code": row.reason_code, "observed_at": row.observed_at.isoformat()} for row in reviews],
            messages=[{"tag": row.tag, "observed_at": row.observed_at.isoformat()} for row in messages],
            min_sample_size=min_sample_size,
        )
        work_item_id = None
        if summary["recommended_action"]:
            evidence = [{"summary": _json_safe(summary), "source": "deterministic_voc_aggregation", "verified_amazon_data": all(row.verified_amazon_data for row in returns + reviews + messages)}]
            action = self._ensure_work_item(tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=marketplace_id, summary=summary, evidence=evidence, freshness=period_end)
            work_item_id = action["work_item_id"]
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(select(SKUVoCSummaryRow).where(SKUVoCSummaryRow.tenant_id == tenant_id, SKUVoCSummaryRow.marketplace_id == marketplace_id, SKUVoCSummaryRow.seller_sku == seller_sku, SKUVoCSummaryRow.period_start == period_start, SKUVoCSummaryRow.period_end == period_end))
            if row is None:
                row = SKUVoCSummaryRow(id=str(uuid4()), tenant_id=tenant_id, marketplace_id=marketplace_id, seller_sku=seller_sku, period_start=period_start, period_end=period_end)
                session.add(row)
            row.return_count = len(returns)
            row.review_count = len(reviews)
            row.message_count = len(messages)
            row.sample_size = summary["sample_size"]
            row.reason_counts = summary["reason_counts"]
            row.top_reason_code = summary["top_reason_code"]
            row.top_reason_count = summary["top_reason_count"]
            row.status = summary["status"]
            row.confidence = Decimal("0.85") if summary["status"] == "actionable" else Decimal("0.35")
            row.recommended_action = _json_safe(summary["recommended_action"])
            row.work_item_id = work_item_id
            row.created_at = row.created_at or now
            row.updated_at = now
            session.flush()
            return _voc_dict(row)

    def list_summaries(self, *, tenant_id: str, seller_sku: str | None = None) -> list[dict[str, Any]]:
        statement = select(SKUVoCSummaryRow).where(SKUVoCSummaryRow.tenant_id == tenant_id)
        if seller_sku:
            statement = statement.where(SKUVoCSummaryRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(SKUVoCSummaryRow.period_end.desc())))
        return [_voc_dict(row) for row in rows]

    def _ensure_work_item(self, *, tenant_id: str, seller_sku: str, marketplace_id: str, summary: dict[str, Any], evidence: list[dict[str, Any]], freshness: datetime) -> dict[str, Any]:
        now = datetime.now(UTC)
        code = summary["top_reason_code"]
        dedupe = sha256(f"{tenant_id}:voc:{marketplace_id}:{seller_sku}:{code}".encode()).hexdigest()[:64]
        issue = self.issue_repository.upsert(OperationalIssue(
            tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=marketplace_id,
            issue_type=f"voc_{code}", severity="high", title=summary["recommended_action"]["title"],
            summary=f"{summary['top_reason_count']} of {summary['sample_size']} observed VOC records use reason {code}.",
            status="open", dedupe_key=dedupe, confidence=Decimal("0.85"), data_freshness_at=freshness,
            evidence=tuple(evidence), missing_data=(), detected_at=now, last_detected_at=now,
            created_at=now, updated_at=now,
        ))
        route = route_voc_reason(code)
        action = self.action_repository.upsert(RecommendedAction(
            tenant_id=tenant_id, issue_id=issue.id, action_type="create_product_improvement_task",
            title=summary["recommended_action"]["title"], description="Review return, review and customer-service evidence before changing product, packaging, Listing or quality controls.",
            execution_mode="single_approval", risk_level="high", status="proposed", parameters={"reason_code": code, "seller_sku": seller_sku, **route}, evidence=tuple(evidence),
            expected_outcome=summary["recommended_action"]["expected_outcome"], confidence=Decimal("0.85"), automation_eligible=False, dedupe_key=dedupe, created_at=now, updated_at=now,
        ), canonical_issue_id=issue.id)
        item = self.work_item_repository.get_by_source(tenant_id=tenant_id, source_type="recommended_action", source_id=action.id)
        return {"work_item_id": item.id if item else None}


def _validate_source(source: str, verified: bool) -> None:
    if verified and not source.startswith("amazon_"):
        raise ValueError("verified_amazon_data requires an Amazon API source")


def _datetime(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else "0"))


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None or value == "" else _decimal(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _signal_dict(row: AmazonOperationalSignalRow) -> dict[str, Any]:
    return {"id": row.id, "tenant_id": row.tenant_id, "marketplace_id": row.marketplace_id, "signal_type": row.signal_type, "resource_id": row.resource_id, "severity": row.severity, "title": row.title, "summary": row.summary, "observed_at": row.observed_at, "collected_at": row.collected_at, "freshness_seconds": row.freshness_seconds, "verified_amazon_data": row.verified_amazon_data, "source": row.source, "raw_status_code": row.raw_status_code, "evidence_manifest": row.evidence_manifest or [], "updated_at": row.updated_at}


def _ad_snapshot_dict(row: AdvertisingDailySnapshotRow) -> dict[str, Any]:
    return {"id": row.id, "tenant_id": row.tenant_id, "marketplace_id": row.marketplace_id, "seller_sku": row.seller_sku, "entity_type": row.entity_type, "entity_id": row.entity_id, "campaign_id": row.campaign_id, "ad_group_id": row.ad_group_id, "period_start": row.period_start, "period_end": row.period_end, "currency": row.currency, "spend": row.spend, "attributed_sales": row.attributed_sales, "total_sales": row.total_sales, "budget": row.budget, "impressions": row.impressions, "clicks": row.clicks, "orders": row.orders, "metrics": row.metrics or {}, "source": row.source, "verified_amazon_data": row.verified_amazon_data, "observed_at": row.observed_at, "collected_at": row.collected_at}


def _search_term_dict(row: SearchTermPerformanceSnapshotRow) -> dict[str, Any]:
    return {"id": row.id, "tenant_id": row.tenant_id, "marketplace_id": row.marketplace_id, "seller_sku": row.seller_sku, "campaign_id": row.campaign_id, "ad_group_id": row.ad_group_id, "search_term": row.search_term, "period_start": row.period_start, "period_end": row.period_end, "currency": row.currency, "spend": row.spend, "attributed_sales": row.attributed_sales, "impressions": row.impressions, "clicks": row.clicks, "orders": row.orders, "metrics": row.metrics or {}, "source": row.source, "verified_amazon_data": row.verified_amazon_data, "observed_at": row.observed_at, "collected_at": row.collected_at}


def _diagnostic_dict(row: AdvertisingBudgetDiagnosticRow) -> dict[str, Any]:
    return {"id": row.id, "snapshot_id": row.snapshot_id, "seller_sku": row.seller_sku, "marketplace_id": row.marketplace_id, "campaign_id": row.campaign_id, "code": row.code, "severity": row.severity, "title": row.title, "details": row.details or {}, "status": row.status, "work_item_id": row.work_item_id, "created_at": row.created_at, "updated_at": row.updated_at}


def _return_dict(row: ReturnEventRow) -> dict[str, Any]:
    return {"id": row.id, "external_return_id": row.external_return_id, "marketplace_id": row.marketplace_id, "seller_sku": row.seller_sku, "reason_code": row.reason_code, "quantity": row.quantity, "observed_at": row.observed_at, "source": row.source, "verified_amazon_data": row.verified_amazon_data}


def _review_dict(row: ReviewObservationRow) -> dict[str, Any]:
    return {"id": row.id, "external_review_id": row.external_review_id, "marketplace_id": row.marketplace_id, "seller_sku": row.seller_sku, "rating": row.rating, "reason_code": row.reason_code, "observed_at": row.observed_at, "source": row.source, "verified_amazon_data": row.verified_amazon_data}


def _message_dict(row: BuyerMessageTagRow) -> dict[str, Any]:
    return {"id": row.id, "external_message_id": row.external_message_id, "marketplace_id": row.marketplace_id, "seller_sku": row.seller_sku, "tag": row.tag, "confidence": row.confidence, "observed_at": row.observed_at, "source": row.source, "verified_amazon_data": row.verified_amazon_data}


def _voc_dict(row: SKUVoCSummaryRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "marketplace_id": row.marketplace_id,
        "seller_sku": row.seller_sku,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "return_count": row.return_count,
        "review_count": row.review_count,
        "message_count": row.message_count,
        "sample_size": row.sample_size,
        "reason_counts": row.reason_counts or {},
        "top_reason_code": row.top_reason_code,
        "top_reason_count": row.top_reason_count,
        "status": row.status,
        "evidence_status": "sufficient" if row.status == "actionable" else "insufficient",
        "confidence": row.confidence,
        "recommended_action": row.recommended_action,
        "route": route_voc_reason(row.top_reason_code or "unknown"),
        "source": "deterministic_voc_aggregation",
        "verified_amazon_data": False,
        "work_item_id": row.work_item_id,
        "updated_at": row.updated_at,
    }
