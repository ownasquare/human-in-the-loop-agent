## What changes

Describe the user-visible problem and the smallest chosen boundary.

## Safety and data impact

- Approval or policy impact:
- Persistence or migration impact:
- Connector or external-effect impact:
- Fixture/live/hosted/production boundary:

## Validation

List exact commands and results. Include desktop and mobile screenshots for visible changes.

## Checklist

- [ ] Unknown tools remain denied by server-owned policy.
- [ ] Preparing a write has no external effect.
- [ ] Every write requires a current exact-action decision.
- [ ] Retry, idempotency, and `outcome_unknown` behavior remain safe.
- [ ] Logs, errors, checkpoints, tests, and screenshots contain no secrets or private data.
- [ ] Cypress was used only for React components and Playwright only for E2E.
- [ ] The nearest public guide was updated.
- [ ] Any remaining risk or rollback step is stated above.
