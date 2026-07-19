"""Interrupt-driven LangGraph workflow for Relay."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from relay.config import Settings
from relay.models import (
    DecisionType,
    ReceiptStatus,
    RunStatus,
    StepStatus,
    parse_action,
)
from relay.persistence import RelayRepository
from relay.planner import Planner, normalize_planned_actions
from relay.policy import ToolPolicy
from relay.state import RelayState
from relay.tools.registry import ToolBundle


def _proposal_copy(tool_name: str) -> tuple[str, str, str]:
    copies = {
        "calendar_create": (
            "Create calendar event",
            "A meeting must be added to the external calendar.",
            "Creates the reviewed event and may notify attendees.",
        ),
        "email_send": (
            "Send email",
            "The workflow needs to contact the reviewed recipients.",
            "Sends the exact reviewed subject and body to the listed recipients.",
        ),
        "db_update_record": (
            "Update customer record",
            "The workflow needs to persist the reviewed next action.",
            "Changes only the displayed allowlisted customer fields.",
        ),
        "purchase_order_create": (
            "Create purchase order",
            "The workflow proposes a financial commitment.",
            "Creates the exact reviewed demo purchase order.",
        ),
    }
    return copies.get(tool_name, ("Approve action", "The action changes external state.", ""))


def build_graph(
    *,
    settings: Settings,
    repository: RelayRepository,
    planner: Planner,
    policy: ToolPolicy,
    tools: ToolBundle,
    checkpointer: Any,
) -> Any:
    async def plan_node(state: RelayState) -> dict[str, Any]:
        actions = normalize_planned_actions(await planner.plan(state["instruction"]), settings)
        steps = repository.save_plan(state["run_id"], actions)
        return {
            "plan": [item.model_dump(mode="json") for item in steps],
            "status": RunStatus.RUNNING.value,
        }

    async def prepare_node(state: RelayState) -> dict[str, Any]:
        index = int(state.get("current_index", 0))
        plan = state.get("plan", [])
        if index >= len(plan):
            return {"status": "finalizing"}
        step = repository.get_step_at(state["run_id"], index)
        repository.update_step(step.id, status=StepStatus.RUNNING)
        return {
            "plan": [
                item.model_dump(mode="json") for item in repository.list_steps(state["run_id"])
            ],
            "current_proposal_id": None,
            "current_interrupt_id": None,
            "decision": None,
            "status": RunStatus.RUNNING.value,
        }

    def route_prepared(state: RelayState) -> Literal["propose", "execute", "finalize"]:
        index = int(state.get("current_index", 0))
        plan = state.get("plan", [])
        if index >= len(plan):
            return "finalize"
        action = parse_action(plan[index]["action"])
        return "propose" if policy.for_action(action).requires_approval else "execute"

    async def propose_node(state: RelayState) -> dict[str, Any]:
        step = repository.get_step_at(state["run_id"], int(state["current_index"]))
        rule = policy.for_action(step.action)
        title, reason, expected_effect = _proposal_copy(step.action.tool_name)
        proposal = repository.create_proposal(
            run_id=state["run_id"],
            step_id=step.id,
            action=step.action,
            risk_level=rule.risk_level,
            title=title,
            reason=reason,
            expected_effect=expected_effect,
            ttl_minutes=settings.action_ttl_minutes,
        )
        return {
            "current_proposal_id": proposal.id,
            "status": RunStatus.AWAITING_APPROVAL.value,
        }

    async def request_approval_node(state: RelayState) -> dict[str, Any]:
        proposal_id = state.get("current_proposal_id")
        if not proposal_id:
            raise RuntimeError("Approval node requires a persisted proposal.")
        proposal = repository.get_proposal(proposal_id)
        rule = policy.for_action(proposal.action)
        decision = interrupt(
            {
                "type": "approval_required",
                "proposal_id": proposal.id,
                "run_id": proposal.run_id,
                "step_id": proposal.step_id,
                "tool_name": proposal.action.tool_name,
                "risk_level": proposal.risk_level.value,
                "title": proposal.title,
                "reason": proposal.reason,
                "expected_effect": proposal.expected_effect,
                "action": proposal.action.model_dump(mode="json"),
                "version": proposal.version,
                "payload_hash": proposal.payload_hash,
                "allowed_decisions": sorted(item.value for item in rule.allowed_decisions),
                "expires_at": proposal.expires_at.isoformat(),
            }
        )
        return {
            "decision": cast(dict[str, Any], decision),
            "status": RunStatus.RUNNING.value,
        }

    async def handle_decision_node(state: RelayState) -> dict[str, Any]:
        raw = state.get("decision") or {}
        decision = DecisionType(str(raw.get("decision")))
        step = repository.get_step_at(state["run_id"], int(state["current_index"]))
        if decision is DecisionType.REVISE:
            revised = normalize_planned_actions(
                [parse_action(cast(dict[str, Any], raw["revised_action"]))], settings
            )[0]
            repository.update_step(step.id, status=StepStatus.PENDING, action=revised)
            return {
                "plan": [
                    item.model_dump(mode="json") for item in repository.list_steps(state["run_id"])
                ],
                "decision": None,
                "current_proposal_id": None,
                "status": RunStatus.RUNNING.value,
            }
        if decision is DecisionType.REJECT:
            repository.update_step(step.id, status=StepStatus.REJECTED)
            return {
                "rejected_count": int(state.get("rejected_count", 0)) + 1,
                "decision": None,
                "status": "rejected",
            }
        return {"status": "approved"}

    def route_decision(state: RelayState) -> Literal["execute", "advance", "prepare"]:
        status = state.get("status")
        if status == "approved":
            return "execute"
        if status == "rejected":
            return "advance"
        return "prepare"

    async def execute_node(state: RelayState) -> dict[str, Any]:
        step = repository.get_step_at(state["run_id"], int(state["current_index"]))
        receipt = await tools.execute(
            run_id=state["run_id"],
            step=step,
            proposal_id=state.get("current_proposal_id"),
        )
        observations = [*state.get("observations", []), receipt.model_dump(mode="json")]
        if receipt.status is ReceiptStatus.OUTCOME_UNKNOWN:
            repository.set_run_status(
                state["run_id"],
                RunStatus.NEEDS_ATTENTION,
                current_index=int(state["current_index"]),
                error_code="outcome_unknown",
                error_summary="A provider outcome requires manual reconciliation.",
            )
            return {"observations": observations, "status": "needs_attention"}
        if receipt.status is ReceiptStatus.FAILED:
            repository.set_run_status(
                state["run_id"],
                RunStatus.FAILED,
                current_index=int(state["current_index"]),
                error_code=receipt.error_code,
                error_summary="A tool failed safely.",
            )
            return {"observations": observations, "status": "failed"}
        return {
            "observations": observations,
            "plan": [
                item.model_dump(mode="json") for item in repository.list_steps(state["run_id"])
            ],
            "status": "executed",
        }

    def route_executed(state: RelayState) -> Literal["advance", "finalize"]:
        return "advance" if state.get("status") == "executed" else "finalize"

    async def advance_node(state: RelayState) -> dict[str, Any]:
        next_index = int(state.get("current_index", 0)) + 1
        repository.set_run_status(state["run_id"], RunStatus.RUNNING, current_index=next_index)
        return {
            "current_index": next_index,
            "current_proposal_id": None,
            "current_interrupt_id": None,
            "decision": None,
            "status": RunStatus.RUNNING.value,
        }

    async def finalize_node(state: RelayState) -> dict[str, Any]:
        if state.get("status") == "needs_attention":
            return {"summary": "Workflow paused for manual outcome reconciliation."}
        if state.get("status") == "failed":
            return {"summary": "Workflow stopped after a safe tool failure."}
        rejected = int(state.get("rejected_count", 0))
        status = RunStatus.COMPLETED_WITH_REJECTIONS if rejected else RunStatus.COMPLETED
        summary = (
            "Workflow completed with one or more operator-rejected actions."
            if rejected
            else "Workflow completed with every reviewed action recorded."
        )
        repository.set_run_status(
            state["run_id"],
            status,
            current_index=int(state.get("current_index", 0)),
            summary=summary,
        )
        return {"status": status.value, "summary": summary}

    builder = StateGraph(RelayState)
    nodes: dict[str, Callable[..., Any]] = {
        "plan": plan_node,
        "prepare": prepare_node,
        "propose": propose_node,
        "request_approval": request_approval_node,
        "handle_decision": handle_decision_node,
        "execute": execute_node,
        "advance": advance_node,
        "finalize": finalize_node,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "prepare")
    builder.add_conditional_edges("prepare", route_prepared)
    builder.add_edge("propose", "request_approval")
    builder.add_edge("request_approval", "handle_decision")
    builder.add_conditional_edges("handle_decision", route_decision)
    builder.add_conditional_edges("execute", route_executed)
    builder.add_edge("advance", "prepare")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
