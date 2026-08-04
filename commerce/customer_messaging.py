from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True)
class BuyerMessage:
    external_case_id: str
    scenario_id: str
    external_order_id: str
    marketplace_id: str
    language: str
    message: str
    received_at: datetime
    source: str = "mock_buyer"
    verified_amazon_data: bool = False


class BuyerMessagingPort(Protocol):
    def list_scenarios(self) -> list[dict[str, str]]: ...
    def create_message(self, *, scenario_id: str, external_order_id: str) -> BuyerMessage: ...


class MockBuyerMessagingAdapter:
    SCENARIOS = {
        "where_is_my_order": ("Where is my order? The tracking has not changed.", "en-US"),
        "delivery_delay": ("My order is late and I need an update today.", "en-US"),
        "cancel_request": ("Please cancel this order before it arrives.", "en-US"),
        "refund_demand": ("I want a full refund immediately.", "en-US"),
        "damaged_item": ("The item arrived damaged. Can you help me?", "en-US"),
        "wrong_item": ("I received the wrong item in my package.", "en-US"),
        "missing_accessory": ("A required accessory is missing from the box.", "en-US"),
        "usage_question": ("How should I use and clean this product?", "en-US"),
        "invoice_request": ("Please provide an invoice for this order.", "en-US"),
        "negative_sentiment": ("This is extremely frustrating and nobody has helped me.", "en-US"),
        "off_platform_contact": ("Can we continue this conversation on WhatsApp?", "en-US"),
        "review_incentive": ("Will you refund me if I remove my negative review?", "en-US"),
    }

    def list_scenarios(self) -> list[dict[str, str]]:
        return [
            {"scenario_id": key, "message": value[0], "language": value[1]} for key, value in self.SCENARIOS.items()
        ]

    def create_message(self, *, scenario_id: str, external_order_id: str) -> BuyerMessage:
        if scenario_id not in self.SCENARIOS:
            raise LookupError("mock customer service scenario not found")
        message, language = self.SCENARIOS[scenario_id]
        return BuyerMessage(
            external_case_id=f"mock-case-{scenario_id}-{uuid4()}",
            scenario_id=scenario_id,
            external_order_id=external_order_id,
            marketplace_id="ATVPDKIKX0DER",
            language=language,
            message=message,
            received_at=datetime.now(UTC),
        )
