from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OperationsIntelligenceBatch:
    source: str
    verified_amazon_data: bool
    advertising_snapshots: list[dict[str, Any]] = field(default_factory=list)
    search_terms: list[dict[str, Any]] = field(default_factory=list)
    return_events: list[dict[str, Any]] = field(default_factory=list)
    review_observations: list[dict[str, Any]] = field(default_factory=list)
    message_tags: list[dict[str, Any]] = field(default_factory=list)
    business_contexts: list[dict[str, Any]] = field(default_factory=list)

    def all_records(self) -> list[dict[str, Any]]:
        return [
            *self.advertising_snapshots,
            *self.search_terms,
            *self.return_events,
            *self.review_observations,
            *self.message_tags,
            *self.business_contexts,
        ]


class OperationsIntelligenceSource(Protocol):
    def collect(self, *, tenant_id: str, marketplace_id: str) -> OperationsIntelligenceBatch: ...
