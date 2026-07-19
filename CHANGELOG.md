# Changelog

Relay follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions. The entries
below track the public source releases.

## Unreleased

### Added

- Connector-by-connector live acceptance commands with explicit read-only and write acknowledgements,
  controlled targets, sanitized results, and live tests that are skipped by default.
- A least-privilege PyPI Trusted Publishing workflow, maintainer runbook, and newcomer adoption
  feedback form.
- A source-backed hosted-deployment decision that separates a future single-operator demo from the
  identity, tenancy, Postgres, and operational controls required for production.

### Changed

- Live readiness rejects reserved example senders and can inspect every connector without creating
  provider clients; live pytest runs require exactly one selected lane.
- Google Calendar writes require exact provider GET readback after creation, and Resend acceptance
  verifies current-invocation provider readback and duplicate suppression.

## 0.1.0 — 2026-07-18

### Added

- Durable LangGraph workflow with SQLite checkpoints and resumable human interrupts.
- Server-owned approval policy for email, calendar, record, and purchase writes.
- Exact-action decisions, revisions, idempotent execution, readback receipts, and hash-chained audit.
- Credential-free deterministic demo plus optional Claude, Tavily, Resend, and Google Calendar
  boundaries.
- FastAPI service, CLI, responsive React workspace, Docker Compose package, CI, and safety tests.

### Changed

- Simplified first-run and approval-review language for open-source adoption.
- Made the Docker demo the primary onboarding path and documented the Python-only package boundary.
- Updated CI actions to their current Node 24-backed major versions.
