"""Protect encrypted secret references with tenant RLS.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-14
"""

from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE "encrypted_secrets" ENABLE ROW LEVEL SECURITY')
    op.execute(
        """CREATE POLICY encrypted_secrets_tenant_isolation ON "encrypted_secrets"
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute('DROP POLICY IF EXISTS encrypted_secrets_tenant_isolation ON "encrypted_secrets"')
    op.execute('ALTER TABLE "encrypted_secrets" DISABLE ROW LEVEL SECURITY')
