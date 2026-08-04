from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.advisors.customer_case import create_customer_case_advisor
from agents.advisors.listing_optimization import create_listing_optimization_advisor
from agents.advisors.operations_brief import create_operations_brief_advisor
from agents.advisors.reconciliation import create_reconciliation_investigator
from agents.runtime import AgentExecutionContext
from api.config import Settings
from evals.agent_quality import (
    GenerationMetadata,
    ReplayRecord,
    evaluate_replay_records,
    load_dataset_catalog,
    render_evaluation_markdown,
)
from model_gateway.factory import create_configured_gateway

WORKFLOW_BY_AGENT = {
    "operations_brief_analyst": "operations_brief_analysis",
    "customer_case_advisor": "customer_case_analysis",
    "listing_optimization_advisor": "listing_optimization_analysis",
    "reconciliation_investigator": "reconciliation_investigation",
}


async def run(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    if os.getenv("REAL_MODEL_BATCH_EVAL", "").lower() not in {"1", "true", "yes"}:
        raise SystemExit("Set REAL_MODEL_BATCH_EVAL=true to run paid external evaluation.")
    settings = Settings()
    if settings.environment.lower() == "production":
        raise SystemExit("Real-model batch evaluation refuses ENVIRONMENT=production.")

    catalog = load_dataset_catalog(ROOT / "evals" / "datasets")
    gateway = create_configured_gateway(settings)
    advisors = {
        "operations_brief_analyst": create_operations_brief_advisor(gateway=gateway, model=settings.model_name),
        "customer_case_advisor": create_customer_case_advisor(gateway=gateway, model=settings.model_name),
        "listing_optimization_advisor": create_listing_optimization_advisor(gateway=gateway, model=settings.model_name),
        "reconciliation_investigator": create_reconciliation_investigator(gateway=gateway, model=settings.model_name),
    }
    selected = [case for case in catalog.cases if not args.agent or case.agent_type in args.agent]
    records: list[ReplayRecord] = []
    for case in selected:
        workflow_type = WORKFLOW_BY_AGENT[case.agent_type]
        profile = settings.agent_budget_profiles[workflow_type]
        context = AgentExecutionContext(
            tenant_id=case.tenant_id,
            actor_id="real-model-eval-operator",
            workflow_run_id=f"real-eval:{case.case_id}:{datetime.now(UTC).timestamp()}",
            correlation_id=f"real-eval:{case.case_id}",
            requested_at=case.requested_at,
            locale=case.locale,
            allowed_capabilities=frozenset({"read_evidence", "draft_only"}),
            max_prompt_tokens=int(profile["max_prompt_tokens"]),
            max_completion_tokens=int(profile["max_completion_tokens"]),
            max_estimated_cost=case.max_estimated_cost,
            max_evidence_items=int(profile["max_evidence_items"]),
            max_evidence_chars=int(profile["max_evidence_chars"]),
            max_total_evidence_chars=int(profile["max_total_evidence_chars"]),
            max_request_bytes=int(profile["max_request_bytes"]),
            timeout_seconds=int(profile["timeout_seconds"]),
        )
        advisor = advisors[case.agent_type]
        try:
            output = await advisor.analyze(context=context, evidence=case.evidence_bundle)
            usage = output.model_usage
            generation = GenerationMetadata(
                source="real_provider",
                provider=usage.provider if usage else settings.model_provider,
                model=usage.model if usage else settings.model_name,
                prompt_hash=usage.prompt_hash if usage else advisor.prompt_hash,
                schema_hash=hashlib.sha256(
                    json.dumps(advisor.model_output_schema.model_json_schema(), sort_keys=True).encode("utf-8")
                ).hexdigest(),
                pricing_version=settings.model_pricing_version,
                request_id_present=bool(usage.request_id) if usage else False,
                first_pass_schema_success=usage.first_pass_schema_success if usage else False,
                repair_attempted=usage.repair_attempted if usage else False,
                final_structured_success=True,
                latency_ms=usage.latency_ms if usage else 0,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                estimated_cost=usage.estimated_cost if usage else 0,
                error_category=None,
            )
            payload = output.model_dump(mode="json")
        except Exception as exc:
            generation = GenerationMetadata(
                source="real_provider",
                provider=settings.model_provider,
                model=settings.model_name,
                prompt_hash=advisor.prompt_hash,
                schema_hash=hashlib.sha256(
                    json.dumps(advisor.model_output_schema.model_json_schema(), sort_keys=True).encode("utf-8")
                ).hexdigest(),
                pricing_version=settings.model_pricing_version,
                request_id_present=False,
                first_pass_schema_success=False,
                repair_attempted=False,
                final_structured_success=False,
                latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost=0,
                error_category=type(exc).__name__,
            )
            payload = {}
        records.append(ReplayRecord(case_id=case.case_id, output=payload, generation=generation))

    raw_path = Path(args.raw_output)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        "".join(
            json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=True) + "\n" for record in records
        ).encode("utf-8")
    )
    report = evaluate_replay_records(
        catalog=catalog.model_copy(update={"cases": tuple(selected)}),
        records=records,
    )
    summary_dir = Path(args.summary_dir)
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "evaluation-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    (summary_dir / "EVALUATION_REPORT.md").write_text(render_evaluation_markdown(report), encoding="utf-8")
    close = getattr(gateway, "close", None)
    if close is not None:
        await close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in real-provider Agent batch evaluation.")
    parser.add_argument("--agent", action="append", choices=tuple(WORKFLOW_BY_AGENT))
    parser.add_argument(
        "--raw-output",
        default=str(ROOT / "artifacts" / "external-validation" / "real-model-eval-records.jsonl"),
    )
    parser.add_argument(
        "--summary-dir",
        default=str(ROOT / "artifacts" / "external-validation" / "real-model-eval-summary"),
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
