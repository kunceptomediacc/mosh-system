# ADR 0002: Controlled Memory and Skill Governance

Status: accepted, 2026-09-23.  
Decider: Joma (owner), with Codex implementation review.

## Context

Phase 4 introduces reusable memory and skills, which can compound value but can also persist secrets, misinformation, or unsafe procedures. Vector retrieval is derived and cannot replace authoritative provenance, validation, approvals, and retention state.

## Decision

- Structured MOSH state is authoritative; vector indexes are derived and deferred.
- Memory and skills use candidate, validation, and immutable digest-bound approval stages.
- Initial memory sources are restricted to MOSH-owned task/event evidence.
- Personal histories, external-agent memory, raw logs, credentials, and the permanent excluded path remain out of scope.
- Versioned JSON Schemas are the boundary contracts.

## Alternatives considered

- Embed first and govern later: rejected because secrets and unvalidated claims could become persistent retrieval data.
- Files-only memory: rejected because lifecycle, provenance, approvals, and concurrency require structured durable state.
- Vector store as source of truth: rejected because embeddings are lossy, model-dependent, and difficult to audit.

## Consequences

Memory promotion is slower and requires evidence, but approvals are auditable and cannot silently authorize modified content. Initial retrieval remains simple and rebuildable. Additional sources and embeddings require later explicit gates.
