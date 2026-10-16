"""CLI 入口：`maa`。

这里只做三件事 —— 解析参数、装配 agent、把过程和结果渲出来。
真正的能力都在 config / backends / profiles 里。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from . import __version__, approval, backends, checks, mcp, paths, profiles, session, skills, ui
from .config import (
    ApprovalConfig,
    Config,
    ConfigError,
    load_config,
    render_config,
    write_config_template,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="跑在终端里的通用助手 agent。",
)

config_app = typer.Typer(no_args_is_help=True, help="配置管理。")
app.add_typer(config_app, name="config")

skills_app = typer.Typer(no_args_is_help=True, help="技能管理。")
app.add_typer(skills_app, name="skills")

mcp_app = typer.Typer(no_args_is_help=True, help="MCP 服务管理。")
app.add_typer(mcp_app, name="mcp")

sessions_app = typer.Typer(no_args_is_help=True, help="历史会话。")
app.add_typer(sessions_app, name="sessions")

OPT_CONFIG = typer.Option(None, "--config", "-c", help="配置文件路径")
OPT_AGENT = typer.Option(None, "--agent", "-a", help="能力 profile")
OPT_MODEL = typer.Option(None, "--model", "-m", help="覆盖模型，如 anthropic:claude-sonnet-5")
OPT_WORKSPACE = typer.Option(None, "--workspace", "-w", help="工作目录，默认取配置里的")


@dataclass
class GlobalOptions:
    """全局选项。子命令上重复声明的那几个会覆盖这里的值。"""

    config: Path | None = None
    agent: str | None = None
    model: str | None = None
    workspace: str | None = None


def _show_version(asked: bool) -> None:
    """`--version` 一到就收工。

    必须走 **eager** 回调（见下面的 `is_eager`）：挂在 group 上的普通选项要等
    "命令选好了"才轮到处理，而 `maa --version` 后面没有命令 —— Click 会先抛
    "Missing command" 退 2，这个分支根本执行不到（`maa --version doctor` 反而能跑，
    那只是碰巧）。`--help` 没这毛病，因为它是 Click 内置的 eager 选项。
    """
    if asked:
        ui.console.print(f"my-assistant-agent {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    config: Path | None = OPT_CONFIG,
    agent: str | None = OPT_AGENT,
    model: str | None = OPT_MODEL,
    workspace: str | None = OPT_WORKSPACE,
    version: bool = typer.Option(
        False, "--version", "-V", help="版本号", callback=_show_version, is_eager=True
    ),
) -> None:
    """跑在终端里的通用助手 agent：写作 / 编码 / 任务规划。"""
    load_environment()
    ctx.obj = GlobalOptions(config, agent, model, workspace)


def load_environment() -> None:
    """把 `.env` 里的变量灌进环境。项目根的优先于 `~/.maa/`，都已设的不覆盖。

    `override=False` 是刻意的：命令行里 `export` 过的值应该赢过文件。
    """
    load_dotenv(paths.project_root() / ".env", override=False)
    load_dotenv(paths.maa_home() / ".env", override=False)


def _resolve(ctx: typer.Context, **overrides: Any) -> GlobalOptions:
    """合并全局选项和子命令选项，子命令的赢。"""
    base = ctx.obj if isinstance(ctx.obj, GlobalOptions) else GlobalOptions()
    return GlobalOptions(
        **{
            field: overrides.get(field) or getattr(base, field)
            for field in ("config", "agent", "model", "workspace")
        }
    )


def _load_config(options: GlobalOptions) -> Config:
    return load_config(options.config)


@dataclass
class Session:
    """一次会话的装配结果。带上 MCP 的连接情况和审批人，因为 banner 要显示前者、
    每一轮要问后者。"""

    built: profiles.BuiltAgent
    mcp: mcp.Loaded = field(default_factory=mcp.Loaded)
    approval_cfg: ApprovalConfig = field(default_factory=ApprovalConfig)
    approver: approval.Approver = field(default_factory=approval.Approver)


def _checkpointer(store: session.Store | None) -> Any:
    """图总得有个 checkpointer，`run` 也要。

    审批（`interrupt_on`）停下来之后要从停下那点接着跑，而"停下那点"是记在
    checkpointer 里的。实测：没有它时中断**本身**不报错（照样停），但接着跑会抛
    `RuntimeError: Cannot use Command(resume=...) without checkpointer` ——
    审到一半翻车比不问还糟。

    - `chat` 给会话库那个（`SqliteSaver`）：多轮上下文和跨进程恢复都靠它。
    - `run` 给内存的：不是要记历史，只是让审批能停一下、能接着跑。一次性任务
      没有"下次接着聊"这回事，还是不该往会话库里写。
    """
    return store.saver if store is not None else InMemorySaver()


def _assemble(options: GlobalOptions, *, store: session.Store | None = None) -> Session:
    """按选项装配 agent。每会话一次 —— MCP 工具是构图期参数，改不了。"""
    cfg = _load_config(options)
    workspace = (
        paths.resolve_workspace(options.workspace)
        if options.workspace
        else paths.resolve_workspace(cfg.backend.workspace)
    )

    loaded = _load_mcp(cfg)
    with ui.status("装配 agent…"):
        built = profiles.build_agent(
            cfg,
            options.agent or "general",
            workspace=workspace,
            model=options.model,
            extra_tools=tuple(loaded.tools),
            checkpointer=_checkpointer(store),
            interrupt_on=approval.gated(cfg.approval),
        )
    for problem in loaded.problems:
        ui.warn(f"MCP {problem}")
    _warn_ineffective(cfg, built)
    return Session(built=built, mcp=loaded, approval_cfg=cfg.approval)


def _warn_ineffective(cfg: Config, built: profiles.BuiltAgent) -> None:
    """审批名单里认不出来的名字，启动时说一声。

    不说的话它会一路静默：中间件见了不认识的工具名就跳过，用户以为拦住了，
    直到某天真被执行了才发现那道门压根没装。查不到的失败最难查。
    """
    names = profiles.tool_names(built)
    if not names:
        # 翻不到工具表（换 SDK 版本可能翻空）—— 这时候报的每一条都可能是假的
        return
    if wrong := approval.ineffective(cfg.approval, names):
        ui.warn(
            f"审批名单里的 {'、'.join(wrong)} 不在这次的工具表里 —— 写了也拦不住（名字打错了？）"
        )


def _load_mcp(cfg: Config) -> mcp.Loaded:
    """连 MCP 服务、拿工具。

    `asyncio.run` 在这儿安全：CLI 是同步的，没有正在跑的事件循环。
    """
    discovery = mcp.discover(cfg)
    for problem in discovery.problems:
        ui.warn(f"MCP 配置 {problem}")
    if not discovery.servers:
        return mcp.Loaded()
    with ui.status(f"连 MCP（{len(discovery.servers)} 个服务）…"):
        return asyncio.run(mcp.connect(discovery.servers, timeout=cfg.mcp.tool_timeout))


def _show_banner(assembled: Session) -> None:
    built = assembled.built
    count = len(skills.scan(built.skill_sources).skills)
    # 子代理跟 profile 写在一起：它们是一回事（profile 决定派谁），
    # 分开写只会让 banner 多出一行来。
    profile = built.profile.name
    if built.subagents:
        profile += f"（子代理：{'、'.join(built.subagents)}）"
    ui.banner(
        profile=profile,
        model=built.model_spec,
        backend=backends.describe(built.backend_cfg, built.workspace),
        workspace=paths.display(built.workspace),
        approval=approval.describe(assembled.approval_cfg),
        skills=f"{count} 个（maa skills list 看详情）" if count else "无",
        mcp=_mcp_summary(assembled.mcp),
    )


def _mcp_summary(loaded: mcp.Loaded) -> str:
    ok = loaded.ok_servers
    if not ok:
        # 有问题的时候不写"无" —— 用户得知道是没配，还是配了但连不上。
        return "连不上（maa mcp test 看详情）" if loaded.reports else "无"
    return (
        f"{len(loaded.tools)} 个工具 / {len(ok)} 个服务"
        f"（{'、'.join(report.server.name for report in ok)}）"
    )


def _run_turn(
    built: profiles.BuiltAgent,
    prompt: str,
    thread_id: str,
    *,
    approver: approval.Approver | None = None,
) -> str:
    """跑一轮，边跑边把工具调用和回复打到终端。返回最后一段回复文本。

    图会在危险工具前面停下来（C8 的 `interrupt_on`），所以这里是个循环：跑到停
    就问你，你点头（或拒绝）之后**从停下那一点接着跑** —— 不是重跑一遍，模型也
    不会被再问一次它已经答过的东西。

    多出来的那个 `approver` 参数是给 `run` 命令用的：它没有会话，每次现造一个。
    """
    config = {"configurable": {"thread_id": thread_id}}
    who = approver if approver is not None else approval.Approver()
    pending: Any = {"messages": [HumanMessage(prompt)]}

    while True:
        final_text, requests = _drain(built.graph, pending, config)
        if not requests:
            return final_text
        decisions: list[dict[str, Any]] = []
        for request in requests:
            decisions += who.decisions(request)
        pending = Command(resume={"decisions": decisions})


def _drain(graph: Any, inputs: Any, config: dict[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
    """跑一段（新输入或者"接着上次"），把过程打到终端。

    返回（最后一段回复, 待审批的请求）。待审批的可能是空表 —— 那就跑完了。

    同时订阅 updates（拿结构：谁调了什么工具）和 messages（拿 token 流），
    前者负责"看得见在干什么"，后者负责"不用干等"。
    """
    final_text = ""
    requests: list[Mapping[str, Any]] = []
    for mode, payload in graph.stream(inputs, config=config, stream_mode=["updates", "messages"]):
        if mode == "messages":
            chunk, _meta = payload
            text = _chunk_text(chunk)
            if text:
                ui.stream_delta(text)
        elif mode == "updates" and isinstance(payload, dict):
            requests += approval.pending(payload)
            for update in payload.values():
                if not isinstance(update, dict):
                    continue
                for message in update.get("messages", []) or []:
                    if isinstance(message, AIMessage) and message.tool_calls:
                        for call in message.tool_calls:
                            ui.tool_call(call.get("name", "?"), call.get("args"))
                    elif isinstance(message, AIMessage) and not message.tool_calls:
                        final_text = _chunk_text(message) or final_text
    return final_text, requests


def _chunk_text(message: Any) -> str:
    """把消息内容压成纯文本。内容可能是 str，也可能是分块列表。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return ""


@app.command()
def run(
    ctx: typer.Context,
    prompt: str = typer.Argument(..., help="要做的事，一句话"),
    agent: str | None = OPT_AGENT,
    model: str | None = OPT_MODEL,
    workspace: str | None = OPT_WORKSPACE,
) -> None:
    """一次性执行：跑完就退出。"""
    options = _resolve(ctx, agent=agent, model=model, workspace=workspace)
    assembled = _assemble(options)
    _show_banner(assembled)

    try:
        final_text = _run_turn(
            assembled.built, prompt, thread_id="run", approver=assembled.approver
        )
    except KeyboardInterrupt:
        ui.newline()
        ui.warn("已中断")
        raise typer.Exit(code=130) from None

    ui.newline()
    if not final_text.strip():
        ui.warn("这一轮没有文本回复 —— 检查上面有没有报错")


@app.command()
def chat(
    ctx: typer.Context,
    agent: str | None = OPT_AGENT,
    model: str | None = OPT_MODEL,
    workspace: str | None = OPT_WORKSPACE,
    resume: str | None = typer.Option(
        None, "--resume", "-r", help="接着某个会话聊：给它的 id 或 id 前缀"
    ),
) -> None:
    """交互式 REPL。每轮都存进会话库，Ctrl-D 退出后可以 --resume 接着聊。"""
    options = _resolve(ctx, agent=agent, model=model, workspace=workspace)
    store = session.Store()
    try:
        # 先认 id 再装配：id 写错了不该让用户先等一次模型和 MCP 的初始化。
        thread_id = store.match(resume) if resume else store.new_id()
        assembled = _assemble(options, store=store)
        _show_banner(assembled)
        if resume:
            _announce_resume(store, thread_id, assembled)
        else:
            ui.hint(f"会话 {thread_id} —— 退出后 `maa chat --resume {thread_id}` 接着聊")
        ui.hint("输入 /help 看可用命令；Ctrl-D 退出。")
        _repl(assembled, store, thread_id)
    except ConfigError as exc:
        ui.error(str(exc))
        raise typer.Exit(code=1) from None
    finally:
        store.close()


def _repl(assembled: Session, store: session.Store, thread_id: str) -> None:
    """读一行、跑一轮、记一笔。会话切换（/new、/resume）就是换个 thread_id。"""
    read = _line_reader()

    while True:
        try:
            line = read("\n> ")
        except (EOFError, KeyboardInterrupt):
            ui.newline()
            _say_goodbye(store, thread_id)
            return

        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            _say_goodbye(store, thread_id)
            return
        if text == "/help":
            _print_chat_help()
            continue
        if text == "/new":
            thread_id = store.new_id()
            ui.hint(f"新会话 {thread_id}（之前那条还在，maa sessions list 找得回来）")
            continue
        if text == "/threads":
            _render_sessions(store.entries(), current=thread_id)
            continue
        if text == "/resume" or text.startswith("/resume "):
            target = text[len("/resume") :].strip()
            if not target:
                ui.warn("用法：/resume <id>（/threads 看有哪些）")
                continue
            try:
                thread_id = store.match(target)
            except ConfigError as exc:
                ui.error(str(exc))
                continue
            _announce_resume(store, thread_id, assembled)
            continue
        if text.startswith("/"):
            # 不认的命令当成提示词发出去才是最坑的：用户以为在敲命令，模型
            # 收到的却是一句莫名其妙的话。
            ui.warn(f"没有这个命令：{text.split()[0]}（/help 看有哪些）")
            continue

        try:
            _run_turn(assembled.built, text, thread_id=thread_id, approver=assembled.approver)
        except KeyboardInterrupt:
            ui.newline()
            ui.warn("已中断，可以接着问")
        except ConfigError as exc:
            ui.error(str(exc))
        else:
            # 只有真跑完的这一轮才记账 —— 中途 Ctrl-C 掉的不算聊过。
            store.record(
                thread_id,
                title=text,
                profile=assembled.built.profile.name,
                workspace=paths.display(assembled.built.workspace),
            )
        ui.newline()


def _line_reader() -> Callable[[str], str]:
    """拿一个"读一行"的函数。

    prompt_toolkit 只在真终端里立得住：stdin / stdout 一旦被重定向，它在 Windows
    上直接抛 `NoConsoleScreenBufferError`。退成内置 `input()`，`maa chat < 脚本`
    才跑得起来 —— 顺带也让这段能被测试驱动。
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory

            paths.ensure_maa_home()
            device = PromptSession[str](history=FileHistory(str(paths.maa_home() / "history")))
            return device.prompt
        except Exception as exc:  # noqa: BLE001 —— 终端千奇百怪，退化成 input 总比崩掉好
            ui.warn(f"终端读行不可用（{type(exc).__name__}），改用最朴素的输入")
    return input


def _announce_resume(store: session.Store, thread_id: str, assembled: Session) -> None:
    """接着聊之前，把"这是哪条会话、跟现在这套装配对不对得上"讲清楚。"""
    entry = store.info(thread_id)
    where = f"（{entry.turns} 轮，{entry.ago()}）" if entry.turns else ""
    ui.hint(f"接着 {thread_id} 聊{where}")
    if entry.profile and entry.profile != assembled.built.profile.name:
        ui.warn(
            f"这条会话上次是 {entry.profile} profile 聊的，现在是"
            f" {assembled.built.profile.name} —— 历史都在，但工具和提示词换了"
        )


def _say_goodbye(store: session.Store, thread_id: str) -> None:
    entry = store.get(thread_id)
    if entry is None or not entry.turns:
        return  # 一句话都没聊过就退出的，没什么好存的，也不值得给个 id 让人记
    ui.hint(f"会话已存：{thread_id}（{entry.turns} 轮）—— 接着聊用 --resume {thread_id}")


def _print_chat_help() -> None:
    ui.table(
        "REPL 命令",
        ["命令", "说明"],
        [
            ("/help", "看这张表"),
            ("/threads", "列出历史会话"),
            ("/resume <id>", "切到某条历史会话（给 id 前缀就行）"),
            ("/new", "开一条新会话，旧的留着"),
            ("/exit, /quit", "退出（Ctrl-D 同）"),
        ],
    )


@sessions_app.command("list")
def sessions_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="最多列几条"),
) -> None:
    """列出存下来的会话。不连网、不建模型 —— 看一眼随时能跑。"""
    store = session.Store()
    try:
        _render_sessions(store.entries(limit=limit))
    finally:
        store.close()
    ui.hint(f"库：{paths.display(paths.sessions_db_path())}（删掉它就清空历史）")


def _render_sessions(entries: list[session.Entry], current: str = "") -> None:
    if not entries:
        ui.warn("还没有聊过的会话 —— `maa chat` 开一个")
        return
    ui.table(
        "会话（新的在前）",
        ["", "id", "标题", "轮数", "最后聊"],
        [
            (
                "▸" if entry.thread_id == current else "",
                entry.thread_id,
                entry.title or "（无标题）",
                entry.turns,
                entry.ago(),
            )
            for entry in entries
        ],
    )


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """打印生效中的配置（含来源文件）。"""
    options = _resolve(ctx)
    cfg = _load_config(options)

    source = cfg.source or paths.config_path()
    if cfg.source:
        ui.info(f"来源：{paths.display(source)}")
    else:
        ui.info(f"没有配置文件，用的是内置默认值（{paths.display(source)} 不存在）")
    ui.raw(render_config(cfg))
    ui.info(f"主目录：{paths.display(paths.maa_home())}（改 MAA_HOME 可重定位）")


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="覆盖已存在的配置"),
) -> None:
    """写出带注释的默认配置。"""
    options = _resolve(ctx)
    target = options.config or paths.config_path()

    if target.exists() and not force:
        ui.warn(f"已存在，没动它：{paths.display(target)}（要覆盖加 --force）")
        raise typer.Exit(code=1)

    paths.ensure_maa_home()
    write_config_template(target, force=force)
    ui.ok(f"写好：{paths.display(target)}")


@skills_app.command("list")
def skills_list(ctx: typer.Context) -> None:
    """列出技能源和最终生效的技能。

    刻意不走装配：这里要回答的是"agent 会看到什么"，不该为了看一眼清单就
    把模型建起来（那还得先有密钥）。
    """
    options = _resolve(ctx)
    cfg = _load_config(options)
    sources = skills.discover(cfg)
    live = {source.path: source for source in sources}

    candidates = paths.skill_search_paths(cfg.skills.extra_dirs)
    if (builtin := skills.builtin_dir()) is not None:
        candidates.insert(0, builtin)

    rows: list[tuple[str, str, str]] = []
    listed: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved in listed:
            continue
        listed.add(resolved)
        source = live.get(resolved)
        # 没挂上的目录没有来源可言，标个占位 —— 目录列已经写清楚是哪儿了，
        # 这里再回一次 `path.name` 只是把 `skills` 这五个字母重念一遍。
        rows.append(
            (
                "✓" if source else "·",
                source.label if source else "—",
                paths.display(path),
            )
        )
    ui.table("技能源（低 → 高优先级，同名后者赢）", ["", "来源", "目录"], rows)

    result = skills.scan(sources)
    if result.skills:
        ui.table(
            f"生效技能（{len(result.skills)} 个）",
            ["技能", "来源", "说明"],
            [
                (skill.name, skill.source.label, _one_line(skill.description))
                for skill in result.skills
            ],
        )
    else:
        ui.warn("一个技能都没有 —— 往 ~/.maa/skills/<名字>/SKILL.md 放一个试试")

    for problem in result.problems:
        ui.warn(problem)


def _one_line(text: str, limit: int = 60) -> str:
    """表格里放不下整段描述，掐到第一个句号。"""
    head = text.split("。")[0].strip()
    return head if len(head) <= limit else f"{head[: limit - 1]}…"


@mcp_app.command("list")
def mcp_list(ctx: typer.Context) -> None:
    """列出配置里有哪些 MCP 服务。**不连网** —— 这一条随时能跑。"""
    options = _resolve(ctx)
    cfg = _load_config(options)
    discovery = mcp.discover(cfg)

    if discovery.files:
        ui.info("读到的文件（后面的覆盖前面的）：")
        for path in discovery.files:
            ui.info(f"  {paths.display(path)}")
    else:
        # 文件不存在是正常状态，但得让人知道"我到底去哪儿找过"。
        ui.info("一个配置文件都没读到。找过这些地方：")
        for raw in cfg.mcp.config_files:
            ui.info(f"  {paths.display(paths.resolve_path(raw))}")

    if discovery.servers:
        ui.table(
            f"MCP 服务（{len(discovery.servers)} 个）",
            ["服务", "连法", "来自"],
            [
                (server.name, server.describe(), paths.display(server.source))
                for server in discovery.servers
            ],
        )
    elif not discovery.problems:
        ui.warn("配置文件里一个服务都没有 —— 照 .mcp.json.example 写一个")

    for problem in discovery.problems:
        ui.error(problem)

    ui.info("这儿只说明条目写对了，能不能连上得跑 maa mcp test。")
    if discovery.problems:
        raise typer.Exit(code=1)


@mcp_app.command("test")
def mcp_test(
    ctx: typer.Context,
    server: str | None = typer.Argument(None, help="只测这一个；不给就全测"),
) -> None:
    """真的连一下，把每个服务能提供的工具列出来。"""
    options = _resolve(ctx)
    cfg = _load_config(options)
    discovery = mcp.discover(cfg)

    for problem in discovery.problems:
        ui.error(f"配置 {problem}")
    if discovery.problems:
        raise typer.Exit(code=1)

    servers = discovery.servers
    if server:
        servers = [item for item in servers if item.name == server]
        if not servers:
            names = "、".join(item.name for item in discovery.servers) or "（一个都没有）"
            ui.error(f"没这个服务：{server}。配置里有的是 {names}")
            raise typer.Exit(code=1)

    if not servers:
        ui.warn("没有可测的服务 —— 照 .mcp.json.example 写一个再试")
        raise typer.Exit(code=1)

    loaded = asyncio.run(mcp.connect(servers, timeout=cfg.mcp.tool_timeout))

    rows: list[tuple[str, str, str]] = []
    for report in loaded.reports:
        if report.error:
            rows.append((ui.BAD, report.server.name, report.error))
        elif report.tools:
            for tool in report.tools:
                rows.append((ui.OK, report.server.name, tool.name))
        else:
            rows.append((ui.WARN, report.server.name, "连上了，但一个工具都没有"))
    ui.table("maa mcp test", ["", "服务", "工具"], rows)

    failed = [report for report in loaded.reports if report.error]
    if failed:
        ui.error(f"{len(failed)} 个服务没连上")
        raise typer.Exit(code=1)
    ui.ok(f"{len(loaded.tools)} 个工具，来自 {len(loaded.reports)} 个服务")


@app.command()
def doctor(ctx: typer.Context) -> None:
    """环境自检：依赖、配置、密钥、工作目录、能不能装配出一个 agent。"""
    options = _resolve(ctx)
    # 十一项检查在 checks 里 —— 那边只认四个字段，不认识 typer，所以这里就是全部
    # 的胶水：把全局选项递下去、按"全绿没有"定退出码。
    passed = checks.run(
        config=options.config,
        agent=options.agent,
        model=options.model,
        workspace=options.workspace,
    )
    if not passed:
        raise typer.Exit(code=1)


def _force_utf8_output() -> None:
    """把 stdout / stderr 固定成 UTF-8。

    Windows 上 Python 默认跟控制台代码页走（中文机器是 GBK），
    打 `✓` 这类符号会直接抛 UnicodeEncodeError 把 CLI 打崩。
    `errors="replace"` 兜底：实在编不出来也别崩。

    注意改的是同一个流对象，`ui` 里已经建好的 Console 会跟着变 ——
    rich 取的是 `sys.stdout` 本身，不是当时的快照。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main() -> None:
    """console_scripts 入口。"""
    _force_utf8_output()
    try:
        app()
    except ConfigError as exc:
        ui.error(str(exc))
        raise SystemExit(1) from exc
