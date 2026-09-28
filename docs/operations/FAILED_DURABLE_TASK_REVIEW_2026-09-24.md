# Failed durable task review

Date: 2026-09-24  
Scope: the three durable runs currently in `failed` state  
Method: read-only SQLite metadata, transition history, redacted failure classification, command/error digests, current registry status, and credential-free adapter acceptance

## Decision summary

| Task | Finding | Recommendation |
|---|---|---|
| `TASK-PH3-CLINE-READ-ADAPTER-ACCEPTANCE-001` | The provider returned an insufficient-credit error. The referenced acceptance outcome was already proven by the separate successful durable Cline task documented in the objective itself. | **Retain as failure evidence. Do not retry.** It is useful evidence that provider billing failures are durably recorded, while retrying an already-satisfied readback objective would spend external credits without adding acceptance value. |
| `TASK-UI-20260923-074710-72E7C1` | Codex UI smoke probe on `codex-work`; one approved attempt, repeated lease renewals, no result, and a redacted `RuntimeError`. | **Retain as canonical failure evidence. Do not retry.** It represents the first of two equivalent cross-account runtime failures and preserves the approved command digest and lease history. |
| `TASK-UI-20260923-080656-B92C8F` | Duplicate Codex UI smoke probe on `codex-personal`; same objective, command digest, error digest, and redacted error class as the preceding work-account probe. | **Archive when a non-destructive archive operation exists. Do not retry.** Until then, leave it untouched. It adds little diagnostic value beyond proving the issue was not isolated to one account. |

## Evidence

- All three runs completed exactly one attempt and produced no result payload.
- The Cline error was a provider credit-balance failure, not a MOSH state, lease, or parsing failure.
- The two Codex tasks had the same bounded objective: return the exact UI smoke-test marker without tools or file access.
- The two Codex runs share command SHA-256 `2c66597ee96c51064078ee02fb5fbbbbc119fb757a30c26f984db9edae16d56d`.
- Their stored redacted errors share SHA-256 `eaf4d418dc3fa40b4fabb0a847efb887f25ba9448f462dbfe53540e4d9defb28`.
- Both Codex runs renewed their leases repeatedly before failing, so they are not abandoned-lease recovery cases.
- Current account and agent registry rows remain `ready`; this is registry configuration state, not proof that external credentials, quota, or provider availability are currently usable.
- A fresh credential-free adapter acceptance probe confirmed isolated state and restart readability. It intentionally left submission disabled because its fresh state contained no provider credentials.
- The separate cancelled task `TASK-PH1-CANCEL-001` was operator-cancelled before execution, contains no run error, and is outside this failed-run review.

## Action boundary

This review made no task, run, approval, account, credential, billing, or provider changes. MOSH currently has no evidenced non-destructive archive operation for durable tasks, so the archive recommendation is advisory only. Retrying any provider task requires a fresh owner decision after verifying account availability and expected external cost.
