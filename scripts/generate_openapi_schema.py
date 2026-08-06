from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.config import Settings  # noqa: E402
from api.main import create_app  # noqa: E402
from api.release_profiles import ReleaseProfile  # noqa: E402

OUTPUT_PATH = PROJECT_ROOT / "docs" / "evidence" / "openapi" / "full-development-v1.json"


def build_schema() -> dict:
    legacy_names = ("VLLM_BASE_URL", "VLLM_MODEL_NAME", "VLLM_API_KEY")
    legacy_values = {name: os.environ.pop(name) for name in legacy_names if name in os.environ}
    try:
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue] - runtime-only BaseSettings constructor option.
            environment="test",
            database_url="sqlite://",
            auth_mode="api_key",
            release_profile=ReleaseProfile.FULL_DEVELOPMENT,
            mock_drill_enabled=True,
            model_base_url="http://localhost:11434/v1",
            model_name="schema-generation",
            model_api_key="not-needed",
        )
    finally:
        os.environ.update(legacy_values)
    schema = create_app(settings).openapi()
    schema["x-omnisell-release-profile"] = ReleaseProfile.FULL_DEVELOPMENT.value
    schema["x-omnisell-generated-by"] = "scripts/generate_openapi_schema.py"
    return schema


def render_schema() -> str:
    return json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the versioned full-development OpenAPI schema.")
    parser.add_argument("--check", action="store_true", help="Fail if the versioned schema is stale.")
    args = parser.parse_args()
    expected = render_schema()
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != expected:
            print(f"OpenAPI schema is stale: {OUTPUT_PATH}")
            return 1
        print(f"OpenAPI schema is current: {OUTPUT_PATH}")
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(expected, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
