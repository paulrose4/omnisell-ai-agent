"""Add Listing quality snapshots and optimization experiments.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14
"""
from alembic import op

from infrastructure.database import models

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_TABLES = ("listing_quality_snapshots", "listing_optimization_experiments")


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in _TABLES:
        models.Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS {table}_tenant_isolation ON "{table}"')
            op.execute(
                f'''CREATE POLICY {table}_tenant_isolation ON "{table}"
                    USING (tenant_id = current_setting('app.current_tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))'''
            )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(_TABLES):
        models.Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
