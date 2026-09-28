# ADR 0003: Credential-Free Plugin Control Plane

Status: accepted, 2026-09-23.  
Decider: Joma (owner), with Codex implementation review.

## Context

Phase 5 introduces connected services, beginning with GitHub. Connection metadata must be durable and inspectable without placing OAuth tokens, application secrets, or other credentials in ordinary MOSH state. Discovery of an available plugin must not be mistaken for installation, authentication, authorization, or health.

## Decision

- MOSH stores plugin definitions, connection state, health, declared capabilities, requested/granted scopes, and explicit agent access, but never credential material.
- New plugin definitions begin `unconfigured`, with unknown health, no granted scopes, and no allowed agents.
- Connection and authorization are separate future actions with explicit owner involvement.
- GitHub begins read-only. Every external write or destructive capability remains task/side-effect approval-gated.
- The first API surface is authenticated and read-only.

## Alternatives considered

- Store access tokens directly in SQLite: rejected because operational state is not an approved credential store.
- Treat an installed connector as connected: rejected because installation, authentication, scope grant, and health are distinct states.
- Grant all agents plugin access: rejected because plugin permissions must follow least privilege.

## Consequences

Phase 5 can expose honest connection readiness before any OAuth flow exists. Later connection adapters must integrate with an approved credential store and preserve the same state boundaries. More setup is required, but capability and authority cannot be silently conflated.
