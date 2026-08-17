# LaunchScope 设计规范 · 势能引擎

> **锚句**：一台放在暖色台灯下的黄铜评审仪器，盘面只被真实证据推动。
>
> 它不是抽奖转盘（盘面不会自转，没有随机数）；不是暗色 SaaS 仪表台（不用发光、不用霓虹）；
> 不是拟物皮革（不做缝线、不做纹理贴图）。它是一台**能被复验的量具**。

本文件是 `apps/web` 与 `apps/ops` 的视觉唯一事实源。组件只允许引用 L2 语义 token，
不得直接写 hex，也不得引用 L1 原子色板。

---

## 1. 物理隐喻：单一光源

所有材质 token 都从一个物理设定推导，不是拍脑袋调的阴影：

- **光源**：左上 34% / 24%，暖白，单一光源，无补光。
- **凸起元素**：顶部有内高光 `rgba(255,255,255,.85)`，底部有阴影。
- **凹陷元素**：反过来——顶部内阴影，底部内高光。
- **阴影永远零饱和度**（纯黑 alpha）。有色相的阴影 = 廉价感，禁止。

> 违反这条的典型症状：给金色元素配金色阴影。那是发光，不是照明。

---

## 2. L1 · 原子色板

只描述颜色本身，不带含义。**组件禁止直接引用 L1。**

### 环境（暖奶油）

| Token | 值 | 用途 |
|---|---|---|
| `--sw-cream-000` | `#fdfaf8` | 最亮，凸起面 |
| `--sw-cream-050` | `#fbf7f4` | 卡片底 |
| `--sw-cream-100` | `#faf4f0` | 页面底 |
| `--sw-cream-200` | `#f5eae1` | 凹陷面 / 开发者扇区场 |
| `--sw-cream-300` | `#ecded0` | 轨道底 |
| `--sw-cream-400` | `#e8e1dc` | 最浅分隔线 |

### 暖茶（描边）

| Token | 值 |
|---|---|
| `--sw-tea-300` | `#ddcabb` |
| `--sw-tea-400` | `#cbb6a2` |
| `--sw-tea-500` | `#b8a08c` |

### 墨（零饱和度偏暖）

| Token | 值 | 承载力 |
|---|---|---|
| `--sw-ink-900` | `#000000` | 标题、正文，任意字号 |
| `--sw-ink-800` | `#2a2622` | 反相块底 |
| `--sw-ink-600` | `#55504a` | 次要文字，≥12px |
| `--sw-ink-400` | `#6b625b` | 辅助文字，≥11px（已压深到 AA） |

### 金属金（五阶）

| Token | 值 | 角色 |
|---|---|---|
| `--sw-gold-700` | `#754608` | 指针、最深金 |
| `--sw-gold-600` | `#775824` | **文字金——唯一合法的金色文字值** |
| `--sw-gold-500` | `#a38355` | **装饰金——禁止承载文字** |
| `--sw-gold-400` | `#d39847` | 开发者色域 |
| `--sw-gold-300` | `#e6c18c` | 高光、宝石 |
| `--sw-gold-100` | `#f6ecdf` | 待输入胶囊底 |

### 三色域 + 评审红

| Token | 值 | 语义 |
|---|---|---|
| `--sw-olive-700` / `-600` / `-200` / `-100` | `#4c6225` `#5b7133` `#d4d3bb` `#e6e8d5` | 投资人 / 商业 |
| `--sw-plum-700` / `-600` / `-200` / `-100` | `#72499b` `#8b63b1` `#e2d6e0` `#e8dde7` | 用户 / 共创 |
| `--sw-red-700` / `-600` / `-100` | `#ba1109` `#c92318` `#f8e2df` | 评审（独立通道） |

### 冷钢（来源标记）

| Token | 值 | 语义 |
|---|---|---|
| `--sw-steel-600` | `#2a627a` | 模型推断 |
| `--sw-steel-200` | `#bcd6e0` | 模型推断（弱） |

> **决定：`azure` 不退役，改名 `steel`。**
> 它承载真实语义——`.source-tag[data-source="model"]` 标记"这个值是模型推断的，不是用户确认的"。
> 这正是盘面边缘那句「不把猜测当事实」的实现。删掉它，UI 就无法区分猜测与事实。
> 改名是因为「azure 天蓝」描述的是颜色，「steel 冷钢」描述的是它在仪器里的角色。

### 已删除

`--sw-prism-cyan` / `-rose` / `-gold` / `-mint` —— 四个 token 零引用，是死代码。

---

## 3. L2 · 语义 token

组件**只允许**用这一层。

```css
/* 表面 */
--surface-base:    var(--sw-cream-100);   /* 页面 */
--surface-card:    var(--sw-cream-050);   /* 卡片 */
--surface-raised:  var(--sw-cream-000);   /* 凸起 */
--surface-sunk:    var(--sw-cream-200);   /* 凹陷 */
--surface-inverse: var(--sw-ink-800);     /* 反相 */

/* 文字 —— 括号内是最小合法字号 */
--ink-on-surface: var(--sw-ink-900);  /* 任意 */
--ink-secondary:  var(--sw-ink-600);  /* ≥12px */
--ink-faint:      var(--sw-ink-400);  /* ≥11px */
--ink-on-inverse: var(--sw-cream-050);

/* 金 —— 两个 token 不可互换 */
--gold-decorative: var(--sw-gold-500);  /* 只能做描边/填充，禁止文字 */
--gold-text:       var(--sw-gold-600);  /* 金色文字唯一合法值 */

/* 线 */
--rule-hair:   var(--sw-cream-400);
--rule-mid:    var(--sw-tea-300);
--rule-strong: var(--sw-tea-400);

/* 三色域 —— 只在盘面与进度条出现，不进状态胶囊 */
--zone-dev: var(--sw-gold-400);       --zone-dev-field: var(--sw-cream-200);
--zone-investor: var(--sw-olive-600); --zone-investor-field: var(--sw-olive-200);
--zone-user: var(--sw-plum-600);      --zone-user-field: var(--sw-plum-200);

/* 评审 —— 独立通道，不属于任何色域 */
--review: var(--sw-red-600);
--review-field: var(--sw-red-100);

/* 来源标记 */
--source-model: var(--sw-steel-600);
--source-user:  var(--sw-olive-700);
--source-missing: var(--sw-red-600);
```

---

## 4. 状态语义表（五态，组件不得自造）

| 状态 | 文字色 | 底色 | 含义 |
|---|---|---|---|
| 运行中 | `--sw-ink-800` | `#efe9e4` | Agent 正在采集 |
| 待输入 | `--gold-text` | `--sw-gold-100` | 等你补字段 |
| 已校准 | `--sw-olive-700` | `--sw-olive-100` | 证据已交叉验证 |
| 需注意 | `--review` | `--review-field` | 冲突/冻结，需裁决 |
| 空闲 | `--ink-faint` | `#f2ede9` | 未排程 |

**评审红是独立通道**，不参与三色域轮换。一个界面里红色只能表示"需要人来裁决"。

---

## 5. 排版

**决定：纯系统字体栈，不自托管数字子集。**

理由是这条规范自己定的约束：不允许运行时 CDN。自托管 subset 要引入构建期字体流水线、
`font-display` 策略、FOUT 处理和一份需要长期维护的二进制资产，
换来的只是数字字形略微整齐一点。仪器的可信度来自读数正确，不来自字体独特。
真正影响读数对齐的是 `tabular-nums`，那个系统等宽字体已经提供。

```css
--font-sans: -apple-system, "PingFang SC", "Microsoft YaHei",
             "Segoe UI", "Noto Sans SC", sans-serif;
/* 等宽栈按"本机实际存在的概率"排序：
   Windows 上 IBM Plex Mono 通常没有，Cascadia/Consolas 才是真正命中的那个 */
--font-mono: ui-monospace, "Cascadia Mono", "SFMono-Regular",
             Consolas, "IBM Plex Mono", monospace;
```

规则：

- **字重只有三档**：700 / 500 / 400。第四档一律拒绝。
- **中文字距为正**（`0.01em`）。汉字是等宽方块，收紧只会糊成一坨。
- **所有数字**用 `--font-mono` + `font-variant-numeric: tabular-nums`。
  数字跳动会让仪器显得不可信。
- **中央大读数**用 `.reading-hero`：76px / 700 / `tabular-nums` / `letter-spacing: -0.02em`。

---

## 6. 形状与间距

**决定：盘面用尺线，侧栏用圆角卡片。**

这不是折中，是两种物件的真实差别：仪器盘面是刻度，刻度必须是直线和锐角；
侧栏是纸卡片，纸有厚度和圆角。混用才是不一致。

```css
--r-pill:  999px;  /* 状态胶囊 */
--r-card:  16px;   /* 侧栏卡片 */
--r-input: 8px;    /* 按钮、输入框 */
/* 盘面区域：不用圆角，用 1px 尺线 */
```

间距基数 4px：`--s1: 4px` … `--s10: 40px`。描边永远 1px。

---

## 7. 材质（四个复合 token）

```css
--lift-card:                                   /* 侧栏卡片：浮起 */
  inset 0 1px 0 rgba(255,255,255,.85),
  0 1px 2px rgba(0,0,0,.04),
  0 8px 24px rgba(0,0,0,.05);

--recess-dial:                                 /* 仪器盘：嵌进机身 */
  inset 0 2px 6px rgba(0,0,0,.10),
  inset 0 -1px 1px rgba(255,255,255,.80);

--metal-rim:                                   /* 金属圈：三层同心 */
  0 0 0 1px var(--sw-tea-400),
  0 0 0 6px var(--sw-cream-050),
  0 0 0 7px var(--sw-tea-300);

--pill-raise:                                  /* 胶囊：微凸 */
  inset 0 1px 0 rgba(255,255,255,.75),
  0 1px 2px rgba(0,0,0,.05);
```

---

## 8. 动效

沿用既有档位与曲线，只加一条指针规则。

```css
--dur-press: 160ms;  --dur-micro: 140ms;  --dur-move: 260ms;
--dur-detent: 320ms; /* 棘轮咬合：走到位就停，不回弹 */
--ease-detent: cubic-bezier(.16,.9,.2,1);
```

- **指针只用 `transform`**，不动 `left/top`。
- **棘轮定格**：每格推进 = 一条证据落库。没有事件，盘面静止。
  匀速无限旋转是 loading spinner，不是仪器。
- `prefers-reduced-motion`：位移归零，只保留约 200ms 的颜色反馈。

---

## 9. 可访问性（硬门槛）

- **正文对比度 ≥ 4.5**，不设例外。
- **颜色不是唯一信道**：来源标记同时用线型区分（模型推断=虚线，用户确认=实线）。
- 校验脚本：`docs/design/themes/03-cream-instrument/check-contrast.py`，
  从 HTML 的 token 定义解析颜色并逐对计算，未达标退出码 1，可挂 CI。

---

## 10. 禁止清单

1. 有色相的阴影 / 任何 glow
2. 用 `--gold-decorative` 承载文字
3. 三色域交叉使用（如用紫色表示投资人）
4. 大面积纯白 `#ffffff`
5. 第四种字重
6. 运行时字体 CDN
7. 盘面数字由随机数或动画驱动
8. 组件直接引用 L1 token 或裸 hex
9. 用红色表示"评审"以外的任何含义

---

## 附：主题归档

历史与备选风格存放在 `docs/design/themes/`，每个目录自带组件、CSS 快照与预览：

| 目录 | 风格 | 状态 |
|---|---|---|
| `01-astrolabe/` | 星盘 / 航海罗经，米白 + 细黑线 + 红点缀 | 已归档，组件仍在仓库但无引用 |
| `02-brass-wheel/` | 深色黄铜转盘，`EvaluationWheel` | 当前线上 |
| `03-cream-instrument/` | 暖奶油仪器，本规范对应 | 提案，见 `index.html` |

完整性校验：`python docs/design/themes/verify-backup.py`
（检查每个组件引用的 class 在同目录 CSS 快照里都有规则，缺失退出码 1）。
