from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from domain.operations_learning import (
    ActivityEvent,
    IssueFeedbackLabel,
    assess_action_outcome,
    calculate_labor_metrics,
    classify_ad_execution,
    evaluate_issue_feedback,
    generate_calibration_proposal,
    prepare_attribution_plan,
)
from infrastructure.database.models import (
    ActionExecutionAttemptRow,
    ActionOutcomeRow,
    AttributionPlanRow,
    BusinessContextEventRow,
    CalibrationProposalRow,
    ExecutionAuthorizationRow,
    IssueFeedbackRow,
    MissedIssueReportRow,
    OperationalIssueRow,
    OperationalWorkItemRow,
    OperatorActivityEventRow,
    RuleVersionRow,
)


class OperationsLearningRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def record_issue_feedback(
        self,
        *,
        tenant_id: str,
        issue_id: str,
        actor_id: str,
        is_correct: bool,
        is_useful: bool,
        reason_code: str,
        rule_version: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            issue = session.scalar(
                select(OperationalIssueRow).where(
                    OperationalIssueRow.tenant_id == tenant_id,
                    OperationalIssueRow.id == issue_id,
                )
            )
            if issue is None:
                raise LookupError("operational issue not found")
            label = IssueFeedbackLabel(issue_id, issue.severity, is_correct, is_useful, reason_code)
            row = session.scalar(
                select(IssueFeedbackRow).where(
                    IssueFeedbackRow.tenant_id == tenant_id,
                    IssueFeedbackRow.operational_issue_id == issue_id,
                    IssueFeedbackRow.actor_id == actor_id,
                )
            )
            if row is None:
                row = IssueFeedbackRow(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    operational_issue_id=issue_id,
                    actor_id=actor_id,
                    created_at=now,
                )
                session.add(row)
            row.severity = label.severity
            row.is_correct = label.is_correct
            row.is_useful = label.is_useful
            row.reason_code = label.reason_code
            row.rule_version = rule_version
            row.note = note
            row.updated_at = now
            session.flush()
            return _feedback_dict(row)

    def list_issue_feedback(self, *, tenant_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(IssueFeedbackRow)
                    .where(
                        IssueFeedbackRow.tenant_id == tenant_id,
                    )
                    .order_by(IssueFeedbackRow.created_at.desc())
                )
            )
        return [_feedback_dict(row) for row in rows]

    def report_missed_issue(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        marketplace_id: str,
        seller_sku: str,
        issue_type: str,
        severity: str,
        description: str,
        evidence_manifest: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid missed issue severity")
        if not description.strip() or not evidence_manifest:
            raise ValueError("missed issue requires description and evidence")
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = MissedIssueReportRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                actor_id=actor_id,
                marketplace_id=marketplace_id,
                seller_sku=seller_sku,
                issue_type=issue_type,
                severity=severity,
                description=description.strip(),
                evidence_manifest=_json_safe(evidence_manifest),
                status="open",
                created_at=now,
            )
            session.add(row)
            session.flush()
            return _missed_dict(row)

    def list_missed_issues(self, *, tenant_id: str, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(MissedIssueReportRow).where(MissedIssueReportRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(MissedIssueReportRow.status == status)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(MissedIssueReportRow.created_at.desc())))
        return [_missed_dict(row) for row in rows]

    def record_activity(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        event_type: str,
        occurred_at: datetime,
        idempotency_key: str,
    ) -> dict[str, Any]:
        event = ActivityEvent(work_item_id, actor_id, event_type, occurred_at)
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(OperatorActivityEventRow).where(
                    OperatorActivityEventRow.tenant_id == tenant_id,
                    OperatorActivityEventRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return _activity_dict(existing)
            work_item = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == work_item_id,
                )
            )
            if work_item is None:
                raise LookupError("operational work item not found")
            previous = session.scalars(
                select(OperatorActivityEventRow)
                .where(
                    OperatorActivityEventRow.tenant_id == tenant_id,
                    OperatorActivityEventRow.work_item_id == work_item_id,
                    OperatorActivityEventRow.actor_id == actor_id,
                )
                .order_by(OperatorActivityEventRow.occurred_at)
            ).all()
            _validate_activity_transition(previous[-1].event_type if previous else None, event.event_type)
            if previous and _utc(previous[-1].occurred_at) > event.occurred_at:
                raise ValueError("activity events must be chronological")
            row = OperatorActivityEventRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                actor_id=actor_id,
                event_type=event_type,
                occurred_at=event.occurred_at,
                idempotency_key=idempotency_key,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            session.flush()
            return _activity_dict(row)

    def labor_metrics(self, *, tenant_id: str, work_item_id: str, actor_id: str) -> dict[str, int | None]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(OperatorActivityEventRow)
                    .where(
                        OperatorActivityEventRow.tenant_id == tenant_id,
                        OperatorActivityEventRow.work_item_id == work_item_id,
                        OperatorActivityEventRow.actor_id == actor_id,
                    )
                    .order_by(OperatorActivityEventRow.occurred_at)
                )
            )
        return calculate_labor_metrics(
            [ActivityEvent(row.work_item_id, row.actor_id, row.event_type, _utc(row.occurred_at)) for row in rows]
        )

    def list_activities(
        self, *, tenant_id: str, work_item_id: str, actor_id: str | None = None
    ) -> list[dict[str, Any]]:
        statement = select(OperatorActivityEventRow).where(
            OperatorActivityEventRow.tenant_id == tenant_id,
            OperatorActivityEventRow.work_item_id == work_item_id,
        )
        if actor_id:
            statement = statement.where(OperatorActivityEventRow.actor_id == actor_id)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(OperatorActivityEventRow.occurred_at)))
        return [_activity_dict(row) for row in rows]

    def save_business_context(self, *, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        starts_at = _datetime(payload["starts_at"])
        ends_at = _datetime(payload["ends_at"])
        if ends_at <= starts_at:
            raise ValueError("business context end must follow start")
        source = str(payload.get("source") or "manual")
        verified = bool(payload.get("verified_amazon_data", False))
        if verified and not source.startswith("amazon_"):
            raise ValueError("verified business context requires Amazon API source")
        dedupe_key = sha256(
            f"{tenant_id}:{payload['marketplace_id']}:{payload.get('seller_sku')}:{payload['context_type']}:{starts_at.isoformat()}:{ends_at.isoformat()}".encode()
        ).hexdigest()
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(BusinessContextEventRow).where(
                    BusinessContextEventRow.tenant_id == tenant_id,
                    BusinessContextEventRow.dedupe_key == dedupe_key,
                )
            )
            if row is None:
                row = BusinessContextEventRow(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    dedupe_key=dedupe_key,
                    marketplace_id=payload["marketplace_id"],
                    seller_sku=payload.get("seller_sku"),
                    context_type=payload["context_type"],
                    starts_at=starts_at,
                    ends_at=ends_at,
                    details=_json_safe(payload.get("details", {})),
                    source=source,
                    verified_amazon_data=verified,
                    created_at=datetime.now(UTC),
                )
                session.add(row)
            session.flush()
            return _context_dict(row)

    def record_execution(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        actor_id: str,
        action_hash: str,
        exact_parameters: dict[str, Any],
        execution_evidence: dict[str, Any],
        executed_at: datetime,
        idempotency_key: str,
        authorization_id: str | None = None,
    ) -> dict[str, Any]:
        if executed_at.tzinfo is None:
            raise ValueError("executed_at must be timezone-aware")
        if not execution_evidence:
            raise ValueError("manual execution evidence is required")
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(ActionExecutionAttemptRow).where(
                    ActionExecutionAttemptRow.tenant_id == tenant_id,
                    ActionExecutionAttemptRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return _execution_dict(existing)
            item = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == work_item_id,
                )
            )
            if item is None:
                raise LookupError("operational work item not found")
            if action_hash != item.action_hash:
                raise ValueError("action hash no longer matches the approved work item")
            authorization = None
            if authorization_id:
                authorization = session.scalar(
                    select(ExecutionAuthorizationRow).where(
                        ExecutionAuthorizationRow.tenant_id == tenant_id,
                        ExecutionAuthorizationRow.id == authorization_id,
                        ExecutionAuthorizationRow.work_item_id == work_item_id,
                    )
                )
                if authorization is None:
                    raise LookupError("execution authorization not found")
                if authorization.status != "approved" or _utc(authorization.expires_at) <= now:
                    raise ValueError("execution authorization is not approved or has expired")
                if authorization.action_hash != action_hash:
                    raise ValueError("execution authorization action hash mismatch")
            row = ActionExecutionAttemptRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                action_hash=action_hash,
                execution_mode="manual_confirm" if authorization else "manual_only",
                status="recorded",
                exact_parameters=_json_safe(exact_parameters),
                execution_evidence=_json_safe(execution_evidence),
                actor_id=actor_id,
                executed_at=executed_at.astimezone(UTC),
                amazon_write_performed=False,
                idempotency_key=idempotency_key,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            if authorization is not None:
                authorization.status = "consumed"
                authorization.updated_at = now
            item.status = "verifying"
            item.updated_at = datetime.now(UTC)
            session.flush()
            return _execution_dict(row)

    def evaluate_outcome(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        execution_id: str,
        primary_metric: str,
        desired_direction: str,
        baseline_metrics: dict[str, Any],
        result_metrics: dict[str, Any],
        guardrail_metrics: dict[str, Any],
        observation_start: datetime,
        observation_end: datetime,
        confounders: list[str],
        currency: str | None = None,
        control_metrics: dict[str, Any] | None = None,
        randomized_experiment: bool = False,
        minimum_sample_satisfied: bool = False,
    ) -> dict[str, Any]:
        start = _datetime(observation_start)
        end = _datetime(observation_end)
        if end <= start:
            raise ValueError("observation end must follow start")
        with self.session_factory.begin() as session:
            execution = session.scalar(
                select(ActionExecutionAttemptRow).where(
                    ActionExecutionAttemptRow.tenant_id == tenant_id,
                    ActionExecutionAttemptRow.id == execution_id,
                    ActionExecutionAttemptRow.work_item_id == work_item_id,
                )
            )
            item = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == work_item_id,
                )
            )
            if execution is None or item is None:
                raise LookupError("execution or work item not found")
            assessment = assess_action_outcome(
                baseline_metrics=baseline_metrics,
                result_metrics=result_metrics,
                primary_metric=primary_metric,
                desired_direction=desired_direction,
                execution_verified=execution.status == "recorded" and not execution.amazon_write_performed,
                confounders=confounders,
                control_metrics=control_metrics,
                randomized_experiment=randomized_experiment,
                minimum_sample_satisfied=minimum_sample_satisfied,
            )
            existing = session.scalar(
                select(ActionOutcomeRow).where(
                    ActionOutcomeRow.tenant_id == tenant_id,
                    ActionOutcomeRow.execution_id == execution_id,
                    ActionOutcomeRow.primary_metric == primary_metric,
                    ActionOutcomeRow.observation_end == end,
                )
            )
            now = datetime.now(UTC)
            row = existing or ActionOutcomeRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                execution_id=execution_id,
                primary_metric=primary_metric,
                observation_end=end,
            )
            if existing is None:
                session.add(row)
            row.desired_direction = desired_direction
            row.baseline_metrics = _json_safe(baseline_metrics)
            row.result_metrics = _json_safe(result_metrics)
            row.guardrail_metrics = _json_safe(guardrail_metrics)
            row.observation_start = start
            row.confounders = list(assessment["confounders"])
            row.control_metrics = _json_safe(control_metrics or {})
            row.randomized_experiment = randomized_experiment
            row.minimum_sample_satisfied = minimum_sample_satisfied
            row.attribution_level = assessment["attribution_level"]
            row.outcome_status = assessment["outcome_status"]
            row.incremental_value = assessment["incremental_value"]
            row.currency = currency.upper() if currency else None
            row.causal_claim_allowed = assessment["causal_claim_allowed"]
            row.recommended_decision = (
                "rollback"
                if assessment["outcome_status"] == "guardrail_regression"
                else "keep"
                if assessment["outcome_status"] == "directional_improvement"
                else "review"
            )
            row.evaluated_at = now
            item.result_metrics = _json_safe(
                {
                    **result_metrics,
                    "attribution_level": assessment["attribution_level"],
                    "outcome_status": assessment["outcome_status"],
                    "incremental_value": assessment["incremental_value"],
                    "causal_claim_allowed": assessment["causal_claim_allowed"],
                }
            )
            item.verification_attempts = (item.verification_attempts or 0) + 1
            if assessment["outcome_status"] in {"directional_improvement", "no_material_change"}:
                item.verification_status = "succeeded"
                item.status = "completed"
                item.completed_at = now
            elif assessment["outcome_status"] == "guardrail_regression":
                item.verification_status = "failed"
                item.status = "verifying"
            else:
                item.verification_status = "inconclusive"
                item.status = "verifying"
            item.updated_at = now
            session.flush()
            return _outcome_dict(row)

    def create_rule_version(
        self,
        *,
        tenant_id: str,
        rule_key: str,
        version: str,
        parameters: dict[str, Any],
        created_by: str,
        status: str = "draft",
        parent_version: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"draft", "shadow", "shadow_ready", "active"}:
            raise ValueError("invalid rule version status")
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            if (
                session.scalar(
                    select(RuleVersionRow).where(
                        RuleVersionRow.tenant_id == tenant_id,
                        RuleVersionRow.rule_key == rule_key,
                        RuleVersionRow.version == version,
                    )
                )
                is not None
            ):
                raise ValueError("rule version already exists")
            if status == "active":
                for previous in session.scalars(
                    select(RuleVersionRow).where(
                        RuleVersionRow.tenant_id == tenant_id,
                        RuleVersionRow.rule_key == rule_key,
                        RuleVersionRow.status == "active",
                    )
                ):
                    previous.status = "retired"
                    previous.updated_at = now
            row = RuleVersionRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                rule_key=rule_key,
                version=version,
                status=status,
                parameters=_json_safe(parameters),
                evaluation_metrics={},
                source="manual",
                parent_version=parent_version,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return _rule_version_dict(row)

    def list_rule_versions(self, *, tenant_id: str, rule_key: str | None = None) -> list[dict[str, Any]]:
        statement = select(RuleVersionRow).where(RuleVersionRow.tenant_id == tenant_id)
        if rule_key:
            statement = statement.where(RuleVersionRow.rule_key == rule_key)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(RuleVersionRow.created_at.desc())))
        return [_rule_version_dict(row) for row in rows]

    def create_calibration_proposal(
        self,
        *,
        tenant_id: str,
        rule_key: str,
        base_version: str,
        current_parameters: dict[str, Any],
        candidate_parameters: dict[str, Any],
        replay_metrics: dict[str, Any],
        proposed_by: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            labels = list(
                session.scalars(
                    select(IssueFeedbackRow).where(
                        IssueFeedbackRow.tenant_id == tenant_id,
                        IssueFeedbackRow.rule_version == base_version,
                    )
                )
            )
        candidate_hash = sha256(
            json.dumps(
                {
                    "rule_key": rule_key,
                    "base_version": base_version,
                    "candidate_parameters": _json_safe(candidate_parameters),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        proposal = generate_calibration_proposal(
            rule_key=rule_key,
            current_version=base_version,
            current_parameters=current_parameters,
            candidate_parameters=candidate_parameters,
            label_count=len(labels),
            replay_metrics=replay_metrics,
        )
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(CalibrationProposalRow).where(
                    CalibrationProposalRow.tenant_id == tenant_id,
                    CalibrationProposalRow.candidate_hash == candidate_hash,
                )
            )
            if existing is not None:
                return _calibration_dict(existing)
            row = CalibrationProposalRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                rule_key=rule_key,
                base_version=base_version,
                candidate_hash=candidate_hash,
                current_parameters=_json_safe(current_parameters),
                candidate_parameters=_json_safe(candidate_parameters),
                replay_metrics=_json_safe(replay_metrics),
                label_count=len(labels),
                status=proposal["status"],
                reason=proposal.get("reason"),
                proposed_by=proposed_by,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            result = _calibration_dict(row)
            result["eligible"] = proposal["eligible"]
            result["publish_mode"] = proposal["publish_mode"]
            return result

    def list_calibration_proposals(self, *, tenant_id: str, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(CalibrationProposalRow).where(CalibrationProposalRow.tenant_id == tenant_id)
        if status:
            statement = statement.where(CalibrationProposalRow.status == status)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(CalibrationProposalRow.created_at.desc())))
        return [_calibration_dict(row) for row in rows]

    def approve_calibration_proposal(self, *, tenant_id: str, proposal_id: str, approver_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            proposal = session.scalar(
                select(CalibrationProposalRow).where(
                    CalibrationProposalRow.tenant_id == tenant_id,
                    CalibrationProposalRow.id == proposal_id,
                )
            )
            if proposal is None:
                raise LookupError("calibration proposal not found")
            if proposal.status != "pending_approval":
                raise ValueError("calibration proposal is not awaiting approval")
            if proposal.proposed_by == approver_id:
                raise ValueError("calibration proposer cannot approve their own proposal")
            version = f"{proposal.base_version}.candidate.{proposal.id[:8]}"
            rule = RuleVersionRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                rule_key=proposal.rule_key,
                version=version,
                status="shadow",
                parameters=proposal.candidate_parameters or {},
                evaluation_metrics=proposal.replay_metrics or {},
                source="calibration_proposal",
                parent_version=proposal.base_version,
                created_by=proposal.proposed_by,
                approved_by=approver_id,
                shadow_started_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(rule)
            proposal.status = "shadow"
            proposal.approved_by = approver_id
            proposal.approved_at = now
            proposal.updated_at = now
            session.flush()
            return {"proposal": _calibration_dict(proposal), "rule_version": _rule_version_dict(rule)}

    def complete_rule_shadow(
        self,
        *,
        tenant_id: str,
        rule_version_id: str,
        shadow_metrics: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        recall = _decimal_or_none(shadow_metrics.get("critical_high_recall"))
        false_positive = _decimal_or_none(shadow_metrics.get("false_positive_rate"))
        if (
            recall is None
            or recall < Decimal("0.95")
            or (false_positive is not None and false_positive > Decimal("0.15"))
        ):
            raise ValueError("shadow metrics do not meet publication gates")
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(RuleVersionRow).where(
                    RuleVersionRow.tenant_id == tenant_id,
                    RuleVersionRow.id == rule_version_id,
                )
            )
            if row is None:
                raise LookupError("rule version not found")
            if row.status != "shadow":
                raise ValueError("rule version is not in shadow")
            if row.shadow_started_at is None or now - _utc(row.shadow_started_at) < timedelta(days=7):
                raise ValueError("rule version must complete at least seven shadow days")
            row.status = "shadow_ready"
            row.evaluation_metrics = _json_safe(
                {**(row.evaluation_metrics or {}), **shadow_metrics, "shadow_completed_by": actor_id}
            )
            row.shadow_ended_at = now
            row.updated_at = now
            session.flush()
            return _rule_version_dict(row)

    def publish_rule_version(self, *, tenant_id: str, rule_version_id: str, actor_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(RuleVersionRow).where(
                    RuleVersionRow.tenant_id == tenant_id,
                    RuleVersionRow.id == rule_version_id,
                )
            )
            if row is None:
                raise LookupError("rule version not found")
            if row.status != "shadow_ready":
                raise ValueError("rule version must pass shadow gates before publishing")
            for previous in session.scalars(
                select(RuleVersionRow).where(
                    RuleVersionRow.tenant_id == tenant_id,
                    RuleVersionRow.rule_key == row.rule_key,
                    RuleVersionRow.status == "active",
                )
            ):
                previous.status = "retired"
                previous.updated_at = now
            row.status = "active"
            row.approved_by = row.approved_by or actor_id
            row.updated_at = now
            session.flush()
            return _rule_version_dict(row)

    def rollback_rule_version(
        self,
        *,
        tenant_id: str,
        active_rule_version_id: str,
        target_rule_version_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if active_rule_version_id == target_rule_version_id:
            raise ValueError("rollback target must differ from the active version")
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            active = session.scalar(
                select(RuleVersionRow).where(
                    RuleVersionRow.tenant_id == tenant_id,
                    RuleVersionRow.id == active_rule_version_id,
                )
            )
            target = session.scalar(
                select(RuleVersionRow).where(
                    RuleVersionRow.tenant_id == tenant_id,
                    RuleVersionRow.id == target_rule_version_id,
                )
            )
            if active is None or target is None:
                raise LookupError("active or rollback rule version not found")
            if (
                active.rule_key != target.rule_key
                or active.status != "active"
                or target.status not in {"retired", "rolled_back"}
            ):
                raise ValueError("invalid rule rollback transition")
            active.status = "rolled_back"
            active.evaluation_metrics = _json_safe(
                {**(active.evaluation_metrics or {}), "rolled_back_by": actor_id, "rolled_back_at": now}
            )
            active.updated_at = now
            target.status = "active"
            target.evaluation_metrics = _json_safe(
                {**(target.evaluation_metrics or {}), "restored_by": actor_id, "restored_at": now}
            )
            target.updated_at = now
            session.flush()
            return {"rolled_back": _rule_version_dict(active), "restored": _rule_version_dict(target)}

    def prepare_execution_authorization(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        action_hash: str,
        action_type: str,
        exact_parameters: dict[str, Any],
        policy_input: dict[str, Any],
        before_snapshot: dict[str, Any],
        requested_by: str,
        idempotency_key: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        policy = classify_ad_execution(action_type=action_type, **policy_input)
        if not policy["eligible"]:
            raise ValueError("execution preflight blocked: " + "; ".join(policy["reasons"]))
        expiry = _datetime(expires_at)
        now = datetime.now(UTC)
        if expiry <= now:
            raise ValueError("execution authorization must expire in the future")
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(ExecutionAuthorizationRow).where(
                    ExecutionAuthorizationRow.tenant_id == tenant_id,
                    ExecutionAuthorizationRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return _authorization_dict(existing)
            item = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == work_item_id,
                )
            )
            if item is None:
                raise LookupError("operational work item not found")
            if item.action_hash != action_hash:
                raise ValueError("action hash no longer matches the work item")
            row = ExecutionAuthorizationRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                action_hash=action_hash,
                action_type=action_type,
                risk_level=policy["risk_level"],
                status="pending_approval" if policy["requires_approval"] else "approved",
                exact_parameters=_json_safe(exact_parameters),
                preflight_evidence=_json_safe({**policy_input, "before_snapshot": before_snapshot, **policy}),
                requested_by=requested_by,
                approved_by=requested_by if not policy["requires_approval"] else None,
                approved_at=now if not policy["requires_approval"] else None,
                expires_at=expiry,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return _authorization_dict(row)

    def approve_execution_authorization(
        self, *, tenant_id: str, authorization_id: str, approver_id: str
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ExecutionAuthorizationRow).where(
                    ExecutionAuthorizationRow.tenant_id == tenant_id,
                    ExecutionAuthorizationRow.id == authorization_id,
                )
            )
            if row is None:
                raise LookupError("execution authorization not found")
            if row.status != "pending_approval":
                raise ValueError("execution authorization is not awaiting approval")
            if row.requested_by == approver_id:
                raise ValueError("execution requester cannot approve their own medium/high risk action")
            if _utc(row.expires_at) <= now:
                row.status = "expired"
                row.updated_at = now
                raise ValueError("execution authorization has expired")
            row.status = "approved"
            row.approved_by = approver_id
            row.approved_at = now
            row.updated_at = now
            session.flush()
            return _authorization_dict(row)

    def revoke_execution_authorization(self, *, tenant_id: str, authorization_id: str, actor_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ExecutionAuthorizationRow).where(
                    ExecutionAuthorizationRow.tenant_id == tenant_id,
                    ExecutionAuthorizationRow.id == authorization_id,
                )
            )
            if row is None:
                raise LookupError("execution authorization not found")
            if row.status not in {"approved", "pending_approval"}:
                raise ValueError("only pending or approved authorization can be revoked")
            row.status = "revoked"
            row.preflight_evidence = _json_safe(
                {**(row.preflight_evidence or {}), "revoked_by": actor_id, "revoked_at": now}
            )
            row.updated_at = now
            session.flush()
            return _authorization_dict(row)

    def list_execution_authorizations(self, *, tenant_id: str, work_item_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(ExecutionAuthorizationRow).where(ExecutionAuthorizationRow.tenant_id == tenant_id)
        if work_item_id:
            statement = statement.where(ExecutionAuthorizationRow.work_item_id == work_item_id)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(ExecutionAuthorizationRow.created_at.desc())))
        return [_authorization_dict(row) for row in rows]

    def register_attribution_plan(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        action_hash: str,
        registered_by: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        plan = prepare_attribution_plan(
            **{
                key: payload[key]
                for key in (
                    "primary_metric",
                    "desired_direction",
                    "baseline_start",
                    "baseline_end",
                    "observation_start",
                    "observation_end",
                    "currency",
                    "randomized_experiment",
                    "has_control_group",
                    "minimum_sample",
                )
            }
        )
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            item = session.scalar(
                select(OperationalWorkItemRow).where(
                    OperationalWorkItemRow.tenant_id == tenant_id,
                    OperationalWorkItemRow.id == work_item_id,
                )
            )
            if item is None:
                raise LookupError("operational work item not found")
            if item.action_hash != action_hash:
                raise ValueError("action hash no longer matches the work item")
            existing = session.scalar(
                select(AttributionPlanRow).where(
                    AttributionPlanRow.tenant_id == tenant_id,
                    AttributionPlanRow.work_item_id == work_item_id,
                    AttributionPlanRow.action_hash == action_hash,
                )
            )
            if existing is not None:
                return _attribution_plan_dict(existing)
            row = AttributionPlanRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                action_hash=action_hash,
                primary_metric=plan["primary_metric"],
                desired_direction=plan["desired_direction"],
                baseline_start=plan["baseline_start"],
                baseline_end=plan["baseline_end"],
                observation_start=plan["observation_start"],
                observation_end=plan["observation_end"],
                currency=plan["currency"],
                method=plan["method"],
                minimum_sample=plan["minimum_sample"],
                guardrail_metrics=list(payload.get("guardrail_metrics", [])),
                control_spec=_json_safe(payload.get("control_spec", {})),
                confounders=list(payload.get("confounders", [])),
                status="planned",
                registered_by=registered_by,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return _attribution_plan_dict(row)

    def list_attribution_plans(self, *, tenant_id: str, work_item_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(AttributionPlanRow).where(AttributionPlanRow.tenant_id == tenant_id)
        if work_item_id:
            statement = statement.where(AttributionPlanRow.work_item_id == work_item_id)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(AttributionPlanRow.created_at.desc())))
        return [_attribution_plan_dict(row) for row in rows]

    def list_outcomes(self, *, tenant_id: str, work_item_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(ActionOutcomeRow).where(ActionOutcomeRow.tenant_id == tenant_id)
        if work_item_id:
            statement = statement.where(ActionOutcomeRow.work_item_id == work_item_id)
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(ActionOutcomeRow.evaluated_at.desc())))
        return [_outcome_dict(row) for row in rows]

    def learning_metrics(self, *, tenant_id: str) -> dict[str, Any]:
        feedback_rows = self.list_issue_feedback(tenant_id=tenant_id)
        missed_rows = self.list_missed_issues(tenant_id=tenant_id)
        labels = [
            IssueFeedbackLabel(
                item["operational_issue_id"],
                item["severity"],
                item["is_correct"],
                item["is_useful"],
                item["reason_code"],
            )
            for item in feedback_rows
        ]
        feedback = evaluate_issue_feedback(
            labels=labels, missed_issue_severities=[item["severity"] for item in missed_rows]
        )
        with self.session_factory() as session:
            activity_rows = list(
                session.scalars(
                    select(OperatorActivityEventRow)
                    .where(
                        OperatorActivityEventRow.tenant_id == tenant_id,
                    )
                    .order_by(OperatorActivityEventRow.occurred_at)
                )
            )
        grouped: dict[tuple[str, str], list[ActivityEvent]] = defaultdict(list)
        for row in activity_rows:
            grouped[(row.work_item_id, row.actor_id)].append(
                ActivityEvent(
                    row.work_item_id,
                    row.actor_id,
                    row.event_type,
                    _utc(row.occurred_at),
                )
            )
        sessions = []
        for events in grouped.values():
            if events and events[-1].event_type == "completed":
                sessions.append(calculate_labor_metrics(events))
        completed = len(sessions)
        active_total = sum(int(item["active_touch_seconds"] or 0) for item in sessions)
        cycle_total = sum(int(item["cycle_seconds"] or 0) for item in sessions)
        with self.session_factory() as session:
            rule_count = len(list(session.scalars(select(RuleVersionRow).where(RuleVersionRow.tenant_id == tenant_id))))
            proposal_rows = list(
                session.scalars(select(CalibrationProposalRow).where(CalibrationProposalRow.tenant_id == tenant_id))
            )
        return {
            "feedback": _json_safe(feedback),
            "labor": {
                "completed_sessions": completed,
                "average_active_touch_seconds": active_total // completed if completed else None,
                "average_cycle_seconds": cycle_total // completed if completed else None,
            },
            "calibration": {
                "rule_versions": rule_count,
                "pending_proposals": sum(1 for item in proposal_rows if item.status == "pending_approval"),
                "shadow_versions": sum(1 for item in proposal_rows if item.status == "shadow"),
                "calibration_eligible": bool(feedback.get("calibration_eligible")),
            },
        }


def _validate_activity_transition(previous: str | None, target: str) -> None:
    allowed = {
        None: {"started"},
        "started": {"paused", "completed"},
        "paused": {"resumed"},
        "resumed": {"paused", "completed"},
        "completed": set(),
    }
    if target not in allowed[previous]:
        raise ValueError(f"invalid activity transition: {previous} -> {target}")


def _datetime(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _feedback_dict(row: IssueFeedbackRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "operational_issue_id": row.operational_issue_id,
        "actor_id": row.actor_id,
        "severity": row.severity,
        "is_correct": row.is_correct,
        "is_useful": row.is_useful,
        "reason_code": row.reason_code,
        "rule_version": row.rule_version,
        "note": row.note,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
    }


def _missed_dict(row: MissedIssueReportRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "actor_id": row.actor_id,
        "marketplace_id": row.marketplace_id,
        "seller_sku": row.seller_sku,
        "issue_type": row.issue_type,
        "severity": row.severity,
        "description": row.description,
        "evidence_manifest": row.evidence_manifest or [],
        "status": row.status,
        "created_at": _utc(row.created_at),
    }


def _activity_dict(row: OperatorActivityEventRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "work_item_id": row.work_item_id,
        "actor_id": row.actor_id,
        "event_type": row.event_type,
        "occurred_at": _utc(row.occurred_at),
        "idempotency_key": row.idempotency_key,
    }


def _context_dict(row: BusinessContextEventRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "marketplace_id": row.marketplace_id,
        "seller_sku": row.seller_sku,
        "context_type": row.context_type,
        "starts_at": _utc(row.starts_at),
        "ends_at": _utc(row.ends_at),
        "details": row.details or {},
        "source": row.source,
        "verified_amazon_data": row.verified_amazon_data,
    }


def _execution_dict(row: ActionExecutionAttemptRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "work_item_id": row.work_item_id,
        "action_hash": row.action_hash,
        "execution_mode": row.execution_mode,
        "status": row.status,
        "exact_parameters": row.exact_parameters or {},
        "execution_evidence": row.execution_evidence or {},
        "actor_id": row.actor_id,
        "executed_at": _utc(row.executed_at),
        "amazon_write_performed": row.amazon_write_performed,
        "idempotency_key": row.idempotency_key,
    }


def _outcome_dict(row: ActionOutcomeRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "work_item_id": row.work_item_id,
        "execution_id": row.execution_id,
        "primary_metric": row.primary_metric,
        "desired_direction": row.desired_direction,
        "baseline_metrics": row.baseline_metrics or {},
        "result_metrics": row.result_metrics or {},
        "guardrail_metrics": row.guardrail_metrics or {},
        "observation_start": _utc(row.observation_start),
        "observation_end": _utc(row.observation_end),
        "confounders": row.confounders or [],
        "control_metrics": row.control_metrics or {},
        "randomized_experiment": row.randomized_experiment,
        "minimum_sample_satisfied": row.minimum_sample_satisfied,
        "attribution_level": row.attribution_level,
        "outcome_status": row.outcome_status,
        "incremental_value": row.incremental_value,
        "currency": row.currency,
        "causal_claim_allowed": row.causal_claim_allowed,
        "recommended_decision": row.recommended_decision,
        "evaluated_at": _utc(row.evaluated_at),
    }


def _rule_version_dict(row: RuleVersionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "rule_key": row.rule_key,
        "version": row.version,
        "status": row.status,
        "parameters": row.parameters or {},
        "evaluation_metrics": row.evaluation_metrics or {},
        "source": row.source,
        "parent_version": row.parent_version,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "shadow_started_at": _utc(row.shadow_started_at) if row.shadow_started_at else None,
        "shadow_ended_at": _utc(row.shadow_ended_at) if row.shadow_ended_at else None,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
    }


def _calibration_dict(row: CalibrationProposalRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "rule_key": row.rule_key,
        "base_version": row.base_version,
        "candidate_hash": row.candidate_hash,
        "current_parameters": row.current_parameters or {},
        "candidate_parameters": row.candidate_parameters or {},
        "replay_metrics": row.replay_metrics or {},
        "label_count": row.label_count,
        "status": row.status,
        "reason": row.reason,
        "proposed_by": row.proposed_by,
        "approved_by": row.approved_by,
        "approved_at": _utc(row.approved_at) if row.approved_at else None,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
    }


def _authorization_dict(row: ExecutionAuthorizationRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "work_item_id": row.work_item_id,
        "action_hash": row.action_hash,
        "action_type": row.action_type,
        "risk_level": row.risk_level,
        "status": row.status,
        "exact_parameters": row.exact_parameters or {},
        "preflight_evidence": row.preflight_evidence or {},
        "requested_by": row.requested_by,
        "approved_by": row.approved_by,
        "approved_at": _utc(row.approved_at) if row.approved_at else None,
        "expires_at": _utc(row.expires_at),
        "idempotency_key": row.idempotency_key,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
    }


def _attribution_plan_dict(row: AttributionPlanRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "work_item_id": row.work_item_id,
        "action_hash": row.action_hash,
        "primary_metric": row.primary_metric,
        "desired_direction": row.desired_direction,
        "baseline_start": _utc(row.baseline_start),
        "baseline_end": _utc(row.baseline_end),
        "observation_start": _utc(row.observation_start),
        "observation_end": _utc(row.observation_end),
        "currency": row.currency,
        "method": row.method,
        "minimum_sample": row.minimum_sample,
        "guardrail_metrics": row.guardrail_metrics or [],
        "control_spec": row.control_spec or {},
        "confounders": row.confounders or [],
        "status": row.status,
        "registered_by": row.registered_by,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
    }
