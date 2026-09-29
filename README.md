# MOSH — The Moshpit

MOSH is a local-first control surface for durable work across agents, tools, accounts, and projects.

The original Phase 0–9 local scope is implemented: the repository includes a Python core, SQLite-backed task and audit state, approval-gated side effects, a loopback-only API, an installable dashboard, identity sessions, service management, governed integrations, and a local Video Factory experiment.

Current checkpoint: **93% overall**, with the safe local scope complete and 140 applicable tests passing. The remaining work requires owner-selected production infrastructure, deployment configuration, or separately approved external publishing. See [the current completion checkpoint](docs/operations/PROJECT_COMPLETION_CHECKPOINT_2026-09-28.md).

## Non-negotiable safety boundary

`D:\balot\thor\` is excluded at scanner level. MOSH discovery must never enumerate, inspect, index, or copy it.

## License

MOSH is proprietary software distributed under the [MOSH License](LICENSE). No permission to use, copy, modify, or redistribute the software is granted without prior written permission.

## Two Codex accounts

MOSH never assumes one Codex identity. Initialize two isolated account homes, then sign in to each explicitly:

```powershell
.\scripts\setup\Initialize-CodexAccounts.ps1 -Aliases personal,work
.\scripts\setup\Start-CodexAccount.ps1 -Alias personal login
.\scripts\setup\Start-CodexAccount.ps1 -Alias work login
```

Run Codex with one account:

```powershell
.\scripts\setup\Start-CodexAccount.ps1 -Alias personal
```

Check every configured account without exposing credentials:

```powershell
.\scripts\setup\Get-CodexAccountStatus.ps1
```

Account state is stored under the ignored `.local\codex-accounts\` directory. Credentials are never committed or merged between accounts.

## Repository map

- `apps/mosh-core/` — durable core, API, worker, adapters, and tests
- `apps/mosh-ui/` — local dashboard and browser QA assets
- `bridge/contracts/v1/` — versioned JSON contracts
- `docs/architecture/` — architecture decisions
- `docs/operations/` — implementation and acceptance evidence
- `docs/security/` — dependency and security review notes
- `projects/experiments/video-factory/` — local media-production experiment
- `scripts/` — setup, discovery, health, QA, and service-management helpers

## Phase 1 core

Initialize and bootstrap the durable account/agent registry:

```powershell
$env:PYTHONPATH = (Resolve-Path '.\apps\mosh-core').Path
python -m mosh_core.cli --db '.\data\mosh.db' init
python -m mosh_core.cli --db '.\data\mosh.db' bootstrap
python -m mosh_core.cli --db '.\data\mosh.db' summary
python -m mosh_core.cli health
```

Codex submission is enabled only through the durable worker in an ephemeral read-only sandbox. The worker persists attempts, renews leases, recovers abandoned work, deduplicates completion, and supports task-level cooperative cancellation:

```powershell
python -m mosh_core.cli --db '.\data\mosh.db' cancel --task-id TASK-ID --reason 'operator requested stop'
```

Cline and Hermes submission are enabled only for explicitly bound isolated accounts. Cline always forces tool auto-approval off; Hermes always uses safe-mode streamed JSON and excludes one-shot/yolo modes. Streaming and adapter-level hard cancellation remain disabled pending capability-specific recovery tests.

Run the non-inference adapter acceptance probe:

```powershell
python -m mosh_core.cli adapter-acceptance --state-root '.\.local\adapter-acceptance-v1'
```

This verifies isolated state and restart readability without submitting prompts or reading credentials. See [docs/operations/ADAPTER_ACCEPTANCE_2026-09-23.md](docs/operations/ADAPTER_ACCEPTANCE_2026-09-23.md).

## Phase 2 approvals

Queue work that must not execute without an owner decision:

```powershell
python -m mosh_core.cli --db '.\data\mosh.db' enqueue --task-id TASK-ID --provider cline --account primary --risk medium --approval-required --objective 'Task objective'
python -m mosh_core.cli --db '.\data\mosh.db' approve --task-id TASK-ID --by owner --reason 'Approved scope and reason'
```

Pending approval-required runs are excluded by the worker's authoritative claim query. Rejections are durable and immutable. See [docs/operations/PHASE_2_STATUS.md](docs/operations/PHASE_2_STATUS.md).

External side effects use a separate single-use request. Approval is bound to the canonical digest of the exact action, target, and JSON parameters; changed parameters and replay are rejected. The registry currently authorizes and audits requests but does not perform external actions.

The first connected executor is `write_marker`, limited to new `.txt` files under `data/side-effects`. It atomically consumes approval, records a durable idempotency key, refuses overwrites/path traversal, and recovers a prepared execution by verifying the artifact hash.

Inspect and reconcile prepared executions:

```powershell
python -m mosh_core.cli --db '.\data\mosh.db' list-side-effect-executions
python -m mosh_core.cli --db '.\data\mosh.db' reconcile-local-markers --root '.\data\side-effects'
```

Reconciliation recreates missing approved markers, but never overwrites a mismatched artifact; mismatches become durable failures for inspection.

Marker cleanup is recoverable: `plan-marker-cleanup` creates a no-move dry run and pending exact approval, `execute-marker-cleanup` moves the verified artifact into `data/side-effects-trash`, and restoration requires its own separately approved request. Permanent deletion is not implemented.

Cleanup operations are durable and restart-reconcilable. New plans record retention metadata (30 days by default), and `list-cleanup-plans` plus `reconcile-marker-cleanup` expose inspection and recovery without deleting audit history.

## Phase 3 read-only API

Generate a local bearer-token file, then start the loopback-only API:

```powershell
python -m mosh_core.cli api-token-init --token-file '.\.local\api\token'
python -m mosh_core.cli --db '.\data\mosh.db' serve-readonly --port 8765 --token-file '.\.local\api\token'
```

Rotate the credential atomically without restarting the server:

```powershell
python -m mosh_core.cli api-token-init --token-file '.\.local\api\token' --rotate
```

The API exposes only authenticated GET endpoints under `/api/v1`; every mutation method returns 405. Task events are available through bounded cursor pagination at `/api/v1/tasks/{task_id}/events`. See [docs/operations/PHASE_3_STATUS.md](docs/operations/PHASE_3_STATUS.md) and [bridge/contracts/v1/openapi.json](bridge/contracts/v1/openapi.json).

Open `http://127.0.0.1:8765/` after starting the server to use the local read-only dashboard. Paste the token into its unlock panel; it is retained only for that browser tab. On Windows, copy the token without printing it:

```powershell
Get-Content '.\.local\api\token' -Raw | Set-Clipboard
```

Or launch the server, copy its token, and open the dashboard in one step:

```powershell
.\scripts\start\Start-MoshDashboard.ps1
```

The launcher creates the local token only when missing, starts a hidden loopback server, waits for readiness, copies the token to the clipboard, and opens the dashboard. Use `-NoBrowser` for automated acceptance runs.
