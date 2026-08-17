export function visibleReportPriorities<TIssue, TAction>(issues: TIssue[], actions: TAction[]) {
  return { issues: issues.slice(0, 3), actions: actions.slice(0, 3) };
}

export function specialistViewFromQuery(value: string | null | undefined): "summary" | "full" {
  return value === "full" ? "full" : "summary";
}

function strings(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(item => String(item)).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

export function specialistPayloadSections(payload: Record<string, unknown>) {
  const definitions: Record<string, Array<[string, string]>> = {
    USER_EVIDENCE: [
      ["target_segments", "Target segments"], ["jobs_and_scenarios", "Jobs and scenarios"],
      ["behavioral_evidence", "Behavioral evidence"], ["retention_and_payment", "Retention and payment"],
      ["validation_plan", "Validation plan"],
    ],
    PRODUCT_ENGINEERING: [
      ["stage_gate", "Stage gate"], ["core_flows", "Core flows"],
      ["delivery_and_reliability", "Delivery and reliability"], ["dependencies_and_security", "Dependencies and security"],
      ["retest_gates", "Retest gates"],
    ],
    BUSINESS_INVESTMENT: [
      ["business_model", "Business model"], ["unit_economics", "Unit economics"],
      ["competition_and_market", "Competition and market"], ["investment_gates", "Investment gates"],
      ["compliance_scope", "Compliance scope"],
    ],
    EVIDENCE_AUDIT: [
      ["coverage_by_dimension", "Coverage by dimension"], ["source_independence", "Source independence"],
      ["conflicts", "Evidence conflicts"], ["calibration_decisions", "Calibration decisions"], ["evidence_gaps", "Evidence gaps"],
    ],
  };
  return (definitions[String(payload.kind)] ?? [])
    .map(([key, title]) => ({ key, title, items: strings(payload[key]) }))
    .filter(section => section.items.length > 0);
}
