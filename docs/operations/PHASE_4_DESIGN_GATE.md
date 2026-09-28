# Phase 4 Design Gate — Controlled Memory/RAG and Skill Forge

Status: **ACCEPTED — first governed persistence slice implemented**  
Date: 2026-09-23

## Outcome

Phase 4 begins contract-first. No existing personal history, external-agent memory, credential store, raw log, chat archive, or arbitrary filesystem content may be scanned or ingested. No embeddings or vector database are added until the owner accepts this gate.

The authoritative contracts are:

- `bridge/contracts/v1/memory-record.schema.json`
- `bridge/contracts/v1/skill-candidate.schema.json`

The schemas are boundary contracts, not database-row exports. Consumer fixtures and future provider serialization must validate against these same files.

## Storage roles

- SQLite/PostgreSQL-compatible structured storage is authoritative for memory identity, scope, lifecycle, provenance, classification, validation, approvals, hashes, and retention.
- Git/files are authoritative for approved skill procedures and their tests.
- A future vector index is derived retrieval data only. It never becomes authoritative and must be rebuildable from approved, redacted source records.
- Redis is unnecessary for the initial slice.

## Memory scopes

- Task memory: bounded facts needed by one durable task.
- Project memory: validated facts reusable inside one project.
- MOSH memory: validated, broadly reusable facts approved for the workstation.
- Active prompt context is ephemeral and is not a stored memory tier.
- Skills are governed separately because they are procedures, not facts.

## Lifecycle and gates

Memory transition:

`candidate → validated → approved → expired`

Alternate terminal transition:

`candidate | validated → rejected`

Skill transition:

`candidate → validated → approved → retired`

Alternate terminal transition:

`candidate | validated → rejected`

Rules:

1. Creation produces `candidate` only.
2. Validation requires evidence and records a method; it does not imply approval.
3. Promotion across scopes, embedding, or reuse as authoritative context requires `approved` status.
4. Memory approval and skill approval are immutable, digest-bound decisions referencing the exact content/procedure hash.
5. Changes after approval create a new candidate/version and require new validation and approval.
6. High/critical-risk skills always require explicit owner approval. Shared `core` and `business` skills require owner approval at every risk level.
7. Rejected, expired, and retired records are not retrieved into prompts.
8. Deletion/retention operations remain separate, explicitly approved cleanup actions.

## Redaction and classification boundary

The ingestion pipeline must reject before persistence or embedding:

- passwords, bearer tokens, API/OAuth keys, cookies, private keys, recovery codes, and credential references that resolve to secret material
- `.env` content and browser/password-manager stores
- raw command output and unrestricted raw logs
- personal chat/history archives without a source-specific owner approval
- paths or records under the permanent exclusion `D:\balot\thor\`
- content lacking a bounded, allowlisted source and provenance reference

Redaction happens before hashing the persisted summary and before any embedding request. A positive allowlist defines supported sources; pattern matching alone is not considered sufficient secret detection. Restricted records are never embedded in the initial implementation.

## First implementation slice

The smallest authorized implementation after owner acceptance should include:

1. migrations for memory candidates, validation evidence, immutable approvals, and skill candidates
2. pure lifecycle services with fail-closed transition checks
3. a deterministic redaction/rejection interface with an allowlisted task/event source only
4. no vector database; retrieval initially uses approved structured records
5. read-only API metadata views only, with content retrieval remaining excluded
6. contract-valid fixtures shared by provider and consumer tests

## Required acceptance tests before embeddings

- invalid or additional contract fields fail validation
- secrets and credential-shaped fixtures are rejected before persistence
- the permanent excluded path is rejected at source selection, before traversal
- unapproved records cannot be retrieved, promoted, or embedded
- validation cannot approve a record
- approval is bound to content SHA-256 and cannot authorize modified content
- scope promotion creates a new auditable record rather than mutating provenance
- restricted classification cannot enter the embedding queue
- rejected/expired memory and rejected/retired skills are excluded from retrieval
- approved skills require evidence; high/critical and shared skills require owner approval
- vector loss can be rebuilt without losing authoritative state
- public API responses contain no raw memory content, procedure content, credentials, or filesystem paths
- restart recovery preserves lifecycle state and pending approvals

## Decisions requiring owner acceptance

1. Accept structured state as authoritative and defer the vector database until approved retrieval works.
2. Accept the proposed lifecycle and immutable digest-bound approval model.
3. Accept initial source allowlisting to MOSH-owned task/event evidence only.
4. Accept that personal/external histories remain out of scope unless separately approved per source.
5. Accept the two versioned JSON Schemas as the Phase 4 boundary contracts.

The owner accepted these decisions by directing MOSH to proceed. ADR 0002 records the decision. Migration 11 and the first fail-closed governance services implement candidate creation, memory validation, digest-bound immutable memory approval, and bounded skill-candidate creation. Embeddings, retrieval, content APIs, and external-source ingestion remain unimplemented.

## First-slice verification

- Live authoritative database migrated forward to schema version 11 with zero memory records automatically ingested.
- Secret-shaped summaries, non-allowlisted sources, missing task/event provenance, and the permanent excluded path fail before insertion.
- Memory cannot request approval before validation.
- A decided approval cannot be rewritten.
- Modified content cannot inherit a pending approval bound to the earlier SHA-256.
- Skill candidates require evidence, a 64-character digest, and a traversal-safe path beneath `skills/`.
- Full automated suite: 60 tests passing.

## Second-slice verification

- Skill validation requires at least one durable test identifier before approval can be requested.
- Agent/project low- and medium-risk skill approvals may be handled by the requesting supervisor; `core`, `business`, `high`, and `critical` skills require the owner to request and decide approval.
- Skill approvals are immutable and bound to the exact procedure SHA-256; modified procedures cannot inherit a pending approval.
- Authenticated `GET /api/v1/learning-status` exposes lifecycle counts and pending approval totals only. It excludes summaries, skill names, procedure paths, scope IDs, evidence IDs, reasons, and content.
- Embeddings remain explicitly reported as disabled with zero indexed records.
- The dashboard renders governed Memory/Skill promotion metadata with populated and empty acceptance fixtures.
- Full automated suite: 64 tests passing; browser responsive, keyboard, accessibility, authentication, and live API acceptance remain green.

## Third-slice verification

- Ten explicit CLI commands cover create, validate, request approval, decide, and inspect for governed memory and skills.
- Approval decisions require an exact approval ID, explicit approved/rejected choice, actor, and reason; no bulk or automatic approval command exists.
- CLI failures are machine-readable and return exit code 2 without inserting rejected content.
- End-to-end CLI tests cover memory approval, owner-gated high-risk core-skill approval, local inspection, and credential-shaped rejection before persistence.
- Full automated suite: 67 tests passing.

## Fourth-slice verification

- Structured retrieval returns only approved, unexpired records matching one exact scope, scope ID, and classification; it has a hard limit of 1–100.
- Confidential and restricted retrieval requires the owner. MOSH-wide retrieval rejects a scope ID, while task/project retrieval requires one.
- Explicit memory expiration and skill retirement require actor and reason and write separate immutable lifecycle events bound to the approved content/procedure digest.
- Shared/high-risk skills require the owner for retirement.
- Live authoritative database migrated forward to schema version 12 with zero automatic memory or skill ingestion.
- Full automated suite: 69 tests passing.

## Fifth-slice verification

- Task-memory binding is explicit and accepts only an existing approved, unexpired memory record plus an existing task, actor, and reason.
- Each binding records the approved content SHA-256. Binding inspection returns metadata only and never returns the memory summary or source reference.
- Scope compatibility is fail-closed: task memory matches only its task, project memory remains inside its project, and MOSH memory may bind globally.
- Confidential and restricted memory binding requires the owner.
- Expiration does not erase binding history; the binding remains visible but is reported unusable.
- The aggregate learning-status view reports total and currently usable bindings without exposing task IDs or memory IDs.
- Automatic retrieval, prompt injection, embeddings, and external-source ingestion remain disabled.
- Live authoritative database migrated forward to schema version 13 with zero task-memory bindings created automatically.
- Full automated suite: 71 tests passing; browser responsive, visual, keyboard, accessibility, authentication, live API, learning-status, and empty-state acceptance remain green.
