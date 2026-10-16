"""能力 profile：把配置 + 后端 + 模型装配成一个能跑的 agent。

profile 之间的差别只有四件事 —— 系统提示、文件工具白名单、要不要任务清单、
派哪几个子代理。**新增一个 profile 应该是往 `REGISTRY` 里加一条**，而不是改
`build_agent`；这条路只有一条，走的人多了才不会各有各的暗门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from . import backends, paths, skills, subagents
from .config import Config, ConfigError

READ_ONLY_TOOLS: tuple[str, ...] = ("read_file", "ls", "glob", "grep")
"""不含 `execute` 也不含写操作的底子。writer / planner 在这上面加。"""

WRITER_TOOLS: tuple[str, ...] = (*READ_ONLY_TOOLS, "write_file", "edit_file")
"""写作要能落盘，但不需要 `delete`，更不需要 `execute`。"""

PLANNER_TOOLS: tuple[str, ...] = (*READ_ONLY_TOOLS, "write_file")
"""规划要能读情况、能把方案写成文件，但不该有副作用。

只给 `write_file` 不给 `edit_file`：规划产出的是新文件，改别人的文件不在这个
profile 的职责里。
"""


@dataclass(frozen=True)
class Profile:
    """一个能力入口。字段刻意留宽，C4/C5/C6 往里加东西时不用改结构。"""

    name: str
    description: str
    system_prompt: str
    uses_todos: bool = True
    """是否挂 `TodoListMiddleware`。

    注意：不传就静默没有 `write_todos` 工具，没有报错 —— 见 PLAN.md §2.4。
    """

    tools: tuple[str, ...] | None = None
    """文件工具白名单；None = 后端给的全量。**`read_file` 必须在列**，否则中间件抛
    `ValueError`（见 `subagents.READ_ONLY_TOOLS` 的说明）。"""

    uses_skills: bool = True
    """是否挂技能库。

    技能目录是环境级的（用户主目录 + 项目目录 + 配置里的 extra_dirs），
    不归 profile 管，见 skills.discover()。这里只决定这个 profile 要不要。
    """

    preferred_skills: tuple[str, ...] = ()
    """这个 profile 的看家技能（对应 SKILL.md 里的 `name`）。

    **不是白名单** —— 环境里的技能全都在，中间件本来也只把名字和描述放进提示，
    没读的技能几乎不占上下文。这里多做的是把用户的注意力（和模型的）指向那一个，
    顺带把它的路径写死在提示里，省得模型每次都要从清单里自己认。找不到就跳过。
    """

    subagent_names: tuple[str, ...] = ()
    """要挂的子代理（名字见 subagents.REGISTRY）。"""

    permissions: tuple[FilesystemPermission, ...] = ()

    def __post_init__(self) -> None:
        if self.tools is not None and "read_file" not in self.tools:
            raise ValueError(
                f"profile {self.name!r} 的工具白名单少了 read_file —— "
                "FilesystemMiddleware 强制要求它在列，少一个直接 ValueError"
            )

    def middleware(self, backend: Any, permissions: list[FilesystemPermission]) -> list[Any]:
        """这个 profile 要往中间件栈里塞的东西。

        `backend` / `permissions` 由调用方给：白名单得配在**同一套**后端和权限上，
        在这儿自己重造一份的话，readonly 模式下的 profile 就可能绕开权限规则。

        白名单的实现方式是"同名替换"：deepagents 自己会在栈里放一层全量的
        `FilesystemMiddleware`，这里造一个同名的塞进去，它就原地顶掉了原来那层
        （机制见 deepagents/graph.py 的 `_apply_custom_middleware`）。
        没给白名单就不动它 —— 少一层替换少一处要跟 SDK 对齐的地方。
        """
        stack: list[Any] = []
        if self.uses_todos:
            stack.append(TodoListMiddleware())
        if self.tools is not None:
            stack.append(
                FilesystemMiddleware(
                    backend=backend,
                    tools=list(self.tools),
                    # 这里只能用私有名：公开的 `permissions=` 是 `create_deep_agent`
                    # 的参数，进了它自己那层中间件，而我们正在把那层换掉。
                    _permissions=list(permissions) or None,
                )
            )
        return stack


GENERAL_PROMPT = """\
你是跑在终端里的通用助手，写作、编码、任务规划都能接。

当前工作目录：{workspace}

工作要求：
- 需要产出文件时直接落到工作目录（`write_file` / `edit_file`），别只在回复里贴内容。
- 多步任务先用 `write_todos` 列计划，做完一步就更新一步；一两步的小事不用列。
- 改文件前先读一遍，别凭猜测覆盖。
- 回复用中文，除非用户换了语言。
"""

WRITER_PROMPT = """\
你是写作助手，负责把想法变成能给人看的完整文章。

当前工作目录：{workspace}

工作要求：
- 产出直接落到工作目录（`write_file` / `edit_file`），别只在回复里贴一整篇。
- 动笔前先读技能库里那份写作技能（路径在下面的"看家技能"里）。
- 八百字以上、或者要分节的活儿，先派 `outliner` 把结构定下来再写。它只给骨架，
  正文还是你写。
- 用户给了字数就照做，写完自己核一遍篇幅。
- 改现成的稿子先读一遍再动，别凭记忆重写 —— 重写会把原稿里对的东西一起弄丢。
- 回复用中文，除非用户换了语言。
"""

CODER_PROMPT = """\
你是编码助手，负责实现功能、修 bug、重构和补测试。

当前工作目录：{workspace}

工作要求：
- 改代码前先读一遍现有实现，跟着周围的写法写。库里已有的工具函数和约定，别再造一套。
- 改完要验证。能跑就跑一遍（`execute`），跑不了（缺依赖、缺服务）就直说没验证过，
  不要用"应该没问题"糊过去。
- 多步任务先 `write_todos` 列计划，做完一步更新一步。
- 交付前可以派 `code-reviewer` 挑一遍毛病 —— 它只读，不会碰你的改动，采纳与否你定。
- 回复用中文，代码和标识符保持原样。
"""

PLANNER_PROMPT = """\
你是任务规划助手。你的产出是**方案**，不是执行动作。

当前工作目录：{workspace}

工作要求：
- **第一件事永远是 `write_todos`。** 接到任务先列计划再往下走 —— 没有计划的规划
  等于没做。计划要能一眼看出先后顺序和每步的产出。
- 列计划前先把情况摸清楚（`ls` / `grep` / `read_file`，或者派 `researcher` 去查）。
  凭空排出来的计划没有价值。
- 每一步写到"做完能验什么"为止。"实现功能"太粗，"打开那个文件"太细，都不合格。
- 拿不准的地方标出来，写清"需要谁确认什么"，不要替用户拍板。
- 你**不能执行命令** —— 规划里不该有副作用。要跑什么、要改什么，写进计划交给
  `coder` 那个 profile 去做。
- 计划写成文件落到工作目录（`plan.md` 或按主题命名），回复里给路径加一段概要。
- 回复用中文。
"""


REGISTRY: dict[str, Profile] = {
    "general": Profile(
        name="general",
        description="默认通用：全工具 + 任务清单",
        system_prompt=GENERAL_PROMPT,
    ),
    "writer": Profile(
        name="writer",
        description="写文章：不给 shell，能落盘，带大纲子代理",
        system_prompt=WRITER_PROMPT,
        tools=WRITER_TOOLS,
        preferred_skills=("article-writing",),
        subagent_names=("outliner",),
    ),
    "coder": Profile(
        name="coder",
        description="写代码：要 shell，带只读审查子代理",
        system_prompt=CODER_PROMPT,
        tools=None,
        preferred_skills=("coding",),
        subagent_names=("code-reviewer",),
    ),
    "planner": Profile(
        name="planner",
        description="做规划：强约束先 write_todos，不给 shell，带调研子代理",
        system_prompt=PLANNER_PROMPT,
        tools=PLANNER_TOOLS,
        preferred_skills=("task-planning",),
        subagent_names=("researcher",),
    ),
}


def get_profile(name: str) -> Profile:
    """按名字取 profile，名字错了给出可选清单而不是 KeyError。"""
    try:
        return REGISTRY[name]
    except KeyError:
        known = "、".join(sorted(REGISTRY))
        raise ConfigError(f"没有这个 profile：{name}。可选：{known}") from None


def names() -> list[str]:
    """所有 profile 名，排序过。给人看用。"""
    return sorted(REGISTRY)


@dataclass
class BuiltAgent:
    """装配结果：图本身，加上 banner / doctor 要展示的来龙去脉。"""

    graph: Any
    profile: Profile
    workspace: Path
    backend_cfg: Any
    model_spec: str
    model: BaseChatModel = field(repr=False)
    skill_sources: list[skills.SkillSource] = field(default_factory=list)
    extra_tools: tuple[Any, ...] = ()
    """调用方塞进来的现成工具（今天只有 MCP 工具）。列出来是为了能显示个数。"""

    subagents: tuple[str, ...] = ()
    """实际挂上的子代理名字。列出来是为了能被断言，也为了 banner 能显示。"""


def build_model(cfg: Config, override: str | None = None) -> BaseChatModel:
    """把 `provider:model` 字符串加上 provider 接入参数，变成模型实例。

    直接给 `create_deep_agent` 传字符串的话，openai 系会默认走 Responses API，
    自建端点多半没有这个路由 —— 所以这里自己实例化，好把 `use_responses_api` 关掉。
    """
    spec = override or cfg.models.default
    provider = cfg.models.provider_for(spec)

    kwargs: dict[str, Any] = {}
    if provider is not None:
        if base_url := provider.resolve_base_url():
            kwargs["base_url"] = base_url
        if api_key := provider.resolve_api_key():
            kwargs["api_key"] = api_key
        if provider.use_responses_api is not None and spec.startswith("openai"):
            kwargs["use_responses_api"] = provider.use_responses_api

    try:
        return init_chat_model(spec, **kwargs)
    except Exception as exc:  # noqa: BLE001 —— 转成用户能看懂的错
        raise ConfigError(f"模型起不来：{spec}\n{type(exc).__name__}: {exc}") from exc


def resolve_model(
    cfg: Config, model: str | BaseChatModel | None = None
) -> tuple[BaseChatModel, str]:
    """返回 `(模型实例, 用来显示的 spec)`。

    传实例就直接用 —— 测试要喂假模型，子代理也会传现成的实例。
    """
    if isinstance(model, BaseChatModel):
        return model, getattr(model, "model_name", None) or type(model).__name__
    spec = model or cfg.models.default
    return build_model(cfg, spec), spec


def build_agent(
    cfg: Config,
    profile_name: str = "general",
    *,
    workspace: Path | None = None,
    model: str | BaseChatModel | None = None,
    extra_tools: tuple[BaseTool | Any, ...] = (),
    checkpointer: Any | None = None,
    interrupt_on: dict[str, Any] | None = None,
) -> BuiltAgent:
    """装配 agent。每会话建一次 —— MCP 工具是构图期参数，只能这么来。

    `extra_tools` 是给调用方塞现成工具的（C5 的 MCP 工具走这条）。这里不自己去
    连 MCP：那是一次网络 I/O，塞进装配会让 doctor 和每个测试都跟着变慢变飘。
    连不连、连谁，由调用方决定。

    `interrupt_on` 是审批表（C8），形状见 `approval.gated()`。同样只接现成的：
    "问不问、怎么问"是 CLI 的事，这里只管把表交给图。
    """
    profile = get_profile(profile_name)
    resolved_workspace = workspace or paths.resolve_workspace(cfg.backend.workspace)

    sources = skills.discover(cfg) if profile.uses_skills else []
    backend = backends.build_backend(cfg.backend, resolved_workspace, routes=skills.routes(sources))
    permissions = [*backends.build_permissions(cfg.backend), *profile.permissions]
    chat_model, model_spec = resolve_model(cfg, model)

    prompt = profile.system_prompt.format(workspace=paths.display(resolved_workspace))
    if hint := _skill_hint(profile, skills.scan(sources)):
        prompt = f"{prompt}\n\n{hint}"

    graph = create_deep_agent(
        model=chat_model,
        tools=list(extra_tools) or None,
        system_prompt=prompt,
        middleware=profile.middleware(backend, permissions),
        subagents=subagents.build_all(
            profile.subagent_names, backend=backend, permissions=permissions
        )
        or None,
        skills=skills.sources_param(sources) or None,
        backend=backend,
        permissions=permissions or None,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on or None,
        name=f"maa-{profile.name}",
    )

    return BuiltAgent(
        graph=graph,
        profile=profile,
        workspace=resolved_workspace,
        backend_cfg=cfg.backend,
        model_spec=model_spec,
        model=chat_model,
        skill_sources=sources,
        extra_tools=tuple(extra_tools),
        subagents=profile.subagent_names,
    )


def _skill_hint(profile: Profile, scanned: skills.ScanResult) -> str:
    """把 profile 点名的技能翻成"去读哪个文件"。

    中间件已经把技能清单放进系统提示了（只有名字和描述），这一步多做的是**把路径
    写死**：模型不用回头从清单里找。
    """
    by_name = {skill.name: skill for skill in scanned.skills}
    lines = [
        f"- {name} → `{by_name[name].read_path}`"
        for name in profile.preferred_skills
        if name in by_name
    ]
    if not lines:
        return ""
    return (
        "这个 profile 的看家技能，动手之前先 `read_file` 读一遍"
        "（带上 `limit=1000`，默认 100 行读不完）：\n" + "\n".join(lines)
    )


def tool_names(built: BuiltAgent) -> list[str]:
    """从建好的图里挖出工具名（`maa doctor` 和测试用）。

    走的是 langgraph 内部结构，所以失败就返回空列表 —— 自检不该因为它崩掉。
    """
    try:
        node = built.graph.nodes["tools"]
        bound = getattr(node, "bound", None) or node
        tools = getattr(bound, "_tools_by_name", None) or {}
        return sorted(tools)
    except Exception:  # noqa: BLE001
        return []
