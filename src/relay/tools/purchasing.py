"""Deterministic approval-gated purchase order adapter."""

from __future__ import annotations

from relay.models import Action, PurchaseOrderCreateAction
from relay.persistence import RelayRepository
from relay.tools.base import AdapterResult, ToolExecutionError


class DemoPurchasingAdapter:
    tool_name = "purchase_order_create"

    def __init__(self, repository: RelayRepository) -> None:
        self.repository = repository

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if not isinstance(action, PurchaseOrderCreateAction):
            raise ToolExecutionError("Purchasing adapter received the wrong action type.")
        provider_id, _ = self.repository.demo_create_purchase_order(
            idempotency_key=idempotency_key, action=action
        )
        return AdapterResult(
            provider="demo_purchasing",
            provider_id=provider_id,
            data={
                "purchase_order_id": provider_id,
                "vendor": action.vendor,
                "amount": str(action.amount),
                "currency": action.currency,
            },
        )


class DisabledLivePurchasingAdapter:
    tool_name = "purchase_order_create"

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        raise ToolExecutionError(
            "Live purchasing is intentionally disabled; use demo mode or add a reviewed connector.",
            code="connector_not_configured",
        )
