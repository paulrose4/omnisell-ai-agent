from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.agent_quality import (
    evaluate_replay_records,
    load_dataset_catalog,
    load_replay_records,
    render_evaluation_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic OmniSell Agent output replay evaluation.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs" / "evidence" / "agent-evals",
    )
    args = parser.parse_args()

    catalog = load_dataset_catalog(ROOT / "evals" / "datasets")
    records = load_replay_records(ROOT / "evals" / "fixtures" / "agent_outputs_v1.jsonl")
    report = evaluate_replay_records(catalog=catalog, records=records)
    report_json = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    report_markdown = render_evaluation_markdown(report)
    json_path = args.output_dir / "evaluation-report-v1.json"
    markdown_path = args.output_dir / "EVALUATION_REPORT.md"
    review_path = args.output_dir / "operator-review-template-v1.csv"

    if args.check:
        if not json_path.exists() or json.loads(json_path.read_text(encoding="utf-8")) != report:
            raise SystemExit("Versioned Agent evaluation JSON report is missing or stale")
        if not markdown_path.exists() or markdown_path.read_text(encoding="utf-8") != report_markdown:
            raise SystemExit("Versioned Agent evaluation Markdown report is missing or stale")
        if not review_path.exists():
            raise SystemExit("Named operator review template is missing")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(report_json.encode("utf-8"))
    markdown_path.write_bytes(report_markdown.encode("utf-8"))
    _write_operator_template(path=review_path, case_ids=[case.case_id for case in catalog.cases])
    return 0


def _write_operator_template(*, path: Path, case_ids: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "case_id",
                "candidate_id",
                "reviewer_name",
                "reviewer_role",
                "reviewed_at_utc",
                "factual_grounding_0_2",
                "evidence_citation_0_2",
                "missing_data_honesty_0_2",
                "operational_usefulness_0_2",
                "next_step_clarity_0_2",
                "policy_safety_0_2",
                "language_quality_0_2",
                "edit_level",
                "adoption_decision",
                "review_notes",
            ]
        )
        for case_id in case_ids:
            writer.writerow([case_id, "candidate-a", "", "", "", "", "", "", "", "", "", "", "", "", ""])


if __name__ == "__main__":
    raise SystemExit(main())
