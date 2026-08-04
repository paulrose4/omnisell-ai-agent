from __future__ import annotations

from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from application.golden_mock import seed_golden_mock_tenant
from infrastructure.database.models import Base


_PRESERVED_TABLES = {
    "tenants",
    "users",
    "memberships",
    "channel_connections",
    "encrypted_secrets",
    "inventory_policies",
    "sku_cost_profiles",
    "sop_playbooks",
}


def reset_mock_demo_tenant(
    *, session_factory: sessionmaker[Session], tenant_id: str,
) -> dict[str, Any]:
    """Reset only tenant-scoped derived data for a local Mock demonstration.

    Authentication, the Mock Amazon connection, operator configuration and SOP
    playbooks remain intact so the next sync can recreate a deterministic demo.
    """
    deleted_tables: list[str] = []
    with session_factory.begin() as session:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _PRESERVED_TABLES or "tenant_id" not in table.c:
                continue
            session.execute(delete(table).where(table.c.tenant_id == tenant_id))
            deleted_tables.append(table.name)

    seeded = seed_golden_mock_tenant(session_factory=session_factory, tenant_id=tenant_id)
    return {
        "tenant_id": tenant_id,
        "deleted_tables": deleted_tables,
        "seeded": seeded,
        "data_source": "mock_amazon",
        "verified_amazon_data": False,
        "next_step": "Run Mock Amazon sync and generate the daily operations brief.",
    }
