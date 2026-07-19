from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from relay.config import Settings
from relay.live_acceptance import (
    LiveAcceptanceError,
    run_claude_acceptance,
    run_google_read_acceptance,
    run_google_write_acceptance,
    run_resend_acceptance,
    run_tavily_acceptance,
)
from relay.models import Action, EmailSendAction, WebSearchAction
from relay.tools.base import AdapterResult, OutcomeUnknownError, ToolExecutionError


class FakePlanner:
    async def plan(self, instruction: str) -> list[Action]:
        return [WebSearchAction(query="public safety patterns", max_results=1)]


class FakeTavilyAdapter:
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        return AdapterResult(
            provider="tavily",
            provider_id=None,
            data={
                "query": "provider-private-query",
                "results": [
                    {
                        "title": "Public result",
                        "url": "https://provider-private.example",
                        "snippet": "provider-private-snippet",
                    }
                ],
            },
        )


class FakeResendAdapter:
    def __init__(self) -> None:
        self.idempotency_keys: list[str] = []
        self.readback_calls = 0

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        self.idempotency_keys.append(idempotency_key)
        return AdapterResult(
            provider="resend",
            provider_id="provider-private-message-id",
            data={"provider-private": "payload"},
        )

    async def readback(self, message_id: str, action: EmailSendAction) -> AdapterResult:
        self.readback_calls += 1
        return AdapterResult(
            provider="resend",
            provider_id=message_id,
            data={"matches_reviewed": True, "created_at": datetime.now(UTC).isoformat()},
        )


class FakeGoogleReadAdapter:
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        return AdapterResult(
            provider="google_calendar",
            provider_id=None,
            data={
                "calendar_id": "provider-private-calendar",
                "busy": [{"start": "2030-01-15T10:00:00Z", "end": "2030-01-15T10:30:00Z"}],
            },
        )


class FakeGoogleWriteAdapter:
    def __init__(self) -> None:
        self.idempotency_keys: list[str] = []

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        self.idempotency_keys.append(idempotency_key)
        replay = len(self.idempotency_keys) == 2
        return AdapterResult(
            provider="google_calendar",
            provider_id="provider-private-event-id",
            data={
                "reconciled_existing": replay,
                "calendar_response": {"provider-private": "event payload"},
            },
        )


class UnknownOutcomeResendAdapter(FakeResendAdapter):
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        raise OutcomeUnknownError("provider-private-unknown", code="outcome_unknown")


class ReadbackFailureResendAdapter(FakeResendAdapter):
    async def readback(self, message_id: str, action: EmailSendAction) -> AdapterResult:
        raise ToolExecutionError("provider-private-readback", code="email_readback_failed")


class ReplayFailureResendAdapter(FakeResendAdapter):
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if self.idempotency_keys:
            raise ToolExecutionError("provider-private-replay", code="email_failed")
        return await super().execute(action, idempotency_key=idempotency_key)


class StaleResendAdapter(FakeResendAdapter):
    async def readback(self, message_id: str, action: EmailSendAction) -> AdapterResult:
        return AdapterResult(
            provider="resend",
            provider_id=message_id,
            data={"matches_reviewed": True, "created_at": "2030-01-14 10:00:00+00:00"},
        )


class UnknownOutcomeGoogleAdapter:
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        raise OutcomeUnknownError("provider-private-unknown", code="outcome_unknown")


class ReplayFailureGoogleAdapter(FakeGoogleWriteAdapter):
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if self.idempotency_keys:
            raise OutcomeUnknownError("provider-private-replay", code="outcome_unknown")
        return await super().execute(action, idempotency_key=idempotency_key)


class FailingReadAdapter:
    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        raise ToolExecutionError("provider-private-read-error", code="search_failed")


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        anthropic_api_key=SecretStr("fixture-anthropic-secret"),
        tavily_api_key=SecretStr("fixture-tavily-secret"),
        resend_api_key=SecretStr("fixture-resend-secret"),
        email_from="relay@company.example.co",
        acceptance_email_to=SecretStr("controlled@company.example.co"),
        google_calendar_access_token=SecretStr("fixture-google-secret"),
        google_calendar_id="provider-private-calendar",
    )


async def test_wrong_acknowledgement_stops_before_factory(tmp_path) -> None:
    called = False

    def factory(settings: Settings) -> FakePlanner:
        nonlocal called
        called = True
        return FakePlanner()

    with pytest.raises(LiveAcceptanceError, match="acknowledgement"):
        await run_claude_acceptance(
            _settings(tmp_path), confirmation="wrong", planner_factory=factory
        )

    assert called is False


async def test_resend_guards_target_and_run_id_before_factory(tmp_path) -> None:
    called = False

    def factory(settings: Settings) -> FakeResendAdapter:
        nonlocal called
        called = True
        return FakeResendAdapter()

    settings = _settings(tmp_path)
    settings.acceptance_email_to = None
    with pytest.raises(LiveAcceptanceError, match="recipient"):
        await run_resend_acceptance(
            settings,
            confirmation="SEND_LIVE_RESEND_EMAIL",
            run_id="valid-run-id",
            adapter_factory=factory,
        )
    with pytest.raises(LiveAcceptanceError, match="run_id"):
        await run_resend_acceptance(
            _settings(tmp_path),
            confirmation="SEND_LIVE_RESEND_EMAIL",
            run_id="short",
            adapter_factory=factory,
        )

    assert called is False


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2030-01-15T10:00:00", "2030-01-15T11:00:00", "explicit UTC offset"),
        ("2030-01-15T11:00:00Z", "2030-01-15T10:00:00Z", "after its start"),
    ],
)
async def test_google_time_guards_stop_before_factory(
    tmp_path, start: str, end: str, message: str
) -> None:
    called = False

    def factory(settings: Settings) -> FakeGoogleReadAdapter:
        nonlocal called
        called = True
        return FakeGoogleReadAdapter()

    with pytest.raises(LiveAcceptanceError, match=message):
        await run_google_read_acceptance(
            _settings(tmp_path),
            confirmation="RUN_LIVE_GOOGLE_READ",
            start=start,
            end=end,
            adapter_factory=factory,
        )

    assert called is False


@pytest.mark.parametrize(
    ("lane", "start", "end", "message"),
    [
        (
            "read",
            "2030-01-01T00:00:00Z",
            "2030-01-09T00:00:00Z",
            "7 days for a read",
        ),
        (
            "write",
            "2030-01-15T10:00:00Z",
            "2030-01-15T12:00:01Z",
            "2 hours for a write",
        ),
    ],
)
async def test_google_interval_bounds_stop_before_factory(
    tmp_path, lane: str, start: str, end: str, message: str
) -> None:
    called = False

    def factory(settings: Settings) -> FakeGoogleReadAdapter:
        nonlocal called
        called = True
        return FakeGoogleReadAdapter()

    with pytest.raises(LiveAcceptanceError, match=message):
        if lane == "read":
            await run_google_read_acceptance(
                _settings(tmp_path),
                confirmation="RUN_LIVE_GOOGLE_READ",
                start=start,
                end=end,
                adapter_factory=factory,
            )
        else:
            await run_google_write_acceptance(
                _settings(tmp_path),
                confirmation="CREATE_LIVE_GOOGLE_EVENT",
                run_id="google-run-bound",
                start=start,
                end=end,
                adapter_factory=factory,
            )

    assert called is False


async def test_read_only_lane_results_are_sanitized(tmp_path) -> None:
    settings = _settings(tmp_path)
    claude = await run_claude_acceptance(
        settings,
        confirmation="RUN_LIVE_CLAUDE",
        planner_factory=lambda _: FakePlanner(),
    )
    tavily = await run_tavily_acceptance(
        settings,
        confirmation="RUN_LIVE_TAVILY",
        adapter_factory=lambda _: FakeTavilyAdapter(),
    )
    google = await run_google_read_acceptance(
        settings,
        confirmation="RUN_LIVE_GOOGLE_READ",
        start="2030-01-15T09:00:00Z",
        end="2030-01-15T17:00:00Z",
        adapter_factory=lambda _: FakeGoogleReadAdapter(),
    )

    serialized = "\n".join(item.model_dump_json() for item in (claude, tavily, google))
    for forbidden in (
        "fixture-anthropic-secret",
        "fixture-tavily-secret",
        "provider-private-query",
        "provider-private-snippet",
        "provider-private-calendar",
        "2030-01-15T10:00:00Z",
    ):
        assert forbidden not in serialized


async def test_resend_acceptance_proves_readback_and_same_key_replay(tmp_path) -> None:
    adapter = FakeResendAdapter()

    result = await run_resend_acceptance(
        _settings(tmp_path),
        confirmation="SEND_LIVE_RESEND_EMAIL",
        run_id="resend-run-0001",
        adapter_factory=lambda _: adapter,
    )

    assert adapter.idempotency_keys == ["relay-live-resend:resend-run-0001"] * 2
    assert adapter.readback_calls == 1
    assert result.checks["provider_readback"] is True
    assert result.checks["provider_creation_current"] is True
    assert result.checks["same_key_duplicate_suppressed"] is True
    serialized = result.model_dump_json()
    for forbidden in (
        "controlled@company.example.co",
        "provider-private-message-id",
        "provider-private",
        "fixture-resend-secret",
        "Relay live acceptance check",
    ):
        assert forbidden not in serialized


async def test_resend_rejects_a_run_id_that_resolves_to_an_old_message(tmp_path) -> None:
    fixed_now = datetime(2030, 1, 15, 10, 0, tzinfo=UTC)

    with pytest.raises(LiveAcceptanceError, match="not created in this invocation") as caught:
        await run_resend_acceptance(
            _settings(tmp_path),
            confirmation="SEND_LIVE_RESEND_EMAIL",
            run_id="resend-reused-01",
            adapter_factory=lambda _: StaleResendAdapter(),
            now=lambda: fixed_now,
        )

    assert "do not rerun" in str(caught.value)


async def test_google_write_proves_readback_and_same_key_reconciliation(tmp_path) -> None:
    adapter = FakeGoogleWriteAdapter()

    result = await run_google_write_acceptance(
        _settings(tmp_path),
        confirmation="CREATE_LIVE_GOOGLE_EVENT",
        run_id="google-run-0001",
        start="2030-01-15T10:00:00Z",
        end="2030-01-15T10:30:00Z",
        adapter_factory=lambda _: adapter,
    )

    assert adapter.idempotency_keys == ["relay-live-google:google-run-0001"] * 2
    assert result.checks["same_key_conflict_reconciled"] is True
    assert result.checks["duplicate_suppressed"] is True
    serialized = result.model_dump_json()
    assert "provider-private" not in serialized
    assert "Relay live acceptance check" not in serialized


@pytest.mark.parametrize(
    "adapter",
    [UnknownOutcomeResendAdapter(), UnknownOutcomeGoogleAdapter()],
)
async def test_first_write_unknown_outcome_requires_reconciliation(
    tmp_path, adapter: object
) -> None:
    if isinstance(adapter, UnknownOutcomeResendAdapter):
        operation = run_resend_acceptance(
            _settings(tmp_path),
            confirmation="SEND_LIVE_RESEND_EMAIL",
            run_id="unknown-resend-01",
            adapter_factory=lambda _: adapter,
        )
    else:
        operation = run_google_write_acceptance(
            _settings(tmp_path),
            confirmation="CREATE_LIVE_GOOGLE_EVENT",
            run_id="unknown-google-01",
            start="2030-01-15T10:00:00Z",
            end="2030-01-15T10:30:00Z",
            adapter_factory=lambda _: adapter,  # type: ignore[arg-type,return-value]
        )

    with pytest.raises(LiveAcceptanceError, match="outcome is unknown") as caught:
        await operation

    assert "reconcile" in str(caught.value)
    assert "provider-private" not in str(caught.value)


@pytest.mark.parametrize(
    ("adapter", "expected_stage"),
    [
        (ReadbackFailureResendAdapter(), "readback failed"),
        (ReplayFailureResendAdapter(), "replay verification failed"),
    ],
)
async def test_resend_post_acceptance_failure_requires_no_rerun(
    tmp_path, adapter: FakeResendAdapter, expected_stage: str
) -> None:
    with pytest.raises(LiveAcceptanceError, match=expected_stage) as caught:
        await run_resend_acceptance(
            _settings(tmp_path),
            confirmation="SEND_LIVE_RESEND_EMAIL",
            run_id="resend-stage-01",
            adapter_factory=lambda _: adapter,
        )

    message = str(caught.value)
    assert "accepted the controlled write" in message
    assert "do not rerun" in message
    assert "provider-private" not in message


async def test_google_post_acceptance_replay_unknown_requires_no_rerun(tmp_path) -> None:
    adapter = ReplayFailureGoogleAdapter()

    with pytest.raises(LiveAcceptanceError, match="replay is outcome unknown") as caught:
        await run_google_write_acceptance(
            _settings(tmp_path),
            confirmation="CREATE_LIVE_GOOGLE_EVENT",
            run_id="google-stage-01",
            start="2030-01-15T10:00:00Z",
            end="2030-01-15T10:30:00Z",
            adapter_factory=lambda _: adapter,
        )

    message = str(caught.value)
    assert "accepted the controlled write" in message
    assert "do not rerun" in message
    assert "provider-private" not in message


async def test_read_only_failure_remains_generic_and_sanitized(tmp_path) -> None:
    with pytest.raises(LiveAcceptanceError, match="live search failed safely") as caught:
        await run_tavily_acceptance(
            _settings(tmp_path),
            confirmation="RUN_LIVE_TAVILY",
            adapter_factory=lambda _: FailingReadAdapter(),
        )

    assert "provider-private" not in str(caught.value)
