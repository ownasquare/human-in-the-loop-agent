from __future__ import annotations

import hashlib

import httpx
import pytest
import respx
from pydantic import SecretStr

from relay.config import Settings
from relay.models import CalendarAvailabilityAction, CalendarCreateAction
from relay.tools.base import OutcomeUnknownError, ToolExecutionError
from relay.tools.calendar import GoogleCalendarAdapter


def _settings(tmp_path) -> Settings:
    return Settings(
        mode="live",
        data_dir=tmp_path,
        google_calendar_access_token=SecretStr("test-calendar-token"),
        google_calendar_id="primary",
    )


def _create_action() -> CalendarCreateAction:
    return CalendarCreateAction(
        summary="Review",
        description="Review the launch plan.",
        start_at="2030-01-15T10:00:00Z",
        end_at="2030-01-15T10:30:00Z",
        attendees=["reviewer@example.com"],
        send_updates="externalOnly",
    )


def _reviewed_event(event_id: str, idempotency_key: str) -> dict[str, object]:
    return {
        "id": event_id,
        "summary": "Review",
        "description": "Review the launch plan.",
        "start": {"dateTime": "2030-01-15T02:00:00-08:00"},
        "end": {"dateTime": "2030-01-15T02:30:00-08:00"},
        "attendees": [{"email": "reviewer@example.com"}],
        "extendedProperties": {"private": {"relayIdempotency": idempotency_key}},
    }


@respx.mock
async def test_google_calendar_availability_returns_bounded_slots(tmp_path) -> None:
    route = respx.post("https://www.googleapis.com/calendar/v3/freeBusy").mock(
        return_value=httpx.Response(
            200,
            json={
                "calendars": {
                    "primary": {
                        "busy": [
                            {
                                "start": "2030-01-15T10:00:00Z",
                                "end": "2030-01-15T10:30:00Z",
                            }
                        ]
                    }
                }
            },
        )
    )
    action = CalendarAvailabilityAction(
        calendar_id="primary",
        start_at="2030-01-15T09:00:00Z",
        end_at="2030-01-15T17:00:00Z",
    )

    result = await GoogleCalendarAdapter(_settings(tmp_path), "calendar_availability").execute(
        action, idempotency_key="calendar-read"
    )

    assert route.called
    assert '"id":"primary"' in route.calls[0].request.content.decode()
    assert result.provider == "google_calendar"
    assert result.data == {
        "calendar_id": "primary",
        "busy": [{"start": "2030-01-15T10:00:00Z", "end": "2030-01-15T10:30:00Z"}],
    }


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"calendars": {"primary": {"busy": "invalid"}}}),
        httpx.Response(503),
    ],
)
async def test_google_calendar_availability_fails_safely(
    tmp_path, response: httpx.Response
) -> None:
    respx.post("https://www.googleapis.com/calendar/v3/freeBusy").mock(return_value=response)
    action = CalendarAvailabilityAction(
        calendar_id="primary",
        start_at="2030-01-15T09:00:00Z",
        end_at="2030-01-15T17:00:00Z",
    )

    with pytest.raises(ToolExecutionError, match="failed safely") as caught:
        await GoogleCalendarAdapter(_settings(tmp_path), "calendar_availability").execute(
            action, idempotency_key="calendar-read-failure"
        )

    assert caught.value.code == "calendar_failed"


@respx.mock
async def test_google_calendar_conflict_reads_exact_idempotent_event(tmp_path) -> None:
    settings = _settings(tmp_path)
    idempotency_key = "relay:run:step:1"
    event_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    respx.post(f"{base}/events").mock(return_value=httpx.Response(409))
    respx.get(f"{base}/events/{event_id}").mock(
        return_value=httpx.Response(200, json=_reviewed_event(event_id, idempotency_key))
    )
    result = await GoogleCalendarAdapter(settings, "calendar_create").execute(
        _create_action(),
        idempotency_key=idempotency_key,
    )
    assert result.provider_id == event_id
    assert result.data["reconciled_existing"] is True
    assert respx.calls.call_count == 2
    assert respx.calls[0].request.url.params["sendUpdates"] == "externalOnly"


@respx.mock
async def test_google_calendar_success_requires_exact_get_readback(tmp_path) -> None:
    idempotency_key = "relay:run:step:success-readback"
    event_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    respx.post(f"{base}/events").mock(
        return_value=httpx.Response(200, json=_reviewed_event(event_id, idempotency_key))
    )
    respx.get(f"{base}/events/{event_id}").mock(
        return_value=httpx.Response(200, json=_reviewed_event(event_id, idempotency_key))
    )

    result = await GoogleCalendarAdapter(_settings(tmp_path), "calendar_create").execute(
        _create_action(), idempotency_key=idempotency_key
    )

    assert result.provider_id == event_id
    assert result.data["reconciled_existing"] is False
    assert respx.calls.call_count == 2


@respx.mock
async def test_google_calendar_mismatched_success_readback_is_unknown(tmp_path) -> None:
    idempotency_key = "relay:run:step:mismatched-readback"
    event_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    mismatched = _reviewed_event(event_id, idempotency_key)
    mismatched["description"] = "Unexpected provider copy"
    respx.post(f"{base}/events").mock(return_value=httpx.Response(200, json={"id": event_id}))
    respx.get(f"{base}/events/{event_id}").mock(return_value=httpx.Response(200, json=mismatched))

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
        await GoogleCalendarAdapter(_settings(tmp_path), "calendar_create").execute(
            _create_action(), idempotency_key=idempotency_key
        )


@respx.mock
async def test_google_calendar_conflict_mismatch_has_unknown_outcome(tmp_path) -> None:
    idempotency_key = "relay:run:step:mismatch"
    event_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    mismatched = _reviewed_event(event_id, idempotency_key)
    mismatched["summary"] = "Substituted event"
    respx.post(f"{base}/events").mock(return_value=httpx.Response(409))
    respx.get(f"{base}/events/{event_id}").mock(return_value=httpx.Response(200, json=mismatched))

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
        await GoogleCalendarAdapter(_settings(tmp_path), "calendar_create").execute(
            _create_action(), idempotency_key=idempotency_key
        )


@respx.mock
async def test_google_calendar_server_error_has_unknown_outcome(tmp_path) -> None:
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    respx.post(f"{base}/events").mock(return_value=httpx.Response(500))

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown") as caught:
        await GoogleCalendarAdapter(_settings(tmp_path), "calendar_create").execute(
            _create_action(), idempotency_key="relay:run:step:server-error"
        )

    assert caught.value.code == "outcome_unknown"


@respx.mock
async def test_google_calendar_client_error_fails_safely(tmp_path) -> None:
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    respx.post(f"{base}/events").mock(return_value=httpx.Response(400))

    with pytest.raises(ToolExecutionError, match="failed safely") as caught:
        await GoogleCalendarAdapter(_settings(tmp_path), "calendar_create").execute(
            _create_action(), idempotency_key="relay:run:step:client-error"
        )

    assert not isinstance(caught.value, OutcomeUnknownError)
    assert caught.value.code == "calendar_failed"


@respx.mock
async def test_google_calendar_malformed_success_has_unknown_outcome(tmp_path) -> None:
    base = "https://www.googleapis.com/calendar/v3/calendars/primary"
    idempotency_key = "relay:run:step:malformed-success"
    event_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    respx.post(f"{base}/events").mock(return_value=httpx.Response(200, json={"id": event_id}))
    respx.get(f"{base}/events/{event_id}").mock(
        return_value=httpx.Response(200, content=b"not-json")
    )

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
        await GoogleCalendarAdapter(_settings(tmp_path), "calendar_create").execute(
            _create_action(), idempotency_key=idempotency_key
        )


@respx.mock
async def test_google_calendar_rejects_unconfigured_calendar_before_request(tmp_path) -> None:
    adapter = GoogleCalendarAdapter(_settings(tmp_path), "calendar_create")
    with pytest.raises(ToolExecutionError, match="configured calendar"):
        await adapter.execute(
            CalendarCreateAction(
                calendar_id="different-calendar@example.net",
                summary="Review",
                start_at="2030-01-15T10:00:00Z",
                end_at="2030-01-15T10:30:00Z",
            ),
            idempotency_key="calendar-mismatch-create",
        )
    with pytest.raises(ToolExecutionError, match="configured calendar"):
        await adapter.execute(
            CalendarAvailabilityAction(
                calendar_id="different-calendar@example.net",
                start_at="2030-01-15T09:00:00Z",
                end_at="2030-01-15T17:00:00Z",
            ),
            idempotency_key="calendar-mismatch-read",
        )
    assert respx.calls.call_count == 0
