# Security Policy

Relay is an approval-first reference application. Its safety controls reduce the chance that an
agent performs an unintended write, but they do not turn an unauthenticated local application into
a production authorization system.

## Supported versions

Security fixes are applied to the latest commit on `main` and the latest published `0.1.x` release,
when a release exists. Older development snapshots are not supported.

## Report a vulnerability privately

Use the repository's **Security → Report a vulnerability** workflow to
[open a private report](https://github.com/ownasquare/human-in-the-loop-agent/security/advisories/new).
Private vulnerability reporting is enabled. Include:

- the affected version or commit;
- the smallest reproducible request or workflow;
- expected and observed behavior;
- whether a real provider, credential, or personal record was involved; and
- a suggested mitigation, if you have one.

Do not put credentials, tokens, private email content, calendar details, provider receipts, or an
exploitable proof of concept in a public issue. Downstream distributors must configure their own
private intake before a shared deployment.

This project does not currently operate a bug-bounty program. Maintainers will acknowledge a
private report and coordinate remediation and disclosure on a best-effort basis.

## High-value security boundaries

Reports are especially useful when they involve:

- execution of a write without a matching approval;
- run-ID, step-ID, proposal-version, payload-hash, or interrupt-ID substitution;
- replay of a stale or already-consumed decision;
- duplicate provider submission despite the idempotency ledger;
- automatic retry after an ambiguous post-submit response;
- policy weakening through model, search, or tool output;
- secrets appearing in logs, API responses, checkpoints, audit events, or committed files;
- cross-run or cross-tenant state access;
- SQL injection, path traversal, unsafe deserialization, or arbitrary code execution;
- demo mode making an external network call; or
- a deployment default that exposes the unauthenticated service beyond loopback.

## Deployment responsibility

The provided source and Compose configuration are local-first. Before any shared or public
deployment, operators must add and validate:

- authentication, authorization, tenant isolation, and reviewer identity;
- TLS and a trusted reverse proxy;
- CSRF protection and an exact CORS allowlist;
- network egress controls and least-privilege provider scopes;
- a production database and LangGraph checkpointer;
- encrypted storage, managed secrets, backups, retention, and incident response;
- provider-specific idempotency and reconciliation; and
- audit export to a system whose administrators cannot silently rewrite application history.

The current public-hosting decision and the separate hosted-demo and production gates are defined
in [Hosted deployment](docs/hosted-deployment.md). Relay v0.1.0 must remain on loopback until the
applicable gate is implemented and validated.

Never put credentials into an instruction, action payload, approval comment, URL, screenshot,
issue, or test fixture. Use a secret manager or an uncommitted local `.env` file. `relay doctor`
reports presence/readiness only and must never print a credential value.

## Safe testing

Use demo mode for reproductions whenever possible. Do not test against accounts, recipients,
calendars, or records you do not own or have explicit permission to use. Live-provider tests must
be individually opted in and must not run as part of the credential-free default test suite.

See [docs/safety.md](docs/safety.md) for the full threat model and safety invariants.
