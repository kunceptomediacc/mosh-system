# Phase 3 Closeout

Date: 2026-09-23  
Decision: **CLOSE — Phase 3 local control surface accepted**

## Scope accepted

The Moshpit UI now covers every Phase 3 surface named by the master handoff:

- Throw It In with a separate write token, explicit account selection, mandatory pending owner approval, and no automatic worker start
- project overview
- agent and account roster
- durable tasks, details, runs, approvals, and cursor-paginated audit history
- system health and explicit two-account routing resilience
- allowlisted Files/Git health
- aggregate Plugins/Tools capability inventory
- durable operational-memory metadata
- categorized audit-log signals
- cleanup and side-effect inspection

## Completion evidence

- 52 Python tests pass.
- OpenAPI 3.1 parses and documents all 16 implemented route patterns.
- Browser acceptance passes against deterministic populated/empty fixtures and the live authenticated API.
- Locked visual baselines pass at 375 px, 768 px, and 1440 px.
- Keyboard focus order, skip navigation, accessible names, minimum control targets, non-color-only status, and the browser accessibility tree pass.
- The owner-assisted Windows Narrator journey is recorded as accepted.
- The server is fixed to `127.0.0.1`, requires bearer authentication, returns `Cache-Control: no-store`, and serves only allowlisted assets.
- Public inspection endpoints exclude credentials, account metadata, raw event payloads, task results/errors, command output, side-effect parameters, absolute paths, filenames, file contents, and raw filesystem logs.
- System operational status requires durable-storage integrity, at least one ready agent, and at least two ready accounts. A single account cannot satisfy routing resilience.
- JavaScript, OpenAPI JSON, and Windows launcher syntax checks pass.
- Delivery-gate disk headroom is healthy at approximately 810 GB free on `D:`.

## Accepted boundaries

Task submission remains the only HTTP mutation. Approval decisions, worker execution, cancellation, side effects, cleanup, credentials, permissions, and filesystem changes stay on audited non-HTTP paths. The dashboard remains a local loopback web application and is not a background service or packaged desktop application.

Phase 4 semantic Memory/RAG and Skill Forge are not implemented by this Phase 3 metadata view. Plugin installation/authorization belongs to Phase 5. Desktop packaging belongs to Phase 8.

## Next authorized planning target

Prepare the Phase 4 design gate for controlled Memory/RAG and Skill Forge. Do not ingest existing personal histories, credentials, raw logs, or external agent memory. Begin with schemas, promotion states, redaction rules, approval boundaries, and acceptance tests before adding embeddings or a vector database.
