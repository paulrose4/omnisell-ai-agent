from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AdvisorRegistry:
    def __init__(self, advisors: Mapping[str, Any] | None = None) -> None:
        self._advisors = dict(advisors or {})

    def register(self, workflow_type: str, advisor: Any) -> None:
        if not workflow_type:
            raise ValueError("workflow_type is required")
        if workflow_type in self._advisors:
            raise ValueError(f"Advisor already registered for workflow_type: {workflow_type}")
        self._advisors[workflow_type] = advisor

    def get(self, workflow_type: str) -> Any:
        try:
            return self._advisors[workflow_type]
        except KeyError as exc:
            raise KeyError(f"Unsupported agent workflow_type: {workflow_type}") from exc

    def workflow_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._advisors))
