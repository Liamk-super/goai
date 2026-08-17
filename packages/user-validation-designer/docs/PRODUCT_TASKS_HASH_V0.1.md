# product_tasks_hash 规范化算法 V1.0.4（UVD reference contract）

> 状态：**仅 UVD 已实现**。关闭 `user-validation-designer / NEW-DECISION-U07`；
> 它是 `product-technical-audit / NEW-DECISION-05` 的建议采纳对象，但 PTA 当前尚未接入，不能宣称跨 Skill hash 已实现。
> 决策依据：`DECISIONS_V0.1.md` D-05。

## 一、为什么需要它

V1/V2 复验的全部可比性都挂在一个前提上：**两轮跑的是同一套任务基线**。若两个 Skill 各自实现规范化，同一组任务会算出不同 hash，跨 Agent 与跨版本的比较立即失效，而失效方式是静默的——不会报错，只会给出看起来合理但不可比的结论。

因此本算法是 UVD 的 reference contract。未来 PTA 接入时必须对齐逐字节结果；在此之前只保证 UVD 内部与跨轮稳定性。

## 二、算法定义

当前输入是 UVD 的 `product_tasks[]`。PTA 的 `audit.core_tasks[]` 映射属于后续 Runtime Integration。

### 步骤

1. **字段筛选**。每个任务只取三个语义字段：
   - `task_key`
   - `description`
   - `expected_observable_outcome`

   **`max_steps` 不参与哈希**。它是执行预算，调整它不改变"验证的是哪件事"。若把它纳入，把 `max_steps` 从 10 调到 12 就会让复验判定为不可比，属误报。

2. **字符串归一化**（对上述三个字段的值逐一施加，顺序固定）：
   1. Unicode NFC 归一化（`String.prototype.normalize("NFC")`）；
   2. 裁剪首尾空白（`trim()`）；
   3. 内部连续空白（含制表、换行、全角空格 `\u3000`）折叠为单个 ASCII 空格。

   **不做**大小写转换（`task_key` 大小写有语义）、**不做**标点归一化（中英文标点差异可能改变任务含义）。

3. **排序**。按归一化后的 `task_key` 的 **UTF-16 码元序**（JavaScript 默认 `Array.prototype.sort` 的字符串比较）升序排序，消除数组书写顺序的影响。
   `task_key` 重复 → **抛错**，不静默去重（重复键说明任务定义本身有歧义）。

4. **序列化**。构造紧凑 JSON，键序固定为 `task_key` → `description` → `expected_observable_outcome`，无缩进、无多余空白：

   ```
   [{"task_key":"...","description":"...","expected_observable_outcome":"..."},...]
   ```

5. **摘要**。对步骤 4 的 UTF-8 字节取 `sha256`，输出 **64 位小写十六进制**。

### 边界情形

| 情形 | 结果 |
|---|---|
| 数组为空或为 `null`/`undefined` | 返回 `null`（**不是**空串的哈希）。表示"无任务基线"，与"任务基线为空集"区分开 |
| 任一必需字段缺失或归一化后为空串 | 抛错 `invalid_task_for_hash`；调用方转 `blocked / script_mismatch` |
| `task_key` 重复 | 抛错 `duplicate_task_key` |

### 参考实现（第三步落地为 `src/product-tasks-hash.mjs`）

```js
import { createHash } from "node:crypto";

const HASH_FIELDS = ["task_key", "description", "expected_observable_outcome"];

function normalizeText(value) {
  if (typeof value !== "string") throw new Error("invalid_task_for_hash");
  const normalized = value.normalize("NFC").trim().replace(/\s+/gu, " ");
  if (normalized.length === 0) throw new Error("invalid_task_for_hash");
  return normalized;
}

/** @returns {string|null} 64-char lowercase hex, or null when there is no baseline. */
export function productTasksHash(tasks) {
  if (!Array.isArray(tasks) || tasks.length === 0) return null;

  const canonical = tasks.map((task) => {
    const entry = {};
    for (const field of HASH_FIELDS) entry[field] = normalizeText(task?.[field]);
    return entry;
  });

  canonical.sort((a, b) => (a.task_key < b.task_key ? -1 : a.task_key > b.task_key ? 1 : 0));
  for (let i = 1; i < canonical.length; i += 1) {
    if (canonical[i].task_key === canonical[i - 1].task_key) {
      throw new Error("duplicate_task_key");
    }
  }

  const serialized = JSON.stringify(canonical);
  return createHash("sha256").update(serialized, "utf8").digest("hex");
}
```

## 三、锁定用测试向量

第三步的契约测试必须包含以下向量，防止后续"优化"悄悄改变行为。

**向量 A — 顺序无关**

```json
[{"task_key":"t2","description":"B","expected_observable_outcome":"Y"},
 {"task_key":"t1","description":"A","expected_observable_outcome":"X"}]
```
与

```json
[{"task_key":"t1","description":"A","expected_observable_outcome":"X"},
 {"task_key":"t2","description":"B","expected_observable_outcome":"Y"}]
```
→ hash 必须相等。

**向量 B — 空白无关**

`{"task_key":"t1","description":"  上传   产品材料 ","expected_observable_outcome":"X"}`
与
`{"task_key":"t1","description":"上传 产品材料","expected_observable_outcome":"X"}`
→ hash 必须相等。

**向量 C — `max_steps` 无关**

同一任务，`max_steps` 分别为 `5` / `12` / 缺失 → hash 必须相等。

**向量 D — 语义敏感**

`description` 改一个字 → hash 必须不同。

**向量 E — 空基线**

`null` / `[]` → 均返回 `null`，且 `null !== createHash(...).digest("hex")` 于任何输入。

**向量 F — 未来跨 Skill 一致性（当前未接入）**

未来同一组任务分别以 `product_tasks` 与 `audit.core_tasks` 传入两个 Skill 时结果必须相等；当前只锁定 UVD reference vector，不声称 PTA 已通过。

## 四、变更纪律

本算法任何变更都必须：

1. 提升本文档版本号（`V0.1` → `V0.2`）；
2. UVD 变更必须保持 reference vectors；PTA 接入后再升级为双方同步变更纪律；
3. 在 `regression_comparison.standard_change_reasons[]` 中记录，并把受影响的历史轮次标记为 `incomparable` —— 换算法等于换基线，旧 hash 与新 hash 之间**不存在**合法映射，不得假装可比。
