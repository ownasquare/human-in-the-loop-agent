# Contributing to Relay

Thank you for helping make human-reviewed agent workflows easier to understand and safer to run.
By participating, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Use the repository issue tracker, when configured, for feature proposals and ordinary defects.
- Use the private process in [SECURITY.md](SECURITY.md) for vulnerabilities or sensitive reports.
- Keep changes focused. A safety-policy change should not be bundled with unrelated UI restyling.
- Never commit credentials, provider payloads, personal records, or copied production data.

## Development setup

Relay supports Python 3.11–3.13 and Node.js 22.

```bash
cp .env.example .env
make install
uv run relay demo reset
```

The default `.env.example` is credential-free. Keep `RELAY_MODE=demo` for normal development and
CI.

Start the API and UI in separate terminals:

```bash
uv run relay serve --reload
```

```bash
cd web
npm run dev
```

## Required design rules

Every change must preserve these boundaries:

1. Unknown tools are denied by server-owned policy.
2. Preparing a write never performs the write.
3. Email, calendar, record, and purchase writes require a current human decision.
4. A decision is bound to the exact run, step, proposal version, payload hash, and interrupt ID.
5. An edit creates a new proposal and a new review; it does not mutate an already-approved payload.
6. Side effects occur only in executor code after the approval node has completed.
7. Pre-submit failures may use bounded retries. Ambiguous post-submit failures become
   `outcome_unknown` and are not retried automatically.
8. Demo mode never calls an external provider.
9. No model, browser request, or connector response may weaken the risk registry.
10. Logs, errors, checkpoints, and audit events must be secret-safe.

Read [docs/safety.md](docs/safety.md) before changing policy, persistence, graph routing, or a live
adapter.

## Add a tool safely

Treat a new tool as one vertical safety change. Follow this order:

1. Add a strict action model and include it in the discriminated `Action` union in
   `src/relay/models.py`. Bound lengths, types, enums, and consequential fields.
2. Add an immutable rule in `src/relay/policy.py`. Unknown tools stay denied; every write must set
   `requires_approval=True` and list only supported decisions.
3. Implement the `ToolAdapter` contract in `src/relay/tools/`. Keep preparation free of effects,
   pass the provided idempotency key to writes, distinguish safe pre-submit failures from
   `OutcomeUnknownError`, and return a minimized readback.
4. Register separate demo and live adapters in `src/relay/tools/registry.py`. Demo adapters must
   never contact a provider; an unavailable live connector must fail closed.
5. Expose the action to the deterministic or Claude planner in `src/relay/planner.py`, including any
   server-owned field normalization required before hashing.
6. Add the plain-language tool label and review projection in `web/src/api.ts` and the approval UI.
   Keep exact consequential fields visible and internal identifiers collapsed.
7. Add the proof required by the table below: schema rejection, policy classification,
   prepare-has-no-effect, approval, idempotency, failure boundary, readback, and browser review.

Start with a read-only tool when learning the extension path. Do not copy a write adapter until its
provider supports idempotency and reconciliation.

## Test policy

Run the smallest focused test first, then the complete relevant gate.

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=relay --cov-branch --cov-report=term-missing
uv run bandit -q -r src
uv run pip-audit
```

For React:

```bash
cd web
npm run lint
npm run typecheck
npm run test:component
npm run build
npm run test:e2e
```

Cypress is exclusively for React component tests under `web/cypress/component/`. Playwright is
exclusively for end-to-end tests under `web/tests/e2e/`. Do not add or run Cypress E2E tests.

Tests that exercise a real provider must use the `live` marker or the project's equivalent explicit
opt-in. They must never run during the default offline suite. Mock-backed and live-provider results
must be reported separately.

## Change-specific assurance

| Change | Minimum added proof |
| --- | --- |
| Action schema | Valid, invalid, canonicalization, and payload-hash tests |
| Risk policy | Allow, interrupt, unknown-tool denial, and policy-override tests |
| Write adapter | Prepare-has-no-effect, approval, idempotency, timeout, and readback tests |
| Checkpoint/runtime | Pause, restart, exact interrupt resume, stale decision, and duplicate decision tests |
| API | Success, stable error code, validation, stale conflict, and pagination tests |
| React approval UI | Keyboard, focus, loading, error, stale, unknown-outcome, and narrow viewport tests |
| Packaging | Locked install, non-root image, health check, persistent data, and Compose validation |

## Documentation and proof language

Update the nearest guide when behavior or an operator command changes. Keep these proof layers
separate in pull requests and completion notes:

- deterministic fixture/demo proof;
- local source and test proof;
- local container/browser proof;
- live Claude or connector proof;
- hosted proof; and
- production proof.

Do not use “production-ready,” “live,” or “delivered” when the evidence is local, mocked, fixture
backed, or only source-reviewed.

## Pull requests

A pull request should include:

- the user-visible problem and chosen boundary;
- changed files and data migrations;
- exact validation commands and outcomes;
- fixture, mock, network, provider, and hosted status;
- screenshots for visible changes at desktop and mobile widths;
- new warnings or unresolved risks; and
- a rollback or migration note when persistence or policy changes.

Keep commits coherent and avoid generated test artifacts, databases, `.env` files, build output, or
dependency directories.
