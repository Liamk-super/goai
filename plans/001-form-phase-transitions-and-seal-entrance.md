# 001 — Animate form phase transitions and the planned-state seal entrance

- **Status**: TODO
- **Commit**: f39f6c1
- **Severity**: MEDIUM
- **Category**: Missed opportunities (teleporting state; rare delight)
- **Estimated scope**: 2 files (1 CSS, 1 TSX), small

## Problem

In `apps/web/src/app/(workspace)/projects/[projectId]/new-evaluation/page.tsx`, the
four phases of the intake flow (`collect` → `review` → `questions` → `planned`) are
conditionally rendered blocks. When the phase changes, the entire block unmounts and
the next one mounts instantly — content teleports with no bridge. This is the most
frequently hit seam in the app: every version submission passes through 2–4 phase
switches.

Two spots are involved:

1. Phase blocks swap with no entrance (jarring change):
   - collect: `page.tsx:357` — `{phase === "collect" && (<>…</>)}`
   - review: `page.tsx:454` — `{phase === "review" && (<>…</>)}`
   - questions: `page.tsx:489` — `{phase === "questions" && (<>…</>)}`
   - planned: `page.tsx:523` — `{phase === "planned" && (<div className="planned-state">…</div>)}`

2. The success seal (`.seal`, the `✓` medallion) in the `planned` phase appears
   statically — a rare, high-emotion moment rendered flat. Per the delight budget,
   this is the one place a small entrance is allowed.

Current relevant code:

```tsx
// new-evaluation/page.tsx:454 — review block (same pattern at 357, 489, 523)
{phase === "review" && (
  <>
    <div className="log-sheet-head">…</div>
    <div className="profile-review">…</div>
    …
  </>
)}
```

```tsx
// new-evaluation/page.tsx:523-524 — planned block
{phase === "planned" && (
  <div className="planned-state">
    <span className="seal" aria-hidden="true">✓</span>
```

The `.enters` class already exists (`globals.css:1344-1346`) but uses
`var(--dur-stage)` (620ms) — too slow for a form-phase switch. A phase change should
feel like a page turn inside an instrument: quick, firm, no bounce.

## Target

Phase blocks enter with the repo's existing `make-way` keyframes but at `--dur-move`
(280ms) + `--ease-far`. The seal enters separately with a scale-in at the same
duration.

```css
/* target — globals.css, appended near .enters (after line 1346) */
.phase-pane {
  animation: make-way var(--dur-move) var(--ease-far) both;
}
.planned-state .seal {
  animation: seal-in var(--dur-move) var(--ease-far) both;
}
@keyframes seal-in {
  from {
    opacity: 0;
    transform: scale(0.9);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}
```

Rationale for values:
- `--dur-move` (280ms) and `--ease-far` come from the repo's own tokens
  (`globals.css:119-122`) — not introduced, reused.
- `scale(0.9)` not `scale(0)` (AUDIT §3: nothing appears from nothing).
- No bounce: this is a crisp instrument, not a playful consumer app. Reserve bounce.
- Reduced motion is already globally handled: `@media (prefers-reduced-motion: reduce)`
  at `globals.css:127-139` zeroes all `--dur-*` and forces `animation-duration: 0.001s`,
  so both animations become near-instant while opacity still lands at 1. No extra CSS
  needed.

## Repo conventions to follow

- Easing/duration tokens already exist in `apps/web/src/app/(workspace)/globals.css:117-124`
  (`--dur-micro`, `--dur-move`, `--dur-stage`, `--ease-out`, `--ease-far`, `--ease-detent`).
  Reuse them; do not add new curves.
- The existing entrance pattern is `.enters` + `@keyframes make-way`
  (`globals.css:1333-1346`). This plan extends that pattern with a faster variant.
- Exemplar: `PageHeader` uses `className="page-head enters"` (`AppShell.tsx:112`).

## Steps

1. In `apps/web/src/app/(workspace)/globals.css`, after the `.enters` block (line 1346),
   append the `.phase-pane`, `.planned-state .seal`, and `@keyframes seal-in` rules
   exactly as in **Target**.

2. In `apps/web/src/app/(workspace)/projects/[projectId]/new-evaluation/page.tsx`,
   wrap each of the four phase blocks in a `<div className="phase-pane">`:
   - `page.tsx:357` collect block: change `{phase === "collect" && (<>` to
     `{phase === "collect" && (<div className="phase-pane">` and close the matching
     `</>` at line 452 to `</div>`.
   - `page.tsx:454` review block: same wrapper change; close `</>` at line 487 → `</div>`.
   - `page.tsx:489` questions block: same wrapper change; close `</>` at line 521 → `</div>`.
   - `page.tsx:523` planned block: change `className="planned-state"` to
     `className="phase-pane planned-state"` (no extra wrapper needed).

   Do not change any inner markup, class names, or content.

## Boundaries

- Do NOT touch `projects/page.tsx`, `run` pages, `Compass`, `MomentumWorkbench`,
  or any file other than the two listed.
- Do NOT modify the existing `.enters` / `make-way` rules — only append new rules.
- Do NOT add new dependencies, no JS motion library.
- Do NOT change durations/easings of existing transitions.
- If the code at `page.tsx:357/454/489/523` no longer matches structure (drift since
  commit f39f6c1), STOP and report instead of improvising.

## Verification

- **Mechanical**:
  - `pnpm.cmd --filter @launchscope/web typecheck` — must pass with no errors.
  - `pnpm.cmd --filter @launchscope/web lint` — must pass with no errors.
- **Feel check**: run `pnpm.cmd --filter @launchscope/web dev`, open
  `/projects/{projectId}/new-evaluation`, fill required fields, then:
  - Click through the four sections (左舷刻度尺) — no phase change, but section
    content swaps are NOT animated (out of scope); only phase transitions animate.
  - Submit gap questions / confirm profile to move `collect → review → questions →
    planned` and confirm each entering block fades+slides in over ~280ms, quick and
    crisp, no bounce, no clipping.
  - On the `planned` phase, confirm the `✓` seal scales in from `scale(0.9)` rather
    than popping.
  - In DevTools → Rendering, enable `prefers-reduced-motion: reduce` and repeat:
    movement is dropped (instant), but the block still becomes visible (opacity lands
    at 1) — no invisible UI.
- **Done when**: all four phase transitions and the seal entrance render with the exact
  values above, typecheck/lint pass, and the reduced-motion check shows visible content.