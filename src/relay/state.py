"""JSON-serializable LangGraph state."""

from __future__ import annotations

from typing import Any, TypedDict


class RelayState(TypedDict, total=False):
    run_id: str
    instruction: str
    mode: str
    plan: list[dict[str, Any]]
    current_index: int
    current_proposal_id: str | None
    current_interrupt_id: str | None
    decision: dict[str, Any] | None
    observations: list[dict[str, Any]]
    rejected_count: int
    status: str
    summary: str
    error: dict[str, Any] | None


def initial_state(*, run_id: str, instruction: str, mode: str) -> RelayState:
    return {
        "run_id": run_id,
        "instruction": instruction,
        "mode": mode,
        "plan": [],
        "current_index": 0,
        "current_proposal_id": None,
        "current_interrupt_id": None,
        "decision": None,
        "observations": [],
        "rejected_count": 0,
        "status": "queued",
        "summary": "",
        "error": None,
    }
