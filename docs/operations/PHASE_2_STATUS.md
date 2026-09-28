# Phase 2 Status

Status: core approval, side-effect, recovery, and reversible-cleanup scope complete, 2026-09-23.

## Implemented

- Authoritative versioned JSON Schema for approval records.
- Durable SQLite approval records with scope, requester, decision, decider, reason, and timestamps.
- One immutable `execute` decision per task: `pending`, `approved`, or `rejected`.
- Worker claim gate enforced inside the authoritative query. Approval-required queued runs cannot be claimed without an approved record.
- Rejection moves a queued task to `rejected` and its queued run to `cancelled`.
- Approval request and decision events in the task audit stream.
- CLI support: `--approval-required`, `--risk`, `approve`, and `reject`.
- Task inspection includes approvals alongside runs and events.
- Live acceptance `TASK-PH2-APPROVAL-001`: worker refused pending work, owner approval was recorded, and the same run completed with exact result `MOSH_APPROVAL_GATE_OK`.
- Versioned side-effect request contract with action, target, JSON parameters, risk, expiry, and canonical SHA-256 digest.
- Approval decisions bind to the exact request digest; changing the target or parameters invalidates the approval.
- Single-use atomic consumption with replay rejection, expiry enforcement, durable rejection, and audit events.
- Live non-transmitting acceptance request `SFX-761fdcbc-42b9-4a44-970c-ff5ff9c070ef`: exact digest approved, consumed once, and replay rejected.
- Versioned side-effect execution contract with a durable idempotency key, prepared/completed state, artifact path, and content hash.
- Sandboxed `write_marker` executor limited to basename-only `.txt` files beneath `data/side-effects`, with a 4 KiB UTF-8 content limit and no overwrite behavior.
- Atomic approval consumption plus execution preparation, followed by hash-verified crash recovery if the process stops before completion is recorded.
- Live execution `SFXE-9ffbadda-5808-4829-9925-bf7327b3bff8`: created `phase2-local-marker.txt` with exact content `MOSH_LOCAL_EFFECT_OK`; retry returned the same completed execution and SHA-256.
- Reconciliation for all `prepared` local executions, with list and recover-all CLI commands.
- Missing artifacts are recreated only from the already-approved request and then hash-verified.
- Existing mismatched artifacts are never overwritten; the execution enters durable `failed` state with an error and failure timestamp.
- Live reconciliation reported no stranded prepared executions after migration, confirming the steady-state path.
- Versioned cleanup-plan contract with exact source, trash destination, artifact hash, plan digest, and planned/trashed/restored lifecycle.
- Dry-run planning performs no move and creates a pending digest-bound `trash_marker` request.
- Cleanup moves only a verified marker into `data/side-effects-trash`; permanent deletion is unsupported.
- Restoration requires a separate `restore_marker` approval with a different digest and verifies the trash artifact hash.
- Live plan `CLN-9eef2e4b-99b0-4649-a736-7a1413743b64`: marker moved to MOSH trash, restored under separate approval, and verified as `MOSH_LOCAL_EFFECT_OK`.
- Durable cleanup-operation records with prepared/completed/failed state around both trash and restore moves.
- Atomic approval consumption plus cleanup-operation preparation, enabling restart reconciliation without reusing an approval.
- Reconciler completes a move from verified source/trash path state or records a durable failure without overwriting artifacts.
- Cleanup-plan inspection includes operations and retention metadata; new plans default to a 30-day retention window.
- Simulated crash after move but before completion passed recovery; live reconciliation found no stranded cleanup operations.

## Deliberately limited

Only the dedicated local marker executor is connected. It cannot overwrite files or escape `data/side-effects`. No email, message, deployment, payment, general filesystem, or credential mutation adapter is connected. Each future executor must atomically consume a matching approval immediately before its external action. Adapters still exclude known approval-bypass modes.

## Beyond the completed core scope

Permanent deletion, financial actions, messaging, deployments, general filesystem access, and credential mutations remain disabled. Each is a separate future capability requiring its own contract, sandbox, approval policy, idempotency mechanism, and acceptance evidence; none is required to consider the current Phase 0–2 core complete.
