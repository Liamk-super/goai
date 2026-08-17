import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const actions = readFileSync(new URL("../../src/components/reports/v2/ReportExportActions.tsx", import.meta.url), "utf8");
const client = readFileSync(new URL("../../src/lib/api-client.ts", import.meta.url), "utf8");
const supervisor = readFileSync(new URL("../../src/components/reports/v2/SupervisorReportV2.tsx", import.meta.url), "utf8");
const specialist = readFileSync(new URL("../../src/components/reports/v2/SpecialistReportV2.tsx", import.meta.url), "utf8");
const tabs = readFileSync(new URL("../../src/components/reports/v2/SpecialistViewTabs.tsx", import.meta.url), "utf8");
const translations = readFileSync(new URL("../../src/lib/i18n.ts", import.meta.url), "utf8");
const publicSupervisor = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/reports/[reportId]/page.tsx", import.meta.url), "utf8");
const publicSpecialist = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/runs/[runId]/agent-reports/[agentCode]/page.tsx", import.meta.url), "utf8");

test("export controls offer one PDF, a complete package, and optional Evidence originals", () => {
  assert.match(actions, /t\("Export PDF"\)/);
  assert.match(actions, /t\("Download complete report package"\)/);
  assert.match(actions, /t\("Include Evidence originals"\)/);
  assert.match(translations, /"Export PDF": "导出 PDF"/);
  assert.match(translations, /"Download complete report package": "一键下载完整报告包"/);
  assert.match(translations, /"Include Evidence originals": "同时下载证据原件"/);
  assert.match(actions, /include_evidence: includeEvidence/);
  assert.match(actions, /kind: "PACKAGE"/);
});

test("private and public export POSTs carry stable idempotency and correlation boundaries", () => {
  assert.match(client, /\/experience\/reports\/\$\{reportId\}\/exports/);
  assert.match(client, /\/public\/demo\/v2\/reports\/\$\{reportId\}\/exports/);
  assert.match(client, /Idempotency-Key/);
  assert.match(client, /X-Correlation-Id/);
  assert.match(actions, /getReportExportReadUrl/);
});

test("supervisor and specialist projections expose export actions and print-ready state", () => {
  assert.match(supervisor, /<ReportExportActions/);
  assert.match(supervisor, /allowPackage/);
  assert.match(specialist, /<ReportExportActions/);
  assert.match(supervisor, /data-report-ready="true"/);
  assert.match(specialist, /data-report-ready="true"/);
  assert.match(tabs, /data-report-view="full"/);
});

test("public report pages pass only their exact share token into export controls", () => {
  assert.match(publicSupervisor, /publicToken=\{token\}/);
  assert.match(publicSpecialist, /publicToken=\{token\}/);
  assert.doesNotMatch(publicSupervisor + publicSpecialist, /sessionFromDocument/);
});

test("long report rendering is polled without a short ten-second cutoff", () => {
  assert.match(actions, /EXPORT_POLL_ATTEMPTS = 240/);
  assert.match(actions, /EXPORT_POLL_INTERVAL_MS = 1_000/);
});

test("renderer upgrades use a new browser idempotency namespace", () => {
  assert.match(actions, /"report-export-v3"/);
});
