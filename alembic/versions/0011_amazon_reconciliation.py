"""Add Amazon reconciliation runs and differences.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-14
"""
from alembic import op
from infrastructure.database import models
revision="0011"
down_revision="0010"
branch_labels=None
depends_on=None
_TABLES=("reconciliation_runs","reconciliation_differences")
def upgrade():
    bind=op.get_bind()
    for name in _TABLES: models.Base.metadata.tables[name].create(bind=bind,checkfirst=True)
    if bind.dialect.name=="postgresql":
        for name in _TABLES:
            op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS {name}_tenant_isolation ON "{name}"')
            op.execute(f'''CREATE POLICY {name}_tenant_isolation ON "{name}" USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))''')
def downgrade():
    bind=op.get_bind()
    for name in reversed(_TABLES): models.Base.metadata.tables[name].drop(bind=bind,checkfirst=True)
