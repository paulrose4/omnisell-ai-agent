from __future__ import annotations

import json

from api.config import get_settings
from application.golden_mock import seed_golden_mock_tenant
from infrastructure.database.session import create_session_factory, tenant_context


def main() -> None:
    settings = get_settings()
    with tenant_context("local-demo"):
        result = seed_golden_mock_tenant(
            session_factory=create_session_factory(settings.database_url),
            tenant_id="local-demo",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
