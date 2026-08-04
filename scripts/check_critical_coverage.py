from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_THRESHOLDS = {
    "agents/validators/evidence.py": 95.0,
    "domain/inventory.py": 90.0,
    "domain/pricing.py": 90.0,
    "domain/profitability.py": 90.0,
    "model_gateway/contracts.py": 90.0,
    "safety/hitl.py": 95.0,
    "safety/policy_engine.py": 95.0,
    "workflows/engine.py": 85.0,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enforce file-level coverage for high-risk OmniSell modules.")
    parser.add_argument("--coverage-json", type=Path, default=Path("test-results/coverage.json"))
    return parser


def _normalized_files(payload: dict) -> dict[str, dict]:
    return {str(path).replace("\\", "/"): details for path, details in dict(payload.get("files") or {}).items()}


def main() -> int:
    args = _parser().parse_args()
    payload = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    files = _normalized_files(payload)
    failures: list[str] = []
    for path, threshold in DEFAULT_THRESHOLDS.items():
        details = files.get(path)
        if details is None:
            failures.append(f"{path}: missing from coverage report")
            continue
        actual = float(details["summary"]["percent_covered"])
        if actual < threshold:
            failures.append(f"{path}: {actual:.2f}% < {threshold:.2f}%")
    if failures:
        print("Critical coverage gate failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Critical coverage gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
