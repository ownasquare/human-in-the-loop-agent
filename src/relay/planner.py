"""Deterministic demo and optional Claude structured planners."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, ConfigDict, Field

from relay.config import Settings
from relay.models import (
    Action,
    CalendarAvailabilityAction,
    CalendarCreateAction,
    DatabaseSelectAction,
    DatabaseUpdateAction,
    EmailSendAction,
    PurchaseOrderCreateAction,
    WebSearchAction,
    parse_action,
)
from relay.policy import ToolPolicy
from relay.prompts import PLANNER_SYSTEM_PROMPT


class PlanningError(RuntimeError):
    """The planner did not produce a valid bounded plan."""


class Planner(Protocol):
    async def plan(self, instruction: str) -> list[Action]: ...


class DemoPlanner:
    """Credential-free seven-step workflow exercising every requested tool boundary."""

    def __init__(self, settings: Settings, policy: ToolPolicy) -> None:
        self.settings = settings
        self.policy = policy

    async def plan(self, instruction: str) -> list[Action]:
        start = datetime(2030, 1, 15, 10, 0, tzinfo=UTC)
        end = datetime(2030, 1, 15, 10, 30, tzinfo=UTC)
        actions: list[Action] = [
            WebSearchAction(query="Acme renewal planning and vendor review", max_results=2),
            DatabaseSelectAction(
                query=(
                    "SELECT id, name, email, status, next_action, owner, renewal_note, version "
                    "FROM customers WHERE id = 'CUST-001'"
                )
            ),
            CalendarAvailabilityAction(
                calendar_id="primary",
                start_at=start,
                end_at=datetime(2030, 1, 17, 17, 0, tzinfo=UTC),
                duration_minutes=30,
            ),
            CalendarCreateAction(
                calendar_id="primary",
                summary="Acme renewal review",
                description="Review renewal context and confirm the next action.",
                start_at=start,
                end_at=end,
                attendees=["owner@acme.example"],
                send_updates=self.settings.calendar_send_updates_default,
            ),
            EmailSendAction(
                sender=self.settings.email_from,
                to=["owner@acme.example"],
                subject="Acme renewal review",
                body=(
                    "Hello,\n\nI prepared the renewal context and proposed a review for "
                    "January 15 at 10:00 UTC. Please confirm the timing.\n\nRegards,\nRelay"
                ),
            ),
            DatabaseUpdateAction(
                table="customers",
                record_id="CUST-001",
                expected_version=1,
                changes={"next_action": "Renewal review scheduled", "status": "review_scheduled"},
            ),
            PurchaseOrderCreateAction(
                vendor="Acme Services",
                amount="450.00",
                currency="USD",
                description="Renewal review preparation package",
            ),
        ]
        for action in actions:
            self.policy.for_action(action)
        return actions


class ClaudePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str = Field(max_length=2000)
    actions: list[dict[str, object]] = Field(min_length=1, max_length=25)


class ClaudePlanner:
    def __init__(self, settings: Settings, policy: ToolPolicy) -> None:
        if settings.anthropic_api_key is None:
            raise PlanningError("Anthropic is not configured for live mode.")
        self.settings = settings
        self.policy = policy
        self.model = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_tokens=4096,
            timeout=settings.request_timeout_seconds,
            max_retries=1,
        ).with_structured_output(ClaudePlan)

    async def plan(self, instruction: str) -> list[Action]:
        try:
            result = await self.model.ainvoke(
                [
                    ("system", PLANNER_SYSTEM_PROMPT),
                    (
                        "human",
                        "Create no more than "
                        f"{self.settings.max_plan_steps} steps for: {instruction}",
                    ),
                ]
            )
            parsed = ClaudePlan.model_validate(result)
            actions = [parse_action(item) for item in parsed.actions]
        except Exception as exc:
            raise PlanningError("Claude did not return a valid Relay plan.") from exc
        if len(actions) > self.settings.max_plan_steps:
            raise PlanningError("Claude returned too many plan steps.")
        for action in actions:
            self.policy.for_action(action)
        return actions


def build_planner(settings: Settings, policy: ToolPolicy) -> Planner:
    if settings.mode == "demo":
        return DemoPlanner(settings, policy)
    return ClaudePlanner(settings, policy)


def normalize_planned_actions(actions: list[Action], settings: Settings) -> list[Action]:
    """Bind server-owned consequential fields before hashing or human review."""
    normalized: list[Action] = []
    for action in actions:
        if isinstance(action, EmailSendAction):
            action = action.model_copy(update={"sender": settings.email_from})
        elif isinstance(action, (CalendarAvailabilityAction, CalendarCreateAction)):
            action = action.model_copy(update={"calendar_id": settings.google_calendar_id})
        normalized.append(action)
    return normalized
