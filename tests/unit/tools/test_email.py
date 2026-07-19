from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from relay.config import Settings
from relay.models import EmailSendAction
from relay.tools.base import OutcomeUnknownError, ToolExecutionError
from relay.tools.email import ResendEmailAdapter


def _settings(tmp_path) -> Settings:
    return Settings(
        mode="live",
        data_dir=tmp_path,
        resend_api_key=SecretStr("test-resend-key"),
        email_from="verified@example.com",
    )


def _action() -> EmailSendAction:
    return EmailSendAction(
        sender="verified@example.com",
        to=["reviewer@example.com"],
        subject="Review",
        body="Please review",
    )


@respx.mock
async def test_resend_uses_exact_reviewed_configured_sender(tmp_path) -> None:
    settings = _settings(tmp_path)
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "email_123"})
    )
    adapter = ResendEmailAdapter(settings)
    result = await adapter.execute(
        EmailSendAction(
            sender="verified@example.com",
            to=["reviewer@example.com"],
            subject="Review",
            body="Please review",
        ),
        idempotency_key="relay-email-idempotency",
    )
    assert result.provider_id == "email_123"
    assert route.calls[0].request.content
    assert '"from":"verified@example.com"' in route.calls[0].request.content.decode()

    with pytest.raises(ToolExecutionError, match="configured sender"):
        await adapter.execute(
            EmailSendAction(
                sender="other@example.net",
                to=["reviewer@example.com"],
                subject="Review",
                body="Please review",
            ),
            idempotency_key="relay-email-mismatch",
        )
    assert route.call_count == 1


@respx.mock
async def test_resend_server_error_has_unknown_outcome(tmp_path) -> None:
    respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(500))

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown") as caught:
        await ResendEmailAdapter(_settings(tmp_path)).execute(
            _action(), idempotency_key="relay-email-server-error"
        )

    assert caught.value.code == "outcome_unknown"


@respx.mock
async def test_resend_read_error_has_unknown_outcome(tmp_path) -> None:
    respx.post("https://api.resend.com/emails").mock(
        side_effect=httpx.ReadError("response stream failed")
    )

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
        await ResendEmailAdapter(_settings(tmp_path)).execute(
            _action(), idempotency_key="relay-email-read-error"
        )


@respx.mock
async def test_resend_explicit_client_error_fails_safely(tmp_path) -> None:
    respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(400))

    with pytest.raises(ToolExecutionError, match="failed safely") as caught:
        await ResendEmailAdapter(_settings(tmp_path)).execute(
            _action(), idempotency_key="relay-email-client-error"
        )

    assert not isinstance(caught.value, OutcomeUnknownError)
    assert caught.value.code == "email_failed"


@respx.mock
async def test_resend_malformed_success_body_has_unknown_outcome(tmp_path) -> None:
    respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, content=b"not-json")
    )

    with pytest.raises(OutcomeUnknownError, match="outcome is unknown"):
        await ResendEmailAdapter(_settings(tmp_path)).execute(
            _action(), idempotency_key="relay-email-malformed-success"
        )
