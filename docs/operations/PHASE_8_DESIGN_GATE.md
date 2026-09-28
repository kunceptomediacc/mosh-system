# Phase 8 Design Gate — Desktop Packaging

Status: **ACCEPTED — dependency-free installable app shell implemented**  
Date: 2026-09-23

## Decision

MOSH keeps the stable local web/service architecture and adds an installable progressive-web-app shell instead of introducing an Electron/Tauri runtime. This provides standalone-window installation without duplicating the server, database, authentication, or approval logic.

## Verification

- The dashboard publishes an explicit web manifest, standalone display metadata, theme metadata, and a local SVG application icon.
- A same-origin service worker caches only five static shell assets.
- `/api/` traffic is always network-only. API responses, bearer/write tokens, task data, approvals, plugin data, and other runtime records are never placed in the service-worker cache.
- Cross-origin and non-GET requests are ignored by the service worker.
- The dashboard still stores the read token in tab-scoped session storage and never stores the write token.
- Static routes remain allowlisted by the local server; traversal paths are rejected.
- Live checks returned HTTP 200 with correct manifest and JavaScript media types and `Cache-Control: no-store`.
- Full automated suite: 104 tests passing. Responsive visual baselines, keyboard navigation, authentication states, and accessibility acceptance remain green.

## Deferred by design

- Native installers, auto-update, protocol handlers, OS credential vault integration, and code signing are not justified yet.
- A native shell can be reconsidered only when a verified requirement cannot be met by the local installable web shell.
