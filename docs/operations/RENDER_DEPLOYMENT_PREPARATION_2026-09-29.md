# Render deployment preparation

Date: 2026-09-29
Status: **PREPARED — NOT DEPLOYED**

## Selected target

The owner selected Render with a provider-generated `onrender.com` HTTPS origin. A custom domain is not required.
No Render service, persistent disk, Google OAuth configuration, purchase, or deployment was created while
preparing this configuration.

## Prepared controls

- One containerized Python web service binds to Render's public port.
- One generated HTTPS origin serves the dashboard, Google sign-in, callback, and authenticated API.
- SQLite is restricted to `/var/data/mosh.db` on a single persistent disk and a single service instance.
- Container startup adjusts only the mounted `/var/data` directory, then drops from root to UID/GID 10001 before
  starting Python.
- `/healthz` exposes only `ok` or `degraded` and checks SQLite without requiring a credential.
- API, write, and Google client credentials are deployment secrets and are not present in `render.yaml`.
- Production session and CSRF cookies use `Secure`, `HttpOnly`, and `SameSite=Strict`.
- HSTS, CSP, frame denial, no-sniff, no-referrer, and no-store headers are applied.
- The local two-port loopback workflow remains available and unchanged.

## Deployment gate

Before the first deployment, the owner must:

1. Create or select a Render account and explicitly accept the paid web-service and persistent-disk cost.
2. Create the Blueprint from this repository and record the generated `https://*.onrender.com` origin.
3. Configure that origin in the Google Identity Services client as an authorized JavaScript origin.
4. Set `GOOGLE_CLIENT_ID`, `MOSH_API_TOKEN`, and `MOSH_WRITE_TOKEN` as Render secrets. Tokens must contain at
   least 32 random characters and must never be sent through chat or committed.
5. Perform the first deployment, grant the initial Google principal an owner role through an authenticated
   operator channel, and run the production security acceptance checklist.
6. Configure external uptime monitoring, backup export ownership, retention, and incident escalation.

Deployment, spending, OAuth changes, secret creation, and role grants remain separately authorized actions.
