"""能力 profile：把配置 + 后端 + 模型装配成一个能跑的 agent。

C3 只有 `general`；writer / coder / planner 在 C6 补。新增一个 profile 应该是
往 `REGISTRY` 里加一条，而不是改 `build_agent` —— 保持这条路只有一条。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from . import backends, paths, skills
from .config import Config, ConfigError


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
    """文件工具白名单；None = 后端给的全量。"""

    uses_skills: bool = True
    """是否挂技能库。

    技能目录是环境级的（用户主目录 + 项目目录 + 配置里的 extra_dirs），
    不归 profile 管，见 skills.discover()。这里只决定这个 profile 要不要。
    """

    subagents: tuple[dict[str, Any], ...] = ()
    """子代理规格，C6 接。"""

    permissions: tuple[FilesystemPermission, ...] = ()

    def middleware(self) -> list[Any]:
        return [TodoListMiddleware()] if self.uses_todos else []


GENERAL_PROMPT = """\
你是跑在终端里的通用助手，写作、编码、任务规划都能接。

当前工作目录：{workspace}

工作要求：
- 需要产出文件时直接落到工作目录（`write_file` / `edit_file`），别只在回复里贴内容。
- 多步任务先用 `write_todos` 列计划，做完一步就更新一步；一两步的小事不用列。
- 改文件前先读一遍，别凭猜测覆盖。
- 回复用中文，除非用户换了语言。
"""


REGISTRY: dict[str, Profile] = {
    "general": Profile(
        name="general",
        description="默认通用：全工具 + 任务清单",
        system_prompt=GENERAL_PROMPT,
    ),
}


def get_profile(name: str) -> Profile:
    """按名字取 profile，名字错了给出可选清单而不是 KeyError。"""
    try:
        return REGISTRY[name]
    except KeyError:
        known = "、".join(sorted(REGISTRY))
        raise ConfigError(f"没有这个 profile：{name}。可选：{known}") from None


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

    传实例就直接用 —— 测试要喂假模型，C6 的子代理也会传现成的实例。
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
) -> BuiltAgent:
    """装配 agent。每会话建一次 —— MCP 工具是构图期参数，只能这么来。

    `extra_tools` 是给调用方塞现成工具的（C5 的 MCP 工具走这条）。这里不自己去
    连 MCP：那是一次网络 I/O，塞进装配会让 doctor 和每个测试都跟着变慢变飘。
    连不连、连谁，由调用方决定。
    """
    profile = get_profile(profile_name)
    resolved_workspace = workspace or paths.resolve_workspace(cfg.backend.workspace)

    sources = skills.discover(cfg) if profile.uses_skills else []
    backend = backends.build_backend(cfg.backend, resolved_workspace, routes=skills.routes(sources))
    permissions = [*backends.build_permissions(cfg.backend), *profile.permissions]
    chat_model, model_spec = resolve_model(cfg, model)

    graph = create_deep_agent(
        model=chat_model,
        tools=list(extra_tools) or None,
        system_prompt=profile.system_prompt.format(workspace=paths.display(resolved_workspace)),
        middleware=profile.middleware(),
        subagents=list(profile.subagents) or None,
        skills=skills.sources_param(sources) or None,
        backend=backend,
        permissions=permissions or None,
        checkpointer=checkpointer,
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
