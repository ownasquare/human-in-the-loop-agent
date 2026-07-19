"""Read-only query and approval-gated structured record update adapters."""

from __future__ import annotations

from relay.models import Action, DatabaseSelectAction, DatabaseUpdateAction
from relay.persistence import ConflictError, NotFoundError, RelayRepository
from relay.tools.base import AdapterResult, ToolExecutionError


class DatabaseAdapter:
    def __init__(self, repository: RelayRepository, tool_name: str) -> None:
        self.repository = repository
        self.tool_name = tool_name

    async def execute(self, action: Action, *, idempotency_key: str) -> AdapterResult:
        if isinstance(action, DatabaseSelectAction):
            rows = self.repository.run_read_query(action.query)
            return AdapterResult(
                provider="relay_sqlite",
                provider_id=f"query:{idempotency_key[-12:]}",
                data={"rows": rows, "row_count": len(rows)},
            )
        if isinstance(action, DatabaseUpdateAction):
            try:
                provider_id, row = self.repository.demo_update_customer(
                    idempotency_key=idempotency_key, action=action
                )
            except NotFoundError as exc:
                raise ToolExecutionError(
                    "The reviewed customer record no longer exists.",
                    code="record_not_found",
                ) from exc
            except ConflictError as exc:
                raise ToolExecutionError(
                    "Customer record version no longer matches the reviewed action.",
                    code="record_version_conflict",
                ) from exc
            return AdapterResult(
                provider="relay_sqlite",
                provider_id=provider_id,
                data={"record_id": provider_id, "record": row},
            )
        raise ToolExecutionError("Database adapter received the wrong action type.")
