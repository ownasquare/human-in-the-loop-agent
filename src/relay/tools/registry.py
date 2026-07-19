"""Tool registry and defense-in-depth approved execution boundary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from relay.config import Settings
from relay.models import (
    Action,
    ExecutionReceipt,
    PlannedStep,
    ReceiptStatus,
    action_payload_hash,
)
from relay.persistence import RelayRepository
from relay.policy import ToolPolicy
from relay.redaction import redact
from relay.tools.base import OutcomeUnknownError, ToolAdapter, ToolExecutionError
from relay.tools.calendar import DemoCalendarAdapter, GoogleCalendarAdapter
from relay.tools.database import DatabaseAdapter
from relay.tools.email import DemoEmailAdapter, ResendEmailAdapter
from relay.tools.purchasing import DemoPurchasingAdapter, DisabledLivePurchasingAdapter
from relay.tools.search import DemoSearchAdapter, TavilySearchAdapter


class ToolBundle:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RelayRepository,
        policy: ToolPolicy,
        adapters: dict[str, ToolAdapter],
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.policy = policy
        self.adapters = adapters

    def prepare(self, action: Action) -> dict[str, Any]:
        """Return a redacted preview without invoking an adapter."""
        rule = self.policy.for_action(action)
        return {
            "tool_name": action.tool_name,
            "risk_level": rule.risk_level.value,
            "requires_approval": rule.requires_approval,
            "payload_hash": action_payload_hash(action),
            "payload": redact(action.model_dump(mode="json")),
        }

    async def execute(
        self,
        *,
        run_id: str,
        step: PlannedStep,
        proposal_id: str | None,
    ) -> ExecutionReceipt:
        action = step.action
        rule = self.policy.for_action(action)
        request_hash = action_payload_hash(action)
        if rule.requires_approval:
            if proposal_id is None:
                raise ToolExecutionError(
                    "A persisted approval is required.", code="approval_required"
                )
            proposal = self.repository.get_proposal(proposal_id)
            action_version = proposal.version
        else:
            action_version = 0
        idempotency_key = f"relay:{run_id}:{step.id}:{action_version}"
        existing = self.repository.get_receipt_by_idempotency(idempotency_key)
        if existing is not None:
            if (
                existing.run_id != run_id
                or existing.proposal_id != proposal_id
                or existing.step_id != step.id
                or existing.tool_name != action.tool_name
                or existing.request_hash != request_hash
                or existing.idempotency_key != idempotency_key
            ):
                raise ToolExecutionError(
                    "Stored receipt does not match this execution.",
                    code="receipt_identity_conflict",
                )
            return existing
        if rule.requires_approval:
            if proposal_id is None:  # defensive narrowing after the receipt lookup
                raise ToolExecutionError(
                    "A persisted approval is required.", code="approval_required"
                )
            self.repository.begin_approved_execution(
                proposal_id,
                action,
                run_id=run_id,
                step=step,
            )
        adapter = self.adapters.get(action.tool_name)
        if adapter is None:
            raise ToolExecutionError("No adapter is registered for this tool.", code="unknown_tool")
        created_at = datetime.now(UTC)
        try:
            result = await adapter.execute(action, idempotency_key=idempotency_key)
            status = ReceiptStatus.SUCCEEDED
            error_code = None
            provider = result.provider
            provider_id = result.provider_id
            response = redact(result.data)
        except OutcomeUnknownError as exc:
            status = ReceiptStatus.OUTCOME_UNKNOWN
            error_code = exc.code
            provider = "unknown"
            provider_id = None
            response = {"message": "Provider outcome requires reconciliation."}
        except ToolExecutionError as exc:
            status = ReceiptStatus.FAILED
            error_code = exc.code
            provider = "unavailable"
            provider_id = None
            response = {"message": "Tool execution failed safely."}
        receipt = ExecutionReceipt(
            id=f"receipt_{uuid.uuid4().hex}",
            run_id=run_id,
            proposal_id=proposal_id,
            step_id=step.id,
            tool_name=action.tool_name,
            status=status,
            provider=provider,
            provider_id=provider_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response,
            error_code=error_code,
            created_at=created_at,
            completed_at=datetime.now(UTC),
        )
        return self.repository.record_receipt(receipt)


def build_tool_bundle(
    settings: Settings,
    repository: RelayRepository,
    policy: ToolPolicy,
) -> ToolBundle:
    if settings.mode == "demo":
        adapters: dict[str, ToolAdapter] = {
            "web_search": DemoSearchAdapter(),
            "db_select": DatabaseAdapter(repository, "db_select"),
            "calendar_availability": DemoCalendarAdapter(repository, "calendar_availability"),
            "calendar_create": DemoCalendarAdapter(repository, "calendar_create"),
            "email_send": DemoEmailAdapter(repository),
            "db_update_record": DatabaseAdapter(repository, "db_update_record"),
            "purchase_order_create": DemoPurchasingAdapter(repository),
        }
    else:
        adapters = {
            "web_search": TavilySearchAdapter(settings),
            "db_select": DatabaseAdapter(repository, "db_select"),
            "calendar_availability": GoogleCalendarAdapter(settings, "calendar_availability"),
            "calendar_create": GoogleCalendarAdapter(settings, "calendar_create"),
            "email_send": ResendEmailAdapter(settings),
            "db_update_record": DatabaseAdapter(repository, "db_update_record"),
            "purchase_order_create": DisabledLivePurchasingAdapter(),
        }
    return ToolBundle(
        settings=settings,
        repository=repository,
        policy=policy,
        adapters=adapters,
    )
