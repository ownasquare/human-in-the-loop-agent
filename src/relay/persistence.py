"""SQLite domain persistence, idempotency, and tamper-evident audit events."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from relay.models import (
    Action,
    ActionProposal,
    ApprovalDecision,
    ApprovalDecisionRequest,
    AuditActor,
    AuditEvent,
    DecisionType,
    ExecutionReceipt,
    Mode,
    PlannedStep,
    ProposalStatus,
    ReceiptStatus,
    RiskLevel,
    RunRecord,
    RunStatus,
    StepStatus,
    action_payload_hash,
    canonical_action_json,
    parse_action,
    utc_now,
)
from relay.redaction import redact


class RepositoryError(RuntimeError):
    """Base repository failure."""


class NotFoundError(RepositoryError):
    """Requested record does not exist."""


class ConflictError(RepositoryError):
    """State changed since the caller's last read."""


class StaleApprovalError(ConflictError):
    """Approval does not bind to the current proposal version and payload."""


class ReadOnlyQueryError(ValueError):
    """Database read tool received unsafe SQL."""


def _iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class RelayRepository:
    """Small transactional repository safe to reopen across runtime lifetimes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._initialize()
        with suppress(OSError):
            self.path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    instruction TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_index INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    error_code TEXT,
                    error_summary TEXT,
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS planned_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    receipt_id TEXT,
                    UNIQUE(run_id, position)
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL REFERENCES planned_steps(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    expected_effect TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    interrupt_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(step_id, version)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
                    decision TEXT NOT NULL,
                    expected_version INTEGER NOT NULL,
                    expected_payload_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    revised_action_json TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS execution_receipts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    proposal_id TEXT REFERENCES proposals(id) ON DELETE SET NULL,
                    step_id TEXT NOT NULL REFERENCES planned_steps(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_id TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    proposal_id TEXT REFERENCES proposals(id) ON DELETE SET NULL,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_customers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_action TEXT,
                    owner TEXT NOT NULL,
                    renewal_note TEXT,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_customer_updates (
                    idempotency_key TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_calendar_events (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL,
                    description TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    attendees_json TEXT NOT NULL,
                    send_updates TEXT NOT NULL DEFAULT 'none',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_email_outbox (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    recipients_json TEXT NOT NULL,
                    cc_json TEXT NOT NULL,
                    sender TEXT NOT NULL DEFAULT 'relay@example.invalid',
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_purchase_orders (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    vendor TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            calendar_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(demo_calendar_events)")
            }
            if "send_updates" not in calendar_columns:
                connection.execute(
                    "ALTER TABLE demo_calendar_events "
                    "ADD COLUMN send_updates TEXT NOT NULL DEFAULT 'none'"
                )
            email_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(demo_email_outbox)")
            }
            if "sender" not in email_columns:
                connection.execute(
                    "ALTER TABLE demo_email_outbox "
                    "ADD COLUMN sender TEXT NOT NULL DEFAULT 'relay@example.invalid'"
                )
        self.seed_demo()

    def close(self) -> None:
        """Compatibility hook; repository connections are method-scoped."""

    def seed_demo(self) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO demo_customers
                    (id, name, email, status, next_action, owner, renewal_note, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "CUST-001",
                    "Acme Industries",
                    "owner@acme.example",
                    "renewal_due",
                    None,
                    "Jordan Lee",
                    "Renewal review due this month.",
                    1,
                ),
            )

    def reset_demo(self) -> None:
        with self._transaction() as connection:
            for table in (
                "audit_events",
                "execution_receipts",
                "decisions",
                "proposals",
                "planned_steps",
                "runs",
                "demo_customer_updates",
                "demo_calendar_events",
                "demo_email_outbox",
                "demo_purchase_orders",
                "demo_customers",
            ):
                connection.execute(f"DELETE FROM {table}")  # noqa: S608  # nosec B608
        self.seed_demo()

    def create_run(
        self,
        *,
        instruction: str,
        mode: Mode,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        with self._transaction() as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing:
                    if existing["instruction"] != instruction or existing["mode"] != mode.value:
                        raise ConflictError(
                            "Run idempotency key was reused with a different request."
                        )
                    return self._run(existing)
            identifier = run_id or f"run_{uuid.uuid4().hex}"
            now = _iso()
            connection.execute(
                """INSERT INTO runs
                (id, thread_id, instruction, mode, status, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    identifier,
                    identifier,
                    instruction,
                    mode.value,
                    RunStatus.QUEUED.value,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            self._append_audit_conn(
                connection,
                run_id=identifier,
                actor=AuditActor.OPERATOR,
                event_type="run.created",
                detail={"instruction": instruction, "mode": mode.value},
            )
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (identifier,)).fetchone()
            if row is None:  # pragma: no cover - transaction invariant
                raise RepositoryError("run insert did not read back")
            return self._run(row)

    def get_run(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Run not found: {run_id}")
        return self._run(row)

    def list_runs(
        self, *, limit: int = 50, offset: int = 0, status: RunStatus | None = None
    ) -> tuple[list[RunRecord], int]:
        self.expire_due_proposals()
        where = " WHERE status = ?" if status else ""
        parameters: list[Any] = [status.value] if status else []
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM runs{where}",  # noqa: S608  # nosec B608
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM runs{where} "  # noqa: S608  # nosec
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        return [self._run(row) for row in rows], total

    def list_run_thread_ids(self) -> list[str]:
        """Return every checkpoint thread without pagination truncation."""
        with self._connect() as connection:
            rows = connection.execute("SELECT thread_id FROM runs ORDER BY created_at").fetchall()
        return [str(row["thread_id"]) for row in rows]

    def save_plan(self, run_id: str, actions: list[Action]) -> list[PlannedStep]:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM planned_steps WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
            if existing:
                return [self._step(row) for row in existing]
            for position, action in enumerate(actions):
                step_id = f"step_{uuid.uuid4().hex}"
                connection.execute(
                    """INSERT INTO planned_steps
                    (id, run_id, position, tool_name, action_json, status)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        step_id,
                        run_id,
                        position,
                        action.tool_name,
                        canonical_action_json(action),
                        StepStatus.PENDING.value,
                    ),
                )
            now = _iso()
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (RunStatus.RUNNING.value, now, run_id),
            )
            self._append_audit_conn(
                connection,
                run_id=run_id,
                actor=AuditActor.AGENT,
                event_type="plan.created",
                detail={"step_count": len(actions), "tools": [item.tool_name for item in actions]},
            )
            rows = connection.execute(
                "SELECT * FROM planned_steps WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
        return [self._step(row) for row in rows]

    def list_steps(self, run_id: str) -> list[PlannedStep]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM planned_steps WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
        return [self._step(row) for row in rows]

    def get_step(self, step_id: str) -> PlannedStep:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM planned_steps WHERE id = ?", (step_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Step not found: {step_id}")
        return self._step(row)

    def get_step_at(self, run_id: str, position: int) -> PlannedStep:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM planned_steps WHERE run_id = ? AND position = ?",
                (run_id, position),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Step not found at position {position}")
        return self._step(row)

    def update_step(
        self,
        step_id: str,
        *,
        status: StepStatus,
        receipt_id: str | None = None,
        action: Action | None = None,
    ) -> PlannedStep:
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM planned_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"Step not found: {step_id}")
            action_json = canonical_action_json(action) if action else current["action_json"]
            tool_name = action.tool_name if action else current["tool_name"]
            connection.execute(
                """UPDATE planned_steps
                SET status = ?, receipt_id = COALESCE(?, receipt_id), action_json = ?, tool_name = ?
                WHERE id = ?""",
                (status.value, receipt_id, action_json, tool_name, step_id),
            )
            row = connection.execute(
                "SELECT * FROM planned_steps WHERE id = ?", (step_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RepositoryError("step update did not read back")
        return self._step(row)

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        current_index: int | None = None,
        summary: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> RunRecord:
        terminal = status in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_REJECTIONS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Run not found: {run_id}")
            now = _iso()
            connection.execute(
                """UPDATE runs SET status = ?, current_index = COALESCE(?, current_index),
                summary = COALESCE(?, summary), error_code = ?, error_summary = ?,
                updated_at = ?, completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE id = ?""",
                (
                    status.value,
                    current_index,
                    summary,
                    error_code,
                    error_summary,
                    now,
                    1 if terminal else 0,
                    now,
                    run_id,
                ),
            )
            self._append_audit_conn(
                connection,
                run_id=run_id,
                actor=AuditActor.SYSTEM,
                event_type="run.status_changed",
                detail={"status": status.value, "current_index": current_index},
            )
            updated = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if updated is None:  # pragma: no cover
            raise RepositoryError("run update did not read back")
        return self._run(updated)

    def create_proposal(
        self,
        *,
        run_id: str,
        step_id: str,
        action: Action,
        risk_level: RiskLevel,
        title: str,
        reason: str,
        expected_effect: str,
        ttl_minutes: int = 60,
    ) -> ActionProposal:
        canonical = canonical_action_json(action)
        payload_hash = action_payload_hash(action)
        with self._transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM proposals
                WHERE step_id = ? AND status = ? ORDER BY version DESC LIMIT 1""",
                (step_id, ProposalStatus.PENDING.value),
            ).fetchone()
            if existing and existing["payload_hash"] == payload_hash:
                return self._proposal(existing)
            version_row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM proposals WHERE step_id = ?", (step_id,)
            ).fetchone()
            version = int(version_row[0])
            now = utc_now()
            proposal_id = f"proposal_{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO proposals
                (id, run_id, step_id, tool_name, risk_level, title, reason, expected_effect,
                 action_json, payload_hash, version, status, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal_id,
                    run_id,
                    step_id,
                    action.tool_name,
                    risk_level.value,
                    title,
                    reason,
                    expected_effect,
                    canonical,
                    payload_hash,
                    version,
                    ProposalStatus.PENDING.value,
                    _iso(now),
                    _iso(now),
                    _iso(now + timedelta(minutes=ttl_minutes)),
                ),
            )
            connection.execute(
                "UPDATE planned_steps SET status = ? WHERE id = ?",
                (StepStatus.AWAITING_APPROVAL.value, step_id),
            )
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (RunStatus.AWAITING_APPROVAL.value, _iso(now), run_id),
            )
            self._append_audit_conn(
                connection,
                run_id=run_id,
                proposal_id=proposal_id,
                actor=AuditActor.AGENT,
                event_type="approval.requested",
                detail={
                    "tool_name": action.tool_name,
                    "risk_level": risk_level.value,
                    "version": version,
                    "payload_hash": payload_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RepositoryError("proposal insert did not read back")
        return self._proposal(row)

    def bind_interrupt(self, proposal_id: str, interrupt_id: str) -> ActionProposal:
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"Proposal not found: {proposal_id}")
            if current["status"] != ProposalStatus.PENDING.value:
                raise ConflictError("Only a pending proposal can bind an interrupt.")
            if current["interrupt_id"] not in {None, interrupt_id}:
                raise ConflictError("Proposal is already bound to another interrupt.")
            connection.execute(
                "UPDATE proposals SET interrupt_id = ?, updated_at = ? WHERE id = ?",
                (interrupt_id, _iso(), proposal_id),
            )
            row = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - transaction invariant
            raise RepositoryError("interrupt binding did not read back")
        return self._proposal(row)

    def get_proposal(self, proposal_id: str) -> ActionProposal:
        self.expire_due_proposals(proposal_id=proposal_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Proposal not found: {proposal_id}")
        return self._proposal(row)

    def pending_proposal(self, run_id: str) -> ActionProposal | None:
        self.expire_due_proposals(run_id=run_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM proposals WHERE run_id = ? AND status = ?
                ORDER BY created_at DESC LIMIT 1""",
                (run_id, ProposalStatus.PENDING.value),
            ).fetchone()
        return self._proposal(row) if row else None

    def list_proposals(
        self, *, status: ProposalStatus | None = None, run_id: str | None = None
    ) -> list[ActionProposal]:
        self.expire_due_proposals(run_id=run_id)
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM proposals{where} "  # noqa: S608  # nosec
                "ORDER BY created_at ASC",
                params,
            ).fetchall()
        return [self._proposal(row) for row in rows]

    def record_decision(
        self, proposal_id: str, request: ApprovalDecisionRequest
    ) -> ApprovalDecision:
        self.expire_due_proposals(proposal_id=proposal_id)
        with self._transaction() as connection:
            duplicate = connection.execute(
                "SELECT * FROM decisions WHERE idempotency_key = ?", (request.idempotency_key,)
            ).fetchone()
            if duplicate:
                decision = self._decision(duplicate)
                self.assert_decision_request_matches(decision, proposal_id, request)
                return decision
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise NotFoundError(f"Proposal not found: {proposal_id}")
            if proposal["status"] == ProposalStatus.EXPIRED.value:
                raise ConflictError("Proposal has expired.")
            if proposal["status"] != ProposalStatus.PENDING.value:
                raise ConflictError("Proposal has already been decided.")
            if (
                proposal["version"] != request.expected_version
                or proposal["payload_hash"] != request.expected_payload_hash
            ):
                raise StaleApprovalError("Approval payload or version is stale.")
            revised_json = None
            if request.revised_action is not None:
                revised = parse_action(request.revised_action)
                if revised.tool_name != proposal["tool_name"]:
                    raise ConflictError("A revision cannot change the tool type.")
                revised_json = canonical_action_json(revised)
            status = {
                DecisionType.APPROVE: ProposalStatus.APPROVED,
                DecisionType.REJECT: ProposalStatus.REJECTED,
                DecisionType.REVISE: ProposalStatus.SUPERSEDED,
            }[request.decision]
            decision_id = f"decision_{uuid.uuid4().hex}"
            now = _iso()
            connection.execute(
                """INSERT INTO decisions
                (id, proposal_id, decision, expected_version, expected_payload_hash, reason,
                 revised_action_json, idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    proposal_id,
                    request.decision.value,
                    request.expected_version,
                    request.expected_payload_hash,
                    request.reason,
                    revised_json,
                    request.idempotency_key,
                    now,
                ),
            )
            connection.execute(
                "UPDATE proposals SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, proposal_id),
            )
            self._append_audit_conn(
                connection,
                run_id=proposal["run_id"],
                proposal_id=proposal_id,
                actor=AuditActor.OPERATOR,
                event_type=f"approval.{request.decision.value}",
                detail={
                    "version": request.expected_version,
                    "payload_hash": request.expected_payload_hash,
                    "reason": request.reason,
                },
            )
            row = connection.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RepositoryError("decision insert did not read back")
        return self._decision(row)

    def mark_decision_consumed(self, decision_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE decisions SET consumed_at = COALESCE(consumed_at, ?) WHERE id = ?",
                (_iso(), decision_id),
            )

    def get_decision_by_idempotency(self, idempotency_key: str) -> ApprovalDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return self._decision(row) if row else None

    def list_unconsumed_decisions(self, run_id: str) -> list[ApprovalDecision]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT decisions.* FROM decisions
                JOIN proposals ON proposals.id = decisions.proposal_id
                WHERE proposals.run_id = ? AND decisions.consumed_at IS NULL
                ORDER BY decisions.created_at""",
                (run_id,),
            ).fetchall()
        return [self._decision(row) for row in rows]

    def expire_due_proposals(
        self,
        *,
        run_id: str | None = None,
        proposal_id: str | None = None,
    ) -> int:
        clauses = ["status = ?", "expires_at <= ?"]
        parameters: list[Any] = [ProposalStatus.PENDING.value, _iso()]
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if proposal_id is not None:
            clauses.append("id = ?")
            parameters.append(proposal_id)
        where = " AND ".join(clauses)
        terminal_statuses = {
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_REJECTIONS.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM proposals WHERE {where}",  # noqa: S608  # nosec B608
                parameters,
            ).fetchall()
            for row in rows:
                now = _iso()
                connection.execute(
                    "UPDATE proposals SET status = ?, updated_at = ? WHERE id = ?",
                    (ProposalStatus.EXPIRED.value, now, row["id"]),
                )
                connection.execute(
                    "UPDATE planned_steps SET status = ? WHERE id = ?",
                    (StepStatus.FAILED.value, row["step_id"]),
                )
                run = connection.execute(
                    "SELECT status FROM runs WHERE id = ?", (row["run_id"],)
                ).fetchone()
                if run is not None and run["status"] not in terminal_statuses:
                    connection.execute(
                        """UPDATE runs SET status = ?, error_code = ?, error_summary = ?,
                        updated_at = ?, completed_at = ? WHERE id = ?""",
                        (
                            RunStatus.FAILED.value,
                            "proposal_expired",
                            "An approval expired before an operator decision.",
                            now,
                            now,
                            row["run_id"],
                        ),
                    )
                    self._append_audit_conn(
                        connection,
                        run_id=row["run_id"],
                        proposal_id=row["id"],
                        actor=AuditActor.SYSTEM,
                        event_type="approval.expired",
                        detail={
                            "error_code": "proposal_expired",
                            "version": row["version"],
                            "payload_hash": row["payload_hash"],
                        },
                    )
        return len(rows)

    @staticmethod
    def assert_decision_request_matches(
        existing: ApprovalDecision,
        proposal_id: str,
        request: ApprovalDecisionRequest,
    ) -> None:
        existing_revision = (
            canonical_action_json(existing.revised_action)
            if existing.revised_action is not None
            else None
        )
        requested_revision = (
            canonical_action_json(request.revised_action)
            if request.revised_action is not None
            else None
        )
        same_request = (
            existing.proposal_id == proposal_id
            and existing.decision is request.decision
            and existing.expected_version == request.expected_version
            and existing.expected_payload_hash == request.expected_payload_hash
            and existing.reason == request.reason
            and existing_revision == requested_revision
        )
        if not same_request:
            raise ConflictError("Decision idempotency key was reused with a different request.")

    def cancel_run(self, run_id: str) -> RunRecord:
        terminal = {
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_REJECTIONS.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Run not found: {run_id}")
            if row["status"] in terminal:
                raise ConflictError("A terminal run cannot be cancelled.")
            now = _iso()
            connection.execute(
                "UPDATE proposals SET status = ?, updated_at = ? WHERE run_id = ? AND status = ?",
                (
                    ProposalStatus.CANCELLED.value,
                    now,
                    run_id,
                    ProposalStatus.PENDING.value,
                ),
            )
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (RunStatus.CANCELLED.value, now, now, run_id),
            )
            self._append_audit_conn(
                connection,
                run_id=run_id,
                actor=AuditActor.OPERATOR,
                event_type="run.cancelled",
                detail={"pending_approvals_invalidated": True},
            )
            updated = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if updated is None:  # pragma: no cover
            raise RepositoryError("cancelled run did not read back")
        return self._run(updated)

    def begin_approved_execution(
        self,
        proposal_id: str,
        action: Action,
        *,
        run_id: str,
        step: PlannedStep,
    ) -> ActionProposal:
        with self._transaction() as connection:
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise NotFoundError(f"Proposal not found: {proposal_id}")
            stored_step = connection.execute(
                "SELECT * FROM planned_steps WHERE id = ?", (step.id,)
            ).fetchone()
            run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if stored_step is None or run is None:
                raise ConflictError("Approved execution no longer has an active run and step.")
            request_hash = action_payload_hash(action)
            stored_step_hash = action_payload_hash(json.loads(stored_step["action_json"]))
            if (
                proposal["run_id"] != run_id
                or proposal["step_id"] != step.id
                or stored_step["run_id"] != run_id
                or step.run_id != run_id
            ):
                raise ConflictError("Approval is not bound to this run and step.")
            if (
                proposal["tool_name"] != action.tool_name
                or stored_step["tool_name"] != action.tool_name
                or step.action.tool_name != action.tool_name
            ):
                raise ConflictError("Approval tool identity differs from the planned step.")
            if (
                proposal["payload_hash"] != request_hash
                or stored_step_hash != request_hash
                or action_payload_hash(step.action) != request_hash
            ):
                raise StaleApprovalError("Execution payload differs from approved payload.")
            if run["status"] not in {
                RunStatus.RUNNING.value,
                RunStatus.AWAITING_APPROVAL.value,
            }:
                raise ConflictError("Approved execution requires an active workflow.")
            if proposal["status"] == ProposalStatus.EXECUTING.value:
                return self._proposal(proposal)
            if proposal["status"] != ProposalStatus.APPROVED.value:
                raise ConflictError("Exact persisted approval is required before execution.")
            connection.execute(
                "UPDATE proposals SET status = ?, updated_at = ? WHERE id = ?",
                (ProposalStatus.EXECUTING.value, _iso(), proposal_id),
            )
            row = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RepositoryError("execution transition did not read back")
        return self._proposal(row)

    def record_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM execution_receipts WHERE idempotency_key = ?",
                (receipt.idempotency_key,),
            ).fetchone()
            if existing:
                stored = self._receipt(existing)
                if not self._same_receipt_identity(stored, receipt):
                    raise ConflictError(
                        "Receipt idempotency key was reused with a different execution."
                    )
                return stored
            step = connection.execute(
                "SELECT * FROM planned_steps WHERE id = ?", (receipt.step_id,)
            ).fetchone()
            run = connection.execute(
                "SELECT id FROM runs WHERE id = ?", (receipt.run_id,)
            ).fetchone()
            if (
                run is None
                or step is None
                or step["run_id"] != receipt.run_id
                or step["tool_name"] != receipt.tool_name
                or action_payload_hash(json.loads(step["action_json"])) != receipt.request_hash
            ):
                raise ConflictError("Receipt identity differs from the persisted run and step.")
            if receipt.proposal_id:
                proposal = connection.execute(
                    "SELECT * FROM proposals WHERE id = ?", (receipt.proposal_id,)
                ).fetchone()
                if (
                    proposal is None
                    or proposal["run_id"] != receipt.run_id
                    or proposal["step_id"] != receipt.step_id
                    or proposal["tool_name"] != receipt.tool_name
                    or proposal["payload_hash"] != receipt.request_hash
                    or proposal["status"] != ProposalStatus.EXECUTING.value
                ):
                    raise ConflictError("Receipt identity differs from the executing approval.")
            connection.execute(
                """INSERT INTO execution_receipts
                (id, run_id, proposal_id, step_id, tool_name, status, provider, provider_id,
                 idempotency_key, request_hash, response_json, error_code, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt.id,
                    receipt.run_id,
                    receipt.proposal_id,
                    receipt.step_id,
                    receipt.tool_name,
                    receipt.status.value,
                    receipt.provider,
                    receipt.provider_id,
                    receipt.idempotency_key,
                    receipt.request_hash,
                    _json(redact(receipt.response)),
                    receipt.error_code,
                    _iso(receipt.created_at),
                    _iso(receipt.completed_at) if receipt.completed_at else None,
                ),
            )
            step_status = (
                StepStatus.SUCCEEDED
                if receipt.status is ReceiptStatus.SUCCEEDED
                else StepStatus.FAILED
            )
            connection.execute(
                "UPDATE planned_steps SET status = ?, receipt_id = ? WHERE id = ?",
                (step_status.value, receipt.id, receipt.step_id),
            )
            if receipt.proposal_id:
                proposal_status = {
                    ReceiptStatus.SUCCEEDED: ProposalStatus.SUCCEEDED,
                    ReceiptStatus.FAILED: ProposalStatus.FAILED,
                    ReceiptStatus.OUTCOME_UNKNOWN: ProposalStatus.OUTCOME_UNKNOWN,
                }[receipt.status]
                connection.execute(
                    "UPDATE proposals SET status = ?, updated_at = ? WHERE id = ?",
                    (proposal_status.value, _iso(), receipt.proposal_id),
                )
            self._append_audit_conn(
                connection,
                run_id=receipt.run_id,
                proposal_id=receipt.proposal_id,
                actor=AuditActor.TOOL,
                event_type=f"tool.{receipt.status.value}",
                detail={
                    "tool_name": receipt.tool_name,
                    "provider": receipt.provider,
                    "provider_id": receipt.provider_id,
                    "receipt_id": receipt.id,
                    "error_code": receipt.error_code,
                },
            )
            row = connection.execute(
                "SELECT * FROM execution_receipts WHERE id = ?", (receipt.id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RepositoryError("receipt insert did not read back")
        return self._receipt(row)

    def list_receipts(self, run_id: str) -> list[ExecutionReceipt]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_receipts WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [self._receipt(row) for row in rows]

    def get_receipt_by_idempotency(self, idempotency_key: str) -> ExecutionReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._receipt(row) if row else None

    def get_receipt_by_proposal(self, proposal_id: str) -> ExecutionReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM execution_receipts
                WHERE proposal_id = ? ORDER BY created_at LIMIT 1""",
                (proposal_id,),
            ).fetchone()
        return self._receipt(row) if row else None

    @staticmethod
    def _same_receipt_identity(existing: ExecutionReceipt, requested: ExecutionReceipt) -> bool:
        return (
            existing.run_id == requested.run_id
            and existing.proposal_id == requested.proposal_id
            and existing.step_id == requested.step_id
            and existing.tool_name == requested.tool_name
            and existing.idempotency_key == requested.idempotency_key
            and existing.request_hash == requested.request_hash
        )

    def append_audit(
        self,
        *,
        run_id: str,
        actor: AuditActor,
        event_type: str,
        detail: Mapping[str, Any],
        proposal_id: str | None = None,
    ) -> AuditEvent:
        with self._transaction() as connection:
            return self._append_audit_conn(
                connection,
                run_id=run_id,
                actor=actor,
                event_type=event_type,
                detail=detail,
                proposal_id=proposal_id,
            )

    def _append_audit_conn(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        actor: AuditActor,
        event_type: str,
        detail: Mapping[str, Any],
        proposal_id: str | None = None,
    ) -> AuditEvent:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        event_id = f"event_{uuid.uuid4().hex}"
        created_at = _iso()
        redacted_detail = redact(dict(detail))
        detail_json = _json(redacted_detail)
        material = _json(
            {
                "event_id": event_id,
                "run_id": run_id,
                "proposal_id": proposal_id,
                "actor": actor.value,
                "event_type": event_type,
                "detail": redacted_detail,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        cursor = connection.execute(
            """INSERT INTO audit_events
            (event_id, run_id, proposal_id, actor, event_type, detail_json, previous_hash,
             event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                run_id,
                proposal_id,
                actor.value,
                event_type,
                detail_json,
                previous_hash,
                event_hash,
                created_at,
            ),
        )
        return AuditEvent(
            sequence=int(cursor.lastrowid or 0),
            event_id=event_id,
            run_id=run_id,
            proposal_id=proposal_id,
            actor=actor,
            event_type=event_type,
            detail=redacted_detail,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_audit_events(
        self,
        *,
        run_id: str | None = None,
        after_sequence: int = 0,
        limit: int | None = 200,
    ) -> tuple[list[AuditEvent], int]:
        with self._connect() as connection:
            if run_id is None:
                params: list[Any] = [after_sequence]
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM audit_events WHERE sequence > ?",
                        params,
                    ).fetchone()[0]
                )
                if limit is None:
                    rows = connection.execute(
                        "SELECT * FROM audit_events WHERE sequence > ? ORDER BY sequence ASC",
                        params,
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM audit_events WHERE sequence > ? "
                        "ORDER BY sequence ASC LIMIT ?",
                        [*params, limit],
                    ).fetchall()
            else:
                params = [after_sequence, run_id]
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM audit_events WHERE sequence > ? AND run_id = ?",
                        params,
                    ).fetchone()[0]
                )
                if limit is None:
                    rows = connection.execute(
                        "SELECT * FROM audit_events "
                        "WHERE sequence > ? AND run_id = ? ORDER BY sequence ASC",
                        params,
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM audit_events "
                        "WHERE sequence > ? AND run_id = ? "
                        "ORDER BY sequence ASC LIMIT ?",
                        [*params, limit],
                    ).fetchall()
        return [self._audit(row) for row in rows], total

    def verify_audit_chain(self, run_id: str) -> bool:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE run_id = ? ORDER BY sequence ASC",
                (run_id,),
            ).fetchall()
        events = [self._audit(row) for row in rows]
        previous = "0" * 64
        for event in events:
            material = _json(
                {
                    "event_id": event.event_id,
                    "run_id": event.run_id,
                    "proposal_id": event.proposal_id,
                    "actor": event.actor.value,
                    "event_type": event.event_type,
                    "detail": event.detail,
                    "previous_hash": previous,
                    "created_at": event.created_at.isoformat(),
                }
            )
            if event.previous_hash != previous:
                return False
            if hashlib.sha256(material.encode("utf-8")).hexdigest() != event.event_hash:
                return False
            previous = event.event_hash
        return True

    _FORBIDDEN_SQL = re.compile(
        r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|PRAGMA|ATTACH|DETACH|VACUUM|TRIGGER)\b",
        re.IGNORECASE,
    )

    def run_read_query(self, query: str) -> list[dict[str, Any]]:
        normalized = query.strip()
        if normalized.endswith(";"):
            normalized = normalized[:-1].strip()
        if (
            not re.match(r"^SELECT\b", normalized, flags=re.IGNORECASE)
            or ";" in normalized
            or "--" in normalized
            or "/*" in normalized
            or self._FORBIDDEN_SQL.search(normalized)
            or not re.search(r"\bFROM\s+(?:demo_)?customers\b", normalized, re.IGNORECASE)
        ):
            raise ReadOnlyQueryError("Only one read-only SELECT from customers is allowed.")
        safe_query = re.sub(
            r"\bFROM\s+customers\b", "FROM demo_customers", normalized, flags=re.IGNORECASE
        )
        with self._connect() as connection:
            allowed_columns = {
                "id",
                "name",
                "email",
                "status",
                "next_action",
                "owner",
                "renewal_note",
                "version",
            }

            def authorize(
                action_code: int,
                parameter_one: str | None,
                parameter_two: str | None,
                database_name: str | None,
                trigger_name: str | None,
            ) -> int:
                if action_code == sqlite3.SQLITE_READ and (
                    parameter_one != "demo_customers" or parameter_two not in allowed_columns
                ):
                    return sqlite3.SQLITE_DENY
                if action_code == sqlite3.SQLITE_FUNCTION:
                    return sqlite3.SQLITE_DENY
                if action_code in {
                    sqlite3.SQLITE_INSERT,
                    sqlite3.SQLITE_UPDATE,
                    sqlite3.SQLITE_DELETE,
                    sqlite3.SQLITE_ATTACH,
                    sqlite3.SQLITE_DETACH,
                    sqlite3.SQLITE_ALTER_TABLE,
                    sqlite3.SQLITE_DROP_TABLE,
                    sqlite3.SQLITE_DROP_TRIGGER,
                    sqlite3.SQLITE_PRAGMA,
                }:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            progress_calls = 0

            def enforce_budget() -> int:
                nonlocal progress_calls
                progress_calls += 1
                return 1 if progress_calls > 100 else 0

            connection.set_progress_handler(enforce_budget, 1_000)
            try:
                rows = connection.execute(
                    f"SELECT * FROM ({safe_query}) AS bounded_result LIMIT 50"  # noqa: S608  # nosec
                ).fetchall()
            except sqlite3.Error as exc:
                raise ReadOnlyQueryError("The allowlisted read query is invalid.") from exc
        return [dict(row) for row in rows]

    def demo_create_calendar_event(
        self, *, idempotency_key: str, action: Action
    ) -> tuple[str, dict[str, Any]]:
        data = action.model_dump(mode="json")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM demo_calendar_events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                return str(existing["id"]), dict(existing)
            event_id = f"demo_event_{uuid.uuid4().hex[:12]}"
            connection.execute(
                """INSERT INTO demo_calendar_events
                (id, idempotency_key, summary, description, start_at, end_at, attendees_json,
                 send_updates, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    idempotency_key,
                    data["summary"],
                    data.get("description", ""),
                    data["start_at"],
                    data["end_at"],
                    _json(data.get("attendees", [])),
                    data.get("send_updates", "none"),
                    _iso(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM demo_calendar_events WHERE id = ?", (event_id,)
            ).fetchone()
        return event_id, dict(row) if row else {"id": event_id}

    def demo_send_email(
        self, *, idempotency_key: str, action: Action
    ) -> tuple[str, dict[str, Any]]:
        data = action.model_dump(mode="json")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM demo_email_outbox WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                return str(existing["id"]), dict(existing)
            message_id = f"demo_email_{uuid.uuid4().hex[:12]}"
            connection.execute(
                """INSERT INTO demo_email_outbox
                (id, idempotency_key, recipients_json, cc_json, sender, subject, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    idempotency_key,
                    _json(data["to"]),
                    _json(data.get("cc", [])),
                    data.get("sender", "relay@example.invalid"),
                    data["subject"],
                    data["body"],
                    _iso(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM demo_email_outbox WHERE id = ?", (message_id,)
            ).fetchone()
        return message_id, dict(row) if row else {"id": message_id}

    def demo_update_customer(
        self, *, idempotency_key: str, action: Action
    ) -> tuple[str, dict[str, Any]]:
        data = action.model_dump(mode="json")
        request_hash = action_payload_hash(action)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM demo_customer_updates WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if (
                    existing["record_id"] != data["record_id"]
                    or existing["request_hash"] != request_hash
                ):
                    raise ConflictError(
                        "Customer update idempotency key was reused with a different request."
                    )
                response = json.loads(existing["response_json"])
                return str(response["record_id"]), response
            row = connection.execute(
                "SELECT * FROM demo_customers WHERE id = ?", (data["record_id"],)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Customer not found: {data['record_id']}")
            if int(row["version"]) != int(data["expected_version"]):
                raise ConflictError("Customer changed since the proposal was reviewed.")
            changes = data["changes"]
            allowed = {"status", "next_action", "owner", "renewal_note"}
            columns = [key for key in changes if key in allowed]
            if not columns:
                raise ConflictError("No supported customer fields were supplied.")
            assignments = ", ".join(f"{key} = ?" for key in columns)
            values = [changes[key] for key in columns]
            connection.execute(
                f"UPDATE demo_customers SET {assignments}, version = version + 1 "  # noqa: S608  # nosec
                "WHERE id = ?",
                [*values, data["record_id"]],
            )
            updated = connection.execute(
                "SELECT * FROM demo_customers WHERE id = ?", (data["record_id"],)
            ).fetchone()
            response = dict(updated) if updated else {"id": data["record_id"]}
            response["record_id"] = str(data["record_id"])
            connection.execute(
                """INSERT INTO demo_customer_updates
                (idempotency_key, record_id, request_hash, response_json, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    idempotency_key,
                    data["record_id"],
                    request_hash,
                    _json(response),
                    _iso(),
                ),
            )
        return str(data["record_id"]), response

    def demo_create_purchase_order(
        self, *, idempotency_key: str, action: Action
    ) -> tuple[str, dict[str, Any]]:
        data = action.model_dump(mode="json")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM demo_purchase_orders WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                return str(existing["id"]), dict(existing)
            purchase_id = f"demo_po_{uuid.uuid4().hex[:12]}"
            connection.execute(
                """INSERT INTO demo_purchase_orders
                (id, idempotency_key, vendor, amount, currency, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    purchase_id,
                    idempotency_key,
                    data["vendor"],
                    str(data["amount"]),
                    data["currency"],
                    data["description"],
                    _iso(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM demo_purchase_orders WHERE id = ?", (purchase_id,)
            ).fetchone()
        return purchase_id, dict(row) if row else {"id": purchase_id}

    def demo_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                "customers": int(
                    connection.execute("SELECT COUNT(*) FROM demo_customers").fetchone()[0]
                ),
                "calendar_events": int(
                    connection.execute("SELECT COUNT(*) FROM demo_calendar_events").fetchone()[0]
                ),
                "emails": int(
                    connection.execute("SELECT COUNT(*) FROM demo_email_outbox").fetchone()[0]
                ),
                "purchase_orders": int(
                    connection.execute("SELECT COUNT(*) FROM demo_purchase_orders").fetchone()[0]
                ),
            }

    def get_demo_customer(self, record_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM demo_customers WHERE id = ?", (record_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_outbox(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM demo_email_outbox ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            instruction=row["instruction"],
            mode=Mode(row["mode"]),
            status=RunStatus(row["status"]),
            current_index=int(row["current_index"]),
            summary=row["summary"],
            error_code=row["error_code"],
            error_summary=row["error_summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=_dt(row["completed_at"]),
        )

    @staticmethod
    def _step(row: sqlite3.Row) -> PlannedStep:
        return PlannedStep(
            id=row["id"],
            run_id=row["run_id"],
            position=int(row["position"]),
            action=parse_action(json.loads(row["action_json"])),
            status=StepStatus(row["status"]),
            receipt_id=row["receipt_id"],
        )

    @staticmethod
    def _proposal(row: sqlite3.Row) -> ActionProposal:
        return ActionProposal(
            id=row["id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            action=parse_action(json.loads(row["action_json"])),
            risk_level=RiskLevel(row["risk_level"]),
            title=row["title"],
            reason=row["reason"],
            expected_effect=row["expected_effect"],
            payload_hash=row["payload_hash"],
            version=int(row["version"]),
            status=ProposalStatus(row["status"]),
            interrupt_id=row["interrupt_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    @staticmethod
    def _decision(row: sqlite3.Row) -> ApprovalDecision:
        revised = json.loads(row["revised_action_json"]) if row["revised_action_json"] else None
        return ApprovalDecision(
            id=row["id"],
            proposal_id=row["proposal_id"],
            decision=DecisionType(row["decision"]),
            expected_version=int(row["expected_version"]),
            expected_payload_hash=row["expected_payload_hash"],
            reason=row["reason"],
            revised_action=revised,
            idempotency_key=row["idempotency_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            consumed_at=_dt(row["consumed_at"]),
        )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> ExecutionReceipt:
        return ExecutionReceipt(
            id=row["id"],
            run_id=row["run_id"],
            proposal_id=row["proposal_id"],
            step_id=row["step_id"],
            tool_name=row["tool_name"],
            status=ReceiptStatus(row["status"]),
            provider=row["provider"],
            provider_id=row["provider_id"],
            idempotency_key=row["idempotency_key"],
            request_hash=row["request_hash"],
            response=json.loads(row["response_json"]),
            error_code=row["error_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=_dt(row["completed_at"]),
        )

    @staticmethod
    def _audit(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            run_id=row["run_id"],
            proposal_id=row["proposal_id"],
            actor=AuditActor(row["actor"]),
            event_type=row["event_type"],
            detail=json.loads(row["detail_json"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
