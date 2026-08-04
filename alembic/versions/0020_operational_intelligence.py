"""Add Amazon operational signals, advertising diagnostics and VOC facts.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-20
"""

from alembic import op

from infrastructure.database import models


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_TABLES = (
    "amazon_operational_signals",
    "advertising_daily_snapshots",
    "search_term_performance_snapshots",
    "advertising_budget_diagnostics",
    "return_events",
    "review_observations",
    "buyer_message_tags",
    "sku_voc_summaries",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in _TABLES:
        models.Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for table_name in _TABLES:
            policy_name = f"{table_name}_tenant_isolation"
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS {policy_name} ON "{table_name}"')
            op.execute(
                f'''CREATE POLICY {policy_name} ON "{table_name}"
                    USING (tenant_id = current_setting('app.current_tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))'''
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in _TABLES:
            op.execute(f'DROP POLICY IF EXISTS {table_name}_tenant_isolation ON "{table_name}"')
    for table_name in reversed(_TABLES):
        models.Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
