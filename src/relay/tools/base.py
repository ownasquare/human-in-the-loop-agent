"""Common tool adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from relay.models import Action


class ToolExecutionError(RuntimeError):
    """A tool failed before a confirmed external side effect."""

    def __init__(self, message: str, *, code: str = "tool_failed") -> None:
        super().__init__(message)
        self.code = code


class OutcomeUnknownError(ToolExecutionError):
    """A request may have reached a provider and must not be retried blindly."""


@dataclass(frozen=True, slots=True)
class AdapterResult:
    provider: str
    provider_id: str | None
    data: dict[str, Any]


class ToolAdapter(Protocol):
    tool_name: str

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult: ...
