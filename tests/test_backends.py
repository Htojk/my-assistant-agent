"""后端工厂：mode 是不是造出了对的东西。"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
    StateBackend,
)

from maa import backends
from maa.config import BackendConfig, ConfigError


def test_local_套了_composite_免得污染工作目录(workspace: Path) -> None:
    backend = backends.build_backend(BackendConfig(mode="local"), workspace)

    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, LocalShellBackend)


def test_内部数据前缀路由到_state(workspace: Path) -> None:
    """deepagents 会往这两个前缀写内部数据，落到真实磁盘就是污染。"""
    backend = backends.build_backend(BackendConfig(mode="local"), workspace)

    assert set(backend.routes) == set(backends.INTERNAL_ROUTE_PREFIXES)
    assert all(isinstance(route, StateBackend) for route in backend.routes.values())


def test_超时透传到后端(workspace: Path) -> None:
    """盯的是"配置有没有传下去"，所以读 SDK 内部字段也无所谓 —— 传丢了才要紧。"""
    backend = backends.build_backend(BackendConfig(mode="local", timeout=7), workspace)

    assert backend.default._default_timeout == 7


def test_readonly_不给_shell(workspace: Path) -> None:
    """两种模式都套 Composite，所以这里看的是 `default`，不是最外层。"""
    backend = backends.build_backend(BackendConfig(mode="readonly"), workspace)

    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, FilesystemBackend)
    assert not isinstance(backend.default, LocalShellBackend)


def test_额外路由能挂上(workspace: Path) -> None:
    """技能目录走的就是这条路 —— readonly 下也得挂得上，不然技能全废。"""
    route = {"/skills/demo/": StateBackend()}

    for mode in ("local", "readonly"):
        backend = backends.build_backend(BackendConfig(mode=mode), workspace, routes=route)

        assert backend.routes["/skills/demo/"] is route["/skills/demo/"]
        # 内部数据的路由不能被顶掉
        assert set(backends.INTERNAL_ROUTE_PREFIXES) <= set(backend.routes)


def test_readonly_配上拒写规则(workspace: Path) -> None:
    rules = backends.build_permissions(BackendConfig(mode="readonly"))

    assert len(rules) == 1
    assert rules[0].operations == ["write"]
    assert rules[0].mode == "deny"
    assert rules[0].paths == ["/**"]


def test_local_不额外加权限规则(workspace: Path) -> None:
    assert backends.build_permissions(BackendConfig(mode="local")) == []


def test_remote_没接通要明说(workspace: Path) -> None:
    """宁可报错，也别假装支持然后给个错的隔离保证。"""
    with pytest.raises(ConfigError, match="remote"):
        backends.build_backend(BackendConfig(mode="remote"), workspace)


def test_描述里带出无隔离这件事(workspace: Path) -> None:
    assert "无隔离" in backends.describe(BackendConfig(mode="local"), workspace)
