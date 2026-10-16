"""沙箱后端工厂：把 `[backend].mode` 翻译成 deepagents 的 `BackendProtocol`。

三种模式的取舍见 docs/PLAN.md §5.3。这里只负责"造对象"和"描述它"，
不管审批 —— 审批是图上的 `interrupt_on`，走 profiles.py。
"""

from __future__ import annotations

from pathlib import Path

from deepagents import FilesystemPermission
from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
    StateBackend,
)
from deepagents.backends.protocol import BackendProtocol

from . import paths
from .config import BackendConfig, ConfigError

INTERNAL_ROUTE_PREFIXES: tuple[str, ...] = (
    "/large_tool_results/",
    "/conversation_history/",
)
"""deepagents 往这两个前缀写自己的内部数据（大工具结果、对话历史）。

裸用本地后端时它们会直接落到用户项目目录里，跟真实产物混在一起。
路由到 StateBackend 就干净了 —— 代价是这部分数据不再持久化，
对 MVP 无所谓（它们本来就是临时数据）。
"""

REMOTE_HINT = (
    'remote 沙箱还没接通：需要先装 extras（pip install -e ".[e2b]" 之类）'
    '并提供一个 Sandbox 实例。就绪前请用 backend.mode = "local" 或 "readonly"。'
)


def build_backend(
    cfg: BackendConfig,
    workspace: Path,
    *,
    routes: dict[str, BackendProtocol] | None = None,
) -> BackendProtocol:
    """按配置造后端。`workspace` 必须是已解析的绝对路径。

    `routes` 是额外要挂的虚拟前缀（C4 的技能目录走这条）。两种模式都套
    CompositeBackend，不只是为了统一 —— 技能目录在工作目录之外，没有路由
    就够不着，readonly 模式下也会连带失效。
    """
    merged = {prefix: StateBackend() for prefix in INTERNAL_ROUTE_PREFIXES}
    merged.update(routes or {})

    if cfg.mode == "local":
        return CompositeBackend(
            default=LocalShellBackend(root_dir=workspace, virtual_mode=True, timeout=cfg.timeout),
            routes=merged,
        )
    if cfg.mode == "readonly":
        return CompositeBackend(
            default=FilesystemBackend(root_dir=workspace, virtual_mode=True),
            routes=merged,
        )
    raise ConfigError(REMOTE_HINT)


def build_permissions(cfg: BackendConfig) -> list[FilesystemPermission]:
    """跟后端配套的权限规则。

    `permissions=` 管的是文件工具，管不住 `execute`（那是后端的事），
    所以 readonly 模式额外靠这里把写操作全拒掉。
    """
    if cfg.mode == "readonly":
        return [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    return []


def describe(cfg: BackendConfig, workspace: Path) -> str:
    """一句话说明当前后端的实际含义，给 banner 和 `maa doctor` 用。"""
    where = paths.display(workspace)
    if cfg.mode == "local":
        return f"local · {where} · 可执行 shell，**无隔离**"
    if cfg.mode == "readonly":
        return f"readonly · {where} · 只读，写操作被拒"
    return f"remote · {where} · 未接通"
