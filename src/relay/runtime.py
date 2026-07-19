"""Durable runtime lifecycle shared by Relay's API, CLI, and tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from relay.config import Settings, get_settings
from relay.graph import build_graph
from relay.models import (
    ActionProposal,
    ApprovalDecisionRequest,
    ApprovalView,
    CalendarCreateAction,
    ConnectorStatus,
    DatabaseUpdateAction,
    DecisionType,
    EmailSendAction,
    Mode,
    PlannedStep,
    ProposalStatus,
    PurchaseOrderCreateAction,
    ReceiptStatus,
    RunDetail,
    RunRecord,
    RunStatus,
    StepStatus,
    action_payload_hash,
    parse_action,
)
from relay.persistence import ConflictError, RelayRepository
from relay.planner import Planner, build_planner, normalize_planned_actions
from relay.policy import DEFAULT_POLICY, ToolPolicy
from relay.state import initial_state
from relay.tools.registry import ToolBundle, build_tool_bundle


class RuntimeConflictError(ConflictError):
    """A graph thread is busy or no longer matches an approval."""


class RelayRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RelayRepository,
        policy: ToolPolicy,
        planner: Planner,
        tools: ToolBundle,
        graph: Any,
        checkpointer: Any,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.policy = policy
        self.planner = planner
        self.tools = tools
        self.graph = graph
        self.checkpointer = checkpointer
        self._locks: dict[str, asyncio.Lock] = {}
        self._maintenance_lock = asyncio.Lock()

    def config(self, run_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": run_id},
            "recursion_limit": max(50, self.settings.max_plan_steps * 8),
            "tags": ["relay", self.settings.mode],
            "metadata": {"run_id": run_id, "mode": self.settings.mode},
        }

    @asynccontextmanager
    async def _file_guard(
        self,
        *,
        lock: asyncio.Lock,
        path: Any,
        conflict_message: str,
    ) -> AsyncIterator[None]:
        async with lock:
            process_lock = FileLock(path)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5
            while True:
                try:
                    process_lock.acquire(timeout=0)
                    break
                except FileLockTimeout as exc:
                    if loop.time() >= deadline:
                        raise RuntimeConflictError(conflict_message) from exc
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                process_lock.release()

    @asynccontextmanager
    async def _maintenance_guard(self) -> AsyncIterator[None]:
        async with self._file_guard(
            lock=self._maintenance_lock,
            path=self.settings.lock_dir / "maintenance.lock",
            conflict_message="Another runtime operation is in progress.",
        ):
            yield

    @asynccontextmanager
    async def _run_guard(self, run_id: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with self._file_guard(
            lock=lock,
            path=self.settings.lock_dir / f"{run_id}.lock",
            conflict_message="Another runtime is working on this run.",
        ):
            yield

    @staticmethod
    def _snapshot_interrupts(snapshot: Any) -> tuple[Any, ...]:
        return tuple(item for task in snapshot.tasks for item in getattr(task, "interrupts", ()))

    def _validate_interrupt(self, run_id: str, interrupt_item: Any) -> ActionProposal:
        value = interrupt_item.value
        if not isinstance(value, dict) or not isinstance(value.get("proposal_id"), str):
            raise RuntimeConflictError("Graph returned an invalid approval interrupt.")
        proposal = self.repository.get_proposal(value["proposal_id"])
        try:
            interrupt_action = parse_action(value["action"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConflictError("Graph approval payload is invalid.") from exc
        if (
            proposal.run_id != run_id
            or value.get("run_id") != run_id
            or value.get("step_id") != proposal.step_id
            or value.get("tool_name") != proposal.tool_name
            or value.get("version") != proposal.version
            or value.get("payload_hash") != proposal.payload_hash
            or action_payload_hash(interrupt_action) != proposal.payload_hash
        ):
            raise RuntimeConflictError("Graph approval does not match persisted proposal state.")
        return proposal

    async def _bind_interrupts(self, run_id: str, interrupts: tuple[Any, ...]) -> None:
        if len(interrupts) > 1:
            raise RuntimeConflictError("Relay supports one pending approval per run.")
        if not interrupts:
            return
        interrupt_item = interrupts[0]
        proposal = self._validate_interrupt(run_id, interrupt_item)
        if proposal.interrupt_id is None:
            self.repository.bind_interrupt(proposal.id, interrupt_item.id)
        elif proposal.interrupt_id != interrupt_item.id:
            raise RuntimeConflictError("Proposal is bound to a different graph interrupt.")

    @staticmethod
    def _terminal_for_decisions(status: RunStatus) -> bool:
        return status in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_REJECTIONS,
            RunStatus.NEEDS_ATTENTION,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }

    def _graph_advanced_past(
        self,
        *,
        run: RunRecord,
        step: PlannedStep,
        current_proposal: ActionProposal | None,
    ) -> bool:
        return (
            (current_proposal is not None and current_proposal.step_id != step.id)
            or run.current_index > step.position
            or self._terminal_for_decisions(run.status)
        )

    def _decision_has_durable_effect(
        self,
        *,
        decision: Any,
        proposal: ActionProposal,
        current_proposal: ActionProposal | None,
    ) -> bool:
        run = self.repository.get_run(proposal.run_id)
        step = self.repository.get_step(proposal.step_id)
        advanced = self._graph_advanced_past(
            run=run,
            step=step,
            current_proposal=current_proposal,
        )
        if decision.decision is DecisionType.APPROVE:
            receipt = self.repository.get_receipt_by_proposal(proposal.id)
            if receipt is None:
                return False
            expected_proposal_status = {
                ReceiptStatus.SUCCEEDED: ProposalStatus.SUCCEEDED,
                ReceiptStatus.FAILED: ProposalStatus.FAILED,
                ReceiptStatus.OUTCOME_UNKNOWN: ProposalStatus.OUTCOME_UNKNOWN,
            }[receipt.status]
            return (
                advanced
                and receipt.run_id == proposal.run_id
                and receipt.proposal_id == proposal.id
                and receipt.step_id == proposal.step_id
                and receipt.tool_name == proposal.tool_name
                and receipt.request_hash == proposal.payload_hash
                and receipt.idempotency_key
                == f"relay:{proposal.run_id}:{proposal.step_id}:{proposal.version}"
                and proposal.status is expected_proposal_status
                and step.receipt_id == receipt.id
            )
        if decision.decision is DecisionType.REJECT:
            return (
                advanced
                and proposal.status is ProposalStatus.REJECTED
                and step.status is StepStatus.REJECTED
            )
        return (
            proposal.status is ProposalStatus.SUPERSEDED
            and current_proposal is not None
            and current_proposal.step_id == proposal.step_id
            and current_proposal.version > proposal.version
        )

    async def _reconcile_run_locked(self, run_id: str) -> None:
        """Repair only state transitions proven by the durable graph checkpoint."""
        config = self.config(run_id)
        snapshot = await self.graph.aget_state(config)
        interrupts = self._snapshot_interrupts(snapshot)
        if len(interrupts) > 1:
            raise RuntimeConflictError("Relay supports one pending approval per run.")
        current_proposal: ActionProposal | None = None
        current_interrupt_id: str | None = None
        if interrupts:
            current_proposal = self._validate_interrupt(run_id, interrupts[0])
            current_interrupt_id = str(interrupts[0].id)
            if current_proposal.interrupt_id is None:
                if current_proposal.status is not ProposalStatus.PENDING:
                    raise RuntimeConflictError("A decided proposal has an unbound graph interrupt.")
                current_proposal = self.repository.bind_interrupt(
                    current_proposal.id, current_interrupt_id
                )
            elif current_proposal.interrupt_id != current_interrupt_id:
                raise RuntimeConflictError("Proposal is bound to a different graph interrupt.")

        for decision in self.repository.list_unconsumed_decisions(run_id):
            proposal = self.repository.get_proposal(decision.proposal_id)
            if current_interrupt_id is not None and proposal.interrupt_id == current_interrupt_id:
                continue
            if self._decision_has_durable_effect(
                decision=decision,
                proposal=proposal,
                current_proposal=current_proposal,
            ):
                self.repository.mark_decision_consumed(decision.id)
                continue
            run = self.repository.get_run(run_id)
            if run.status is not RunStatus.NEEDS_ATTENTION:
                self.repository.set_run_status(
                    run_id,
                    RunStatus.NEEDS_ATTENTION,
                    error_code="checkpoint_reconciliation_failed",
                    error_summary="Durable workflow state requires operator reconciliation.",
                )

    async def start(
        self,
        instruction: str,
        *,
        mode: Mode | None = None,
        idempotency_key: str | None = None,
    ) -> RunDetail:
        requested_mode = mode or Mode(self.settings.mode)
        if requested_mode.value != self.settings.mode:
            raise ValueError("A run cannot switch the configured demo/live mode.")
        async with self._maintenance_guard():
            run = self.repository.create_run(
                instruction=instruction,
                mode=requested_mode,
                idempotency_key=idempotency_key,
            )
            async with self._run_guard(run.id):
                current = self.repository.get_run(run.id)
                if self.repository.list_steps(run.id) or self._terminal_for_decisions(
                    current.status
                ):
                    await self._reconcile_run_locked(run.id)
                    return self.detail(run.id)
                try:
                    output = await self.graph.ainvoke(
                        initial_state(
                            run_id=run.id,
                            instruction=run.instruction,
                            mode=run.mode.value,
                        ),
                        self.config(run.id),
                        durability="sync",
                        version="v2",
                    )
                    await self._bind_interrupts(run.id, tuple(output.interrupts))
                except Exception as exc:
                    self.repository.set_run_status(
                        run.id,
                        RunStatus.FAILED,
                        error_code=type(exc).__name__,
                        error_summary="The workflow failed safely during startup.",
                    )
                    raise
                return self.detail(run.id)

    async def decide(self, proposal_id: str, request: ApprovalDecisionRequest) -> RunDetail:
        async with self._maintenance_guard():
            proposal = self.repository.get_proposal(proposal_id)
            run_id = proposal.run_id
            async with self._run_guard(run_id):
                await self._reconcile_run_locked(run_id)
                proposal = self.repository.get_proposal(proposal_id)
                run = self.repository.get_run(run_id)
                rule = self.policy.for_action(proposal.action)
                if request.decision not in rule.allowed_decisions:
                    raise ConflictError("That decision is not allowed for this action.")
                if request.decision is DecisionType.REVISE:
                    revised = normalize_planned_actions(
                        [parse_action(request.revised_action or {})], self.settings
                    )[0]
                    if revised.tool_name != proposal.tool_name:
                        raise ConflictError("A revision cannot change the tool type.")
                    request = request.model_copy(
                        update={"revised_action": revised.model_dump(mode="json")}
                    )
                existing_decision = self.repository.get_decision_by_idempotency(
                    request.idempotency_key
                )
                if existing_decision is not None:
                    self.repository.assert_decision_request_matches(
                        existing_decision, proposal_id, request
                    )
                    if existing_decision.consumed_at is not None:
                        return self.detail(run_id)
                if self._terminal_for_decisions(run.status):
                    raise ConflictError("A terminal run cannot accept approval decisions.")
                config = self.config(run_id)
                snapshot = await self.graph.aget_state(config)
                interrupts = self._snapshot_interrupts(snapshot)
                if (
                    len(interrupts) != 1
                    or proposal.interrupt_id is None
                    or interrupts[0].id != proposal.interrupt_id
                ):
                    existing = self.repository.get_decision_by_idempotency(request.idempotency_key)
                    if existing is not None:
                        self.repository.assert_decision_request_matches(
                            existing, proposal_id, request
                        )
                        if existing.consumed_at is not None:
                            return self.detail(run_id)
                    raise RuntimeConflictError("The approval is stale or no longer pending.")
                decision = self.repository.record_decision(proposal_id, request)
                if decision.consumed_at is not None:
                    return self.detail(run_id)
                resume_payload = decision.model_dump(
                    mode="json",
                    exclude={
                        "id",
                        "proposal_id",
                        "created_at",
                        "consumed_at",
                        "idempotency_key",
                    },
                )
                output = await self.graph.ainvoke(
                    Command(resume={proposal.interrupt_id: resume_payload}),
                    config,
                    durability="sync",
                    version="v2",
                )
                await self._bind_interrupts(run_id, tuple(output.interrupts))
                self.repository.mark_decision_consumed(decision.id)
                return self.detail(run_id)

    async def get_detail(self, run_id: str) -> RunDetail:
        async with self._maintenance_guard(), self._run_guard(run_id):
            self.repository.get_run(run_id)
            await self._reconcile_run_locked(run_id)
            return self.detail(run_id)

    def detail(self, run_id: str) -> RunDetail:
        self.repository.expire_due_proposals(run_id=run_id)
        run = self.repository.get_run(run_id)
        events, _ = self.repository.list_audit_events(run_id=run_id, limit=1000)
        pending = self.repository.pending_proposal(run_id)
        return RunDetail(
            run=run,
            steps=self.repository.list_steps(run_id),
            pending_approval=self._approval_view(pending) if pending else None,
            audit_events=events,
            receipts=self.repository.list_receipts(run_id),
        )

    def _approval_view(self, proposal: ActionProposal) -> ApprovalView:
        rule = self.policy.for_action(proposal.action)
        action = proposal.action
        review_context: dict[str, Any] = {
            "effect": rule.description,
            "requires_exact_payload_approval": True,
        }
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        if isinstance(action, EmailSendAction):
            review_context.update(
                {
                    "sender_identity": action.sender,
                    "recipient_count": len(action.to) + len(action.cc),
                    "delivery_behavior": "The email API sends the exact reviewed message.",
                }
            )
        elif isinstance(action, CalendarCreateAction):
            notification_copy = {
                "none": "Google Calendar will not send attendee notifications.",
                "all": "Google Calendar will notify every attendee.",
                "externalOnly": "Google Calendar will notify external attendees only.",
            }
            review_context.update(
                {
                    "attendee_count": len(action.attendees),
                    "send_updates": action.send_updates,
                    "notification_behavior": notification_copy[action.send_updates],
                }
            )
        elif isinstance(action, DatabaseUpdateAction):
            before = self.repository.get_demo_customer(action.record_id)
            current_version = int(before["version"]) if before is not None else None
            version_match = current_version == action.expected_version
            review_context.update(
                {
                    "changed_fields": sorted(action.changes),
                    "expected_version": action.expected_version,
                    "current_version": current_version,
                    "version_match": version_match,
                }
            )
            if version_match and before is not None:
                after = {
                    **before,
                    **action.changes,
                    "version": int(before["version"]) + 1,
                }
            else:
                review_context["warning"] = (
                    "The current record no longer matches the reviewed version."
                )
        elif isinstance(action, PurchaseOrderCreateAction):
            review_context.update({"amount": str(action.amount), "currency": action.currency})
        return ApprovalView(
            **proposal.model_dump(mode="python", exclude={"tool_name"}),
            allowed_decisions=sorted(rule.allowed_decisions, key=lambda item: item.value),
            review_context=review_context,
            before=before,
            after=after,
        )

    def approval(self, proposal_id: str) -> ApprovalView:
        return self._approval_view(self.repository.get_proposal(proposal_id))

    def list_runs(
        self, *, limit: int = 50, offset: int = 0, status: RunStatus | None = None
    ) -> tuple[list[RunRecord], int]:
        return self.repository.list_runs(limit=limit, offset=offset, status=status)

    def list_approvals(
        self, *, status: ProposalStatus | None = ProposalStatus.PENDING, run_id: str | None = None
    ) -> list[ApprovalView]:
        return [
            self._approval_view(item)
            for item in self.repository.list_proposals(status=status, run_id=run_id)
        ]

    def connectors(self) -> list[ConnectorStatus]:
        mode = Mode(self.settings.mode)
        configured = self.settings.connector_configuration()
        if mode is Mode.DEMO:
            return [
                ConnectorStatus(
                    name=name,
                    mode=mode,
                    configured=True,
                    ready=True,
                    detail="Deterministic demo adapter; no external request is made.",
                )
                for name in (
                    "anthropic",
                    "web_search",
                    "email",
                    "calendar",
                    "database",
                    "purchasing",
                )
            ]
        details = {
            "anthropic": "Claude structured planner",
            "web_search": "Tavily search API",
            "email": "Resend email API",
            "calendar": "Google Calendar API",
            "database": "Local allowlisted SQLite records",
            "purchasing": "Live purchasing intentionally disabled",
        }
        return [
            ConnectorStatus(
                name=name,
                mode=mode,
                configured=value,
                ready=value and name != "purchasing",
                detail=details[name],
            )
            for name, value in configured.items()
        ]

    def readiness(self) -> dict[str, Any]:
        connectors = self.connectors()
        required_names = {"anthropic", "web_search", "email", "calendar", "database"}
        required = [item for item in connectors if item.name in required_names]
        ready = all(item.ready for item in required)
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "mode": self.settings.mode,
            "database": {"ready": self.settings.database_path.exists()},
            "checkpoint": {"ready": self.settings.checkpoint_path.exists()},
            "connectors": [item.model_dump(mode="json") for item in connectors],
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "mode": self.settings.mode,
            "tools": self.policy.capabilities(),
            "features": {
                "persistent_checkpoints": True,
                "exact_payload_approval": True,
                "audit_hash_chain": True,
                "demo_reset": self.settings.mode == "demo",
                "live_replay_after_write": False,
            },
        }

    async def reset_demo(self) -> dict[str, Any]:
        if self.settings.mode != "demo":
            raise ConflictError("Demo reset is unavailable in live mode.")
        async with self._maintenance_guard():
            for thread_id in self.repository.list_run_thread_ids():
                await self.checkpointer.adelete_thread(thread_id)
            self.repository.reset_demo()
            return {"status": "reset", "counts": self.repository.demo_counts()}

    async def cancel(self, run_id: str) -> RunDetail:
        async with self._maintenance_guard(), self._run_guard(run_id):
            await self._reconcile_run_locked(run_id)
            self.repository.cancel_run(run_id)
            return self.detail(run_id)

    def graph_mermaid(self) -> str:
        return cast(str, self.graph.get_graph().draw_mermaid())


@asynccontextmanager
async def open_runtime(
    settings: Settings | None = None,
    *,
    repository: RelayRepository | None = None,
    policy: ToolPolicy | None = None,
    planner: Planner | None = None,
    tools: ToolBundle | None = None,
) -> AsyncIterator[RelayRuntime]:
    active_settings = settings or get_settings()
    active_settings.ensure_directories()
    active_settings.assert_safe_bind()
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    active_repository = repository or RelayRepository(active_settings.database_path)
    active_policy = policy or DEFAULT_POLICY
    active_planner = planner or build_planner(active_settings, active_policy)
    active_tools = tools or build_tool_bundle(active_settings, active_repository, active_policy)
    async with AsyncSqliteSaver.from_conn_string(str(active_settings.checkpoint_path)) as saver:
        await saver.setup()
        graph = build_graph(
            settings=active_settings,
            repository=active_repository,
            planner=active_planner,
            policy=active_policy,
            tools=active_tools,
            checkpointer=saver,
        )
        yield RelayRuntime(
            settings=active_settings,
            repository=active_repository,
            policy=active_policy,
            planner=active_planner,
            tools=active_tools,
            graph=graph,
            checkpointer=saver,
        )
