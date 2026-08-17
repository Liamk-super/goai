/**
 * Reference simulation executor — TEST FIXTURE, not production code.
 *
 * Stands in for a bound `simulation_engine` + `product_reader` adapter so the
 * S1..S6 pipeline can be exercised end to end deterministically. Every value it
 * returns is fabricated fixture data and is labelled as such: it exists to prove
 * the ORCHESTRATION and the GATES work, never to assert anything about a real
 * product or real users.
 *
 * Deterministic on purpose: no clock, no randomness, so output.example.json is
 * reproducible and a diff means a real behavioural change.
 *
 * Note the shape contract this fixture must honour — the orchestrator merges
 * `personas / jobs / scenarios / firstExperience / taskTests / experienceIssues /
 * interview / hiddenNeeds / insights / politenessRemoved / hypotheses / plans /
 * segments / negativeFindings` and clamps any `evidence` it returns to E2.
 */

const TS = "2026-08-09T02:00:00.000Z";

/** Behaviour keys are deliberately far apart: a homogeneous set must fail A-07. */
const PERSONAS = [
  {
    persona_id: "P1",
    label: "二战考研生 · 每天刷资料超 1 小时，时间极紧",
    archetype: "high_need",
    background: { identity: "二战考研生", occupation: "全职备考", environment: "自习室 + 手机" },
    goal_statement: "当我在自习室复习时，我想快速筛掉重复资料，以便把时间花在真正没掌握的考点上",
    motivation: "距离初试只剩 4 个月，重复资料让我焦虑",
    pains: [
      { description: "同一考点资料重复下载，筛选耗时", frequency: 5, severity: 4, workaround_cost: 4, pain_class: "functional", fact_type: "assumption" },
    ],
    barriers: ["担心筛选把重点漏掉", "不愿再学一个新工具"],
    behavior_keys: {
      alternative_in_use: "手动整理文件夹 + Excel 清单",
      budget_constraint: "月预算 50 元以内",
      skill_level: "intermediate",
      urgency: 5,
      risk_attitude: "averse",
    },
    value_threshold: { statement: "每周至少省 2 小时才值得换", quantified: true, fact_type: "assumption" },
    rejection_threshold: { statement: "学习成本超过 30 分钟或要付费超过 50 元就放弃", quantified: true, fact_type: "assumption" },
    rejection_reasons: ["怕筛掉自己还没掌握的考点", "考完就用不上，不想为一次性需求付费"],
    confidence: "medium",
    field_provenance: { goal: "inference", pains: "inference", alternative: "inference", value_threshold: "assumption" },
    eligible_for_scoring: true,
    calibrated_by_real_evidence: [],
  },
  {
    persona_id: "P2",
    label: "考研机构助教 · 已有内部资料库，最挑剔",
    archetype: "skeptic",
    background: { identity: "机构助教", occupation: "教辅", environment: "办公室电脑" },
    goal_statement: "当我给学生发资料时，我想确认资料没有重复和过期，以便不被学生质疑专业度",
    motivation: "机构已有资料库，换工具要说服负责人",
    pains: [
      { description: "现有资料库维护靠人工，版本混乱", frequency: 3, severity: 3, workaround_cost: 3, pain_class: "functional", fact_type: "assumption" },
    ],
    barriers: ["数据放在哪、谁能看", "采购流程要走审批", "和现有资料库打不通"],
    behavior_keys: {
      alternative_in_use: "机构自建资料库 + 人工校对",
      budget_constraint: "走机构采购，个人不付费",
      skill_level: "expert",
      urgency: 2,
      risk_attitude: "neutral",
    },
    value_threshold: { statement: "能和现有资料库打通并支持批量导出才考虑", quantified: false, fact_type: "assumption" },
    rejection_threshold: { statement: "学生资料要上传到第三方服务器就直接否决", quantified: false, fact_type: "assumption" },
    rejection_reasons: ["学生资料上传第三方有合规顾虑", "采购流程比工具本身麻烦"],
    confidence: "medium",
    field_provenance: { goal: "inference", pains: "inference", alternative: "inference", value_threshold: "assumption" },
    eligible_for_scoring: true,
    calibrated_by_real_evidence: [],
  },
  {
    persona_id: "P3",
    label: "在职跨考生 · 通勤碎片时间，弱网络低预算",
    archetype: "edge_case",
    background: { identity: "在职跨考生", occupation: "全职工作", environment: "地铁通勤 + 手机弱网" },
    goal_statement: "当我通勤时，我想用碎片时间看最该看的资料，以便不浪费仅有的学习时间",
    motivation: "每天只有 1 小时通勤可用",
    pains: [
      { description: "弱网下加载慢，干脆放弃", frequency: 4, severity: 4, workaround_cost: 2, pain_class: "functional", fact_type: "assumption" },
    ],
    barriers: ["不能离线用就没意义", "注册流程太长直接退出"],
    behavior_keys: {
      alternative_in_use: "什么都不做，随便看看公众号",
      budget_constraint: "只用免费产品",
      skill_level: "novice",
      urgency: 3,
      risk_attitude: "tolerant",
    },
    value_threshold: { statement: "地铁上能离线打开才有用", quantified: false, fact_type: "assumption" },
    rejection_threshold: { statement: "要注册填超过 3 项信息就放弃", quantified: true, fact_type: "assumption" },
    rejection_reasons: ["弱网加载不出来", "要填太多信息才能试用"],
    confidence: "medium",
    field_provenance: { goal: "inference", pains: "inference", alternative: "inference", value_threshold: "assumption" },
    eligible_for_scoring: true,
    calibrated_by_real_evidence: [],
  },
];

function card({ id, type, personaId, unit, observation, dimensions }) {
  return {
    evidence_id: id,
    evidence_type: type,
    source: `simulation://${unit}/${personaId ?? "set"}`,
    source_tier: "tier_3",
    timestamp: TS,
    reliability_level: "E2",
    supporting_claims: [personaId ?? "persona_set"],
    applicability: {
      product_version: "V1.0",
      scope: unit,
      environment: null,
      persona_ids: personaId ? [personaId] : null,
      segment: null,
      valid_for_dimensions: dimensions,
    },
    expiry: "unknown",
    content_hash: "a".repeat(64),
    observation,
    fact_type: "inference",
    simulation_note: `Simulated by ${unit} for ${personaId ?? "persona set"}. Not a real user statement.`,
  };
}

/**
 * @param {{ zeroNegatives?: boolean, homogeneousPersonas?: boolean, lowConfidencePersonas?: boolean, missingInterviewPersona?: boolean, badUpgrade?: boolean }} [opts]
 */
function referenceHypotheses(input, opts = {}) {
  if (input?.runtime?.mode !== "version_regression") {
    return [
      { hypothesis_id: "H1", statement: "目标用户每周为筛选重复资料花费 2 小时以上", claim_type: "demand", fact_type: "assumption", current_evidence_level: "E2", supporting_refs: ["EV-uvd-1"], contradicting_refs: [], affected_dimensions: ["demand_strength", "pain_severity"], decision_impact: "blocking", status: "open", carried_from_previous: false, deferred_reason: null },
      { hypothesis_id: "H2", statement: "学生用户愿意为该产品付费 50 元以内", claim_type: "willingness_to_pay", fact_type: "assumption", current_evidence_level: "E2", supporting_refs: [], contradicting_refs: [], affected_dimensions: ["willingness_to_pay"], decision_impact: "high", status: "open", carried_from_previous: false, deferred_reason: null },
      { hypothesis_id: "H3", statement: "机构型用户的合规顾虑会阻断采购", claim_type: "segment", fact_type: "assumption", current_evidence_level: "E2", supporting_refs: ["EV-uvd-4"], contradicting_refs: [], affected_dimensions: ["alternative_gap"], decision_impact: "medium", status: "open", carried_from_previous: false, deferred_reason: null },
    ];
  }
  if (opts.dropInherited) return [];
  const claimTypes = { H1: "demand", H2: "willingness_to_pay", H3: "usability", H4: "segment" };
  const dimensions = { H1: ["demand_strength", "pain_severity"], H2: ["willingness_to_pay"], H3: ["pain_severity"], H4: ["demand_strength"] };
  return (input.previous_validation_results?.hypotheses ?? []).map((prior) => ({
    hypothesis_id: prior.hypothesis_id,
    statement: prior.statement,
    claim_type: claimTypes[prior.hypothesis_id] ?? "segment",
    fact_type: "assumption",
    current_evidence_level: prior.evidence_level,
    supporting_refs: [],
    contradicting_refs: [],
    affected_dimensions: dimensions[prior.hypothesis_id] ?? ["demand_strength"],
    decision_impact: prior.status === "open" ? "high" : "medium",
    status: prior.status,
    carried_from_previous: true,
    deferred_reason: prior.status === "open" ? "同任务复验待真实执行；本轮不静默重开或替换" : "上一轮已 settled；未显式 reopen，不生成新计划",
  }));
}

function buildTaskMatrix(tasks = [], mode = "first_validation") {
  const overrides = new Map(mode === "version_regression" ? [] : [
    ["P1::upload_and_rank", { result: "completed_with_difficulty", hesitation_steps: ["选择去重强度"], cause_type: "cognitive" }],
    ["P2::export_weekly_plan", { result: "failed", errors: ["导出按钮无响应"], abandon_reason: "导出失败，无法交付给学生", cause_type: "functional" }],
    ["P3::upload_and_rank", { result: "failed", errors: ["弱网下上传超时"], abandon_reason: "加载不出来", cause_type: "performance" }],
  ]);
  return PERSONAS.flatMap((persona) =>
    tasks.map((task) => {
      const override = overrides.get(`${persona.persona_id}::${task.task_key}`) ?? {};
      const failed = override.result === "failed";
      return {
        persona_id: persona.persona_id,
        task_key: task.task_key,
        result: override.result ?? "completed",
        reason: null,
        path: ["start", task.task_key, "result"],
        hesitation_steps: override.hesitation_steps ?? [],
        errors: override.errors ?? [],
        abandon_reason: override.abandon_reason ?? null,
        cognitive_walkthrough: failed
          ? {
              will_try_correct_goal: true,
              will_notice_correct_action: override.cause_type !== "performance",
              can_link_action_to_goal: true,
              can_understand_feedback: false,
            }
          : null,
        cause_type: override.cause_type ?? "unknown",
        evidence_refs: ["EV-uvd-3"],
      };
    }),
  );
}

export function createReferenceExecutor(opts = {}) {
  return async function executeStep(step, input) {
    const personas = PERSONAS.map((persona) =>
      opts.lowConfidencePersonas
        ? { ...structuredClone(persona), confidence: "low", eligible_for_scoring: false, calibrated_by_real_evidence: [] }
        : structuredClone(persona),
    );
    switch (step.id) {
      case "s2":
        return {
          status: "completed",
          detail: "3 personas modelled across high_need / skeptic / edge_case",
          personas: opts.homogeneousPersonas
            ? [
                personas[0],
                { ...personas[1], persona_id: "P2", archetype: "skeptic", behavior_keys: { ...personas[0].behavior_keys } },
                { ...personas[2], persona_id: "P3", archetype: "edge_case", behavior_keys: { ...personas[0].behavior_keys } },
              ]
            : personas,
          segments: [
            {
              segment_id: "SEG1",
              label: "距初试 6 个月内的二战考研生",
              who_hurts_most: "二战考研生：每天筛资料超 1 小时",
              who_pays: "学生本人",
              who_adopts_first: "自习室高频使用者",
              payer_differs_from_user: false,
              fact_type: "assumption",
              evidence_refs: ["EV-uvd-1"],
            },
          ],
          jobs: [
            {
              job_id: "J1",
              persona_ids: ["P1", "P2", "P3"],
              statement: "当我复习时间紧张时，我想快速筛掉重复资料，以便把时间花在没掌握的考点上",
              job_type: "functional",
              outcome_metric: "每周减少筛资料时间 2 小时以上",
              fact_type: "assumption",
              evidence_refs: ["EV-uvd-1"],
            },
          ],
          evidence: [
            card({
              id: "EV-uvd-1",
              type: "persona_evidence",
              personaId: "P1",
              unit: "s2",
              observation: "模拟建模：P1 每周为筛资料花费约 5 小时，已用 Excel 清单绕行",
              dimensions: ["demand_strength", "pain_severity"],
            }),
          ],
          dimensions: {
            demand_strength: { score: 4, evidence_refs: ["EV-uvd-1"], basis: "模拟 Persona 表现出高绕行成本（E2）" },
            pain_severity: { score: 4, evidence_refs: ["EV-uvd-1"], basis: "频率 5 × 强度 4 × 绕行成本 4（E2 模拟）" },
          },
        };

      case "s3":
        return {
          status: "completed",
          detail: "3 scenarios with alternatives and switching forces",
          scenarios: [
            {
              scenario_id: "SC1",
              persona_id: "P1",
              trigger_event: "下载完一批资料，发现和上周的重复",
              environment: "自习室，笔记本电脑",
              limits: { time: "每天可支配 30 分钟整理", budget: "50 元/月", skill: null, device: "笔记本", network: "正常", permission: null },
              alternatives: [
                { name: "手动整理文件夹 + Excel 清单", alternative_type: "workaround", cost: { money: "0", time: "每周 5 小时", effort: "高" }, gap: "无法判断内容是否重复，只能靠文件名", fact_type: "assumption", evidence_refs: ["EV-uvd-1"] },
                { name: "什么都不做", alternative_type: "do_nothing", cost: { money: "0", time: "0", effort: "低" }, gap: "资料越堆越多，焦虑加剧", fact_type: "assumption", evidence_refs: [] },
              ],
              switching_forces: { push: 4, pull: 4, anxiety: 3, habit: 3, verdict: "will_switch", basis: "绕行成本 4，push 按 KB-USR-F02 提至 4", push_forced_by_workaround_cost: true },
              journey: [
                { stage: "awareness", behavior: "看到同学推荐", thought: "又一个工具？", emotion: 3, touchpoint: "微信群", pain: null, drop_off_risk: "medium" },
                { stage: "trial", behavior: "打开首页", thought: "这到底帮我做什么", emotion: 2, touchpoint: "落地页", pain: "价值主张不清晰", drop_off_risk: "high" },
                { stage: "first_use", behavior: "上传资料", thought: "还要等多久", emotion: 3, touchpoint: "上传页", pain: "无进度提示", drop_off_risk: "medium" },
                { stage: "continued_use", behavior: "第二周未再打开", thought: "考完就用不上", emotion: 2, touchpoint: null, pain: "缺乏持续使用理由", drop_off_risk: "high" },
                { stage: "referral", behavior: "未推荐", thought: "没什么好说的", emotion: 3, touchpoint: null, pain: null, drop_off_risk: "high" },
              ],
              flags: { pseudo_demand_risk: false, high_switching_friction: false },
            },
            {
              scenario_id: "SC2",
              persona_id: "P2",
              trigger_event: "负责人要求核对本期资料版本",
              environment: "办公室",
              limits: { time: null, budget: "走采购", skill: null, device: "台式机", network: "正常", permission: "需负责人审批" },
              alternatives: [
                { name: "继续维持现状", alternative_type: "do_nothing", cost: { money: "0", time: "0", effort: "低" }, gap: "版本混乱继续存在", fact_type: "assumption", evidence_refs: [] },
                { name: "机构自建资料库 + 人工校对", alternative_type: "direct_product", cost: { money: "已投入", time: "每周 2 小时", effort: "中" }, gap: "版本混乱但已被接受", fact_type: "assumption", evidence_refs: [] },
              ],
              switching_forces: { push: 2, pull: 3, anxiety: 4, habit: 4, verdict: "will_not_switch", basis: "anxiety+habit(8) >= push+pull(5)", push_forced_by_workaround_cost: false },
              journey: [
                { stage: "awareness", behavior: "同事转发", thought: "数据放哪", emotion: 2, touchpoint: "微信", pain: "合规疑虑", drop_off_risk: "high" },
                { stage: "trial", behavior: "未试用", thought: "先问负责人", emotion: 2, touchpoint: null, pain: "采购流程", drop_off_risk: "high" },
                { stage: "first_use", behavior: "未进行", thought: null, emotion: 2, touchpoint: null, pain: null, drop_off_risk: "high" },
                { stage: "continued_use", behavior: "未进行", thought: null, emotion: 2, touchpoint: null, pain: null, drop_off_risk: "high" },
                { stage: "referral", behavior: "未进行", thought: null, emotion: 2, touchpoint: null, pain: null, drop_off_risk: "high" },
              ],
              flags: { pseudo_demand_risk: false, high_switching_friction: true },
            },
            {
              scenario_id: "SC3",
              persona_id: "P3",
              trigger_event: "地铁上想利用碎片时间",
              environment: "地铁，手机弱网",
              limits: { time: "每天 1 小时通勤", budget: "只用免费", skill: "新手", device: "手机", network: "弱网", permission: null },
              alternatives: [
                { name: "暂不处理", alternative_type: "do_nothing", cost: { money: "0", time: "0", effort: "低" }, gap: "碎片信息继续累积", fact_type: "assumption", evidence_refs: [] },
                { name: "刷公众号文章", alternative_type: "manual", cost: { money: "0", time: "1 小时", effort: "低" }, gap: "内容零散，不成体系", fact_type: "assumption", evidence_refs: [] },
              ],
              switching_forces: { push: 3, pull: 3, anxiety: 3, habit: 4, verdict: "will_not_switch", basis: "习惯强，弱网体验差", push_forced_by_workaround_cost: false },
              journey: [
                { stage: "awareness", behavior: "应用商店看到", thought: "免费吗", emotion: 3, touchpoint: "商店", pain: null, drop_off_risk: "medium" },
                { stage: "trial", behavior: "注册时退出", thought: "填这么多干嘛", emotion: 1, touchpoint: "注册页", pain: "注册项过多", drop_off_risk: "high" },
                { stage: "first_use", behavior: "弱网加载失败", thought: "算了", emotion: 1, touchpoint: "首页", pain: "无离线能力", drop_off_risk: "high" },
                { stage: "continued_use", behavior: "卸载", thought: null, emotion: 1, touchpoint: null, pain: null, drop_off_risk: "high" },
                { stage: "referral", behavior: "未推荐", thought: null, emotion: 1, touchpoint: null, pain: null, drop_off_risk: "high" },
              ],
              flags: { pseudo_demand_risk: false, high_switching_friction: true },
            },
          ],
          dimensions: {
            alternative_gap: { score: 3, evidence_refs: ["EV-uvd-1"], basis: "替代方案有明确缺点但已被接受（E2 模拟）" },
            usage_frequency: { score: 4, evidence_refs: ["EV-uvd-1"], basis: "备考期近似日频（E2 模拟）" },
          },
        };

      case "s4a":
        return {
          status: "completed",
          detail: "first-experience simulation for 3 personas",
          firstExperience: [
            { persona_id: "P1", five_second_impression: "像是一个资料整理工具", can_restate_value: true, restated_value: "帮我筛掉重复资料", deviation_from_claim: null, continue_intent: "hesitant", reason: "不确定会不会漏掉考点", patience_exceeded: false, evidence_refs: ["EV-uvd-2"] },
            { persona_id: "P2", five_second_impression: "没看出和我们自建库的区别", can_restate_value: false, restated_value: null, deviation_from_claim: "团队主张“5 分钟筛完”，但首屏未说明依据", continue_intent: "refuse", reason: "数据放哪没说清", patience_exceeded: false, evidence_refs: ["EV-uvd-2"] },
            { persona_id: "P3", five_second_impression: "不知道是干嘛的", can_restate_value: false, restated_value: null, deviation_from_claim: "首屏未提到离线或弱网", continue_intent: "refuse", reason: "注册要填太多", patience_exceeded: true, evidence_refs: ["EV-uvd-2"] },
          ],
          negativeFindings: 2,
          evidence: [
            card({ id: "EV-uvd-2", type: "simulated_experience_evidence", personaId: "P2", unit: "s4a", observation: "模拟首体验：2/3 Persona 无法复述价值主张", dimensions: ["demand_strength"] }),
          ],
        };

      case "s4b":
        return {
          status: "completed",
          detail: "core task test across 3 personas",
          taskTests: buildTaskMatrix(input.product_tasks, input.runtime?.mode),
          experienceIssues: [
            { issue_id: "UX1", description: "导出功能失效，机构型用户核心任务中断", severity: "blocker", frequency_persona_count: 1, cause_type: "functional", affected_personas: ["P2"], step_ref: "导出", cognitive_break_point: false, evidence_refs: ["EV-uvd-3"] },
            { issue_id: "UX2", description: "弱网环境下上传超时且无提示", severity: "major", frequency_persona_count: 1, cause_type: "performance", affected_personas: ["P3"], step_ref: "上传", cognitive_break_point: false, evidence_refs: ["EV-uvd-3"] },
            { issue_id: "UX3", description: "首屏价值主张无法被复述", severity: "major", frequency_persona_count: 2, cause_type: "cognitive", affected_personas: ["P2", "P3"], step_ref: "首页", cognitive_break_point: true, evidence_refs: ["EV-uvd-2"] },
          ],
          negativeFindings: 3,
          evidence: [
            card({ id: "EV-uvd-3", type: "simulated_task_evidence", personaId: "P2", unit: "s4b", observation: "模拟任务测试：导出任务失败（功能性），弱网上传超时", dimensions: ["pain_severity"] }),
          ],
        };

      case "s5":
        return {
          status: "completed",
          detail: "simulated interviews, insights and hypotheses",
          interview: [
            {
              persona_id: "P1",
              turns: [
                { speaker: "researcher", text: "你上一次整理重复资料是什么时候？花了多久？" },
                { speaker: "user", text: "上周日，大概三个多小时，最后还是靠文件名猜。" },
              ],
              signals: [{ text: "上周日花了三个多小时", strength: "strong", reason: "近期真实行为 + 时间成本" }],
              questions_raised: ["筛掉的资料还能找回来吗？"],
              complaints: ["不敢全信自动筛选"],
            },
            {
              persona_id: "P2",
              turns: [
                { speaker: "researcher", text: "你们现在怎么核对资料版本？" },
                { speaker: "user", text: "助教人工过一遍，每周两小时，出过一次发错版本的事故。" },
              ],
              signals: [{ text: "每周两小时人工核对，出过事故", strength: "strong", reason: "行为 + 成本 + 后果" }],
              questions_raised: ["学生资料存在你们服务器吗？"],
              complaints: ["导出直接失败，没法交付"],
            },
            ...(
              opts.missingInterviewPersona
                ? []
                : [{
                    persona_id: "P3",
                    turns: [
                      { speaker: "researcher", text: "通勤场景中哪里失败了？" },
                      { speaker: "user", text: "弱网时上传超时，而且没有恢复入口。" },
                    ],
                    signals: [{ text: "弱网上传超时", strength: "strong", reason: "具体任务失败及后果" }],
                    questions_raised: ["排序结果能离线使用吗？"],
                    complaints: ["上传超时后进度全部丢失。"],
                  }]
            ),
          ],
          hiddenNeeds: opts.zeroNegatives ? [] : [
            { description: "需要“可撤销/可追溯”的筛选结果，否则不敢用", derived_from: "P1 反复确认能否找回被筛掉的资料", persona_ids: ["P1"], fact_type: "inference" },
          ],
          insights: [
            { observation: "2/3 Persona 无法复述价值主张", root_cause: "首屏用功能语言描述，未落到用户任务", kano_type: "basic", theme: "价值传达", recommendation: "首屏改为任务语言：省下多少时间", evidence_refs: (input.product_profile?.url || input.product_profile?.experience_report_ref) ? ["EV-uvd-2"] : ["EV-uvd-4"] },
            { observation: "机构型用户先问数据存放位置", root_cause: "涉及学生资料的合规顾虑未被前置回应", kano_type: "basic", theme: "信任与合规", recommendation: "在定价页前明确数据存储与权限说明", evidence_refs: ["EV-uvd-4"] },
          ],
          politenessRemoved: [{ text: "整体挺好的", persona_id: "P1", reason: "无行为或成本支撑，按 KB-USR-B02 记为礼貌性反馈，权重 0" }],
          negativeFindings: opts.zeroNegatives ? 0 : 2,
          hypotheses: referenceHypotheses(input, opts),
          evidence: [
            card({ id: "EV-uvd-4", type: "simulated_interview_evidence", personaId: "P2", unit: "s5", observation: "模拟访谈：机构型用户首先追问数据存放与权限", dimensions: ["alternative_gap"] }),
          ],
          dimensions: {
            willingness_to_pay: { score: 2, evidence_refs: ["EV-uvd-4"], basis: "仅口头意愿，按 KB-USR-F07 记为弱信号（E2）" },
            virality: { score: 2, evidence_refs: ["EV-uvd-4"], basis: "私用场景，缺乏天然传播动机（E2 模拟）" },
          },
          flags: { value_communication_failure: true, retention_risk: true, high_switching_friction: true, politeness_only_feedback: false },
        };

      case "s6":
        return {
          status: "completed",
          detail: "2 validation plans designed (design only; execution is human)",
          plans: input?.runtime?.mode === "version_regression" ? [] : (() => {
            const candidates = [
            {
              plan_id: "VP1",
              hypothesis_id: "H1",
              hypothesis: "目标用户每周为筛选重复资料花费 2 小时以上",
              validation_target: { falsifiable_statement: "若 <60% 受访者描述出每周 2 小时以上的真实耗时，则该假设被证伪", proves_or_disproves: "both" },
              target_participants: { persona_ids: ["P1", "P3"], segment_label: "距初试 6 个月内的二战考研生", must_be_real_user: true },
              recruitment_criteria: {
                inclusion: ["近 1 个月内自行整理过考研资料", "距初试 6 个月内"],
                exclusion: ["从未自行搜集资料者"],
                screening_questions: ["你上一次整理资料是什么时候？", "那次大概花了多少时间？"],
                channels: ["学校自习室", "考研微信群"],
                pii_handling_note: "仅登记联系方式用于约访，由人工在审批后收集；本 Skill 不采集任何个人信息",
              },
              method: "problem_interview",
              method_rationale: "需求真实性与痛点强度属行为类问题，按 KB-USR-V02 用问题访谈而非问卷",
              sample_size: { value: 6, unit: "persons_per_persona", basis: "KB-USR-V03：每 Persona 5–8 人", saturation_rule: "连续 2–3 场无新主题即停", underpowered: false },
              tasks_or_questions: [
                { item_id: "Q1", content: "你上一次整理重复资料是什么时候？", kind: "past_behavior_question", question_type: "last_occurrence", reused_from_previous_round: false },
                { item_id: "Q2", content: "那次花了多少时间／花了多少钱？", kind: "cost_question", question_type: "past_time_cost", reused_from_previous_round: false },
              ],
              success_metrics: [{ metric_id: "M1", metric: "主动描述出每周 ≥2 小时真实耗时的受访者比例", metric_type: "behavioral", measurement_type: "rate", observable_event: "participant_reports_past_weekly_sorting_time", commitment_type: null, measurable: true }],
              success_threshold: { metric_id: "M1", operator: ">=", value: 60, unit: "percent", expression: "≥60% 受访者主动提及每周 2 小时以上耗时", basis: "KB-USR-V03 建议阈值", reused_from_previous_round: false, change_reason: null },
              duration: { weeks: 2, fits_constraints: true, note: null },
              estimated_cost: { money_cny: 600, person_days: 3, confidence: "medium" },
              current_evidence_level: "E2",
              target_evidence_level: "E3",
              evidence_upgrade: [{ claim_id: "H1", claim: "目标用户每周为筛选重复资料花费 2 小时以上", from_tier: "E2", to_tier: "E3", upgrade_condition: "≥60% 受访者主动描述真实耗时且达到饱和" }],
              priority_rank: 1,
              execution_owner: "human",
              needs_human_review: true,
              external_actions_required: ["recruit_participants"],
              risks_and_limits: ["访谈样本偏向自习室高频用户，可能高估耗时"],
              carried_from_previous: false,
            },
            {
              plan_id: "VP2",
              hypothesis_id: "H2",
              hypothesis: "学生用户愿意为该产品付费 50 元以内",
              validation_target: { falsifiable_statement: "若真实留资/预付转化 <10%，则付费意愿假设被证伪", proves_or_disproves: "both" },
              target_participants: { persona_ids: ["P1"], segment_label: "距初试 6 个月内的二战考研生", must_be_real_user: true },
              recruitment_criteria: {
                inclusion: ["访问定价页的真实用户"],
                exclusion: ["团队成员及其熟人"],
                screening_questions: ["你目前处于备考第几轮？"],
                channels: ["考研微信群"],
                pii_handling_note: "留资仅收集单一联系方式，人工审批后执行；本 Skill 只设计不执行",
              },
              method: "pricing_experiment",
              method_rationale: "付费意愿必须由真实承诺行为验证，口头询问无效（KB-USR-V02）",
              sample_size: { value: 100, unit: "persons_total", basis: "KB-USR-V03：定价实验需足量曝光", saturation_rule: null, underpowered: false },
              tasks_or_questions: [{ item_id: "T1", content: "在定价页选择套餐并提交预约", kind: "task", question_type: null, reused_from_previous_round: false }],
              success_metrics: [{ metric_id: "M2", metric: "真实预约转化率", metric_type: "commitment", measurement_type: "commitment", observable_event: "reservation_created", commitment_type: "reservation_created", measurable: true }],
              success_threshold: { metric_id: "M2", operator: ">=", value: 10, unit: "percent", expression: "真实预约转化 ≥10%", basis: "KB-USR-V03 建议阈值（视客单价调整）", reused_from_previous_round: false, change_reason: null },
              duration: { weeks: 3, fits_constraints: true, note: null },
              estimated_cost: { money_cny: 1500, person_days: 4, confidence: "low" },
              current_evidence_level: "E0",
              target_evidence_level: "E4",
              evidence_upgrade: opts.badUpgrade
                ? [{ claim_id: "H2", claim: "学生用户愿意付费", from_tier: "E0", to_tier: "E0", upgrade_condition: "不升级（用于测试拒绝路径）" }]
                : [{ claim_id: "H2", claim: "学生用户愿意为该产品付费 50 元以内", from_tier: "E0", to_tier: "E4", upgrade_condition: "留资转化 ≥10% 且为真实用户行为" }],
              priority_rank: 2,
              execution_owner: "human",
              needs_human_review: true,
              external_actions_required: ["publish_landing_page", "collect_personal_data"],
              risks_and_limits: ["落地页转化受文案影响大，不能单独证明长期付费"],
              carried_from_previous: false,
            },
            ];
            const base = structuredClone(candidates[0]);
            candidates.push({
              ...base,
              plan_id: "VP3",
              hypothesis_id: "H3",
              hypothesis: "机构型用户的合规顾虑会阻断采购",
              validation_target: { falsifiable_statement: "若少于 60% 的真实机构用户能描述最近一次因合规要求放弃采购，则该阻断假设不成立", proves_or_disproves: "both" },
              target_participants: { ...base.target_participants, persona_ids: ["P2"], segment_label: "机构型用户" },
              tasks_or_questions: [
                { item_id: "Q1", content: "你上一次因为合规要求放弃或延迟采购是什么时候？", kind: "past_behavior_question", question_type: "last_occurrence", reused_from_previous_round: false },
                { item_id: "Q2", content: "当时具体哪项要求阻断了流程，最后如何处理？", kind: "past_behavior_question", question_type: "past_failure", reused_from_previous_round: false },
              ],
              success_metrics: [{ metric_id: "M3", metric: "能描述最近一次真实合规阻断行为的机构用户比例", metric_type: "behavioral", measurement_type: "rate", observable_event: "participant_reports_recent_compliance_block", commitment_type: null, measurable: true }],
              success_threshold: { metric_id: "M3", operator: ">=", value: 60, unit: "percent", expression: "≥60% 受访机构用户描述最近一次真实合规阻断行为", basis: "预注册的行为证据阈值", reused_from_previous_round: false, change_reason: null },
              current_evidence_level: "E2",
              target_evidence_level: "E3",
              evidence_upgrade: [{ claim_id: "H3", claim: "机构型用户的合规顾虑会阻断采购", from_tier: "E2", to_tier: "E3", upgrade_condition: "至少 60% 的真实机构用户描述最近一次合规阻断行为" }],
              priority_rank: 3,
            });
            return candidates;
          })(),
        };

      default:
        return { status: "completed", detail: `${step.id} executed` };
    }
  };
}

export { PERSONAS as REFERENCE_PERSONAS, TS as REFERENCE_TIMESTAMP };
