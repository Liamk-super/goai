export type FieldWeight = "primary" | "base" | "tertiary";

export type IntakeField = {
  key: string;
  label: string;
  hint: string;
  placeholder: string;
  weight: FieldWeight;
};

export type IntakeSection = {
  id: string;
  code: string;
  title: string;
  subtitle: string;
  fields: IntakeField[];
};

export const INTAKE_SECTIONS: IntakeSection[] = [
  {
    id: "product",
    code: "I",
    title: "产品材料",
    subtitle: "问题、核心功能与可检查入口",
    fields: [
      {
        key: "one_line_value_claim",
        label: "一句话价值主张",
        hint: "用一句话说明为哪类用户，在什么场景下，带来什么可观察价值。",
        placeholder: "帮助……在……情况下完成……",
        weight: "primary",
      },
      {
        key: "problem",
        label: "产品解决什么问题",
        hint: "这是最影响结论的一项。写清真实痛点和它发生的场景。",
        placeholder: "谁，在什么情况下，卡在哪一步",
        weight: "primary",
      },
      {
        key: "core_features",
        label: "核心功能",
        hint: "1—3 个核心能力就够，不必罗列全部。",
        placeholder: "列出 1—3 个核心能力",
        weight: "base",
      },
      {
        key: "inspectable_materials",
        label: "产品网址 / 仓库地址",
        hint: "可检查的入口。仓库只读，不写入。",
        placeholder: "https://",
        weight: "tertiary",
      },
    ],
  },
  {
    id: "team",
    code: "II",
    title: "团队能力",
    subtitle: "角色、能力与当前交付边界",
    fields: [
      {
        key: "team",
        label: "团队与分工",
        hint: "核心成员、负责领域和投入方式。",
        placeholder: "谁负责什么，全职还是兼职",
        weight: "base",
      },
      {
        key: "stage",
        label: "当前产品阶段",
        hint: "不同阶段会采用不同的预测标准。",
        placeholder: "想法 / 原型 / 内测 / 已上线",
        weight: "tertiary",
      },
    ],
  },
  {
    id: "market",
    code: "III",
    title: "用户与经营",
    subtitle: "使用者、付费者与验证目标",
    fields: [
      {
        key: "target_user",
        label: "目标用户 / 使用者",
        hint: "谁在什么情况下使用。",
        placeholder: "使用者的身份与处境",
        weight: "base",
      },
      {
        key: "payer",
        label: "付费者",
        hint: "使用者和付费者经常不是同一个人。",
        placeholder: "谁做购买决策并承担成本",
        weight: "base",
      },
      {
        key: "validation_goal",
        label: "本轮最想预测什么",
        hint: "写得越具体，预测结果越有用。",
        placeholder: "例如：是否值得继续投入并推向市场",
        weight: "primary",
      },
    ],
  },
  {
    id: "geo",
    code: "IV",
    title: "时间与地域",
    subtitle: "市场边界、政策与信息时效",
    fields: [
      {
        key: "region",
        label: "目标国家或地区",
        hint: "地域决定适用哪套政策与平台规则。",
        placeholder: "例如：中国香港 / 东南亚",
        weight: "tertiary",
      },
      {
        key: "timing",
        label: "计划时间与窗口",
        hint: "上线时间、窗口期或截止日期。",
        placeholder: "上线时间或窗口期",
        weight: "tertiary",
      },
    ],
  },
];

export const ALL_INTAKE_FIELDS = INTAKE_SECTIONS.flatMap(section => section.fields);

export const REQUIRED_FIELDS = [
  "one_line_value_claim",
  "problem",
  "core_features",
  "target_user",
  "payer",
  "stage",
  "validation_goal",
  "region",
  "inspectable_materials",
];

export type FieldSource = "user" | "model" | "missing" | "unknown";

/** 手动输入永远压过模型草稿；模型只补空位。返回合并结果与每个字段的来源。 */
export function mergeExtraction(
  manual: Record<string, string>,
  extracted: Record<string, string | null>,
): { fields: Record<string, string>; sources: Record<string, FieldSource> } {
  const fields: Record<string, string> = {};
  const sources: Record<string, FieldSource> = {};
  const keys = new Set([...Object.keys(extracted), ...Object.keys(manual)]);
  for (const key of keys) {
    const manualValue = manual[key]?.trim();
    const modelValue = extracted[key]?.trim();
    if (manualValue) {
      fields[key] = manual[key];
      sources[key] = "user";
    } else if (modelValue) {
      fields[key] = modelValue;
      sources[key] = "model";
    }
  }
  return { fields, sources };
}

export function fieldSourceOf(
  key: string,
  fields: Record<string, string>,
  sources: Record<string, FieldSource>,
): FieldSource {
  const value = fields[key]?.trim();
  if (!value) return "missing";
  if (value.toLowerCase() === "unknown" || value === "不知道") return "unknown";
  return sources[key] ?? "user";
}

export const SOURCE_LABELS: Record<FieldSource, string> = {
  user: "用户填写",
  model: "模型草稿",
  missing: "缺失",
  unknown: "UNKNOWN",
};

export type MaterialItem = {
  name: string;
  kind: "pdf" | "text" | "opaque";
  status: string;
  ok: boolean;
};

/** 对不能本地解析正文的文件，只承诺真实能力，不谎称"已读懂"。 */
export function describeMaterial(fileName: string, mimeType: string, parsedChars?: number, locale = "zh-CN"): MaterialItem {
  const chinese = locale.toLowerCase().startsWith("zh");
  if (mimeType === "application/pdf") {
    return {
      name: fileName,
      kind: "pdf",
      status: parsedChars
        ? chinese
          ? `已本地读取 ${parsedChars.toLocaleString("zh-CN")} 字符，可参与模型提取`
          : `${parsedChars.toLocaleString("en")} characters read locally and available for model extraction`
        : chinese ? "PDF 已加入，等待本地读取" : "PDF added and awaiting local extraction",
      ok: Boolean(parsedChars),
    };
  }
  if (mimeType.startsWith("text/")) {
    return { name: fileName, kind: "text", status: chinese ? "文本文件已加入" : "Text file added", ok: true };
  }
  return {
    name: fileName,
    kind: "opaque",
    status: chinese ? "已作为私有材料加入，支持读取时会用于预测" : "Added as private material; it will be used for prediction when supported",
    ok: true,
  };
}

export function completionOf(fields: Record<string, string>): { filled: number; total: number; percent: number } {
  const filled = REQUIRED_FIELDS.filter(key => fields[key]?.trim()).length;
  return { filled, total: REQUIRED_FIELDS.length, percent: Math.round((filled / REQUIRED_FIELDS.length) * 100) };
}
