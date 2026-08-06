from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.release_profiles import ReleaseProfile  # noqa: E402
from api.route_catalog import build_route_inventory  # noqa: E402

OUTPUT_PATH = PROJECT_ROOT / "docs" / "evidence" / "routes" / "route-inventory-v1.json"


def build_payload() -> dict[str, Any]:
    routes = build_route_inventory()
    return {
        "schema_version": "1.0",
        "generator": "scripts/generate_route_inventory.py",
        "profiles": [profile.value for profile in ReleaseProfile],
        "route_count": len(routes),
        "routes": routes,
    }


def render_payload() -> str:
    return json.dumps(build_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the versioned API route ownership inventory.")
    parser.add_argument("--check", action="store_true", help="Fail if the versioned inventory is stale.")
    args = parser.parse_args()
    expected = render_payload()
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != expected:
            print(f"Route inventory is stale: {OUTPUT_PATH}")
            return 1
        print(f"Route inventory is current: {OUTPUT_PATH}")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(expected, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
