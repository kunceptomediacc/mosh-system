# Phase 1 Status

Status: complete; Phase 2 execution approvals started, 2026-09-23.

## Implemented

- Authoritative versioned JSON Schemas for accounts, agents, tasks, runs, events, and adapter capabilities.
- SQLite authoritative state with WAL, full synchronous writes, foreign keys, migrations, task events, and optimistic task versions.
- Account-aware task validation: provider and account must be supplied together, exist, match, and be ready.
- Explicit lifecycle transition enforcement.
- Restart recovery proof: a claimed task and its event history survive repository close/reopen.
- Read-only Codex, Cline, and Hermes adapters.
- Two distinct Codex account probes using isolated `CODEX_HOME` values.
- Concurrent health probes so one slow provider does not serialize the entire health check.
- Durable run attempts with worker leases, abandonment, retry creation, and audit events.
- Codex-only ephemeral read-only task execution with persisted results.
- Manual account rerouting for non-active tasks, including an immutable audit event.
- Live acceptance task `TASK-PH1-SMOKE-001`: queued on `personal`, explicitly rerouted to `work`, completed by the worker, exact result persisted, task advanced to `review`.
- Real process-death recovery proof: an expired lease left by a terminated subprocess is abandoned and replaced with attempt 2.
- Automatic lease heartbeats during slow execution, with ownership and expiry checks on every renewal.
- Idempotent run completion using a unique completion key, preventing duplicate completion events.
- Task-level cancellation for queued and active work. Active-task results are discarded when cancellation is observed after execution.
- Live queued-cancellation acceptance task `TASK-PH1-CANCEL-001`: cancelled before claim and verified never to execute.
- Repeatable Cline/Hermes acceptance probe using fresh MOSH-owned state.
- Cline isolated SQLite persistence and restart readability verified; its unsafe-for-MOSH default auto-approval behavior is recorded.
- Hermes isolated state and restart readability verified; one-shot and yolo approval bypass modes are explicitly barred from a future submit adapter.
- Cline isolated state authenticated through its native device flow and registered as explicit account `cline-primary`; credentials remain outside MOSH payloads and logs.
- Cline account-bound submit/collect enabled with JSON result parsing and `--auto-approve false` forced by the adapter.
- Live durable Cline task `TASK-PH1-CLINE-001`: completed with exact result `MOSH_CLINE_DURABLE_OK`, persisted with a completion key, and advanced to `review`.
- Hermes isolated state authenticated through OpenAI's native device flow and registered as explicit account `hermes-primary`.
- Hermes account-bound submit/collect enabled through safe-mode streamed JSON; one-shot and yolo remain structurally excluded.
- Live durable Hermes task `TASK-PH1-HERMES-001`: completed with exact result `MOSH_HERMES_DURABLE_OK`, persisted with a completion key, and advanced to `review`.

## Deliberately disabled

Codex, Cline, and Hermes advertise synchronous `submit` and `collect_result` through the durable worker. Task-level cooperative cancellation is available through MOSH, but adapters do not yet advertise hard process cancellation. Public streaming operations remain disabled. No automatic account failover is implemented.

## Next acceptance slice

1. Define the Phase 2 approval policy for tool-bearing tasks and external side effects.
2. Add durable approval records and owner decision events before enabling any mutation-capable task.
3. Add adapter-level hard cancellation only when the underlying process can be stopped safely and its terminal state is durable.
4. Keep account selection explicit; add automatic failover only behind a policy contract and audit trail.
