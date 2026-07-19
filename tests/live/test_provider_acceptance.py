from __future__ import annotations

import os

import pytest

from relay.config import Settings
from relay.live_acceptance import (
    run_claude_acceptance,
    run_google_read_acceptance,
    run_google_write_acceptance,
    run_resend_acceptance,
    run_tavily_acceptance,
)


def _require_opt_in(key: str, phrase: str) -> None:
    if os.getenv(key) != phrase:
        pytest.skip(f"requires exact {key} acknowledgement")


def _required_value(key: str) -> str:
    value = os.getenv(key)
    if not value:
        pytest.fail(f"{key} is required after provider opt-in")
    return value


@pytest.mark.live(lane="claude")
async def test_claude_provider_acceptance() -> None:
    phrase = "RUN_LIVE_CLAUDE"
    _require_opt_in("RELAY_RUN_LIVE_CLAUDE", phrase)
    result = await run_claude_acceptance(Settings(), confirmation=phrase)
    assert result.lane == "claude"


@pytest.mark.live(lane="tavily")
async def test_tavily_provider_acceptance() -> None:
    phrase = "RUN_LIVE_TAVILY"
    _require_opt_in("RELAY_RUN_LIVE_TAVILY", phrase)
    result = await run_tavily_acceptance(Settings(), confirmation=phrase)
    assert result.lane == "tavily"


@pytest.mark.live(lane="resend")
async def test_resend_provider_acceptance() -> None:
    phrase = "SEND_LIVE_RESEND_EMAIL"
    _require_opt_in("RELAY_RUN_LIVE_RESEND", phrase)
    result = await run_resend_acceptance(
        Settings(),
        confirmation=phrase,
        run_id=_required_value("RELAY_LIVE_ACCEPTANCE_RUN_ID"),
    )
    assert result.lane == "resend"


@pytest.mark.live(lane="google-read")
async def test_google_calendar_read_acceptance() -> None:
    phrase = "RUN_LIVE_GOOGLE_READ"
    _require_opt_in("RELAY_RUN_LIVE_GOOGLE_READ", phrase)
    result = await run_google_read_acceptance(
        Settings(),
        confirmation=phrase,
        start=_required_value("RELAY_LIVE_ACCEPTANCE_START"),
        end=_required_value("RELAY_LIVE_ACCEPTANCE_END"),
    )
    assert result.lane == "google_calendar_read"


@pytest.mark.live(lane="google-write")
async def test_google_calendar_write_acceptance() -> None:
    phrase = "CREATE_LIVE_GOOGLE_EVENT"
    _require_opt_in("RELAY_RUN_LIVE_GOOGLE_WRITE", phrase)
    result = await run_google_write_acceptance(
        Settings(),
        confirmation=phrase,
        run_id=_required_value("RELAY_LIVE_ACCEPTANCE_RUN_ID"),
        start=_required_value("RELAY_LIVE_ACCEPTANCE_START"),
        end=_required_value("RELAY_LIVE_ACCEPTANCE_END"),
    )
    assert result.lane == "google_calendar_write"
