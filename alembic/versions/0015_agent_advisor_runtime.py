"""Persist bounded advisor metadata on agent runs.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("agent_runs")}
    columns = (
        sa.Column("agent_type", sa.String(length=100), nullable=True),
        sa.Column("schema_version", sa.String(length=50), nullable=True),
        sa.Column("model_usage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("analysis_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_category", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("agent_runs", column)
    indexes = {index["name"] for index in inspect(bind).get_indexes("agent_runs")}
    if "ix_agent_runs_tenant_workflow_created" not in indexes:
        op.create_index("ix_agent_runs_tenant_workflow_created", "agent_runs", ["tenant_id", "workflow_type", "created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in inspect(bind).get_indexes("agent_runs")}
    if "ix_agent_runs_tenant_workflow_created" in indexes:
        op.drop_index("ix_agent_runs_tenant_workflow_created", table_name="agent_runs")
    existing = {column["name"] for column in inspect(bind).get_columns("agent_runs")}
    for column_name in (
        "retry_count", "error_category", "analysis_result", "evidence_manifest",
        "model_usage", "schema_version", "agent_type",
    ):
        if column_name in existing:
            op.drop_column("agent_runs", column_name)
