# user-validation-designer 状态机 V1.0.4

> 实现依据。`src/index.mjs` 的控制流必须与本文件逐条对应。
> 契约：`SKILL_SPEC_V0.1.md` 第三节；决策：`DECISIONS_V0.1.md`。

---

## 一、总图

```
                      ┌─────────────────────┐
   task envelope ───► │ GATE-0 输入校验      │
                      │ input schema + PII   │
                      └──────┬──────────┬────┘
                             │ pass     │ fail
                             ▼          ▼
                      ┌─────────────┐  blocked
                      │ S1 准入检查  │  invalid_task_envelope
                      └──┬───────┬──┘  schema_validation_failed
              executable │       │ too_broad          pii_in_input
              borderline │       ▼                    external_action_requires_approval
                         │   blocked
                         │   target_user_too_broad
                         │   insufficient_product_context
                         ▼
                  ┌─────────────┐   fail + retries<2
                  │ S2 Persona  │◄────────────┐
                  │    + JTBD   │─────────────┘
                  └──┬───────┬──┘
                pass │       │ fail + retries==2
                     │       ▼
                     │   failed / persona_modeling_failed
                     ▼
              ┌──────────────┐
              │ S3 场景/替代  │  （无条件执行）
              └──────┬───────┘
                     ▼
        ┌────────────────────────┐
        │ GATE-4a 首体验可执行?   │
        └───┬────────────────┬───┘
       yes  │                │ no
            ▼                ▼
       ┌─────────┐    first_experience = []
       │ S4a 首体验│    log: not_executable
       └────┬────┘    skip_reasons += {s4a}
            │                │
            ▼                │
   ┌──────────────────────┐  │
   │ GATE-4b 任务测试可执行?│◄─┘ （S4a 未执行 ⇒ S4b 必然不可执行）
   └───┬──────────────┬───┘
  yes  │              │ no
       ▼              ▼
  ┌─────────┐   task_test_matrix = []
  │ S4b 任务 │   log: not_executable
  └────┬────┘   failure_reason ← missing_product_task (partial)
       │              │
       └──────┬───────┘
              ▼
       ┌─────────────┐   fail + retries<2
       │ S5 假设归纳  │◄────────────┐
       └──┬───────┬──┘─────────────┘
     pass │       │ fail + retries==2
          │       ▼
          │   partial / simulation_invalid + needs_human_review
          │       │
          ▼       ▼
       ┌──────────────┐
       │ S6 验证方案   │
       └──┬───────┬───┘
     ok   │       │ unsupported_validation_method
          │       ▼
          │   failed
          ▼
    ┌──────────────────────────────┐
    │ S7 装配（程序，无 LLM）        │
    │ 1 证据校验与降级 A-01/A-28     │
    │ 2 冲突裁决 A-06               │
    │ 3 六维评分 A-17               │
    │ 4 封顶与闸门 A-18/A-19        │
    │ 5 user_value_judgment D-01    │
    │ 6 → overall_judgment D-02     │
    │ 7 交叉引用检查 A-12/A-14       │
    │ 8 交接包切分                  │
    │ 9 output schema 自校验 A-26   │
    └──────┬────────────────┬──────┘
      pass │                │ fail
           ▼                ▼
   completed / partial   failed / invalid_output_schema
```

---

## 二、GATE-0 输入校验

顺序固定，短路返回。**顺序本身是安全属性**：PII 扫描必须在任何内容进入模型上下文之前完成。

| 序 | 检查 | 失败结果 |
|---|---|---|
| 1 | `task_id` / `project_id` / `product_version` / `validation_goal.objective` 存在且非空 | `blocked / invalid_task_envelope` |
| 2 | PII 与凭据扫描（字段名 + 值形态） | `blocked / pii_in_input`，`retryable=false`，`needs_human_review=true` |
| 3 | input schema 校验 | `blocked / schema_validation_failed`，`needs_human_review=true` |
| 4 | 请求含被禁外部动作 | `blocked / external_action_requires_approval`，`retryable=false` |
| 5 | `mode == version_regression` 时：`previous_validation_results` 存在、`product_tasks` 非空、重算 hash 与上一轮一致 | `blocked / script_mismatch` |
| 6 | `runtime.product_tasks_hash` 存在时与重算值一致 | `blocked / script_mismatch` |
| 7 | `simulation_engine` 未绑定 | `blocked / tool_unavailable`（无模拟引擎则 S2–S5 全不可执行，无有效产出） |

**blocked 返回形状**：`overall_judgment="insufficient_evidence"`、`user_value_judgment="unverified"`、所有维度 `score=null` / `counted=false`、`evidence_cards=[]`、`execution_log` 至少一条记明阻塞点。绝不返回伪造 Persona 或分数。

---

## 三、单元门控条件

```js
// GATE-4a
canRunFirstExperience =
     (product_profile.url != null || product_profile.experience_report_ref != null)
  && availability.product_reader === "available"
  && availability.simulation_engine === "available";

// GATE-4b —— 注意对 S4a 的依赖：没有首体验就没有产品接触面，
// 任务测试无从执行；这一依赖是防伪造的第二道锁。
canRunTaskTest =
     canRunFirstExperience
  && Array.isArray(product_tasks) && product_tasks.length > 0;
```

S5 只依赖成功的 S2/S3；S6 只依赖 S1/S2/S3/S5。S4a/S4b 的结果是可选输入，合法的 `not_executable` 不会传播成 S5/S6 依赖失败。

不可执行时的**强制副作用**（A-04 / A-05）：

| 单元 | 强制置空字段 | execution_log.outcome | 附加 |
|---|---|---|---|
| S4a | `simulated_findings.first_experience = []` | `not_executable` | `skip_reasons += {unit:"s4a", missing_input:...}` |
| S4b | `simulated_findings.task_test_matrix = []` | `not_executable` | `failure_reason ← missing_product_task`（status 降 `partial`）；`missing_information += {field:"product_tasks"}` |

**输出后过滤**：即使模型在不可执行的情况下仍返回了内容，S7 第 1 步会将其丢弃并置 `flags.fabrication_blocked = true`。门控与过滤是两道独立防线，不可省其一。

---

## 四、重跑控制

两处重跑，语义不同，**都不是同参重放**。

### S2 Persona 重跑（A-07 / A-08）

```
attempt 0: 正常生成
  ├─ verdict == pass → 继续 S3
  └─ verdict == fail → attempt 1（强制注入约束：不同替代方案 + 不同预算约束 + 补齐缺失 archetype）
        ├─ pass → 继续，persona_set_check.retries_used = 1
        └─ fail → attempt 2（同上，进一步强制 edge_case 的能力/环境限制）
              ├─ pass → 继续，retries_used = 2
              └─ fail → failed / persona_modeling_failed
                        needs_human_review = true
                        保留 S1 产物 + persona_set_check 诊断（homogeneous_pairs 明示哪两个雷同）
```

### S5 模拟失真重跑（A-10）

```
attempt 0: 正常模拟
  ├─ negative_findings_count > 0 且 hidden_needs_count > 0 → pass
  └─ 任一为 0 → attempt 1（强制注入：每 Persona 至少 1 条具体不满 + 1 条疑问 +
                          企业型必须谈安全/采购/集成 B03 + 边界 Persona 必须触发限制）
        ├─ pass → realism_check.retries_used = 1
        └─ fail → attempt 2
              ├─ pass → retries_used = 2
              └─ fail → partial / simulation_invalid
                        needs_human_review = true
                        保留 S1–S4 全部产物（证据保全硬规则）
```

`max_simulation_retries` 可由 `runtime` 下调至 0 或 1，**不可上调超过 2**（schema `maximum:2`）。

---

## 五、S7 装配步骤（严格顺序）

顺序不可调换：每一步都消费前一步的结果，且封顶必须晚于评分、映射必须晚于封顶。

| 序 | 步骤 | 规则 | 关键不变量 |
|---|---|---|---|
| 1 | 伪造过滤 | A-04 / A-05 | 不可执行单元的产物一律清空 |
| 2 | Evidence Card 校验 | A-01 / A-28 | `reliability_level > E2` → 降 E2 记 `downgraded_entries`；缺 `expiry` → `expiry_unknown_refs`；孤儿卡（`supporting_claims` 空）剔除 |
| 3 | 冲突裁决 | A-06 | E3+ 胜；模拟侧记 `demoted_ref`；**不取平均**；`persona_vs_persona` 一律 `both_retained` |
| 4 | 痛点优先级 | — | `frequency × severity × workaround_cost` 回填 |
| 5 | 四力判定 | A-11 | `workaround_cost>=4` → `push>=4`；再判 `verdict` |
| 6 | 维度 `counted` 判定 | A-17 | 仅 ≤E1 支撑 → `counted=false`, `score=null` |
| 7 | 三值计算 | D-01 | `raw_total` / `counted_weight` / `normalized_total` |
| 8 | 五档映射 | D-01 §6.2 | `uncounted>=3` 优先判 `unverified` |
| 9 | 封顶 | A-18 | 无 E3+ → 压至 `medium`，置 `preliminary` / `evidence_ceiling` |
| 10 | 公共映射 | D-02 | `very_weak→weak`，`unverified→insufficient_evidence` |
| 11 | `evidence_confidence` | §6.4 | `partial` 时至多 `medium` |
| 12 | 假设-方案交叉引用 | A-12 | `open` 假设须有 `linked_plan_ids` 或 `deferred_reason` |
| 13 | 方案可升级性 | A-14 / A-15 | 不升级或方法非法 → 剔除；`claim_id` 必须存在于 `per_claim` 且 `current_tier == from_tier` |
| 14 | 强制审批位 | A-16 | 每个方案 `needs_human_review=true`；顶层 `needs_human_review` 取或 |
| 15 | 复验台账 | R-01~R-08 | `inheritance_check.complete` 必须 true；`progress_verdict` 判定 |
| 16 | 交接包切分 | §12 | 按字段切分，不群发同一份报告 |
| 17 | 输出自校验 | A-26 | 不过 → `failed / invalid_output_schema` |

---

## 六、状态判定

```js
function resolveStatus(log, hasAnyValidProduct) {
  if (blockedAtGate)                      return "blocked";
  if (outputSchemaFailed)                 return "failed";
  if (personaModelingExhausted)           return "failed";
  if (unsupportedValidationMethod)        return "failed";
  if (!hasAnyValidProduct)                return "failed";
  const incomplete = log.some(e =>
    ["not_executable", "failed", "skipped"].includes(e.outcome));
  if (simulationInvalidExhausted)         return "partial";
  if (incomplete)                         return "partial";
  return "completed";
}
```

**`completed` 的含义**：S1–S6 全部完成。S4a/S4b 因输入缺失而 `not_executable` 会使 status 降为 `partial` —— 这是有意的：缺了任务测试的用户价值判断，证据强度确实不同，不应与完整运行同级呈现。

**证据保全（贯穿所有非 completed 状态）**：已采集的 `evidence_cards` 与已成立的结构化产物原样返回。单个单元失败不得清空其他单元成果。契约测试 `Test 2` 校验。
