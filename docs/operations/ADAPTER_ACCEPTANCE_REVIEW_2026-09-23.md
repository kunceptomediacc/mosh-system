# Adapter Acceptance Review

Date: 2026-09-23  
Applies to: `ADAPTER_ACCEPTANCE_2026-09-23.md`

This dated addendum preserves the original acceptance record while correcting claims that were re-checked against the implementation, native CLI help, and persisted local evidence.

## Scope clarification

The initial adapter probe was read-only: it created isolated state and performed empty-history or session-list reads. The later account-aware acceptance phase did submit controlled prompts for both Cline and Hermes. Neither phase inspected credential values or accessed the excluded `D:\balot\thor\` tree.

## Corrections

### Hermes execution semantics

Hermes `--format stream-json` implies quiet, non-TTY execution and therefore one-shot session semantics. The `--oneshot` flag describes session lifecycle and is not independently proven to bypass approvals.

The enforceable MOSH controls are:

- `--safe-mode`
- `--max-turns 1`
- no `--yolo`
- a bounded, no-tool objective for controlled acceptance
- `--source tool` for programmatic MOSH sessions

`--yolo` remains prohibited because Hermes documents it as bypassing dangerous-command approval prompts.

### Session attribution

- `20260923_104040_274c4f` was the controlled pre-flight `MOSH_HERMES_OK` probe.
- `20260923_104315_fd5561` was the durable `MOSH_HERMES_DURABLE_OK` submission associated with `TASK-PH1-HERMES-001`.

### Runtime

The reproduce command took approximately 40 seconds on the observed workstation: two Cline history reads of roughly 12 seconds each and two Hermes session-list reads of roughly 6 seconds each. This is an observation, not a guaranteed performance bound.

## Defects and evidence gaps found during review

The original Windows process timeout killed only the direct child. A grandchild retaining captured pipes could keep the caller blocked beyond the configured timeout. The subsequent implementation slice launches Windows commands suspended, assigns the exact process to a kill-on-close Job Object before resuming it, and fails closed if isolation cannot be established. Regression coverage includes both descendant-pipe timeout cleanup and assignment failure before command execution.

The original task-run schema did not persist adapter argv evidence. The subsequent implementation slice records credential-safe, immutable command evidence per run attempt before execution. It stores centrally redacted argv, environment key names only, and a digest of the canonical redacted evidence; it never stores environment values, credential references, raw objective text, or raw stderr. These internal evidence fields are excluded from public task snapshots.

This addendum does not rewrite the original evidence. It documents corrections and the controls introduced afterward.
