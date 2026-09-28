# Phase 0 Dependency and License Review

## Agent Conference application

Path: `D:\Workspace\projects\my-apps\agent-desktop-app`.

- Direct dependency: Electron `^27.3.11`.
- `npm audit --json` result: two high-severity vulnerable dependency groups (`electron` and transitive `extract-zip`).
- The audit includes path-traversal/arbitrary-write issues in `extract-zip` and numerous Electron advisories.
- The suggested secure Electron line is a semver-major upgrade, so remediation requires compatibility testing rather than an automatic update.
- The package lock hash was unchanged by the audit.
- No project-level `LICENSE`, `COPYING`, or `NOTICE` file was found.

Decision: **do not copy, package, or run the legacy desktop application as a MOSH component**. Reuse only reviewed architectural patterns until dependencies are upgraded, Electron hardening is verified, and ownership/license terms are documented.

## Local MCP servers

- Gmail and Calendar servers use broad unpinned minimum ranges for `mcp` and Google client/auth packages.
- Ollama MCP uses `mcp>=1.0.0` and `requests>=2.31.0`.
- Minimum-only ranges reduce reproducibility and allow unexpected future upgrades.
- Gmail/Calendar operations require separate OAuth review, least-privilege scopes, and explicit action approvals.

Decision: reference only. Before adoption, create locked dependencies, run vulnerability/license scans in an isolated environment, and test read-only capabilities before enabling writes.
