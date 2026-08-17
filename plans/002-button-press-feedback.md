# 002 — Add press feedback to buttons

- **Status**: TODO
- **Commit**: f39f6c1
- **Severity**: LOW
- **Category**: Purpose & frequency (feedback gap)
- **Estimated scope**: 1 file (CSS), trivial

## Problem

Buttons in `apps/web/src/app/(workspace)/globals.css:241-269` have a hover state
(`translateY(-1px)`) but the `:active` state only returns the button to `translateY(0)`.
There is no press feedback — the interface does not confirm the click at the moment of
impact. Every pressable element in the app (form submit, section nav, primary CTAs)
inherits this.

Current code:

```css
/* globals.css:263-269 */
button:hover:not(:disabled),
.button:hover {
  transform: translateY(-1px);
}
button:active:not(:disabled) {
  transform: translateY(0);
}
```

## Target

Add a subtle press scale on `:active`, keeping the existing translateY geometry.
Press feedback must be fast (100–160ms per AUDIT §2) — the repo already has
`--dur-micro: 120ms` for exactly this.

```css
/* target — globals.css */
button:active:not(:disabled),
.button:active:not(:disabled) {
  transform: translateY(0) scale(0.98);
}
```

The existing `transition` on `button`/`.button` (`globals.css:258-261`) already covers
`transform` with `--dur-micro` + `--ease-out`, so no transition change is needed.

Note: `.button:active` is reachable via `<a className="button">` links — the target
adds the `:active` state for those too, matching the existing `:hover` pairing.

## Repo conventions to follow

- Duration token already exists: `--dur-micro: 120ms` (`globals.css:118`).
- Easing already exists: `--ease-out: cubic-bezier(0.2, 0, 0.38, 0.9)` (`globals.css:121`).
- The existing `transition` on `button, .button` (`globals.css:258-261`) already
  animates `transform` — reuse it, do not add a new transition declaration.
- Reduced motion is already handled globally (`globals.css:127-127` zeroes durations);
  scale feedback vanishes instantly under reduced motion while the tap still registers —
  correct behavior, no extra CSS needed.

## Steps

1. In `apps/web/src/app/(workspace)/globals.css`, replace the block at lines 267-269:

```css
button:active:not(:disabled) {
  transform: translateY(0);
}
```

with:

```css
button:active:not(:disabled),
.button:active:not(:disabled) {
  transform: translateY(0) scale(0.98);
}
```

2. Do not touch any other rule.

## Boundaries

- Do NOT change markup in any TSX file.
- Do NOT add keyframes, springs, or JS.
- Do NOT modify the `transition` declaration or other button rules.
- If the exact code at `globals.css:267-269` no longer matches (drift since commit
  f39f6c1), STOP and report instead of improvising.

## Verification

- **Mechanical**:
  - `pnpm.cmd --filter @launchscope/web typecheck` — must pass (CSS-only change; run to
    confirm no accidental breakage).
  - `pnpm.cmd --filter @launchscope/web lint` — must pass.
- **Feel check**: run `pnpm.cmd --filter @launchscope/web dev`, open `/projects`:
  - Press (mouse down, hold) the "创建项目" button and confirm it compresses to
    `scale(0.98)` within ~120ms and returns on release — a firm, subtle click, not a
    slide.
  - Press the same button via keyboard (Tab to focus, Enter/Space) — confirm the press
    still registers (scale applies) and the click action fires.
  - In DevTools → Rendering, enable `prefers-reduced-motion: reduce` and confirm the
    scale is dropped without losing the click.
- **Done when**: press feedback compresses to `scale(0.98)` on `:active` with the
  existing 120ms ease-out, typecheck/lint pass.