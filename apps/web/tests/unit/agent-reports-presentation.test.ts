import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const panelSource = readFileSync(
  new URL("../../src/components/reports/AgentReportsPanel.tsx", import.meta.url),
  "utf8",
);
const v2CardsSource = readFileSync(
  new URL("../../src/components/reports/v2/AgentReportCards.tsx", import.meta.url),
  "utf8",
);
const detailPageSource = readFileSync(
  new URL("../../src/app/(workspace)/runs/[runId]/agent-reports/[agentCode]/page.tsx", import.meta.url),
  "utf8",
);
const supervisorReportSource = readFileSync(
  new URL("../../src/components/reports/SupervisorLayeredReport.tsx", import.meta.url),
  "utf8",
);
const wheelSource = readFileSync(
  new URL("../../src/components/workspace/EvaluationWheel.tsx", import.meta.url),
  "utf8",
);
const cssSource = readFileSync(
  new URL("../../src/app/(workspace)/globals.css", import.meta.url),
  "utf8",
);

test("final report always exposes the Agent report catalog outside developer mode", () => {
  assert.match(supervisorReportSource, /\{!readOnly && <AgentReportsPanel runId=\{report\.run_id\} \/>\}/);
  assert.doesNotMatch(supervisorReportSource, /developerMode && !readOnly && <AgentReportsPanel/);
  assert.match(panelSource, /listAgentReports/);
  assert.doesNotMatch(panelSource, /getAgentReport/);
});

test("Agent report bodies open in isolated tabs from every private report catalog", () => {
  assert.match(panelSource, /target="_blank"/);
  assert.match(panelSource, /rel="noopener noreferrer"/);
  assert.match(v2CardsSource, /target="_blank"/);
  assert.match(v2CardsSource, /rel="noopener noreferrer"/);
  assert.match(panelSource, /\/agent-reports\/\$\{summary\.agent_code\}/);
  assert.match(detailPageSource, /getAgentReportForDisplay\(runId, agentCode\)/);
  assert.doesNotMatch(panelSource, /dangerouslySetInnerHTML/);
  assert.doesNotMatch(detailPageSource, /dangerouslySetInnerHTML/);
  assert.match(panelSource, /Findings and evidence/);
  assert.match(panelSource, /Evidence references/);
  assert.match(panelSource, /Risks and limitations/);
  assert.match(panelSource, /Evidence audit decisions/);
  assert.match(panelSource, /View raw verified structure/);
  assert.match(panelSource, /The execution needs attention before an independent report can be read/);
  assert.match(panelSource, /Durable Agent report catalog/);
});

test("continuous wheel motion is run-state gated and reduced-motion safe", () => {
  assert.match(wheelSource, /motionState === "RUNNING"/);
  assert.match(wheelSource, /calibrationStatus === "CALIBRATING" && motionState === "RUNNING"/);
  assert.match(cssSource, /@media \(prefers-reduced-motion: no-preference\)[\s\S]*?ambient-rotation/);
  assert.doesNotMatch(
    cssSource,
    /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.ambient-spin[\s\S]*?animation:\s*ambient-rotation/,
  );
});
