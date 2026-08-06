from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from infrastructure.database.models import OperationalIssueRow, OperationalWorkItemRow, RecommendedActionRow
from infrastructure.database.session import create_session_factory, tenant_context
from infrastructure.repositories.audit import SQLAuditLogger
from infrastructure.repositories.manual_execution import ManualExecutionRepository


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_all_tenant_tables_have_postgresql_rls_and_isolation_policy() -> None:
    from commerce.adapters.amazon.secrets import SecretRow  # noqa: F401
    from infrastructure.database import models  # noqa: F401
    from infrastructure.database.session import Base

    expected_tables = {table.name for table in Base.metadata.sorted_tables if "tenant_id" in table.columns}
    engine = create_engine(os.environ["POSTGRES_RLS_URL"])
    try:
        with engine.connect() as connection:
            rls_rows = connection.execute(
                text("""
                SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
                  FROM pg_class AS c
                  JOIN pg_namespace AS n ON n.oid = c.relnamespace
                 WHERE n.nspname = current_schema()
                   AND c.relkind = 'r'
                   AND c.relname = ANY(:table_names)
            """),
                {"table_names": sorted(expected_tables)},
            ).mappings()
            policy_rows = connection.execute(
                text("""
                SELECT tablename AS table_name, policyname AS policy_name, qual, with_check
                  FROM pg_policies
                 WHERE schemaname = current_schema()
                   AND tablename = ANY(:table_names)
            """),
                {"table_names": sorted(expected_tables)},
            ).mappings()
            rls_by_table = {row["table_name"]: bool(row["rls_enabled"]) for row in rls_rows}
            policies_by_table = {row["table_name"]: row for row in policy_rows}

        assert set(rls_by_table) == expected_tables
        assert all(rls_by_table.values())
        assert set(policies_by_table) == expected_tables
        for table_name, policy in policies_by_table.items():
            assert policy["policy_name"] == f"{table_name}_tenant_isolation"
            assert "app.current_tenant_id" in str(policy["qual"])
            assert "app.current_tenant_id" in str(policy["with_check"])
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("POSTGRES_RLS_URL") or not os.getenv("POSTGRES_APP_URL"),
    reason="fixed PostgreSQL application role URLs not configured",
)
def test_fixed_postgresql_application_role_is_non_owner_nobypassrls_and_tenant_isolated() -> None:
    admin = create_engine(os.environ["POSTGRES_RLS_URL"])
    app_url = os.environ["POSTGRES_APP_URL"]
    app_role = make_url(app_url).username
    assert app_role
    suffix = uuid4().hex[:8]
    tenant_a = f"fixed-role-a-{suffix}"
    tenant_b = f"fixed-role-b-{suffix}"
    with admin.begin() as connection:
        role = (
            connection.execute(
                text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname=:role"),
                {"role": app_role},
            )
            .mappings()
            .one()
        )
        assert role == {
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolbypassrls": False,
        }
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_roles r ON r.oid=c.relowner "
                    "WHERE n.nspname=current_schema() AND c.relkind='r' AND r.rolname=:role"
                ),
                {"role": app_role},
            )
            == 0
        )
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) "
                "VALUES (:a,'Fixed Role A',:a,false),(:b,'Fixed Role B',:b,false)"
            ),
            {"a": tenant_a, "b": tenant_b},
        )
        connection.execute(
            text(
                "INSERT INTO audit_events(id,tenant_id,event_type,actor,action,risk_level,status,details) "
                "VALUES (:a,:tenant_a,'test','qa','read','low','success','{}'),"
                "(:b,:tenant_b,'test','qa','read','low','success','{}')"
            ),
            {"a": str(uuid4()), "b": str(uuid4()), "tenant_a": tenant_a, "tenant_b": tenant_b},
        )

    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            assert connection.scalar(text("SELECT current_user")) == app_role
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM audit_events WHERE tenant_id=:tenant"), {"tenant": tenant_a}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM audit_events WHERE tenant_id=:tenant"), {"tenant": tenant_b}
                )
                == 0
            )
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            connection.execute(
                text(
                    "INSERT INTO audit_events(id,tenant_id,event_type,actor,action,risk_level,status,details) "
                    "VALUES (:id,:tenant,'test','qa','cross_write','low','success','{}')"
                ),
                {"id": str(uuid4()), "tenant": tenant_b},
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_events WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
        admin.dispose()


@pytest.mark.skipif(
    not os.getenv("POSTGRES_RLS_URL") or not os.getenv("POSTGRES_APP_URL"),
    reason="fixed PostgreSQL application role URLs not configured",
)
def test_manual_execution_concurrent_reservations_use_fixed_postgresql_app_role() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    app_url = os.environ["POSTGRES_APP_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    tenant_id = f"execution-race-{suffix}"
    issue_id = str(uuid4())
    approval_id = str(uuid4())
    action_hash = "a" * 64
    now = datetime.now(UTC)
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES (:id,'Execution Race',:id,false)"),
            {"id": tenant_id},
        )
    admin_factory = create_session_factory(admin_url)
    with admin_factory.begin() as session:
        session.add(
            OperationalIssueRow(
                id=issue_id,
                tenant_id=tenant_id,
                seller_sku="SKU-RACE",
                marketplace_id="ATVPDKIKX0DER",
                issue_type="inventory_stockout_risk",
                severity="high",
                title="Concurrent execution",
                summary="Only one reservation may own the approved plan.",
                status="approved_for_manual_execution",
                dedupe_key=f"race-{suffix}",
                confidence=Decimal("1"),
                data_freshness_at=now,
                evidence=[],
                missing_data=[],
                occurrence_count=1,
                detected_at=now,
                last_detected_at=now,
                created_at=now,
                updated_at=now,
                priority_score=Decimal("10"),
            )
        )
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_requests("
                "id,tenant_id,actor_id,action,action_hash,policy_decision,policy_version,policy_reason,"
                "required_approval_count,risk_level,before_snapshot,proposed_changes,execution_plan,status,created_at"
                ") VALUES ("
                ":id,:tenant,'operator','manual_execute_recommended_action',:hash,'require_approval',"
                "'policy-engine-v1','Approved plan is immutable.',1,'high','{}','{}','{}','approved',:created_at)"
            ),
            {"id": approval_id, "tenant": tenant_id, "hash": action_hash, "created_at": now},
        )

    repository = ManualExecutionRepository(create_session_factory(app_url))
    barrier = Barrier(2)

    def reserve(idempotency_key: str):
        with tenant_context(tenant_id):
            barrier.wait(timeout=10)
            return repository.reserve(
                tenant_id=tenant_id,
                issue_id=issue_id,
                approval_request_id=approval_id,
                recommended_action_id=f"action-{suffix}",
                action_hash=action_hash,
                execution_contract_version="manual-execution-v1",
                idempotency_key=idempotency_key,
                action_type="create_replenishment_plan",
                approved_change={"inbound_quantity": 600},
                executed_by="operator-2",
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, (f"race-a-{suffix}", f"race-b-{suffix}")))
        assert sum(1 for _, created in results if created) == 1
        assert len({row.id for row, _ in results}) == 1
        with tenant_context(tenant_id):
            persisted = repository.get_by_approval(tenant_id=tenant_id, approval_request_id=approval_id)
        assert persisted is not None
        assert persisted.status == "executing"
    finally:
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM manual_execution_results WHERE tenant_id=:tenant"), {"tenant": tenant_id}
            )
            connection.execute(text("DELETE FROM approval_requests WHERE tenant_id=:tenant"), {"tenant": tenant_id})
            connection.execute(text("DELETE FROM operational_issues WHERE tenant_id=:tenant"), {"tenant": tenant_id})
            connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_postgresql_rls_blocks_cross_tenant_reads_and_writes() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_rls_{suffix}"
    password = f"Rls-{suffix}-Aa1"
    with admin.begin() as connection:
        connection.execute(text("DELETE FROM audit_events WHERE tenant_id IN ('rls-a','rls-b')"))
        connection.execute(text("DELETE FROM tenants WHERE id IN ('rls-a','rls-b')"))
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES ('rls-a','RLS A','rls-a',false),('rls-b','RLS B','rls-b',false) ON CONFLICT DO NOTHING"
            )
        )
        connection.execute(
            text(
                "INSERT INTO audit_events(id,tenant_id,event_type,actor,action,risk_level,status,details) VALUES (:a,'rls-a','test','qa','read','low','success','{}'),(:b,'rls-b','test','qa','read','low','success','{}')"
            ),
            {"a": str(uuid4()), "b": str(uuid4())},
        )
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT ON audit_events TO {role}"))
    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id','rls-a',true)"))
            assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE tenant_id='rls-a'")) >= 1
            assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE tenant_id='rls-b'")) == 0
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id','rls-a',true)"))
            connection.execute(
                text(
                    "INSERT INTO audit_events(id,tenant_id,event_type,actor,action,risk_level,status,details) VALUES (:id,'rls-b','test','qa','write','low','success','{}')"
                ),
                {"id": str(uuid4())},
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM audit_events WHERE tenant_id IN ('rls-a','rls-b')"))
            connection.execute(text("DELETE FROM tenants WHERE id IN ('rls-a','rls-b')"))
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_repository_sessions_apply_postgresql_tenant_context() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_repo_{suffix}"
    password = f"Repo-{suffix}-Aa1"
    with admin.begin() as connection:
        connection.execute(text("DELETE FROM audit_events WHERE tenant_id IN ('repo-a','repo-b')"))
        connection.execute(text("DELETE FROM tenants WHERE id IN ('repo-a','repo-b')"))
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES ('repo-a','Repo A','repo-a',false),('repo-b','Repo B','repo-b',false) ON CONFLICT DO NOTHING"
            )
        )
        connection.execute(
            text(
                "INSERT INTO audit_events(id,tenant_id,event_type,actor,action,risk_level,status,details) VALUES (:a,'repo-a','test','qa','read','low','success','{}'),(:b,'repo-b','test','qa','read','low','success','{}')"
            ),
            {"a": str(uuid4()), "b": str(uuid4())},
        )
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT ON audit_events TO {role}"))
    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    repository = SQLAuditLogger(create_session_factory(app_url))
    try:
        with tenant_context("repo-a"):
            assert len(repository.query(tenant_id="repo-a")) == 1
            assert repository.query(tenant_id="repo-b") == []
            repository.log_operation("repository_rls_write", "success", {"tenant_id": "repo-a"})
            assert len(repository.query(tenant_id="repo-a", action="repository_rls_write")) == 1
        assert repository.query(tenant_id="repo-a") == []
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM audit_events WHERE tenant_id IN ('repo-a','repo-b')"))
            connection.execute(text("DELETE FROM tenants WHERE id IN ('repo-a','repo-b')"))
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_customer_case_cluster_rls_blocks_cross_tenant_reads_and_writes() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_cluster_{suffix}"
    password = f"Cluster-{suffix}-Aa1"
    tenant_a = f"cluster-a-{suffix}"
    tenant_b = f"cluster-b-{suffix}"
    insert_cluster = text("""
        INSERT INTO customer_case_clusters(
            id,tenant_id,marketplace_id,seller_sku,root_cause_code,window_start,window_end,
            minimum_samples,sample_count,status,severity,domain,team,evidence_manifest,
            verified_amazon_data,dedupe_key,created_at,updated_at
        ) VALUES (
            :id,:tenant,'ATVPDKIKX0DER',:sku,'packaging_problem',now(),now() + interval '7 days',
            3,1,'collecting','high','inventory','supply_chain','[]',false,:dedupe,now(),now()
        )
    """)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES (:a,'Cluster A',:a,false),(:b,'Cluster B',:b,false)"
            ),
            {"a": tenant_a, "b": tenant_b},
        )
        connection.execute(insert_cluster, {"id": str(uuid4()), "tenant": tenant_a, "sku": "SKU-A", "dedupe": "a" * 64})
        connection.execute(insert_cluster, {"id": str(uuid4()), "tenant": tenant_b, "sku": "SKU-B", "dedupe": "b" * 64})
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT ON customer_case_clusters TO {role}"))
    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM customer_case_clusters WHERE tenant_id=:tenant"), {"tenant": tenant_a}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM customer_case_clusters WHERE tenant_id=:tenant"), {"tenant": tenant_b}
                )
                == 0
            )
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            connection.execute(
                insert_cluster, {"id": str(uuid4()), "tenant": tenant_b, "sku": "SKU-CROSS", "dedupe": "c" * 64}
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM customer_case_clusters WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_agent_runs_rls_and_idempotency_constraint() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_agent_{suffix}"
    password = f"Agent-{suffix}-Aa1"
    tenant_a = f"agent-a-{suffix}"
    tenant_b = f"agent-b-{suffix}"
    workflow = "operations_brief_analysis"
    idempotency_key = f"idem-{suffix}"

    insert_run = text("""
        INSERT INTO agent_runs(
            id,tenant_id,actor_id,workflow_type,correlation_id,idempotency_key,status,prompt_version,
            token_usage,estimated_cost,model_usage,evidence_manifest,analysis_result,retry_count,state
        ) VALUES (
            :id,:tenant_id,'operator',:workflow_type,:correlation_id,:idempotency_key,'received','operations-brief-v1',
            0,0,'{}','{}','{}',0,'{}'
        )
    """)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES (:a,'Agent A',:a,false),(:b,'Agent B',:b,false)"
            ),
            {"a": tenant_a, "b": tenant_b},
        )
        connection.execute(
            insert_run,
            {
                "id": str(uuid4()),
                "tenant_id": tenant_a,
                "workflow_type": workflow,
                "correlation_id": f"corr-a-{suffix}",
                "idempotency_key": idempotency_key,
            },
        )
        connection.execute(
            insert_run,
            {
                "id": str(uuid4()),
                "tenant_id": tenant_b,
                "workflow_type": workflow,
                "correlation_id": f"corr-b-{suffix}",
                "idempotency_key": idempotency_key,
            },
        )
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT, UPDATE ON agent_runs TO {role}"))

    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            assert (
                connection.scalar(text("SELECT count(*) FROM agent_runs WHERE tenant_id=:tenant"), {"tenant": tenant_a})
                == 1
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM agent_runs WHERE tenant_id=:tenant"), {"tenant": tenant_b})
                == 0
            )
            updated = connection.execute(
                text("UPDATE agent_runs SET status='failed' WHERE tenant_id=:tenant"), {"tenant": tenant_b}
            )
            assert updated.rowcount == 0
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            connection.execute(
                insert_run,
                {
                    "id": str(uuid4()),
                    "tenant_id": tenant_b,
                    "workflow_type": "customer_case_analysis",
                    "correlation_id": f"cross-{suffix}",
                    "idempotency_key": f"cross-{suffix}",
                },
            )
        with pytest.raises(DBAPIError), admin.begin() as connection:
            connection.execute(
                insert_run,
                {
                    "id": str(uuid4()),
                    "tenant_id": tenant_a,
                    "workflow_type": workflow,
                    "correlation_id": f"duplicate-{suffix}",
                    "idempotency_key": idempotency_key,
                },
            )
        with admin.begin() as connection:
            connection.execute(
                insert_run,
                {
                    "id": str(uuid4()),
                    "tenant_id": tenant_a,
                    "workflow_type": "customer_case_analysis",
                    "correlation_id": f"other-workflow-{suffix}",
                    "idempotency_key": idempotency_key,
                },
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM agent_runs WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_approval_decisions_rls_and_sequence_constraint() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_approval_{suffix}"
    password = f"Approval-{suffix}-Aa1"
    tenant_a = f"approval-a-{suffix}"
    tenant_b = f"approval-b-{suffix}"
    request_a = str(uuid4())
    request_b = str(uuid4())
    insert_request = text("""
        INSERT INTO approval_requests(
            id,tenant_id,actor_id,action,action_hash,policy_decision,policy_version,
            policy_reason,required_approval_count,risk_level,before_snapshot,proposed_changes,execution_plan,status,created_at
        ) VALUES (
            :id,:tenant,'requester','delete_listing',:hash,'require_approval','policy-engine-v1',
            'Critical commerce write requires explicit approval.',2,'critical','{}','{}','{}','pending',now()
        )
    """)
    insert_decision = text("""
        INSERT INTO approval_decisions(
            id,tenant_id,approval_request_id,sequence,stage,decision,actor_id,reason,created_at
        ) VALUES (:id,:tenant,:request_id,:sequence,'first_approval','approved','approver','reviewed',now())
    """)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES (:a,'Approval A',:a,false),(:b,'Approval B',:b,false)"
            ),
            {"a": tenant_a, "b": tenant_b},
        )
        connection.execute(insert_request, {"id": request_a, "tenant": tenant_a, "hash": "a" * 64})
        connection.execute(insert_request, {"id": request_b, "tenant": tenant_b, "hash": "b" * 64})
        connection.execute(
            insert_decision, {"id": str(uuid4()), "tenant": tenant_a, "request_id": request_a, "sequence": 1}
        )
        connection.execute(
            insert_decision, {"id": str(uuid4()), "tenant": tenant_b, "request_id": request_b, "sequence": 1}
        )
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT ON approval_requests, approval_decisions TO {role}"))

    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM approval_decisions WHERE tenant_id=:tenant"), {"tenant": tenant_a}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM approval_decisions WHERE tenant_id=:tenant"), {"tenant": tenant_b}
                )
                == 0
            )
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            connection.execute(
                insert_decision, {"id": str(uuid4()), "tenant": tenant_b, "request_id": request_b, "sequence": 2}
            )
        with pytest.raises(DBAPIError), admin.begin() as connection:
            connection.execute(
                insert_decision, {"id": str(uuid4()), "tenant": tenant_a, "request_id": request_a, "sequence": 1}
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM approval_decisions WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(
                text("DELETE FROM approval_requests WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_operational_work_items_rls_blocks_cross_tenant_access_and_preserves_unique_source() -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_work_item_{suffix}"
    password = f"WorkItem-{suffix}-Aa1"
    tenant_a = f"work-a-{suffix}"
    tenant_b = f"work-b-{suffix}"
    now = datetime.now(UTC)
    issue_a = f"issue-a-{suffix}"
    issue_b = f"issue-b-{suffix}"
    action_a = f"action-a-{suffix}"
    action_b = f"action-b-{suffix}"
    work_a = f"work-a-{suffix}"
    insert_work_item = text("""
        INSERT INTO operational_work_items(
            id,tenant_id,source_type,source_id,operational_issue_id,recommended_action_id,domain,team,seller_sku,
            marketplace_id,severity,priority_score,title,description,status,due_at,sla_policy_version,escalation_level,
            expected_outcome,baseline_metrics,result_metrics,verification_status,verification_attempts,evidence_manifest,action_hash,created_at,updated_at
        ) VALUES (:id,:tenant,:source_type,:source_id,:issue_id,:action_id,'inventory','supply_chain',:sku,'ATVPDKIKX0DER','high',10,'Task','Review','open',:due_at,'ops-sla-v1',0,'Verify','{}','{}','pending',0,'[]',:hash,:now,:now)
    """)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES (:a,'Work A',:a,false),(:b,'Work B',:b,false)"
            ),
            {"a": tenant_a, "b": tenant_b},
        )
    admin_factory = create_session_factory(admin_url)
    with admin_factory.begin() as session:
        for tenant_id, issue_id, action_id, _work_id, sku in (
            (tenant_a, issue_a, action_a, work_a, "SKU-A"),
            (tenant_b, issue_b, action_b, f"work-b-{suffix}", "SKU-B"),
        ):
            session.add(
                OperationalIssueRow(
                    id=issue_id,
                    tenant_id=tenant_id,
                    seller_sku=sku,
                    marketplace_id="ATVPDKIKX0DER",
                    issue_type="inventory_stockout_risk",
                    severity="high",
                    title="Issue",
                    summary="Review",
                    status="open",
                    dedupe_key=f"dedupe-{tenant_id}",
                    confidence=Decimal("0.9"),
                    data_freshness_at=now,
                    evidence=[],
                    missing_data=[],
                    occurrence_count=1,
                    detected_at=now,
                    last_detected_at=now,
                    created_at=now,
                    updated_at=now,
                    priority_score=Decimal("10"),
                )
            )
            session.add(
                RecommendedActionRow(
                    id=action_id,
                    tenant_id=tenant_id,
                    issue_id=issue_id,
                    action_type="review_inventory",
                    title="Review",
                    description="Review",
                    execution_mode="single_approval",
                    risk_level="high",
                    status="proposed",
                    parameters={},
                    evidence=[],
                    expected_outcome="Verify",
                    confidence=Decimal("0.9"),
                    automation_eligible=False,
                    dedupe_key=f"action-{tenant_id}",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for tenant_id, issue_id, action_id, work_id, sku in (
            (tenant_a, issue_a, action_a, work_a, "SKU-A"),
            (tenant_b, issue_b, action_b, f"work-b-{suffix}", "SKU-B"),
        ):
            session.add(
                OperationalWorkItemRow(
                    id=work_id,
                    tenant_id=tenant_id,
                    source_type="recommended_action",
                    source_id=action_id,
                    operational_issue_id=issue_id,
                    recommended_action_id=action_id,
                    domain="inventory",
                    team="supply_chain",
                    seller_sku=sku,
                    marketplace_id="ATVPDKIKX0DER",
                    severity="high",
                    priority_score=Decimal("10"),
                    title="Task",
                    description="Review",
                    status="open",
                    due_at=now,
                    sla_policy_version="ops-sla-v1",
                    escalation_level=0,
                    expected_outcome="Verify",
                    baseline_metrics={},
                    result_metrics={},
                    verification_status="pending",
                    verification_attempts=0,
                    evidence_manifest=[],
                    action_hash="a" * 64,
                    created_at=now,
                    updated_at=now,
                )
            )
    with admin.begin() as connection:
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT, UPDATE ON operational_work_items TO {role}"))
        connection.execute(text(f"GRANT SELECT ON operational_issues, recommended_actions TO {role}"))

    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM operational_work_items WHERE tenant_id=:tenant"), {"tenant": tenant_a}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM operational_work_items WHERE tenant_id=:tenant"), {"tenant": tenant_b}
                )
                == 0
            )
            assert (
                connection.execute(
                    text("UPDATE operational_work_items SET status='in_progress' WHERE tenant_id=:tenant"),
                    {"tenant": tenant_b},
                ).rowcount
                == 0
            )
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            connection.execute(
                insert_work_item,
                {
                    "id": str(uuid4()),
                    "tenant": tenant_b,
                    "source_type": "recommended_action",
                    "source_id": f"cross-{suffix}",
                    "issue_id": issue_b,
                    "action_id": action_b,
                    "sku": "SKU-B",
                    "due_at": now,
                    "hash": "b" * 64,
                    "now": now,
                },
            )
        with pytest.raises(DBAPIError), admin.begin() as connection:
            connection.execute(
                insert_work_item,
                {
                    "id": str(uuid4()),
                    "tenant": tenant_a,
                    "source_type": "recommended_action",
                    "source_id": action_a,
                    "issue_id": issue_a,
                    "action_id": action_a,
                    "sku": "SKU-A",
                    "due_at": now,
                    "hash": "c" * 64,
                    "now": now,
                },
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM operational_work_items WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(
                text("DELETE FROM recommended_actions WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(
                text("DELETE FROM operational_issues WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.mark.skipif(not os.getenv("POSTGRES_RLS_URL"), reason="real PostgreSQL RLS URL not configured")
def test_operations_learning_business_context_rls_blocks_cross_tenant_access() -> None:
    admin_url = os.environ["POSTGRES_RLS_URL"]
    admin = create_engine(admin_url)
    suffix = uuid4().hex[:8]
    role = f"omnisell_learning_{suffix}"
    password = f"Learning-{suffix}-Aa1"
    tenant_a = f"learn-a-{suffix}"
    tenant_b = f"learn-b-{suffix}"
    now = datetime.now(UTC)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants(id,name,slug,amazon_writes_enabled) VALUES (:a,'Learn A',:a,false),(:b,'Learn B',:b,false)"
            ),
            {"a": tenant_a, "b": tenant_b},
        )
        for tenant_id in (tenant_a, tenant_b):
            connection.execute(
                text("""
                INSERT INTO business_context_events(
                    id,tenant_id,marketplace_id,seller_sku,context_type,starts_at,ends_at,details,
                    source,verified_amazon_data,dedupe_key,created_at
                ) VALUES (:id,:tenant,'ATVPDKIKX0DER','SKU-1','planned_promotion',:start,:end,'{}','manual',false,:dedupe,:start)
            """),
                {
                    "id": str(uuid4()),
                    "tenant": tenant_id,
                    "start": now,
                    "end": now + timedelta(days=1),
                    "dedupe": uuid4().hex,
                },
            )
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT SELECT, INSERT ON business_context_events TO {role}"))
    app_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM business_context_events WHERE tenant_id=:tenant"), {"tenant": tenant_a}
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM business_context_events WHERE tenant_id=:tenant"), {"tenant": tenant_b}
                )
                == 0
            )
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {"tenant": tenant_a})
            connection.execute(
                text("""
                    INSERT INTO business_context_events(
                        id,tenant_id,marketplace_id,context_type,starts_at,ends_at,details,source,
                        verified_amazon_data,dedupe_key,created_at
                    ) VALUES (:id,:tenant,'ATVPDKIKX0DER','seasonality',:start,:end,'{}','manual',false,:dedupe,:start)
                """),
                {
                    "id": str(uuid4()),
                    "tenant": tenant_b,
                    "start": now,
                    "end": now + timedelta(days=1),
                    "dedupe": uuid4().hex,
                },
            )
    finally:
        app.dispose()
        with admin.begin() as connection:
            connection.execute(
                text("DELETE FROM business_context_events WHERE tenant_id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b}
            )
            connection.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant_a, "b": tenant_b})
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
