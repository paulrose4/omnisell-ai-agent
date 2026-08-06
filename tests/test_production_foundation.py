from __future__ import annotations

import asyncio
import time
import warnings
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from cryptography.fernet import Fernet

from api.config import Settings
from api.oauth_state import OAuthStateManager
from commerce.adapters.amazon.adapter import AmazonSPAPIAdapter, AmazonSPAPIConfig
from commerce.adapters.amazon.rate_limit import OperationRateLimiter
from commerce.adapters.amazon.secrets import EncryptedDatabaseSecretStore, InMemorySecretStore
from commerce.errors import RateLimited, ValidationFailed
from domain.models import Money, OrderStatus
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.tasks import DurableTaskRepository, EventRepository
from safety.policy_engine import PolicyDecision, PolicyEngine, canonical_action_hash


def test_money_uses_decimal_and_normalizes_currency() -> None:
    money = Money(Decimal("12.3400"), "usd")
    assert money.amount == Decimal("12.3400")
    assert money.currency == "USD"
    with pytest.raises(ValueError):
        Money(Decimal("1"), "US")


@pytest.mark.parametrize(
    "action",
    ["refund", "approve_refund", "payment_reversal", "reverse_payment", "cancel_order", "autonomous_cancellation"],
)
def test_policy_permanently_blocks_agent_financial_reversal_and_cancellation_actions(action: str) -> None:
    engine = PolicyEngine()
    assert engine.evaluate(action=action, arguments={}).decision == PolicyDecision.BLOCK


def test_action_hash_binds_arguments() -> None:
    first = canonical_action_hash(
        action="update_price", tenant_id="a", resource_id="sku", arguments={"new_price": "10.00"}
    )
    second = canonical_action_hash(
        action="update_price", tenant_id="a", resource_id="sku", arguments={"new_price": "11.00"}
    )
    assert first != second


def test_policy_allows_only_small_high_confidence_price_change() -> None:
    engine = PolicyEngine()
    complete_evidence = {
        "current_price": "100",
        "new_price": "102",
        "currency": "USD",
        "break_even_price": "80",
        "projected_margin": "0.20",
        "minimum_margin_rate": "0.10",
        "inventory_stockout_risk": "healthy",
        "data_complete": True,
    }
    allowed = engine.evaluate(action="update_price", arguments=complete_evidence, confidence=0.99)
    approval = engine.evaluate(
        action="update_price",
        arguments={**complete_evidence, "new_price": "90"},
        confidence=0.99,
    )
    assert allowed.decision == PolicyDecision.ALLOW
    assert approval.decision == PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    "arguments",
    [
        {"current_price": "100", "new_price": "102"},
        {
            "current_price": "100",
            "new_price": "NaN",
            "currency": "USD",
            "break_even_price": "80",
            "projected_margin": "0.20",
            "minimum_margin_rate": "0.10",
            "inventory_stockout_risk": "healthy",
            "data_complete": True,
        },
        {
            "current_price": "100",
            "new_price": "102",
            "currency": "US",
            "break_even_price": "80",
            "projected_margin": "0.20",
            "minimum_margin_rate": "0.10",
            "inventory_stockout_risk": "healthy",
            "data_complete": True,
        },
    ],
)
def test_policy_never_defaults_missing_or_invalid_price_evidence_to_zero(arguments: dict) -> None:
    result = PolicyEngine().evaluate(action="update_price", arguments=arguments, confidence=0.99)
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert "evidence" in result.reason.lower()


def test_hitl_price_risk_uses_exact_decimal_arithmetic() -> None:
    from safety.hitl import HITLManager, RiskLevel

    manager = HITLManager()

    assert manager._extract_price_change({"current_price": "100.00", "recommended_price": "120.00"}) == Decimal("0.20")
    assert (
        manager.assess_risk("update_price", {"current_price": "100.00", "recommended_price": "120.00"})[0]
        == RiskLevel.HIGH
    )
    assert (
        manager.assess_risk("update_price", {"current_price": "100.00", "recommended_price": "120.0001"})[0]
        == RiskLevel.CRITICAL
    )


def test_oauth_state_is_tenant_bound_and_single_use() -> None:
    manager = OAuthStateManager("secret")
    value = manager.issue("tenant-a")
    assert manager.consume(value).tenant_id == "tenant-a"
    with pytest.raises(ValueError):
        manager.consume(value)


def test_durable_tasks_and_events_are_tenant_scoped(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    factory = create_session_factory(url)
    from infrastructure.database.models import TenantRow

    with factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    tasks = DurableTaskRepository(factory)
    row = tasks.create(
        tenant_id="tenant-a",
        actor_id="u",
        queue_name="text",
        action="customer.ask",
        payload={},
        idempotency_key="same",
        correlation_id="c",
    )
    duplicate = tasks.create(
        tenant_id="tenant-a",
        actor_id="u",
        queue_name="text",
        action="customer.ask",
        payload={},
        idempotency_key="same",
        correlation_id="c",
    )
    assert row.id == duplicate.id
    assert tasks.get(tenant_id="tenant-b", task_id=row.id) is None
    events = EventRepository(factory)
    assert (
        events.accept_inbox(
            tenant_id="tenant-a", provider="amazon", external_event_id="e1", event_type="ORDER", payload={}
        )
        is True
    )
    assert (
        events.accept_inbox(
            tenant_id="tenant-a", provider="amazon", external_event_id="e1", event_type="ORDER", payload={}
        )
        is False
    )
    assert (
        events.accept_inbox(
            tenant_id="tenant-b", provider="amazon", external_event_id="e1", event_type="ORDER", payload={}
        )
        is True
    )


def test_encrypted_secret_store_never_persists_plaintext(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'secrets.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    store = EncryptedDatabaseSecretStore(factory, Fernet.generate_key().decode())
    ref = store.put(tenant_id="default", value="refresh-token-secret")
    assert store.get(tenant_id="default", secret_ref=ref) == "refresh-token-secret"
    assert "refresh-token-secret" not in (tmp_path / "secrets.sqlite").read_bytes().decode("latin-1")


def _adapter(handler) -> AmazonSPAPIAdapter:
    store = InMemorySecretStore()
    ref = store.put(tenant_id="tenant", value="refresh")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = AmazonSPAPIAdapter(
        config=AmazonSPAPIConfig(
            lwa_client_id="id",
            lwa_client_secret="secret",
            application_id="app",
            redirect_uri="https://callback",
            max_retries=0,
        ),
        tenant_id="tenant",
        selling_partner_id="seller",
        marketplace_ids=["ATVPDKIKX0DER"],
        refresh_token_ref=ref,
        secret_store=store,
        client=client,
    )
    adapter._access_token = "access"
    adapter._access_token_expires_at = 10**12
    return adapter


def test_amazon_unknown_order_status_is_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "payload": {
                    "AmazonOrderId": "O1",
                    "MarketplaceId": "ATVPDKIKX0DER",
                    "OrderStatus": "FutureStatus",
                    "FulfillmentChannel": "AFN",
                }
            },
        )

    adapter = _adapter(handler)

    async def scenario() -> None:
        order = await adapter.get_order(external_order_id="O1")
        assert order.status == OrderStatus.UNKNOWN
        assert order.platform_status == "FutureStatus"
        await adapter.close()

    asyncio.run(scenario())


def test_amazon_429_maps_to_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"}, json={"errors": [{"code": "QuotaExceeded"}]})

    adapter = _adapter(handler)

    async def scenario() -> None:
        with pytest.raises(RateLimited) as exc:
            await adapter.get_order(external_order_id="O1")
        assert exc.value.retry_after_seconds == 1
        await adapter.close()

    asyncio.run(scenario())


def test_mock_rate_limit_multiplier_accelerates_local_replay_without_disabling_http_429() -> None:
    limiter = OperationRateLimiter(rate_multiplier=1000)

    async def scenario() -> float:
        started = time.perf_counter()
        key = ("seller", "app", "getOrders", "na")
        await limiter.acquire(key, rate=0.0167, burst=1)
        await limiter.acquire(key, rate=0.0167, burst=1)
        return time.perf_counter() - started

    assert asyncio.run(scenario()) < 0.5


def test_mock_rate_limit_multiplier_is_rejected_outside_isolated_mock() -> None:
    with pytest.raises(ValueError, match="MOCK_AMAZON_RATE_LIMIT_MULTIPLIER"):
        Settings(
            environment="development",
            mock_drill_enabled=False,
            amazon_sp_api_endpoint="https://sellingpartnerapi-na.amazon.com",
            mock_amazon_rate_limit_multiplier=1000,
        )


def test_amazon_mvp_blocks_direct_status_and_fba_writes() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json={}))

    async def scenario() -> None:
        with pytest.raises(ValidationFailed):
            await adapter.update_order_status(external_order_id="O1", status="shipped", idempotency_key="x")
        with pytest.raises(ValidationFailed):
            await adapter.adjust_inventory(
                seller_sku="SKU", marketplace_id="ATVPDKIKX0DER", quantity=2, idempotency_key="x"
            )
        await adapter.close()

    asyncio.run(scenario())


def test_sql_hitl_prevents_self_approval_and_requires_two_people(tmp_path) -> None:
    from sqlalchemy import select

    from infrastructure.database.models import ApprovalDecisionRow
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager

    url = f"sqlite:///{tmp_path / 'hitl.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    manager = SQLHITLManager(factory, SQLAuditLogger(factory))
    request = manager.submit_request(
        "delete_listing",
        {"tenant_id": "default", "resource_id": "SKU-1", "requires_second_verification": True},
        requester="requester",
    )
    from safety.hitl import ApprovalRejectedError

    with pytest.raises(ApprovalRejectedError):
        manager.approve(request.request_id, approver="requester", tenant_id="default")

    first = manager.approve(
        request.request_id, approver="approver", reason="First independent review.", tenant_id="default"
    )
    assert first.status.value == "awaiting_second_approval"
    assert first.first_approver_id == "approver"
    assert first.decided_at is None

    with pytest.raises(ApprovalRejectedError):
        manager.approve(request.request_id, approver="approver", tenant_id="default")
    with pytest.raises(ApprovalRejectedError):
        manager.approve(request.request_id, approver="requester", tenant_id="default")

    approved = manager.approve(
        request.request_id, approver="verifier", reason="Second independent review.", tenant_id="default"
    )
    assert approved.status.value == "approved"
    assert approved.first_approver_id == "approver"
    assert approved.second_approver_id == "verifier"
    assert approved.payload["action_hash"]
    with factory() as session:
        decisions = list(
            session.scalars(
                select(ApprovalDecisionRow)
                .where(ApprovalDecisionRow.approval_request_id == request.request_id)
                .order_by(ApprovalDecisionRow.sequence)
            )
        )
    assert [(item.sequence, item.stage, item.actor_id) for item in decisions] == [
        (1, "first_approval", "approver"),
        (2, "second_approval", "verifier"),
    ]


def test_sql_hitl_never_persists_permanently_prohibited_approval(tmp_path) -> None:
    from sqlalchemy import func, select

    from infrastructure.database.models import ApprovalRequestRow
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager
    from safety.hitl import ApprovalRejectedError

    url = f"sqlite:///{tmp_path / 'hitl-prohibited.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    manager = SQLHITLManager(factory, SQLAuditLogger(factory))

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.submit_request(
            "manual_review",
            {"tenant_id": "tenant-a", "resource_id": "O1001", "action_type": "payment_reversal"},
            requester="operator",
            risk_level="critical",
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalRequestRow)) == 0


def test_sql_hitl_rejects_legacy_pending_prohibited_action(tmp_path) -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from infrastructure.database.models import ApprovalRequestRow
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager
    from safety.hitl import ApprovalRejectedError, ApprovalStatus

    url = f"sqlite:///{tmp_path / 'hitl-legacy-prohibited.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    with factory.begin() as session:
        session.add(
            ApprovalRequestRow(
                id="legacy-cancel",
                tenant_id="tenant-a",
                actor_id="legacy-requester",
                action="cancel_order",
                action_hash="a" * 64,
                policy_decision="require_approval",
                policy_version="policy-engine-v1",
                policy_reason="Legacy request requires independent approval.",
                required_approval_count=2,
                risk_level="critical",
                before_snapshot={},
                proposed_changes={"tenant_id": "tenant-a", "order_id": "O1001"},
                status="pending",
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    manager = SQLHITLManager(factory, SQLAuditLogger(factory))

    with pytest.raises(ApprovalRejectedError, match="permanently prohibited"):
        manager.approve(
            "legacy-cancel",
            approver="lead",
            tenant_id="tenant-a",
        )

    with factory() as session:
        row = session.scalar(select(ApprovalRequestRow).where(ApprovalRequestRow.id == "legacy-cancel"))
        assert row is not None
        assert row.status == ApprovalStatus.REJECTED.value


def test_sql_hitl_lists_newest_pending_request_first(tmp_path) -> None:
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager

    url = f"sqlite:///{tmp_path / 'hitl-order.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    manager = SQLHITLManager(factory, SQLAuditLogger(factory))
    older = manager.submit_request(
        "request_manual_execution",
        {"tenant_id": "default", "resource_id": "SKU-OLD", "seller_sku": "SKU-OLD"},
        requester="operator",
        risk_level="high",
    )
    newer = manager.submit_request(
        "manual_execute_recommended_action",
        {"tenant_id": "default", "resource_id": "SKU-NEW", "seller_sku": "SKU-NEW"},
        requester="operator",
        risk_level="high",
    )

    pending = manager.get_pending(tenant_id="default")

    assert [item.request_id for item in pending] == [newer.request_id, older.request_id]


def test_amazon_routes_are_registered() -> None:
    from api.main import create_app

    app = create_app()
    paths = set(app.openapi()["paths"])
    assert hasattr(app.state, "secret_store")
    assert hasattr(app.state, "amazon_oauth_state")
    assert "/api/channels/amazon/authorize" in paths
    assert "/api/channels/amazon/callback" in paths
    assert "/api/channels/amazon/{connection_id}/orders" in paths


def test_policy_block_emits_structured_warning(caplog) -> None:
    caplog.set_level("WARNING", logger="omnisell.observability")
    result = PolicyEngine().evaluate(action="refund_order", arguments={"order_id": "O-1"}, confidence=0.88)
    assert result.decision == PolicyDecision.BLOCK
    assert any('"event": "policy_action_blocked"' in record.message for record in caplog.records)
    assert any('"arguments_sha256"' in record.message for record in caplog.records)


def test_persistent_workflow_records_expected_transitions() -> None:
    from domain.models import WorkflowStatus
    from workflows.engine import PersistentAgentWorkflow, WorkflowPlan

    class Store:
        def __init__(self) -> None:
            self.transitions = []

        def create_run(self, **kwargs) -> str:
            return "run-1"

        def transition(self, run_id, status, state, *, tenant_id) -> None:
            self.transitions.append(status)

    class Planner:
        async def plan(self, **kwargs):
            return WorkflowPlan(summary="Read only", actions=[])

    class Executor:
        async def execute(self, action, **kwargs):
            return {}

    store = Store()
    workflow = PersistentAgentWorkflow(store=store, planner=Planner(), executor=Executor())
    result = asyncio.run(
        workflow.run(tenant_id="tenant", actor_id="user", workflow_type="test", correlation_id="corr", request={})
    )
    assert result["status"] == "succeeded"
    assert store.transitions == [
        WorkflowStatus.VALIDATED,
        WorkflowStatus.PLANNED,
        WorkflowStatus.POLICY_EVALUATED,
        WorkflowStatus.VERIFYING,
        WorkflowStatus.SUCCEEDED,
    ]


def test_app_startup_uses_persistent_hitl_and_audit(tmp_path) -> None:
    from api.config import Settings
    from api.main import create_app
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager

    url = f"sqlite:///{tmp_path / 'persistent-runtime.sqlite'}"
    app = create_app(Settings(database_url=url, auto_create_schema=True, api_key="test-key", auth_mode="api_key"))

    async def scenario() -> None:
        for handler in app.router.on_startup:
            result = handler()
            if hasattr(result, "__await__"):
                await result
        try:
            assert isinstance(app.state.audit_logger, SQLAuditLogger)
            assert isinstance(app.state.hitl_manager, SQLHITLManager)
            approval = app.state.hitl_manager.submit_request(
                "publish_listing",
                {"tenant_id": "default", "resource_id": "SKU-1", "action_hash": "a" * 64},
                requester="operator-1",
                risk_level="high",
            )
            reloaded = SQLHITLManager(app.state.session_factory, SQLAuditLogger(app.state.session_factory)).get_request(
                approval.request_id, tenant_id="default"
            )
            assert reloaded is not None
            assert reloaded.payload["action_hash"] == "a" * 64
        finally:
            for handler in app.router.on_shutdown:
                result = handler()
                if hasattr(result, "__await__"):
                    await result

    asyncio.run(scenario())


def test_amazon_write_gate_blocks_pilot_listing_updates() -> None:
    from safety.amazon_write_gate import AmazonWriteBlocked, AmazonWriteGate

    gate = AmazonWriteGate(global_enabled=False, tenant_enabled=False, pilot_read_only=True)
    with pytest.raises(AmazonWriteBlocked) as exc:
        gate.require_write(action="update_listing", tenant_id="tenant-a", resource_id="SKU-1")
    assert exc.value.code == "blocked_read_only_pilot"


def test_amazon_read_sync_persists_idempotent_business_data(tmp_path) -> None:
    from application.amazon_sync import AmazonReadSyncService
    from commerce.ports import Page
    from domain.models import (
        ChannelConnection,
        ChannelProvider,
        ConnectionStatus,
        FulfillmentMode,
        InventoryPosition,
        Listing,
        Order,
    )
    from infrastructure.database.models import (
        ChannelConnectionRow,
        InventoryPositionRow,
        ListingRow,
        OrderRow,
        SyncCursorRow,
    )
    from infrastructure.repositories.commerce_sync import CommerceSyncRepository, SyncCursorRepository

    url = f"sqlite:///{tmp_path / 'sync.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    factory = create_session_factory(url)
    connection = ChannelConnection(
        id="connection-a",
        tenant_id="tenant-a",
        provider=ChannelProvider.AMAZON,
        selling_partner_id="seller-a",
        region="na",
        marketplace_ids=("ATVPDKIKX0DER",),
        secret_ref="secret",
        status=ConnectionStatus.ACTIVE,
    )
    with factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id=connection.id,
                tenant_id=connection.tenant_id,
                provider="amazon",
                selling_partner_id=connection.selling_partner_id,
                region="na",
                marketplace_ids=list(connection.marketplace_ids),
                secret_ref="secret",
                status="active",
                version=1,
            )
        )

    class Adapter:
        async def list_orders(self, **kwargs):
            return Page(
                items=[
                    Order(
                        tenant_id="tenant-a",
                        channel_connection_id="",
                        external_order_id="ORDER-1",
                        marketplace_id="ATVPDKIKX0DER",
                        status=OrderStatus.SHIPPED,
                        fulfillment_mode=FulfillmentMode.FBA,
                    )
                ]
            )

        async def list_inventory(self, **kwargs):
            return Page(
                items=[
                    InventoryPosition(
                        tenant_id="tenant-a",
                        channel_connection_id="",
                        seller_sku="SKU-1",
                        marketplace_id="ATVPDKIKX0DER",
                        fulfillment_mode=FulfillmentMode.FBA,
                        available=8,
                    )
                ]
            )

        async def get_listing(self, **kwargs):
            return Listing(
                tenant_id="tenant-a",
                channel_connection_id="",
                variant_id="",
                marketplace_id="ATVPDKIKX0DER",
                seller_sku="SKU-1",
                title="Pilot item",
                status="active",
            )

    service = AmazonReadSyncService(
        adapter=Adapter(),
        connection=connection,
        commerce_repository=CommerceSyncRepository(factory),
        cursor_repository=SyncCursorRepository(factory),
    )
    asyncio.run(service.sync())
    asyncio.run(service.sync())
    with factory() as session:
        assert len(list(session.query(OrderRow).all())) == 1
        assert len(list(session.query(InventoryPositionRow).all())) == 1
        assert len(list(session.query(ListingRow).all())) == 1
        cursors = list(session.query(SyncCursorRow).all())
        assert {item.resource_type for item in cursors} == {"orders", "inventory", "listings"}
        assert all(item.status == "succeeded" for item in cursors)


def test_dead_letter_replay_and_close_are_persistent(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'dead-letter.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    repository = DurableTaskRepository(factory)
    original = repository.create(
        tenant_id="default",
        actor_id="worker",
        queue_name="text",
        action="customer.ask",
        payload={"question": "x"},
        idempotency_key="original",
        correlation_id="corr",
    )
    repository.update_status(
        tenant_id="default",
        task_id=original.id,
        status="dead_letter",
        error_code="TimeoutError",
        error_message="timeout",
    )
    replay = repository.replay(tenant_id="default", task_id=original.id, actor_id="admin")
    assert replay.replayed_from_task_id == original.id
    assert repository.get(tenant_id="default", task_id=original.id).replayed_to_task_id == replay.id
    repository.update_status(
        tenant_id="default", task_id=replay.id, status="dead_letter", error_code="TimeoutError", error_message="timeout"
    )
    closed = repository.close(tenant_id="default", task_id=replay.id, actor_id="admin", reason="handled outside system")
    assert closed.status == "closed" and closed.closed_by == "admin"


def test_inbox_claim_retry_and_dead_letter_state_machine(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'inbox-state.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    events = EventRepository(factory)
    assert events.accept_inbox(
        tenant_id="default", provider="amazon", external_event_id="event-1", event_type="UNKNOWN", payload={}
    )
    claimed = events.claim_pending(tenant_id="default", limit=10)
    assert len(claimed) == 1 and claimed[0].status == "processing" and claimed[0].attempts == 1
    events.mark_retry(tenant_id="default", event_id=claimed[0].id, error=RuntimeError("temporary"), max_attempts=1)
    failures = events.list_inbox_failures(tenant_id="default")
    assert len(failures) == 1 and failures[0].status == "dead_letter"


def test_sql_audit_and_hitl_queries_are_tenant_scoped(tmp_path) -> None:
    from infrastructure.database.models import TenantRow
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager

    url = f"sqlite:///{tmp_path / 'tenant-operations.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    factory = create_session_factory(url)
    with factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="Tenant B", slug="tenant-b"))

    audit = SQLAuditLogger(factory)
    audit.log_operation("sync", "success", {"tenant_id": "tenant-a"})
    audit.log_operation("sync", "success", {"tenant_id": "tenant-b"})
    assert len(audit.query(tenant_id="tenant-a")) == 1
    assert len(audit.query(tenant_id="tenant-b")) == 1

    hitl = SQLHITLManager(factory, audit)
    hitl.submit_request(
        "update_listing", {"tenant_id": "tenant-a", "resource_id": "A"}, requester="a", risk_level="high"
    )
    hitl.submit_request(
        "update_listing", {"tenant_id": "tenant-b", "resource_id": "B"}, requester="b", risk_level="high"
    )
    assert [item.payload["tenant_id"] for item in hitl.get_pending(tenant_id="tenant-a")] == ["tenant-a"]
    assert [item.payload["tenant_id"] for item in hitl.get_pending(tenant_id="tenant-b")] == ["tenant-b"]


def test_inbox_claims_only_one_tenant(tmp_path) -> None:
    from infrastructure.database.models import TenantRow

    url = f"sqlite:///{tmp_path / 'tenant-inbox.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    factory = create_session_factory(url)
    with factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="Tenant B", slug="tenant-b"))
    events = EventRepository(factory)
    events.accept_inbox(
        tenant_id="tenant-a", provider="amazon", external_event_id="a", event_type="ORDER_CHANGE", payload={}
    )
    events.accept_inbox(
        tenant_id="tenant-b", provider="amazon", external_event_id="b", event_type="ORDER_CHANGE", payload={}
    )
    claimed = events.claim_pending(tenant_id="tenant-a", limit=10)
    assert [item.tenant_id for item in claimed] == ["tenant-a"]


def test_sql_workflow_store_rejects_illegal_transition_and_keeps_history(tmp_path) -> None:
    from domain.models import WorkflowStatus
    from infrastructure.repositories.workflows import SQLWorkflowStore

    url = f"sqlite:///{tmp_path / 'workflow.sqlite'}"
    initialize_database(url, default_tenant_id="tenant-a")
    store = SQLWorkflowStore(create_session_factory(url))
    run_id = store.create_run(
        tenant_id="tenant-a",
        actor_id="operator",
        workflow_type="ops",
        correlation_id="corr",
        request={"task": "review"},
    )
    with pytest.raises(ValueError):
        store.transition(tenant_id="tenant-a", run_id=run_id, status=WorkflowStatus.SUCCEEDED, state={})
    store.transition(tenant_id="tenant-a", run_id=run_id, status=WorkflowStatus.VALIDATED, state={"validated": True})
    run = store.get_run(tenant_id="tenant-a", run_id=run_id)
    assert run is not None
    assert run.status == WorkflowStatus.VALIDATED.value
    assert [item["status"] for item in run.state["history"]] == ["received", "validated"]


def test_amazon_adapter_blocks_every_platform_write_in_read_only_pilot() -> None:
    from safety.amazon_write_gate import AmazonWriteBlocked, AmazonWriteGate

    store = InMemorySecretStore()
    refresh_token_ref = store.put(tenant_id="tenant-a", value="refresh-token")
    adapter = AmazonSPAPIAdapter(
        config=AmazonSPAPIConfig(
            lwa_client_id="client",
            lwa_client_secret="secret",
            application_id="app",
            redirect_uri="https://example.test/callback",
        ),
        tenant_id="tenant-a",
        selling_partner_id="seller",
        marketplace_ids=["ATVPDKIKX0DER"],
        refresh_token_ref=refresh_token_ref,
        secret_store=store,
        write_gate=AmazonWriteGate(global_enabled=False, tenant_enabled=False, pilot_read_only=True),
    )
    with pytest.raises(AmazonWriteBlocked) as exc:
        asyncio.run(adapter.create_subscription(notification_type="ORDER_CHANGE", destination_id="destination"))
    assert exc.value.code == "blocked_read_only_pilot"


def test_postgres_app_role_password_sql_is_literal_and_escaped():
    from scripts.configure_postgres_app_role import _role_password_statement

    statement = _role_password_statement("CREATE", "tenant_role", "p'assword")

    assert statement.as_string() == "CREATE ROLE \"tenant_role\" LOGIN PASSWORD 'p''assword' NOBYPASSRLS"


def test_postgres_app_role_script_supports_direct_repository_execution():
    from pathlib import Path

    script = Path("scripts/configure_postgres_app_role.py").read_text(encoding="utf-8")
    path_index = script.index("sys.path.insert")
    api_import_index = script.index("from api.config import get_settings")

    assert path_index < api_import_index


def test_runtime_container_has_writable_state_paths():
    from pathlib import Path

    dockerfile = Path("api/Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")

    assert "mkdir -p /app/tmp" in dockerfile
    assert "--schedule=/tmp/celerybeat-schedule" in compose


def test_approval_bff_never_self_reports_approver_identity() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for decision in ("approve", "reject"):
        source = (root / "frontend" / "app" / "api" / "approvals" / "[requestId]" / decision / "route.ts").read_text(
            encoding="utf-8"
        )
        assert 'approver: "admin-console"' not in source
        assert "body: JSON.stringify(payload)" in source


def test_customer_case_and_work_item_workbenches_preserve_deep_link_context() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workbench = (root / "frontend" / "components" / "operations" / "operational-work-items-workbench.tsx").read_text(
        encoding="utf-8"
    )
    case_workbench = (root / "frontend" / "components" / "customer-service" / "customer-case-workbench.tsx").read_text(
        encoding="utf-8"
    )

    assert 'get("work_item")' in workbench
    assert "/api/work-items/${encodeURIComponent(deepLinkedWorkItemId!)}" in workbench
    assert 'const [view, setView] = useState<View>("today");' in workbench
    assert "const [deepLinkedWorkItemId, setDeepLinkedWorkItemId] = useState<string | null>(null);" in workbench
    assert 'setDeepLinkedWorkItemId(params.get("work_item"));' in workbench
    assert 'get("case")' in case_workbench
    assert "case=${encodeURIComponent(caseContext.data.case_ids[0])}" in workbench


def test_interview_release_profile_limits_navigation_without_removing_extensions() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shell = (root / "frontend" / "components" / "app-shell.tsx").read_text(encoding="utf-8")
    layout = (root / "frontend" / "app" / "layout.tsx").read_text(encoding="utf-8")
    dockerfile = (root / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert 'normalizedReleaseProfile === "golden_demo"' in shell
    assert 'href: "/dashboard"' in shell
    assert 'href: "/issues"' in shell
    assert 'href: "/approvals"' in shell
    assert 'href: "/mock-seller-central"' in shell
    assert 'href: "/agents"' in shell
    assert 'href: "/customer-cases"' in shell
    assert 'process.env.NEXT_PUBLIC_RELEASE_PROFILE ?? "full_development"' in layout
    assert "ARG NEXT_PUBLIC_RELEASE_PROFILE=full_development" in dockerfile
    middleware = (root / "frontend" / "middleware.ts").read_text(encoding="utf-8")
    profile_routes = (root / "frontend" / "lib" / "api" / "release-profile-routes.ts").read_text(encoding="utf-8")
    assert "normalizeFrontendReleaseProfile(releaseProfile)" in shell
    assert 'normalizedReleaseProfile !== "read_only_pilot"' in shell
    assert "normalizeFrontendReleaseProfile(configuredProfile)" in middleware
    assert "isPageRouteAllowed(releaseProfile, pathname)" in middleware
    assert "isBffRouteAllowed(releaseProfile, request.method, pathname)" in middleware
    assert "export function isPageRouteAllowed" in profile_routes
    assert '"/customer-cases"' not in middleware


def test_mock_pilot_waits_for_api_and_uses_isolated_postgres_port():
    from pathlib import Path

    launcher = Path("scripts/start_mock_pilot.ps1").read_text(encoding="utf-8")
    smoke = Path("scripts/run_local_e2e.py").read_text(encoding="utf-8")

    assert "Invoke-WebRequest" in launcher
    assert "localhost:55432/omnisell" in smoke
    assert "uuid4" in smoke


def test_channel_connection_save_is_idempotent_by_seller_region(tmp_path):
    from domain.models import ChannelConnection, ChannelProvider, ConnectionStatus
    from infrastructure.database.bootstrap import initialize_database
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.tenants import TenantRepository

    database_url = f"sqlite:///{tmp_path / 'connections.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    repository = TenantRepository(create_session_factory(database_url))

    first = repository.save_connection(
        ChannelConnection(
            tenant_id="tenant-a",
            provider=ChannelProvider.AMAZON,
            selling_partner_id="seller-a",
            region="na",
            marketplace_ids=("ATVPDKIKX0DER",),
            secret_ref="secret://one",
            status=ConnectionStatus.ACTIVE,
        )
    )
    second = repository.save_connection(
        ChannelConnection(
            tenant_id="tenant-a",
            provider=ChannelProvider.AMAZON,
            selling_partner_id="seller-a",
            region="na",
            marketplace_ids=("ATVPDKIKX0DER",),
            secret_ref="secret://two",
            status=ConnectionStatus.ACTIVE,
        )
    )

    assert second.id == first.id
    assert second.secret_ref == "secret://two"
    assert len(repository.list_connections(tenant_id="tenant-a")) == 1


def test_celery_sync_tasks_route_to_consumed_queue():
    from application.celery_app import celery_app

    routes = celery_app.conf.task_routes
    for task_name in (
        "omnisell.amazon_sync",
        "omnisell.dispatch_amazon_sync",
        "omnisell.consume_amazon_notifications",
        "omnisell.amazon_reconciliation",
        "omnisell.dispatch_amazon_reconciliation",
    ):
        assert routes[task_name]["queue"] == "sync"


def test_dead_letter_action_replay_preserves_tenant_argument():
    from pathlib import Path

    routes = Path("api/routes/task_routes.py").read_text(encoding="utf-8")
    assert "args=[replay.id, principal.tenant_id, replay.action, replay.payload]" in routes


def test_mock_stack_uses_browser_reachable_oidc_issuer():
    from pathlib import Path

    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")
    assert "OIDC_ISSUER: http://localhost:9000" in compose
    assert "MOCK_OIDC_ISSUER: http://localhost:9000" in compose


def test_local_stack_model_provider_is_configurable_from_local_environment():
    from pathlib import Path

    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")
    environment = Path(".env.local.example").read_text(encoding="utf-8")

    assert "${LOCAL_MODEL_BASE_URL:-http://host.docker.internal:11434/v1}" in compose
    assert "${LOCAL_MODEL_NAME:-qwen2.5:3b}" in compose
    assert "${LOCAL_MODEL_API_KEY:-not-needed}" in compose
    assert "LOCAL_MODEL_BASE_URL=" in environment
    assert "LOCAL_MODEL_NAME=" in environment
    assert "LOCAL_MODEL_API_KEY=" in environment


def test_frontend_mock_environment_uses_oidc_session():
    from pathlib import Path

    environment = Path("frontend/.env.mock.example").read_text(encoding="utf-8")
    assert "AUTH_OIDC_ISSUER=http://localhost:9000" in environment
    assert "BACKEND_API_URL=http://localhost:8080" in environment
    assert "BACKEND_API_KEY" not in environment


def test_example_environment_disables_llm_content_capture_by_default():
    from pathlib import Path

    environment = Path(".env.example").read_text(encoding="utf-8")
    assert "TELEMETRY_CAPTURE_LLM_CONTENT=false" in environment


def _valid_production_settings() -> dict[str, object]:
    return {
        "environment": "production",
        "release_profile": "read_only_pilot",
        "auth_mode": "oidc",
        "oidc_issuer": "https://identity.example.com",
        "oidc_jwks_url": "https://identity.example.com/.well-known/jwks.json",
        "oidc_audience": "omnisell-api",
        "oidc_require_mfa": True,
        "allowed_origins": "https://ops.example.com",
        "database_url": "postgresql+psycopg://omnisell_app:app-secret@postgres:5432/omnisell",
        "migration_database_url": "postgresql+psycopg://omnisell_owner:owner-secret@postgres:5432/omnisell",
        "auto_create_schema": False,
        "task_backend": "celery",
        "redis_url": "redis://:redis-secret@redis:6379/0",
        "secret_encryption_key": Fernet.generate_key().decode(),
        "amazon_notification_token": "notification-token-with-at-least-32-characters",
        "mock_drill_enabled": False,
        "telemetry_capture_llm_content": False,
        "amazon_writes_enabled": False,
        "amazon_pilot_read_only": True,
        "model_input_cost_per_million": "1.00",
        "model_output_cost_per_million": "2.00",
        "model_pricing_version": "approved-primary-pricing-v1",
        "model_pricing_effective_at": "2026-01-01T00:00:00Z",
    }


def test_production_hides_legacy_customer_and_global_rag_demo_routes():
    from api.config import Settings
    from api.main import create_app

    app = create_app(Settings(**_valid_production_settings()))
    paths = {route.path for route in app.routes}

    assert "/api/customer/ask" not in paths
    assert "/api/customer/ask-image" not in paths
    assert "/api/rag/search" not in paths
    assert "/api/customer-cases" in paths
    assert "/api/mock-customer-service/scenarios" not in paths
    assert "/api/mock-scenarios" not in paths
    assert "/api/mock-seller-central/execute" not in paths
    assert "/api/manual-executions/{execution_id}" in paths


def test_production_settings_accept_fail_closed_configuration():
    from api.config import Settings

    settings = Settings(**_valid_production_settings())

    assert settings.environment == "production"


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("database_url", "sqlite:///./tmp/production.db"),
        ("database_url", "postgresql+psycopg://omnisell_app:replace-with-password@postgres:5432/omnisell"),
        ("migration_database_url", ""),
        ("migration_database_url", "postgresql+psycopg://omnisell_app:app-secret@postgres:5432/omnisell"),
        ("redis_url", "redis://redis:6379/0"),
        ("redis_url", "redis://:replace-with-password@redis:6379/0"),
        ("allowed_origins", "http://localhost:3000"),
        ("secret_encryption_key", "change-me"),
        ("amazon_notification_token", "short"),
        ("amazon_notification_token", "replace-with-at-least-32-random-characters"),
        ("oidc_require_mfa", False),
        ("mock_drill_enabled", True),
        ("telemetry_capture_llm_content", True),
        ("amazon_writes_enabled", True),
        ("release_profile", "full_development"),
        ("release_profile", "golden_demo"),
        ("model_input_cost_per_million", "0"),
        ("model_output_cost_per_million", "0"),
    ],
)
def test_production_settings_reject_unsafe_configuration(field, unsafe_value):
    from api.config import Settings

    values = _valid_production_settings()
    values[field] = unsafe_value

    with pytest.raises(ValueError):
        Settings(**values)


@pytest.mark.parametrize(
    ("writes_enabled", "pilot_read_only"),
    [(True, False), (False, False)],
)
def test_current_production_release_requires_amazon_read_only_pilot(
    writes_enabled: bool,
    pilot_read_only: bool,
) -> None:
    from api.config import Settings

    values = _valid_production_settings()
    values["amazon_writes_enabled"] = writes_enabled
    values["amazon_pilot_read_only"] = pilot_read_only

    with pytest.raises(ValueError, match="read-only pilot"):
        Settings(**values)


def test_production_compose_loads_production_environment_for_all_app_processes():
    from pathlib import Path

    compose = Path("docker-compose.production.yml").read_text(encoding="utf-8")
    assert "ports: !reset []" in compose
    for service in ("migrate", "api", "worker", "scheduler"):
        block = compose.split(f"  {service}:\n", 1)[1].split("\n  ", 1)[0]
        assert "env_file: [.env.production]" in block


def test_frontend_docker_context_excludes_local_environment_files():
    from pathlib import Path

    dockerignore = Path("frontend/.dockerignore").read_text(encoding="utf-8")
    assert ".env*" in dockerignore
    assert "!.env.example" in dockerignore
    assert "!.env.production.example" in dockerignore


def test_frontend_runtime_container_uses_non_root_user():
    from pathlib import Path

    dockerfile = Path("frontend/Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chown=node:node --from=build /app/.next/standalone ./" in dockerfile
    assert "USER node" in dockerfile


def test_frontend_public_runtime_flags_are_available_during_next_build():
    from pathlib import Path

    dockerfile = Path("frontend/Dockerfile").read_text(encoding="utf-8")
    local_compose = Path("docker-compose.local.yml").read_text(encoding="utf-8")
    production_compose = Path("docker-compose.production.yml").read_text(encoding="utf-8")

    for key in (
        "NEXT_PUBLIC_ENVIRONMENT",
        "NEXT_PUBLIC_MOCK_DRILL_ENABLED",
        "NEXT_PUBLIC_SELLER_CENTRAL_URL",
        "NEXT_PUBLIC_REFRESH_INTERVAL_MS",
    ):
        assert f"ARG {key}" in dockerfile
        assert f"{key}=${{{key}}}" in dockerfile
        assert f"{key}:" in local_compose.split("    build:", 1)[1].split("    environment:", 1)[0]

    production_build = production_compose.split("  frontend:", 1)[1].split("    env_file:", 1)[0]
    assert 'NEXT_PUBLIC_ENVIRONMENT: "production"' in production_build
    assert 'NEXT_PUBLIC_MOCK_DRILL_ENABLED: "false"' in production_build


def test_deployment_guide_uses_explicit_production_compose_files():
    from pathlib import Path

    deployment = Path("DEPLOYMENT.md").read_text(encoding="utf-8")
    command = "docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml"
    assert f"{command} build" in deployment
    assert f"{command} run --rm migrate" in deployment
    assert f"{command} up -d" in deployment


def test_production_environment_templates_are_tracked_and_complete():
    from pathlib import Path

    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "!.env.production.example" in gitignore
    assert "!frontend/.env.production.example" in gitignore
    backend = Path(".env.production.example").read_text(encoding="utf-8")
    frontend = Path("frontend/.env.production.example").read_text(encoding="utf-8")
    for key in (
        "ENVIRONMENT=production",
        "AUTH_MODE=oidc",
        "AUTO_CREATE_SCHEMA=false",
        "TASK_BACKEND=celery",
        "DATABASE_URL=",
        "MIGRATION_DATABASE_URL=",
        "OPS_DOMAIN=",
        "API_DOMAIN=",
        "TELEMETRY_CAPTURE_LLM_CONTENT=false",
    ):
        assert key in backend
    for key in (
        "BACKEND_API_URL=http://api:8000",
        "AUTH_SECRET=",
        "AUTH_OIDC_ISSUER=",
        "NEXT_PUBLIC_ENVIRONMENT=production",
        "NEXT_PUBLIC_SELLER_CENTRAL_URL=https://sellercentral.amazon.com",
    ):
        assert key in frontend


def test_primary_requirements_exclude_unpatched_optional_ml_stack():
    from pathlib import Path

    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "-r requirements.runtime.txt" in requirements
    for package in ("chromadb", "sentence-transformers", "transformers", "torch"):
        assert package not in requirements


def test_runtime_requirements_pin_patched_security_releases():
    from pathlib import Path

    requirements = Path("requirements.runtime.txt").read_text(encoding="utf-8").lower()
    for pin in (
        "python-dotenv==1.2.2",
        "python-multipart==0.0.31",
        "requests==2.33.0",
        "pyjwt[crypto]==2.13.0",
        "cryptography==50.0.0",
    ):
        assert pin in requirements


def test_python_dependency_install_is_locked_and_checked_in_ci():
    from pathlib import Path

    lock = Path("requirements.lock.txt").read_text(encoding="utf-8").lower()
    runtime_lock = Path("requirements.runtime.lock.txt").read_text(encoding="utf-8").lower()
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    dockerfile = Path("api/Dockerfile").read_text(encoding="utf-8")

    assert "# this file was autogenerated by uv" in lock
    assert "# this file was autogenerated by uv" in runtime_lock
    assert "cryptography==50.0.0" in lock
    assert "cryptography==50.0.0" in runtime_lock
    assert "pytest==" not in runtime_lock
    assert "pip-audit==" not in runtime_lock
    assert "scripts/check_dependency_locks.py" in workflow
    assert "--require-hashes" in workflow
    assert "python -m pip check" in workflow
    assert "pip-audit -r requirements.lock.txt" in workflow
    assert "requirements.runtime.lock.txt" in dockerfile
    assert "--require-hashes" in dockerfile


def test_dependency_lock_checker_rebuilds_both_uv_locks():
    from pathlib import Path

    checker = Path("scripts/check_dependency_locks.py").read_text(encoding="utf-8")

    assert "uv" in checker
    assert "0.12.1" in checker
    assert "requirements.runtime.lock.txt" in checker
    assert "requirements.lock.txt" in checker
    assert "--generate-hashes" in checker
    assert "--universal" in checker


def test_development_bootstrap_creates_and_verifies_python_312_environment():
    from pathlib import Path

    bootstrap = Path("scripts/bootstrap_dev.ps1").read_text(encoding="utf-8")

    assert "3.12" in bootstrap
    assert "requirements.lock.txt" in bootstrap
    assert "requirements.runtime.lock.txt" in bootstrap
    assert "--require-hashes" in bootstrap
    assert "PYTHONUTF8" in bootstrap
    assert "Console]::InputEncoding" in bootstrap
    assert "Console]::OutputEncoding" in bootstrap
    assert "bootstrap-manifest.json" in bootstrap
    assert "os_platform" in bootstrap
    assert "python_utf8_mode" in bootstrap
    assert "workspace_path_character_profile" in bootstrap
    assert "postgresql_rls" in bootstrap
    assert "pip check" in bootstrap
    assert "pip-audit" in bootstrap
    assert "compileall" in bootstrap
    assert "alembic upgrade head" in bootstrap
    assert "test_safety.py" in bootstrap


def test_versioned_clean_bootstrap_evidence_matches_current_dependency_locks():
    import hashlib
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "docs/evidence/environments/windows-clean-bootstrap-2026-08-05.json").read_text(encoding="utf-8")
    )

    assert evidence["evidence_level"] == "local_clean_bootstrap"
    assert evidence["python_utf8_mode"] == "enabled"
    assert evidence["workspace_path_character_profile"] == "contains_non_ascii"
    assert evidence["pip_check"] == "passed"
    assert evidence["dependency_audit"] == "passed"
    assert evidence["focused_smoke"] == "passed"
    assert evidence["postgresql_rls"].startswith("not_verified")
    assert evidence["docker_compose"].startswith("not_verified")
    assert (
        evidence["requirements_lock_sha256"]
        == hashlib.sha256((root / "requirements.lock.txt").read_bytes()).hexdigest()
    )
    assert (
        evidence["runtime_lock_sha256"]
        == hashlib.sha256((root / "requirements.runtime.lock.txt").read_bytes()).hexdigest()
    )
    serialized = json.dumps(evidence).lower()
    assert "users\\" not in serialized
    assert "api_key" not in serialized
    assert "token" not in serialized


def test_request_resets_database_tenant_context(tmp_path):
    from api.config import Settings
    from api.main import create_app
    from infrastructure.database.session import _current_tenant_id

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'tenant-context.sqlite'}",
            auto_create_schema=True,
            api_key="test-key",
            auth_mode="api_key",
        )
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/admin/health",
                headers={"X-API-Key": "test-key", "X-Tenant-ID": "tenant-a"},
            )
        assert response.status_code == 200
        assert _current_tenant_id.get() is None

    asyncio.run(scenario())


def test_database_tenant_context_is_explicitly_unbound():
    from types import SimpleNamespace

    from api.security import unbind_database_tenant_context
    from infrastructure.database.session import _current_tenant_id, bind_tenant_context

    request = SimpleNamespace(state=SimpleNamespace())
    request.state.database_tenant_token = bind_tenant_context("tenant-a")
    assert _current_tenant_id.get() == "tenant-a"

    unbind_database_tenant_context(request)

    assert _current_tenant_id.get() is None
    assert not hasattr(request.state, "database_tenant_token")


def test_global_operational_controls_require_admin_role(tmp_path):
    from api.config import Settings
    from api.main import create_app

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'admin-controls.sqlite'}",
            auto_create_schema=True,
            api_key="test-key",
            auth_mode="api_key",
        )
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            operator = await client.get(
                "/api/admin/circuit-breakers",
                headers={
                    "X-API-Key": "test-key",
                    "X-Tenant-ID": "default",
                    "X-User-Role": "operator",
                    "X-Permission-Scopes": "read write",
                },
            )
            admin = await client.get(
                "/api/admin/circuit-breakers",
                headers={
                    "X-API-Key": "test-key",
                    "X-Tenant-ID": "default",
                    "X-User-Role": "admin",
                    "X-Permission-Scopes": "read write admin",
                },
            )
            metrics = await client.get(
                "/api/admin/metrics",
                headers={
                    "X-API-Key": "test-key",
                    "X-Tenant-ID": "default",
                    "X-User-Role": "operator",
                    "X-Permission-Scopes": "read write",
                },
            )
        assert operator.status_code == 403
        assert admin.status_code == 200
        assert metrics.status_code == 403

    asyncio.run(scenario())


def test_amazon_notification_secret_is_not_part_of_request_path(tmp_path):
    from api.config import Settings
    from api.main import create_app

    token = "notification-token-with-at-least-32-characters"
    database_url = f"sqlite:///{tmp_path / 'notification-route.sqlite'}"
    initialize_database(database_url)
    app = create_app(
        Settings(
            database_url=database_url,
            auto_create_schema=False,
            api_key="test-key",
            auth_mode="api_key",
            amazon_notification_token=token,
        )
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/channels/amazon/missing-connection/notifications",
                params={"tenant_id": "default"},
                headers={"X-OmniSell-Notification-Token": token},
                json={"NotificationType": "ORDER_CHANGE"},
            )
            legacy = await client.post(
                f"/api/channels/amazon/missing-connection/notifications/{token}",
                params={"tenant_id": "default"},
                json={"NotificationType": "ORDER_CHANGE"},
            )
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
        assert legacy.status_code == 404
        assert legacy.json()["detail"] == "Not Found"

    asyncio.run(scenario())


def test_legacy_product_sync_is_disabled_in_production():
    from types import SimpleNamespace

    from api.config import Settings
    from api.routes.product_routes import SyncProductRequest, sync_product

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=Settings(**_valid_production_settings()))),
        state=SimpleNamespace(principal=SimpleNamespace(scopes=frozenset({"read", "write", "admin"}))),
    )

    async def scenario() -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await sync_product(SyncProductRequest(product_id="P1001"), request)
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "legacy_route_disabled"

    asyncio.run(scenario())


def test_legacy_product_sync_apply_requires_write_scope():
    from types import SimpleNamespace

    from api.config import Settings
    from api.routes.product_routes import SyncProductRequest, sync_product

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=Settings(environment="development"))),
        state=SimpleNamespace(principal=SimpleNamespace(scopes=frozenset({"read"}))),
    )

    async def scenario() -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await sync_product(
                SyncProductRequest(product_id="P1001", dry_run=False, confirm=True),
                request,
            )
        assert exc.value.status_code == 403

    asyncio.run(scenario())


def test_public_production_health_response_is_minimal():
    from types import SimpleNamespace

    from api.routes.admin_routes import health

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(environment="production"),
                team=object(),
                task_queue=SimpleNamespace(queue_lengths=lambda: {"text": 12}),
                hitl_manager=object(),
            )
        )
    )

    result = asyncio.run(health(request))

    assert result == {"status": "ok", "service": "omnisell-api"}


def test_caddy_applies_security_headers_to_ui_and_api():
    from pathlib import Path

    caddyfile = Path("deploy/Caddyfile").read_text(encoding="utf-8")
    assert caddyfile.count('Strict-Transport-Security "max-age=31536000; includeSubDomains"') == 2
    assert caddyfile.count('X-Content-Type-Options "nosniff"') == 2
    assert caddyfile.count('X-Frame-Options "DENY"') == 2
    assert "Content-Security-Policy \"default-src 'self'" in caddyfile
    assert 'Permissions-Policy "camera=(), microphone=(), geolocation=()"' in caddyfile


def test_app_factory_does_not_register_deprecated_fastapi_events(tmp_path):
    from api.config import Settings
    from api.main import create_app

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'lifespan.sqlite'}",
                auto_create_schema=True,
                api_key="test-key",
                auth_mode="api_key",
            )
        )

    assert not [item for item in captured if "on_event is deprecated" in str(item.message)]


def test_backup_scripts_use_explicit_production_compose_and_atomic_files():
    from pathlib import Path

    backup = Path("scripts/backup_postgres.sh").read_text(encoding="utf-8")
    offsite = Path("scripts/backup_postgres_offsite.sh").read_text(encoding="utf-8")
    restore = Path("scripts/restore_postgres.sh").read_text(encoding="utf-8")
    for script in (backup, offsite, restore):
        assert 'COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"' in script
        assert 'COMPOSE_OVERRIDE_FILE="${COMPOSE_OVERRIDE_FILE:-docker-compose.production.yml}"' in script
        assert (
            'compose=(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_BASE_FILE" -f "$COMPOSE_OVERRIDE_FILE")'
            in script
        )
        assert '"${compose[@]}" exec -T' in script
    assert 'partial="$file.partial"' in backup
    assert 'mv "$partial" "$file"' in backup
    assert 'chmod 600 "$file" "$file.sha256"' in backup


def test_tenant_repository_has_no_unscoped_connection_lookup():
    from pathlib import Path

    repository = Path("infrastructure/repositories/tenants.py").read_text(encoding="utf-8")
    assert "def get_connection_by_id(" not in repository


def test_persistent_approval_and_audit_queries_require_tenant(tmp_path):
    from infrastructure.repositories.audit import SQLAuditLogger
    from infrastructure.repositories.hitl import SQLHITLManager

    url = f"sqlite:///{tmp_path / 'tenant-required.sqlite'}"
    initialize_database(url)
    factory = create_session_factory(url)
    audit = SQLAuditLogger(factory)
    hitl = SQLHITLManager(factory, audit)

    with pytest.raises(ValueError):
        audit.query()
    with pytest.raises(ValueError):
        hitl.get_pending()


def test_frontend_auth_validates_runtime_production_environment():
    from pathlib import Path

    auth = Path("frontend/auth.ts").read_text(encoding="utf-8")
    environment = Path("frontend/.env.production.example").read_text(encoding="utf-8")
    assert 'process.env.OMNISELL_ENVIRONMENT === "production"' in auth
    for key in ("AUTH_SECRET", "AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET", "BACKEND_API_URL"):
        assert key in auth
    assert "OMNISELL_ENVIRONMENT=production" in environment
    middleware = Path("frontend/middleware.ts").read_text(encoding="utf-8")
    assert 'process.env.NEXT_PUBLIC_MOCK_DRILL_ENABLED !== "true"' in middleware
    assert 'pathname.startsWith("/mock-")' in middleware
    assert 'pathname.startsWith("/api/mock-")' in middleware
    assert "status: 404" in middleware


def test_frontend_middleware_rejects_incomplete_or_expired_oidc_sessions():
    from pathlib import Path

    middleware = Path("frontend/middleware.ts").read_text(encoding="utf-8")
    assert "request.auth?.accessToken" in middleware
    assert "request.auth?.accessTokenExpiresAt" in middleware
    assert 'pathname.startsWith("/api/")' in middleware
    assert "status: 401" in middleware
    assert 'new URL("/signin", request.nextUrl)' in middleware


def test_frontend_mobile_navigation_does_not_expand_the_page_width():
    from pathlib import Path

    layout = Path("frontend/app/layout.tsx").read_text(encoding="utf-8")
    shell = Path("frontend/components/app-shell.tsx").read_text(encoding="utf-8")
    assert "<AppShell" in layout
    assert "overflow-x-auto" not in shell
    assert "translate-x-0" in shell and "-translate-x-full" in shell
    assert "overflow-x-clip" in shell
    assert 'aria-label="主导航"' in shell


def test_frontend_primary_navigation_uses_native_links_for_reliable_page_loads():
    from pathlib import Path

    shell = Path("frontend/components/app-shell.tsx").read_text(encoding="utf-8")
    assert 'import Link from "next/link"' not in shell
    assert "<a ref={index === 0 ? firstLink : undefined} key={href} href={href}" in shell


def test_frontend_mobile_header_uses_compact_nonwrapping_controls():
    from pathlib import Path

    shell = Path("frontend/components/app-shell.tsx").read_text(encoding="utf-8")
    assert "min-h-16" in shell
    assert "shrink-0 rounded-full" in shell
    assert "max-w-[116px] truncate" in shell
    assert 'queryKey: ["session-context"]' in shell


def test_local_compose_overlay_adds_frontend_without_tls_proxy():
    from pathlib import Path

    compose = Path("docker-compose.local.yml").read_text(encoding="utf-8")
    assert "  frontend:" in compose
    assert "BACKEND_API_URL: http://api:8000" in compose
    assert "AUTH_OIDC_ISSUER: http://localhost:${E2E_MOCK_CLOUD_PORT:-9000}" in compose
    assert "AUTH_OIDC_WELL_KNOWN_URL: http://mock-cloud:9000/.well-known/openid-configuration" in compose
    assert "${LOCAL_FRONTEND_PORT:-3000}:3000" in compose
    assert "  caddy:" not in compose


def test_frontend_auth_supports_internal_oidc_discovery_url():
    from pathlib import Path

    auth = Path("frontend/auth.ts").read_text(encoding="utf-8")
    assert "AUTH_OIDC_WELL_KNOWN_URL" in auth
    assert "wellKnown:" in auth


def test_mock_oidc_metadata_separates_public_and_internal_endpoints():
    from pathlib import Path

    server = Path("local_e2e_mock/server.py").read_text(encoding="utf-8")
    assert "INTERNAL_BASE_URL" in server
    assert '"authorization_endpoint": f"{ISSUER}/oidc/authorize"' in server
    assert '"token_endpoint": f"{INTERNAL_BASE_URL}/oidc/token"' in server
    assert '"jwks_uri": f"{INTERNAL_BASE_URL}/.well-known/jwks.json"' in server


def test_local_launcher_starts_backend_and_frontend_layers():
    from pathlib import Path

    launcher = Path("scripts/start_local.ps1").read_text(encoding="utf-8")
    assert "docker-compose.e2e.yml" in launcher
    assert "docker-compose.local.yml" in launcher
    assert "http://localhost:$frontendPort/signin" in launcher
    assert "scripts/run_local_e2e.py" in launcher
    assert "& $pythonExecutable $smokeArgs" in launcher


def test_local_launcher_uses_the_bootstrapped_virtualenv_for_smoke_scripts():
    from pathlib import Path

    launcher = Path("scripts/start_local.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $projectRoot ".venv/Scripts/python.exe"' in launcher
    assert "scripts/bootstrap_dev.ps1" in launcher
    assert "& python $smokeArgs" not in launcher


def test_local_launcher_supports_explicit_release_profile_without_editing_env_files():
    from pathlib import Path

    launcher = Path("scripts/start_local.ps1").read_text(encoding="utf-8")
    compose = Path("docker-compose.local.yml").read_text(encoding="utf-8")

    assert 'ValidateSet("full_development", "golden_demo", "read_only_pilot")' in launcher
    assert "$env:OMNISELL_LOCAL_RELEASE_PROFILE = $ReleaseProfile" in launcher
    assert "OMNISELL_LOCAL_RELEASE_PROFILE" in compose
    assert "${OMNISELL_LOCAL_RELEASE_PROFILE:-full_development}" in compose
    assert "LOCAL_PIP_INDEX_URL" in compose


def test_local_e2e_notification_token_uses_request_header():
    from pathlib import Path

    smoke = Path("scripts/run_local_e2e.py").read_text(encoding="utf-8")
    assert '"X-OmniSell-Notification-Token"' in smoke
    assert "/notifications/local-notification-token" not in smoke


def test_local_stack_does_not_bypass_production_https_validation():
    from pathlib import Path

    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")
    assert "ENVIRONMENT: local" in compose
    assert "ENVIRONMENT: production" not in compose


def test_local_postgres_uses_frozen_baseline_volume_and_fails_closed_on_schema_drift():
    from pathlib import Path

    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")

    assert "e2e-postgres-frozen-0023-data" in compose
    assert "alembic upgrade head && alembic check &&" in compose


def test_local_launcher_health_checks_bypass_system_proxy():
    from pathlib import Path

    launcher = Path("scripts/start_local.ps1").read_text(encoding="utf-8")
    assert "HttpClientHandler" in launcher
    assert "UseProxy = $false" in launcher


def test_local_launcher_loads_system_net_http_for_windows_powershell():
    from pathlib import Path

    launcher = Path("scripts/start_local.ps1").read_text(encoding="utf-8")
    load_index = launcher.index('Add-Type -AssemblyName "System.Net.Http"')
    handler_index = launcher.index("[System.Net.Http.HttpClientHandler]::new()")

    assert load_index < handler_index


def test_local_e2e_smoke_accepts_seeded_superset_for_repeat_runs():
    from pathlib import Path

    smoke = Path("scripts/run_local_e2e.py").read_text(encoding="utf-8")
    assert "expected_order_ids.issubset" in smoke
    assert "while True:" in smoke
    assert "next_token" in smoke


def test_frontend_local_oidc_rewrites_server_backchannel_requests():
    from pathlib import Path

    auth = Path("frontend/auth.ts").read_text(encoding="utf-8")
    compose = Path("docker-compose.local.yml").read_text(encoding="utf-8")
    assert "customFetch" in auth
    assert "AUTH_OIDC_INTERNAL_BASE_URL" in auth
    assert "[customFetch]" in auth
    assert "AUTH_OIDC_INTERNAL_BASE_URL: http://mock-cloud:9000" in compose


def test_local_launcher_runs_frontend_oidc_session_smoke():
    from pathlib import Path

    launcher = Path("scripts/start_local.ps1").read_text(encoding="utf-8")
    smoke = Path("scripts/run_local_frontend_auth_smoke.py").read_text(encoding="utf-8")
    assert "scripts/run_local_frontend_auth_smoke.py" in launcher
    assert '"--release-profile", $ReleaseProfile' in launcher
    assert "/api/auth/csrf" in smoke
    assert "/api/auth/signin/oidc" in smoke
    assert "/api/auth/session" in smoke
    assert "/api/issues" in smoke
    assert "/api/listing-quality/snapshots" in smoke
    assert 'args.release_profile == "golden_demo"' in smoke
    assert "require_status(extension_response, 404)" in smoke
    assert "accessToken" not in smoke.split("print", 1)[-1]


def test_settings_define_distinct_bounded_advisor_budget_profiles() -> None:
    from api.config import Settings

    profiles = Settings().agent_budget_profiles

    assert set(profiles) == {
        "operations_brief_analysis",
        "customer_case_analysis",
        "listing_optimization_analysis",
        "reconciliation_investigation",
    }
    assert profiles["operations_brief_analysis"] != profiles["reconciliation_investigation"]
    assert int(profiles["operations_brief_analysis"]["max_evidence_chars"]) >= 6000
    assert int(profiles["operations_brief_analysis"]["timeout_seconds"]) >= 240
    for profile in profiles.values():
        assert int(profile["max_prompt_tokens"]) > 0
        assert int(profile["max_completion_tokens"]) > 0
        assert Decimal(str(profile["max_estimated_cost"])) > 0
        assert int(profile["max_evidence_items"]) > 0
        assert int(profile["max_evidence_chars"]) > 0
        assert int(profile["max_total_evidence_chars"]) >= int(profile["max_evidence_chars"])
        assert int(profile["max_request_bytes"]) > int(profile["max_total_evidence_chars"])
        assert int(profile["timeout_seconds"]) > 0


def test_settings_reject_incomplete_agent_preflight_budget() -> None:
    from api.config import Settings

    profiles = Settings().agent_budget_profiles
    profiles["operations_brief_analysis"] = dict(profiles["operations_brief_analysis"])
    profiles["operations_brief_analysis"].pop("max_total_evidence_chars")

    with pytest.raises(ValueError, match="max_total_evidence_chars"):
        Settings(agent_budget_profiles=profiles)


def test_settings_reject_agent_budget_that_cannot_cover_schema_repair_output() -> None:
    from api.config import Settings

    with pytest.raises(ValueError, match="schema repair and failover output"):
        Settings(
            model_input_cost_per_million=Decimal("2.5"),
            model_output_cost_per_million=Decimal("15"),
        )


def test_settings_reject_failover_chain_that_cannot_fit_workflow_cost_budget() -> None:
    from api.config import Settings

    with pytest.raises(ValueError, match="failover output"):
        Settings(
            model_fallback_enabled=True,
            model_fallback_model_name="fallback-model",
            model_fallback_api_key="fallback-key",
            model_input_cost_per_million=Decimal("1"),
            model_output_cost_per_million=Decimal("2"),
            model_fallback_input_cost_per_million=Decimal("10"),
            model_fallback_output_cost_per_million=Decimal("20"),
        )


def test_settings_reject_model_token_budget_that_cannot_cover_schema_repair() -> None:
    from api.config import Settings

    with pytest.raises(ValueError, match="MODEL_MAX_TOKENS_PER_TASK"):
        Settings(model_max_tokens_per_task=14000)


def test_model_configuration_prefers_generic_names_and_supports_one_legacy_cycle(monkeypatch) -> None:
    from api import config
    from api.config import Settings

    for name in ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY", "VLLM_BASE_URL", "VLLM_MODEL_NAME", "VLLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VLLM_BASE_URL", "https://legacy-provider.example/v1")
    monkeypatch.setenv("VLLM_MODEL_NAME", "legacy-model")
    monkeypatch.setenv("VLLM_API_KEY", "legacy-secret")
    monkeypatch.setattr(config, "_LEGACY_MODEL_WARNING_EMITTED", False)

    with pytest.warns(DeprecationWarning, match="removed in 2.0.0"):
        settings = Settings(_env_file=None)

    assert settings.model_base_url == "https://legacy-provider.example/v1"
    assert settings.model_name == "legacy-model"
    assert settings.model_api_key == "legacy-secret"


def test_production_model_callers_do_not_read_legacy_vllm_environment_directly() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    production_callers = (
        root / "agents" / "tools" / "vision_tool.py",
        root / "scripts" / "run_real_model_staging_smoke.py",
        root / "scripts" / "test_model_gateway.py",
    )

    assert not (root / "scripts" / "test_vllm.py").exists()
    for path in production_callers:
        assert path.exists()
        assert "VLLM_" not in path.read_text(encoding="utf-8")


def test_conflicting_generic_and_legacy_model_configuration_fails_closed(monkeypatch) -> None:
    from api.config import Settings

    monkeypatch.setenv("MODEL_BASE_URL", "https://new-provider.example/v1")
    monkeypatch.setenv("VLLM_BASE_URL", "https://legacy-provider.example/v1")

    with pytest.raises(ValueError, match="MODEL_BASE_URL conflicts with deprecated VLLM_BASE_URL"):
        Settings(_env_file=None)


def test_settings_define_tenant_agent_minute_and_daily_quotas() -> None:
    from api.config import Settings

    settings = Settings()

    assert settings.agent_tenant_quota_per_minute > 0
    assert settings.agent_tenant_quota_per_day >= settings.agent_tenant_quota_per_minute


def test_local_compose_uses_real_model_validated_budgets() -> None:
    from pathlib import Path

    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")

    assert "MODEL_MAX_TOKENS_PER_TASK: ${LOCAL_MODEL_MAX_TOKENS_PER_TASK:-36000}" in compose
    assert "MODEL_TIMEOUT_SECONDS: ${LOCAL_MODEL_TIMEOUT_SECONDS:-240}" in compose
    assert "MODEL_INPUT_COST_PER_MILLION: ${LOCAL_MODEL_INPUT_COST_PER_MILLION:-2.5}" in compose
    assert "MODEL_OUTPUT_COST_PER_MILLION: ${LOCAL_MODEL_OUTPUT_COST_PER_MILLION:-15}" in compose
    assert "AGENT_BUDGET_PROFILES:" in compose
    assert '"operations_brief_analysis"' in compose
    assert '"timeout_seconds":240' in compose


def test_reconciliation_workbench_tracks_persisted_task_outcome() -> None:
    from pathlib import Path

    workbench = Path("frontend/components/operations/reconciliation-workbench.tsx").read_text(encoding="utf-8")

    assert 'queryKey: ["infrastructure-tasks"]' in workbench
    assert 'apiFetch<TaskListResponse>("/api/tasks?limit=30")' in workbench
    assert "activeTask?.error" in workbench


def test_api_docker_context_excludes_clean_virtual_environment() -> None:
    from pathlib import Path

    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".venv-clean" in dockerignore or ".venv*" in dockerignore
