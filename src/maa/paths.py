"""路径解析。

"什么东西放哪儿"的判断全部收在这里，其他模块只调函数、不自己拼路径 ——
这样换布局时只需要改这一个文件。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

MAA_HOME_ENV = "MAA_HOME"
DEFAULT_HOME_DIRNAME = ".maa"


def home_dir() -> Path:
    """用户主目录。

    单独抽一层是为了给测试留个替换点：直接读 `Path.home()` 的话，测试会去扫
    开发机真实的 `~/.claude/skills`，结果随机器而变——本机上那两个目录里正好
    躺着几十个技能。
    """
    return Path.home()


def maa_home() -> Path:
    """MAA 主目录：配置、会话库、日志都在这儿。`MAA_HOME` 可重定位。"""
    raw = os.environ.get(MAA_HOME_ENV, "").strip()
    return Path(raw).expanduser() if raw else home_dir() / DEFAULT_HOME_DIRNAME


def ensure_maa_home() -> Path:
    """确保主目录存在并返回（写配置、开会话库之前调）。"""
    home = maa_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def config_path() -> Path:
    """主配置文件。"""
    return maa_home() / "config.toml"


def sessions_db_path() -> Path:
    """会话持久化用的 sqlite 库（C7 用）。"""
    return maa_home() / "sessions.db"


def user_mcp_path() -> Path:
    """用户级 MCP 配置。"""
    return maa_home() / "mcp.json"


def user_skills_dir() -> Path:
    """用户级技能目录。"""
    return maa_home() / "skills"


def project_root(start: Path | None = None) -> Path:
    """从 `start`（默认 cwd）向上找项目根。

    先认 `.git`，没有再认 `pyproject.toml`；都没有就返回 `start` 本身，
    免得从仓库外调用时炸掉。
    """
    origin = (start or Path.cwd()).resolve()
    for marker in (".git", "pyproject.toml"):
        for candidate in (origin, *origin.parents):
            if (candidate / marker).exists():
                return candidate
    return origin


def project_mcp_path(start: Path | None = None) -> Path:
    """项目级 MCP 配置。"""
    return project_root(start) / ".mcp.json"


def skill_search_paths(project: Path | None = None, extra: Iterable[str] = ()) -> list[Path]:
    """技能目录，低 → 高优先级；同名技能后面的覆盖前面的。

    刻意把 `~/.claude/skills` 和 `~/.agents/skills` 也算进来，好复用机器上已有的技能。
    返回的目录不保证存在 —— 过滤交给 skills.py。
    """
    root = project or project_root()
    home = home_dir()
    dirs = [
        user_skills_dir(),
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        root / ".maa" / "skills",
        root / ".agents" / "skills",
    ]
    dirs.extend(Path(p).expanduser() for p in extra)
    return dirs


def resolve_path(raw: str | Path, project: Path | None = None) -> Path:
    """把配置里写的路径解析成绝对路径；相对路径按项目根算，`~` 展开。

    配置里写相对路径，人心里想的都是"相对这个项目"，不是"相对我此刻在哪个
    目录"—— 后者会让同一份配置在不同 cwd 下指向不同的地方。
    """
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return ((project or project_root()) / candidate).resolve()


def resolve_workspace(raw: str | Path, project: Path | None = None) -> Path:
    """把 `[backend].workspace` 解析成绝对路径；相对路径按项目根算。"""
    return resolve_path(raw, project)


def display(path: Path) -> str:
    """给人看的短路径：能相对 cwd 就相对，能缩成 `~` 就缩。"""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd()))
    except ValueError:
        pass
    try:
        return f"~/{resolved.relative_to(home_dir())}"
    except ValueError:
        return str(resolved)
