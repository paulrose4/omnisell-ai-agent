from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.config import get_settings  # noqa: E402
from application.agent_payload_audit import (  # noqa: E402
    render_agent_payload_scan_report,
    scan_agent_payload_database,
)
from infrastructure.database.session import create_session_factory  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only compatibility scan for persisted Agent analysis payloads.")
    parser.add_argument(
        "--tenant-id",
        action="append",
        required=True,
        help="Tenant ID to scan. Repeat for each explicitly approved tenant.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional database URL. Defaults to the configured application database without printing it.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "agent-payload-compatibility-report.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    database_url = args.database_url or get_settings().database_url
    report = scan_agent_payload_database(
        create_session_factory(database_url),
        tenant_ids=args.tenant_id,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_agent_payload_scan_report(report), encoding="utf-8")
    totals = report["totals"]
    print(f"Wrote read-only Agent payload compatibility report: {output}")
    print(
        "records={records_scanned} compatible={compatible_records} rejected={rejected_records} empty={empty_payload_records}".format(
            **totals
        )
    )
    return 1 if totals["rejected_records"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
