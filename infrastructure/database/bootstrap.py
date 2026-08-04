from __future__ import annotations

from commerce.adapters.amazon.secrets import SecretRow  # noqa: F401
from infrastructure.database.models import TenantRow
from infrastructure.database.session import Base, create_database_engine, create_session_factory


def initialize_database(
    database_url: str, *, default_tenant_id: str = "default", default_tenant_name: str = "Default Tenant"
) -> None:
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(database_url)
    with factory.begin() as session:
        if session.get(TenantRow, default_tenant_id) is None:
            session.add(TenantRow(id=default_tenant_id, name=default_tenant_name, slug=default_tenant_id))
