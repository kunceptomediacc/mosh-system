# Identity-enabled local startup

Date: 2026-09-23

MOSH uses two loopback services:

- Dashboard and API: `http://127.0.0.1:8889/`
- Google identity callback: `http://127.0.0.1:1455/`

Manage both services together with PID-scoped local state:

```powershell
& .\scripts\start\Manage-MoshServices.ps1 start -NoBrowser
& .\scripts\start\Manage-MoshServices.ps1 status
& .\scripts\start\Manage-MoshServices.ps1 restart -NoBrowser
& .\scripts\start\Manage-MoshServices.ps1 stop
```

The manager records only the PID, executable path, process start time, port, and timestamps under the gitignored `.local\services` directory. Before stopping a process, it requires the PID, executable path, and start time to match. Missing or mismatched ownership data fails closed, so an unmanaged process is never stopped. Service stdout and stderr are redirected to separate files under `.local\logs` so launch commands return without retaining their shell's output handles.

The individual launchers remain available when separate service control is needed. Processes started directly with them are intentionally not adopted by the manager.

Start the dashboard:

```powershell
& .\scripts\start\Start-MoshDashboard.ps1 -NoBrowser
```

Start Google identity:

```powershell
& .\scripts\start\Start-MoshIdentity.ps1
```

Verify both services and SQLite without exposing credentials:

```powershell
& .\scripts\health\Test-MoshServices.ps1
```

Expected health output includes `"ok":true`, dashboard and identity `ok`, database integrity `ok`, and schema version 26.

Both services bind only to `127.0.0.1`. Google client configuration and local API tokens remain in the gitignored `.local` tree. The startup scripts fail closed if required configuration is absent or a requested port is already occupied.
