from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


_current_tenant_id: ContextVar[str | None] = ContextVar("database_tenant_id", default=None)


class Base(DeclarativeBase):
    pass


class TenantAwareSession(Session):
    pass


@event.listens_for(TenantAwareSession, "after_begin")
def _apply_postgresql_tenant_context(session: Session, transaction: object, connection: object) -> None:
    if connection.dialect.name != "postgresql":
        return
    tenant_id = _current_tenant_id.get()
    connection.execute(text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id or ""})


def create_database_engine(database_url: str):
    options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **options)


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=create_database_engine(database_url), class_=TenantAwareSession, expire_on_commit=False)


def bind_tenant_context(tenant_id: str) -> Token[str | None]:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    return _current_tenant_id.set(tenant_id)


def reset_tenant_context(token: Token[str | None]) -> None:
    _current_tenant_id.reset(token)


@contextmanager
def tenant_context(tenant_id: str):
    token = bind_tenant_context(tenant_id)
    try:
        yield
    finally:
        reset_tenant_context(token)


def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
