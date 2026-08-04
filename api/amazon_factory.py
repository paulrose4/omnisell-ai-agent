from __future__ import annotations

from api.config import Settings
from commerce.adapters.amazon import AmazonSPAPIAdapter, AmazonSPAPIConfig
from commerce.adapters.amazon.rate_limit import OperationRateLimiter
from commerce.adapters.amazon.secrets import EncryptedDatabaseSecretStore, InMemorySecretStore
from safety.amazon_write_gate import AmazonWriteGate


def create_secret_store(settings: Settings, session_factory):
    if settings.secret_encryption_key:
        return EncryptedDatabaseSecretStore(session_factory, settings.secret_encryption_key)
    if settings.environment == "production":
        raise RuntimeError("SECRET_ENCRYPTION_KEY is required in production")
    return InMemorySecretStore()


def create_amazon_adapter(*, settings: Settings, connection, secret_store, tenant_repository=None, client=None) -> AmazonSPAPIAdapter:
    return AmazonSPAPIAdapter(
        config=AmazonSPAPIConfig(
            lwa_client_id=settings.amazon_lwa_client_id,
            lwa_client_secret=settings.amazon_lwa_client_secret,
            lwa_token_url=settings.amazon_lwa_token_url,
            lwa_revoke_url=settings.amazon_lwa_revoke_url,
            application_id=settings.amazon_application_id,
            redirect_uri=settings.amazon_redirect_uri,
            endpoint=settings.amazon_sp_api_endpoint,
            seller_central_url=settings.amazon_seller_central_url,
            region=settings.amazon_region,
        ),
        tenant_id=connection.tenant_id,
        selling_partner_id=connection.selling_partner_id,
        marketplace_ids=list(connection.marketplace_ids),
        refresh_token_ref=connection.secret_ref,
        secret_store=secret_store,
        client=client,
        rate_limiter=OperationRateLimiter(rate_multiplier=settings.mock_amazon_rate_limit_multiplier),
        write_gate=AmazonWriteGate(
            global_enabled=settings.amazon_writes_enabled,
            tenant_enabled=bool(tenant_repository and tenant_repository.amazon_writes_enabled(connection.tenant_id)),
            pilot_read_only=settings.amazon_pilot_read_only,
        ),
    )
