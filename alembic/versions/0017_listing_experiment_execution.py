"""Persist Listing experiment execution and decision lifecycle.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("execution_status", sa.String(length=30), "not_executed"),
    ("actual_changes", sa.JSON(), "{}"),
    ("execution_evidence", sa.JSON(), "{}"),
    ("executed_by", sa.String(length=255), None),
    ("executed_at", sa.DateTime(timezone=True), None),
    ("execution_notes", sa.Text(), None),
    ("decision", sa.String(length=30), None),
    ("decision_by", sa.String(length=255), None),
    ("decision_at", sa.DateTime(timezone=True), None),
    ("decision_reason", sa.Text(), None),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("listing_optimization_experiments")}
    for name, column_type, default in _COLUMNS:
        if name in existing:
            continue
        kwargs = {"nullable": name not in {"execution_status"}}
        if name == "execution_status":
            kwargs["nullable"] = False
            kwargs["server_default"] = sa.text("'not_executed'")
        elif name in {"actual_changes", "execution_evidence"}:
            kwargs["nullable"] = False
            kwargs["server_default"] = sa.text("'{}'")
        op.add_column("listing_optimization_experiments", sa.Column(name, column_type, **kwargs))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("listing_optimization_experiments")}
    for name, _, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("listing_optimization_experiments", name)
