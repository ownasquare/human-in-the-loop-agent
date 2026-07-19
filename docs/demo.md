# Deterministic Demo

The Relay demo is designed to prove the approval lifecycle without requiring or contacting an
external provider. It uses deterministic plans, fixture-backed reads, local SQLite writes, and
file-backed LangGraph checkpoints.

## What the demo does

The seeded renewal workflow:

1. searches a bounded deterministic fixture;
2. reads a demo customer record;
3. checks fixture-backed availability;
4. proposes a calendar event and pauses;
5. after exact approval, writes one event to the demo calendar;
6. proposes an email and pauses again;
7. after exact approval, writes one message to the demo outbox;
8. proposes a customer-record update and pauses before that mutation;
9. proposes a local demo purchase order and pauses before creating it; and
10. records receipts, readbacks, and a hash-chained audit trail.

A rejected step records no tool effect. A revised email or calendar payload becomes a new proposal
and requires another approval.

A demo purchase order is a local database row; it is not a payment, supplier submission, or live
purchase.

## Source quickstart

Requirements:

- Python 3.11–3.13;
- [uv](https://docs.astral.sh/uv/);
- Node.js 22 and npm; and
- a modern browser.

```bash
cp .env.example .env
uv sync --frozen --all-extras --dev
cd web
npm ci
cd ..
uv run relay demo reset
```

Keep this value in `.env`:

```dotenv
RELAY_MODE=demo
```

Empty provider-key fields are correct for the demo.

Start the API:

```bash
uv run relay serve
```

In a second terminal, start the frontend:

```bash
cd web
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173).

## Compose quickstart

The root image builds the React app and serves it through FastAPI:

```bash
docker compose up --build
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The service binds to loopback by default and
stores durable data in the `relay-data` volume.

Stop without deleting the volume:

```bash
docker compose down
```

Removing the named volume deletes local Relay data and checkpoints. Do not use `down --volumes`
unless that deletion is intended.

## Browser walkthrough

### 1. Confirm identity and readiness

The page should identify itself as Relay and show demo mode in Connections. Planner, search, email,
and calendar should be ready in demo mode. No screen should claim live provider readiness.

### 2. Start the renewal workflow

From **Work**, click **Start workflow**, then **Start walkthrough**. Demo mode always runs the same
Acme renewal scenario so the result is repeatable. The timeline should show three completed safe
reads followed by **Waiting for your decision** on the calendar write.

### 3. Inspect before approving

Open the approval and verify:

- action type and human-readable effect;
- full recipients or attendees;
- time, timezone, and notification behavior for calendar actions;
- complete subject/body for email actions;
- before/after values for record mutations;
- proposal version, payload hash, step ID, and interrupt ID under **Technical binding details**; and
- that **Reject**, **Edit**, and **Approve action** match the allowed decisions.

Before approving, **What happened** must not show an execution result for the pending action. The
demo outbox/calendar/order/readback must remain unchanged.

### 4. Exercise rejection or revision

For rejection, add a short reason and reject. The run records no effect and either advances safely
or ends according to the plan.

For revision, change an allowed email or calendar field. Relay should show a new proposal with a
higher version and different payload hash. The revised action must still be pending; approve it only
after reviewing the complete new payload.

### 5. Approve and inspect readback

Approve the exact payload. The run resumes and stops at the next write. **Latest confirmed result**
and **What happened** should show:

- the human decision;
- one execution attempt for the stable run/step/proposal-version idempotency key;
- one demo provider-shaped receipt;
- one readback summary; and
- a result connected to the same run, proposal, action, and receipt. The read-only **History** view
  carries the corresponding audit events.

Double-clicking or replaying the decision must not create a second receipt or demo effect.

### 6. Finish the workflow

Review each write independently. A prior approval never authorizes a later step. At completion,
inspect **What happened** and **History**.

## Restart-resume check

To prove file-backed pause/resume locally:

1. start a run and stop at an approval;
2. note the run ID without approving;
3. stop the API normally;
4. start it again with the same `RELAY_DATA_DIR`;
5. reopen the run; and
6. verify that the same proposal, version, hash, and interrupt remain pending.

Only then submit a decision. A new runtime should resume the persisted graph thread rather than
starting a replacement run.

## Reset demo data

CLI reset:

```bash
uv run relay demo reset
```

The reset restores seeded records and clears demo runs, checkpoints, proposals, decisions,
receipts, outbox messages, events, and purchase orders. It is intentionally destructive to demo
history.

API reset is also available in demo mode:

```bash
curl --fail --silent \
  --request POST \
  http://127.0.0.1:8000/api/v1/demo/reset
```

Reset fails closed outside demo mode. In demo mode it intentionally deletes every local graph
thread and workflow record, including active runs.

## Expected failure states

| State | What the UI should do |
| --- | --- |
| Offline | Preserve context, disable mutation, and offer retry after connectivity returns |
| Stale approval | Close/disable old controls and load the current proposal |
| Connector unavailable | Show the exact connector boundary; do not substitute demo data |
| Run busy | Keep the decision pending and retry the read, not the write |
| `outcome_unknown` | Stop automatic progress and direct the operator to reconciliation |

## Proof classification

The demo can establish local source, deterministic workflow, fixture-backed persistence, and local
browser behavior. It does **not** establish:

- Claude API access or planning quality;
- Tavily search freshness;
- email delivery;
- Google Calendar persistence or guest notifications;
- any real database mutation;
- hosted behavior; or
- production security/readiness.

Record those layers separately if they are later exercised.
