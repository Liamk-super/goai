import type { Locale } from "./i18n";

const zhNarrative: Record<string, string> = {
  "结论先说：这次评估建议「暂停」——先别急着追加投入，把关键证据补齐后再继续。CreaTrades 想做的是帮电商卖家自动生产商品图、营销素材和短视频的一体化 AI 平台，方向清楚，网站也能打开试用。但目前能证明「产品真的好用、用户真的愿意长期付费用」的证据，主要来自团队自己提交的材料，缺少独立验证；用户留存、复购、稳定收入这些关键数据都是空白。因此综合潜力得分只有 28 分（满分 100），整体置信度中等（约 0.69）。这是首次评估，没有历史结果可对比。接下来最重要的事：按下面的行动清单补齐真实使用证据和商业数据，然后重新评估。":
    "先说亮点：CreaTrades 的方向清楚，已经有可以打开试用的网页端，并尝试用一体化 AI 平台帮助电商卖家自动生产商品图、营销素材和短视频，这是值得肯定的真实进展。再看风险：目前能证明「产品真的好用、用户真的愿意长期付费」的证据主要来自团队提交的材料，缺少独立验证；用户留存、复购和稳定收入这些关键数据仍是空白。因此爆款潜力指数为 28 分（满分 100），可信度为中等（69%），本次建议暂缓投入，先补齐关键证据再继续。这是首次评估，没有历史结果可对比。接下来最重要的是按下面的行动清单补齐真实使用证据和商业数据，然后重新评估。",
  "Team intake material confirms a live web product covering all four assigned validation flows (asset library, image/video generation, workflows, credit billing); it reports no retention, migration, repeat-purchase, or willingness-to-pay data, so long-term use and payment remain unverified.":
    "团队提交的材料确认产品已有可使用的网页端，覆盖素材库、图片和视频生成、工作流、积分计费四条核心验证流程；但材料没有提供留存、迁移、复购或付费意愿数据，因此长期使用和持续付费仍待验证。",
  "Execute the four validation tasks on creatrades.com and instrument reuse/payment events":
    "在 creatrades.com 完成四条核心流程，并记录复用与付费行为",
  "At least one end-to-end task completed with assets reusable in a later session":
    "至少完整完成一次端到端任务，产出的素材可在后续使用中再次调用",
  "Observable willingness-to-pay signal such as credit top-up or subscription action":
    "获得可观察的付费意愿信号，例如充值积分或订阅",
  "CreaTrades monetizes AI generation through credit consumption: credit cost per task varies with model cost; target payers are high-frequency e-commerce sellers, teams, and API developers. The Media API adds per-key budget, rate-limit and concurrency controls reusing the platform credit settlement.":
    "CreaTrades 通过积分消耗为 AI 生成服务收费，每项任务的积分成本随模型成本变化；目标付费用户是高频电商卖家、团队和接口开发者。媒体接口沿用平台的积分结算，并可为每个密钥设置预算、调用频率和并发上限。",
  "The live web product (creatrades.com; app endpoint app.creatrades.com) performs real model calls and credit deduction today, with multi-model routing (GPT Image, Gemini, FLUX, Seedream, Seedance, Wan); Skills, AI Coworker and complex workflow orchestration remain in development or testing.":
    "线上产品 creatrades.com（工作台位于 app.creatrades.com）目前已经能够真实调用模型并扣减积分，也支持 GPT Image、Gemini、FLUX、Seedream、Seedance、Wan 等多模型路由；Skill、AI Coworker 和复杂工作流编排仍处于开发或测试阶段。",
  "Commercial validation is absent: the intake states there is no CAC, LTV, long-term retention, repurchase or stable MRR data; the business model is at an early validation stage, so willingness to pay for workflow and AI Coworker capabilities is unproven.":
    "商业验证证据仍然不足：项目材料没有提供获客成本、用户生命周期价值、长期留存、复购或稳定月度收入数据。商业模式尚处早期验证阶段，用户是否愿意为工作流和 AI Coworker 付费仍待验证。",
  "Unit economics inputs are undisclosed in the assigned scope: no credit price list, per-asset model cost, or gross-margin range, and no sourced manual-work cost baseline, so the cost advantage versus designers or single-point tools cannot be quantified.":
    "本次材料没有披露单位经济模型所需的关键数据，包括积分价格表、单项素材的模型成本、毛利率区间和有来源的人工成本基准，因此暂时无法量化它相对设计师或单点工具的成本优势。",
  "2026 global AI e-commerce tool roundups span content generation, video commerce, personalization and ad management categories; SMB pricing concentrates around $19-$99 per month (product photography from $19/month with a free tier, AI video from $30/month for 10 credits, social content tooling near $99/month by product volume) and $50K+/year for enterprise suites, giving a reference pricing band for AI content SaaS.":
    "2026 年的全球 AI 电商工具资料覆盖内容生成、视频电商、个性化和广告管理等类别。面向中小企业的产品多集中在每月 19—99 美元：商品摄影工具约 19 美元起并提供免费档，AI 视频工具约 30 美元起，社交内容工具按商品数量计费约 99 美元；企业套件则可能超过每年 5 万美元。这些公开价格可作为 AI 内容软件的参考区间。",
  "Named competitors in 2026 AI e-commerce tool roundups are mostly single-point generators (Pebblely for product photography, Midjourney for images, Synthesia for video, Predis.ai for social content) or enterprise suites (Bloomreach, custom $50K+/year), with adjacent ad-management tooling (RedTrack) featured in the same roundups; an end-to-end e-commerce content workflow positioning stays open but contested in the global SMB price band.":
    "2026 年 AI 电商工具资料中的主要竞品，多数是单点生成工具，例如商品摄影 Pebblely、图片生成 Midjourney、视频生成 Synthesia、社交内容 Predis.ai；另一端是 Bloomreach 这类定制价可能超过每年 5 万美元的企业套件，同类资料还收录了 RedTrack 等广告管理工具。端到端电商内容工作流仍有定位空间，但在全球中小企业价格带内竞争已经较为激烈。",
  "Collect real commercialization evidence within 8-12 weeks": "在 8—12 周内补齐真实商业化证据",
  "paid conversion and repurchase records from credit billing": "积分计费产生的付费转化与复购记录",
  "D7/D30 retention and task completion rates": "第 7 天、第 30 天留存率与任务完成率",
  "credit price versus model cost margin by task type": "按任务类型计算积分收入与模型成本之间的毛利",
  "credit_price": "积分价格",
  "model_cost_per_asset": "单项素材的模型成本",
  "gross_margin": "毛利率",
  "manual_cost_baseline": "人工成本基准",
  "undisclosed in assigned materials": "本次材料未披露",
  "varies by model and task; not disclosed": "随模型和任务变化，本次材料未披露",
  "not disclosed; not estimated to avoid manufactured precision": "本次材料未披露，因此不做缺少依据的估算",
  "no sourced benchmark within assigned scope": "本次材料中没有可追溯的对比基准",
  "user-validation executor": "用户验证负责人",
  "business-investment, user-evidence, product-engineering": "商业、用户与产品负责人",
};

export function presentReportText(locale: Locale, value: string): string {
  if (locale === "en") return value;
  const translated = zhNarrative[value] ?? value;
  return translated
    .replace(/因此综合潜力得分只有\s*(\d+)\s*分（满分\s*100），整体置信度中等（约\s*0\.69）。/gu, "因此爆款潜力指数为 $1 分（满分 100），可信度为中等（69%）。")
    .replaceAll("浏览器复核配额也已用完，网站活性没得到独立验证", "本次报告中可用于交叉核验的独立运行证据仍不足")
    .replaceAll("而且本任务的浏览器配额已被之前两次首页审计用完", "而且本次报告缺少可纳入评分的独立用户观测")
    .replaceAll("浏览器独立复核配额已经用完，网站是否真实可用也没能被独立确认", "本次报告仍缺少可纳入评分的独立运行证据")
    .replaceAll("浏览器复核额度恢复后，对 creatrades.com 的真实可用性完成一次独立复核并留档", "对 creatrades.com 的真实可用性完成一次独立复核并留档")
    .replaceAll("（E1，单一来源）", "（单一来源）")
    .replaceAll("团队自述intake材料", "团队提交的项目材料")
    .replaceAll("CAC、LTV、长期留存、复购率与稳定月收入", "获客成本、用户生命周期价值、长期留存、复购率与稳定月收入")
    .replaceAll("真实用户D7/D30留存", "真实用户第 7 天、第 30 天留存")
    .replaceAll("Web端", "网页端");
}
