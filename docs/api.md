# API

Relay exposes a versioned JSON API under `/api/v1`. Interactive OpenAPI documentation is available
at `/docs` while the service is running.

The API returns domain records for runs, proposals, decisions, receipts, and audit events. It does
not accept serialized LangGraph objects. In particular, the browser cannot submit a LangGraph
`Command`; the runtime constructs the resume mapping from the current durable proposal and
checkpoint.

## Conventions

- Content type is `application/json`.
- IDs are opaque strings.
- Timestamps are UTC RFC 3339 values.
- Mutation models reject unknown fields.
- List endpoints return `{"items": [...], "total": N}`.
- Run lists use bounded `limit` and `offset` pagination.
- Connector mode is server-owned. A request may confirm but cannot switch the configured mode.
- Error responses use a stable machine-readable code:

```json
{
  "error": {
    "code": "stale_approval",
    "message": "The approval no longer matches the pending proposal."
  }
}
```

Clients should branch on `error.code`, not the human-readable message.

## Health and capabilities

### `GET /api/v1/health`

Liveness only. A green response does not prove database readiness or provider access.

```json
{
  "status": "ok",
  "service": "relay",
  "version": "0.1.0"
}
```

### `GET /api/v1/readiness`

Checks local application/checkpoint storage and reports non-secret connector readiness.

```json
{
  "status": "ready",
  "ready": true,
  "mode": "demo",
  "database": {"ready": true},
  "checkpoint": {"ready": true},
  "connectors": [
    {
      "name": "anthropic",
      "mode": "demo",
      "configured": true,
      "ready": true,
      "detail": "Deterministic demo adapter; no external request is made."
    }
  ]
}
```

In live mode the required planner, search, email, calendar, and database connectors must be ready for
overall readiness. Purchasing remains intentionally unavailable as a live connector. Readiness does
not send a message, create an event, or prove provider delivery/readback.

### `GET /api/v1/capabilities`

Returns active mode, registered tool policy, and feature flags.

```json
{
  "mode": "demo",
  "tools": [
    {
      "tool_name": "calendar_create",
      "risk_level": "high",
      "requires_approval": true,
      "allowed_decisions": ["approve", "reject", "revise"],
      "description": "Create an external calendar event"
    }
  ],
  "features": {
    "persistent_checkpoints": true,
    "exact_payload_approval": true,
    "audit_hash_chain": true,
    "demo_reset": true,
    "live_replay_after_write": false
  }
}
```

The example shows one representative entry. The response contains every registered tool, and the
complete list comes from immutable server policy rather than model output.

### `GET /api/v1/connectors`

Returns `{"items": [...], "total": N}` using the connector objects shown by readiness. It never
returns API keys, tokens, authorization headers, or provider account data.

## Runs

### `POST /api/v1/runs`

Creates a run and executes safe steps until completion, failure, or the first approval interrupt.

```json
{
  "instruction": "Prepare the seeded customer renewal follow-up.",
  "mode": "demo",
  "idempotency_key": "create-renewal-run-001"
}
```

- `instruction` is required and contains 3–5000 characters.
- `mode` is optional. If present, it must match the configured server mode; it cannot activate live
  connectors.
- `idempotency_key` is optional, 8–200 characters, and makes create-run retries return the original
  logical run.

Response: `202 Accepted`, with a `RunDetail`:

```json
{
  "run": {
    "id": "run_01...",
    "thread_id": "run_01...",
    "instruction": "Prepare the seeded customer renewal follow-up.",
    "mode": "demo",
    "status": "awaiting_approval",
    "current_index": 2,
    "summary": "",
    "error_code": null,
    "error_summary": null,
    "created_at": "2026-07-18T18:00:00Z",
    "updated_at": "2026-07-18T18:00:01Z",
    "completed_at": null
  },
  "steps": [],
  "pending_approval": {},
  "audit_events": [],
  "receipts": []
}
```

`thread_id` equals the public run ID and is the LangGraph `configurable.thread_id`.

Run statuses are:

- `queued`;
- `running`;
- `awaiting_approval`;
- `completed`;
- `completed_with_rejections`;
- `needs_attention`;
- `failed`; or
- `cancelled`.

An ambiguous side-effect result is represented by an `outcome_unknown` proposal/receipt and a run
that needs attention. It blocks automatic replay.

### `GET /api/v1/runs`

Lists runs newest first.

Query parameters:

- `status`: optional exact run status;
- `limit`: 1–100, default 50; and
- `offset`: non-negative integer, default 0.

```json
{
  "items": [],
  "total": 0
}
```

### `GET /api/v1/runs/{run_id}`

Returns one `RunDetail` with the run, ordered steps, current pending approval (or `null`), audit
events, and receipts.

Each planned step includes:

```json
{
  "id": "step_01...",
  "run_id": "run_01...",
  "position": 0,
  "action": {"tool_name": "db_select", "query": "SELECT ..."},
  "status": "succeeded",
  "receipt_id": "receipt_01..."
}
```

Completed and uncertain write attempts appear in `receipts` with the identifiers needed for
readback and reconciliation:

```json
{
  "id": "receipt_01...",
  "run_id": "run_01...",
  "proposal_id": "proposal_01...",
  "step_id": "step_01...",
  "tool_name": "calendar_create",
  "status": "succeeded",
  "provider": "demo_calendar",
  "provider_id": "event_01...",
  "idempotency_key": "run_01...:step_01...:1",
  "request_hash": "64-character-sha256...",
  "response": {"readback": {"summary": "Customer renewal review"}},
  "error_code": null,
  "created_at": "2026-07-18T18:00:02Z",
  "completed_at": "2026-07-18T18:00:02Z"
}
```

Receipt statuses are `succeeded`, `failed`, and `outcome_unknown`. Treat `response` as
provider-shaped data: sanitize it before logging or sharing it.

### `POST /api/v1/runs/{run_id}/cancel`

Cancels a non-terminal run and returns its updated `RunDetail`. A terminal run returns `409
conflict`. Cancellation does not reverse an already executed external effect.

## Approvals

### `GET /api/v1/approvals`

Lists proposals as `{"items": [...], "total": N}`.

Query parameters:

- `status`: proposal status, default `pending`; and
- `run_id`: optional exact run filter.

Proposal statuses are `pending`, `approved`, `rejected`, `superseded`, `executing`, `succeeded`,
`failed`, `outcome_unknown`, `expired`, and `cancelled`.

### `GET /api/v1/approvals/{proposal_id}`

Returns the complete current proposal:

```json
{
  "id": "proposal_01...",
  "run_id": "run_01...",
  "step_id": "step_02...",
  "tool_name": "calendar_create",
  "action": {
    "tool_name": "calendar_create",
    "calendar_id": "primary",
    "summary": "Customer renewal review",
    "description": "Review renewal options.",
    "start_at": "2030-01-15T10:00:00Z",
    "end_at": "2030-01-15T10:30:00Z",
    "attendees": ["owner@example.test", "customer@example.test"],
    "send_updates": "none"
  },
  "risk_level": "high",
  "title": "Create calendar event",
  "reason": "The workflow prepared a customer review meeting.",
  "expected_effect": "Creates one event in the configured calendar.",
  "payload_hash": "4e8d...64-hex-characters...",
  "version": 1,
  "status": "pending",
  "interrupt_id": "interrupt_01...",
  "allowed_decisions": ["approve", "reject", "revise"],
  "review_context": {
    "effect": "Create an external calendar event",
    "requires_exact_payload_approval": true,
    "attendee_count": 2,
    "send_updates": "none",
    "notification_behavior": "Google Calendar will not send attendee notifications."
  },
  "before": null,
  "after": null,
  "created_at": "2026-07-18T18:00:01Z",
  "updated_at": "2026-07-18T18:00:01Z",
  "expires_at": "2026-07-18T19:00:01Z"
}
```

The browser should display every consequential field from `action` together with the proposal
`title`, `reason`, and `expected_effect`. Server-derived `review_context`, the hash, version,
run/step/proposal identity, and interrupt ID may remain behind technical disclosures for exact
verification. Email actions include the server-bound `sender`; calendar writes include
`send_updates`. `allowed_decisions` comes from server policy and controls whether the UI may offer
editing. Record-update proposals populate `before` and `after` so a reviewer can compare the changed
fields; other action types return `null` for both.

### `POST /api/v1/approvals/{proposal_id}/decisions`

Records one decision, validates it against the current proposal/checkpoint, and resumes the graph
until its next pause or terminal state.

Approve:

```json
{
  "decision": "approve",
  "expected_version": 1,
  "expected_payload_hash": "4e8d...64-hex-characters...",
  "reason": "Time and attendees confirmed.",
  "idempotency_key": "decision-calendar-001"
}
```

Reject (reason required):

```json
{
  "decision": "reject",
  "expected_version": 1,
  "expected_payload_hash": "4e8d...64-hex-characters...",
  "reason": "Do not contact this customer yet.",
  "idempotency_key": "decision-calendar-002"
}
```

Revise, when the tool policy allows revision:

```json
{
  "decision": "revise",
  "expected_version": 1,
  "expected_payload_hash": "4e8d...64-hex-characters...",
  "reason": "Move the meeting one hour later.",
  "revised_action": {
    "tool_name": "calendar_create",
    "calendar_id": "primary",
    "summary": "Customer renewal review",
    "description": "Review renewal options.",
    "start_at": "2030-01-15T11:00:00Z",
    "end_at": "2030-01-15T11:30:00Z",
    "attendees": ["owner@example.test", "customer@example.test"],
    "send_updates": "none"
  },
  "idempotency_key": "decision-calendar-003"
}
```

Response: `202 Accepted`, with the updated `RunDetail`.

A revision validates the replacement action, supersedes the old proposal, creates a new version and
payload hash, and interrupts again. It does not execute from the revision request alone.

The client does not repeat `interrupt_id` in the decision body. The runtime loads the durable
proposal, checks that its stored interrupt ID is the only pending interrupt for the run, and resumes
with an interrupt-ID mapping. If the checkpoint no longer matches, it returns a conflict without
executing.

`idempotency_key` makes an HTTP retry return the existing logical decision. Replaying a consumed
decision does not execute a second write.

`idempotency_key` is required and contains 8–200 characters. A rejection also requires a non-empty
`reason`; a revision requires `revised_action` and is accepted only for tools whose
`allowed_decisions` includes `revise`.

## Audit events

### `GET /api/v1/audit-events`

Returns `{"items": [...], "total": N}`.

Query parameters:

- `run_id`: optional exact run filter;
- `after_sequence`: return events after this sequence, default 0; and
- `limit`: 1–1000, default 200.

Each event includes sequence, event and run IDs, optional proposal ID, actor, event type, redacted
detail, previous hash, event hash, and creation timestamp.

### `GET /api/v1/audit-events/export`

Returns a downloadable JSON document:

```json
{
  "exported_at": "2026-07-18T18:30:00Z",
  "items": [],
  "total": 0,
  "chain_valid": true
}
```

Use optional `run_id` to export one run. `chain_valid` verifies the local repository hash chain; it
does not make storage independently immutable.

## Demo reset

### `POST /api/v1/demo/reset`

Takes no request body. It clears local demo workflow/checkpoint records and restores deterministic
fixtures.

```bash
curl --fail --silent --request POST http://127.0.0.1:8000/api/v1/demo/reset
```

Response:

```json
{
  "status": "reset",
  "counts": {
    "customers": 1,
    "calendar_events": 0,
    "emails": 0,
    "purchase_orders": 0
  }
}
```

Reset is destructive to local demo history and returns `409 conflict` outside demo mode. It never
deletes or changes a live provider resource.

## Errors

| HTTP | Code | Meaning |
| --- | --- | --- |
| `404` | `not_found` | Requested run/proposal does not exist |
| `409` | `stale_approval` | Version or payload hash no longer matches |
| `409` | `conflict` | Run busy, decision disallowed/consumed, terminal cancellation, or reset unavailable |
| `422` | `invalid_request` | Runtime/domain validation failed |
| `422` | `validation_error` | Request body or query does not match the declared schema |
| `500` | `internal_error` | Sanitized unexpected failure with an `X-Request-ID`; no write should be inferred |

Never interpret a network error or missing response as proof that a write failed. Reload the run;
if its proposal/receipt is `outcome_unknown`, follow [Operations](operations.md#reconcile-outcome_unknown).

## Minimal flow

Create a demo run:

```bash
curl --fail --silent \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"instruction":"Prepare the seeded renewal follow-up.","mode":"demo","idempotency_key":"demo-run-0001"}' \
  http://127.0.0.1:8000/api/v1/runs
```

Fetch its current proposal from the returned `pending_approval`, review it, and submit the current
`version` and `payload_hash`:

```bash
curl --fail --silent \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"decision":"approve","expected_version":1,"expected_payload_hash":"REPLACE_WITH_64_CHARACTER_HASH","idempotency_key":"demo-decision-0001"}' \
  http://127.0.0.1:8000/api/v1/approvals/REPLACE_WITH_PROPOSAL_ID/decisions
```

The response contains the updated run, which either pauses at the next write or reaches a terminal
state. Reload it at `GET /api/v1/runs/{run_id}` to verify the current proposal and receipts. Never
reuse bindings from another run or an earlier proposal version.
