# Animation plans

Plans for the LaunchScope `apps/web` motion work, produced by the
`improve-animations` skill. Each plan is self-contained for an executor with zero
context (any agent, incl. cheaper models). Stamp commit: `f39f6c1`.

## Plans

| # | Title | Severity | Category | Status |
|---|-------|----------|----------|--------|
| 001 | Animate form phase transitions and the planned-state seal entrance | MEDIUM | Missed opportunities (teleporting state; rare delight) | TODO |
| 002 | Add press feedback to buttons | LOW | Purpose & frequency (feedback gap) | TODO |

## Recommended execution order

1. **001** first — it touches the most-frequent seam (intake phase switches);
   it also introduces the reusable `.phase-pane`/`seal-in` patterns.
2. **002** — independent single-rule CSS change; can run in parallel with 001.

## Dependencies

- None between 001 and 002 (different files, no shared edits).
- Both depend on `apps/web` being the only changed workspace; do not touch
  `apps/orchestrator`, `apps/api`, or backend code.