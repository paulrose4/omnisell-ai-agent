"""Add tenant-scoped AgentRun idempotency.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "uq_agent_run_tenant_workflow_idempotency"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    if "idempotency_key" not in columns:
        op.add_column("agent_runs", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("agent_runs")}
    unique_constraints = {
        constraint["name"]
        for constraint in inspect(bind).get_unique_constraints("agent_runs")
        if constraint["name"]
    }
    if CONSTRAINT_NAME not in indexes and CONSTRAINT_NAME not in unique_constraints:
        op.create_index(
            CONSTRAINT_NAME,
            "agent_runs",
            ["tenant_id", "workflow_type", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("agent_runs")}
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("agent_runs")
        if constraint["name"]
    }
    columns = {column["name"] for column in inspector.get_columns("agent_runs")}

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("agent_runs") as batch_op:
            if CONSTRAINT_NAME in unique_constraints:
                batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
            elif CONSTRAINT_NAME in indexes:
                batch_op.drop_index(CONSTRAINT_NAME)
            if "idempotency_key" in columns:
                batch_op.drop_column("idempotency_key")
        return

    if CONSTRAINT_NAME in unique_constraints:
        op.drop_constraint(CONSTRAINT_NAME, "agent_runs", type_="unique")
    elif CONSTRAINT_NAME in indexes:
        op.drop_index(CONSTRAINT_NAME, table_name="agent_runs")
    if "idempotency_key" in columns:
        op.drop_column("agent_runs", "idempotency_key")
