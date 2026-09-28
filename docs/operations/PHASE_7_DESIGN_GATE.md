# Phase 7 Design Gate — Workflows, MCP, Automation, and Patterns

Status: **ACCEPTED — governed local pattern/tool foundation implemented; effectful execution remains gated**  
Date: 2026-09-23

## Boundary

Phase 7 begins with reusable definitions, review, and evidence. A registered or approved pattern is not authorization to execute it. External messages, publishing, deployment, spending, credential or permission changes, destructive actions, and other external effects remain subject to their existing explicit approval gates.

## First-slice verification

- Migration 21 adds immutable workflow-pattern candidates sourced from local procedures, skills, plugins, MCP, or n8n.
- Definitions are stored by SHA-256 digest with bounded names, risk, source, creator, and an explicit external-effects flag; raw workflow definitions are not copied into the database.
- Only the owner can approve or reject a candidate, using SHA-256 evidence. Decisions are one-time and immutable.
- Approval makes a pattern reusable metadata; it does not execute the pattern. The Phase 7 execution state is explicitly `disabled`.
- Authenticated `GET /api/v1/workflow-status` and the dashboard expose aggregate lifecycle/source counts and the number of externally gated patterns without names, actors, or definition/evidence digests.
- Live pattern `WFP-59BD0FD37FD24FA2B845D6E885399561` records the already-tested local backup verification procedure as approved and non-external.
- Live database schema is version 21. Full automated suite: 97 tests passing; responsive browser, keyboard, visual-baseline, authentication, and accessibility acceptance remain green.

## Second-slice verification

- Migration 22 adds deterministic workflow plans bound to the approved pattern digest and a SHA-256 input digest.
- Candidate or rejected patterns cannot be planned. Approved non-external patterns become `ready`; patterns marked as externally effectful become `approval_required`.
- Plans contain no execution transition or adapter invocation. The existing durable side-effect approval model remains the required future bridge for effectful work.
- Aggregate plan counts are exposed in the authenticated API and dashboard without plan IDs, input digests, pattern names, or actors.
- Live plan `WPL-E4455C551AEF40A4B9D836D091DCC6EA` binds the approved local backup-check pattern to the live-database input digest and is ready for local use only.
- Live database schema is version 22. Full automated suite: 99 tests passing; responsive browser, keyboard, visual-baseline, authentication, and accessibility acceptance remain green.

## Third-slice verification

- Read-only inventory confirmed local n8n 2.36.7 is healthy on loopback port 5678 with its PostgreSQL 18 container healthy.
- The inventory deliberately excluded environment variables, credentials, workflows, execution records, and database contents.
- `N8nReadOnlyAdapter` is restricted to local plain-HTTP loopback, rejects embedded credentials and remote hosts, reads at most 1 KiB from `/healthz`, and returns only a bounded health result.
- Its capability contract supports `health` only and explicitly declares workflow list/read/write/execute unsupported with no external effects.
- Live health probing and mocked contract tests pass. Full automated suite: 101 tests passing.

## Fourth-slice verification

- Migration 23 adds a protocol-neutral automation-tool registry for local, MCP, and HTTP capabilities.
- Every observation is descriptor-digest-bound and classified as `none`, `read`, `write`, or `destructive` before owner review.
- Owner approval accepts registry metadata only; it does not expose an invocation method, enable workflow execution, or bypass side-effect approval.
- Authenticated workflow status and the dashboard expose aggregate review/effect counts without tool names, descriptors, evidence, or actors.
- Live tool `ATL-0DF9502AF1244B329CBBE48815F678B5` records the tested n8n loopback health capability as approved with effect class `none`.
- Live database schema is version 23. Full automated suite: 103 tests passing; responsive browser, keyboard, visual-baseline, authentication, and accessibility acceptance remain green.

## Next gates

- Add execution envelopes that reuse the durable task and side-effect approval model.
- Inventory n8n/MCP interfaces read-only before enabling adapters.
- Require per-capability allowlists and outcome receipts before any effectful execution path is enabled.
