"""Add sequential critical approval decisions.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from infrastructure.database import models


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


_APPROVAL_COLUMNS = (
    sa.Column("first_approver_id", sa.String(length=255), nullable=True),
    sa.Column("first_approved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("first_approval_reason", sa.Text(), nullable=True),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("decision_reason", sa.Text(), nullable=True),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("approval_requests")}
    for column in _APPROVAL_COLUMNS:
        if column.name not in existing:
            op.add_column("approval_requests", column)

    models.Base.metadata.tables["approval_decisions"].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "approval_decisions" ENABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS approval_decisions_tenant_isolation ON "approval_decisions"')
        op.execute(
            """CREATE POLICY approval_decisions_tenant_isolation ON "approval_decisions"
            USING (tenant_id = current_setting('app.current_tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))"""
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS approval_decisions_tenant_isolation ON "approval_decisions"')
    models.Base.metadata.tables["approval_decisions"].drop(bind=bind, checkfirst=True)

    existing = {column["name"] for column in inspect(bind).get_columns("approval_requests")}
    names = [column.name for column in reversed(_APPROVAL_COLUMNS) if column.name in existing]
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("approval_requests") as batch_op:
            for name in names:
                batch_op.drop_column(name)
        return
    for name in names:
        op.drop_column("approval_requests", name)
