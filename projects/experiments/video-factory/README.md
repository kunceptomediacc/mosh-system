# MOSH Video Factory

Phase 9's isolated Remotion project. It currently proves a typed, data-driven composition with deterministic duration, premounted scenes, clamped frame-based animation, and transitions whose overlap is included in duration calculation.

The project does not fetch remote media, synthesize narration, render automatically, or publish. Those actions enter the durable Video Factory stages only when their artifacts exist. Publishing remains a separate explicit side-effect approval.

Commands:

```text
pnpm install
pnpm typecheck
pnpm studio
pnpm render:preview
```

Rendered files belong under the workspace `artifacts/` area and must be hashed before their corresponding MOSH stage is completed.
