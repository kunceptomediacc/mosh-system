# GitHub repository grant lifecycle

Date: 2026-09-24  
Status: implemented locally; no external GitHub action performed

## Outcome

MOSH repository allowlist authority now uses an append-only grant ledger.

- Migration 27 creates `plugin_repository_access_grants` with optional expiry and immutable revocation evidence fields.
- Migration 28 separately backfills legacy allowlist rows as non-expiring grants without changing their authority.
- New grants may be permanent only when explicitly requested by local code, or may expire at an owner-supplied timezone-aware timestamp no more than 30 days in the future.
- Authorization ignores expired and revoked grants.
- Revocation requires the owner and a bounded reason; the original grant row remains as history.
- A repository can be regranted after expiry or revocation, producing a new grant row rather than rewriting history.
- Public plugin activity counts only distinct, currently active repositories and exposes no repository identity.

## Live migration evidence

- Pre-migration backup: `data/backups/mosh-20260924T053723290853Z.db`
- Backup SHA-256: `77e38ce5eb226e5b1f3a0437e8e1e9083b2f2f0422f9ef45ee04101cb020f26b`
- Backup verification: integrity verified, schema 26, 622,592 bytes, 43 tables
- Post-migration schema: 28
- Post-migration integrity: `ok`
- Legacy allowlist rows: 1
- Backfilled grant rows: 1

No existing grant was expired or revoked. No repository identity, credential, connector response, or file content was added to public API output.
