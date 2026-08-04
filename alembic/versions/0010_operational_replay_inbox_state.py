"""Add operational replay and Inbox processing state.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    durable_columns = {column["name"] for column in inspector.get_columns("durable_tasks")}
    for name, column in [
        ("replayed_from_task_id", sa.Column("replayed_from_task_id", sa.String(36))),
        ("replayed_to_task_id", sa.Column("replayed_to_task_id", sa.String(36))),
        ("closed_at", sa.Column("closed_at", sa.DateTime(timezone=True))),
        ("closed_by", sa.Column("closed_by", sa.String(255))),
        ("close_reason", sa.Column("close_reason", sa.Text())),
    ]:
        if name not in durable_columns:
            op.add_column("durable_tasks", column)
    inbox_columns = {column["name"] for column in inspector.get_columns("inbox_events")}
    for name, column in [
        ("status", sa.Column("status", sa.String(30), nullable=False, server_default="received")),
        ("attempts", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")),
        ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(timezone=True))),
        ("next_retry_at", sa.Column("next_retry_at", sa.DateTime(timezone=True))),
        ("error_code", sa.Column("error_code", sa.String(100))),
        ("error_message", sa.Column("error_message", sa.Text())),
    ]:
        if name not in inbox_columns:
            op.add_column("inbox_events", column)


def downgrade() -> None:
    for name in ("error_message", "error_code", "next_retry_at", "lease_expires_at", "attempts", "status"):
        op.drop_column("inbox_events", name)
    for name in ("close_reason", "closed_by", "closed_at", "replayed_to_task_id", "replayed_from_task_id"):
        op.drop_column("durable_tasks", name)
