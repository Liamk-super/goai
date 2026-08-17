"""WCAG AA 对比度校验 —— 颜色一律从 index.html 的 token 解析，不重复声明 hex。

用法: python check-contrast.py   (未达标时退出码 1)
"""

import re
import sys
from pathlib import Path

AA = 4.5
HTML = Path(__file__).with_name("index.html")
src = HTML.read_text(encoding="utf-8")

# 字面 hex 与 var() 别名都收，别名在 resolve() 里逐级展开
TOKENS = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6}|var\(--[\w-]+\));", src))


def luminance(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chan = []
    for i in (0, 2, 4):
        v = int(h[i : i + 2], 16) / 255
        chan.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]


def ratio(fg, bg):
    a, b = luminance(resolve(fg)), luminance(resolve(bg))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def resolve(c, depth=0):
    """token 名或字面 hex；var() 别名逐级展开。缺失即报错，不静默通过。"""
    if not c.startswith("--"):
        return c
    if depth > 8:
        sys.exit(f"token 别名成环: {c}")
    if c not in TOKENS:
        sys.exit(f"token 未定义: {c}")
    val = TOKENS[c]
    alias = re.fullmatch(r"var\((--[\w-]+)\)", val)
    return resolve(alias.group(1), depth + 1) if alias else val


# 胶囊底色写在规则里而非 :root，只能字面给；其余全部走 token
PILL_RUNNING, PILL_IDLE, PILL_AWAIT = "#efe9e4", "#f2ede9", "--sw-gold-100"
CARD, BG, TRACK = "--surface-card", "--surface-base", "--sw-cream-300"

CHECKS = [
    ("胶囊 待输入 gold-600", "--sw-gold-600", PILL_AWAIT),
    ("胶囊 空闲 ink-400", "--sw-ink-400", PILL_IDLE),
    ("胶囊 运行中 ink-800", "--sw-ink-800", PILL_RUNNING),
    ("胶囊 已校准 olive-700", "--sw-olive-700", "--sw-olive-100"),
    ("胶囊 需注意 red-600", "--sw-red-600", "--review-field"),
    ("ev-src ink-400 / 卡片", "--sw-ink-400", CARD),
    ("card-meta ink-400 / 背景", "--sw-ink-400", BG),
    ("ink-400 / 轨道", "--sw-ink-400", TRACK),
    ("正文 ink-900 / 卡片", "--sw-ink-900", CARD),
    ("次要 ink-600 / 卡片", "--sw-ink-600", CARD),
    ("评审标题 red-600 / 卡片", "--sw-red-600", CARD),
    ("主按钮 cream / ink-800", CARD, "--sw-ink-800"),
    ("扇区标签 黑 / 奶油场", "--sw-ink-900", "--zone-dev-field"),
    ("扇区标签 黑 / 橄榄场", "--sw-ink-900", "--zone-investor-field"),
    ("扇区标签 黑 / 紫场", "--sw-ink-900", "--zone-user-field"),
    ("扇区读数 ink-600 / 奶油场", "--sw-ink-600", "--zone-dev-field"),
    ("扇区读数 ink-600 / 橄榄场", "--sw-ink-600", "--zone-investor-field"),
    ("扇区读数 ink-600 / 紫场", "--sw-ink-600", "--zone-user-field"),
    ("色板角色标注 gold-600 / raised", "--sw-gold-600", "--surface-raised"),
    ("来源标记 model / 卡片", "--source-model", CARD),
    ("来源标记 user / 卡片", "--source-user", CARD),
    ("来源标记 missing / 卡片", "--source-missing", CARD),
]

print(f"{'检查项':<32}{'比值':>7}  结果   (AA 门槛 {AA})")
print("-" * 58)
scored = [(name, ratio(fg, bg)) for name, fg, bg in CHECKS]
for name, r in scored:
    print(f"{name:<32}{r:>6.2f}  {'PASS' if r >= AA else 'FAIL'}")
fails = [(n, r) for n, r in scored if r < AA]

# 改深前的旧值不得残留在任何位置（含 SVG 呈现属性与内联 style）
STALE = {"#8a6a2f", "#7a716a"}
found = sorted(v for v in STALE if v in src)

print(f"\n未达标 {len(fails)} / {len(CHECKS)}")
for n, r in fails:
    print(f"  FAIL {n} = {r:.2f}")
print(f"残留旧 token 值: {found or '无'}")

sys.exit(1 if fails or found else 0)
