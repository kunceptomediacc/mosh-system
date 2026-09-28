# n8n workflow discovery gate

Date: 2026-09-24  
Status: **COMPLETED — BOUNDED METADATA DISCOVERY ONLY**

## Live acceptance result

On 2026-09-28, owner-authorized request `N8ND-2E8D3286AE2C4DC3BA06CB60FD59268E`
completed one loopback `GET /api/v1/workflows` call with a 25-workflow limit and a 1,048,576-byte
transient ceiling. The bounded result recorded 17 workflows: 10 active and 7 inactive.

Persisted evidence contains only the aggregate counts, a combined workflow-identity SHA-256, the projected
response SHA-256, status, and timestamp. No workflow name, identifier, definition, node, parameter, credential,
execution record, webhook URL, API key, or response body was persisted or reported.

Earlier attempts remain immutable failure evidence for `credential_unavailable`, `health_failed`,
`response_oversized`, and `pagination_required`. None triggered a follow-up connector call.

## Proposed bounded capability

Perform one authenticated metadata-list call against the existing loopback-only n8n service.

- Base URL: `http://127.0.0.1:5678`
- Operation: `workflow.list_metadata`
- Connector-call limit: one
- Maximum returned workflows: owner-selected value from 1 through 25
- Response byte ceiling: owner-selected value from 1 through 1,048,576 bytes
- Allowed transient fields: workflow identifier, display name, active flag, and update timestamp only
- Persisted workflow identity: SHA-256 digests only
- Persisted result: aggregate count, active/inactive counts, response digest, and bounded outcome receipt
- Workflow definitions, nodes, parameters, expressions, tags, credential references, execution records, and webhook URLs: excluded
- Creation, update, import, activation, deactivation, deletion, execution, and webhook invocation: prohibited

The owner-approved request must validate against `bridge/contracts/v1/n8n-workflow-discovery-request.schema.json`.

## Current local evidence

- The existing `N8nReadOnlyAdapter` accepts local plain-HTTP loopback only.
- Its only supported capability is `health`; workflow list/read/write/execute remain unsupported in code.
- The last accepted inventory observed n8n 2.36.7 on port 5678 and a healthy PostgreSQL 18 container.
- The accepted health tool has effect class `none`.
- Workflow execution remains globally `disabled` in MOSH.

Fresh local package inspection on 2026-09-24 pinned n8n 2.36.7 to `GET /api/v1/workflows`, the
`X-N8N-API-KEY` header, required `workflow:list` scope, and the `{data, nextCursor}` response envelope.
This inspection read installed container files only; it made no n8n HTTP request and accessed no credential,
workflow, execution, or database data. Runtime health and authentication state remain unproven.

## Required fresh owner authorization

Before any discovery call, the owner must provide and approve:

1. A bounded purpose of at most 500 characters.
2. The exact maximum number of workflows to inspect, from 1 through 25.
3. Authorization for one authenticated metadata-list call against loopback n8n.
4. An exact response byte ceiling from 1 through 1,048,576 bytes.
5. A runtime credential channel that does not place a credential in the request packet, source tree, database, command line, logs, or chat.

This approval would not authorize reading an individual workflow definition, inspecting node configuration, accessing credentials or execution history, invoking webhooks, changing activation, or executing anything.

## Local implementation prerequisites

`N8nWorkflowDiscoveryAdapter` is implemented locally but has not been invoked against n8n. It keeps the
existing health-only adapter unchanged and enforces the following prerequisites in code:

1. Separate discovery adapter; the existing health-only adapter remains unchanged.
2. Route and response contract pinned from the installed n8n 2.36.7 package.
3. Runtime-only API-key argument injected through the single allowlisted header; errors are bounded and redacted.
4. Configurable response byte ceiling plus the 25-item ceiling.
5. Strict projection onto identifier, name, active flag, and update timestamp; output contains only counts and SHA-256 digests.
6. Pagination fails closed after the first call and can never trigger a follow-up connector call.
7. Tests prove workflow definitions, nodes, credentials, connections, webhook material, names, and identifiers cannot appear in adapter output.

Still required before execution:

The runtime credential channel is implemented but has not been invoked. `mosh-core n8n-discover` accepts only
an approved request identifier on the command line, performs health first, refuses redirected standard input,
and reads the API key through a hidden interactive terminal prompt. It does not accept the key through arguments,
environment variables, files, packets, logs, or database fields. The key reference is dropped immediately after
the single connector call, and command output contains only the immutable digest/count receipt.

Owner authorization is also available as a separate local-only command:

`mosh-core n8n-authorize-discovery --packet <request.json> --requested-by <actor> --approved-by owner`

The packet must be a regular non-symlink UTF-8 JSON file no larger than 8,192 bytes and must exactly match the
closed request contract. Authorization performs no health probe, prompt, credential access, or n8n call. The
packet remains under owner control; MOSH persists only its digest-bound authorization record.

Read-only inspection is available through `mosh-core n8n-discovery-status`. With no request identifier it
returns aggregate pending/completed/failed counts and the last activity timestamp. With `--request-id` it returns
only the digest-bound authorization and immutable outcome; raw purpose, workflow metadata, response content,
and credentials are never included. Status inspection performs no n8n call or health probe.

Schema v29 adds append-only `n8n_discovery_requests` and `n8n_discovery_outcomes`. Authorization stores
only the request digest, purpose digest, approved ceilings, actors, and timestamp. Each request accepts exactly
one immutable outcome: bounded aggregate counts and combined identity/response digests, or one allowlisted
failure code. No workflow name, identifier, definition, credential, response body, or raw purpose is stored.

## Execution sequence after implementation and approval

1. Validate the closed request packet locally.
2. Re-run the credential-free loopback health probe.
3. Inject the owner-provided runtime credential without logging or persistence.
4. Perform exactly one metadata-list request with the approved item limit.
5. Stop and fail closed if the response is oversized, paginated beyond the first page, malformed, or contains disallowed structures.
6. Project allowed metadata in memory, compute digests and aggregate counts, then discard the response.
7. Persist only the digest-bound outcome receipt.
8. Report aggregate counts and a bounded error code; do not report workflow names or IDs in chat.

## Fail-closed stop conditions

Do not perform discovery if:

- the request is absent, ambiguous, or invalid;
- n8n is not freshly healthy on exact loopback;
- the local API route and response shape have not been verified against the installed version;
- authentication requires login automation, credential creation, scope expansion, or secret persistence;
- the credential has more authority than the owner accepts for this probe;
- the response cannot be bounded to one call, 25 items, and the configured byte ceiling;
- any workflow definition, node, parameter, credential, execution, or webhook field would be persisted;
- any mutation, activation, execution, or webhook call is proposed.

## Deliberately deferred

- Individual workflow reads
- Workflow-definition import or export
- Node and credential inventory
- Execution history
- Activation changes
- Workflow mutation or deletion
- Manual, scheduled, or webhook execution
- Remote n8n hosts

No n8n request, credential access, workflow discovery, database inspection, mutation, or execution was performed while preparing this gate.
