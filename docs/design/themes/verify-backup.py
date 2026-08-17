"""校验主题备份的完整性：组件引用的每个 class 是否都能在同目录 CSS 里找到规则。

备份最容易出的错不是"文件没拷到"，而是"拷了 tsx 但样式留在别处"——
渲染出来一片白，而当时没人发现。此脚本把这种失败变成退出码 1。

用法: python verify-backup.py     (缺样式即退出码 1)
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# 主题 → (组件文件, 该主题对应的 CSS 快照)
THEMES = {
    "01-astrolabe": ("Compass.tsx", "globals.head.css"),
    "02-brass-wheel": ("EvaluationWheel.tsx", "globals.worktree.css"),
}

# 非样式 class / 工具类白名单（出现在 className 里但不需要 CSS 规则）
IGNORE = {"", "mono"}


def classes_in(tsx: str) -> set[str]:
    """抽出所有 className 里的 class 名。

    模板串里的 ${cond ? "a" : "b"} 三元里也藏着真 class，
    先把插值内的字符串字面量抠出来，再把插值本身抹掉——
    否则会把 'point-label${isCardinal' 当成一个 class 误报。
    """
    found: set[str] = set()
    # 模板串内含 " 引号，字符类不能同时排除两种引号，故分两次匹配
    chunks = re.findall(r'className="([^"]*)"', tsx)
    chunks += re.findall(r"className=\{`([^`]*)`\}", tsx)
    for chunk in chunks:
        found.update(re.findall(r'"([^"]*)"', chunk))  # 插值内的字面量
        found.update(re.sub(r"\$\{[^}]*\}", " ", chunk).split())  # 静态部分
    out: set[str] = set()
    for c in found:
        out.update(c.split())
    return {c for c in out if c and c not in IGNORE}


def rules_in(css: str) -> set[str]:
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", css))


fail = False
for theme, (tsx_name, css_name) in THEMES.items():
    tsx_path, css_path = ROOT / theme / tsx_name, ROOT / theme / css_name
    print(f"\n=== {theme} ===")

    missing_files = [p.name for p in (tsx_path, css_path) if not p.exists()]
    if missing_files:
        print(f"  文件缺失: {missing_files}")
        fail = True
        continue

    used = classes_in(tsx_path.read_text(encoding="utf-8"))
    have = rules_in(css_path.read_text(encoding="utf-8"))
    orphan = sorted(used - have)

    print(f"  {tsx_name}: 引用 {len(used)} 个 class")
    print(f"  {css_name}: 定义 {len(have)} 个 class 规则")
    if orphan:
        print(f"  *** 无对应 CSS 规则 ({len(orphan)}): {orphan}")
        fail = True
    else:
        print("  样式完整：每个 class 都有规则")

sys.exit(1 if fail else 0)
