"""Initial multi-tenant commerce schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-13
"""
from alembic import op

from infrastructure.database.session import Base
from infrastructure.database import models

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        for table in models.TENANT_SCOPED_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(
                f'''CREATE POLICY {table}_tenant_isolation ON "{table}"
                    USING (tenant_id = current_setting('app.current_tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))'''
            )


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
