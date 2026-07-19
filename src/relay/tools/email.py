"""Approval-gated demo and Resend email adapters."""

from __future__ import annotations

from urllib.parse import quote

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

    async def readback(self, message_id: str, action: EmailSendAction) -> AdapterResult:
        """Read and validate the provider copy without exposing its payload."""

        if self.settings.resend_api_key is None:
            raise ToolExecutionError("Resend is not configured.", code="connector_not_configured")
        if not message_id or len(message_id) > 500:
            raise ToolExecutionError("Email readback failed safely.", code="email_readback_failed")
        headers = {
            "Authorization": f"Bearer {self.settings.resend_api_key.get_secret_value()}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.request_timeout_seconds)
            ) as client:
                response = await client.get(
                    f"https://api.resend.com/emails/{quote(message_id, safe='')}",
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Resend returned an invalid readback body.")
            provider_to = body.get("to")
            provider_cc = body.get("cc", [])
            created_at = body.get("created_at")
            if (
                not isinstance(provider_to, list)
                or not isinstance(provider_cc, list)
                or not isinstance(created_at, str)
            ):
                raise ValueError("Resend returned invalid readback fields.")
            if not (
                body.get("id") == message_id
                and body.get("from") == action.sender
                and sorted(provider_to) == sorted(action.to)
                and sorted(provider_cc) == sorted(action.cc)
                and body.get("subject") == action.subject
                and body.get("text") == action.body
            ):
                raise ValueError("Resend readback does not match the reviewed email.")
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise ToolExecutionError(
                "Email readback failed safely.", code="email_readback_failed"
            ) from exc
        return AdapterResult(
            provider="resend",
            provider_id=message_id,
            data={"matches_reviewed": True, "created_at": created_at},
        )
