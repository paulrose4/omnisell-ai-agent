from __future__ import annotations

from api.config import get_settings
from domain.models import Membership, Role, Tenant, User
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.tenants import TenantRepository

PERSONAS = (
    ("local-owner", "owner@local.omnisell.test", "Local Owner", Role.OWNER),
    ("local-operator", "operator@local.omnisell.test", "Local Operator", Role.OPERATOR),
    ("local-approver", "approver@local.omnisell.test", "Local Approver", Role.APPROVER),
    ("local-viewer", "viewer@local.omnisell.test", "Local Viewer", Role.VIEWER),
)


def main() -> None:
    settings = get_settings()
    repository = TenantRepository(create_session_factory(settings.database_url))
    if repository.get_tenant("local-demo") is None:
        repository.create_tenant(Tenant(id="local-demo", name="Local E2E Demo", slug="local-demo"))
    for subject, email, display_name, role in PERSONAS:
        user = repository.find_user_by_oidc_subject(subject)
        if user is None:
            user = repository.create_user(User(email=email, display_name=display_name, oidc_subject=subject))
        if repository.find_membership(tenant_id="local-demo", user_id=user.id) is None:
            repository.add_membership(Membership(tenant_id="local-demo", user_id=user.id, role=role))
    print("local-demo tenant and owner/operator/approver/viewer personas ready")


if __name__ == "__main__":
    main()
