from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from domain.models import InventoryHealthSnapshot, InventoryPolicy
from infrastructure.database.models import InventoryHealthSnapshotRow, InventoryPolicyRow


class InventoryPolicyRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, policy: InventoryPolicy) -> InventoryPolicy:
        with self.session_factory.begin() as session:
            max_version = (
                session.scalar(
                    select(func.max(InventoryPolicyRow.version)).where(
                        InventoryPolicyRow.tenant_id == policy.tenant_id,
                        InventoryPolicyRow.marketplace_id == policy.marketplace_id,
                        InventoryPolicyRow.seller_sku == policy.seller_sku,
                    )
                )
                or 0
            )
            active = session.scalar(
                select(InventoryPolicyRow)
                .where(
                    InventoryPolicyRow.tenant_id == policy.tenant_id,
                    InventoryPolicyRow.marketplace_id == policy.marketplace_id,
                    InventoryPolicyRow.seller_sku == policy.seller_sku,
                    InventoryPolicyRow.effective_to.is_(None),
                )
                .order_by(InventoryPolicyRow.effective_from.desc())
                .limit(1)
            )
            if active is not None:
                if _as_utc(active.effective_from) >= policy.effective_from:
                    raise ValueError("new inventory policy must start after the active policy")
                active.effective_to = policy.effective_from
            policy.version = int(max_version) + 1
            session.add(
                InventoryPolicyRow(
                    id=policy.id,
                    tenant_id=policy.tenant_id,
                    seller_sku=policy.seller_sku,
                    marketplace_id=policy.marketplace_id,
                    supplier_lead_time_days=policy.supplier_lead_time_days,
                    production_lead_time_days=policy.production_lead_time_days,
                    shipping_lead_time_days=policy.shipping_lead_time_days,
                    amazon_receiving_days=policy.amazon_receiving_days,
                    safety_stock_days=policy.safety_stock_days,
                    target_coverage_days=policy.target_coverage_days,
                    minimum_order_quantity=policy.minimum_order_quantity,
                    case_pack_quantity=policy.case_pack_quantity,
                    preferred_shipping_mode=policy.preferred_shipping_mode,
                    sales_weight_7d=policy.sales_weight_7d,
                    sales_weight_14d=policy.sales_weight_14d,
                    sales_weight_30d=policy.sales_weight_30d,
                    source=policy.source,
                    version=policy.version,
                    effective_from=policy.effective_from,
                    effective_to=policy.effective_to,
                    collected_at=policy.collected_at,
                    created_at=policy.created_at,
                )
            )
        return policy

    def get_active(
        self, *, tenant_id: str, marketplace_id: str, seller_sku: str, effective_at: datetime
    ) -> InventoryPolicy | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventoryPolicyRow)
                .where(
                    InventoryPolicyRow.tenant_id == tenant_id,
                    InventoryPolicyRow.marketplace_id == marketplace_id,
                    InventoryPolicyRow.seller_sku == seller_sku,
                    InventoryPolicyRow.effective_from <= effective_at,
                    (InventoryPolicyRow.effective_to.is_(None) | (InventoryPolicyRow.effective_to > effective_at)),
                )
                .order_by(InventoryPolicyRow.version.desc())
                .limit(1)
            )
        return self._domain(row) if row else None

    def list_policies(
        self, *, tenant_id: str, marketplace_id: str | None = None, seller_sku: str | None = None
    ) -> list[InventoryPolicy]:
        statement = select(InventoryPolicyRow).where(InventoryPolicyRow.tenant_id == tenant_id)
        if marketplace_id:
            statement = statement.where(InventoryPolicyRow.marketplace_id == marketplace_id)
        if seller_sku:
            statement = statement.where(InventoryPolicyRow.seller_sku == seller_sku)
        with self.session_factory() as session:
            rows = list(
                session.scalars(statement.order_by(InventoryPolicyRow.seller_sku, InventoryPolicyRow.version.desc()))
            )
        return [self._domain(row) for row in rows]

    @staticmethod
    def _domain(row: InventoryPolicyRow) -> InventoryPolicy:
        return InventoryPolicy(
            id=row.id,
            tenant_id=row.tenant_id,
            seller_sku=row.seller_sku,
            marketplace_id=row.marketplace_id,
            supplier_lead_time_days=row.supplier_lead_time_days,
            production_lead_time_days=row.production_lead_time_days,
            shipping_lead_time_days=row.shipping_lead_time_days,
            amazon_receiving_days=row.amazon_receiving_days,
            safety_stock_days=row.safety_stock_days,
            target_coverage_days=row.target_coverage_days,
            minimum_order_quantity=row.minimum_order_quantity,
            case_pack_quantity=row.case_pack_quantity,
            preferred_shipping_mode=row.preferred_shipping_mode,
            sales_weight_7d=row.sales_weight_7d,
            sales_weight_14d=row.sales_weight_14d,
            sales_weight_30d=row.sales_weight_30d,
            source=row.source,
            version=row.version,
            effective_from=_as_utc(row.effective_from),
            effective_to=_as_utc(row.effective_to),
            collected_at=_as_utc(row.collected_at),
            created_at=_as_utc(row.created_at),
        )


class InventoryHealthSnapshotRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def save(self, snapshot: InventoryHealthSnapshot) -> InventoryHealthSnapshot:
        with self.session_factory.begin() as session:
            session.add(
                InventoryHealthSnapshotRow(
                    id=snapshot.id,
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    policy_id=snapshot.policy_id,
                    policy_version=snapshot.policy_version,
                    available=snapshot.available,
                    reserved=snapshot.reserved,
                    inbound=snapshot.inbound,
                    in_transit=snapshot.in_transit,
                    unsellable=snapshot.unsellable,
                    inventory_position=snapshot.inventory_position,
                    weighted_daily_sales=snapshot.weighted_daily_sales,
                    days_of_supply=snapshot.days_of_supply,
                    expected_stockout_date=snapshot.expected_stockout_date,
                    total_lead_time_days=snapshot.total_lead_time_days,
                    safety_stock_units=snapshot.safety_stock_units,
                    reorder_point=snapshot.reorder_point,
                    recommended_reorder_quantity=snapshot.recommended_reorder_quantity,
                    excess_units=snapshot.excess_units,
                    recommended_shipping_mode=snapshot.recommended_shipping_mode,
                    inventory_health=snapshot.inventory_health,
                    stockout_risk=snapshot.stockout_risk,
                    excess_risk=snapshot.excess_risk,
                    forecast_confidence=snapshot.forecast_confidence,
                    missing_data=list(snapshot.missing_data),
                    data_warnings=list(snapshot.data_warnings),
                    degraded_reason=snapshot.degraded_reason,
                    automation_eligible=snapshot.automation_eligible,
                    inventory_source=snapshot.inventory_source,
                    inventory_collected_at=snapshot.inventory_collected_at,
                    calculated_at=snapshot.calculated_at,
                    calculation_version=snapshot.calculation_version,
                    input_manifest=snapshot.input_manifest,
                    estimated_revenue_at_risk=snapshot.estimated_revenue_at_risk,
                    estimated_profit_at_risk=snapshot.estimated_profit_at_risk,
                    estimated_extra_logistics_cost=snapshot.estimated_extra_logistics_cost,
                )
            )
        return snapshot

    def get(self, *, tenant_id: str, snapshot_id: str) -> InventoryHealthSnapshot | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventoryHealthSnapshotRow).where(
                    InventoryHealthSnapshotRow.tenant_id == tenant_id,
                    InventoryHealthSnapshotRow.id == snapshot_id,
                )
            )
        return self._domain(row) if row else None

    @staticmethod
    def _domain(row: InventoryHealthSnapshotRow) -> InventoryHealthSnapshot:
        return InventoryHealthSnapshot(
            id=row.id,
            tenant_id=row.tenant_id,
            seller_sku=row.seller_sku,
            marketplace_id=row.marketplace_id,
            policy_id=row.policy_id,
            policy_version=row.policy_version,
            available=row.available,
            reserved=row.reserved,
            inbound=row.inbound,
            in_transit=row.in_transit,
            unsellable=row.unsellable,
            inventory_position=row.inventory_position,
            weighted_daily_sales=row.weighted_daily_sales,
            days_of_supply=row.days_of_supply,
            expected_stockout_date=_as_utc(row.expected_stockout_date),
            total_lead_time_days=row.total_lead_time_days,
            safety_stock_units=row.safety_stock_units,
            reorder_point=row.reorder_point,
            recommended_reorder_quantity=row.recommended_reorder_quantity,
            excess_units=row.excess_units,
            recommended_shipping_mode=row.recommended_shipping_mode,
            inventory_health=row.inventory_health,
            stockout_risk=row.stockout_risk,
            excess_risk=row.excess_risk,
            forecast_confidence=row.forecast_confidence,
            missing_data=tuple(row.missing_data or []),
            data_warnings=tuple(row.data_warnings or []),
            degraded_reason=row.degraded_reason,
            automation_eligible=row.automation_eligible,
            inventory_source=row.inventory_source,
            inventory_collected_at=_as_utc(row.inventory_collected_at),
            calculated_at=_as_utc(row.calculated_at),
            calculation_version=row.calculation_version,
            input_manifest=row.input_manifest or {},
            estimated_revenue_at_risk=row.estimated_revenue_at_risk,
            estimated_profit_at_risk=row.estimated_profit_at_risk,
            estimated_extra_logistics_cost=row.estimated_extra_logistics_cost,
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
