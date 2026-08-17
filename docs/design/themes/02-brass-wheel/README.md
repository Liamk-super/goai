# 02 · 深色黄铜转盘（当前线上）

## 是什么

`EvaluationWheel` —— 明亮实体评审仪器，拉丝金属机身 + 深色盘面。

- **内环** = 用户确认的客观资料（可点）
- **外环** = 四个评审视角（PRODUCT_IMPLEMENTATION / USER_USAGE / BUSINESS_INVESTMENT / GEO_POLICY_TREND）
- **中心** = 项目名 / 版本 / 当前阶段 / 当前 CTA（由外层渲染覆盖）
- **最外** = Evidence Auditor 校准光环（状态 / 完整度 / 已落库证据数）

盘面只被真实证据、真实状态推动；没有事件，盘面静止。

## 色板（L1）

与 `03-cream-instrument` 提案的**暖奶油**不同，这版是冷白 + 黄铜：

| 族 | 说明 |
|---|---|
| `--sw-paper-*` | 环境冷白浅灰 `#f3f6f4` 起 |
| `--sw-ink-*` | 石墨文字 `#171c1d` 起 |
| `--sw-brass-*` | 拉丝金属，机身构件 / 刻度环 / 旋钮 |
| `--sw-vermilion-*` | 信号红，当前读数 / 需要你 |
| `--sw-verdigris-*` | 校准绿，已校准的证据 |
| `--sw-azure-*` | 冷蓝，来源标记「模型推断」 |
| `--sw-abyss-*` | 深板岩，反相区块 |

> `--sw-prism-*` 四个 token 在这版里**零引用**，是死代码。

## ⚠️ 备份来源与风险

这份快照取自**工作区未提交状态**，不是某个 commit：

| 文件 | git 状态 |
|---|---|
| `EvaluationWheel.tsx` | **未跟踪**（untracked） |
| `globals.worktree.css` | 已跟踪但有约 1741 行未提交改动 |
| `wheel-state.ts` / `agent-glyphs.ts` | 组件的运行时依赖 |

也就是说，在做这份备份之前，这一版风格**只存在于工作区**——
一次 `git checkout .` 或 `git clean -fd` 就会永久消失。这也是备份它的直接理由。

## 文件

| 文件 | 说明 |
|---|---|
| `EvaluationWheel.tsx` | 组件源码（工作区版本） |
| `globals.worktree.css` | 完整 CSS（工作区版本，3052 行） |
| `wheel-state.ts` | 盘面状态计算：证据 → 格数 / 扇区 / 维度 |
| `agent-glyphs.ts` | 1+5 Agent 的字形映射 |

## 无预览图的原因

这版是当前线上样式，跑 `pnpm dev` 就能看到实物。
另外它依赖 `AgentTeamsRun` 类型的真实运行数据，静态渲染需要构造一整套 fixture，
成本高于价值——真要看，起开发服务器更快。
