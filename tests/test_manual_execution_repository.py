from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier

from domain.models import OperationalIssue
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.hitl import SQLHITLManager
from infrastructure.repositories.manual_execution import ManualExecutionRepository
from infrastructure.repositories.operations import OperationalIssueRepository


def test_concurrent_reservations_grant_one_execution_right(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'manual-execution-race.sqlite'}"
    initialize_database(database_url, default_tenant_id="default")
    session_factory = create_session_factory(database_url)
    issue_repository = OperationalIssueRepository(session_factory)
    execution_repository = ManualExecutionRepository(session_factory)
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    issue = issue_repository.upsert(
        OperationalIssue(
            id="issue-race",
            tenant_id="default",
            seller_sku="SKU-RACE",
            marketplace_id="ATVPDKIKX0DER",
            issue_type="inventory_stockout_risk",
            severity="high",
            title="Concurrent reservation",
            summary="One execution only.",
            status="approved_for_manual_execution",
            dedupe_key="issue-race",
            confidence=Decimal("1"),
            data_freshness_at=now,
            evidence=(),
            missing_data=(),
            detected_at=now,
            last_detected_at=now,
        )
    )
    hitl = SQLHITLManager(session_factory, SQLAuditLogger(session_factory))
    approval = hitl.submit_request(
        "manual_execute_recommended_action",
        {"tenant_id": "default", "resource_id": "action-race"},
        requester="operator-1",
        risk_level="high",
        reason="Concurrency test.",
    )
    hitl.approve(
        approval.request_id,
        approver="approver-1",
        reason="Approved for concurrency test.",
        tenant_id="default",
    )
    barrier = Barrier(2)

    def reserve(idempotency_key: str):
        barrier.wait(timeout=10)
        return execution_repository.reserve(
            tenant_id="default",
            issue_id=issue.id,
            approval_request_id=approval.request_id,
            recommended_action_id="action-race",
            action_hash=approval.payload["action_hash"],
            execution_contract_version="manual-execution-v1",
            idempotency_key=idempotency_key,
            action_type="create_replenishment_plan",
            approved_change={"inbound_quantity": 600},
            executed_by="operator-2",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ("race-key-1", "race-key-2")))

    assert sum(1 for _, created in results if created) == 1
    assert len({row.id for row, _ in results}) == 1
    persisted = execution_repository.get_by_approval(
        tenant_id="default",
        approval_request_id=approval.request_id,
    )
    assert persisted is not None
    assert persisted.status == "executing"
