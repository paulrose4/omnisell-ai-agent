from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from agents.prompts.reconciliation_investigation_v5 import RECONCILIATION_INVESTIGATION_PROMPT_VERSION, RECONCILIATION_INVESTIGATION_SYSTEM_PROMPT
from agents.registry import AdvisorRegistry
from application.reconciliation import AmazonReconciliationService
from application.reconciliation_agent import ReconciliationAgentService, ReconciliationEvidenceCollector
from commerce.errors import ResourceNotFound
from commerce.ports import Page
from infrastructure.database.models import ChannelConnectionRow, TenantRow
from infrastructure.database.session import Base, TenantAwareSession, create_database_engine
from infrastructure.repositories.reconciliation import ReconciliationRepository as SQLReconciliationRepository


NOW = datetime(2026, 7, 16, 13, 0, tzinfo=UTC)


class ReconciliationRepository:
    def list_runs(self, *, tenant_id: str, limit: int = 30):
        if tenant_id != "tenant-a":
            return []
        return [SimpleNamespace(id="recon-1", tenant_id=tenant_id, channel_connection_id="conn-1", status="completed", summary={"difference_count": 1}, started_at=NOW, completed_at=NOW, created_at=NOW)]

    def differences(self, *, tenant_id: str, run_id: str):
        return [SimpleNamespace(id="diff-1", tenant_id=tenant_id, run_id=run_id, resource_type="inventory", resource_id="SKU-1", difference_type="quantity_mismatch", amazon_value={"available": 5}, internal_value={"available": 4}, created_at=NOW)]


class ConnectionRepository:
    def get_connection(self, *, tenant_id: str, connection_id: str):
        return SimpleNamespace(id=connection_id, tenant_id=tenant_id, provider=SimpleNamespace(value="amazon"), selling_partner_id="masked", region="na", marketplace_ids=("ATVPDKIKX0DER",), status=SimpleNamespace(value="active"), version=2, updated_at=NOW)


class TaskRepository:
    def list(self, *, tenant_id: str, limit: int = 100):
        return [SimpleNamespace(id="task-1", action="amazon.sync.inventory", status="failed", attempts=2, error_code="Timeout", created_at=NOW, updated_at=NOW, result=None)]


class EventRepository:
    def list_inbox_failures(self, *, tenant_id: str):
        return [SimpleNamespace(id="event-1", provider="amazon", external_event_id="evt-1", event_type="inventory.updated", status="dead_letter", attempts=5, error_code="MappingError", received_at=NOW)]


class AuditLogger:
    def query(self, *, tenant_id: str, limit: int):
        return [{"timestamp": NOW.isoformat(), "event_type": "operation", "action": "amazon_sync_failed", "status": "failure", "risk_level": "medium", "details": {"connection_id": "conn-1"}}]


class Workflow:
    async def run_advisory(self, **kwargs):
        self.kwargs = kwargs
        return {"run_id": "run-1", "status": "succeeded", "analysis": {"agent_type": kwargs["advisor"].agent_type}}


def test_reconciliation_collector_builds_read_only_investigation_manifest() -> None:
    collector = ReconciliationEvidenceCollector(
        reconciliation_repository=ReconciliationRepository(), connection_repository=ConnectionRepository(),
        task_repository=TaskRepository(), event_repository=EventRepository(), audit_logger=AuditLogger(),
    )

    bundle = asyncio.run(collector.collect(tenant_id="tenant-a", request={"reconciliation_run_id": "recon-1"}))

    ids = {item.evidence_id for item in bundle.evidence}
    assert {"recon-1", "diff-1", "connection:conn-1", "task:task-1", "inbox:event-1", "audit:0"} <= ids
    assert not any(item.verified_amazon_data for item in bundle.evidence)
    assert bundle.by_id()["diff-1"].metadata["amazon_value"] == {"available": 5}
    assert bundle.by_id()["diff-1"].metadata["internal_value"] == {"available": 4}


def test_reconciliation_collector_hides_cross_tenant_run() -> None:
    collector = ReconciliationEvidenceCollector(reconciliation_repository=ReconciliationRepository())
    with pytest.raises(LookupError, match="not found"):
        asyncio.run(collector.collect(tenant_id="tenant-b", request={"reconciliation_run_id": "recon-1"}))


def test_reconciliation_records_missing_amazon_listing_instead_of_failing_run() -> None:
    class Adapter:
        async def list_orders(self, **_):
            return Page(items=[])

        async def list_inventory(self, **_):
            return Page(items=[])

        async def get_listing(self, **_):
            raise ResourceNotFound("Amazon resource not found")

    class CommerceRepository:
        def order_ids(self, **_):
            return set()

        def inventory_snapshot(self, **_):
            return {}

        def listing_snapshot(self, **_):
            return {"SKU-MISSING": {"status": "buyable", "title": "Internal title", "external_product_id": "ASIN-1"}}

    class RunRepository:
        def save(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(id="recon-1", status=kwargs["status"], summary=kwargs["summary"])

    class IssueRepository:
        def upsert(self, issue):
            self.issue = issue

    runs = RunRepository()
    issues = IssueRepository()
    service = AmazonReconciliationService(
        adapter=Adapter(),
        connection=SimpleNamespace(
            id="conn-1",
            tenant_id="tenant-a",
            marketplace_ids=("ATVPDKIKX0DER",),
        ),
        commerce_repository=CommerceRepository(),
        reconciliation_repository=runs,
        issue_repository=issues,
    )

    result = asyncio.run(service.run())

    assert result.status == "requires_manual_review"
    assert runs.kwargs["differences"] == [{
        "resource_type": "listing",
        "resource_id": "SKU-MISSING",
        "difference_type": "missing_amazon",
        "amazon_value": None,
        "internal_value": {"status": "buyable", "title": "Internal title", "external_product_id": "ASIN-1"},
    }]
    assert issues.issue.evidence == ({"run_id": "recon-1", "difference_count": 1},)


def test_reconciliation_repository_persists_parent_before_differences(tmp_path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'reconciliation.sqlite'}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=TenantAwareSession, expire_on_commit=False)
    with factory.begin() as session:
        session.add(TenantRow(id="tenant-a", name="Tenant A", slug="tenant-a"))
        session.flush()
        session.add(ChannelConnectionRow(
            id="conn-1",
            tenant_id="tenant-a",
            provider="amazon",
            selling_partner_id="seller-1",
            region="na",
            marketplace_ids=["ATVPDKIKX0DER"],
            secret_ref="secret://amazon",
            status="active",
        ))

    repository = SQLReconciliationRepository(factory)
    row = repository.save(
        tenant_id="tenant-a",
        connection_id="conn-1",
        status="requires_manual_review",
        summary={"difference_count": 1},
        differences=[{
            "resource_type": "listing",
            "resource_id": "SKU-1",
            "difference_type": "missing_amazon",
            "amazon_value": None,
            "internal_value": {"status": "buyable"},
        }],
        started_at=NOW,
        completed_at=NOW,
    )

    assert row.status == "requires_manual_review"
    assert len(repository.differences(tenant_id="tenant-a", run_id=row.id)) == 1


def test_reconciliation_service_allows_only_read_checks_and_task_drafts() -> None:
    workflow = Workflow()
    advisor = SimpleNamespace(agent_type="reconciliation_investigator", prompt_version="reconciliation-investigation-v1")
    service = ReconciliationAgentService(
        workflow=workflow, registry=AdvisorRegistry({"reconciliation_investigation": advisor}), evidence_collector=object()
    )

    asyncio.run(service.investigate(tenant_id="tenant-a", actor_id="operator-1", correlation_id="corr-1", reconciliation_run_id="recon-1"))

    assert workflow.kwargs["request"]["allowed_capabilities"] == ["read_evidence", "draft_internal_investigation_task"]


def test_reconciliation_prompt_never_assumes_amazon_or_internal_truth() -> None:
    prompt = RECONCILIATION_INVESTIGATION_SYSTEM_PROMPT.lower()
    assert "do not assume amazon is correct" in prompt
    assert "do not assume internal data is correct" in prompt
    assert "hypothesis" in prompt
    assert "read-only" in prompt
    assert "do not overwrite" in prompt
    assert RECONCILIATION_INVESTIGATION_PROMPT_VERSION == "reconciliation-investigation-v5"
    assert "runtime adds identity" in prompt
    assert "field, reason, required_for, blocking, and suggested_source" in prompt
    assert "resource_id must equal an existing resource_id" in prompt
    assert "never include target_quantity" in prompt
    assert "numeric target" in prompt
