"""Add mock operations drill metadata and execution lifecycle.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa

from infrastructure.database import models


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    additions = {
        "orders": [sa.Column("platform_extensions", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))],
        "inventory_positions": [sa.Column("platform_extensions", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))],
        "operational_issues": [
            sa.Column("assignee_id", sa.String(length=255), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution_code", sa.String(length=100), nullable=True),
            sa.Column("execution_result_id", sa.String(length=36), nullable=True),
            sa.Column("reopened_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("priority_score", sa.Numeric(19, 6), nullable=False, server_default="0"),
        ],
    }
    for table_name, columns in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table_name, column)

    models.Base.metadata.tables["manual_execution_results"].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "manual_execution_results" ENABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS manual_execution_results_tenant_isolation ON "manual_execution_results"')
        op.execute("""CREATE POLICY manual_execution_results_tenant_isolation ON "manual_execution_results"
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))""")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS manual_execution_results_tenant_isolation ON "manual_execution_results"')
    models.Base.metadata.tables["manual_execution_results"].drop(bind=bind, checkfirst=True)
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("operational_issues")}
    for column in ("priority_score", "reopened_count", "execution_result_id", "resolution_code", "resolved_at", "started_at", "acknowledged_at", "due_at", "assignee_id"):
        if column in existing:
            op.drop_column("operational_issues", column)
    for table_name in ("inventory_positions", "orders"):
        current = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        if "platform_extensions" in current:
            op.drop_column(table_name, "platform_extensions")
