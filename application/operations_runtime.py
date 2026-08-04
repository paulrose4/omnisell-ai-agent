from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from application.briefing import DailyOperationsBriefService
from application.operations_refresh import OperationsRefreshService
from infrastructure.repositories.briefing import DailyOperationsBriefRepository, ListingBasicSignalRepository
from infrastructure.repositories.commerce_sync import CommerceSyncRepository
from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository, InventoryPolicyRepository
from infrastructure.repositories.manual_execution import ManualExecutionRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.profitability import SKUCostProfileRepository, SKUProfitSnapshotRepository


def build_operations_refresh_service(session_factory: sessionmaker[Session]) -> OperationsRefreshService:
    issue_repository = OperationalIssueRepository(session_factory)
    action_repository = RecommendedActionRepository(session_factory)
    brief_service = DailyOperationsBriefService(
        brief_repository=DailyOperationsBriefRepository(session_factory),
        listing_repository=ListingBasicSignalRepository(session_factory),
        issue_repository=issue_repository,
        action_repository=action_repository,
    )
    return OperationsRefreshService(
        commerce_repository=CommerceSyncRepository(session_factory),
        cost_repository=SKUCostProfileRepository(session_factory),
        profit_repository=SKUProfitSnapshotRepository(session_factory),
        policy_repository=InventoryPolicyRepository(session_factory),
        inventory_repository=InventoryHealthSnapshotRepository(session_factory),
        issue_repository=issue_repository,
        action_repository=action_repository,
        brief_service=brief_service,
        manual_execution_repository=ManualExecutionRepository(session_factory),
    )
