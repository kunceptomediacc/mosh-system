# ADR 0001: Begin Phase 1 with JSON Schemas and SQLite

Status: accepted, 2026-09-23.

## Context

MOSH needs a durable bridge proof before a large UI or distributed infrastructure. Multiple adapters must agree on task, account, agent, and capability shapes. Tasks must survive process restarts, and two Codex accounts must remain distinct.

## Decision

- Versioned JSON Schemas in `bridge/contracts/v1/` are authoritative.
- Python standard-library models and runtime checks implement those contracts.
- SQLite with WAL, full synchronous writes, foreign keys, migrations, events, and optimistic task versions is the Phase 1 authoritative store.
- Adapters expose only `health()` and `capabilities()` initially.
- Codex routing always carries both provider and opaque account ID.

## Consequences

This avoids premature PostgreSQL/Redis operations while proving durability. A later PostgreSQL migration must preserve contract-visible semantics and task/event history. Submit/cancel/result operations remain disabled until adapter-specific recovery tests pass.
