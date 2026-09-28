# Throw It In — Acceptance Record

Date: 2026-09-23  
Scope: first authenticated dashboard mutation

## Safety contract

- `POST /api/v1/tasks` requires both the normal bearer token and a separate `X-MOSH-Write-Token`.
- The browser keeps the read token in session storage only and never stores the write token; the write field is cleared after a successful submission.
- The owner must select an explicit ready account. The server derives the provider from that account, preserving independent Codex accounts and preventing client-side provider substitution.
- Accepted input is allowlisted to `objective`, `account_id`, and `risk`.
- Objectives are bounded to 1–4000 characters and request bodies to 16 KiB.
- Every submitted task creates a queued run with mandatory pending owner approval.
- Submission never starts a worker and cannot approve, execute, cancel, or perform side effects.
- Authenticated submissions are limited to ten attempts per minute; invalid write tokens do not consume the quota.

## Evidence

- OpenAPI 3.1 documents the dual-token requirement and response codes.
- API regressions cover write authorization, explicit second-account routing, provider derivation, input bounds, unknown fields, malformed JSON, pending approval, and rate limiting.
- Browser acceptance covers the accessible form, separate write-token header, explicit account/risk selection, success announcement, token clearing, and refresh of the task list using a non-persistent fixture.
- Full Python suite: 46 tests passing.
- Browser acceptance: passing.

No real task was submitted during automated browser acceptance.

## Controlled live acceptance

Task `TASK-UI-20260923-074710-72E7C1` was submitted through the live endpoint to the explicitly selected `codex-work` account. It created one queued run and one pending approval. After explicit owner approval through the CLI, a single worker claimed the run, recorded redacted command evidence, and continuously renewed its lease.

The provider attempt eventually failed within the adapter's bounded execution window. MOSH persisted only `execution failed (RuntimeError)` and did not leak raw stderr. No duplicate or automatic retry was launched. A subsequent read-only health probe reported both `codex-personal` and `codex-work` authenticated and healthy, so the precise upstream failure remains unresolved.

Live verdict: **submission and approval safety passed; provider completion failed**. A fallback attempt on another account requires a new task and a new explicit approval.

The owner approved a separate fallback task, `TASK-UI-20260923-080656-B92C8F`, routed explicitly to `codex-personal`. It followed the same submission, approval, claim, command-evidence, and lease-renewal path, then ended with the same sanitized `execution failed (RuntimeError)` result. No third attempt was created.

Because both authenticated Codex accounts failed identically while account selection and durable orchestration behaved correctly, the remaining blocker is classified as a shared Codex execution-path defect rather than single-account dependence. Diagnosis must use a bounded, credential-safe probe before any retry.

## Diagnostic resolution and final pass

The credential-safe probe reproduced zero-output timeouts for both accounts inside the restricted command network sandbox. A direct control exposed TLS `UnknownIssuer` and connection failures on that restricted path. The same bounded probe, run with approved provider-network access, succeeded independently for both `codex-personal` and `codex-work` in approximately 11–14 seconds and returned the exact probe artifact.

This localized the prior failures to the diagnostic shell's restricted network context, not MOSH, the adapter contract, or either account.

Final live task `TASK-UI-20260923-082701-52593C` was then submitted through Throw It In to `codex-work`, explicitly approved, and executed once with the correct provider-network access. It reached `review`; its run reached `completed`; and the persisted result was exactly `MOSH_THROW_IT_IN_OK` with no error.

Final live verdict: **pass**. Both accounts passed independent bounded execution probes, and the approval-gated Throw It In path completed end to end on the explicitly selected work account.
