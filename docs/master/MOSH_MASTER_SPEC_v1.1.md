# MOSH Master Specification v1.1

Status: Phase 0 working specification, 2026-09-23.

## Mission

MOSH is the durable local-first system above changing agents, providers, tools, and accounts. Tasks and evidence survive agent or UI restarts; the owner remains the approval authority.

## Core principles

- Complexity must earn its place.
- Discover and verify native interfaces before custom integration.
- Agents are replaceable; task state is not.
- Secrets never enter Git, RAG, ordinary logs, prompts, or task payloads.
- High-risk and external actions require explicit human approval.
- `D:\balot\thor\` is excluded from all discovery at scanner level.

## Multi-account requirement

MOSH must support at least two accounts per provider, including Codex/OpenAI, from initial setup.

An account record contains:

```text
account_id (opaque MOSH ID)
provider
alias (human-readable, unique within provider)
credential_ref (never the credential itself)
workspace/organization metadata when available
capabilities and rate-limit state
enabled/disabled status
last verified timestamp
```

Rules:

1. Every routed task records the selected provider and account ID.
2. Account choice is explicit or policy-driven and auditable.
3. No task silently switches accounts after submission.
4. Failover requires a declared policy; cross-account failover defaults to owner approval.
5. Histories, credentials, usage limits, and account-scoped configuration remain isolated.
6. Logging may show aliases and opaque IDs, never tokens.
7. Setup cannot mark Codex ready until both configured accounts pass independent login/status verification.

For local Codex CLI usage, separate `CODEX_HOME` directories are the credential isolation boundary. Config profiles may specialize settings inside an account but are not used to collapse two authentications into one store.

## Observed agent and tool baseline

- Codex CLI `0.155.0-alpha.9.2`.
- Cline CLI `3.0.61` with provider/model selection, ACP, hub, JSON, worktrees, isolated state, and MCP.
- Hermes `0.21.3` with profiles, pooled auth, fallbacks, gateway, ACP, MCP, server/headless operation, and durable sessions.
- Julia exists as a Python personal-operations automation but is unsafe to reuse unmodified because its scanner lacks the mandatory exclusion and its workflows use broad Google access.
- Joma Project Lead exists as a supervised Ollama-hosted specialist package.
- The existing Agent Conference application defines Oreo (coding), JessieJay (UI/UX), Mercedes (infrastructure), Ab (QA), and Julia (orchestration). Its source maps all roles to the same undifferentiated `codex` provider, confirming the need for account-aware routing.
- Both isolated Codex account homes passed independent ephemeral read-only inference tests.
- Cline's hub and Hermes's gateways are currently stopped. Hermes provides loopback server, ACP, and MCP modes, but its current log path is not writable from the restricted runtime identity.
- The Agent Conference Electron 27 dependency tree has high-severity audit findings and no observed project license file; its runtime is barred from reuse until remediated.

## Bridge contract (Phase 1)

```text
health()
capabilities()
submit(task)
status(task_id)
stream(task_id)
cancel(task_id)
collect_result(task_id)
```

Each operation advertises whether it is supported by the underlying agent. Account context is mandatory for account-backed operations.

## Durable task lifecycle

`draft → queued → claimed → running → review → completed`

Exceptional states: `blocked | failed | cancelled | rejected`.

Persist the task envelope, selected agent, selected account, attempts, approvals, events, and artifacts. An offline worker must never silently lose work.

## Phase gates

Phase 0 evidence is sufficient to begin narrowly scoped Phase 1 foundation work. Remaining operational uncertainties become explicit adapter acceptance tests: Cline provider behavior, Hermes sandbox-compatible logging, task persistence, restart recovery, and account-aware failure handling. Phase 1 begins with read-only adapter capabilities and durable state, then adds mutation only behind tests and approvals.

Phase 2 begins with a durable `execute` approval gate. Approval-required runs remain queued and unclaimable until an immutable owner decision is present. This task-level decision does not authorize arbitrary tool calls or external side effects; those require a later request-specific contract bound to exact parameters.

Phase 2 core completion includes digest-bound single-use side-effect requests, a sandboxed local marker executor, durable crash reconciliation, and separately approved reversible cleanup. Higher-risk external capabilities are excluded from the core and require independent future gates.

Phase 3 begins with an authenticated, loopback-only, read-only control API. Its contract is OpenAPI 3.1, all routes are versioned under `/api/v1`, and mutation methods are rejected until a separate write-API approval model is accepted.

## Evidence labels

- **Observed:** listed in the environment and behavior reports.
- **Recommended:** account-isolated Codex homes and native adapter reuse.
- **Observed:** Cline isolated SQLite state and Hermes isolated state are readable across process restart. Cline defaults to auto-approval; Hermes one-shot and yolo modes bypass approval gates and are prohibited for mutation-capable MOSH adapters.
- **Observed:** isolated Cline and Hermes accounts passed controlled inference, restart-visible native session persistence, and durable MOSH worker completion with approval-bypass modes excluded.
- **Unresolved:** Phase 2 policy and durable owner approvals for tool-bearing or externally mutating tasks.
- **Blocked:** credential-bearing assets cannot be adopted without owner approval and remediation; the legacy Agent Conference runtime cannot be adopted until dependency and license gates pass.
