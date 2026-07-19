# Model Compatibility

Relay keeps its Anthropic model operator-configurable because model availability and request
constraints change independently of the application.

## Current default

As of July 18, 2026, Relay defaults to:

```dotenv
ANTHROPIC_MODEL=claude-sonnet-5
```

Anthropic identifies `claude-sonnet-5` as the current Sonnet model and a drop-in migration from
Claude Sonnet 4.6. It supports tool use and structured output needed by Relay's live planner.

Official references:

- [What's new in Claude Sonnet 5](https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5)
- [Anthropic model IDs and versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
- [LangChain ChatAnthropic integration](https://docs.langchain.com/oss/python/integrations/chat/anthropic)

## Claude 3.5 Sonnet retirement

The original project concept named Claude 3.5 Sonnet. That model is no longer available through the
Anthropic API:

| Retired model ID | Retirement date | Anthropic replacement at retirement |
| --- | --- | --- |
| `claude-3-5-sonnet-20240620` | October 28, 2025 | `claude-sonnet-4-6` |
| `claude-3-5-sonnet-20241022` | October 28, 2025 | `claude-sonnet-4-6` |

Anthropic states that requests to the retired models return an error. Relay must not advertise a
Claude 3.5-backed live success or keep either retired identifier as a fallback. See
[Anthropic model deprecations](https://platform.claude.com/docs/en/docs/about-claude/model-deprecations).

## Sonnet 5 request constraints

Relay deliberately omits these settings for `claude-sonnet-5`:

- non-default `temperature`;
- non-default `top_p`;
- non-default `top_k`; and
- manual extended thinking such as `thinking={"type":"enabled","budget_tokens":...}`.

Anthropic documents that Sonnet 5 rejects non-default sampling parameters and manual extended
thinking with HTTP 400. Adaptive thinking is the supported thinking mode and is on by default.
`max_tokens` is a hard ceiling across thinking plus answer text, so a value tuned for an older model
may truncate output.

Relay's safe baseline is therefore equivalent to:

```python
ChatAnthropic(
    model=settings.anthropic_model,
    max_tokens=4096,
    timeout=settings.request_timeout_seconds,
    max_retries=1,
)
```

There is no explicit sampling parameter or manual thinking budget.

## What model selection can and cannot change

The model may produce only a structured plan that validates against Relay's action schemas. Model
selection cannot change:

- the registered tool set;
- a tool's risk classification;
- allowed decision types;
- payload canonicalization or hashing;
- approval expiry or current proposal version;
- executor selection;
- connector mode;
- idempotency/retry behavior; or
- audit requirements.

An unsupported or invalid model response fails planning. It never downgrades a write to a safe read
or bypasses approval.

## Change the model safely

Before changing `ANTHROPIC_MODEL`:

1. confirm that the exact model ID is active in Anthropic's current model and deprecation docs;
2. confirm tool use and structured output support in the current `langchain-anthropic` integration;
3. review context, output, thinking, sampling, and rate-limit constraints;
4. update only the uncommitted environment or deployment setting first;
5. run schema-validation and adversarial planner tests with captured, redacted outputs;
6. run the deterministic suite to confirm policy remains provider-independent;
7. perform an explicit live planning check without executing any write;
8. perform one approval-gated non-production connector workflow if authorized; and
9. record model-specific live evidence separately from deterministic and hosted evidence.

Do not use a dateless model identifier on the assumption that it automatically tracks the newest
model. Anthropic documents model IDs as distinct versions with their own lifecycle.

## Compatibility failure behavior

| Failure | Relay behavior |
| --- | --- |
| Missing Anthropic key in demo | Demo remains ready; no Anthropic call occurs |
| Missing Anthropic key in live planning | Runtime startup/doctor fails closed before a run is accepted |
| Retired/unknown model ID | Planner reports a sanitized connector error; no fallback |
| Structured output validation failure | Plan is rejected; no action proposal executes |
| Timeout known before response | Bounded planner retry may occur |
| Rate limit | Bounded backoff within configured budget, then explicit failure |
| Provider refusal | Planning fails safely; the refusal never becomes tool authorization |

Model call retries are separate from irreversible tool retries. A planning retry has no provider
side effect in Relay's tool adapters; an email/calendar/database submission follows the stricter
rules in [Safety](safety.md).

## Proof language

- A configured model name proves only configuration.
- `relay doctor` proves only non-secret readiness checks.
- A successful structured planning call proves access and response validation for that request.
- It does not prove search, email, calendar, record, or purchase execution.
- Deterministic demo success does not prove any Anthropic access.
- Local live-model success does not prove hosted or production behavior.

Record the exact model ID, date, package lock, validation scope, and provider boundary whenever
reporting live model proof.
