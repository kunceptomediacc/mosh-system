# Phase 9 Design Gate — Video Factory

Status: **ACCEPTED — local production pipeline and preview proven; publishing remains gated**  
Date: 2026-09-23

## Core lifecycle

- Migration 24 adds durable Video Factory projects and ordered stages: inspiration → story → scene plan → media → narration → composition → render → review → publish.
- Every completed stage is bound to SHA-256 evidence and an actor. Stages cannot be skipped, repeated, or completed out of order.
- Review completion moves a project to review; only the owner can approve it using review evidence.
- The publish stage cannot be completed through the production API. It remains pending and requires a future, separate external side-effect approval and outcome receipt.
- Authenticated video status and the dashboard expose aggregate project/stage counts only.

## Remotion project

- Isolated project: `projects/experiments/video-factory`.
- Pinned Remotion toolchain: 4.0.527. The lockfile passed pnpm supply-chain policy; only the required esbuild install script was explicitly allowed.
- Typed props drive three scenes. `calculateMetadata` derives the 255-frame duration, subtracting two 15-frame transition overlaps.
- Scene animations are frame-derived and clamped. Transition sequences are premounted. The foundation preview uses no remote media, unlicensed assets, voice synthesis, music, or external service.
- TypeScript validation passes and Remotion enumerated `MoshVideo` at 1920×1080, 30 fps, 255 frames / 8.5 seconds.

## Evidence

- Local H.264 preview: `projects/experiments/video-factory/artifacts/preview.mp4`, 424.6 kB, SHA-256 `16b1e2b56a7de8916b94ded989e38411eecf650b8cdf82b875958f0568f2b99d`.
- Review frame: `projects/experiments/video-factory/artifacts/review-frame.png`, SHA-256 `01704eb09989f2e6a6278c09d6107c2753108c6121efdc67af903c4509b06b8a`.
- Live project `VID-EFB26B8E9C0445BFAA38FFC8819C33B4` is approved after all local production/review stages completed; its publish stage remains pending.
- Live database schema is version 24. Full core suite: 106 tests passing. Remotion typecheck, bundle, composition discovery, local render, visual-frame inspection, and dashboard browser/accessibility acceptance pass.

## Publishing boundary

No video was uploaded, shared, messaged, posted, or published. Any future destination must be explicitly selected and authorized, and the exact artifact/destination parameters must pass MOSH's expiring, single-consumption side-effect approval flow.
