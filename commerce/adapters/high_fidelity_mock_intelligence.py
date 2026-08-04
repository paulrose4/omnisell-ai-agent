from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from application.high_fidelity_mock import HighFidelityStoreDataset
from commerce.intelligence_ports import OperationsIntelligenceBatch


class HighFidelityMockOperationsSource:
    """Normalized intelligence adapter backed by a deterministic synthetic store."""

    def __init__(self, dataset: HighFidelityStoreDataset) -> None:
        self.dataset = dataset

    def collect(self, *, tenant_id: str, marketplace_id: str) -> OperationsIntelligenceBatch:
        if marketplace_id != "ATVPDKIKX0DER":
            raise ValueError("high-fidelity pilot dataset currently supports the US marketplace only")

        def scoped(record: dict[str, Any]) -> dict[str, Any]:
            normalized = {
                **record,
                "tenant_id": tenant_id,
                "marketplace_id": marketplace_id,
                "source": "mock",
                "verified_amazon_data": False,
            }
            for key in ("period_start", "period_end", "observed_at", "collected_at", "starts_at", "ends_at"):
                value = normalized.get(key)
                if isinstance(value, str):
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError(f"high-fidelity timestamp must be timezone-aware: {key}")
                    normalized[key] = parsed.astimezone(UTC)
            return normalized

        return OperationsIntelligenceBatch(
            source="mock",
            verified_amazon_data=False,
            advertising_snapshots=[scoped(item) for item in self.dataset.advertising_snapshots],
            search_terms=[scoped(item) for item in self.dataset.search_terms],
            return_events=[scoped(item) for item in self.dataset.return_events],
            review_observations=[scoped(item) for item in self.dataset.review_observations],
            message_tags=[scoped(item) for item in self.dataset.customer_messages],
            business_contexts=[scoped(item) for item in self.dataset.business_contexts],
        )
