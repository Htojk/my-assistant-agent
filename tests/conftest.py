"""测试夹具。

配置和路径都是全局状态（`MAA_HOME`、cwd），不隔离就会互相污染、
甚至写到真人主目录里去 —— 所以每个测试都拿到自己的临时世界。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_openai import Handler, serve

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    args: list[str],
    home: Path,
    *,
    cwd: Path | None = None,
    stdin: str | None = None,
    **env: str,
) -> subprocess.CompletedProcess[str]:
    """在隔离的 `MAA_HOME` 下跑 `python -m maa`。

    固定 UTF-8 输出，免得断言受 Windows 控制台代码页影响。`stdin` 给 REPL 用 ——
    管道进去就不是终端了，那边会自动退成 `input()`（见 cli._line_reader）。
    """
    return subprocess.run(
        [sys.executable, "-m", "maa", *args],
        cwd=cwd or REPO_ROOT,
        env={
            **os.environ,
            "MAA_HOME": str(home),
            "PYTHONIOENCODING": "utf-8",
            **env,
        },
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


@pytest.fixture
def fake_openai() -> Iterator[str]:
    """一个假的 OpenAI 兼容服务，返回它的 base_url。"""
    Handler.requests_seen = []
    server, base_url = serve()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


def write_config(home: Path, *, approval: str = "manual") -> Path:
    """照 README 的样子写一份配置 —— 顺带验证模板那套键名是能用的。

    `approval` 默认跟产品默认一致（manual）。要跑"一键到底"的端到端得显式给
    `yolo`：管道里没人能点头，manual 模式下写操作会被拒。
    """
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        "[models]\n"
        'default = "openai:gpt-5.5"\n'
        "\n"
        "[models.providers.openai]\n"
        'base_url_env = "OPENAI_BASE_URL"\n'
        'api_key_env = "OPENAI_API_KEY"\n'
        "use_responses_api = false\n"
        "\n"
        "[backend]\n"
        'mode = "local"\n'
        'workspace = "."\n'
        "\n"
        "[approval]\n"
        f'mode = "{approval}"\n',
        encoding="utf-8",
    )
    return home


@pytest.fixture
def home_with_config(maa_home: Path) -> Path:
    """配好的 `~/.maa`，审批**关掉**。

    端到端那几条测的是"配置 → HTTP → 工具执行 → 产物落盘"这条链路，跑在管道里
    没人能点头 —— 审批行为由 test_approval 专门测，这里显式关掉免得两条线互相绊。
    """
    return write_config(maa_home, approval="yolo")


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
