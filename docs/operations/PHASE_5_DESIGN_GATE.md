# Phase 5 Design Gate — Plugin Manager and GitHub First

Status: **ACCEPTED — credential-free registry slice implemented**  
Date: 2026-09-23

## Boundary

Phase 5 starts with an honest control plane, not an OAuth shortcut. MOSH may record plugin identity, provider, connection state, authentication mechanism, health, capabilities, requested/granted scopes, and allowed agents. It must not store access tokens, refresh tokens, client secrets, cookies, private keys, or resolved credential references in plugin registry rows or public API responses.

The boundary contract is `bridge/contracts/v1/plugin-registration.schema.json`. ADR 0003 records the decision.

## State semantics

- `unconfigured`: definition exists; no connection or authorization is claimed.
- `disconnected`: configuration exists but no usable connection is active.
- `connected`: an adapter has verified the connection using an approved credential channel.
- `degraded`: connection exists but health or capability checks are incomplete.
- `disabled`: operator-disabled; no invocation is allowed.

Health is separate: `unknown | healthy | degraded | unavailable`. Installation, connection, granted scope, health, allowed-agent access, and action approval are independent facts.

## GitHub-first policy

1. Begin with repository discovery/read context only.
2. Request minimum scopes progressively.
3. Grant agents individually; empty means no agent may invoke the plugin.
4. Creating branches, commits, issues, comments, pull requests, merges, releases, or repository settings changes are external side effects and require explicit approval.
5. Protected-branch changes and destructive repository actions remain high risk.
6. Connector installation or authentication requires a separate owner-directed step.

## First-slice verification

- Migration 14 adds plugin registration and per-agent access tables without credential columns or data backfill.
- New definitions default to `unconfigured`, unknown health, zero granted scopes, and zero allowed agents.
- Definition refresh cannot overwrite explicit connection or health state.
- Authenticated `GET /api/v1/plugins` exposes only the contract fields and always reports that writes require approval.
- The dashboard shows governed registry state separately from filesystem integration-area discovery.
- GitHub is not installed, authenticated, or granted scopes by this slice.

## Second-slice verification

- The owner installed and granted the GitHub connector through the Codex connector flow.
- Authenticated-user and installed-repository discovery probes completed through read-only connector operations.
- MOSH records the identity-layer connection as connected/healthy without storing the returned login or any credential material.
- Repository scope grants remain empty because the connector did not expose authoritative granted-scope evidence to this slice.
- Allowed agents remain empty, so MOSH does not yet authorize agent invocation.
- Connection checks accept only observed states and a unique subset of the definition's requested scopes; they cannot silently expand authority.
- A subsequent installed-account and repository-list probe verified `metadata:read`; file-content and write scopes remain unverified.

## Third-slice verification

- Cline is the first explicitly authorized MOSH agent for GitHub by owner direction.
- Agent authorization requires the owner, a registered agent, a connected/healthy plugin, and at least one verified scope.
- Cline inherits only the plugin's currently verified `metadata:read` scope.
- Codex and Hermes remain unauthorized. File-content access and every GitHub write remain unavailable through MOSH policy.

## Fourth-slice verification

- A bounded `README.md` fetch from an accessible repository verified `contents:read` without persisting repository content.
- Cline now has MOSH registry authorization for `metadata:read` and `contents:read` only.
- The upstream GitHub installation reports broader repository permissions. Those upstream permissions are not MOSH grants: no GitHub write adapter is implemented, and external writes remain prohibited until a separate digest-bound side-effect approval path exists.

## Fifth-slice verification

- Migration 15 adds an explicit repository allowlist and append-only GitHub read-request audit events.
- The first allowlisted repository is the single repository used for the successful bounded content probe; other accessible repositories remain denied by the MOSH gate.
- The governed adapter authorizes only `repository.metadata` and `file.read` for an explicitly allowed agent with matching verified scopes.
- Remote paths must be repository-relative POSIX paths; traversal, absolute paths, backslashes, unknown operations, and reads over 500 lines fail before an audit request is created.
- Audit rows contain SHA-256 hashes of repository and resource identifiers plus requested line bounds. They do not contain fetched content, credentials, or GitHub account identity.
- A live five-line `README.md` read succeeded after authorization and produced audit event 1. MOSH did not persist the returned content.
- Live database schema is version 15. Full automated suite: 81 tests passing.

## Sixth-slice verification

- Migration 16 adds immutable one-to-one outcome receipts without altering or backfilling prior audit events.
- A completed receipt requires a lowercase SHA-256 response digest and bounded byte/line counts; returned content is never stored.
- A failed receipt accepts only an allowlisted error code and cannot persist raw connector error text.
- Receipts are agent-bound and immutable: another agent cannot finish Cline's event, and a completed/failed event cannot be rewritten.
- A fresh live five-line read produced event 2 and an immutable completed receipt for 239 returned bytes and 5 lines.
- Full automated suite: 83 tests passing.

## Seventh-slice verification

- Authenticated `GET /api/v1/plugin-activity` returns only allowlisted-repository count, authorized/completed/failed/pending read totals, and the last activity timestamp.
- The response excludes repository names, paths, repository/resource/response hashes, returned content, agent IDs, and account identity.
- The dashboard renders the aggregate GitHub-read state with populated and empty fixtures.
- Live state reports one allowlisted repository, two authorized reads, one completed receipt, zero failures, and one pending legacy authorization created before outcome receipts existed.
- Full automated suite: 84 tests passing; browser visual, responsive, keyboard, accessibility, authentication, live API, plugin, and empty-state acceptance remain green.
