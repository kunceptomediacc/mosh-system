# Cline and Hermes Adapter Acceptance

> Review note: later verification found corrections to Hermes one-shot semantics, session attribution, Windows timeout behavior, and durable command evidence. See [ADAPTER_ACCEPTANCE_REVIEW_2026-09-23.md](ADAPTER_ACCEPTANCE_REVIEW_2026-09-23.md).

Date: 2026-09-23

## Scope

This acceptance pass used fresh MOSH-owned state beneath `.local/adapter-acceptance-v1`. It did not submit a prompt, inspect credential files, reuse a user session, or scan the excluded `D:\balot\thor\` tree.

## Observed

- Cline accepts an isolated `--config` directory, creates a SQLite session store, and reads it successfully after process restart.
- Cline CLI task mode defaults to auto-approval. A future MOSH submit adapter must explicitly pass `--auto-approve false`.
- Hermes honors an isolated `HERMES_HOME`, creates its state tree, and reads the session store successfully after process restart.
- Hermes documents its session store as SQLite and exposes explicit resume/recovery operations.
- Hermes `--oneshot` bypasses approvals, and `--yolo` bypasses dangerous-command prompts. Neither mode is permitted for a MOSH mutation-capable adapter.
- Hermes `chat --safe-mode --format stream-json` is the candidate protocol for a later controlled submission test.

## Gate result

Persistence and restart readability: **pass** for both adapters.

Account-aware Cline submission: **pass**. The isolated state was authenticated through Cline's native device flow, bound to the opaque MOSH account `cline-primary`, and verified by a no-tool inference followed by restart-visible history.

Durable Cline acceptance task `TASK-PH1-CLINE-001` completed through the MOSH worker with exact persisted result `MOSH_CLINE_DURABLE_OK` and advanced to `review`.

Account-aware Hermes submission: **pass**. The isolated state was authenticated through OpenAI's native device flow, bound to opaque MOSH account `hermes-primary`, and verified using safe-mode streamed JSON without one-shot or yolo modes.

Hermes session `20260923_104040_274c4f` remained visible after process restart. Durable acceptance task `TASK-PH1-HERMES-001` then completed through the MOSH worker with exact persisted result `MOSH_HERMES_DURABLE_OK` and advanced to `review`.

## Reproduce

```powershell
$env:PYTHONPATH = (Resolve-Path '.\apps\mosh-core').Path
python -m mosh_core.cli adapter-acceptance --state-root '.\.local\adapter-acceptance-v1'
```

The command performs only empty-history/session reads in isolated state and reports required and forbidden execution flags.
