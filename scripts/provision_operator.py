from __future__ import annotations

import argparse

from api.config import get_settings
from domain.models import Membership, Role, User
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.tenants import TenantRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision an OIDC operator and tenant membership.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--oidc-subject", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", choices=[item.value for item in Role if item.value in {"owner", "admin", "operator", "approver", "viewer"}], required=True)
    args = parser.parse_args()

    repository = TenantRepository(create_session_factory(get_settings().database_url))
    user = repository.find_user_by_oidc_subject(args.oidc_subject)
    if user is None:
        user = repository.create_user(User(email=args.email, display_name=args.display_name, oidc_subject=args.oidc_subject))
    membership = repository.find_membership(tenant_id=args.tenant_id, user_id=user.id)
    if membership is None:
        repository.add_membership(Membership(tenant_id=args.tenant_id, user_id=user.id, role=Role(args.role)))
    print(f"provisioned user={user.id} tenant={args.tenant_id} role={args.role}")


if __name__ == "__main__":
    main()