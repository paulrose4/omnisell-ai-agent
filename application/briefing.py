from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain.briefing import DailyOperationsBriefBuilder, ListingIssueEvaluator
from domain.models import DailyOperationsBrief, OperationalIssue, RecommendedAction, SKUHealthSummary, utc_now
from infrastructure.repositories.briefing import DailyOperationsBriefRepository, ListingBasicSignalRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository


@dataclass(frozen=True)
class PersistedBriefResult:
    brief: DailyOperationsBrief
    sku_health: tuple[SKUHealthSummary, ...]


@dataclass(frozen=True)
class DashboardResult:
    brief: DailyOperationsBrief
    priorities: tuple[dict[str, OperationalIssue | RecommendedAction | None], ...]


class DailyOperationsBriefService:
    def __init__(
        self,
        *,
        brief_repository: DailyOperationsBriefRepository,
        listing_repository: ListingBasicSignalRepository,
        issue_repository: OperationalIssueRepository,
        action_repository: RecommendedActionRepository,
        builder: DailyOperationsBriefBuilder | None = None,
        listing_evaluator: ListingIssueEvaluator | None = None,
    ) -> None:
        self.brief_repository = brief_repository
        self.listing_repository = listing_repository
        self.issue_repository = issue_repository
        self.action_repository = action_repository
        self.builder = builder or DailyOperationsBriefBuilder()
        self.listing_evaluator = listing_evaluator or ListingIssueEvaluator()

    def generate(
        self,
        *,
        tenant_id: str,
        brief_date: date | None,
        timezone: str,
        scan_listings: bool,
        generated_at: datetime | None = None,
    ) -> PersistedBriefResult:
        try:
            timezone_info = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        now = generated_at or utc_now()
        resolved_date = brief_date or now.astimezone(timezone_info).date()
        if scan_listings:
            signals = self.listing_repository.list_signals(tenant_id=tenant_id)
            active_listing_keys: set[str] = set()
            for signal in signals:
                evaluated = self.listing_evaluator.evaluate(signal, assessed_at=now)
                if evaluated is None:
                    continue
                issue, action = evaluated
                canonical_issue = self.issue_repository.upsert(issue)
                active_listing_keys.add(canonical_issue.dedupe_key)
                self.action_repository.upsert(action, canonical_issue_id=canonical_issue.id)
            self.issue_repository.resolve_complete_scan_absent(
                tenant_id=tenant_id,
                complete_scopes={
                    (signal.marketplace_id, signal.seller_sku, "listing_basic")
                    for signal in signals
                },
                active_dedupe_keys=active_listing_keys,
                resolved_at=now,
            )
        issues = self.issue_repository.list_issues(tenant_id=tenant_id)
        actions = self.action_repository.list_actions(tenant_id=tenant_id)
        built = self.builder.build(
            tenant_id=tenant_id, brief_date=resolved_date, timezone=timezone,
            generated_at=now, issues=issues, actions=actions,
        )
        persisted = self.brief_repository.save(built.brief)
        return PersistedBriefResult(brief=persisted, sku_health=persisted.sku_health)

    def dashboard(self, *, tenant_id: str) -> DashboardResult | None:
        brief = self.brief_repository.get_latest(tenant_id=tenant_id)
        if brief is None:
            return None
        issues = {item.id: item for item in self.issue_repository.list_issues(tenant_id=tenant_id)}
        allowed_action_ids = set(brief.priority_action_ids)
        actions = {
            item.issue_id: item
            for item in self.action_repository.list_actions(tenant_id=tenant_id)
            if item.id in allowed_action_ids
        }
        priorities = tuple(
            {"issue": issues[issue_id], "action": actions.get(issue_id)}
            for issue_id in brief.priority_issue_ids
            if issue_id in issues
        )
        return DashboardResult(brief=brief, priorities=priorities)
