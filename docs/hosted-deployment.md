# Hosted deployment

> **Decision: do not operate a public hosted Relay instance from the current v0.1.0
> architecture.** v0.1.0 supports a local, loopback-only demo. It is not an internet-facing
> service.

Relay's approval controls prevent an agent from silently authorizing a write. They do not identify
the person using the API, separate different users' data, or make local SQLite safe for a shared
deployment.

## Why v0.1.0 is a no-go

| Current boundary | Source evidence | Public-hosting gap |
| --- | --- | --- |
| API routes are mounted without an authentication dependency | [`api/app.py`](../src/relay/api/app.py#L36-L43), [`api/routes.py`](../src/relay/api/routes.py#L29-L146) | Anyone who can reach the service can view workflows, create work, decide approvals, export audit data, or reset the demo. |
| Decisions contain no authenticated reviewer identity | [`models.py`](../src/relay/models.py#L278-L301), [`persistence.py`](../src/relay/persistence.py#L157-L168) | The generic `operator` audit actor cannot prove who approved an action. |
| Application records belong to one global workspace | [`persistence.py`](../src/relay/persistence.py#L113-L168) | There is no tenant key on records, queries, idempotency keys, or checkpoint namespaces. |
| Run serialization uses process and file locks | [`runtime.py`](../src/relay/runtime.py#L70-L100) | Multiple instances cannot safely coordinate one workflow. |
| LangGraph uses a SQLite checkpointer | [`runtime.py`](../src/relay/runtime.py#L573-L607) | Checkpoints are local to one filesystem and are not a production multi-host store. |
| Compose publishes only to loopback | [`compose.yaml`](../compose.yaml#L35-L36) | The loopback mapping is a required safety boundary, not an inconvenience to remove. |

Do not change the published mapping to a public interface, place the current container at a public
URL, or treat an access proxy alone as production authorization.

## Gate A: single-operator hosted demo

A private hosted demo is a separate future profile. Every item below must be implemented and tested
before a demo URL exists:

- deterministic demo adapters only; startup rejects live mode;
- one named operator, one workspace, one process, and disposable SQLite data;
- a signed session with `Secure`, `HttpOnly`, and `SameSite=Strict` cookie attributes;
- an exact HTTPS public origin and trusted-host allowlist, with TLS terminated by a trusted edge;
- same-origin validation for every mutation and no broad CORS grant;
- authentication on every data, workflow, approval, audit, reset, and OpenAPI route, leaving only a
  shallow health check and session entry point public;
- a reviewer identity derived by the server from the session and persisted with each decision and
  audit event; the browser must not supply or override it;
- generic, rate-limited login failures and bounded application resource limits; and
- fail-closed startup when the origin is not HTTPS, required secrets are missing, or the profile is
  configured for live providers.

This profile is suitable only for its one operator. Sharing one access code with unrelated visitors
does not provide identity, authorization, or data isolation.

## Gate B: production service

Production is not an expanded version of Gate A. It requires a separately designed and validated
deployment:

| Area | Production requirement |
| --- | --- |
| Identity | OIDC with MFA, authenticated reviewer identity, roles, and least-privilege authorization |
| Tenancy | A tenant ID on every record, query, idempotency key, audit event, and checkpoint namespace, with cross-tenant denial tests |
| Storage | A migrated Postgres application repository and production LangGraph Postgres checkpointer |
| Coordination | Distributed per-run serialization or compare-and-swap ownership across processes |
| Provider access | Managed secrets, narrow provider scopes, token refresh, egress controls, and provider-specific reconciliation |
| Network | TLS, explicit trusted-proxy handling, trusted hosts, exact origin policy, rate limits, queues, and backpressure |
| Operations | Structured redacted logs, metrics, traces, alerts, capacity tests, and on-call procedures |
| Audit | Export to separately administered storage that application operators cannot silently rewrite |
| Recovery | Schema migrations, encrypted backups, point-in-time recovery, restore drills, retention, deletion, and incident response |

Local tests, a green container health check, or a protected demo URL do not prove this gate.

## Owner decisions required

Before implementation or deployment, the owner must choose:

1. whether the intended audience is one owner, invited named users, or unrelated public visitors;
2. the hosting platform, region, domain, and TLS termination boundary;
3. the identity provider and allowed accounts when more than one operator is involved;
4. whether demo data is disposable and how often it is reset;
5. the retention and deletion policy for instructions, decisions, receipts, and audit records; and
6. the managed database, backup, and restore policy for any production service.

If unrelated public visitors are in scope, Gate A is not acceptable. Begin with OIDC and tenant
isolation, then complete Gate B before exposing the service.

## Proof order

Keep evidence separate and advance only in this order:

1. existing local and loopback-container proof;
2. hosted-demo authentication, origin, reviewer-identity, restart, and resource-limit tests;
3. provider-specific live acceptance using dedicated accounts, if explicitly authorized; and
4. production identity, tenancy, Postgres, failover, monitoring, backup, and incident-response proof.

Passing one layer never implies the next.
