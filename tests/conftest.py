"""测试夹具。

配置和路径都是全局状态（`MAA_HOME`、cwd），不隔离就会互相污染、
甚至写到真人主目录里去 —— 所以每个测试都拿到自己的临时世界。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def maa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 `MAA_HOME` 指到临时目录，别碰 `~/.maa`。"""
    home = tmp_path / "maa-home"
    monkeypatch.setenv("MAA_HOME", str(home))
    return home


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """造一个带 `.git` 的临时项目根，并把 cwd 挪过去。

    没有 `.git` 的话 `paths.project_root()` 会一路往上找到真仓库。
    """
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """给 agent 用的工作目录。"""
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def no_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """把可能影响断言的模型环境变量清干净。"""
    for name in ("MAA_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
