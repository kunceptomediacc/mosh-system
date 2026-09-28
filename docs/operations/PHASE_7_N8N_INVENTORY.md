# Phase 7 n8n Interface Inventory

Date: 2026-09-23  
Mode: read-only observation

## Observed

- Container `n8n-local-n8n-1` is running image `n8nio/n8n:latest` and exposes local port 5678.
- Installed n8n reports version `2.36.7`.
- `GET http://127.0.0.1:5678/healthz` returned HTTP 200 with `{"status":"ok"}`.
- Container `n8n-local-postgres-1` is running PostgreSQL 18 and reported healthy.

No environment variables, credentials, stored workflows, execution data, or database contents were inspected.

## Supported MOSH surface

- Local loopback health probe only.
- The probe returns a bounded health result and exposes no response body, credential, workflow, or host details.

## Explicitly unsupported

- Workflow list/read/import/export.
- Workflow creation, update, activation, or deletion.
- Workflow execution or webhook invocation.
- Credential discovery or mutation.
- Remote n8n hosts.

These operations remain unsupported until the owner approves an authenticated, least-privilege interface and MOSH binds any external effects to its durable side-effect approval and outcome-receipt controls.
