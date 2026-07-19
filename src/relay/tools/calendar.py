"""Calendar read and approval-gated write adapters."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx

from relay.config import Settings
from relay.models import Action, CalendarAvailabilityAction, CalendarCreateAction
from relay.persistence import RelayRepository
from relay.tools.base import (
    AdapterResult,
    OutcomeUnknownError,
    ToolExecutionError,
)

_AMBIGUOUS_WRITE_ERRORS = (
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
)
_SAFE_PRE_SUBMIT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


def _same_datetime(actual: object, expected: datetime) -> bool:
    if not isinstance(actual, dict):
        return False
    raw = actual.get("dateTime")
    if not isinstance(raw, str):
        return False
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if (parsed.tzinfo is None) != (expected.tzinfo is None):
        return False
    if parsed.tzinfo is None:
        return parsed == expected
    return parsed.astimezone(UTC) == expected.astimezone(UTC)


def _matches_reviewed_event(
    body: dict[str, Any],
    *,
    action: CalendarCreateAction,
    event_id: str,
    idempotency_key: str,
) -> bool:
    extended = body.get("extendedProperties")
    private = extended.get("private") if isinstance(extended, dict) else None
    marker = private.get("relayIdempotency") if isinstance(private, dict) else None

    raw_attendees = body.get("attendees", [])
    if not isinstance(raw_attendees, list):
        return False
    attendee_emails: list[str] = []
    for attendee in raw_attendees:
        if not isinstance(attendee, dict) or not isinstance(attendee.get("email"), str):
            return False
        attendee_emails.append(attendee["email"])

    return (
        body.get("id") == event_id
        and marker == idempotency_key
        and body.get("summary") == action.summary
        and body.get("description", "") == action.description
        and _same_datetime(body.get("start"), action.start_at)
        and _same_datetime(body.get("end"), action.end_at)
        and sorted(attendee_emails) == sorted(action.attendees)
    )


class DemoCalendarAdapter:
    def __init__(self, repository: RelayRepository, tool_name: str) -> None:
        self.repository = repository
        self.tool_name = tool_name

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if isinstance(action, CalendarAvailabilityAction):
            slots = [
                {
                    "start_at": action.start_at.isoformat(),
                    "end_at": action.start_at.replace(hour=action.start_at.hour + 1).isoformat(),
                },
                {
                    "start_at": action.start_at.replace(day=action.start_at.day + 1).isoformat(),
                    "end_at": action.start_at.replace(
                        day=action.start_at.day + 1, hour=action.start_at.hour + 1
                    ).isoformat(),
                },
            ]
            return AdapterResult(
                provider="demo_calendar",
                provider_id=f"availability:{idempotency_key[-12:]}",
                data={"calendar_id": action.calendar_id, "slots": slots},
            )
        if isinstance(action, CalendarCreateAction):
            provider_id, _ = self.repository.demo_create_calendar_event(
                idempotency_key=idempotency_key, action=action
            )
            return AdapterResult(
                provider="demo_calendar",
                provider_id=provider_id,
                data={
                    "event_id": provider_id,
                    "summary": action.summary,
                    "start_at": action.start_at.isoformat(),
                    "attendees": action.attendees,
                },
            )
        raise ToolExecutionError("Calendar adapter received the wrong action type.")


class GoogleCalendarAdapter:
    def __init__(self, settings: Settings, tool_name: str) -> None:
        self.settings = settings
        self.tool_name = tool_name

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        token = self.settings.google_calendar_access_token
        if token is None:
            raise ToolExecutionError(
                "Google Calendar is not configured.", code="connector_not_configured"
            )
        if isinstance(action, (CalendarAvailabilityAction, CalendarCreateAction)) and (
            action.calendar_id != self.settings.google_calendar_id
        ):
            raise ToolExecutionError(
                "The reviewed calendar does not match the configured calendar.",
                code="calendar_not_configured",
            )
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Content-Type": "application/json",
        }
        calendar_id = self.settings.google_calendar_id
        base = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}"
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)

        if isinstance(action, CalendarAvailabilityAction):
            return await self._read_availability(
                action,
                idempotency_key=idempotency_key,
                headers=headers,
                request_timeout=timeout,
            )
        if isinstance(action, CalendarCreateAction):
            return await self._create_event(
                action,
                idempotency_key=idempotency_key,
                headers=headers,
                base=base,
                request_timeout=timeout,
            )
        raise ToolExecutionError("Calendar adapter received the wrong action type.")

    async def _read_availability(
        self,
        action: CalendarAvailabilityAction,
        *,
        idempotency_key: str,
        headers: dict[str, str],
        request_timeout: httpx.Timeout,
    ) -> AdapterResult:
        payload = {
            "timeMin": action.start_at.isoformat(),
            "timeMax": action.end_at.isoformat(),
            "items": [{"id": action.calendar_id}],
        }
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(
                    "https://www.googleapis.com/calendar/v3/freeBusy",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                "Calendar request failed safely.", code="calendar_failed"
            ) from exc
        if not 200 <= response.status_code < 300:
            raise ToolExecutionError("Calendar request failed safely.", code="calendar_failed")
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Google Calendar returned an invalid read body.")
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError(
                "Calendar request failed safely.", code="calendar_failed"
            ) from exc
        return AdapterResult(
            provider="google_calendar",
            provider_id=str(body.get("id", "")) or None,
            data={"calendar_response": body},
        )

    async def _create_event(
        self,
        action: CalendarCreateAction,
        *,
        idempotency_key: str,
        headers: dict[str, str],
        base: str,
        request_timeout: httpx.Timeout,
    ) -> AdapterResult:
        event_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        payload = {
            "id": event_id,
            "summary": action.summary,
            "description": action.description,
            "start": {"dateTime": action.start_at.isoformat()},
            "end": {"dateTime": action.end_at.isoformat()},
            "attendees": [{"email": item} for item in action.attendees],
            "extendedProperties": {"private": {"relayIdempotency": idempotency_key}},
        }
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(
                    f"{base}/events",
                    headers=headers,
                    params={"sendUpdates": action.send_updates},
                    json=payload,
                )
                if response.status_code == 409:
                    return await self._read_existing_event(
                        client,
                        action,
                        idempotency_key=idempotency_key,
                        event_id=event_id,
                        headers=headers,
                        base=base,
                    )
        except _AMBIGUOUS_WRITE_ERRORS as exc:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc
        except _SAFE_PRE_SUBMIT_ERRORS as exc:
            raise ToolExecutionError(
                "Calendar request failed safely.", code="calendar_failed"
            ) from exc
        except httpx.HTTPError as exc:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc

        if response.status_code >= 500:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            )
        if 400 <= response.status_code < 500:
            raise ToolExecutionError("Calendar request failed safely.", code="calendar_failed")
        if not 200 <= response.status_code < 300:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            )
        try:
            body = response.json()
            if not isinstance(body, dict) or body.get("id") != event_id:
                raise ValueError("Google Calendar returned an invalid create body.")
        except (TypeError, ValueError) as exc:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc
        return AdapterResult(
            provider="google_calendar",
            provider_id=event_id,
            data={"calendar_response": body},
        )

    async def _read_existing_event(
        self,
        client: httpx.AsyncClient,
        action: CalendarCreateAction,
        *,
        idempotency_key: str,
        event_id: str,
        headers: dict[str, str],
        base: str,
    ) -> AdapterResult:
        try:
            response = await client.get(f"{base}/events/{event_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc
        if not 200 <= response.status_code < 300:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            )
        try:
            body = response.json()
            if not isinstance(body, dict) or not _matches_reviewed_event(
                body,
                action=action,
                event_id=event_id,
                idempotency_key=idempotency_key,
            ):
                raise ValueError("The existing calendar event does not match the reviewed event.")
        except (TypeError, ValueError) as exc:
            raise OutcomeUnknownError(
                "Calendar provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc
        return AdapterResult(
            provider="google_calendar",
            provider_id=event_id,
            data={"calendar_response": body},
        )
