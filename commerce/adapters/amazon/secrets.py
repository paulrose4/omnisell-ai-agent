from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from infrastructure.database.session import Base


class SecretStore(Protocol):
    def put(self, *, tenant_id: str, value: str) -> str: ...
    def get(self, *, tenant_id: str, secret_ref: str) -> str: ...
    def delete(self, *, tenant_id: str, secret_ref: str) -> None: ...


class InMemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def put(self, *, tenant_id: str, value: str) -> str:
        ref = f"memory://{uuid4()}"
        self.values[(tenant_id, ref)] = value
        return ref

    def get(self, *, tenant_id: str, secret_ref: str) -> str:
        return self.values[(tenant_id, secret_ref)]

    def delete(self, *, tenant_id: str, secret_ref: str) -> None:
        self.values.pop((tenant_id, secret_ref), None)


class SecretRow(Base):
    __tablename__ = "encrypted_secrets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class EncryptedDatabaseSecretStore:
    """MVP secret store. Production can replace this Port with AWS/GCP/Azure Secret Manager."""

    def __init__(self, session_factory: sessionmaker[Session], encryption_key: str) -> None:
        if not encryption_key:
            raise ValueError("SECRET_ENCRYPTION_KEY is required")
        self.session_factory = session_factory
        self.fernet = Fernet(encryption_key.encode("ascii"))

    def put(self, *, tenant_id: str, value: str) -> str:
        secret_id = str(uuid4())
        with self.session_factory.begin() as session:
            session.add(
                SecretRow(id=secret_id, tenant_id=tenant_id, ciphertext=self.fernet.encrypt(value.encode("utf-8")))
            )
        return f"db-secret://{secret_id}"

    def get(self, *, tenant_id: str, secret_ref: str) -> str:
        secret_id = secret_ref.removeprefix("db-secret://")
        with self.session_factory() as session:
            row = session.scalar(select(SecretRow).where(SecretRow.id == secret_id, SecretRow.tenant_id == tenant_id))
            if row is None:
                raise KeyError("Secret not found")
            try:
                return self.fernet.decrypt(row.ciphertext).decode("utf-8")
            except InvalidToken as exc:
                raise RuntimeError("Secret decryption failed") from exc

    def delete(self, *, tenant_id: str, secret_ref: str) -> None:
        secret_id = secret_ref.removeprefix("db-secret://")
        with self.session_factory.begin() as session:
            row = session.scalar(select(SecretRow).where(SecretRow.id == secret_id, SecretRow.tenant_id == tenant_id))
            if row is not None:
                session.delete(row)
