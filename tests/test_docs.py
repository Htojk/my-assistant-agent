"""文档里的链接不该是死链。

README 是门面，门面上的链接 404 比压根没有链接更糟 —— 人点一次就不点了。

C10 的验收里有一条"README.md 里的悬空链接不再 404"，当时是手写脚本跑了一遍就完事，
脚本还没进仓库，等于下次改文档还得再写一遍。这里把它收成一条测试：链接和**锚点**
都在这儿验，改坏了当场红。

（这也是为什么它测的是文件系统而不是"能不能访问"—— 相对链接指的文件在不在，
是个不需要网络就能回答的问题。）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCS = sorted(
    [
        REPO_ROOT / "README.md",
        REPO_ROOT / "CHANGELOG.md",
        *sorted((REPO_ROOT / "docs").glob("*.md")),
    ]
)
"""要验的文档：仓库门面加 `docs/` 下所有 md。新增文档自动进这张表。"""

LINK = re.compile(r"\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def slug(text: str) -> str:
    """近似 GitHub 的锚点算法：小写、去标点、空格换 `-`。

    中文字符**要留着**（GitHub 也不转），`.` 和 `，` 这类标点直接删掉 ——
    `## 5.3 a，b` 的锚点是 `53-ab`，不是 `5.3-ab`。
    """
    kept = []
    for char in text.strip().lower():
        if char.isalnum() or char in "-_" or "一" <= char <= "鿿":
            kept.append(char)
        elif char == " ":
            kept.append("-")
    return "".join(kept)


def anchors_in(text: str) -> set[str]:
    return {slug(match) for match in HEADING.findall(text)}


def anchors_of(path: Path) -> set[str]:
    return anchors_in(path.read_text(encoding="utf-8"))


def check(path: Path) -> list[str]:
    """返回这个文件里所有坏掉的链接（空表 = 干净）。一次报全，不报一个停一个。"""
    problems: list[str] = []
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, fragment = target.partition("#")
        dest = (path.parent / file_part).resolve() if file_part else path
        if not dest.exists():
            problems.append(f"目标不存在：{target}")
        elif fragment and fragment not in anchors_of(dest) and dest.suffix == ".md":
            problems.append(f"锚点找不到：{target}")
    return problems


@pytest.mark.parametrize("path", DOCS, ids=[p.relative_to(REPO_ROOT).as_posix() for p in DOCS])
def test_文档里的相对链接都还在(path: Path) -> None:
    problems = check(path)

    assert not problems, f"{path.relative_to(REPO_ROOT)}：\n  " + "\n  ".join(problems)


def test_该验的文档一个都没漏() -> None:
    """`docs/` 下新写的 md 得自动进上面那张表 —— 手工维护的清单迟早会漏。

    这条同时保证：glob 要是哪天返回空表（比如目录改名），上面那组测试不会变成
    "0 条通过"这种看着像全绿的空跑。
    """
    names = {path.name for path in DOCS}

    assert {"README.md", "CHANGELOG.md", "PLAN.md", "ARCHITECTURE.md", "INDEX-COVERAGE.md"} <= names


def test_锚点算法认得中文标题() -> None:
    """这条测试的判据本身也得钉住 —— 它要是把中文全吃了，锚点检查就永远通过。

    走 `anchors_in` 而不是直接喂 `slug`：生产路径是先按 `#` 切出标题、再算 slug，
    少这一步就会拿带 `## ` 的整行去算，多出一个前导 `-`。
    """
    assert anchors_in("## 5.3 前缀解决了，但 annotations 仍然没接进审批") == {
        "53-前缀解决了但-annotations-仍然没接进审批"
    }
    assert anchors_in("### 6. 已知限制（诚实清单）") == {"6-已知限制诚实清单"}
