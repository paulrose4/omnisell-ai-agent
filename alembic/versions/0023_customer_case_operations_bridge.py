"""Add the governed customer Case to operations bridge.

Revision ID: 0023
Revises: 0022
"""

from alembic import op
import sqlalchemy as sa

from infrastructure.database import models


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_BRIDGE_TABLES = ("customer_case_clusters", "customer_case_cluster_members")


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {item["name"] for item in sa.inspect(bind).get_columns("outbox_events")}
    additions = (
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    missing_columns = [column for column in additions if column.name not in existing_columns]
    if missing_columns:
        with op.batch_alter_table("outbox_events") as batch:
            for column in missing_columns:
                batch.add_column(column)
    if "dedupe_key" not in existing_columns:
        op.execute("UPDATE outbox_events SET dedupe_key = event_type || ':' || aggregate_type || ':' || aggregate_id || ':' || id WHERE dedupe_key IS NULL")
        with op.batch_alter_table("outbox_events") as batch:
            batch.alter_column("dedupe_key", existing_type=sa.String(length=255), nullable=False)
    inspector = sa.inspect(bind)
    unique_names = {item["name"] for item in inspector.get_unique_constraints("outbox_events")}
    index_names = {item["name"] for item in inspector.get_indexes("outbox_events")}
    with op.batch_alter_table("outbox_events") as batch:
        if "uq_outbox_tenant_dedupe" not in unique_names:
            batch.create_unique_constraint("uq_outbox_tenant_dedupe", ["tenant_id", "dedupe_key"])
        if "ix_outbox_dispatch" not in index_names:
            batch.create_index("ix_outbox_dispatch", ["tenant_id", "status", "next_retry_at", "created_at"], unique=False)

    for table_name in _BRIDGE_TABLES:
        models.Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for table_name in _BRIDGE_TABLES:
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
        for table_name in _BRIDGE_TABLES:
            op.execute(f'DROP POLICY IF EXISTS {table_name}_tenant_isolation ON "{table_name}"')
    for table_name in reversed(_BRIDGE_TABLES):
        models.Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_index("ix_outbox_dispatch")
        batch.drop_constraint("uq_outbox_tenant_dedupe", type_="unique")
        batch.drop_column("error_message")
        batch.drop_column("error_code")
        batch.drop_column("next_retry_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("attempts")
        batch.drop_column("status")
        batch.drop_column("processed_at")
        batch.drop_column("dedupe_key")
