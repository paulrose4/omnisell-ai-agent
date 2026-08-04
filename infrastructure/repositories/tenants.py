from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from domain.models import ChannelConnection, ChannelProvider, ConnectionStatus, Membership, Role, Tenant, User
from infrastructure.database.models import ChannelConnectionRow, MembershipRow, TenantRow, UserRow


class TenantRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    @contextmanager
    def session(self, tenant_id: str | None = None) -> Iterator[Session]:
        session = self.session_factory()
        try:
            if tenant_id and session.bind and session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tenant(self, tenant: Tenant) -> Tenant:
        with self.session() as session:
            session.add(TenantRow(id=tenant.id, name=tenant.name, slug=tenant.slug, created_at=tenant.created_at))
        return tenant

    def list_tenant_ids(self) -> list[str]:
        with self.session() as session:
            return list(session.scalars(select(TenantRow.id).order_by(TenantRow.id)))

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self.session(tenant_id) as session:
            row = session.get(TenantRow, tenant_id)
            return None if row is None else Tenant(id=row.id, name=row.name, slug=row.slug, created_at=row.created_at)

    def create_user(self, user: User) -> User:
        with self.session() as session:
            session.add(UserRow(id=user.id, email=user.email, display_name=user.display_name, oidc_subject=user.oidc_subject))
        return user

    def find_user_by_oidc_subject(self, oidc_subject: str) -> User | None:
        with self.session() as session:
            row = session.scalar(select(UserRow).where(UserRow.oidc_subject == oidc_subject))
            return None if row is None else User(id=row.id, email=row.email, display_name=row.display_name, oidc_subject=row.oidc_subject)

    def amazon_writes_enabled(self, tenant_id: str) -> bool:
        with self.session(tenant_id) as session:
            row = session.get(TenantRow, tenant_id)
            return bool(row and row.amazon_writes_enabled)

    def list_connections(self, *, tenant_id: str) -> list[ChannelConnection]:
        with self.session(tenant_id) as session:
            rows = list(session.scalars(select(ChannelConnectionRow).where(ChannelConnectionRow.tenant_id == tenant_id).order_by(ChannelConnectionRow.created_at.desc())))
        return [ChannelConnection(
            id=row.id, tenant_id=row.tenant_id, provider=ChannelProvider(row.provider),
            selling_partner_id=row.selling_partner_id, region=row.region,
            marketplace_ids=tuple(row.marketplace_ids), secret_ref=row.secret_ref,
            status=ConnectionStatus(row.status), version=row.version,
            created_at=row.created_at, updated_at=row.updated_at,
        ) for row in rows]
    def add_membership(self, membership: Membership) -> Membership:
        with self.session(membership.tenant_id) as session:
            session.add(MembershipRow(id=membership.id, tenant_id=membership.tenant_id, user_id=membership.user_id, role=membership.role.value))
        return membership

    def find_membership(self, *, tenant_id: str, user_id: str) -> Membership | None:
        with self.session(tenant_id) as session:
            row = session.scalar(select(MembershipRow).where(MembershipRow.tenant_id == tenant_id, MembershipRow.user_id == user_id))
            return None if row is None else Membership(id=row.id, tenant_id=row.tenant_id, user_id=row.user_id, role=Role(row.role))

    def save_connection(self, connection: ChannelConnection) -> ChannelConnection:
        with self.session(connection.tenant_id) as session:
            row = session.scalar(select(ChannelConnectionRow).where(
                ChannelConnectionRow.tenant_id == connection.tenant_id,
                ChannelConnectionRow.provider == connection.provider.value,
                ChannelConnectionRow.selling_partner_id == connection.selling_partner_id,
                ChannelConnectionRow.region == connection.region,
            ))
            if row is None:
                row = ChannelConnectionRow(
                    id=connection.id, tenant_id=connection.tenant_id, provider=connection.provider.value,
                    selling_partner_id=connection.selling_partner_id, region=connection.region,
                    marketplace_ids=list(connection.marketplace_ids), secret_ref=connection.secret_ref,
                    status=connection.status.value, version=connection.version,
                    created_at=connection.created_at, updated_at=connection.updated_at,
                )
                session.add(row)
            else:
                row.marketplace_ids = list(connection.marketplace_ids)
                row.secret_ref = connection.secret_ref
                row.status = connection.status.value
                row.version += 1
                row.updated_at = connection.updated_at
            saved = ChannelConnection(
                id=row.id, tenant_id=row.tenant_id, provider=ChannelProvider(row.provider),
                selling_partner_id=row.selling_partner_id, region=row.region,
                marketplace_ids=tuple(row.marketplace_ids), secret_ref=row.secret_ref,
                status=ConnectionStatus(row.status), version=row.version,
                created_at=row.created_at, updated_at=row.updated_at,
            )
        return saved

    def get_connection(self, *, tenant_id: str, connection_id: str) -> ChannelConnection | None:
        with self.session(tenant_id) as session:
            row = session.scalar(select(ChannelConnectionRow).where(ChannelConnectionRow.tenant_id == tenant_id, ChannelConnectionRow.id == connection_id))
            if row is None:
                return None
            return ChannelConnection(
                id=row.id, tenant_id=row.tenant_id, provider=ChannelProvider(row.provider),
                selling_partner_id=row.selling_partner_id, region=row.region,
                marketplace_ids=tuple(row.marketplace_ids), secret_ref=row.secret_ref,
                status=ConnectionStatus(row.status), version=row.version,
                created_at=row.created_at, updated_at=row.updated_at,
            )
    def list_active_connections(self, *, tenant_id: str) -> list[ChannelConnection]:
        with self.session(tenant_id) as session:
            rows = list(session.scalars(select(ChannelConnectionRow).where(
                ChannelConnectionRow.tenant_id == tenant_id,
                ChannelConnectionRow.status == ConnectionStatus.ACTIVE.value,
            )))
        return [ChannelConnection(
            id=row.id, tenant_id=row.tenant_id, provider=ChannelProvider(row.provider),
            selling_partner_id=row.selling_partner_id, region=row.region,
            marketplace_ids=tuple(row.marketplace_ids), secret_ref=row.secret_ref,
            status=ConnectionStatus(row.status), version=row.version,
            created_at=row.created_at, updated_at=row.updated_at,
        ) for row in rows]
