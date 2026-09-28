# Phase 3 Status

Status: **COMPLETE** — local API/dashboard and approval-gated task submission accepted, 2026-09-23. See `PHASE_3_CLOSEOUT_2026-09-23.md`.

## Implemented

- Authoritative OpenAPI 3.1 contract under `bridge/contracts/v1/openapi.json`.
- Loopback-only bind on `127.0.0.1`; callers cannot select an external interface.
- Bearer authentication required for every endpoint, with a generated token kept in a local file rather than command history.
- Atomic token rotation with live reload; a running server rejects the previous token immediately.
- GET-only `/api/v1` resources for health, repository summary, paginated tasks, bounded event history, task snapshots, and cleanup plans.
- Consistent `{data}` success envelopes and semantic error envelopes/status codes.
- Task pagination constrained to 1–100 records with non-negative offsets.
- Task events use an `after_event_id` cursor and a 1–100 record limit; snapshots do not embed an unbounded event list.
- No-store response policy and no server logging of requests or authorization headers.
- Every non-GET method returns `405 Method Not Allowed` with `Allow: GET`.
- Full automated suite: 35 tests passing.
- Live acceptance: authenticated access returned 200, atomic rotation completed while the server remained running, the old token returned 401, and the new token returned 200.
- Dependency-free local dashboard served from the same loopback origin, with no mutation controls.
- Dashboard views for repository metrics, paginated tasks, task details, cursor-paginated events, and cleanup plans.
- Browser-session-only token storage, an allowlisted asset surface, no-store headers, MIME sniffing protection, and a restrictive content-security policy.
- Dashboard acceptance: HTML and JavaScript returned 200, CSP was present, and unauthenticated API access remained 401.
- Redacted agent and account health endpoints; both Codex accounts are represented independently without credential references or provider metadata.
- Paginated approval and side-effect inspection endpoints; side-effect targets, parameters, and request digests are excluded.
- Dashboard roster, approval queue, and side-effect queue views.
- One-command Windows launcher that creates a missing token, starts the loopback server hidden, waits for readiness, copies the token, and opens the dashboard.
- Repeatable Playwright acceptance journey covering invalid authentication, authenticated load, metrics, roster rendering, and task-detail selection.
- Full automated suite: 43 Python tests plus JavaScript and PowerShell syntax validation.
- MOSH-local Cline CLI 3.0.63 installed and authenticated after isolating it from broken global shims.
- Cline 3.x event-stream compatibility added to the adapter, with explicit UTF-8 subprocess decoding on Windows.
- First durable Cline task `TASK-PH3-CLINE-UI-PLAN-001` completed through the MOSH worker and reached review with its result and lease-renewal history preserved.
- Adapter acceptance corrections are preserved in `ADAPTER_ACCEPTANCE_REVIEW_2026-09-23.md`; the original record remains intact.
- Windows adapter execution is assigned to a kill-on-close Job Object before it is allowed to run; isolation setup failures are fail-closed and timeout cleanup covers descendant processes.
- Every adapter run records centrally redacted canonical command evidence before execution, while raw secrets, environment values, stderr, and internal command evidence remain absent from public task snapshots.
- Committed locked-state visual baselines cover 375 px, 768 px, and 1440 px viewports with pixel comparison and failure artifacts.
- Automated accessibility acceptance covers control names, landmarks, minimum target size, visible focus, skip navigation, and non-color-only status labels; the manual screen-reader follow-up is recorded in `UI_QA_2026-09-23.md`.
- Deterministic browser fixtures cover multi-page task navigation, cursor-paginated event history, final-page controls, and seeded empty states across every dashboard collection.
- Successful authentication now moves focus to the revealed dashboard; deterministic keyboard navigation and authenticated accessibility-tree structure are covered in browser acceptance.
- The owner-assisted Windows Narrator journey completed without a reported issue; verbose task announcements were bounded before final acceptance.
- Throw It In provides the first narrowly scoped HTTP mutation: dual-token authenticated task submission with explicit account selection, bounded input, server-derived provider routing, mandatory pending approval, and no automatic worker execution.
- Submission is rate-limited to ten authenticated attempts per minute; malformed input and unknown fields fail closed. Acceptance evidence is recorded in `THROW_IT_IN_ACCEPTANCE_2026-09-23.md`.
- Full automated suite: 46 Python tests plus browser, JavaScript, OpenAPI JSON, and PowerShell syntax validation.
- Controlled live Throw It In submission and CLI approval gates passed on `codex-work`; the bounded provider run failed with sanitized evidence despite both Codex accounts subsequently reporting authenticated and healthy. No automatic retry occurred.
- A separately approved `codex-personal` fallback followed the same durable path and failed identically. Both accounts were exercised independently; further retries are paused pending credential-safe diagnosis of the shared Codex execution path.
- Credential-safe diagnosis proved the failures were caused by the restricted command network sandbox: both accounts passed independently with approved provider-network access. Final task `TASK-UI-20260923-082701-52593C` completed on `codex-work` with exact result `MOSH_THROW_IT_IN_OK` and reached review.
- Read-only project overview is derived from authoritative durable tasks and exposes only project IDs, operational task counts, and last activity. The live database currently reports `MOSH-INBOX` and `MOSH-CORE` independently.
- Full automated suite: 47 Python tests; browser acceptance includes populated and empty project states.
- Files/Git workspace health is restricted to allowlisted top-level areas and summarized Git counts. It exposes no absolute paths, filenames, file contents, diffs, hidden directories, dependency trees, caches, credentials, or repository metadata contents.
- Live workspace status reports Git metadata as parked rather than silently treating the workspace as a clean active repository.
- Full automated suite: 48 Python tests; browser acceptance includes Files/Git workspace health.
- Plugins/Tools now provides a read-only capability inventory: aggregate registered/ready agent counts, capability labels with agent counts, and entry counts for four fixed integration areas. Plugin identities, filenames, paths, configuration, manifests, credentials, and file contents are excluded.
- Concurrent dashboard reads no longer attempt to reset an already-active SQLite WAL mode on every connection, preventing parallel startup requests from failing under load.
- Full automated suite: 49 Python tests; browser acceptance includes populated and empty Plugins/Tools states across mobile, tablet, and desktop baselines.
- Memory now reports the health of MOSH's durable operational store, schema version, and aggregate counts/latest timestamps for task context, execution history, audit events, approvals, side effects, and cleanup. It never scans external agent memory and exposes no objectives, results, event payloads, approval reasons, targets, parameters, paths, or private metadata.
- Full automated suite: 50 Python tests; browser acceptance includes populated and empty Memory states plus the live authenticated view.
- Logs now summarizes the authoritative durable audit table into fixed task, run, approval, side-effect, cleanup, and other streams, with aggregate failure/cancellation/recovery signals. Raw messages, reasons, payloads, objectives, results, errors, command output, paths, and filesystem logs remain excluded.
- Full automated suite: 51 Python tests; browser acceptance includes populated and empty Logs states plus the live authenticated view.
- System Status now provides one aggregate readiness view for durable storage, agents, accounts, active/attention work, and pending approval/side-effect gates. Routing resilience is a first-class component and requires at least two ready accounts; one ready account cannot produce an operational status.
- Full automated suite: 52 Python tests; browser acceptance includes operational and limited System Status states plus the live authenticated view.

## Deliberately excluded

Task submission is the only HTTP mutation. HTTP approval decisions, worker execution, cancellation, side effects, cleanup, credential changes, and filesystem operations remain excluded. The API does not expose credentials, account metadata, database paths, or raw SQL rows. It is not installed as a background service.

## Next phase

1. Prepare the Phase 4 design gate for controlled Memory/RAG and Skill Forge.
2. Keep approval decisions, execution, side effects, credentials, and ingestion on explicitly reviewed paths.
