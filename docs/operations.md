# Operations

Relay's checked-in operating path is local and single-instance. Source startup and Compose are
supported development/demo shapes; neither is a claim of hosted or production readiness.

## Operating modes

| Mode | Purpose | External calls |
| --- | --- | --- |
| Source demo | Development and deterministic proof | None |
| Compose demo | Packaged local image and persistent restart proof | None |
| Source live | Explicit integration work with the live adapter set | Required configured live connectors |
| Hosted | Operator-created deployment | Not provided or proven by this repository |

Relay has one overall `RELAY_MODE`, not a browser or per-connector switch. Demo selects every
deterministic adapter. Live selects the live planner/search/email/calendar adapter set plus the
allowlisted local database and requires the needed credentials. If email is unavailable in live
mode, the email step fails at that boundary; it does not write to the demo outbox and report success.

## Configuration

Copy `.env.example` to an untracked `.env`. Do not commit the result.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAY_MODE` | `demo` | Overall deterministic/live operating posture |
| `RELAY_DATA_DIR` | `.relay` | Application and checkpoint database directory |
| `RELAY_HOST` | `127.0.0.1` | API bind address |
| `RELAY_PORT` | `8000` | API/container port |
| `RELAY_STATIC_DIR` | unset in source; `/app/static` in image | Built React asset directory |
| `RELAY_ACTION_TTL_MINUTES` | `60` | Pending proposal lifetime |
| `RELAY_MAX_PLAN_STEPS` | `12` | Maximum validated actions in one plan |
| `RELAY_MAX_SEARCH_RESULTS` | `5` | Search result ceiling |
| `RELAY_REQUEST_TIMEOUT_SECONDS` | `20` | Provider request timeout ceiling |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Operator-selected live planner model (accepted alias) |
| `ANTHROPIC_API_KEY` | empty | Anthropic credential for explicit live planning |
| `TAVILY_API_KEY` | empty | Tavily credential |
| `RESEND_API_KEY` | empty | Resend credential |
| `RELAY_EMAIL_FROM` | `relay@example.invalid` in source | Allowed sender identity; `.env.example` supplies a demo value |
| `GOOGLE_CALENDAR_ACCESS_TOKEN` | empty | Google Calendar access token |
| `GOOGLE_CALENDAR_ID` | `primary` | Target calendar identifier |
| `RELAY_CALENDAR_SEND_UPDATES_DEFAULT` | `none` | Default for deterministic calendar writes: `none`, `all`, or `externalOnly`; the resulting action value is still reviewed and hashed |
| `RELAY_ALLOW_CONTAINER_BIND` | `false` | Narrow Compose-only acknowledgement for the container's `0.0.0.0` listener |

Readiness output must report only mode and configured/not-configured/available state. It must not
print, echo, hash, partially reveal, or persist a credential value.

`GOOGLE_CALENDAR_ACCESS_TOKEN` is suitable only for a bounded local integration check. A real
deployment should use an approved OAuth/token broker and refresh lifecycle rather than a long-lived
token in a file.

## Source startup

```bash
cp .env.example .env
uv sync --frozen --all-extras --dev
cd web
npm ci
cd ..
uv run relay doctor
uv run relay demo reset
```

Start the API:

```bash
uv run relay serve
```

Start the development frontend separately:

```bash
cd web
npm run dev
```

The development UI is at `http://127.0.0.1:5173`; the API and OpenAPI UI are at
`http://127.0.0.1:8000` and `http://127.0.0.1:8000/docs`.

## Container startup

Validate the rendered configuration before starting it:

```bash
docker compose config --quiet
docker compose up --build --detach
```

Check local health:

```bash
curl --fail --silent http://127.0.0.1:8000/api/v1/health
curl --fail --silent http://127.0.0.1:8000/api/v1/readiness
```

The Compose service:

- binds the published port to `127.0.0.1` by default;
- runs as a non-root image user;
- drops Linux capabilities and enables `no-new-privileges`;
- uses a read-only root filesystem and a bounded `/tmp` tmpfs;
- persists only `/app/data` in the `relay-data` volume; and
- serves `/app/static` and the API from one origin.

`RELAY_ALLOW_CONTAINER_BIND=true` acknowledges the container's internal `0.0.0.0` listener; it is
not authentication. Do not change the published mapping from exactly `127.0.0.1:8000:8000` unless
an authenticated, authorized, TLS-terminating access layer has been added and validated.

Stop the service without deleting data:

```bash
docker compose down
```

Do not add `--volumes` unless deleting all local Relay data and checkpoints is intended.

`web/Dockerfile` is a standalone static build/preview reference. It is not part of Compose proof.
The supported container path is the single root image, which serves the built UI and API from one
origin. Vite remains the host development workflow.

## Health versus readiness

- `/api/v1/health` is liveness. A green response means the process can serve HTTP.
- `/api/v1/readiness` verifies local stores and reports connector readiness.
- Neither endpoint performs an irreversible action.
- A live connector may be `not_configured` or unavailable while the process remains healthy.
- A ready connector has configuration and local initialization, not proof of a real delivery or
  mutation.

## Data layout and permissions

`RELAY_DATA_DIR` contains application SQLite data and LangGraph checkpoint data. Exact filenames are
an implementation detail; back up and restore the directory as a unit.

On a local host, restrict the directory to the account running Relay. In a container, `/app/data` is
owned by the unprivileged Relay user. Do not mount a world-readable directory or a repository path
that will be committed.

Application records include instructions, proposal payloads, approval comments, demo messages,
calendar details, record values, receipts, and redacted audit events. Treat the data directory as
sensitive even when it contains no provider credential.

## Backup

SQLite can have active WAL files. The simplest consistent local backup is:

1. stop new requests;
2. wait for the current run transition to finish;
3. stop Relay cleanly;
4. copy or snapshot the complete `RELAY_DATA_DIR` or `relay-data` volume; and
5. restart and verify readiness.

Record the Relay version/commit and backup time alongside the archive. Do not copy only one database
file while the process is writing.

## Restore

1. Stop Relay.
2. Preserve the current data directory separately.
3. Restore the complete application/checkpoint backup with restrictive ownership.
4. Start the same Relay version that created the backup.
5. Check health and readiness.
6. Inspect pending approvals before allowing any decision.
7. Verify the audit chain and one completed receipt/readback.
8. Upgrade only after the restored state is known good.

Restoring an older backup can resurrect an action that a provider already accepted after the backup.
Reconcile pending and `outcome_unknown` actions against provider history before approving or
retrying anything.

## Reconcile `outcome_unknown`

`outcome_unknown` means Relay cannot prove whether a provider accepted a submitted action. It is not
the same as failure.

1. Do not click approve again and do not restart the action with a new run.
2. Retrieve `GET /api/v1/runs/{run_id}` and inspect the affected receipt plus the run's collapsed
   technical details. Record the available run/step/proposal version, provider idempotency key,
   sanitized request or correlation ID, submission window, and target account. The primary UI does
   not promise to expose every reconciliation identifier.
3. Use a read-only provider API or provider dashboard to search for that identity and time window.
4. If the effect exists, record reconciliation as success in the operator's incident system and
   attach the provider resource ID.
5. If the provider can prove the effect does not exist, record that external reconciliation as
   absent.
6. If neither can be established, keep the action blocked as unknown and escalate to the provider.
7. Only after proven absence should an operator create separately authorized new work.

Relay currently has no reconciliation-write API and does not reopen a `needs_attention` run. Keep
the run and its receipt as evidence; reconciliation is an explicit external operator procedure.

For email, provider acceptance is not recipient delivery. For calendar, verify the event in the
target calendar and check attendee-notification behavior. For record updates, read the current
version and values. For purchasing, Relay ships no live connector; a demo row needs no provider
reconciliation.

## Restart and interrupted runs

An interrupted run should survive normal process restart:

1. restart with the same data directory;
2. load the run by its public ID;
3. compare proposal ID, version, payload hash, and interrupt ID with the browser's view; and
4. refresh stale browser state before submitting a decision.

Never “repair” a missing checkpoint by manually marking a proposal approved or calling an executor.
If the application record and checkpoint disagree, stop the run, preserve both databases, and treat
it as a recovery incident.

## Demo reset

```bash
uv run relay demo reset
```

Reset is destructive to local demo history and restores deterministic fixtures. It must be disabled
outside demo mode and must never call a live provider. See [Demo](demo.md) for the API equivalent.

## Live connector activation

Activate live mode deliberately:

1. keep the overall app on loopback;
2. configure every required connector with its narrowest credential scope;
3. set `RELAY_MODE=live`, run `relay doctor`, and inspect only configured/not-configured state;
4. use dedicated non-production accounts, recipients, calendars, and records;
5. exercise one bounded connector boundary at a time with visible approval for writes;
6. capture each provider receipt and readback separately; and
7. record connector-specific live proof separately from demo proof.

### Claude planning

Set `ANTHROPIC_API_KEY` and a supported `ANTHROPIC_MODEL`. Review
[Model compatibility](model-compatibility.md). The configured model is not called in demo planning
mode.

### Tavily search

Use a dedicated Tavily key and bound result count/content. Search output is untrusted data. Do not
send secrets, complete records, or private email/calendar content in a search query.

### Resend email

Use a verified, least-privilege sender and a test recipient. Preserve Relay's derived
run/step/proposal-version key as the idempotency identity. Treat timeout after submission as unknown
until provider reconciliation.

### Google Calendar

Use the narrow calendar-event scope and a test calendar. The approval must show calendar ID,
attendees, offset-aware start/end values, and `send_updates` plus its human-readable notification
effect. The adapter sends that reviewed value as Google Calendar's `sendUpdates` query parameter.
The current action does not create conferencing data. Do not infer guest receipt from a successful
event response.

## Logs and audit

Logs should contain request ID, run ID, step/action type, transition, duration, and sanitized error
class. They must not contain credential values, authorization headers, complete provider payloads,
or private action bodies.

The local audit hash chain detects modification but is not independent immutable storage. Export
redacted audit events to separately administered storage before relying on them for a production
compliance control.

## Scaling beyond local SQLite

Before multiple API processes or hosts:

- move application records to a production transactional database;
- replace `AsyncSqliteSaver` with a production LangGraph checkpointer such as Postgres;
- add a distributed per-run lock or compare-and-swap transition owner;
- enforce authenticated reviewer identity, roles, and tenant ownership;
- use managed secrets and token refresh;
- add queues, timeouts, backpressure, rate limits, metrics, and tracing;
- test failover at every pre-submit and post-submit boundary; and
- rehearse backup, restore, and uncertain-outcome reconciliation.

Do not interpret a successful local Compose run as evidence that these controls exist.

## Validation and proof record

For every release, keep these results separate:

- Python lint, types, unit/integration/API tests, security audits, and build;
- React lint, type check, Cypress component tests, and build;
- Playwright local E2E with desktop/mobile browser inspection;
- local Docker build, non-root runtime, health/readiness, and persistent restart;
- each live connector exercised, with provider receipt/readback;
- hosted URL behavior; and
- production controls.

A skipped or unavailable layer remains unproved; it is not implied by another green layer.
