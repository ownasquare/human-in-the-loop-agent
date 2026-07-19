from __future__ import annotations

import pytest
from pydantic import SecretStr

from relay.config import Settings
from relay.models import (
    CalendarAvailabilityAction,
    CalendarCreateAction,
    EmailSendAction,
)
from relay.planner import ClaudePlanner, PlanningError, normalize_planned_actions
from relay.policy import DEFAULT_POLICY, ToolPolicy, UnknownToolError


class FakeStructuredModel:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.called = False

    async def ainvoke(self, messages: object) -> object:
        self.called = True
        if self.error is not None:
            raise self.error
        return self.result


def _claude_planner(tmp_path, *, max_plan_steps: int = 12) -> ClaudePlanner:
    settings = Settings(
        mode="live",
        data_dir=tmp_path,
        anthropic_api_key=SecretStr("test-anthropic-key"),
        max_plan_steps=max_plan_steps,
    )
    return ClaudePlanner(settings, DEFAULT_POLICY)


class RejectingPolicy(ToolPolicy):
    def for_action(self, action: object) -> object:
        raise UnknownToolError("denied by test policy")


def test_consequential_connector_identity_is_normalized_before_review(tmp_path) -> None:
    settings = Settings(
        mode="live",
        data_dir=tmp_path,
        email_from="verified@example.com",
        google_calendar_id="configured-calendar@example.com",
    )
    actions = [
        EmailSendAction(
            sender="model-chosen@example.net",
            to=["reviewer@example.com"],
            subject="Review",
            body="Please review",
        ),
        CalendarAvailabilityAction(
            calendar_id="model-calendar@example.net",
            start_at="2030-01-15T09:00:00Z",
            end_at="2030-01-15T17:00:00Z",
        ),
        CalendarCreateAction(
            calendar_id="model-calendar@example.net",
            summary="Review",
            start_at="2030-01-15T10:00:00Z",
            end_at="2030-01-15T10:30:00Z",
        ),
    ]
    normalized = normalize_planned_actions(actions, settings)
    assert normalized[0].sender == "verified@example.com"
    assert normalized[1].calendar_id == "configured-calendar@example.com"
    assert normalized[2].calendar_id == "configured-calendar@example.com"
    assert actions[0].sender == "model-chosen@example.net"


async def test_claude_planner_parses_typed_actions(tmp_path) -> None:
    planner = _claude_planner(tmp_path)
    fake = FakeStructuredModel(
        {
            "rationale": "Use one bounded public search.",
            "actions": [
                {"tool_name": "web_search", "query": "public Relay project", "max_results": 2}
            ],
        }
    )
    planner.model = fake  # type: ignore[assignment]

    actions = await planner.plan("Research the public Relay project")

    assert fake.called is True
    assert len(actions) == 1
    assert actions[0].tool_name == "web_search"


async def test_claude_planner_wraps_provider_and_schema_failures(tmp_path) -> None:
    planner = _claude_planner(tmp_path)
    planner.model = FakeStructuredModel(error=RuntimeError("provider detail"))  # type: ignore[assignment]

    with pytest.raises(PlanningError, match="valid Relay plan") as caught:
        await planner.plan("Create a safe plan")

    assert "provider detail" not in str(caught.value)

    planner.model = FakeStructuredModel(  # type: ignore[assignment]
        {"rationale": "No valid actions", "actions": []}
    )
    with pytest.raises(PlanningError, match="valid Relay plan"):
        await planner.plan("Create a safe plan")


async def test_claude_planner_rejects_plan_over_configured_limit(tmp_path) -> None:
    planner = _claude_planner(tmp_path, max_plan_steps=1)
    planner.model = FakeStructuredModel(  # type: ignore[assignment]
        {
            "rationale": "Too many steps.",
            "actions": [
                {"tool_name": "web_search", "query": "first query", "max_results": 1},
                {"tool_name": "web_search", "query": "second query", "max_results": 1},
            ],
        }
    )

    with pytest.raises(PlanningError, match="too many plan steps"):
        await planner.plan("Create one step")


async def test_claude_planner_applies_server_owned_policy(tmp_path) -> None:
    settings = Settings(
        mode="live",
        data_dir=tmp_path,
        anthropic_api_key=SecretStr("test-anthropic-key"),
    )
    planner = ClaudePlanner(settings, RejectingPolicy())
    planner.model = FakeStructuredModel(  # type: ignore[assignment]
        {
            "rationale": "A valid schema is still subject to policy.",
            "actions": [{"tool_name": "web_search", "query": "public query", "max_results": 1}],
        }
    )

    with pytest.raises(UnknownToolError, match="denied by test policy"):
        await planner.plan("Create a safe plan")
