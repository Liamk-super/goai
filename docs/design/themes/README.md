# 主题归档 · LaunchScope

存放历史与备选视觉风格。每个目录**自包含**：组件源码 + 同期 CSS 快照 + 预览。
目的是任何一版风格都能独立复活，不依赖当时的工作区状态。

## 目录

| 目录 | 风格 | 来源 | 状态 |
|---|---|---|---|
| `01-astrolabe/` | 星盘 / 航海罗经。米白底、细黑线、红色点缀、32 方位刻度、边缘铭文 | `Compass.tsx` @ HEAD `f39f6c1` | **孤儿组件**——仍在仓库但无任何 import |
| `02-brass-wheel/` | 深色黄铜转盘。拉丝金属、四扇区、1+5 Agent 指针 | 工作区 `EvaluationWheel.tsx` | **当前线上** |
| `03-cream-instrument/` | 暖奶油仪器。三色域 + 评审红独立通道 | 提案 | 见 `docs/design.md` |

## 完整性校验

```bash
python docs/design/themes/verify-backup.py
```

校验每个组件引用的 class 在同目录 CSS 快照里都有对应规则，缺失则退出码 1。

> 备份最容易出的错不是"文件没拷到"，而是"拷了 tsx 但样式留在别处"——
> 日后想复活时渲染出一片白，而当时没人发现。这个脚本把那种失败提前变成可见的错误。

## 重新生成星盘预览

`01-astrolabe/preview.html` 由**真实组件**经 `react-dom/server` 渲染，非手工临摹：

```bash
cd apps/web
node ../../node_modules/.pnpm/tsx@4.23.11/node_modules/tsx/dist/cli.mjs render-theme-preview.mts
```

脚本必须放在 `apps/web/` 下（ESM 从脚本自身位置解析裸包名，放 `docs/` 会解析不到 react）。
它用 workspace 自带的 `typescript` 以 automatic JSX runtime 转译 `Compass.tsx` 后动态载入——
Node 的 `--experimental-strip-types` 不处理 JSX，tsx 的 `--tsconfig` 也不作用于被 import 的文件。

## 新增一版风格

1. 建 `NN-slug/` 目录
2. 放入组件源码 + 它依赖的 CSS 快照（**整份**，不要只挑相关规则——
   两版风格常共用 `.plate-ring` 这类 class，挑拣必然漏）
3. 放 `reference.png`（灵感来源）与 `preview.png`（实际渲染）
4. 写 `README.md`：这版是什么、为什么做、为什么停用
5. 在 `verify-backup.py` 的 `THEMES` 里登记，跑一次确认退出码 0
