# Browser session QA

Date: 2026-09-24  
Target: isolated loopback fixture using a temporary SQLite database and synthetic local identities

## Result

The real-browser session flow passed:

| Behavior | Evidence | Result |
|---|---|---|
| Session-based unlock | An `HttpOnly`, `SameSite=Strict` synthetic session cookie unlocked the dashboard without a bearer token | Pass |
| Account label | Header rendered `BROWSER QA OWNER · OWNER` | Pass |
| Logout | `Sign out` revoked the synthetic server-side session, expired the cookie, reloaded, and returned the UI to `LOCKED` | Pass |
| Expired session | A revoked synthetic session was rejected, its cookie was expired, the dashboard remained hidden, and the UI displayed `Session expired or unavailable; sign in again` | Pass |

No real Google account, owner session, credential, or production database was used. The fixture listened only on loopback and used a temporary database.

## Reusable fixture

Start the isolated fixture from the repository root:

```powershell
$env:PYTHONPATH = ".\.local\python-packages;.\apps\mosh-core"
python .\scripts\qa\Start-MoshSessionBrowserFixture.py --port 8892
```

Open `http://localhost:8892/qa/active` for the active-session path or `http://localhost:8892/qa/expired` for the expired-session path. `localhost` intentionally isolates the fixture cookie from live `127.0.0.1` MOSH cookies.

The automated core suite also covers session validation, logout revocation, expired-cookie clearing, safe session metadata, owner authorization, single-session revocation, and principal-wide revocation.
