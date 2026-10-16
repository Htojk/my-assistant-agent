"""`maa doctor` 的十一项检查：逐项查，最后打一张表。

单独一个模块，是因为这段占了 `cli.py` 的五分之一，而且**跟命令行没有关系**：
它不解析参数、不认识 typer，输入是四个字段，输出是"全绿没有"。`cli.py` 那边只剩
一个壳 —— 把全局选项递进来、按返回值定退出码。

一条规矩：**自检自己不能崩**。查环境的命令因为环境有问题而挂掉，那就成了笑话。
每项都自己接住异常，翻成表上的一行红字。（所以这里的 `except Exception` 是有意写的。）
"""

from __future__ import annotations

import importlib.metadata as metadata
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import __version__, approval, backends, paths, profiles, session, ui
from .config import Config, ConfigError, load_config

Recorder = Callable[[str, bool | None, str], None]
"""自检记一条：`(标签, 是否通过 —— None 表示提醒, 详情)`。"""


def run(
    *,
    config: Path | None = None,
    agent: str | None = None,
    model: str | None = None,
    workspace: str | None = None,
) -> bool:
    """查一遍、打表，返回"是不是全绿"。

    参数就是全局选项那四个，但不收 `GlobalOptions` 本身：那是 typer 上下文里的东西，
    自检不该为了拿四个字段去认识 typer。
    """
    checks: list[tuple[str, str, str]] = []

    def record(label: str, good: bool | None, detail: str) -> None:
        checks.append((label, "ok" if good else ("warn" if good is None else "bad"), detail))

    version = ".".join(str(part) for part in sys.version_info[:3])
    record("Python", sys.version_info >= (3, 11), f"{version}（要求 ≥3.11）")
    record("maa", True, f"{__version__} · {paths.display(Path(__file__).parent)}")
    record("依赖", *_check_dependencies())

    cfg = _check_config(config, record)
    if cfg is not None:
        _check_environment(cfg, agent=agent, model=model, workspace=workspace, record=record)

    return _report(checks)


def _check_config(config: Path | None, record: Recorder) -> Config | None:
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        record("配置", False, str(exc).splitlines()[0])
        return None
    where = paths.display(cfg.source) if cfg.source else "内置默认值（还没 init）"
    record("配置", True, f"{where} · 模型 {cfg.models.default}")
    return cfg


def _check_environment(
    cfg: Config,
    *,
    agent: str | None,
    model: str | None,
    workspace: str | None,
    record: Recorder,
) -> None:
    """配置能读之后再往下查 —— 后面几项都得先有 config 才有意义。"""
    record("密钥", *_check_credentials(cfg))

    where = paths.resolve_workspace(workspace or cfg.backend.workspace)
    record("工作目录", *_check_workspace(where))
    _check_sessions(record)

    try:
        backends.build_backend(cfg.backend, where)
        record(
            "沙箱后端",
            cfg.backend.mode != "remote",
            backends.describe(cfg.backend, where).replace("**", ""),
        )
    except ConfigError as exc:
        record("沙箱后端", False, str(exc))

    try:
        built = profiles.build_agent(cfg, agent or "general", workspace=where, model=model)
    except ConfigError as exc:
        record("agent 装配", False, str(exc))
        return

    names = profiles.tool_names(built)
    detail = f"{built.profile.name} · {built.model_spec}"
    if names:
        detail += f" · 工具 {len(names)} 个：{'、'.join(names)}"
    record("agent 装配", True, detail)

    # 「审批开着」和「审批拦得住你想拦的东西」是两件事，第二件只有在知道工具表
    # 之后才说得清。只有 yolo 或者名单里全是认识的名字时这行才不出现。
    if names and (wrong := approval.ineffective(cfg.approval, names)):
        record("审批名单", None, f"{'、'.join(wrong)} 不在工具表里，拦不住（名字打错了？）")


def _check_sessions(record: Recorder) -> None:
    """会话库读不读得动。它在主目录里，权限出问题的机器上这里是第一个炸的。

    只读不写：自检不该顺带在你主目录里建文件。
    """
    path = paths.sessions_db_path()
    try:
        store = session.Store(path)
        try:
            count = len(store.entries(limit=1000))
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 —— 自检不该被任何异常打断
        record("会话库", False, f"{paths.display(path)} 读不了：{exc}")
        return
    record("会话库", True, f"{paths.display(path)} · 历史 {count} 条")


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
