"""CLI 入口：`maa`。

这里只做三件事 —— 解析参数、装配 agent、把过程和结果渲出来。
真正的能力都在 config / backends / profiles 里。
"""

from __future__ import annotations

import importlib.metadata as metadata
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage

from . import __version__, backends, paths, profiles, skills, ui
from .config import (
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


Recorder = Callable[[str, bool | None, str], None]
"""自检记一条：`(标签, 是否通过 —— None 表示提醒, 详情)`。"""


@app.callback()
def _root(
    ctx: typer.Context,
    config: Path | None = OPT_CONFIG,
    agent: str | None = OPT_AGENT,
    model: str | None = OPT_MODEL,
    workspace: str | None = OPT_WORKSPACE,
    version: bool = typer.Option(False, "--version", "-V", help="版本号"),
) -> None:
    """跑在终端里的通用助手 agent：写作 / 编码 / 任务规划。"""
    if version:
        ui.console.print(f"my-assistant-agent {__version__}")
        raise typer.Exit()

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


def _assemble(options: GlobalOptions, *, for_chat: bool = False) -> profiles.BuiltAgent:
    """按选项装配 agent。每会话一次 —— MCP 工具改不了构图期。"""
    cfg = _load_config(options)
    workspace = (
        paths.resolve_workspace(options.workspace)
        if options.workspace
        else paths.resolve_workspace(cfg.backend.workspace)
    )

    checkpointer = None
    if for_chat:
        from langgraph.checkpoint.memory import InMemorySaver

        # C7 换成 SqliteSaver 就能跨进程恢复；现在先让一次会话内的多轮能记住上下文。
        checkpointer = InMemorySaver()

    with ui.status("装配 agent…"):
        built = profiles.build_agent(
            cfg,
            options.agent or "general",
            workspace=workspace,
            model=options.model,
            checkpointer=checkpointer,
        )
    return built


def _show_banner(built: profiles.BuiltAgent) -> None:
    count = len(skills.scan(built.skill_sources).skills)
    ui.banner(
        profile=built.profile.name,
        model=built.model_spec,
        backend=backends.describe(built.backend_cfg, built.workspace),
        workspace=paths.display(built.workspace),
        skills=f"{count} 个（maa skills list 看详情）" if count else "无",
    )


def _run_turn(built: profiles.BuiltAgent, prompt: str, thread_id: str) -> str:
    """跑一轮，边跑边把工具调用和回复打到终端。返回最后一段回复文本。

    同时订阅 updates（拿结构：谁调了什么工具）和 messages（拿 token 流），
    前者负责"看得见在干什么"，后者负责"不用干等"。
    """
    inputs = {"messages": [HumanMessage(prompt)]}
    config = {"configurable": {"thread_id": thread_id}}

    final_text = ""
    for mode, payload in built.graph.stream(
        inputs, config=config, stream_mode=["updates", "messages"]
    ):
        if mode == "messages":
            chunk, _meta = payload
            text = _chunk_text(chunk)
            if text:
                ui.stream_delta(text)
        elif mode == "updates" and isinstance(payload, dict):
            for update in payload.values():
                if not isinstance(update, dict):
                    continue
                for message in update.get("messages", []) or []:
                    if isinstance(message, AIMessage) and message.tool_calls:
                        for call in message.tool_calls:
                            ui.tool_call(call.get("name", "?"), call.get("args"))
                    elif isinstance(message, AIMessage) and not message.tool_calls:
                        final_text = _chunk_text(message) or final_text
    return final_text


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
    built = _assemble(options)
    _show_banner(built)

    try:
        final_text = _run_turn(built, prompt, thread_id="run")
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
) -> None:
    """交互式 REPL。Ctrl-D 或 /exit 退出。"""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    options = _resolve(ctx, agent=agent, model=model, workspace=workspace)
    built = _assemble(options, for_chat=True)
    _show_banner(built)
    ui.info("输入 /help 看可用命令；Ctrl-D 退出。")

    session: PromptSession[str] = PromptSession(history=InMemoryHistory())
    thread_id = f"chat-{os.getpid()}"

    while True:
        try:
            line = session.prompt("\n> ")
        except (EOFError, KeyboardInterrupt):
            ui.newline()
            return

        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return
        if text == "/help":
            _print_chat_help()
            continue

        try:
            _run_turn(built, text, thread_id=thread_id)
        except KeyboardInterrupt:
            ui.newline()
            ui.warn("已中断，可以接着问")
        except ConfigError as exc:
            ui.error(str(exc))
        ui.newline()


def _print_chat_help() -> None:
    ui.table(
        "REPL 命令",
        ["命令", "说明"],
        [
            ("/help", "看这张表"),
            ("/exit, /quit", "退出（Ctrl-D 同）"),
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


@app.command()
def doctor(ctx: typer.Context) -> None:
    """环境自检：依赖、配置、密钥、工作目录、能不能装配出一个 agent。"""
    options = _resolve(ctx)
    checks: list[tuple[str, str, str]] = []

    def record(label: str, good: bool | None, detail: str) -> None:
        checks.append((label, "ok" if good else ("warn" if good is None else "bad"), detail))

    version = ".".join(str(part) for part in sys.version_info[:3])
    record("Python", sys.version_info >= (3, 11), f"{version}（要求 ≥3.11）")
    record("maa", True, f"{__version__} · {paths.display(Path(__file__).parent)}")
    record("依赖", *_check_dependencies())

    cfg = _check_config(options, record)
    if cfg is not None:
        _check_environment(options, cfg, record)

    if not _report(checks):
        raise typer.Exit(code=1)


def _check_config(options: GlobalOptions, record: Recorder) -> Config | None:
    try:
        cfg = _load_config(options)
    except ConfigError as exc:
        record("配置", False, str(exc).splitlines()[0])
        return None
    where = paths.display(cfg.source) if cfg.source else "内置默认值（还没 init）"
    record("配置", True, f"{where} · 模型 {cfg.models.default}")
    return cfg


def _check_environment(options: GlobalOptions, cfg: Config, record: Recorder) -> None:
    """配置能读之后再往下查 —— 后面几项都得先有 config 才有意义。"""
    record("密钥", *_check_credentials(cfg))

    workspace = paths.resolve_workspace(options.workspace or cfg.backend.workspace)
    record("工作目录", *_check_workspace(workspace))

    try:
        backends.build_backend(cfg.backend, workspace)
        record(
            "沙箱后端",
            cfg.backend.mode != "remote",
            backends.describe(cfg.backend, workspace).replace("**", ""),
        )
    except ConfigError as exc:
        record("沙箱后端", False, str(exc))

    try:
        built = profiles.build_agent(
            cfg,
            options.agent or "general",
            workspace=workspace,
            model=options.model,
        )
    except ConfigError as exc:
        record("agent 装配", False, str(exc))
        return

    names = profiles.tool_names(built)
    detail = f"{built.profile.name} · {built.model_spec}"
    if names:
        detail += f" · 工具 {len(names)} 个：{'、'.join(names)}"
    record("agent 装配", True, detail)


def _report(checks: list[tuple[str, str, str]]) -> bool:
    """打表，返回"是不是全绿"。"""
    marks = {"ok": ui.OK, "warn": ui.WARN, "bad": ui.BAD}
    ui.table(
        "maa doctor",
        ["检查", "", "结果"],
        [(label, marks[state], detail) for label, state, detail in checks],
    )
    return all(state != "bad" for _label, state, _detail in checks)


PACKAGES = ("deepagents", "langchain", "langchain-core", "langgraph", "typer", "rich")
"""自检要看的包。缺任何一个都跑不起来。"""

CONVENTIONAL_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}
"""没写 provider 段落时，按各家 SDK 的老规矩找密钥。"""


def _check_dependencies() -> tuple[bool | None, str]:
    """核对关键依赖装没装、版本对不对。"""
    found: list[str] = []
    missing: list[str] = []

    for name in PACKAGES:
        try:
            found.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)

    if missing:
        return False, f'缺：{"、".join(missing)} —— 跑 pip install -e ".[dev]"'

    # pyproject 锁了 deepagents>=0.7.24,<0.8，跨大版本 API 会漂
    if not _within(_clean_version("deepagents"), (0, 7, 24), (0, 8)):
        return None, "、".join(found) + "（deepagents 超出 pyproject 锁的 >=0.7.24,<0.8）"

    return True, "、".join(found)


def _clean_version(name: str) -> tuple[int, ...] | None:
    try:
        raw = metadata.version(name).split("+")[0].split("-")[0]
        return tuple(int(part) for part in raw.split(".")[:3])
    except (metadata.PackageNotFoundError, ValueError):
        return None


def _within(version: tuple[int, ...] | None, low: tuple[int, ...], high: tuple[int, ...]) -> bool:
    """版本号解析不了就当它没问题 —— 自检不该因为格式怪就报警。"""
    if version is None:
        return True
    return low <= version < high


def _check_credentials(cfg: Config) -> tuple[bool | None, str]:
    """看密钥在不在环境里。只说有没有，绝不打印值。"""
    spec = cfg.models.default
    provider = cfg.models.provider_for(spec)

    if provider is not None:
        key = provider.resolve_api_key()
        url = provider.resolve_base_url()
        hint = provider.api_key_env
    else:
        hint = CONVENTIONAL_KEY_ENV.get(spec.split(":", 1)[0])
        key = _env_value(hint)
        url = None

    parts = [spec, "密钥已就位" if key else "密钥没找到"]
    if url:
        parts.append(f"端点 {url}")
    if key:
        return True, " · ".join(parts)

    parts.append(f"检查 {hint or '对应环境变量'}，或写进项目 .env")
    # 没有配置文件时顺手提示一下，否则用户不知道该去哪儿配
    if cfg.source is None:
        parts.append("先跑 maa config init")
    return False, " · ".join(parts)


def _env_value(name: str | None) -> str | None:
    if not name:
        return None
    return os.environ.get(name, "").strip() or None


def _check_workspace(workspace: Path) -> tuple[bool, str]:
    """工作目录在不在、能不能写。真去写一下比问权限位可靠。"""
    if not workspace.exists():
        return False, f"{paths.display(workspace)} 不存在"
    if not workspace.is_dir():
        return False, f"{paths.display(workspace)} 不是目录"
    try:
        with tempfile.NamedTemporaryFile(dir=workspace, prefix=".maa-doctor-", delete=True):
            pass
    except OSError as exc:
        return False, f"{paths.display(workspace)} 不可写：{exc}"
    return True, f"{paths.display(workspace)} 可读写"


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
