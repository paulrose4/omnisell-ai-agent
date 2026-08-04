from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal

import scripts.seed_golden_mock_tenant as seed_cli
from application.demo_reset import reset_mock_demo_tenant
from application.golden_mock import seed_golden_mock_tenant
from commerce.adapters.amazon.secrets import SecretRow
from domain.models import OperationalIssue, RecommendedAction
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ChannelConnectionRow
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository


def test_golden_mock_tenant_seeds_only_operator_configuration(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'golden-mock.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    session_factory = create_session_factory(database_url)

    result = seed_golden_mock_tenant(
        session_factory=session_factory,
        tenant_id="local-demo",
        now=datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
    )

    assert result["mock_data"] is True
    assert result["data_source"] == "mock_amazon"
    assert result["verified_amazon_data"] is False
    assert result["inventory_policy_count"] == 10
    assert result["cost_profile_count"] == 9
    assert result["snapshot_count"] == 0
    assert result["issue_count"] == 0

    repeated = seed_golden_mock_tenant(
        session_factory=session_factory,
        tenant_id="local-demo",
        now=datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
    )
    assert repeated["inventory_policy_count"] == 10
    assert repeated["cost_profile_count"] == 9


def test_golden_mock_cli_binds_database_tenant_context(monkeypatch, capsys) -> None:
    entered_tenants: list[str] = []

    @contextmanager
    def capture_tenant_context(tenant_id: str):
        entered_tenants.append(tenant_id)
        yield

    monkeypatch.setattr(seed_cli, "get_settings", lambda: type("Settings", (), {"database_url": "sqlite://"})())
    monkeypatch.setattr(seed_cli, "create_session_factory", lambda _database_url: object())
    monkeypatch.setattr(seed_cli, "tenant_context", capture_tenant_context, raising=False)
    monkeypatch.setattr(
        seed_cli,
        "seed_golden_mock_tenant",
        lambda *, session_factory, tenant_id: {"tenant_id": tenant_id},
    )

    seed_cli.main()

    assert entered_tenants == ["local-demo"]
    assert '"tenant_id": "local-demo"' in capsys.readouterr().out


def test_mock_demo_reset_clears_derived_data_and_preserves_configuration(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo-reset.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    session_factory = create_session_factory(database_url)
    with session_factory.begin() as session:
        session.add(SecretRow(id="secret-1", tenant_id="local-demo", ciphertext=b"encrypted-refresh-token"))
        session.add(
            ChannelConnectionRow(
                id="connection-1",
                tenant_id="local-demo",
                provider="amazon",
                selling_partner_id="seller-1",
                region="na",
                marketplace_ids=["ATVPDKIKX0DER"],
                secret_ref="db-secret://secret-1",
                status="active",
                version=1,
            )
        )
    seed_golden_mock_tenant(
        session_factory=session_factory, tenant_id="local-demo", now=datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    )
    issues = OperationalIssueRepository(session_factory)
    actions = RecommendedActionRepository(session_factory)
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    issues.upsert(
        OperationalIssue(
            id="reset-issue",
            tenant_id="local-demo",
            seller_sku="MOCK-RESET",
            marketplace_id="ATVPDKIKX0DER",
            issue_type="inventory_stockout_risk",
            severity="high",
            title="Reset me",
            summary="Derived data",
            status="open",
            dedupe_key="reset-issue",
            confidence=Decimal("0.5"),
            data_freshness_at=now,
            evidence=({"source": "mock"},),
            missing_data=(),
            detected_at=now,
            last_detected_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    actions.upsert(
        RecommendedAction(
            tenant_id="local-demo",
            issue_id="reset-issue",
            action_type="review_stock",
            title="Reset me",
            description="Derived action",
            execution_mode="recommendation_only",
            risk_level="medium",
            status="proposed",
            parameters={},
            evidence=(),
            expected_outcome="Reset",
            confidence=Decimal("0.5"),
            automation_eligible=False,
            dedupe_key="reset-action",
            created_at=now,
            updated_at=now,
        ),
        canonical_issue_id="reset-issue",
    )

    result = reset_mock_demo_tenant(session_factory=session_factory, tenant_id="local-demo")

    assert result["verified_amazon_data"] is False
    assert "operational_issues" in result["deleted_tables"]
    assert OperationalIssueRepository(session_factory).get(tenant_id="local-demo", issue_id="reset-issue") is None
    assert len(OperationalIssueRepository(session_factory).list_issues(tenant_id="local-demo")) == 0
    with session_factory() as session:
        assert session.get(SecretRow, "secret-1") is not None
        assert session.get(ChannelConnectionRow, "connection-1").secret_ref == "db-secret://secret-1"
