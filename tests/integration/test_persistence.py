from __future__ import annotations

import hashlib
import json

import pytest

from relay.models import (
    ApprovalDecisionRequest,
    AuditActor,
    CalendarCreateAction,
    DatabaseUpdateAction,
    DecisionType,
    Mode,
    ProposalStatus,
    RiskLevel,
    RunStatus,
    StepStatus,
    utc_now,
)
from relay.persistence import (
    ConflictError,
    ReadOnlyQueryError,
    RelayRepository,
    StaleApprovalError,
)
from relay.policy import DEFAULT_POLICY
from relay.tools.base import AdapterResult
from relay.tools.registry import ToolBundle


def _proposal(repository: RelayRepository):
    run = repository.create_run(instruction="Schedule a review", mode=Mode.DEMO)
    action = CalendarCreateAction(
        summary="Review",
        start_at="2030-01-15T10:00:00Z",
        end_at="2030-01-15T10:30:00Z",
        attendees=["reviewer@example.com"],
    )
    step = repository.save_plan(run.id, [action])[0]
    return repository.create_proposal(
        run_id=run.id,
        step_id=step.id,
        action=action,
        risk_level=RiskLevel.HIGH,
        title="Create event",
        reason="Schedule the review",
        expected_effect="Adds one event",
    )


def test_pending_proposal_survives_reopen(settings) -> None:
    first = RelayRepository(settings.database_path)
    proposal = _proposal(first)
    first.close()
    second = RelayRepository(settings.database_path)
    assert second.get_proposal(proposal.id).payload_hash == proposal.payload_hash
    assert second.pending_proposal(proposal.run_id).id == proposal.id


def test_expired_proposal_fails_run_and_never_leaves_dead_end(
    repository: RelayRepository,
) -> None:
    run = repository.create_run(instruction="Schedule a review", mode=Mode.DEMO)
    action = CalendarCreateAction(
        summary="Review",
        start_at="2030-01-15T10:00:00Z",
        end_at="2030-01-15T10:30:00Z",
    )
    step = repository.save_plan(run.id, [action])[0]
    proposal = repository.create_proposal(
        run_id=run.id,
        step_id=step.id,
        action=action,
        risk_level=RiskLevel.HIGH,
        title="Create event",
        reason="Schedule the review",
        expected_effect="Adds one event",
        ttl_minutes=-1,
    )
    assert repository.pending_proposal(run.id) is None
    expired = repository.get_proposal(proposal.id)
    failed_run = repository.get_run(run.id)
    assert expired.status is ProposalStatus.EXPIRED
    assert failed_run.status is RunStatus.FAILED
    assert failed_run.error_code == "proposal_expired"
    assert repository.get_step(step.id).status is StepStatus.FAILED
    events, _ = repository.list_audit_events(run_id=run.id)
    assert events[-1].event_type == "approval.expired"


def test_stale_approval_is_rejected(repository: RelayRepository) -> None:
    proposal = _proposal(repository)
    with pytest.raises(StaleApprovalError):
        repository.record_decision(
            proposal.id,
            ApprovalDecisionRequest(
                decision=DecisionType.APPROVE,
                expected_version=proposal.version,
                expected_payload_hash="0" * 64,
                idempotency_key="stale-decision-key",
            ),
        )


def test_unconsumed_decision_idempotency_key_binds_entire_request(
    repository: RelayRepository,
) -> None:
    proposal = _proposal(repository)
    request = ApprovalDecisionRequest(
        decision=DecisionType.APPROVE,
        expected_version=proposal.version,
        expected_payload_hash=proposal.payload_hash,
        reason="first body",
        idempotency_key="exact-decision-key",
    )
    first = repository.record_decision(proposal.id, request)
    assert repository.record_decision(proposal.id, request).id == first.id
    with pytest.raises(ConflictError, match="different request"):
        repository.record_decision(
            proposal.id,
            request.model_copy(update={"reason": "changed body"}),
        )


def test_run_idempotency_key_binds_instruction_and_mode(
    repository: RelayRepository,
) -> None:
    first = repository.create_run(
        instruction="First instruction",
        mode=Mode.DEMO,
        idempotency_key="exact-run-key",
    )
    assert (
        repository.create_run(
            instruction="First instruction",
            mode=Mode.DEMO,
            idempotency_key="exact-run-key",
        ).id
        == first.id
    )
    with pytest.raises(ConflictError, match="different request"):
        repository.create_run(
            instruction="Different instruction",
            mode=Mode.DEMO,
            idempotency_key="exact-run-key",
        )


def test_read_query_denies_mutation_join_and_subquery(repository: RelayRepository) -> None:
    safe = repository.run_read_query("SELECT id, name, version FROM customers")
    assert safe[0]["id"] == "CUST-001"
    unsafe = "SELECT c.id FROM customers c JOIN demo_email_outbox e ON c.id = e.id"
    with pytest.raises(ReadOnlyQueryError):
        repository.run_read_query(unsafe)
    with pytest.raises(ReadOnlyQueryError):
        repository.run_read_query(
            "SELECT id, (SELECT subject FROM demo_email_outbox LIMIT 1) FROM customers"
        )
    with pytest.raises(ReadOnlyQueryError):
        repository.run_read_query("UPDATE customers SET status = 'lost'")
    with pytest.raises(ReadOnlyQueryError):
        repository.run_read_query("SELECT id, length(name) FROM customers")


def test_audit_chain_verifies_and_contains_no_raw_secret(repository: RelayRepository) -> None:
    proposal = _proposal(repository)
    repository.append_audit(
        run_id=proposal.run_id,
        actor=AuditActor.SYSTEM,
        event_type="test.redaction",
        detail={"api_key": "never-persist-this"},
    )
    events, _ = repository.list_audit_events(run_id=proposal.run_id)
    assert repository.verify_audit_chain(proposal.run_id)
    assert "never-persist-this" not in json.dumps([event.detail for event in events])


def test_audit_verification_checks_corrupt_tail_beyond_ten_thousand(
    repository: RelayRepository,
) -> None:
    run = repository.create_run(instruction="Audit every event", mode=Mode.DEMO)
    created_at = utc_now().isoformat()
    with repository._transaction() as connection:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run.id,),
        ).fetchone()["event_hash"]
        rows: list[tuple[str, str, None, str, str, str, str, str, str]] = []
        for index in range(10_000):
            event_id = f"bulk_event_{index}"
            material = json.dumps(
                {
                    "event_id": event_id,
                    "run_id": run.id,
                    "proposal_id": None,
                    "actor": AuditActor.SYSTEM.value,
                    "event_type": "bulk.test",
                    "detail": {},
                    "previous_hash": previous,
                    "created_at": created_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            valid_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            stored_hash = "f" * 64 if index == 9_999 else valid_hash
            rows.append(
                (
                    event_id,
                    run.id,
                    None,
                    AuditActor.SYSTEM.value,
                    "bulk.test",
                    "{}",
                    previous,
                    stored_hash,
                    created_at,
                )
            )
            previous = valid_hash
        connection.executemany(
            """INSERT INTO audit_events
            (event_id, run_id, proposal_id, actor, event_type, detail_json, previous_hash,
             event_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    assert repository.verify_audit_chain(run.id) is False


def test_demo_customer_update_has_atomic_idempotency_journal(
    repository: RelayRepository,
) -> None:
    action = DatabaseUpdateAction(
        record_id="CUST-001",
        expected_version=1,
        changes={"status": "renewal_contacted"},
    )
    first_id, first = repository.demo_update_customer(
        idempotency_key="customer-update-exact-key", action=action
    )
    second_id, second = repository.demo_update_customer(
        idempotency_key="customer-update-exact-key", action=action
    )
    assert first_id == second_id == "CUST-001"
    assert first == second
    assert repository.get_demo_customer("CUST-001")["version"] == 2
    with pytest.raises(ConflictError, match="different request"):
        repository.demo_update_customer(
            idempotency_key="customer-update-exact-key",
            action=action.model_copy(update={"changes": {"status": "different"}}),
        )


class _CountingCalendarAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, action, *, idempotency_key: str) -> AdapterResult:
        self.calls += 1
        return AdapterResult(
            provider="counting",
            provider_id="event_exact",
            data={"idempotency_key": idempotency_key},
        )


def _approve(repository: RelayRepository, proposal, *, key: str) -> None:
    repository.record_decision(
        proposal.id,
        ApprovalDecisionRequest(
            decision=DecisionType.APPROVE,
            expected_version=proposal.version,
            expected_payload_hash=proposal.payload_hash,
            idempotency_key=key,
        ),
    )


async def test_receipt_replay_precedes_proposal_transition(
    repository: RelayRepository, settings
) -> None:
    proposal = _proposal(repository)
    _approve(repository, proposal, key="approve-receipt-replay")
    adapter = _CountingCalendarAdapter()
    tools = ToolBundle(
        settings=settings,
        repository=repository,
        policy=DEFAULT_POLICY,
        adapters={"calendar_create": adapter},
    )
    step = repository.get_step(proposal.step_id)
    first = await tools.execute(run_id=proposal.run_id, step=step, proposal_id=proposal.id)
    replayed = await tools.execute(run_id=proposal.run_id, step=step, proposal_id=proposal.id)
    assert replayed.id == first.id
    assert adapter.calls == 1
    assert repository.get_proposal(proposal.id).status is ProposalStatus.SUCCEEDED


async def test_approval_cannot_authorize_another_run_or_step(
    repository: RelayRepository, settings
) -> None:
    proposal = _proposal(repository)
    _approve(repository, proposal, key="approve-no-substitution")
    adapter = _CountingCalendarAdapter()
    tools = ToolBundle(
        settings=settings,
        repository=repository,
        policy=DEFAULT_POLICY,
        adapters={"calendar_create": adapter},
    )

    other_run = repository.create_run(instruction="Other run", mode=Mode.DEMO)
    other_step = repository.save_plan(other_run.id, [proposal.action])[0]
    with pytest.raises(ConflictError, match="bound to this run and step"):
        await tools.execute(
            run_id=other_run.id,
            step=other_step,
            proposal_id=proposal.id,
        )

    same_run = repository.create_run(instruction="Two steps", mode=Mode.DEMO)
    first_step, substituted_step = repository.save_plan(
        same_run.id, [proposal.action, proposal.action]
    )
    same_run_proposal = repository.create_proposal(
        run_id=same_run.id,
        step_id=first_step.id,
        action=proposal.action,
        risk_level=RiskLevel.HIGH,
        title="Create event",
        reason="Schedule the review",
        expected_effect="Adds one event",
    )
    _approve(repository, same_run_proposal, key="approve-no-step-substitution")
    with pytest.raises(ConflictError, match="bound to this run and step"):
        await tools.execute(
            run_id=same_run.id,
            step=substituted_step,
            proposal_id=same_run_proposal.id,
        )
    assert adapter.calls == 0
