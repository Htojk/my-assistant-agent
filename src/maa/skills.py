"""技能库：把散落各处的 SKILL.md 汇成一份 agent 能自己翻的技能清单。

技能走的是 Anthropic 的 agent skills 模式：一个技能一个目录，里面有 SKILL.md，
frontmatter 写 `name` / `description`，正文写具体怎么做。agent 在系统提示里只看
得到名字和描述，真觉得用得上时才自己 `read_file` 读正文——渐进披露，几十个技能
也不会把上下文撑爆。

这就带来一个约束：**技能目录必须能被 `read_file` 摸到**。可技能散在用户主目录和
项目目录里，都在工作目录之外，而工作目录之外的东西默认不该让 agent 够得着。

解法是 CompositeBackend 的路由：每个技能源挂一个虚拟前缀（`/skills/user-claude/`），
路由到一个 `root_dir` 指着真实技能目录的后端。于是 agent 眼里是一个统一的
`/skills/` 命名空间，真实目录一个字节都不用搬，工作目录外的其他东西也照样够不着。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from deepagents.backends import FilesystemBackend

from . import paths
from .config import Config

SKILL_FILENAME = "SKILL.md"
"""技能定义的固定文件名（Agent Skills 规范）。"""

BUILTIN_DIRNAME = "skills"
"""仓库根的内置技能目录名。"""

PACKAGED_DIRNAME = "_skills"
"""装成 wheel 后内置技能在包内叫这个名字。

不能也叫 `skills` —— 那会跟本模块 `maa/skills.py` 撞名，Python 会优先当成包，
`import maa.skills` 直接变成导包而不是导模块。
"""

ROUTE_ROOT = "/skills/"
"""所有技能源共用的虚拟前缀。"""

MAX_SKILL_FILE_SIZE = 256 * 1024
"""超过这个大小的 SKILL.md 不读——到了这个体量就不是技能说明，是别的东西。"""

_OWNER_LABELS: dict[str, tuple[str, str]] = {
    ".maa": ("maa", ""),
    ".claude": ("claude", "Claude"),
    ".agents": ("agents", "Agents"),
}
"""技能目录的上一级目录名 → (路由里的短名, 显示名)。

`.maa` 的显示名留空，因为它就是这个工具自己的目录，写"用户 maa"反而啰嗦。
"""

_SCOPE_LABELS = {"user": "用户", "proj": "项目"}


@dataclass(frozen=True)
class SkillSource:
    """一个技能源：磁盘上的真实目录，加上它在后端里的虚拟前缀。"""

    route: str
    """虚拟前缀，形如 `/skills/user-claude/`，以 `/` 结尾。"""

    label: str
    """显示名。中间件会渲染成 `**{label} Skills**`，所以别带 "Skills" 后缀。"""

    path: Path
    """真实目录，已 resolve。"""

    def describe(self) -> str:
        return f"{self.label or '内置'}  {paths.display(self.path)}"


@dataclass(frozen=True)
class Skill:
    """一个技能，也就是一个含 SKILL.md 的目录。"""

    name: str
    description: str
    source: SkillSource

    @property
    def read_path(self) -> str:
        """该让 agent 用哪个路径去 `read_file`。

        这是虚拟路径，不是磁盘路径——写进系统提示的就是它。
        """
        return f"{self.source.route}{self.name}/{SKILL_FILENAME}"


@dataclass
class ScanResult:
    """`scan()` 的结果：生效的技能，加上被跳过的技能和原因。"""

    skills: list[Skill] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """中间的 `name` 跟目录名对不上、frontmatter 解析不了之类。

    单独列出来是因为中间件遇到这些是**静默跳过**的——技能写着却永远不生效，
    没有任何报错。这份清单就是为了让这种事至少有个地方能看见。
    """


def builtin_dir() -> Path | None:
    """内置技能目录。

    装成 wheel 时在包内（`maa/skills/`），源码树里在仓库根（`skills/`）——
    两种布局都认。都没有就返回 None：没有内置技能不算故障。
    """
    packaged = Path(__file__).resolve().parent / PACKAGED_DIRNAME
    if packaged.is_dir():
        return packaged
    from_source_tree = Path(__file__).resolve().parents[2] / BUILTIN_DIRNAME
    if from_source_tree.is_dir():
        return from_source_tree
    return None


def discover(cfg: Config, project: Path | None = None) -> list[SkillSource]:
    """按优先级从低到高列出**真实存在**的技能目录。

    顺序沿用 `paths.skill_search_paths()`（PLAN §3.3），内置技能垫在最底下——
    "base → user → project → 附加"这个分层是中间件设计好的，同名技能后者赢。

    不存在的目录直接跳过。挂上去只会让中间件每次启动都报一条加载失败，
    而 `~/.agents/skills` 这类目录在多数机器上本来就没有。
    """
    root = (project or paths.project_root()).resolve()
    candidates: list[tuple[Path, str, str]] = []

    if (builtin := builtin_dir()) is not None:
        candidates.append((builtin, "builtin", "内置"))

    extra_index = 0
    for path in paths.skill_search_paths(root, cfg.skills.extra_dirs):
        owner = path.parent.name
        if path.name == "skills" and owner in _OWNER_LABELS:
            slug, owner_label = _OWNER_LABELS[owner]
            scope = "proj" if path.is_relative_to(root) else "user"
            label = f"{_SCOPE_LABELS[scope]} {owner_label}".strip()
            candidates.append((path, f"{scope}-{slug}", label))
        else:
            # extra_dirs 里的目录，位置随意，只能按顺序编号。
            extra_index += 1
            candidates.append((path, f"extra-{extra_index}", f"附加 {extra_index}"))

    sources: list[SkillSource] = []
    seen: set[Path] = set()
    for path, slug, label in candidates:
        resolved = path.expanduser()
        if not resolved.is_dir():
            continue
        resolved = resolved.resolve()
        if resolved in seen:
            # 同一个目录被两条规则指到（比如 extra_dirs 里填了 ~/.claude/skills），
            # 只留先出现的那条，免得同名技能被自己盖掉。
            continue
        seen.add(resolved)
        sources.append(SkillSource(route=f"{ROUTE_ROOT}{slug}/", label=label, path=resolved))
    return sources


def routes(sources: Iterable[SkillSource]) -> dict[str, Any]:
    """技能源 → CompositeBackend 的路由表。

    用 `FilesystemBackend` 而不是 `LocalShellBackend`：技能是只读资料，agent
    没有理由在技能目录里跑 shell。至于写——路由进来的路径本来就只该被读。
    """
    return {
        source.route: FilesystemBackend(root_dir=source.path, virtual_mode=True)
        for source in sources
    }


def sources_param(sources: Iterable[SkillSource]) -> list[tuple[str, str]]:
    """给 `create_deep_agent(skills=...)` 的源列表。

    传 `(路径, 名字)` 二元组而不是裸路径：中间件对裸路径会自动取名，而
    `/skills/user-maa/` 和 `/skills/proj-maa/` 的末段都是 `skills`，自动取名
    会撞在一起分不出谁是谁。
    """
    return [(source.route, source.label) for source in sources]


def scan(sources: Iterable[SkillSource]) -> ScanResult:
    """列出真正生效的技能：同名的高优先级覆盖低优先级。

    这里刻意照着中间件的判定来（`name` 必须等于目录名、`description` 不能空），
    因为 `maa skills list` 要回答的是"agent 实际看得到什么"，不是"磁盘上有什么"。
    对不上的写进 `problems`。
    """
    result = ScanResult()
    by_name: dict[str, Skill] = {}
    for source in sources:
        for skill in _scan_source(source, result):
            if skill.name in by_name:
                result.problems.append(
                    f"{skill.name}：{skill.source.label} 的版本覆盖了 "
                    f"{by_name[skill.name].source.label} 的版本"
                )
            by_name[skill.name] = skill  # 后出现的优先级更高
    result.skills = sorted(by_name.values(), key=lambda item: item.name)
    return result


def _scan_source(source: SkillSource, result: ScanResult) -> Iterator[Skill]:
    try:
        entries = sorted(entry for entry in source.path.iterdir() if entry.is_dir())
    except OSError as exc:
        result.problems.append(f"{source.describe()}：读不了（{exc.strerror}）")
        return

    for entry in entries:
        skill_md = entry / SKILL_FILENAME
        if not skill_md.is_file():
            continue
        try:
            if skill_md.stat().st_size > MAX_SKILL_FILE_SIZE:
                result.problems.append(f"{entry.name}：SKILL.md 太大，跳过")
                continue
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.problems.append(f"{entry.name}：SKILL.md 读不了（{exc}）")
            continue

        data = parse_frontmatter(text)
        name = str(data.get("name") or "")
        description = str(data.get("description") or "")

        if name != entry.name:
            result.problems.append(
                f"{entry.name}：frontmatter 里的 name 是 {name!r}，跟目录名不一致——中间件会跳过它"
            )
            continue
        if not description:
            result.problems.append(f"{entry.name}：frontmatter 缺 description，中间件会跳过它")
            continue

        yield Skill(name=name, description=description, source=source)


def parse_frontmatter(text: str) -> dict[str, Any]:
    """取 SKILL.md 开头那段 YAML frontmatter。

    解析不了就返回空字典——这里只负责显示，判定该不该拒绝是中间件的事，
    不越俎代庖。
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
