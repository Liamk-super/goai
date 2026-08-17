# Presentation Layer V0.4 — Dual Report

## Goal

Separate “quick decision” and “complete evidence/execution” into two independent human artifacts generated from the same validated structured output.

## Outputs

- `summary_report` / `summary_report_html`: concise default report for 3–30 second reading.
- `full_report` / `full_report_html`: complete human-readable report for competition materials, planning, evidence review, and execution.
- `human_report` / `human_report_html`: backward-compatible aliases of the summary report.

## Summary report

Contains only target customer hierarchy, why use / why not / biggest problem, Top problems, development priorities, compact score rationale, and at most two next validations. HTML does not embed the complete report or deep accordions.

## Full report

Contains the same verdict plus detailed user segmentation/personas/scenarios, human-readable evidence, problem-evidence-action chains, six-dimension score rationale, complete development priorities, validation execution plans, and information gaps/boundaries. Internal ids and state-machine/audit fields stay machine-only.

## Consistency

Both artifacts are deterministic renderings of one validated View Model. The full report may add evidence and execution detail, but cannot change the core user, verdict, biggest problem, or priority direction shown in the summary.

