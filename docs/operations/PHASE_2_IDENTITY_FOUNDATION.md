# Phase 2 Identity Foundation

Status: **ACCEPTED — live loopback Google identity and revocable dashboard sessions implemented**  
Date: 2026-09-23

This document supplements the existing Phase 2 approval/side-effect status and restores the identity scope named in the original master handoff.

## Implemented

- Migration 25 adds identity providers, digest-bound external subjects, principals, and owner-granted workspace roles.
- Provider types are `local_dev`, `google_oidc`, and generic `oidc`.
- Workspace roles are Owner, Admin, Developer, Operator, Client, and Viewer/Auditor.
- Local identity has a fixed `mosh://local-development` issuer, cannot carry a client ID, is permanently marked development-only, and fails closed unless `MOSH_ENV=development` is explicit.
- External providers require HTTPS issuers and store only the SHA-256 of the client ID.
- Google Sign-In authorization uses OpenID Connect scopes, random state and nonce, PKCE S256, and `prompt=select_account` so multiple Google accounts are not silently collapsed.
- Redirects are limited to HTTPS or local loopback HTTP. The PKCE verifier is never placed in the authorization URL.
- Google-service authorization remains separate; Gmail, Drive, Sheets, Calendar, and similar scopes are not requested by sign-in.
- Authenticated identity status and the dashboard expose aggregate provider, principal, and role counts only.

## Live state

- The local development provider and one owner principal/role are registered.
- Two distinct Google principals are registered and each has an explicit Owner role; no role was granted automatically during sign-in.
- Google Identity Services verifies ID tokens with the maintained `google-auth` library, exact audience and issuer checks, verified email enforcement, and a bounded 60-second clock tolerance justified by measured local drift.
- Migration 26 adds eight-hour server-side identity sessions. Only SHA-256 token digests are stored; the browser receives an opaque `HttpOnly`, `SameSite=Strict` loopback cookie.
- Dashboard APIs accept a valid role-bearing Google session. Logout revokes the server-side session and expires the cookie. The prior bearer-token mechanism remains a local recovery path.
- Role permissions are deny-by-default. Identity management and publish requests are reserved to Owner; unknown roles receive no permissions.
- Live database schema is version 26. Full core suite: 115 tests passing; live browser acceptance confirmed automatic cookie authentication at `127.0.0.1:8889`.

## Remaining deployment gate

The current configuration is loopback-only. Any non-loopback or public deployment still requires HTTPS, production cookie hardening (`Secure`), explicit origin/redirect review, deployment-specific session storage/rotation policy, and a fresh security acceptance pass.
