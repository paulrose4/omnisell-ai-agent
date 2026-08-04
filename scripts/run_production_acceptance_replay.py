from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.production_acceptance import (
    evaluate_replay,
    generate_blind_review_template,
    generate_synthetic_replay,
    render_markdown_report,
)


DEFAULT_OUTPUT_DIR = Path("artifacts/production-acceptance")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic OmniSell production acceptance replay.")
    parser.add_argument("--days", type=int, default=90, choices=range(30, 91), metavar="30-90")
    parser.add_argument("--end-at", help="Timezone-aware ISO timestamp used to make the replay reproducible.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    arguments = parser.parse_args()

    end_at = datetime.now(UTC) if arguments.end_at is None else datetime.fromisoformat(arguments.end_at)
    if end_at.tzinfo is None:
        parser.error("--end-at must include a timezone offset")

    dataset = generate_synthetic_replay(days=arguments.days, end_at=end_at)
    report = evaluate_replay(dataset)
    review_template = generate_blind_review_template(_blind_review_candidates(report))

    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "dataset": arguments.output_dir / "amazon-operations-90d.json",
        "report_json": arguments.output_dir / "production-acceptance-report.json",
        "report_markdown": arguments.output_dir / "production-acceptance-report.md",
        "blind_review": arguments.output_dir / "expert-blind-review-template.json",
    }
    _write_json(paths["dataset"], dataset)
    _write_json(paths["report_json"], report)
    paths["report_markdown"].write_text(render_markdown_report(report), encoding="utf-8")
    _write_json(paths["blind_review"], review_template)

    print(json.dumps({
        "decision": report["decisions"],
        "synthetic_metrics": report["synthetic_metrics"],
        "outputs": {name: str(path.resolve()) for name, path in paths.items()},
    }, ensure_ascii=False, indent=2))
    return 0 if report["gates"]["synthetic_rule_replay"]["status"] == "passed" else 1


def _blind_review_candidates(report: dict[str, Any]) -> list[dict[str, str]]:
    sample = report.get("detection_sample", [])
    candidates: list[dict[str, str]] = []
    for index, finding in enumerate(sample[:4], start=1):
        case_id = f"replay-case-{index}"
        candidates.extend([
            {
                "case_id": case_id,
                "candidate_id": "A",
                "content": (
                    f"发现 {finding['seller_sku']} 的 {finding['code']}。请复核证据、负责人和 SLA，"
                    "不要执行 Amazon 写入。"
                ),
            },
            {
                "case_id": case_id,
                "candidate_id": "B",
                "content": "待填入独立人工建议或真实 Advisor 建议。",
            },
        ])
    return candidates


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
