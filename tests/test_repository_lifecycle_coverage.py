from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.models import (
    ChannelConnection,
    ChannelProvider,
    ConnectionStatus,
    Membership,
    Role,
    Tenant,
    User,
    WorkflowStatus,
)
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.tasks import DurableTaskRepository, EventRepository
from infrastructure.repositories.tenants import TenantRepository
from infrastructure.repositories.workflows import SQLWorkflowStore


def test_tenant_repository_covers_identity_membership_and_connection_lifecycle(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'tenant-lifecycle.sqlite'}"
    initialize_database(database_url, default_tenant_id="default")
    repository = TenantRepository(create_session_factory(database_url))

    tenant = repository.create_tenant(Tenant(id="tenant-1", name="Tenant One", slug="tenant-one"))
    user = repository.create_user(
        User(id="user-1", email="operator@example.com", display_name="Operator", oidc_subject="subject-1")
    )
    membership = repository.add_membership(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OPERATOR))

    assert "tenant-1" in repository.list_tenant_ids()
    persisted_tenant = repository.get_tenant("tenant-1")
    assert persisted_tenant is not None
    assert (persisted_tenant.id, persisted_tenant.name, persisted_tenant.slug) == (
        tenant.id,
        tenant.name,
        tenant.slug,
    )
    assert persisted_tenant.created_at.replace(tzinfo=UTC) == tenant.created_at
    assert repository.get_tenant("missing") is None
    assert repository.find_user_by_oidc_subject("subject-1") == user
    assert repository.find_user_by_oidc_subject("missing") is None
    assert repository.find_membership(tenant_id=tenant.id, user_id=user.id) == membership
    assert repository.find_membership(tenant_id=tenant.id, user_id="missing") is None
    assert repository.amazon_writes_enabled(tenant.id) is False

    active = repository.save_connection(
        ChannelConnection(
            id="connection-active",
            tenant_id=tenant.id,
            provider=ChannelProvider.AMAZON,
            selling_partner_id="seller-active",
            region="na",
            marketplace_ids=("US",),
            secret_ref="secret://active",
            status=ConnectionStatus.ACTIVE,
        )
    )
    repository.save_connection(
        ChannelConnection(
            id="connection-pending",
            tenant_id=tenant.id,
            provider=ChannelProvider.AMAZON,
            selling_partner_id="seller-pending",
            region="eu",
            marketplace_ids=("DE",),
            secret_ref="secret://pending",
            status=ConnectionStatus.PENDING,
        )
    )

    persisted_connection = repository.get_connection(tenant_id=tenant.id, connection_id=active.id)
    assert persisted_connection is not None
    assert (
        persisted_connection.id,
        persisted_connection.tenant_id,
        persisted_connection.provider,
        persisted_connection.selling_partner_id,
        persisted_connection.marketplace_ids,
        persisted_connection.status,
    ) == (
        active.id,
        active.tenant_id,
        active.provider,
        active.selling_partner_id,
        active.marketplace_ids,
        active.status,
    )
    assert repository.get_connection(tenant_id=tenant.id, connection_id="missing") is None
    assert {item.id for item in repository.list_connections(tenant_id=tenant.id)} == {
        "connection-active",
        "connection-pending",
    }
    assert [item.id for item in repository.list_active_connections(tenant_id=tenant.id)] == ["connection-active"]
    assert repository.list_connections(tenant_id="default") == []


def test_durable_task_repository_enforces_replay_close_and_status_lifecycle(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'task-lifecycle.sqlite'}"
    initialize_database(database_url)
    repository = DurableTaskRepository(create_session_factory(database_url))
    task = repository.create(
        tenant_id="default",
        actor_id="operator-1",
        queue_name="operations",
        action="operations.refresh",
        payload={"connection_id": "connection-1"},
        idempotency_key="task-1",
        correlation_id="correlation-1",
    )

    assert [row.id for row in repository.list(tenant_id="default")] == [task.id]
    with pytest.raises(LookupError, match="task not found"):
        repository.replay(tenant_id="default", task_id="missing", actor_id="operator-1")
    with pytest.raises(ValueError, match="only failed"):
        repository.replay(tenant_id="default", task_id=task.id, actor_id="operator-1")
    with pytest.raises(LookupError, match="task not found"):
        repository.close(tenant_id="default", task_id="missing", actor_id="operator-1", reason="none")
    with pytest.raises(ValueError, match="only failed"):
        repository.close(tenant_id="default", task_id=task.id, actor_id="operator-1", reason="none")

    repository.update_status(tenant_id="default", task_id=task.id, status="running")
    running = repository.get(tenant_id="default", task_id=task.id)
    assert running is not None and running.attempts == 1 and running.started_at is not None
    repository.update_status(
        tenant_id="default",
        task_id=task.id,
        status="failed",
        error_code="TimeoutError",
        error_message="provider timeout",
    )
    replay = repository.replay(tenant_id="default", task_id=task.id, actor_id="operator-2")
    assert replay.replayed_from_task_id == task.id
    repository.update_status(tenant_id="default", task_id=replay.id, status="dead_letter")
    closed = repository.close(
        tenant_id="default",
        task_id=replay.id,
        actor_id="operator-2",
        reason="verified outside system",
    )
    assert closed.status == "closed"
    assert closed.close_reason == "verified outside system"
    repository.update_status(tenant_id="default", task_id="missing", status="failed")


def test_event_repository_covers_inbox_and_outbox_retry_state_machines(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'event-lifecycle.sqlite'}"
    initialize_database(database_url)
    repository = EventRepository(create_session_factory(database_url))

    assert repository.accept_inbox(
        tenant_id="default",
        provider="amazon",
        external_event_id="event-1",
        event_type="ORDER_CHANGE",
        payload={"order_id": "order-1"},
    )
    first = repository.claim_pending(tenant_id="default", lease_seconds=30)[0]
    repository.mark_retry(tenant_id="default", event_id=first.id, error=TimeoutError("temporary"), max_attempts=5)
    failure = repository.list_inbox_failures(tenant_id="default")[0]
    assert failure.status == "retry_pending"

    with repository.session_factory.begin() as session:
        row = session.get(type(failure), failure.id)
        assert row is not None
        row.next_retry_at = datetime(2020, 1, 1, tzinfo=UTC)
    second = repository.claim_pending(tenant_id="default", lease_seconds=30)[0]
    repository.mark_processed(tenant_id="default", event_id=second.id)
    repository.mark_processed(tenant_id="default", event_id="missing")
    repository.mark_retry(tenant_id="default", event_id="missing", error=RuntimeError("ignored"))
    repository.mark_dead_letter(
        tenant_id="default",
        event_id="missing",
        error_code="Unknown",
        error_message="ignored",
    )
    assert repository.list_inbox_failures(tenant_id="default") == []

    outbox_id = repository.add_outbox(
        tenant_id="default",
        aggregate_type="issue",
        aggregate_id="issue-1",
        event_type="issue.changed",
        payload={"status": "open"},
        dedupe_key="issue-1:changed",
    )
    assert (
        repository.add_outbox(
            tenant_id="default",
            aggregate_type="issue",
            aggregate_id="issue-1",
            event_type="issue.changed",
            payload={"status": "open"},
            dedupe_key="issue-1:changed",
        )
        == outbox_id
    )
    claimed = repository.claim_outbox(tenant_id="default", lease_seconds=30)
    assert [row.id for row in claimed] == [outbox_id]
    repository.mark_outbox_retry(
        tenant_id="default",
        event_id=outbox_id,
        error=ConnectionError("temporary"),
        max_attempts=5,
    )
    with repository.session_factory.begin() as session:
        row = session.get(type(claimed[0]), outbox_id)
        assert row is not None
        row.next_retry_at = datetime(2020, 1, 1, tzinfo=UTC)
    claimed_again = repository.claim_outbox(tenant_id="default", lease_seconds=30)
    assert [row.id for row in claimed_again] == [outbox_id]
    repository.mark_outbox_processed(tenant_id="default", event_id=outbox_id)
    repository.mark_outbox_processed(tenant_id="default", event_id="missing")
    repository.mark_outbox_retry(
        tenant_id="default",
        event_id="missing",
        error=RuntimeError("ignored"),
    )


def test_workflow_store_persists_analysis_dispatch_retry_and_recovery(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow-lifecycle.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-1")
    store = SQLWorkflowStore(create_session_factory(database_url))
    run_id, created = store.create_run_once(
        tenant_id="tenant-1",
        actor_id="operator-1",
        workflow_type="operations_brief",
        correlation_id="correlation-1",
        request={"marketplace_id": "US"},
        prompt_version="v7",
        agent_type="operations_brief_advisor",
        schema_version="2.0",
        idempotency_key="run-1",
    )
    duplicate_id, duplicate_created = store.create_run_once(
        tenant_id="tenant-1",
        actor_id="operator-1",
        workflow_type="operations_brief",
        correlation_id="correlation-2",
        request={},
        idempotency_key="run-1",
    )
    assert created is True
    assert (duplicate_id, duplicate_created) == (run_id, False)
    assert (
        store.find_by_idempotency(tenant_id="tenant-1", workflow_type="operations_brief", idempotency_key="run-1")
        is not None
    )

    store.transition(
        tenant_id="tenant-1",
        run_id=run_id,
        status=WorkflowStatus.VALIDATED,
        state={
            "agent_type": "operations_brief_advisor",
            "schema_version": "2.0",
            "evidence_manifest": [{"evidence_id": "inventory:1"}],
        },
    )
    store.transition(
        tenant_id="tenant-1",
        run_id=run_id,
        status=WorkflowStatus.EVIDENCE_COLLECTED,
        state={
            "analysis_result": {
                "schema_version": "2.1",
                "model_usage": {
                    "model": "test-model",
                    "total_tokens": 42,
                    "estimated_cost": "0.0012",
                },
            },
            "error_category": "none",
        },
    )
    store.set_dispatch_task(
        tenant_id="tenant-1",
        run_id=run_id,
        task_id="task-1",
        task_status="running",
    )
    assert store.increment_retry(tenant_id="tenant-1", run_id=run_id) == 1
    assert store.claim_retry_attempt(tenant_id="tenant-1", run_id=run_id) == 1
    row = store.get_run(tenant_id="tenant-1", run_id=run_id)
    assert row is not None
    assert row.model == "test-model"
    assert row.token_usage == 42
    assert str(row.estimated_cost) == "0.001200"
    assert row.state["dispatch"]["task_id"] == "task-1"
    assert [
        item.id for item in store.list_runs(tenant_id="tenant-1", status=WorkflowStatus.EVIDENCE_COLLECTED.value)
    ] == [run_id]

    assert store.reset_active_run_for_recovery(tenant_id="tenant-1", run_id=run_id) is True
    recovered = store.get_run(tenant_id="tenant-1", run_id=run_id)
    assert recovered is not None and recovered.status == WorkflowStatus.RECEIVED.value
    assert recovered.state["recovery"]["recovered_from"] == WorkflowStatus.EVIDENCE_COLLECTED.value

    terminal_id = store.create_run(
        tenant_id="tenant-1",
        actor_id="operator-1",
        workflow_type="operations_brief",
        correlation_id="terminal",
        request={},
    )
    store.transition(
        tenant_id="tenant-1",
        run_id=terminal_id,
        status=WorkflowStatus.FAILED,
        state={"error_category": "provider_timeout"},
    )
    assert store.reset_active_run_for_recovery(tenant_id="tenant-1", run_id=terminal_id) is False

    for operation in (
        lambda: store.transition(tenant_id="tenant-1", run_id="missing", status=WorkflowStatus.FAILED, state={}),
        lambda: store.reset_active_run_for_recovery(tenant_id="tenant-1", run_id="missing"),
        lambda: store.set_dispatch_task(tenant_id="tenant-1", run_id="missing", task_id="task", task_status="failed"),
        lambda: store.increment_retry(tenant_id="tenant-1", run_id="missing"),
        lambda: store.claim_retry_attempt(tenant_id="tenant-1", run_id="missing"),
    ):
        with pytest.raises(KeyError, match="Agent run not found"):
            operation()
