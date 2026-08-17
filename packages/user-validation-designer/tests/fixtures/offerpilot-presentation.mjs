/**
 * Presentation-only Golden Case reconstructed from the real OfferPilot user
 * evidence supplied for UVD acceptance. It deliberately exercises the
 * student-developer renderer without pretending that S4 browser/task testing
 * ran when no URL/task script was available.
 */
export const OFFERPILOT_GOLDEN = Object.freeze({
  input: {
    product_profile: { name: "OfferPilot" },
  },
  ingestedEvidence: [
    {
      kind: "retention_data",
      source: "产品后台漏斗数据",
      observation: "注册 420→上传简历 286→开始面试 231→完成面试 168→30 天内第二次使用 63→3 次以上 37；当前真实付费 0 人",
    },
    {
      kind: "interview",
      source: "12 名用户访谈记录（6 份详细）",
      observation: "用户反馈第一次使用有新鲜感，但评价容易像套模板；希望围绕简历项目深入追问；有用户等待时以为卡住；也有用户只在面试密集期考虑购买。",
    },
  ],
  structured: {
    target_user_definition: { admitted: true },
    user_value_judgment: "weak",
    evidence_confidence: "medium",
    evidence_level_summary: { has_real_user_evidence: true },
    user_value_score: {
      dimensions: {
        demand_strength: { score: 3, counted: true, basis: "面试临近时需求明显，但并非所有求职学生都有强需求" },
        usage_frequency: { score: 2, counted: true, basis: "更像面试通知触发的阶段性使用" },
        pain_severity: { score: 3, counted: true, basis: "通用面经不够针对，但仍有可接受替代" },
        alternative_gap: { score: 2, counted: true, basis: "ChatGPT、面经和真人帮助能解决部分需求" },
        willingness_to_pay: { score: null, counted: false, basis: "尚未通过正式收费入口验证真实付费行为" },
        virality: { score: null, counted: false, basis: "暂无可靠的自然推荐或分享行为证据" },
      },
    },
    personas: [
      {
        persona_id: "P1",
        label: "秋招冲刺期、近期有面试的学生",
        goal_statement: "面试前快速做几轮针对性练习",
        motivation: "面试时间临近，希望低成本发现回答问题",
        rejection_reasons: ["评价如果继续像通用模板，就没有再次使用的理由"],
        behavior_keys: { alternative_in_use: "牛客面经 + 同学互练", urgency: 5 },
      },
      {
        persona_id: "P2",
        label: "准备暑期实习、仍在比较替代方案的学生",
        goal_statement: "先熟悉面试流程和常见问题",
        motivation: "希望比通用面经更针对自己的简历",
        rejection_reasons: ["ChatGPT 和免费面经已经够用，产品差异不明显时不会切换"],
        behavior_keys: { alternative_in_use: "ChatGPT + 免费面经", urgency: 3 },
      },
      {
        persona_id: "P3",
        label: "学术背景转工业界的研究生",
        goal_statement: "把学术项目讲成工业界面试能理解的回答",
        motivation: "需要产品真正理解简历中的论文和项目",
        rejection_reasons: ["师兄可以免费帮忙，时间不紧时不会优先换工具"],
        behavior_keys: { alternative_in_use: "师兄模拟 + 网上面试题", urgency: 2 },
      },
    ],
    scenarios_and_alternatives: [
      { persona_id: "P1", trigger_event: "收到或等待面试通知，只有几天准备时间" },
      { persona_id: "P2", trigger_event: "实习招聘开始，想先练一次看看效果" },
      { persona_id: "P3", trigger_event: "准备工业界岗位，需要练项目追问" },
    ],
    simulated_findings: {
      experience_issues: [
        {
          issue_id: "REAL-CONTENT-1",
          description: "面试评价多次出现相似的通用建议，用户认为像套模板",
          severity: "major",
          cause_type: "content",
        },
        {
          issue_id: "REAL-CONTENT-2",
          description: "AI 追问没有充分围绕简历项目和目标岗位深入展开",
          severity: "major",
          cause_type: "content",
        },
        {
          issue_id: "REAL-WAIT-1",
          description: "生成问题等待时间较长，有用户一度以为页面卡住",
          severity: "major",
          cause_type: "performance",
        },
      ],
      insights: [],
    },
    top_user_problems: [],
    validation_plans: [
      {
        hypothesis: "更具体、针对本次回答的诊断能否提升用户再次使用",
        method: "trial_cohort_retention",
        tasks_or_questions: [{ content: "上线新版诊断后，用同一口径比较完成首次后再次使用的比例" }],
        success_metrics: [{ metric: "再次使用率" }],
        success_threshold: { expression: "新版上线后，再次使用率明显高于修改前基线" },
        duration: { weeks: 2 },
      },
      {
        hypothesis: "流失主要来自 AI 反馈太泛，还是需求本身只在面试期出现",
        method: "problem_interview",
        tasks_or_questions: [{ content: "访谈完成过首次但没有继续使用的用户，记录他们主动提到的首要流失原因" }],
        success_metrics: [{ metric: "首要流失原因分布" }],
        success_threshold: { expression: "能够区分内容质量、低频需求和替代方案三类原因的主次" },
        duration: { weeks: 2 },
      },
    ],
    missing_information: [
      {
        field: "retention_cohort",
        why_it_matters: "还缺统一口径的 D7/D30 cohort 数据，当前只能确认 30 天内是否再次使用",
        how_to_obtain: "按首次完成日期分 cohort 重新计算",
      },
      {
        field: "payment_activation",
        why_it_matters: "尚未通过正式收费入口验证真实付费行为",
        how_to_obtain: "核心价值改善后再做真实收费测试",
      },
    ],
  },
});

