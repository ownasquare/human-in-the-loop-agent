from __future__ import annotations

import asyncio

import pytest

from relay.models import (
    ApprovalDecisionRequest,
    DatabaseUpdateAction,
    DecisionType,
    ProposalStatus,
    ReceiptStatus,
    RunStatus,
)
from relay.persistence import ConflictError
from relay.runtime import open_runtime


def decision(proposal, *, kind: DecisionType = DecisionType.APPROVE, suffix: str = "approve"):
    return ApprovalDecisionRequest(
        decision=kind,
        expected_version=proposal.version,
        expected_payload_hash=proposal.payload_hash,
        reason="Reviewed by the operator" if kind is DecisionType.REJECT else "",
        idempotency_key=f"decision-{proposal.id}-{suffix}",
    )


async def test_demo_pauses_sequentially_before_every_write(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        assert result.pending_approval.tool_name == "calendar_create"
        assert result.pending_approval.run_id == result.run.id
        assert result.pending_approval.step_id == result.steps[3].id
        assert set(result.pending_approval.allowed_decisions) == {
            DecisionType.APPROVE,
            DecisionType.REJECT,
            DecisionType.REVISE,
        }
        assert result.pending_approval.action.send_updates == "none"
        assert result.pending_approval.review_context["notification_behavior"] == (
            "Google Calendar will not send attendee notifications."
        )
        assert runtime.repository.demo_counts() == {
            "customers": 1,
            "calendar_events": 0,
            "emails": 0,
            "purchase_orders": 0,
        }

        result = await runtime.decide(
            result.pending_approval.id, decision(result.pending_approval, suffix="calendar")
        )
        assert result.pending_approval.tool_name == "email_send"
        assert result.pending_approval.action.sender == settings.email_from
        assert result.pending_approval.review_context["sender_identity"] == (settings.email_from)
        assert DecisionType.REVISE in result.pending_approval.allowed_decisions
        assert runtime.repository.demo_counts()["calendar_events"] == 1
        assert runtime.repository.demo_counts()["emails"] == 0

        result = await runtime.decide(
            result.pending_approval.id, decision(result.pending_approval, suffix="email")
        )
        assert result.pending_approval.tool_name == "db_update_record"
        assert result.pending_approval.before["version"] == 1
        assert result.pending_approval.after["version"] == 2
        assert result.pending_approval.after["status"] == "review_scheduled"
        assert result.pending_approval.review_context["version_match"] is True
        assert set(result.pending_approval.allowed_decisions) == {
            DecisionType.APPROVE,
            DecisionType.REJECT,
        }
        assert runtime.repository.demo_counts()["emails"] == 1

        result = await runtime.decide(
            result.pending_approval.id, decision(result.pending_approval, suffix="database")
        )
        assert result.pending_approval.tool_name == "purchase_order_create"
        assert set(result.pending_approval.allowed_decisions) == {
            DecisionType.APPROVE,
            DecisionType.REJECT,
        }
        assert runtime.repository.demo_counts()["purchase_orders"] == 0

        result = await runtime.decide(
            result.pending_approval.id, decision(result.pending_approval, suffix="purchase")
        )
        assert result.pending_approval is None
        assert result.run.status is RunStatus.COMPLETED
        assert runtime.repository.demo_counts()["purchase_orders"] == 1
        assert len(result.receipts) == 7
        assert runtime.repository.verify_audit_chain(result.run.id)


async def test_rejection_has_no_effect_and_workflow_continues(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        result = await runtime.decide(
            result.pending_approval.id,
            decision(
                result.pending_approval,
                kind=DecisionType.REJECT,
                suffix="reject-calendar",
            ),
        )
        assert runtime.repository.demo_counts()["calendar_events"] == 0
        assert result.pending_approval.tool_name == "email_send"


async def test_revision_creates_new_pending_version(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        original = result.pending_approval
        revised = original.action.model_dump(mode="json")
        revised["summary"] = "Revised Acme renewal review"
        revised["calendar_id"] = "unconfigured-calendar@example.net"
        revised["send_updates"] = "all"
        request = ApprovalDecisionRequest(
            decision=DecisionType.REVISE,
            expected_version=original.version,
            expected_payload_hash=original.payload_hash,
            revised_action=revised,
            idempotency_key=f"decision-{original.id}-revise",
        )
        result = await runtime.decide(original.id, request)
        assert result.pending_approval.id != original.id
        assert result.pending_approval.version == original.version + 1
        assert result.pending_approval.action.summary == "Revised Acme renewal review"
        assert result.pending_approval.action.calendar_id == "primary"
        assert result.pending_approval.action.send_updates == "all"
        assert runtime.repository.demo_counts()["calendar_events"] == 0


async def test_email_revision_cannot_change_server_owned_sender(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        result = await runtime.decide(
            result.pending_approval.id,
            decision(result.pending_approval, suffix="calendar-before-email-revise"),
        )
        original = result.pending_approval
        revised = original.action.model_dump(mode="json")
        revised["sender"] = "unconfigured-sender@example.net"
        revised["subject"] = "Reviewed subject"
        result = await runtime.decide(
            original.id,
            ApprovalDecisionRequest(
                decision=DecisionType.REVISE,
                expected_version=original.version,
                expected_payload_hash=original.payload_hash,
                revised_action=revised,
                idempotency_key=f"decision-{original.id}-sender-revise",
            ),
        )
        assert result.pending_approval.action.sender == settings.email_from
        assert result.pending_approval.action.subject == "Reviewed subject"


async def test_identical_revision_still_creates_fresh_pending_proposal(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        original = result.pending_approval
        request = ApprovalDecisionRequest(
            decision=DecisionType.REVISE,
            expected_version=original.version,
            expected_payload_hash=original.payload_hash,
            revised_action=original.action.model_dump(mode="json"),
            idempotency_key=f"decision-{original.id}-identical-revise",
        )
        result = await runtime.decide(original.id, request)
        assert result.pending_approval.id != original.id
        assert result.pending_approval.version == original.version + 1
        assert result.pending_approval.status is ProposalStatus.PENDING


async def test_runtime_reopen_resumes_same_interrupt(settings) -> None:
    async with open_runtime(settings) as first:
        result = await first.start("Prepare the Acme renewal workflow")
        run_id = result.run.id
        proposal = result.pending_approval

    async with open_runtime(settings) as second:
        assert (await second.get_detail(run_id)).pending_approval.id == proposal.id
        result = await second.decide(proposal.id, decision(proposal, suffix="after-reopen"))
        assert result.pending_approval.tool_name == "email_send"
        assert second.repository.demo_counts()["calendar_events"] == 1


async def test_database_revision_is_denied_before_decision_persistence(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        for suffix in ("calendar-to-db", "email-to-db"):
            result = await runtime.decide(
                result.pending_approval.id,
                decision(result.pending_approval, suffix=suffix),
            )
        proposal = result.pending_approval
        assert proposal.tool_name == "db_update_record"
        with pytest.raises(ConflictError, match="not allowed"):
            await runtime.decide(
                proposal.id,
                ApprovalDecisionRequest(
                    decision=DecisionType.REVISE,
                    expected_version=proposal.version,
                    expected_payload_hash=proposal.payload_hash,
                    revised_action=proposal.action.model_dump(mode="json"),
                    idempotency_key=f"decision-{proposal.id}-forbidden-revise",
                ),
            )
        assert runtime.repository.get_proposal(proposal.id).status is ProposalStatus.PENDING


async def test_cancel_invalidates_pending_approval_and_blocks_resume(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval
        cancelled = await runtime.cancel(result.run.id)
        assert cancelled.run.status is RunStatus.CANCELLED
        assert cancelled.pending_approval is None
        assert runtime.repository.get_proposal(proposal.id).status is ProposalStatus.CANCELLED
        with pytest.raises(ConflictError, match="terminal run"):
            await runtime.decide(proposal.id, decision(proposal, suffix="after-cancel"))
        assert runtime.repository.demo_counts()["calendar_events"] == 0


async def test_cancel_queued_before_decision_prevents_post_cancel_write(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval
        async with runtime._run_guard(result.run.id):
            cancel_task = asyncio.create_task(runtime.cancel(result.run.id))
            await asyncio.sleep(0)
            decision_task = asyncio.create_task(
                runtime.decide(
                    proposal.id,
                    decision(proposal, suffix="serialized-after-cancel"),
                )
            )
            await asyncio.sleep(0)
            assert not cancel_task.done()
            assert not decision_task.done()
        cancelled = await cancel_task
        assert cancelled.run.status is RunStatus.CANCELLED
        with pytest.raises(ConflictError, match="terminal run"):
            await decision_task
        assert runtime.repository.demo_counts()["calendar_events"] == 0


async def test_concurrent_same_key_start_creates_one_logical_plan(settings) -> None:
    async with open_runtime(settings) as runtime:
        first, second = await asyncio.gather(
            runtime.start(
                "Prepare the Acme renewal workflow",
                idempotency_key="concurrent-run-key",
            ),
            runtime.start(
                "Prepare the Acme renewal workflow",
                idempotency_key="concurrent-run-key",
            ),
        )
        assert first.run.id == second.run.id
        assert len(runtime.repository.list_steps(first.run.id)) == 7
        assert len(runtime.repository.list_proposals(run_id=first.run.id)) == 1
        events, _ = runtime.repository.list_audit_events(run_id=first.run.id)
        assert sum(event.event_type == "plan.created" for event in events) == 1
        assert len(runtime.repository.list_receipts(first.run.id)) == 3


async def test_lost_checkpoint_cannot_mutate_proposal_decision(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval
        await runtime.checkpointer.adelete_thread(result.run.thread_id)
        with pytest.raises(ConflictError, match="stale"):
            await runtime.decide(proposal.id, decision(proposal, suffix="lost-checkpoint"))
        assert runtime.repository.get_proposal(proposal.id).status is ProposalStatus.PENDING


async def test_consumed_decision_key_requires_exact_same_request(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval
        request = decision(proposal, suffix="consumed-exact")
        next_result = await runtime.decide(proposal.id, request)
        repeated = await runtime.decide(proposal.id, request)
        assert repeated.pending_approval.id == next_result.pending_approval.id
        changed = request.model_copy(update={"reason": "different normalized body"})
        with pytest.raises(ConflictError, match="different request"):
            await runtime.decide(proposal.id, changed)


async def test_demo_reset_removes_domain_rows_and_checkpoints(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        config = runtime.config(result.run.id)
        assert (await runtime.graph.aget_state(config)).values
        reset = await runtime.reset_demo()
        assert reset["counts"]["customers"] == 1
        assert runtime.list_runs()[1] == 0
        assert not (await runtime.graph.aget_state(config)).values


async def test_reopen_repairs_checkpointed_but_unbound_interrupt(settings, monkeypatch) -> None:
    async with open_runtime(settings) as first:

        async def skip_binding(_run_id, _interrupts) -> None:
            return None

        monkeypatch.setattr(first, "_bind_interrupts", skip_binding)
        result = await first.start("Prepare the Acme renewal workflow")
        run_id = result.run.id
        proposal_id = result.pending_approval.id
        assert first.repository.get_proposal(proposal_id).interrupt_id is None

    async with open_runtime(settings) as second:
        repaired = await second.get_detail(run_id)
        assert repaired.pending_approval.id == proposal_id
        assert second.repository.get_proposal(proposal_id).interrupt_id is not None
        resumed = await second.decide(
            proposal_id,
            decision(repaired.pending_approval, suffix="after-binding-repair"),
        )
        assert resumed.pending_approval.tool_name == "email_send"


async def test_unconsumed_decision_replays_when_original_interrupt_is_still_current(
    settings, monkeypatch
) -> None:
    request: ApprovalDecisionRequest
    async with open_runtime(settings) as first:
        result = await first.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval
        request = decision(proposal, suffix="crash-before-resume")

        async def crash_before_resume(*_args, **_kwargs):
            raise RuntimeError("simulated crash before durable graph resume")

        monkeypatch.setattr(first.graph, "ainvoke", crash_before_resume)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await first.decide(proposal.id, request)
        stored = first.repository.get_decision_by_idempotency(request.idempotency_key)
        assert stored is not None
        assert stored.consumed_at is None
        assert first.repository.get_proposal(proposal.id).status is ProposalStatus.APPROVED
        assert first.repository.demo_counts()["calendar_events"] == 0

    async with open_runtime(settings) as second:
        resumed = await second.decide(proposal.id, request)
        assert resumed.pending_approval.tool_name == "email_send"
        assert second.repository.demo_counts()["calendar_events"] == 1
        stored = second.repository.get_decision_by_idempotency(request.idempotency_key)
        assert stored is not None
        assert stored.consumed_at is not None


async def test_reopen_repairs_successor_binding_and_consumption_together(
    settings, monkeypatch
) -> None:
    async with open_runtime(settings) as first:
        result = await first.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval

        async def skip_binding(_run_id, _interrupts) -> None:
            return None

        def skip_consumption(_decision_id: str) -> None:
            return None

        monkeypatch.setattr(first, "_bind_interrupts", skip_binding)
        monkeypatch.setattr(first.repository, "mark_decision_consumed", skip_consumption)
        advanced = await first.decide(
            proposal.id,
            decision(proposal, suffix="combined-crash-window"),
        )
        run_id = advanced.run.id
        successor_id = advanced.pending_approval.id
        assert first.repository.get_proposal(successor_id).interrupt_id is None
        assert (
            first.repository.get_decision_by_idempotency(
                f"decision-{proposal.id}-combined-crash-window"
            ).consumed_at
            is None
        )

    async with open_runtime(settings) as second:
        repaired = await second.get_detail(run_id)
        assert repaired.pending_approval.id == successor_id
        assert second.repository.get_proposal(successor_id).interrupt_id is not None
        stored = second.repository.get_decision_by_idempotency(
            f"decision-{proposal.id}-combined-crash-window"
        )
        assert stored is not None
        assert stored.consumed_at is not None


@pytest.mark.parametrize(
    "kind",
    [DecisionType.APPROVE, DecisionType.REJECT, DecisionType.REVISE],
)
async def test_reopen_reconciles_checkpointed_unconsumed_decision(
    settings, monkeypatch, kind: DecisionType
) -> None:
    decision_key = f"crash-consumption-{kind.value}"
    async with open_runtime(settings) as first:
        result = await first.start("Prepare the Acme renewal workflow")
        proposal = result.pending_approval

        def skip_consumption(_decision_id: str) -> None:
            return None

        monkeypatch.setattr(first.repository, "mark_decision_consumed", skip_consumption)
        revised_action = None
        reason = ""
        if kind is DecisionType.REVISE:
            revised_action = proposal.action.model_dump(mode="json")
            revised_action["summary"] = "Crash-safe revised review"
        elif kind is DecisionType.REJECT:
            reason = "Operator rejected the action"
        request = ApprovalDecisionRequest(
            decision=kind,
            expected_version=proposal.version,
            expected_payload_hash=proposal.payload_hash,
            reason=reason,
            revised_action=revised_action,
            idempotency_key=decision_key,
        )
        advanced = await first.decide(proposal.id, request)
        run_id = advanced.run.id
        assert advanced.pending_approval is not None
        assert first.repository.get_decision_by_idempotency(decision_key).consumed_at is None

    async with open_runtime(settings) as second:
        repaired = await second.get_detail(run_id)
        assert repaired.pending_approval is not None
        assert second.repository.get_decision_by_idempotency(decision_key).consumed_at is not None


async def test_database_version_conflict_records_stable_failure(settings) -> None:
    async with open_runtime(settings) as runtime:
        result = await runtime.start("Prepare the Acme renewal workflow")
        for suffix in ("calendar-before-db-conflict", "email-before-db-conflict"):
            result = await runtime.decide(
                result.pending_approval.id,
                decision(result.pending_approval, suffix=suffix),
            )
        proposal = result.pending_approval
        assert proposal.tool_name == "db_update_record"
        runtime.repository.demo_update_customer(
            idempotency_key="external-customer-change",
            action=DatabaseUpdateAction(
                record_id="CUST-001",
                expected_version=1,
                changes={"owner": "Changed Owner"},
            ),
        )
        refreshed = runtime.approval(proposal.id)
        assert refreshed.before["version"] == 2
        assert refreshed.after is None
        assert refreshed.review_context["version_match"] is False
        assert "warning" in refreshed.review_context

        request = decision(proposal, suffix="database-version-conflict")
        failed = await runtime.decide(proposal.id, request)
        assert failed.pending_approval is None
        assert failed.run.status is RunStatus.FAILED
        assert failed.run.error_code == "record_version_conflict"
        receipt = next(item for item in failed.receipts if item.proposal_id == proposal.id)
        assert receipt.status is ReceiptStatus.FAILED
        assert receipt.error_code == "record_version_conflict"
        assert runtime.repository.get_proposal(proposal.id).status is ProposalStatus.FAILED

        replayed = await runtime.decide(proposal.id, request)
        assert replayed.run.status is RunStatus.FAILED
        assert [item.id for item in replayed.receipts] == [item.id for item in failed.receipts]


async def test_reset_excludes_new_run_until_checkpoint_and_domain_reset(
    settings, monkeypatch
) -> None:
    async with open_runtime(settings) as runtime:
        old = await runtime.start("Prepare the old workflow")
        old_config = runtime.config(old.run.id)
        entered_delete = asyncio.Event()
        release_delete = asyncio.Event()
        original_delete = runtime.checkpointer.adelete_thread

        async def paused_delete(thread_id: str) -> None:
            entered_delete.set()
            await release_delete.wait()
            await original_delete(thread_id)

        monkeypatch.setattr(runtime.checkpointer, "adelete_thread", paused_delete)
        reset_task = asyncio.create_task(runtime.reset_demo())
        await entered_delete.wait()
        start_task = asyncio.create_task(runtime.start("Prepare the new workflow"))
        await asyncio.sleep(0)
        assert not start_task.done()

        release_delete.set()
        await reset_task
        new = await start_task
        assert not (await runtime.graph.aget_state(old_config)).values
        assert (await runtime.graph.aget_state(runtime.config(new.run.id))).values
        assert runtime.repository.get_run(new.run.id).id == new.run.id
        assert runtime.list_runs()[1] == 1
