# Support

Relay is an open-source local reference project and does not include a support SLA.

## Self-help order

1. Run `uv run relay doctor`. It reports modes and readiness without revealing credentials.
2. Read the [Demo guide](docs/demo.md) for deterministic reset and browser steps.
3. Read [Operations](docs/operations.md) for startup, storage, backup, and recovery.
4. Check the API's `/api/v1/health` and `/api/v1/readiness` responses.
5. If this source has been published with an issue tracker, search existing issues before opening a
   new one.

## Open an issue

For an ordinary defect in a published distribution, include:

- Relay version or commit;
- operating system, Python version, Node version, and startup method;
- the runtime mode and configured/not-configured status of each relevant connector;
- exact command or browser action;
- expected and observed behavior;
- the machine-readable API error code, if present;
- whether a restart or `relay demo reset` changes the result; and
- sanitized logs or a screenshot with private data removed.

Report configuration key **names** and configured/not-configured status only. Never include secret
values, authorization headers, complete provider responses, email content, calendar attendee data,
database rows, or `.env` contents.

## Live connector questions

A green deterministic demo does not prove live-provider access. For a live failure, identify the
boundary precisely:

- Claude model selection or Anthropic access;
- Tavily search;
- Resend email submission/readback;
- Google Calendar authorization/insertion/readback; or
- application-owned database integration.

If Relay reports `outcome_unknown`, do not retry the action. Follow the reconciliation procedure in
[Operations](docs/operations.md#reconcile-outcome_unknown) and inspect the provider using the
action's idempotency key or provider receipt ID.

## Sensitive reports

Security vulnerabilities, credential exposure, approval bypasses, and reports containing private
data belong in the private process described in [SECURITY.md](SECURITY.md), not a public issue.
