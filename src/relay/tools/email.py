"""Approval-gated demo and Resend email adapters."""

from __future__ import annotations

import httpx

from relay.config import Settings
from relay.models import Action, EmailSendAction
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


class DemoEmailAdapter:
    tool_name = "email_send"

    def __init__(self, repository: RelayRepository) -> None:
        self.repository = repository

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if not isinstance(action, EmailSendAction):
            raise ToolExecutionError("Email adapter received the wrong action type.")
        provider_id, row = self.repository.demo_send_email(
            idempotency_key=idempotency_key, action=action
        )
        return AdapterResult(
            provider="demo_email",
            provider_id=provider_id,
            data={
                "message_id": provider_id,
                "recipients": action.to,
                "subject": action.subject,
                "stored": bool(row),
            },
        )


class ResendEmailAdapter:
    tool_name = "email_send"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if not isinstance(action, EmailSendAction):
            raise ToolExecutionError("Email adapter received the wrong action type.")
        if self.settings.resend_api_key is None:
            raise ToolExecutionError("Resend is not configured.", code="connector_not_configured")
        if action.sender.lower() != self.settings.email_from.lower():
            raise ToolExecutionError(
                "The reviewed sender does not match the configured sender identity.",
                code="sender_not_configured",
            )
        headers = {
            "Authorization": f"Bearer {self.settings.resend_api_key.get_secret_value()}",
            "Idempotency-Key": idempotency_key,
        }
        payload = {
            "from": action.sender,
            "to": action.to,
            "cc": action.cc,
            "subject": action.subject,
            "text": action.body,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.request_timeout_seconds)
            ) as client:
                response = await client.post(
                    "https://api.resend.com/emails", headers=headers, json=payload
                )
        except _AMBIGUOUS_WRITE_ERRORS as exc:
            raise OutcomeUnknownError(
                "Email provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc
        except _SAFE_PRE_SUBMIT_ERRORS as exc:
            raise ToolExecutionError("Email send failed safely.", code="email_failed") from exc
        except httpx.HTTPError as exc:
            raise OutcomeUnknownError(
                "Email provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc

        if response.status_code >= 500:
            raise OutcomeUnknownError(
                "Email provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            )
        if 400 <= response.status_code < 500:
            raise ToolExecutionError("Email send failed safely.", code="email_failed")
        if not 200 <= response.status_code < 300:
            raise OutcomeUnknownError(
                "Email provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            )
        try:
            body = response.json()
            message_id = body["id"]
            if not isinstance(body, dict) or not isinstance(message_id, str) or not message_id:
                raise ValueError("Resend returned an invalid success body.")
        except (KeyError, TypeError, ValueError) as exc:
            raise OutcomeUnknownError(
                "Email provider outcome is unknown; reconcile before retrying.",
                code="outcome_unknown",
            ) from exc
        return AdapterResult(
            provider="resend",
            provider_id=message_id,
            data={"message_id": message_id, "recipients": action.to, "subject": action.subject},
        )
