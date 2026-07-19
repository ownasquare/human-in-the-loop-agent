# Live provider acceptance

Live acceptance is optional integration proof. It is deliberately separate from Relay's
credential-free demo, normal test suite, hosted deployment, and production readiness.

Passing one lane proves only that lane. Provider acceptance is not recipient delivery, hosted
readiness, production readiness, or proof that another connector works.

## Safety prerequisites

Before any live command:

1. use dedicated non-production provider accounts and least-privilege credentials;
2. keep Relay on loopback and put credentials only in an untracked `.env` or approved secret store;
3. use a verified test sender, controlled recipient, and dedicated test calendar;
4. run one lane at a time in the order below;
5. use a new 8-64 character run ID for each write acceptance; and
6. keep credentials, addresses, calendar IDs, prompts, provider payloads, and resource IDs out of
   screenshots, issues, logs, and evidence records.

Copy `.env.example` to `.env`, then replace only the values required for the lane. The checked-in
sender is intentionally unusable. Resend additionally requires `RELAY_ACCEPTANCE_EMAIL_TO` to be a
controlled non-production recipient.

## Credential-safe preflight

```bash
uv run relay live doctor
```

The preflight constructs no planner or provider client and makes no network request. It prints only
fixed connector names and configured/not-configured state. It lists every live connector and exits
nonzero until Claude, Tavily, Resend, Google Calendar, and the local database are configured.
Purchasing remains disabled in live mode by design.

An incomplete overall preflight does not authorize skipping a lane's requirements. It simply makes
all missing configuration visible before any connector-specific command.

## Run the lanes sequentially

| Order | Lane | Effect | Exact `--confirm` phrase |
| --- | --- | --- | --- |
| 1 | Claude | Read-only planning | `RUN_LIVE_CLAUDE` |
| 2 | Tavily | Read-only public search | `RUN_LIVE_TAVILY` |
| 3 | Google free/busy | Read-only calendar query | `RUN_LIVE_GOOGLE_READ` |
| 4 | Resend | Controlled external email | `SEND_LIVE_RESEND_EMAIL` |
| 5 | Google event | Controlled external event | `CREATE_LIVE_GOOGLE_EVENT` |

### Claude

```bash
uv run relay live claude --confirm RUN_LIVE_CLAUDE
```

Relay submits one fixed, non-sensitive instruction and records only action count plus schema and
policy validation. No tool action is executed. Record the exact configured model ID, date, Relay
commit, and locked Anthropic/LangChain versions separately.

### Tavily

```bash
uv run relay live tavily --confirm RUN_LIVE_TAVILY
```

Relay submits one fixed public query, requests at most two results, disables generated answers and
raw content, and records only result count and bounds. Search content remains untrusted data.

### Google Calendar read

Choose an offset-aware interval no longer than seven days:

```bash
uv run relay live google-read \
  --confirm RUN_LIVE_GOOGLE_READ \
  --start 2030-01-15T09:00:00Z \
  --end 2030-01-15T17:00:00Z
```

The result contains only whether a bounded response arrived and the busy-slot count. It does not
print the calendar ID or returned intervals.

### Resend email

Obtain explicit approval for the controlled recipient before running:

```bash
uv run relay live resend \
  --confirm SEND_LIVE_RESEND_EMAIL \
  --run-id relay-resend-20300115-001
```

Relay sends fixed content with one deterministic idempotency key, reads the provider copy back with
`GET /emails/{message_id}`, validates it against the reviewed action and the current invocation
window, then repeats the exact same POST key. The lane passes only when the provider timestamp is
current, both POST responses identify the same message, and readback matches. Reusing an older run
ID fails closed. The sanitized result contains booleans and counts only.

Provider acceptance and duplicate suppression do not prove inbox delivery. A human must verify the
controlled recipient separately and record that as a distinct observation.

### Google Calendar write

Use a new run ID and an offset-aware event no longer than two hours. The acceptance event has no
attendees and always uses `sendUpdates=none`:

```bash
uv run relay live google-write \
  --confirm CREATE_LIVE_GOOGLE_EVENT \
  --run-id relay-google-20300115-001 \
  --start 2030-01-15T10:00:00Z \
  --end 2030-01-15T10:30:00Z
```

Relay creates the deterministic event, performs an exact GET readback, then repeats the same
idempotency identity. The lane passes only when Google returns the existing-event conflict path,
the provider copy matches the reviewed event, and both attempts resolve to the same event.

## Maintainer live tests

Normal `pytest` always skips `@pytest.mark.live`. `--run-live` alone is also inert: every invocation
must select one exact `--live-lane`, and that test must have its provider-specific acknowledgement
variable. All other lanes stay skipped even if an old acknowledgement remains in the shell.

| Test lane | `--live-lane` | Required acknowledgement variable and value |
| --- | --- | --- |
| Claude | `claude` | `RELAY_RUN_LIVE_CLAUDE=RUN_LIVE_CLAUDE` |
| Tavily | `tavily` | `RELAY_RUN_LIVE_TAVILY=RUN_LIVE_TAVILY` |
| Resend | `resend` | `RELAY_RUN_LIVE_RESEND=SEND_LIVE_RESEND_EMAIL` |
| Google read | `google-read` | `RELAY_RUN_LIVE_GOOGLE_READ=RUN_LIVE_GOOGLE_READ` |
| Google write | `google-write` | `RELAY_RUN_LIVE_GOOGLE_WRITE=CREATE_LIVE_GOOGLE_EVENT` |

Write tests also require `RELAY_LIVE_ACCEPTANCE_RUN_ID`. Google tests require
`RELAY_LIVE_ACCEPTANCE_START` and `RELAY_LIVE_ACCEPTANCE_END`. Run the one selected lane and remove
the acknowledgement from the shell after the check. For example:

```bash
uv run pytest --run-live --live-lane claude \
  tests/live/test_provider_acceptance.py::test_claude_provider_acceptance
```

## Evidence and reconciliation

Keep one record per lane containing only:

- UTC date, Relay commit, lane, validation environment, and exact command;
- sanitized result booleans/counts;
- fixture/mock status (`live provider`, not hosted or production);
- provider-dashboard readback status without resource IDs or payloads; and
- a separate human delivery/notification observation when applicable.

If a write returns `outcome_unknown`, stop. Do not change the run ID or retry. Use the provider's
read-only API or dashboard to reconcile the deterministic identity and submission window as
described in [Operations](operations.md#reconcile-outcome_unknown). A new attempt is permitted only
after the provider proves the first effect absent and the operator separately authorizes it.

Once the first provider receipt and exact readback exist, a later readback or same-key replay-check
failure is not a no-effect failure. The controlled write was accepted; inspect and reconcile it and
do not rerun the lane.

Only after all required lanes have independent current proof should an operator consider the full
`RELAY_MODE=live` integration flow. That later flow still does not establish hosted or production
readiness.
