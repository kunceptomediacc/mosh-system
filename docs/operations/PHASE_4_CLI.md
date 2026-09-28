# Phase 4 Governed CLI

All commands require `--db <path>` before the subcommand and emit JSON. Governance failures return exit code 2 with `{ "ok": false, "error": "..." }`.

## Memory workflow

1. `memory-create` — requires explicit ID, scope, classification, summary, source kind, and source ID.
2. `memory-validate` — requires an explicit method and one or more repeated `--evidence` values.
3. `memory-request-approval` — creates one pending approval bound to the current content SHA-256.
4. `memory-decide` — requires approval ID, `approved|rejected`, actor, and reason.
5. `memory-inspect` — returns the local governed record for operator review.
6. `memory-retrieve` — returns approved, unexpired records for one exact scope and classification; confidential/restricted retrieval requires the owner.
7. `memory-expire` — moves one approved record to terminal `expired` state and writes a digest-bound audit event.
8. `task-memory-bind` — explicitly binds one approved, unexpired memory digest to an existing compatible task; requires actor and reason.
9. `task-memory-list` — lists binding metadata and current usability without exposing memory summaries or source content.

Task-scoped memory may bind only to its task; project-scoped memory may bind only inside its project; MOSH-scoped memory may bind globally. Confidential and restricted bindings require `--by owner`. Bindings remain audit-visible after memory expiration, but are reported unusable.

## Skill workflow

1. `skill-create` — requires explicit identity, scope, risk, safe `skills/` procedure reference, SHA-256, and evidence.
2. `skill-validate` — requires one or more repeated `--test` values.
3. `skill-request-approval` — creates a pending approval bound to the procedure SHA-256.
4. `skill-decide` — requires approval ID, `approved|rejected`, actor, and reason.
5. `skill-inspect` — returns the local governed candidate for operator review.
6. `skill-retire` — moves one approved skill to terminal `retired` state and writes a digest-bound audit event; shared/high-risk retirement requires the owner.

`core`, `business`, `high`, and `critical` skill approvals require `--by owner` at request and decision time. There is no bulk approval, default approval, interactive prompt, HTTP mutation, embedding command, external-source ingestion, automatic promotion, automatic retrieval, or prompt injection.

Use `python -m mosh_core.cli <subcommand> --help` for exact flags. Do not place secrets in command arguments; credential-shaped memory summaries are rejected, but shell history is still not an approved secret channel.
