import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { presentReportText } from "../../src/lib/report-copy.ts";

const specialist = readFileSync(new URL("../../src/components/reports/v2/SpecialistReportV2.tsx", import.meta.url), "utf8");
const tabs = readFileSync(new URL("../../src/components/reports/v2/SpecialistViewTabs.tsx", import.meta.url), "utf8");
const privatePage = readFileSync(new URL("../../src/app/(workspace)/runs/[runId]/agent-reports/[agentCode]/page.tsx", import.meta.url), "utf8");
const panel = readFileSync(new URL("../../src/components/reports/AgentReportsPanel.tsx", import.meta.url), "utf8");
const v2Cards = readFileSync(new URL("../../src/components/reports/v2/AgentReportCards.tsx", import.meta.url), "utf8");
const publicPage = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/runs/[runId]/agent-reports/[agentCode]/page.tsx", import.meta.url), "utf8");
const evidencePage = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/runs/[runId]/evidence/[evidenceId]/page.tsx", import.meta.url), "utf8");

test("specialist cards open independently in a protected new tab", () => {
  assert.match(panel, /<a[\s\S]*?href=\{`\/runs\//);
  assert.match(panel, /target="_blank"/);
  assert.match(panel, /rel="noopener noreferrer"/);
  assert.match(v2Cards, /target="_blank"/);
  assert.match(v2Cards, /rel="noopener noreferrer"/);
});

test("summary and full tabs project the same canonical SHA and Claim IDs", () => {
  assert.match(tabs, /"summary" \| "full"/);
  assert.match(specialist, /data-content-sha256=\{report\.integrity\.canonical_sha256\}/);
  assert.match(specialist, /document\.executive_summary/);
  assert.match(specialist, /document\.claims/);
  assert.doesNotMatch(specialist, /iframe|dangerouslySetInnerHTML/);
});

test("private and public child pages return to the supervisor report anchor", () => {
  assert.match(privatePage, /`\/reports\/\$\{report\.projection\.supervisor_report_id\}#agent-reports`/);
  assert.match(publicPage, /reports\/\$\{report\.projection\.supervisor_report_id\}#agent-reports/);
});

test("specialist report keeps integrity and raw audit codes out of the audience projection", () => {
  assert.match(specialist, /auditLabelKeys/);
  assert.doesNotMatch(specialist, /canonical_sha256\.slice/);
  assert.doesNotMatch(specialist, /View raw audit codes|raw_audit_refs|domain_payload/);
});

test("zh-CN projection presents English specialist prose in natural Chinese and en preserves the source", () => {
  const userClaim = "Team intake material confirms a live web product covering all four assigned validation flows (asset library, image/video generation, workflows, credit billing); it reports no retention, migration, repeat-purchase, or willingness-to-pay data, so long-term use and payment remain unverified.";
  assert.equal(presentReportText("en", userClaim), userClaim);
  assert.match(presentReportText("zh-CN", userClaim), /四条核心验证流程/);
  assert.doesNotMatch(presentReportText("zh-CN", userClaim), /Team intake|willingness-to-pay/);
  assert.equal(presentReportText("zh-CN", "undisclosed in assigned materials"), "本次材料未披露");
});

test("report copy normalizes audience-facing index language and removes internal quota wording", () => {
  const source = "因此综合潜力得分只有 28 分（满分 100），整体置信度中等（约 0.69）。浏览器复核配额也已用完，网站活性没得到独立验证。";
  const presented = presentReportText("zh-CN", source);
  assert.match(presented, /爆款潜力指数为 28 分/);
  assert.match(presented, /可信度为中等（69%）/);
  assert.doesNotMatch(presented, /配额|综合潜力得分|0\.69/);
});

test("the Chinese supervisor conclusion acknowledges product progress before risks", () => {
  const source = "结论先说：这次评估建议「暂停」——先别急着追加投入，把关键证据补齐后再继续。CreaTrades 想做的是帮电商卖家自动生产商品图、营销素材和短视频的一体化 AI 平台，方向清楚，网站也能打开试用。但目前能证明「产品真的好用、用户真的愿意长期付费用」的证据，主要来自团队自己提交的材料，缺少独立验证；用户留存、复购、稳定收入这些关键数据都是空白。因此综合潜力得分只有 28 分（满分 100），整体置信度中等（约 0.69）。这是首次评估，没有历史结果可对比。接下来最重要的事：按下面的行动清单补齐真实使用证据和商业数据，然后重新评估。";
  const presented = presentReportText("zh-CN", source);
  assert.match(presented, /^先说亮点：/);
  assert.ok(presented.indexOf("真实进展") < presented.indexOf("再看风险"));
  assert.match(presented, /建议暂缓投入/);
});

test("public specialist and Evidence pages are sessionless and never execute raw Evidence", () => {
  for (const source of [publicPage, evidencePage]) {
    assert.doesNotMatch(source, /sessionFromDocument|browserApi|DemoSessionGuard|demo-login/);
  }
  assert.match(evidencePage, /download/);
  assert.doesNotMatch(evidencePage, /iframe|dangerouslySetInnerHTML/);
});
