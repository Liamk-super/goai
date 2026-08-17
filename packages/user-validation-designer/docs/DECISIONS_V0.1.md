# user-validation-designer / 决策冻结记录 V1.0.4

> 本文件记录第二步遗留的待确认项的**最终裁定**。冻结项不再重开；代码与 Schema 以本文件为准。
> 上游来源：用户指令（2026-08-08，第三步前置冻结）。
> 未列入本文件的 `NEW-DECISION` 视为仍开放，见 `SKILL_SPEC_V0.1.md` 第十四节。

---

## D-01（关闭 C1 / C2 / NEW-DECISION-U02）用户侧五档判断与折算口径

**裁定**：本 Skill 内部保留用户侧 KB 的完整五档判断，落在**独立字段** `structured_output.user_value_judgment`。

```
enum: [strong, medium, weak, very_weak, unverified]
```

判定规则**严格沿用 KB-USR-VS01 / VS03，不得修改**：

| 条件 | user_value_judgment |
|---|---|
| `normalized_total >= 80` **且** `dimensions.demand_strength.score >= 4` | `strong` |
| `65 <= normalized_total <= 79` | `medium` |
| `50 <= normalized_total <= 64` | `weak` |
| `normalized_total < 50` | `very_weak` |
| 因证据不足而 `counted=false` 的维度 **>= 3** | `unverified`（优先级高于分数） |
| 不存在任何 E3+ 真实用户证据 | 上限 `medium`，并置 `preliminary=true` / `evidence_ceiling="E2"` |

**折算口径（本条为 U02 的关闭内容）**
KB-USR-VS01 规定「某维度只有 ≤E1 支撑则不计分，总分按已计分权重折算」，而 VS03 的 80/65/50 阈值按满分 100 定义。为使二者可比，固定如下三字段，全部由程序计算：

```
raw_total        = Σ over counted dims of (score / 5 × weight)
counted_weight   = Σ over counted dims of weight
normalized_total = counted_weight > 0 ? round(raw_total / counted_weight × 100, 2) : null
```

- **判断阈值一律作用于 `normalized_total`**，VS03 的数值不变。
- `raw_total` 与 `counted_weight` 同时输出，保留审计与改判空间。
- `counted_weight == 0` → `normalized_total = null` → `user_value_judgment = unverified`。
- 封顶（无 E3+ → 上限 `medium`）在阈值映射**之后**施加，不改变 `normalized_total` 数值，只改变输出档位，并在 `user_value_ceiling` 中记明原因。

**理由**：KB 的「折算」本身就要求重新标定分母；归一化到 100 是唯一能让 VS03 阈值保持原义的做法。若不归一化，缺失一个 15 权重维度会使同样表现的产品分数从 72 掉到 61，跨版本不可比。

---

## D-02（关闭 C4 / NEW-DECISION-U04）公共 `overall_judgment` 复用

**裁定**：`overall_judgment` 是跨 Agent、供评审主管消费的**公共字段**，不新建五值枚举。直接复用 `product-technical-audit` 的既有定义。

已核实来源：`skills/product-technical-audit/schema/output.schema.json` 第 68–71 行

```json
"overall_judgment": {
  "type": "string",
  "enum": ["strong", "medium", "weak", "insufficient_evidence"]
}
```

字段名、枚举字符串、语义完全沿用，**第四值为 `insufficient_evidence`**（不是 `unverified`，不改名）。

**映射表（程序实现，单向，不可逆推）**

| `user_value_judgment`（用户侧细粒度） | `overall_judgment`（公共交接） |
|---|---|
| `strong` | `strong` |
| `medium` | `medium` |
| `weak` | `weak` |
| `very_weak` | `weak` |
| `unverified` | `insufficient_evidence` |

**blocked / failed 场景**：以 `status` 为主。`overall_judgment` 在输出 wrapper 中必填，此时一律填 `insufficient_evidence`，**禁止填 `weak`**——`weak` 表示"已评估且结论偏弱"，与"未能评估"语义不同，混用会让主管把未验证误读为负面结论。同时 `user_value_judgment` 填 `unverified`。

**字段职责边界**
- `user_value_judgment`：用户研究模块自己的业务判断，含 `very_weak` 这一 KB 特有档位，只在本 Skill 报告与用户侧分析中使用。
- `overall_judgment`：多 Agent 统一交接判断，主管与证据校准 Agent 消费。
- 两者**不合并**，也不允许下游从 `overall_judgment` 反推 `very_weak`（`weak` 与 `very_weak` 在公共层合并是有意的信息压缩，细粒度请读 `user_value_judgment`）。

---

## D-03（关闭 NEW-DECISION-U05）六维 weight 枚举独立

**裁定**：本 Skill 独立定义 dimension weight `enum: [10, 15, 20]`。

**不修改** `product-technical-audit` 已有的 `enum: [15, 20, 25]`。两套评分体系服务不同问题（实现能力 vs 用户价值），权重集合本就不同；强行共用会污染第一个 Skill 已通过的 12 项契约测试。

本 Skill 六维权重（KB-USR-VS01，合计 100）：

| 维度 | weight |
|---|---|
| `demand_strength` | 20 |
| `usage_frequency` | 20 |
| `pain_severity` | 20 |
| `alternative_gap` | 15 |
| `willingness_to_pay` | 15 |
| `virality` | 10 |

---

## D-04（关闭 NEW-DECISION-U06）证据有效期不在本 Skill 定义

**裁定**：本 Skill **不自行定义** `expiry` 默认有效期。

- `existing_user_evidence[]` 的每一项**必须显式携带** `timestamp`；`expiry` 为可选但强烈建议携带。
- 本 Skill 产出的 Evidence Card `timestamp` 与 `expiry` 均为 **required**，由调用方或运行时显式提供，程序不填默认值。
- **证据是否过期、过期后能否支撑结论，由 `evidence_calibration_agent` 统一判断。** 本 Skill 只做两件事：原样透传时间字段；在 `evidence_level_summary` 中标注哪些证据缺少时间信息（进 `missing_information`）。
- 若 `expiry` 缺失，本 Skill **不推断**、不按"永久有效"处理，而是标 `expiry_unknown` 并交由校准 Agent 裁定。

承接 `product-technical-audit` 的 `NEW-DECISION-04`，处理方式一致。

---

## D-05（关闭 NEW-DECISION-U07）`product_tasks_hash` 规范化算法统一

**裁定**：必须与 `product-technical-audit` 使用**完全相同**的规范化算法。同一组任务无论由哪个 Skill 处理，都必须得到相同 hash，V1/V2 复验才能确认使用的是同一套任务基线。

规范定义落在 `docs/PRODUCT_TASKS_HASH_V0.1.md`，作为**两个 Skill 共用的跨 Skill 契约**。要点：

- 只对语义字段取哈希：`task_key` / `description` / `expected_observable_outcome`；`max_steps` 不参与（执行预算变化不改变任务基线）。
- 字符串 NFC 归一化 + 首尾空白裁剪 + 内部连续空白折叠为单个空格。
- 按 `task_key` 的 UTF-8 码点序排序，消除数组顺序影响。
- 紧凑 JSON 序列化（无空格、键序固定）后取 `sha256`，输出 64 位小写 hex。

**待办（不阻塞本 Skill）**：`product-technical-audit` 的 `NEW-DECISION-05` 应在其下一次修订中采纳同一文档，而不是各自发明。本 Skill 不擅自修改第一个 Skill 的文件；第三步实现时会提供可复用的 `product-tasks-hash.mjs`，并在第一个 Skill 采纳前用一致性测试锁定行为。

---

## D-06（关闭 NEW-DECISION-U08）`validate.mjs` 副本与漂移防护

**裁定**：第一版允许在本 Skill 内保留 `src/validate.mjs` 独立副本，以保证 Skill 可独立分发（符合赛道开源复用要求）。

**约束**：其公共 wrapper 与 schema 校验行为必须与 `product-technical-audit` 的副本保持兼容；当前由 `skills/_shared/tests/evidence-card-parity-test.mjs` 直接验证 canonical 与两个 Skill 的实例和 schema 一致性：

1. 对同一组（合法 + 各类非法）取样输入，两个 validator 的 `valid` 布尔值必须一致；
2. 两者 `SUPPORTED` 关键字集合必须相同——集合漂移会导致一方静默放行另一方拒绝；
3. 同一份 wrapper 样本在两个 validator 下都必须通过；
4. 任一断言失败即测试失败，提示"两个 validator 已漂移，请同步"。

**已发现并必须遵守的兼容性事实**：该 validator **不支持 `const`**，且未知关键字会被记为**校验错误**（`validate.mjs:15-34, 69-71`）。因此本 Skill 全部 Schema：

- 用 `"enum": [true]` 表达布尔常量，不用 `"const": true`；
- 不使用 `oneOf` / `anyOf` / `allOf` / `not` / `format` / `default` / `patternProperties` / 对象形式的 `additionalProperties`；
- 可用关键字集合限定为：`$schema` `$id` `title` `description` `definitions` `type` `enum` `required` `properties` `additionalProperties(false)` `items` `minItems` `maxItems` `minLength` `minimum` `maximum` `pattern` `$ref`。

---

## 冻结项汇总

| ID | 原编号 | 状态 |
|---|---|---|
| D-01 | C1 / C2 / NEW-DECISION-U02 | **已关闭** |
| D-02 | C4 / NEW-DECISION-U04 | **已关闭** |
| D-03 | C3 / NEW-DECISION-U05 | **已关闭** |
| D-04 | NEW-DECISION-U06 | **已关闭** |
| D-05 | NEW-DECISION-U07 | **已关闭**（算法已定；第一个 Skill 采纳为待办，不阻塞） |
| D-06 | NEW-DECISION-U08 | **已关闭** |

V1 Freeze 最终裁定：`NEW-DECISION-U01`（Persona 同质化五键 ≥4 相同判据）、`NEW-DECISION-U03`（假设优先级公式）、`NEW-DECISION-U09`（模拟卡与 caller evidence 的 canonical `content_hash` 取材范围）均已接受并冻结。后续变更必须进入新的 Major/Minor Contract Version。
