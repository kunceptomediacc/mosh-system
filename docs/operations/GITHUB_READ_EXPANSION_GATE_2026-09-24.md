# GitHub read expansion gate

Date: 2026-09-24  
Status: **COMPLETED — BOUNDED READ RECEIPT RECORDED**

## Proposed bounded capability

Authorize one metadata-only read against one additional, owner-selected GitHub repository.

- Agent: `cline` only
- Operation: `repository.metadata` only
- Connector-call limit: one
- Required verified MOSH scope: `metadata:read`
- File contents, pull requests, issues, branches, commits, identities, and settings: out of scope
- GitHub writes: prohibited
- Response content persistence: prohibited
- Repository grant: owner-specified expiry, no more than 30 days after creation
- Audit persistence: repository digest, operation, timestamps, and immutable bounded outcome only

The request must validate against `bridge/contracts/v1/github-read-expansion-request.schema.json`. The repository value must be supplied by the owner as an exact `owner/name`; this document does not invent or infer a target.

## Current local evidence

- GitHub registry state is recorded as `connected` and `healthy`, last checked on 2026-09-23.
- Verified MOSH grants are `metadata:read` and `contents:read`.
- `pull_requests:read` is requested but not verified and is excluded from this gate.
- Cline is the only allowed GitHub agent.
- One repository is currently allowlisted.
- Two read events exist: one completed and one pending legacy event.
- The governed adapter supports only `repository.metadata` and bounded `file.read`; it has no GitHub write path.

Recorded state is evidence, not a promise that the external connector remains available. Execution requires a fresh read-only health check.

## Required fresh owner authorization

Before execution, the owner must provide and approve all of the following in one instruction:

1. Exact repository in `owner/name` form.
2. A bounded purpose of at most 500 characters.
3. An exact grant-expiration timestamp within the next 30 days.
4. Authorization for exactly one external GitHub metadata read by Cline.

Approval of this packet does not authorize file reads, new OAuth scopes, connector reauthentication, GitHub writes, additional repositories, repeated calls, or credential changes.

## Execution sequence after approval

1. Validate the approved packet locally against the closed schema.
2. Perform a fresh read-only connector identity/installation health check without persisting returned identity.
3. Stop if `metadata:read` cannot be authoritatively verified.
4. Add only the approved repository to the local MOSH allowlist with the approved expiry.
5. Authorize exactly one `repository.metadata` audit event for Cline.
6. Perform one connector read.
7. Persist only a completed digest/count receipt or an allowlisted failure code.
8. Report the bounded outcome without repository content, identity, credential, or raw connector-error disclosure.

## Fail-closed stop conditions

Do not perform the connector read if any of these are true:

- the packet is missing, invalid, ambiguous, or names more than one repository;
- the expiry is absent, already elapsed, lacks a timezone, or exceeds 30 days;
- plugin state is not freshly connected and healthy;
- `metadata:read` is not freshly verified;
- the target repository differs from the exact approved value;
- the requested operation is anything other than `repository.metadata`;
- execution would require login, reauthentication, permission expansion, payment, or a write;
- the connector proposes more than one call or returns an unbounded payload;
- an audit event cannot be created before the external read.

## Deliberately deferred

- Pull-request discovery and `pull_requests:read` verification
- Additional file-content reads
- Codex or Hermes agent access
- Every GitHub write operation

Repository grants now support bounded expiry and reasoned owner revocation locally. No grant was created, renewed, or revoked while preparing this gate.

## Execution result

On 2026-09-29, the owner supplied and confirmed one contract-valid request with an exact repository, bounded
purpose, timezone-qualified expiry within 30 days, Cline as the agent, and a one-call limit. A fresh
credential-safe health check verified the required read capability before execution.

- Exactly one `repository.metadata` connector call completed.
- The expiring local grant was normalized to UTC.
- Audit event `3` has one immutable completed outcome.
- Only the response SHA-256, byte count, line count, and completion timestamp were persisted.
- Raw response content, repository identity, account identity, credentials, and connector output were not
  persisted in this evidence document or the outcome receipt.
- No file, issue, pull-request, branch, commit, settings, identity, permission, or write operation was performed.
- Post-operation SQLite integrity check returned `ok`.

Aggregate plugin activity after completion: two active repository grants, three authorized reads, two completed
outcomes, zero failed outcomes, and one pre-existing legacy pending event.
