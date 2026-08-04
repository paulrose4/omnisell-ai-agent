"""Create the immutable OmniSell schema baseline.

Revision ID: 0023
Revises: None
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = None
branch_labels = None
depends_on = None

FROZEN_RLS_TABLES = (
    "action_attribution_plans",
    "action_execution_attempts",
    "action_execution_authorizations",
    "action_outcomes",
    "advertising_budget_diagnostics",
    "advertising_daily_snapshots",
    "agent_runs",
    "amazon_operational_signals",
    "approval_decisions",
    "approval_requests",
    "audit_events",
    "business_context_events",
    "buyer_message_tags",
    "channel_connections",
    "customer_case_cluster_members",
    "customer_case_clusters",
    "customer_case_events",
    "customer_cases",
    "daily_operations_briefs",
    "data_import_jobs",
    "durable_tasks",
    "encrypted_secrets",
    "inbox_events",
    "intelligence_calibration_proposals",
    "intelligence_rule_versions",
    "inventory_health_snapshots",
    "inventory_policies",
    "inventory_positions",
    "issue_feedback",
    "listing_optimization_experiments",
    "listing_quality_snapshots",
    "listings",
    "manual_execution_results",
    "memberships",
    "missed_issue_reports",
    "operational_issues",
    "operational_work_items",
    "operator_activity_events",
    "order_lines",
    "orders",
    "outbox_events",
    "price_recommendations",
    "products",
    "recommended_actions",
    "reconciliation_differences",
    "reconciliation_runs",
    "return_events",
    "review_observations",
    "search_term_performance_snapshots",
    "sku_cost_profiles",
    "sku_profit_snapshots",
    "sku_voc_summaries",
    "sop_playbooks",
    "sync_cursors",
    "variants",
)


def _enable_postgresql_rls() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table_name in FROZEN_RLS_TABLES:
        policy_name = f"{table_name}_tenant_isolation"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{policy_name}" ON "{table_name}"
                USING (tenant_id = current_setting('app.current_tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))'''
        )


def _drop_postgresql_rls_policies() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table_name in reversed(FROZEN_RLS_TABLES):
        policy_name = f"{table_name}_tenant_isolation"
        op.execute(f'DROP POLICY "{policy_name}" ON "{table_name}"')


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_tenant_id"), "audit_events", ["tenant_id"], unique=False)
    op.create_index("ix_audit_tenant_created", "audit_events", ["tenant_id", "created_at"], unique=False)
    op.create_table(
        "encrypted_secrets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_encrypted_secrets_tenant_id"), "encrypted_secrets", ["tenant_id"], unique=False)
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("amazon_writes_enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("oidc_subject", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("oidc_subject"),
    )
    op.create_table(
        "advertising_daily_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("campaign_id", sa.String(length=200), nullable=True),
        sa.Column("ad_group_id", sa.String(length=200), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("spend", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("attributed_sales", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("total_sales", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("budget", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("orders", sa.Integer(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "entity_type", "entity_id", "period_start", name="uq_ad_daily_snapshot_period"
        ),
    )
    op.create_index(
        "ix_ad_daily_queue",
        "advertising_daily_snapshots",
        ["tenant_id", "marketplace_id", "seller_sku", "period_end"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advertising_daily_snapshots_tenant_id"), "advertising_daily_snapshots", ["tenant_id"], unique=False
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("workflow_type", sa.String(length=100), nullable=False),
        sa.Column("agent_type", sa.String(length=100), nullable=True),
        sa.Column("schema_version", sa.String(length=50), nullable=True),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("model_usage", sa.JSON(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("analysis_result", sa.JSON(), nullable=False),
        sa.Column("error_category", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "workflow_type", "idempotency_key", name="uq_agent_run_tenant_workflow_idempotency"
        ),
    )
    op.create_index(op.f("ix_agent_runs_correlation_id"), "agent_runs", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_tenant_id"), "agent_runs", ["tenant_id"], unique=False)
    op.create_index(
        "ix_agent_runs_tenant_workflow_created",
        "agent_runs",
        ["tenant_id", "workflow_type", "created_at"],
        unique=False,
    )
    op.create_table(
        "amazon_operational_signals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("signal_type", sa.String(length=60), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freshness_seconds", sa.Integer(), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("raw_status_code", sa.String(length=100), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "signal_type", "resource_id", "observed_at", name="uq_amazon_signal_observation"
        ),
    )
    op.create_index(
        op.f("ix_amazon_operational_signals_tenant_id"), "amazon_operational_signals", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_amazon_signal_queue",
        "amazon_operational_signals",
        ["tenant_id", "severity", "signal_type", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_amazon_signal_resource",
        "amazon_operational_signals",
        ["tenant_id", "marketplace_id", "resource_id"],
        unique=False,
    )
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_decision", sa.String(length=30), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_reason", sa.Text(), nullable=False),
        sa.Column("required_approval_count", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("proposed_changes", sa.JSON(), nullable=False),
        sa.Column("execution_contract_version", sa.String(length=64), nullable=True),
        sa.Column("execution_plan", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("approver_id", sa.String(length=255), nullable=True),
        sa.Column("first_approver_id", sa.String(length=255), nullable=True),
        sa.Column("first_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_approval_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.CheckConstraint(
            "(policy_decision = 'allow' AND risk_level = 'low' AND status = 'auto_approved' AND required_approval_count = 0) OR (policy_decision = 'require_approval' AND ((risk_level = 'critical' AND required_approval_count = 2) OR (risk_level <> 'critical' AND required_approval_count = 1)) AND status IN ('pending', 'awaiting_second_approval', 'approved', 'rejected', 'expired'))",
            name="ck_approval_requests_policy_status",
        ),
        sa.CheckConstraint(
            "policy_decision IN ('allow', 'require_approval')", name="ck_approval_requests_policy_decision"
        ),
        sa.CheckConstraint(
            "execution_contract_version IS NULL OR length(trim(execution_contract_version)) > 0",
            name="ck_approval_requests_execution_contract_version",
        ),
        sa.CheckConstraint("length(trim(policy_reason)) > 0", name="ck_approval_requests_policy_reason"),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')", name="ck_approval_requests_risk_level"
        ),
        sa.CheckConstraint("required_approval_count IN (0, 1, 2)", name="ck_approval_requests_required_count"),
        sa.CheckConstraint("length(trim(policy_version)) > 0", name="ck_approval_requests_policy_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_approval_requests_tenant_id"), "approval_requests", ["tenant_id"], unique=False)
    op.create_index(
        "ix_approval_requests_tenant_status_created",
        "approval_requests",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "business_context_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=True),
        sa.Column("context_type", sa.String(length=60), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_business_context_dedupe"),
    )
    op.create_index(
        op.f("ix_business_context_events_tenant_id"), "business_context_events", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_business_context_window",
        "business_context_events",
        ["tenant_id", "marketplace_id", "seller_sku", "starts_at", "ends_at"],
        unique=False,
    )
    op.create_table(
        "buyer_message_tags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("external_message_id", sa.String(length=200), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("tag", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_message_id", "tag", name="uq_buyer_message_tag"),
    )
    op.create_index(
        "ix_buyer_message_tag_sku",
        "buyer_message_tags",
        ["tenant_id", "marketplace_id", "seller_sku", "observed_at"],
        unique=False,
    )
    op.create_index(op.f("ix_buyer_message_tags_tenant_id"), "buyer_message_tags", ["tenant_id"], unique=False)
    op.create_table(
        "channel_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("selling_partner_id", sa.String(length=100), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=False),
        sa.Column("marketplace_ids", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", "selling_partner_id", "region", name="uq_channel_seller_region"),
    )
    op.create_index(op.f("ix_channel_connections_tenant_id"), "channel_connections", ["tenant_id"], unique=False)
    op.create_table(
        "customer_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("external_case_id", sa.String(length=255), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("external_order_id", sa.String(length=100), nullable=True),
        sa.Column("seller_sku", sa.String(length=100), nullable=True),
        sa.Column("asin", sa.String(length=100), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("intent", sa.String(length=100), nullable=False),
        sa.Column("sentiment", sa.String(length=30), nullable=False),
        sa.Column("urgency", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("priority_score", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("customer_message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("assignee_id", sa.String(length=255), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_reply", sa.Text(), nullable=True),
        sa.Column("approved_reply", sa.Text(), nullable=True),
        sa.Column("actual_reply", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("missing_data", sa.JSON(), nullable=False),
        sa.Column("policy_checks", sa.JSON(), nullable=False),
        sa.Column("playbook_key", sa.String(length=100), nullable=True),
        sa.Column("playbook_version", sa.String(length=40), nullable=True),
        sa.Column("approval_request_id", sa.String(length=36), nullable=True),
        sa.Column("resolution_code", sa.String(length=100), nullable=True),
        sa.Column("root_cause_code", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "source", "external_case_id", name="uq_customer_case_external"),
    )
    op.create_index("ix_customer_case_order", "customer_cases", ["tenant_id", "external_order_id"], unique=False)
    op.create_index(
        "ix_customer_case_queue",
        "customer_cases",
        ["tenant_id", "status", "priority_score", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_customer_case_sku", "customer_cases", ["tenant_id", "marketplace_id", "seller_sku"], unique=False
    )
    op.create_index(
        op.f("ix_customer_cases_approval_request_id"), "customer_cases", ["approval_request_id"], unique=False
    )
    op.create_index(op.f("ix_customer_cases_tenant_id"), "customer_cases", ["tenant_id"], unique=False)
    op.create_table(
        "daily_operations_briefs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issue_counts", sa.JSON(), nullable=False),
        sa.Column("domain_counts", sa.JSON(), nullable=False),
        sa.Column("total_open_issues", sa.Integer(), nullable=False),
        sa.Column("data_quality_issue_count", sa.Integer(), nullable=False),
        sa.Column("impact_by_currency", sa.JSON(), nullable=False),
        sa.Column("unallocated_impact_issue_count", sa.Integer(), nullable=False),
        sa.Column("priority_issue_ids", sa.JSON(), nullable=False),
        sa.Column("priority_action_ids", sa.JSON(), nullable=False),
        sa.Column("sku_health", sa.JSON(), nullable=False),
        sa.Column("source_manifest", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "brief_date", "timezone", name="uq_daily_brief_tenant_date_timezone"),
    )
    op.create_index("ix_daily_brief_latest", "daily_operations_briefs", ["tenant_id", "generated_at"], unique=False)
    op.create_index(
        op.f("ix_daily_operations_briefs_tenant_id"), "daily_operations_briefs", ["tenant_id"], unique=False
    )
    op.create_table(
        "data_import_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("import_type", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("valid_rows", sa.Integer(), nullable=False),
        sa.Column("committed_rows", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "import_type", "idempotency_key", name="uq_import_tenant_type_idempotency"),
    )
    op.create_index(op.f("ix_data_import_jobs_tenant_id"), "data_import_jobs", ["tenant_id"], unique=False)
    op.create_table(
        "durable_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("queue_name", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replayed_from_task_id", sa.String(length=36), nullable=True),
        sa.Column("replayed_to_task_id", sa.String(length=36), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=255), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_task_tenant_idempotency"),
    )
    op.create_index(op.f("ix_durable_tasks_correlation_id"), "durable_tasks", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_durable_tasks_tenant_id"), "durable_tasks", ["tenant_id"], unique=False)
    op.create_index(
        "ix_task_tenant_status_created", "durable_tasks", ["tenant_id", "status", "created_at"], unique=False
    )
    op.create_table(
        "inbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", "external_event_id", name="uq_inbox_external_event"),
    )
    op.create_index(op.f("ix_inbox_events_tenant_id"), "inbox_events", ["tenant_id"], unique=False)
    op.create_table(
        "intelligence_calibration_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("rule_key", sa.String(length=160), nullable=False),
        sa.Column("base_version", sa.String(length=100), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("current_parameters", sa.JSON(), nullable=False),
        sa.Column("candidate_parameters", sa.JSON(), nullable=False),
        sa.Column("replay_metrics", sa.JSON(), nullable=False),
        sa.Column("label_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("proposed_by", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "candidate_hash", name="uq_calibration_candidate_hash"),
    )
    op.create_index(
        "ix_calibration_queue",
        "intelligence_calibration_proposals",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_intelligence_calibration_proposals_tenant_id"),
        "intelligence_calibration_proposals",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "intelligence_rule_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("rule_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("evaluation_metrics", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("parent_version", sa.String(length=100), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("shadow_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shadow_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "rule_key", "version", name="uq_rule_version_scope"),
    )
    op.create_index(
        op.f("ix_intelligence_rule_versions_tenant_id"), "intelligence_rule_versions", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_rule_version_active", "intelligence_rule_versions", ["tenant_id", "rule_key", "status"], unique=False
    )
    op.create_table(
        "inventory_health_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("inbound", sa.Integer(), nullable=False),
        sa.Column("in_transit", sa.Integer(), nullable=False),
        sa.Column("unsellable", sa.Integer(), nullable=False),
        sa.Column("inventory_position", sa.Integer(), nullable=False),
        sa.Column("weighted_daily_sales", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("days_of_supply", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("expected_stockout_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_lead_time_days", sa.Integer(), nullable=False),
        sa.Column("safety_stock_units", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("reorder_point", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("recommended_reorder_quantity", sa.Integer(), nullable=False),
        sa.Column("excess_units", sa.Integer(), nullable=False),
        sa.Column("recommended_shipping_mode", sa.String(length=30), nullable=False),
        sa.Column("inventory_health", sa.String(length=50), nullable=False),
        sa.Column("stockout_risk", sa.String(length=20), nullable=False),
        sa.Column("excess_risk", sa.String(length=20), nullable=False),
        sa.Column("forecast_confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("missing_data", sa.JSON(), nullable=False),
        sa.Column("data_warnings", sa.JSON(), nullable=False),
        sa.Column("degraded_reason", sa.String(length=200), nullable=True),
        sa.Column("automation_eligible", sa.Boolean(), nullable=False),
        sa.Column("inventory_source", sa.String(length=40), nullable=False),
        sa.Column("inventory_collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_version", sa.String(length=100), nullable=False),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("estimated_revenue_at_risk", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("estimated_profit_at_risk", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("estimated_extra_logistics_cost", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_health_scope_calculated",
        "inventory_health_snapshots",
        ["tenant_id", "marketplace_id", "seller_sku", "calculated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_health_snapshots_tenant_id"), "inventory_health_snapshots", ["tenant_id"], unique=False
    )
    op.create_table(
        "inventory_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("supplier_lead_time_days", sa.Integer(), nullable=False),
        sa.Column("production_lead_time_days", sa.Integer(), nullable=False),
        sa.Column("shipping_lead_time_days", sa.Integer(), nullable=False),
        sa.Column("amazon_receiving_days", sa.Integer(), nullable=False),
        sa.Column("safety_stock_days", sa.Integer(), nullable=False),
        sa.Column("target_coverage_days", sa.Integer(), nullable=False),
        sa.Column("minimum_order_quantity", sa.Integer(), nullable=False),
        sa.Column("case_pack_quantity", sa.Integer(), nullable=False),
        sa.Column("preferred_shipping_mode", sa.String(length=30), nullable=False),
        sa.Column("sales_weight_7d", sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column("sales_weight_14d", sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column("sales_weight_30d", sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "marketplace_id", "seller_sku", "version", name="uq_inventory_policy_scope_version"
        ),
    )
    op.create_index(op.f("ix_inventory_policies_tenant_id"), "inventory_policies", ["tenant_id"], unique=False)
    op.create_index(
        "ix_inventory_policy_active",
        "inventory_policies",
        ["tenant_id", "marketplace_id", "seller_sku", "effective_from", "effective_to"],
        unique=False,
    )
    op.create_table(
        "listing_optimization_experiments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("before_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("after_snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("proposed_changes", sa.JSON(), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("observation_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("approval_request_id", sa.String(length=36), nullable=True),
        sa.Column("effect_metrics", sa.JSON(), nullable=False),
        sa.Column("evaluation_reasons", sa.JSON(), nullable=False),
        sa.Column("rollback_recommended", sa.Boolean(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_status", sa.String(length=30), nullable=False),
        sa.Column("actual_changes", sa.JSON(), nullable=False),
        sa.Column("execution_evidence", sa.JSON(), nullable=False),
        sa.Column("executed_by", sa.String(length=255), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_notes", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(length=30), nullable=True),
        sa.Column("decision_by", sa.String(length=255), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "action_hash", name="uq_listing_experiment_tenant_action_hash"),
    )
    op.create_index(
        "ix_listing_experiment_sku",
        "listing_optimization_experiments",
        ["tenant_id", "marketplace_id", "seller_sku"],
        unique=False,
    )
    op.create_index(
        "ix_listing_experiment_status",
        "listing_optimization_experiments",
        ["tenant_id", "status", "observation_end"],
        unique=False,
    )
    op.create_index(
        op.f("ix_listing_optimization_experiments_approval_request_id"),
        "listing_optimization_experiments",
        ["approval_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_listing_optimization_experiments_tenant_id"),
        "listing_optimization_experiments",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "listing_quality_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("bullet_points", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("search_terms", sa.JSON(), nullable=False),
        sa.Column("listing_status", sa.String(length=50), nullable=False),
        sa.Column("quality_score", sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column("quality_status", sa.String(length=20), nullable=False),
        sa.Column("section_scores", sa.JSON(), nullable=False),
        sa.Column("quality_issues", sa.JSON(), nullable=False),
        sa.Column("recommendations", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("missing_data", sa.JSON(), nullable=False),
        sa.Column("degraded_reason", sa.String(length=255), nullable=True),
        sa.Column("automation_eligible", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_version", sa.String(length=50), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "listing_id", "content_version", "calculated_at", name="uq_listing_quality_version_time"
        ),
    )
    op.create_index(
        "ix_listing_quality_latest",
        "listing_quality_snapshots",
        ["tenant_id", "marketplace_id", "seller_sku", "calculated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_listing_quality_snapshots_tenant_id"), "listing_quality_snapshots", ["tenant_id"], unique=False
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )
    op.create_index(op.f("ix_memberships_tenant_id"), "memberships", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_table(
        "missed_issue_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("issue_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_missed_issue_queue", "missed_issue_reports", ["tenant_id", "status", "severity", "created_at"], unique=False
    )
    op.create_index(op.f("ix_missed_issue_reports_tenant_id"), "missed_issue_reports", ["tenant_id"], unique=False)
    op.create_index(
        "ix_missed_issue_scope",
        "missed_issue_reports",
        ["tenant_id", "marketplace_id", "seller_sku", "issue_type"],
        unique=False,
    )
    op.create_table(
        "operational_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("issue_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("data_freshness_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("missing_data", sa.JSON(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("estimated_revenue_impact", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("estimated_profit_impact", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("estimated_cost_impact", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("degraded_reason", sa.String(length=255), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignee_id", sa.String(length=255), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_code", sa.String(length=100), nullable=True),
        sa.Column("execution_result_id", sa.String(length=36), nullable=True),
        sa.Column("reopened_count", sa.Integer(), nullable=False),
        sa.Column("priority_score", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_operational_issue_tenant_dedupe"),
    )
    op.create_index(
        "ix_operational_issue_queue",
        "operational_issues",
        ["tenant_id", "status", "severity", "last_detected_at"],
        unique=False,
    )
    op.create_index(
        "ix_operational_issue_sku", "operational_issues", ["tenant_id", "marketplace_id", "seller_sku"], unique=False
    )
    op.create_index(op.f("ix_operational_issues_tenant_id"), "operational_issues", ["tenant_id"], unique=False)
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_outbox_tenant_dedupe"),
    )
    op.create_index(
        "ix_outbox_dispatch", "outbox_events", ["tenant_id", "status", "next_retry_at", "created_at"], unique=False
    )
    op.create_index(op.f("ix_outbox_events_tenant_id"), "outbox_events", ["tenant_id"], unique=False)
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at", "created_at"], unique=False)
    op.create_table(
        "price_recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("profit_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("inventory_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("cost_profile_id", sa.String(length=36), nullable=False),
        sa.Column("current_price", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("recommended_price", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("candidate_source", sa.String(length=40), nullable=False),
        sa.Column("break_even_floor", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("minimum_margin_floor", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("minimum_margin_rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("projected_unit_profit", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("projected_margin", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("change_rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("policy_decision", sa.String(length=30), nullable=False),
        sa.Column("policy_reasons", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("automation_eligible", sa.Boolean(), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("missing_data", sa.JSON(), nullable=False),
        sa.Column("degraded_reason", sa.String(length=255), nullable=True),
        sa.Column("approval_request_id", sa.String(length=36), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "action_hash", name="uq_price_recommendation_tenant_action_hash"),
    )
    op.create_index(
        "ix_price_recommendation_queue",
        "price_recommendations",
        ["tenant_id", "status", "risk_level", "calculated_at"],
        unique=False,
    )
    op.create_index(
        "ix_price_recommendation_sku",
        "price_recommendations",
        ["tenant_id", "marketplace_id", "seller_sku"],
        unique=False,
    )
    op.create_index(
        op.f("ix_price_recommendations_approval_request_id"),
        "price_recommendations",
        ["approval_request_id"],
        unique=False,
    )
    op.create_index(op.f("ix_price_recommendations_tenant_id"), "price_recommendations", ["tenant_id"], unique=False)
    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_products_tenant_id"), "products", ["tenant_id"], unique=False)
    op.create_table(
        "return_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("external_return_id", sa.String(length=200), nullable=False),
        sa.Column("external_order_id", sa.String(length=200), nullable=True),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("return_amount", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_return_id", name="uq_return_event_external"),
    )
    op.create_index(
        "ix_return_event_sku",
        "return_events",
        ["tenant_id", "marketplace_id", "seller_sku", "observed_at"],
        unique=False,
    )
    op.create_index(op.f("ix_return_events_tenant_id"), "return_events", ["tenant_id"], unique=False)
    op.create_table(
        "review_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("external_review_id", sa.String(length=200), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("review_text", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_review_id", name="uq_review_observation_external"),
    )
    op.create_index(
        "ix_review_observation_sku",
        "review_observations",
        ["tenant_id", "marketplace_id", "seller_sku", "observed_at"],
        unique=False,
    )
    op.create_index(op.f("ix_review_observations_tenant_id"), "review_observations", ["tenant_id"], unique=False)
    op.create_table(
        "search_term_performance_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("campaign_id", sa.String(length=200), nullable=False),
        sa.Column("ad_group_id", sa.String(length=200), nullable=True),
        sa.Column("search_term", sa.String(length=500), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("spend", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("attributed_sales", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("orders", sa.Integer(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "campaign_id", "search_term", "period_start", name="uq_search_term_snapshot_period"
        ),
    )
    op.create_index(
        op.f("ix_search_term_performance_snapshots_tenant_id"),
        "search_term_performance_snapshots",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_term_queue",
        "search_term_performance_snapshots",
        ["tenant_id", "marketplace_id", "seller_sku", "period_end"],
        unique=False,
    )
    op.create_table(
        "sku_cost_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("product_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("domestic_shipping_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("international_freight_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("duty_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("prep_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("packaging_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("estimated_referral_fee_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("estimated_fulfillment_fee", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("estimated_storage_cost", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("estimated_return_loss_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("exchange_rate_buffer_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("target_margin_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("minimum_margin_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("import_job_id", sa.String(length=36), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "marketplace_id", "seller_sku", "version", name="uq_cost_profile_scope_version"
        ),
    )
    op.create_index(
        "ix_cost_profile_active",
        "sku_cost_profiles",
        ["tenant_id", "marketplace_id", "seller_sku", "effective_from", "effective_to"],
        unique=False,
    )
    op.create_index(op.f("ix_sku_cost_profiles_import_job_id"), "sku_cost_profiles", ["import_job_id"], unique=False)
    op.create_index(op.f("ix_sku_cost_profiles_tenant_id"), "sku_cost_profiles", ["tenant_id"], unique=False)
    op.create_table(
        "sku_profit_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("refunds", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("promotions", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("referral_fees", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("fulfillment_fees", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("storage_cost", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("advertising_cost", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("landed_cost", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("return_loss", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("other_variable_cost", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("contribution_profit", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("contribution_margin", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("break_even_price", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("break_even_acos", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_version", sa.String(length=100), nullable=False),
        sa.Column("missing_inputs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("degraded_reason", sa.String(length=200), nullable=True),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profit_snapshot_scope_window",
        "sku_profit_snapshots",
        ["tenant_id", "marketplace_id", "seller_sku", "window_end"],
        unique=False,
    )
    op.create_index(op.f("ix_sku_profit_snapshots_tenant_id"), "sku_profit_snapshots", ["tenant_id"], unique=False)
    op.create_table(
        "sku_voc_summaries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_count", sa.Integer(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("reason_counts", sa.JSON(), nullable=False),
        sa.Column("top_reason_code", sa.String(length=80), nullable=True),
        sa.Column("top_reason_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("recommended_action", sa.JSON(), nullable=True),
        sa.Column("work_item_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "marketplace_id", "seller_sku", "period_start", "period_end", name="uq_sku_voc_period"
        ),
    )
    op.create_index(
        "ix_sku_voc_queue", "sku_voc_summaries", ["tenant_id", "status", "top_reason_code", "period_end"], unique=False
    )
    op.create_index(op.f("ix_sku_voc_summaries_tenant_id"), "sku_voc_summaries", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_sku_voc_summaries_work_item_id"), "sku_voc_summaries", ["work_item_id"], unique=False)
    op.create_table(
        "sop_playbooks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("playbook_key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "playbook_key", "version", name="uq_sop_playbook_version"),
    )
    op.create_index("ix_sop_playbook_active", "sop_playbooks", ["tenant_id", "playbook_key", "active"], unique=False)
    op.create_index(op.f("ix_sop_playbooks_tenant_id"), "sop_playbooks", ["tenant_id"], unique=False)
    op.create_table(
        "advertising_budget_diagnostics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("campaign_id", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["advertising_daily_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "snapshot_id", "code", name="uq_ad_budget_diagnostic"),
    )
    op.create_index(
        "ix_ad_budget_diagnostic_queue",
        "advertising_budget_diagnostics",
        ["tenant_id", "status", "severity", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advertising_budget_diagnostics_tenant_id"),
        "advertising_budget_diagnostics",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advertising_budget_diagnostics_work_item_id"),
        "advertising_budget_diagnostics",
        ["work_item_id"],
        unique=False,
    )
    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("approval_request_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_request_id", "sequence", name="uq_approval_decision_request_sequence"),
    )
    op.create_index(
        op.f("ix_approval_decisions_approval_request_id"), "approval_decisions", ["approval_request_id"], unique=False
    )
    op.create_index(op.f("ix_approval_decisions_tenant_id"), "approval_decisions", ["tenant_id"], unique=False)
    op.create_index(
        "ix_approval_decisions_tenant_request_created",
        "approval_decisions",
        ["tenant_id", "approval_request_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "customer_case_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["customer_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_case_event_timeline", "customer_case_events", ["tenant_id", "case_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_customer_case_events_case_id"), "customer_case_events", ["case_id"], unique=False)
    op.create_index(op.f("ix_customer_case_events_tenant_id"), "customer_case_events", ["tenant_id"], unique=False)
    op.create_table(
        "inventory_positions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("channel_connection_id", sa.String(length=36), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("fulfillment_mode", sa.String(length=20), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("inbound", sa.Integer(), nullable=False),
        sa.Column("unsellable", sa.Integer(), nullable=False),
        sa.Column("warehouse_code", sa.String(length=100), nullable=True),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("platform_extensions", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["channel_connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "channel_connection_id",
            "marketplace_id",
            "seller_sku",
            "fulfillment_mode",
            "warehouse_code",
            name="uq_inventory_position",
        ),
    )
    op.create_index(
        op.f("ix_inventory_positions_channel_connection_id"),
        "inventory_positions",
        ["channel_connection_id"],
        unique=False,
    )
    op.create_index(op.f("ix_inventory_positions_tenant_id"), "inventory_positions", ["tenant_id"], unique=False)
    op.create_table(
        "issue_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("operational_issue_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("is_useful", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operational_issue_id"], ["operational_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "operational_issue_id", "actor_id", name="uq_issue_feedback_actor"),
    )
    op.create_index(
        "ix_issue_feedback_reason", "issue_feedback", ["tenant_id", "reason_code", "created_at"], unique=False
    )
    op.create_index(
        "ix_issue_feedback_rule", "issue_feedback", ["tenant_id", "rule_version", "created_at"], unique=False
    )
    op.create_index(op.f("ix_issue_feedback_tenant_id"), "issue_feedback", ["tenant_id"], unique=False)
    op.create_table(
        "manual_execution_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("approval_request_id", sa.String(length=36), nullable=False),
        sa.Column("recommended_action_id", sa.String(length=36), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_contract_version", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("approved_change", sa.JSON(), nullable=False),
        sa.Column("actual_change", sa.JSON(), nullable=False),
        sa.Column("execution_evidence", sa.JSON(), nullable=False),
        sa.Column("executed_by", sa.String(length=255), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("verification_details", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("length(action_hash) = 64", name="ck_manual_execution_action_hash"),
        sa.CheckConstraint("length(trim(execution_contract_version)) > 0", name="ck_manual_execution_contract_version"),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_manual_execution_idempotency_key"),
        sa.CheckConstraint("status IN ('executing', 'executed', 'failed')", name="ck_manual_execution_status"),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["issue_id"], ["operational_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "approval_request_id", name="uq_manual_execution_tenant_approval"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_manual_execution_tenant_idempotency"),
    )
    op.create_index(
        op.f("ix_manual_execution_results_tenant_id"), "manual_execution_results", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_manual_execution_tenant_issue", "manual_execution_results", ["tenant_id", "issue_id"], unique=False
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("channel_connection_id", sa.String(length=36), nullable=False),
        sa.Column("external_order_id", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("fulfillment_mode", sa.String(length=20), nullable=False),
        sa.Column("platform_status", sa.String(length=100), nullable=True),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_object_key", sa.String(length=1000), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("platform_extensions", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["channel_connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "channel_connection_id", "external_order_id", name="uq_order_external"),
    )
    op.create_index(op.f("ix_orders_channel_connection_id"), "orders", ["channel_connection_id"], unique=False)
    op.create_index(op.f("ix_orders_tenant_id"), "orders", ["tenant_id"], unique=False)
    op.create_table(
        "recommended_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("execution_mode", sa.String(length=30), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("automation_eligible", sa.Boolean(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("expected_revenue_impact", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("expected_profit_impact", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("expected_cost_impact", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["issue_id"], ["operational_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_recommended_action_tenant_dedupe"),
    )
    op.create_index("ix_recommended_action_issue", "recommended_actions", ["tenant_id", "issue_id"], unique=False)
    op.create_index(
        "ix_recommended_action_queue",
        "recommended_actions",
        ["tenant_id", "status", "risk_level", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_recommended_actions_tenant_id"), "recommended_actions", ["tenant_id"], unique=False)
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("channel_connection_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["channel_connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_reconciliation_runs_channel_connection_id"),
        "reconciliation_runs",
        ["channel_connection_id"],
        unique=False,
    )
    op.create_index(op.f("ix_reconciliation_runs_tenant_id"), "reconciliation_runs", ["tenant_id"], unique=False)
    op.create_index(
        "ix_reconciliation_tenant_created", "reconciliation_runs", ["tenant_id", "created_at"], unique=False
    )
    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("channel_connection_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["channel_connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "channel_connection_id", "resource_type", name="uq_sync_cursor_resource"),
    )
    op.create_index("ix_sync_cursor_tenant_status", "sync_cursors", ["tenant_id", "status"], unique=False)
    op.create_index(
        op.f("ix_sync_cursors_channel_connection_id"), "sync_cursors", ["channel_connection_id"], unique=False
    )
    op.create_index(op.f("ix_sync_cursors_tenant_id"), "sync_cursors", ["tenant_id"], unique=False)
    op.create_table(
        "variants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("barcode", sa.String(length=100), nullable=True),
        sa.Column("cost_amount", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_variant_tenant_sku"),
    )
    op.create_index(op.f("ix_variants_product_id"), "variants", ["product_id"], unique=False)
    op.create_index(op.f("ix_variants_tenant_id"), "variants", ["tenant_id"], unique=False)
    op.create_table(
        "listings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("channel_connection_id", sa.String(length=36), nullable=False),
        sa.Column("variant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("external_product_id", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.Column("price_amount", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("platform_extensions", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["channel_connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["variants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "channel_connection_id", "marketplace_id", "seller_sku", name="uq_listing_channel_sku"
        ),
    )
    op.create_index(op.f("ix_listings_channel_connection_id"), "listings", ["channel_connection_id"], unique=False)
    op.create_index(op.f("ix_listings_tenant_id"), "listings", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_listings_variant_id"), "listings", ["variant_id"], unique=False)
    op.create_table(
        "operational_work_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("operational_issue_id", sa.String(length=36), nullable=False),
        sa.Column("recommended_action_id", sa.String(length=36), nullable=True),
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("team", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("assignee_id", sa.String(length=255), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("priority_score", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_policy_version", sa.String(length=50), nullable=False),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("result_metrics", sa.JSON(), nullable=False),
        sa.Column("verification_status", sa.String(length=30), nullable=False),
        sa.Column("verification_attempts", sa.Integer(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("handoff_note", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("last_handoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["operational_issue_id"], ["operational_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommended_action_id"], ["recommended_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "source_type", "source_id", name="uq_work_item_tenant_source"),
    )
    op.create_index(op.f("ix_operational_work_items_tenant_id"), "operational_work_items", ["tenant_id"], unique=False)
    op.create_index(
        "ix_work_item_assignee",
        "operational_work_items",
        ["tenant_id", "assignee_id", "status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_item_queue", "operational_work_items", ["tenant_id", "status", "severity", "due_at"], unique=False
    )
    op.create_index(
        "ix_work_item_scope",
        "operational_work_items",
        ["tenant_id", "marketplace_id", "seller_sku", "domain"],
        unique=False,
    )
    op.create_table(
        "order_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("external_line_id", sa.String(length=100), nullable=True),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_amount", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("unit_price_currency", sa.String(length=3), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_lines_order_id"), "order_lines", ["order_id"], unique=False)
    op.create_index(op.f("ix_order_lines_tenant_id"), "order_lines", ["tenant_id"], unique=False)
    op.create_table(
        "reconciliation_differences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("difference_type", sa.String(length=100), nullable=False),
        sa.Column("amazon_value", sa.JSON(), nullable=True),
        sa.Column("internal_value", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["reconciliation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reconciliation_difference_run", "reconciliation_differences", ["tenant_id", "run_id"], unique=False
    )
    op.create_index(
        op.f("ix_reconciliation_differences_run_id"), "reconciliation_differences", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_reconciliation_differences_tenant_id"), "reconciliation_differences", ["tenant_id"], unique=False
    )
    op.create_table(
        "action_attribution_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("primary_metric", sa.String(length=100), nullable=False),
        sa.Column("desired_direction", sa.String(length=20), nullable=False),
        sa.Column("baseline_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.Column("minimum_sample", sa.Integer(), nullable=False),
        sa.Column("guardrail_metrics", sa.JSON(), nullable=False),
        sa.Column("control_spec", sa.JSON(), nullable=False),
        sa.Column("confounders", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("registered_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["operational_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "work_item_id", "action_hash", name="uq_attribution_plan_action"),
    )
    op.create_index(
        op.f("ix_action_attribution_plans_tenant_id"), "action_attribution_plans", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_attribution_plan_queue",
        "action_attribution_plans",
        ["tenant_id", "status", "observation_end"],
        unique=False,
    )
    op.create_table(
        "action_execution_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("exact_parameters", sa.JSON(), nullable=False),
        sa.Column("execution_evidence", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amazon_write_performed", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["operational_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_action_execution_idempotency"),
    )
    op.create_index(
        op.f("ix_action_execution_attempts_tenant_id"), "action_execution_attempts", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_action_execution_work_item",
        "action_execution_attempts",
        ["tenant_id", "work_item_id", "executed_at"],
        unique=False,
    )
    op.create_table(
        "action_execution_authorizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("exact_parameters", sa.JSON(), nullable=False),
        sa.Column("preflight_evidence", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["operational_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_execution_authorization_idempotency"),
    )
    op.create_index(
        op.f("ix_action_execution_authorizations_tenant_id"),
        "action_execution_authorizations",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_authorization_queue",
        "action_execution_authorizations",
        ["tenant_id", "status", "expires_at"],
        unique=False,
    )
    op.create_table(
        "customer_case_clusters",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_id", sa.String(length=50), nullable=False),
        sa.Column("seller_sku", sa.String(length=100), nullable=False),
        sa.Column("root_cause_code", sa.String(length=100), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minimum_samples", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("team", sa.String(length=50), nullable=False),
        sa.Column("operational_issue_id", sa.String(length=36), nullable=True),
        sa.Column("recommended_action_id", sa.String(length=36), nullable=True),
        sa.Column("work_item_id", sa.String(length=36), nullable=True),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("verified_amazon_data", sa.Boolean(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("actionable_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operational_issue_id"], ["operational_issues.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recommended_action_id"], ["recommended_actions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["operational_work_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_case_cluster_tenant_dedupe"),
    )
    op.create_index(
        "ix_case_cluster_queue",
        "customer_case_clusters",
        ["tenant_id", "status", "severity", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_case_cluster_scope",
        "customer_case_clusters",
        ["tenant_id", "marketplace_id", "seller_sku", "root_cause_code"],
        unique=False,
    )
    op.create_index("ix_case_cluster_work_item", "customer_case_clusters", ["tenant_id", "work_item_id"], unique=False)
    op.create_index(op.f("ix_customer_case_clusters_tenant_id"), "customer_case_clusters", ["tenant_id"], unique=False)
    op.create_table(
        "operator_activity_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["operational_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_activity_event_idempotency"),
    )
    op.create_index(
        "ix_activity_work_item",
        "operator_activity_events",
        ["tenant_id", "work_item_id", "actor_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operator_activity_events_tenant_id"), "operator_activity_events", ["tenant_id"], unique=False
    )
    op.create_table(
        "action_outcomes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("primary_metric", sa.String(length=100), nullable=False),
        sa.Column("desired_direction", sa.String(length=20), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("result_metrics", sa.JSON(), nullable=False),
        sa.Column("guardrail_metrics", sa.JSON(), nullable=False),
        sa.Column("observation_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confounders", sa.JSON(), nullable=False),
        sa.Column("control_metrics", sa.JSON(), nullable=False),
        sa.Column("randomized_experiment", sa.Boolean(), nullable=False),
        sa.Column("minimum_sample_satisfied", sa.Boolean(), nullable=False),
        sa.Column("attribution_level", sa.String(length=40), nullable=False),
        sa.Column("outcome_status", sa.String(length=40), nullable=False),
        sa.Column("incremental_value", sa.Numeric(precision=19, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("causal_claim_allowed", sa.Boolean(), nullable=False),
        sa.Column("recommended_decision", sa.String(length=30), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["action_execution_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["operational_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "execution_id", "primary_metric", "observation_end", name="uq_action_outcome_window"
        ),
    )
    op.create_index(
        "ix_action_outcome_attribution",
        "action_outcomes",
        ["tenant_id", "attribution_level", "outcome_status"],
        unique=False,
    )
    op.create_index(
        "ix_action_outcome_work_item", "action_outcomes", ["tenant_id", "work_item_id", "evaluated_at"], unique=False
    )
    op.create_index(op.f("ix_action_outcomes_tenant_id"), "action_outcomes", ["tenant_id"], unique=False)
    op.create_table(
        "customer_case_cluster_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("cluster_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["customer_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["customer_case_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "case_id", name="uq_case_cluster_member_case"),
    )
    op.create_index(
        "ix_case_cluster_member_cluster",
        "customer_case_cluster_members",
        ["tenant_id", "cluster_id", "linked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_case_cluster_members_tenant_id"), "customer_case_cluster_members", ["tenant_id"], unique=False
    )
    _enable_postgresql_rls()


def downgrade() -> None:
    _drop_postgresql_rls_policies()
    op.drop_index(op.f("ix_customer_case_cluster_members_tenant_id"), table_name="customer_case_cluster_members")
    op.drop_index("ix_case_cluster_member_cluster", table_name="customer_case_cluster_members")
    op.drop_table("customer_case_cluster_members")
    op.drop_index(op.f("ix_action_outcomes_tenant_id"), table_name="action_outcomes")
    op.drop_index("ix_action_outcome_work_item", table_name="action_outcomes")
    op.drop_index("ix_action_outcome_attribution", table_name="action_outcomes")
    op.drop_table("action_outcomes")
    op.drop_index(op.f("ix_operator_activity_events_tenant_id"), table_name="operator_activity_events")
    op.drop_index("ix_activity_work_item", table_name="operator_activity_events")
    op.drop_table("operator_activity_events")
    op.drop_index(op.f("ix_customer_case_clusters_tenant_id"), table_name="customer_case_clusters")
    op.drop_index("ix_case_cluster_work_item", table_name="customer_case_clusters")
    op.drop_index("ix_case_cluster_scope", table_name="customer_case_clusters")
    op.drop_index("ix_case_cluster_queue", table_name="customer_case_clusters")
    op.drop_table("customer_case_clusters")
    op.drop_index("ix_execution_authorization_queue", table_name="action_execution_authorizations")
    op.drop_index(op.f("ix_action_execution_authorizations_tenant_id"), table_name="action_execution_authorizations")
    op.drop_table("action_execution_authorizations")
    op.drop_index("ix_action_execution_work_item", table_name="action_execution_attempts")
    op.drop_index(op.f("ix_action_execution_attempts_tenant_id"), table_name="action_execution_attempts")
    op.drop_table("action_execution_attempts")
    op.drop_index("ix_attribution_plan_queue", table_name="action_attribution_plans")
    op.drop_index(op.f("ix_action_attribution_plans_tenant_id"), table_name="action_attribution_plans")
    op.drop_table("action_attribution_plans")
    op.drop_index(op.f("ix_reconciliation_differences_tenant_id"), table_name="reconciliation_differences")
    op.drop_index(op.f("ix_reconciliation_differences_run_id"), table_name="reconciliation_differences")
    op.drop_index("ix_reconciliation_difference_run", table_name="reconciliation_differences")
    op.drop_table("reconciliation_differences")
    op.drop_index(op.f("ix_order_lines_tenant_id"), table_name="order_lines")
    op.drop_index(op.f("ix_order_lines_order_id"), table_name="order_lines")
    op.drop_table("order_lines")
    op.drop_index("ix_work_item_scope", table_name="operational_work_items")
    op.drop_index("ix_work_item_queue", table_name="operational_work_items")
    op.drop_index("ix_work_item_assignee", table_name="operational_work_items")
    op.drop_index(op.f("ix_operational_work_items_tenant_id"), table_name="operational_work_items")
    op.drop_table("operational_work_items")
    op.drop_index(op.f("ix_listings_variant_id"), table_name="listings")
    op.drop_index(op.f("ix_listings_tenant_id"), table_name="listings")
    op.drop_index(op.f("ix_listings_channel_connection_id"), table_name="listings")
    op.drop_table("listings")
    op.drop_index(op.f("ix_variants_tenant_id"), table_name="variants")
    op.drop_index(op.f("ix_variants_product_id"), table_name="variants")
    op.drop_table("variants")
    op.drop_index(op.f("ix_sync_cursors_tenant_id"), table_name="sync_cursors")
    op.drop_index(op.f("ix_sync_cursors_channel_connection_id"), table_name="sync_cursors")
    op.drop_index("ix_sync_cursor_tenant_status", table_name="sync_cursors")
    op.drop_table("sync_cursors")
    op.drop_index("ix_reconciliation_tenant_created", table_name="reconciliation_runs")
    op.drop_index(op.f("ix_reconciliation_runs_tenant_id"), table_name="reconciliation_runs")
    op.drop_index(op.f("ix_reconciliation_runs_channel_connection_id"), table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
    op.drop_index(op.f("ix_recommended_actions_tenant_id"), table_name="recommended_actions")
    op.drop_index("ix_recommended_action_queue", table_name="recommended_actions")
    op.drop_index("ix_recommended_action_issue", table_name="recommended_actions")
    op.drop_table("recommended_actions")
    op.drop_index(op.f("ix_orders_tenant_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_channel_connection_id"), table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_manual_execution_tenant_issue", table_name="manual_execution_results")
    op.drop_index(op.f("ix_manual_execution_results_tenant_id"), table_name="manual_execution_results")
    op.drop_table("manual_execution_results")
    op.drop_index(op.f("ix_issue_feedback_tenant_id"), table_name="issue_feedback")
    op.drop_index("ix_issue_feedback_rule", table_name="issue_feedback")
    op.drop_index("ix_issue_feedback_reason", table_name="issue_feedback")
    op.drop_table("issue_feedback")
    op.drop_index(op.f("ix_inventory_positions_tenant_id"), table_name="inventory_positions")
    op.drop_index(op.f("ix_inventory_positions_channel_connection_id"), table_name="inventory_positions")
    op.drop_table("inventory_positions")
    op.drop_index(op.f("ix_customer_case_events_tenant_id"), table_name="customer_case_events")
    op.drop_index(op.f("ix_customer_case_events_case_id"), table_name="customer_case_events")
    op.drop_index("ix_customer_case_event_timeline", table_name="customer_case_events")
    op.drop_table("customer_case_events")
    op.drop_index("ix_approval_decisions_tenant_request_created", table_name="approval_decisions")
    op.drop_index(op.f("ix_approval_decisions_tenant_id"), table_name="approval_decisions")
    op.drop_index(op.f("ix_approval_decisions_approval_request_id"), table_name="approval_decisions")
    op.drop_table("approval_decisions")
    op.drop_index(op.f("ix_advertising_budget_diagnostics_work_item_id"), table_name="advertising_budget_diagnostics")
    op.drop_index(op.f("ix_advertising_budget_diagnostics_tenant_id"), table_name="advertising_budget_diagnostics")
    op.drop_index("ix_ad_budget_diagnostic_queue", table_name="advertising_budget_diagnostics")
    op.drop_table("advertising_budget_diagnostics")
    op.drop_index(op.f("ix_sop_playbooks_tenant_id"), table_name="sop_playbooks")
    op.drop_index("ix_sop_playbook_active", table_name="sop_playbooks")
    op.drop_table("sop_playbooks")
    op.drop_index(op.f("ix_sku_voc_summaries_work_item_id"), table_name="sku_voc_summaries")
    op.drop_index(op.f("ix_sku_voc_summaries_tenant_id"), table_name="sku_voc_summaries")
    op.drop_index("ix_sku_voc_queue", table_name="sku_voc_summaries")
    op.drop_table("sku_voc_summaries")
    op.drop_index(op.f("ix_sku_profit_snapshots_tenant_id"), table_name="sku_profit_snapshots")
    op.drop_index("ix_profit_snapshot_scope_window", table_name="sku_profit_snapshots")
    op.drop_table("sku_profit_snapshots")
    op.drop_index(op.f("ix_sku_cost_profiles_tenant_id"), table_name="sku_cost_profiles")
    op.drop_index(op.f("ix_sku_cost_profiles_import_job_id"), table_name="sku_cost_profiles")
    op.drop_index("ix_cost_profile_active", table_name="sku_cost_profiles")
    op.drop_table("sku_cost_profiles")
    op.drop_index("ix_search_term_queue", table_name="search_term_performance_snapshots")
    op.drop_index(
        op.f("ix_search_term_performance_snapshots_tenant_id"), table_name="search_term_performance_snapshots"
    )
    op.drop_table("search_term_performance_snapshots")
    op.drop_index(op.f("ix_review_observations_tenant_id"), table_name="review_observations")
    op.drop_index("ix_review_observation_sku", table_name="review_observations")
    op.drop_table("review_observations")
    op.drop_index(op.f("ix_return_events_tenant_id"), table_name="return_events")
    op.drop_index("ix_return_event_sku", table_name="return_events")
    op.drop_table("return_events")
    op.drop_index(op.f("ix_products_tenant_id"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_price_recommendations_tenant_id"), table_name="price_recommendations")
    op.drop_index(op.f("ix_price_recommendations_approval_request_id"), table_name="price_recommendations")
    op.drop_index("ix_price_recommendation_sku", table_name="price_recommendations")
    op.drop_index("ix_price_recommendation_queue", table_name="price_recommendations")
    op.drop_table("price_recommendations")
    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_tenant_id"), table_name="outbox_events")
    op.drop_index("ix_outbox_dispatch", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index(op.f("ix_operational_issues_tenant_id"), table_name="operational_issues")
    op.drop_index("ix_operational_issue_sku", table_name="operational_issues")
    op.drop_index("ix_operational_issue_queue", table_name="operational_issues")
    op.drop_table("operational_issues")
    op.drop_index("ix_missed_issue_scope", table_name="missed_issue_reports")
    op.drop_index(op.f("ix_missed_issue_reports_tenant_id"), table_name="missed_issue_reports")
    op.drop_index("ix_missed_issue_queue", table_name="missed_issue_reports")
    op.drop_table("missed_issue_reports")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_tenant_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_listing_quality_snapshots_tenant_id"), table_name="listing_quality_snapshots")
    op.drop_index("ix_listing_quality_latest", table_name="listing_quality_snapshots")
    op.drop_table("listing_quality_snapshots")
    op.drop_index(op.f("ix_listing_optimization_experiments_tenant_id"), table_name="listing_optimization_experiments")
    op.drop_index(
        op.f("ix_listing_optimization_experiments_approval_request_id"), table_name="listing_optimization_experiments"
    )
    op.drop_index("ix_listing_experiment_status", table_name="listing_optimization_experiments")
    op.drop_index("ix_listing_experiment_sku", table_name="listing_optimization_experiments")
    op.drop_table("listing_optimization_experiments")
    op.drop_index("ix_inventory_policy_active", table_name="inventory_policies")
    op.drop_index(op.f("ix_inventory_policies_tenant_id"), table_name="inventory_policies")
    op.drop_table("inventory_policies")
    op.drop_index(op.f("ix_inventory_health_snapshots_tenant_id"), table_name="inventory_health_snapshots")
    op.drop_index("ix_inventory_health_scope_calculated", table_name="inventory_health_snapshots")
    op.drop_table("inventory_health_snapshots")
    op.drop_index("ix_rule_version_active", table_name="intelligence_rule_versions")
    op.drop_index(op.f("ix_intelligence_rule_versions_tenant_id"), table_name="intelligence_rule_versions")
    op.drop_table("intelligence_rule_versions")
    op.drop_index(
        op.f("ix_intelligence_calibration_proposals_tenant_id"), table_name="intelligence_calibration_proposals"
    )
    op.drop_index("ix_calibration_queue", table_name="intelligence_calibration_proposals")
    op.drop_table("intelligence_calibration_proposals")
    op.drop_index(op.f("ix_inbox_events_tenant_id"), table_name="inbox_events")
    op.drop_table("inbox_events")
    op.drop_index("ix_task_tenant_status_created", table_name="durable_tasks")
    op.drop_index(op.f("ix_durable_tasks_tenant_id"), table_name="durable_tasks")
    op.drop_index(op.f("ix_durable_tasks_correlation_id"), table_name="durable_tasks")
    op.drop_table("durable_tasks")
    op.drop_index(op.f("ix_data_import_jobs_tenant_id"), table_name="data_import_jobs")
    op.drop_table("data_import_jobs")
    op.drop_index(op.f("ix_daily_operations_briefs_tenant_id"), table_name="daily_operations_briefs")
    op.drop_index("ix_daily_brief_latest", table_name="daily_operations_briefs")
    op.drop_table("daily_operations_briefs")
    op.drop_index(op.f("ix_customer_cases_tenant_id"), table_name="customer_cases")
    op.drop_index(op.f("ix_customer_cases_approval_request_id"), table_name="customer_cases")
    op.drop_index("ix_customer_case_sku", table_name="customer_cases")
    op.drop_index("ix_customer_case_queue", table_name="customer_cases")
    op.drop_index("ix_customer_case_order", table_name="customer_cases")
    op.drop_table("customer_cases")
    op.drop_index(op.f("ix_channel_connections_tenant_id"), table_name="channel_connections")
    op.drop_table("channel_connections")
    op.drop_index(op.f("ix_buyer_message_tags_tenant_id"), table_name="buyer_message_tags")
    op.drop_index("ix_buyer_message_tag_sku", table_name="buyer_message_tags")
    op.drop_table("buyer_message_tags")
    op.drop_index("ix_business_context_window", table_name="business_context_events")
    op.drop_index(op.f("ix_business_context_events_tenant_id"), table_name="business_context_events")
    op.drop_table("business_context_events")
    op.drop_index("ix_approval_requests_tenant_status_created", table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_tenant_id"), table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_amazon_signal_resource", table_name="amazon_operational_signals")
    op.drop_index("ix_amazon_signal_queue", table_name="amazon_operational_signals")
    op.drop_index(op.f("ix_amazon_operational_signals_tenant_id"), table_name="amazon_operational_signals")
    op.drop_table("amazon_operational_signals")
    op.drop_index("ix_agent_runs_tenant_workflow_created", table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_tenant_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_correlation_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_advertising_daily_snapshots_tenant_id"), table_name="advertising_daily_snapshots")
    op.drop_index("ix_ad_daily_queue", table_name="advertising_daily_snapshots")
    op.drop_table("advertising_daily_snapshots")
    op.drop_table("users")
    op.drop_table("tenants")
    op.drop_index(op.f("ix_encrypted_secrets_tenant_id"), table_name="encrypted_secrets")
    op.drop_table("encrypted_secrets")
    op.drop_index("ix_audit_tenant_created", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_tenant_id"), table_name="audit_events")
    op.drop_table("audit_events")
