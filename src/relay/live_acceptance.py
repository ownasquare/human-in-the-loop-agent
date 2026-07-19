"""Explicit, connector-by-connector live acceptance boundaries."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict

from relay.config import Settings, _is_real_sender
from relay.models import (
    Action,
    CalendarAvailabilityAction,
    CalendarCreateAction,
    EmailSendAction,
    WebSearchAction,
)
from relay.planner import ClaudePlanner, Planner, PlanningError
from relay.policy import DEFAULT_POLICY
from relay.tools.base import AdapterResult, OutcomeUnknownError, ToolAdapter, ToolExecutionError
from relay.tools.calendar import GoogleCalendarAdapter
from relay.tools.email import ResendEmailAdapter
from relay.tools.search import TavilySearchAdapter

LiveLane: TypeAlias = Literal[
    "claude",
    "tavily",
    "resend",
    "google_calendar_read",
    "google_calendar_write",
]

ACKNOWLEDGEMENTS: dict[LiveLane, str] = {
    "claude": "RUN_LIVE_CLAUDE",
    "tavily": "RUN_LIVE_TAVILY",
    "resend": "SEND_LIVE_RESEND_EMAIL",
    "google_calendar_read": "RUN_LIVE_GOOGLE_READ",
    "google_calendar_write": "CREATE_LIVE_GOOGLE_EVENT",
}

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")


class LiveAcceptanceError(RuntimeError):
    """A fixed, credential-safe acceptance failure."""


class LiveAcceptanceResult(BaseModel):
    """Sanitized evidence that deliberately excludes provider and reviewed payloads."""

    model_config = ConfigDict(extra="forbid")

    lane: LiveLane
    status: Literal["passed"] = "passed"
    effect: Literal["read_only", "external_write"]
    checks: dict[str, bool | int | str]


class ResendAcceptanceAdapter(Protocol):
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult: ...

    async def readback(self, message_id: str, action: EmailSendAction) -> AdapterResult: ...


PlannerFactory: TypeAlias = Callable[[Settings], Planner]
AdapterFactory: TypeAlias = Callable[[Settings], ToolAdapter]
ResendFactory: TypeAlias = Callable[[Settings], ResendAcceptanceAdapter]


def require_acknowledgement(lane: LiveLane, supplied: str) -> None:
    """Reject a lane before provider construction unless its exact phrase is supplied."""

    if supplied != ACKNOWLEDGEMENTS[lane]:
        raise LiveAcceptanceError(f"{lane} acknowledgement is missing or incorrect.")


def _require_connector(settings: Settings, name: str) -> None:
    configured = settings.connector_configuration()
    if not configured.get(name, False):
        raise LiveAcceptanceError(f"{name} is not configured for live acceptance.")


def _validate_run_id(value: str) -> str:
    if _RUN_ID.fullmatch(value) is None:
        raise LiveAcceptanceError(
            "run_id must be 8-64 characters using letters, numbers, dot, underscore, or hyphen."
        )
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _provider_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Provider timestamp is missing.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Provider timestamp is not offset-aware.")
    return parsed.astimezone(UTC)


def _parse_interval(
    start: str,
    end: str,
    *,
    maximum: timedelta,
    maximum_label: str,
) -> tuple[datetime, datetime]:
    try:
        start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveAcceptanceError("Google acceptance times must be valid ISO 8601 values.") from exc
    if (
        start_at.tzinfo is None
        or start_at.utcoffset() is None
        or end_at.tzinfo is None
        or end_at.utcoffset() is None
    ):
        raise LiveAcceptanceError("Google acceptance times must include an explicit UTC offset.")
    if end_at <= start_at:
        raise LiveAcceptanceError("Google acceptance end time must be after its start time.")
    if end_at - start_at > maximum:
        raise LiveAcceptanceError(f"Google acceptance interval must not exceed {maximum_label}.")
    return start_at, end_at


def _default_claude(settings: Settings) -> Planner:
    return ClaudePlanner(settings, DEFAULT_POLICY)


async def run_claude_acceptance(
    settings: Settings,
    *,
    confirmation: str,
    planner_factory: PlannerFactory = _default_claude,
) -> LiveAcceptanceResult:
    require_acknowledgement("claude", confirmation)
    _require_connector(settings, "anthropic")
    try:
        planner = planner_factory(settings)
        actions = await planner.plan(
            "Create a short plan that researches public human-in-the-loop agent safety patterns."
        )
        for action in actions:
            DEFAULT_POLICY.for_action(action)
    except (PlanningError, ValueError) as exc:
        raise LiveAcceptanceError("Claude live planning failed safely.") from exc
    return LiveAcceptanceResult(
        lane="claude",
        effect="read_only",
        checks={
            "action_count": len(actions),
            "schema_valid": True,
            "policy_valid": True,
        },
    )


async def run_tavily_acceptance(
    settings: Settings,
    *,
    confirmation: str,
    adapter_factory: AdapterFactory | None = None,
) -> LiveAcceptanceResult:
    require_acknowledgement("tavily", confirmation)
    _require_connector(settings, "web_search")
    adapter = adapter_factory(settings) if adapter_factory else TavilySearchAdapter(settings)
    try:
        result = await adapter.execute(
            WebSearchAction(query="public human-in-the-loop agent safety patterns", max_results=2),
            idempotency_key="relay-live-tavily-read",
        )
    except ToolExecutionError as exc:
        raise LiveAcceptanceError("Tavily live search failed safely.") from exc
    results = result.data.get("results")
    if not isinstance(results, list) or not results or len(results) > 2:
        raise LiveAcceptanceError("Tavily returned an invalid bounded result set.")
    bounded = all(
        isinstance(item, dict)
        and len(str(item.get("title", ""))) <= 300
        and len(str(item.get("url", ""))) <= 2000
        and len(str(item.get("snippet", ""))) <= 2000
        for item in results
    )
    if not bounded:
        raise LiveAcceptanceError("Tavily returned an invalid bounded result set.")
    return LiveAcceptanceResult(
        lane="tavily",
        effect="read_only",
        checks={
            "result_count": len(results),
            "result_limit_respected": True,
            "content_bounds_respected": True,
        },
    )


async def run_resend_acceptance(
    settings: Settings,
    *,
    confirmation: str,
    run_id: str,
    adapter_factory: ResendFactory | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> LiveAcceptanceResult:
    require_acknowledgement("resend", confirmation)
    active_run_id = _validate_run_id(run_id)
    _require_connector(settings, "email")
    target = settings.acceptance_email_to
    if target is None or not _is_real_sender(target.get_secret_value()):
        raise LiveAcceptanceError("A controlled non-production Resend recipient is required.")
    action = EmailSendAction(
        sender=settings.email_from,
        to=[target.get_secret_value()],
        subject="Relay live acceptance check",
        body=(
            "This controlled message verifies Relay's Resend adapter, provider readback, "
            "and idempotent replay boundary."
        ),
    )
    adapter = adapter_factory(settings) if adapter_factory else ResendEmailAdapter(settings)
    idempotency_key = f"relay-live-resend:{active_run_id}"
    invocation_started = now().astimezone(UTC)
    try:
        first = await adapter.execute(action, idempotency_key=idempotency_key)
    except OutcomeUnknownError as exc:
        raise LiveAcceptanceError(
            "Resend outcome is unknown; reconcile the provider before retrying."
        ) from exc
    except ToolExecutionError as exc:
        raise LiveAcceptanceError(
            "Resend live acceptance failed safely before provider acceptance."
        ) from exc
    if first.provider_id is None:
        raise LiveAcceptanceError(
            "Resend may have accepted the controlled write without a usable receipt; "
            "inspect and reconcile it before retrying."
        )
    try:
        readback = await adapter.readback(first.provider_id, action)
    except ToolExecutionError as exc:
        raise LiveAcceptanceError(
            "Resend accepted the controlled write, but provider readback failed; "
            "inspect and reconcile it, and do not rerun."
        ) from exc
    try:
        provider_created_at = _provider_datetime(readback.data.get("created_at"))
    except (TypeError, ValueError) as exc:
        raise LiveAcceptanceError(
            "Resend accepted the controlled write, but its creation time could not be verified; "
            "inspect and reconcile it, and do not rerun."
        ) from exc
    invocation_finished = now().astimezone(UTC)
    creation_current = (
        invocation_started - timedelta(seconds=5)
        <= provider_created_at
        <= invocation_finished + timedelta(seconds=5)
    )
    if not creation_current:
        raise LiveAcceptanceError(
            "Resend returned a message that was not created in this invocation; the run ID may "
            "have been used already. Inspect and reconcile it, and do not rerun."
        )
    try:
        second = await adapter.execute(action, idempotency_key=idempotency_key)
    except OutcomeUnknownError as exc:
        raise LiveAcceptanceError(
            "Resend accepted the controlled write, but same-key replay is outcome unknown; "
            "inspect and reconcile it, and do not rerun."
        ) from exc
    except ToolExecutionError as exc:
        raise LiveAcceptanceError(
            "Resend accepted the controlled write, but same-key replay verification failed; "
            "inspect and reconcile it, and do not rerun."
        ) from exc
    readback_verified = readback.data.get("matches_reviewed") is True
    duplicate_suppressed = second.provider_id == first.provider_id
    if not readback_verified or not duplicate_suppressed:
        raise LiveAcceptanceError(
            "Resend accepted the controlled write, but readback or replay did not match; "
            "inspect and reconcile it, and do not rerun."
        )
    return LiveAcceptanceResult(
        lane="resend",
        effect="external_write",
        checks={
            "provider_receipt": True,
            "provider_readback": True,
            "provider_creation_current": True,
            "same_key_duplicate_suppressed": True,
            "submission_attempts": 2,
            "recipient_delivery_requires_human_check": True,
        },
    )


async def run_google_read_acceptance(
    settings: Settings,
    *,
    confirmation: str,
    start: str,
    end: str,
    adapter_factory: AdapterFactory | None = None,
) -> LiveAcceptanceResult:
    require_acknowledgement("google_calendar_read", confirmation)
    start_at, end_at = _parse_interval(
        start,
        end,
        maximum=timedelta(days=7),
        maximum_label="7 days for a read",
    )
    _require_connector(settings, "calendar")
    adapter = (
        adapter_factory(settings)
        if adapter_factory
        else GoogleCalendarAdapter(settings, "calendar_availability")
    )
    try:
        result = await adapter.execute(
            CalendarAvailabilityAction(
                calendar_id=settings.google_calendar_id,
                start_at=start_at,
                end_at=end_at,
            ),
            idempotency_key="relay-live-google-read",
        )
    except ToolExecutionError as exc:
        raise LiveAcceptanceError("Google Calendar read acceptance failed safely.") from exc
    busy = result.data.get("busy")
    if not isinstance(busy, list) or len(busy) > 50:
        raise LiveAcceptanceError("Google Calendar returned an invalid bounded readback.")
    return LiveAcceptanceResult(
        lane="google_calendar_read",
        effect="read_only",
        checks={"response_received": True, "busy_slot_count": len(busy), "bounded": True},
    )


async def run_google_write_acceptance(
    settings: Settings,
    *,
    confirmation: str,
    run_id: str,
    start: str,
    end: str,
    adapter_factory: AdapterFactory | None = None,
) -> LiveAcceptanceResult:
    require_acknowledgement("google_calendar_write", confirmation)
    active_run_id = _validate_run_id(run_id)
    start_at, end_at = _parse_interval(
        start,
        end,
        maximum=timedelta(hours=2),
        maximum_label="2 hours for a write",
    )
    _require_connector(settings, "calendar")
    action = CalendarCreateAction(
        calendar_id=settings.google_calendar_id,
        summary="Relay live acceptance check",
        description="Controlled non-production event for Relay adapter acceptance.",
        start_at=start_at,
        end_at=end_at,
        attendees=[],
        send_updates="none",
    )
    adapter = (
        adapter_factory(settings)
        if adapter_factory
        else GoogleCalendarAdapter(settings, "calendar_create")
    )
    idempotency_key = f"relay-live-google:{active_run_id}"
    try:
        first = await adapter.execute(action, idempotency_key=idempotency_key)
    except OutcomeUnknownError as exc:
        raise LiveAcceptanceError(
            "Google Calendar outcome is unknown; reconcile the provider before retrying."
        ) from exc
    except ToolExecutionError as exc:
        raise LiveAcceptanceError(
            "Google Calendar write acceptance failed safely before provider acceptance."
        ) from exc
    first_readback = first.data.get("reconciled_existing") is False
    if not first_readback or first.provider_id is None:
        raise LiveAcceptanceError(
            "Google Calendar returned an existing or unverifiable controlled event; "
            "inspect and reconcile it, and do not rerun."
        )
    try:
        second = await adapter.execute(action, idempotency_key=idempotency_key)
    except OutcomeUnknownError as exc:
        raise LiveAcceptanceError(
            "Google Calendar accepted the controlled write, but same-key replay is outcome "
            "unknown; inspect and reconcile it, and do not rerun."
        ) from exc
    except ToolExecutionError as exc:
        raise LiveAcceptanceError(
            "Google Calendar accepted the controlled write, but replay verification failed; "
            "inspect and reconcile it, and do not rerun."
        ) from exc
    replay_reconciled = second.data.get("reconciled_existing") is True
    duplicate_suppressed = first.provider_id == second.provider_id
    if not replay_reconciled or not duplicate_suppressed:
        raise LiveAcceptanceError(
            "Google Calendar accepted the controlled write, but replay did not reconcile; "
            "inspect and reconcile it, and do not rerun."
        )
    return LiveAcceptanceResult(
        lane="google_calendar_write",
        effect="external_write",
        checks={
            "provider_receipt": True,
            "provider_readback": True,
            "same_key_conflict_reconciled": True,
            "duplicate_suppressed": True,
            "send_updates_disabled": True,
            "attendee_count": 0,
        },
    )
