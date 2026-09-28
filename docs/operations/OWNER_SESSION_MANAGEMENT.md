# Owner session management

Date: 2026-09-24

MOSH owners can inspect and revoke active dashboard sessions through the loopback API. These operations accept either an authenticated owner session or the local recovery bearer token.

## API operations

- `GET /api/v1/sessions` lists active sessions using safe metadata only: opaque session ID, principal ID, display alias, creation and expiration timestamps, and whether the session is current.
- `POST /api/v1/sessions/{session_id}/revoke` revokes another active session. The current session is rejected with `409`; use `/api/v1/logout` for it.
- `POST /api/v1/principals/{principal_id}/sessions/revoke-all` revokes all active sessions for one principal. When this includes the caller's session, MOSH also expires the browser cookie.

Token values and token digests are never returned. All mutations require an owner role, use exact database identifiers, and return `404` for absent or inactive targets. Non-owner sessions receive `403`.

These endpoints are local operational controls. They do not change roles, identity-provider configuration, credentials, or deployment settings.
