"""Add persistent Amazon synchronization cursors.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-14
"""
from alembic import op

from infrastructure.database import models

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    models.Base.metadata.tables["sync_cursors"].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "sync_cursors" ENABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS sync_cursors_tenant_isolation ON "sync_cursors"')
        op.execute('''CREATE POLICY sync_cursors_tenant_isolation ON "sync_cursors"
                      USING (tenant_id = current_setting('app.current_tenant_id', true))
                      WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))''')


def downgrade() -> None:
    models.Base.metadata.tables["sync_cursors"].drop(bind=op.get_bind(), checkfirst=True)
