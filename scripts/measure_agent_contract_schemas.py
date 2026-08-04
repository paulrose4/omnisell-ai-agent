from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from openai.lib._pydantic import to_strict_json_schema

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.contracts import (
    CustomerCaseAnalysisDraft,
    ListingOptimizationAnalysisDraft,
    OperationsBriefAnalysisDraft,
    ReconciliationInvestigationDraft,
)
from model_gateway.gateway import compact_transport_schema

SCHEMA_NAMES = (
    "OperationsBriefAnalysisDraft",
    "CustomerCaseAnalysisDraft",
    "ListingOptimizationAnalysisDraft",
    "ReconciliationInvestigationDraft",
)
CURRENT_SCHEMAS = {
    schema.__name__: schema
    for schema in (
        OperationsBriefAnalysisDraft,
        CustomerCaseAnalysisDraft,
        ListingOptimizationAnalysisDraft,
        ReconciliationInvestigationDraft,
    )
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure model-facing Agent JSON Schema boundaries.")
    parser.add_argument("--baseline-ref", default="00de543")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evidence/agent-contracts/schema-size-v1-v2.json"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    baseline_module = _load_contracts_from_git(args.baseline_ref)
    baseline_schemas = {name: getattr(baseline_module, name) for name in SCHEMA_NAMES}
    report = {
        "report_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline_ref": args.baseline_ref,
        "token_estimation_rule": "ceil(compact_schema_utf8_bytes / 1.8)",
        "schemas": {
            name: {
                "baseline": _measure(baseline_schemas[name]),
                "current": _measure(CURRENT_SCHEMAS[name]),
            }
            for name in SCHEMA_NAMES
        },
    }
    for values in report["schemas"].values():
        values["change"] = {
            "compact_chars": values["current"]["compact_chars"] - values["baseline"]["compact_chars"],
            "estimated_tokens": values["current"]["estimated_tokens"] - values["baseline"]["estimated_tokens"],
            "open_map_count": values["current"]["open_map_count"] - values["baseline"]["open_map_count"],
        }

    comparable = _comparable_json(report)
    if args.check:
        if not args.output.exists():
            raise SystemExit(f"Schema evidence is missing: {args.output}")
        committed = json.loads(args.output.read_text(encoding="utf-8"))
        if _comparable_json(committed) != comparable:
            raise SystemExit("Agent contract schema evidence is stale; rerun the measurement script")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return 0


def _load_contracts_from_git(ref: str) -> ModuleType:
    source = subprocess.run(
        ["git", "show", f"{ref}:agents/contracts.py"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    name = f"_omnisell_contracts_{ref.replace('-', '_')}"
    spec = importlib.util.spec_from_loader(name, loader=None)
    if spec is None:
        raise RuntimeError("Could not create baseline contracts module")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = f"git:{ref}:agents/contracts.py"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _measure(schema: Any) -> dict[str, int]:
    strict = to_strict_json_schema(schema)
    compact = compact_transport_schema(strict)
    raw_json = json.dumps(strict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    compact_json = json.dumps(compact, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    compact_bytes = len(compact_json.encode("utf-8"))
    return {
        "raw_chars": len(raw_json),
        "compact_chars": len(compact_json),
        "compact_utf8_bytes": compact_bytes,
        "estimated_tokens": math.ceil(compact_bytes / 1.8),
        "open_map_count": _count_open_maps(strict),
        "max_length_constraints": _count_key(strict, "maxLength"),
        "max_items_constraints": _count_key(strict, "maxItems"),
    }


def _count_open_maps(value: Any) -> int:
    if isinstance(value, dict):
        current = int("additionalProperties" in value and value["additionalProperties"] is not False)
        return current + sum(_count_open_maps(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_open_maps(item) for item in value)
    return 0


def _count_key(value: Any, key: str) -> int:
    if isinstance(value, dict):
        return int(key in value) + sum(_count_key(item, key) for item in value.values())
    if isinstance(value, list):
        return sum(_count_key(item, key) for item in value)
    return 0


def _comparable_json(report: dict[str, Any]) -> str:
    normalized = dict(report)
    normalized.pop("generated_at", None)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


if __name__ == "__main__":
    raise SystemExit(main())
