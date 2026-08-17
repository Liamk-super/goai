import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const designSystem = readFileSync(
  new URL("../../src/components/reports/institutional/InstitutionalReport.tsx", import.meta.url),
  "utf8",
);
const supervisor = readFileSync(
  new URL("../../src/components/reports/v3/SupervisorReportV3.tsx", import.meta.url),
  "utf8",
);
const specialist = readFileSync(
  new URL("../../src/components/reports/v3/SpecialistReportV3.tsx", import.meta.url),
  "utf8",
);
const css = readFileSync(
  new URL("../../src/app/(workspace)/globals.css", import.meta.url),
  "utf8",
);

test("institutional design system stays a canonical-data projection", () => {
  for (const component of [
    "InstitutionalReportShell", "ConfidentialCover", "DecisionCard", "EvidenceBadgeRow", "VersionDeltaPanel",
    "ScoreDimensionTable", "GateBanner", "DueDiligenceTable", "ActionCard", "RiskCallout", "SourceDirectory", "ReportFooter",
  ]) assert.match(designSystem, new RegExp(`export function ${component}`));
  assert.doesNotMatch(designSystem, /fetch\(|browserApi|dangerouslySetInnerHTML/);
  assert.match(supervisor, /VersionDeltaPanel/);
  assert.match(supervisor, /ScoreDimensionTable/);
  assert.match(specialist, /DueDiligenceTable/);
  assert.match(specialist, /SourceDirectory/);
});

test("institutional reports have print, narrow-screen, and reduced-motion treatments", () => {
  assert.match(css, /--institutional-ink: #0f172a/);
  assert.match(css, /width: min\(940px, calc\(100% - 40px\)\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.institutional-report/);
  assert.match(css, /@media print[\s\S]*?\.institutional-report-actions/);
  assert.match(css, /@page \{ size: A4/);
  assert.match(designSystem, /<table className="institutional-table">/);
});
