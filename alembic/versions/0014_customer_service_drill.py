"""Add persistent customer service drill and SOP playbooks.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-15
"""

from alembic import op

from infrastructure.database import models


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


TABLES = ("customer_cases", "customer_case_events", "sop_playbooks")


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        models.Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for table_name in TABLES:
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS {table_name}_tenant_isolation ON "{table_name}"')
            op.execute(
                f'''CREATE POLICY {table_name}_tenant_isolation ON "{table_name}"
                USING (tenant_id = current_setting('app.current_tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))'''
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table_name in reversed(TABLES):
            op.execute(f'DROP POLICY IF EXISTS {table_name}_tenant_isolation ON "{table_name}"')
    for table_name in reversed(TABLES):
        models.Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
