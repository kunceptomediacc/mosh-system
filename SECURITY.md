# Security policy

## Supported versions

MOSH is currently pre-1.0. Security fixes are applied to the latest commit on `main`; older snapshots are not maintained as supported release lines.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability, exposed credential, authentication weakness, approval bypass, path traversal, unsafe external effect, or private-data disclosure.

Use the repository's **Security** tab to submit a private vulnerability report through GitHub Security Advisories. Include:

- the affected component and commit;
- clear reproduction steps;
- expected and observed behavior;
- potential impact;
- any safe mitigation you have already tested.

Do not include live credentials or unrelated personal data. If a secret has been exposed, revoke or rotate it before sharing sanitized evidence.

## Security expectations

MOSH is designed to fail closed. Changes affecting authentication, authorization, durable approvals, credentials, filesystem boundaries, external integrations, or side effects should include negative-path and replay tests. External actions must remain explicitly scoped, auditable, and separately authorized.

The repository is not a production deployment. Operators remain responsible for production HTTPS, secure cookies, identity-provider configuration, monitoring, backup ownership, retention, and incident response.
