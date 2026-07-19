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
    assert route.calls[0].request.headers["Idempotency-Key"] == "relay-email-idempotency"
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer ")
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


@respx.mock
async def test_resend_readback_validates_exact_reviewed_email(tmp_path) -> None:
    route = respx.get("https://api.resend.com/emails/email_123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "email_123",
                "from": "verified@example.com",
                "to": ["reviewer@example.com"],
                "cc": [],
                "created_at": "2030-01-15 10:00:00+00:00",
                "subject": "Review",
                "text": "Please review",
            },
        )
    )

    result = await ResendEmailAdapter(_settings(tmp_path)).readback("email_123", _action())

    assert route.called
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer ")
    assert result.provider_id == "email_123"
    assert result.data == {
        "matches_reviewed": True,
        "created_at": "2030-01-15 10:00:00+00:00",
    }


@respx.mock
async def test_resend_readback_mismatch_fails_safely(tmp_path) -> None:
    respx.get("https://api.resend.com/emails/email_123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "email_123",
                "from": "verified@example.com",
                "to": ["other@example.com"],
                "cc": [],
                "created_at": "2030-01-15 10:00:00+00:00",
                "subject": "Review",
                "text": "Please review",
            },
        )
    )

    with pytest.raises(ToolExecutionError, match="readback failed safely") as caught:
        await ResendEmailAdapter(_settings(tmp_path)).readback("email_123", _action())

    assert caught.value.code == "email_readback_failed"


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(404), httpx.Response(200, content=b"bad")])
async def test_resend_readback_http_and_schema_fail_safely(
    tmp_path, response: httpx.Response
) -> None:
    respx.get("https://api.resend.com/emails/email_123").mock(return_value=response)

    with pytest.raises(ToolExecutionError, match="readback failed safely") as caught:
        await ResendEmailAdapter(_settings(tmp_path)).readback("email_123", _action())

    assert caught.value.code == "email_readback_failed"
