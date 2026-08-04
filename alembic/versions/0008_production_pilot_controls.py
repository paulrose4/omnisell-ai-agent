"""Add production pilot tenant write control.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tenants")}
    if "amazon_writes_enabled" not in columns:
        op.add_column("tenants", sa.Column("amazon_writes_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tenants", "amazon_writes_enabled")
