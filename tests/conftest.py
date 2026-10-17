"""测试夹具。

配置和路径都是全局状态（`MAA_HOME`、cwd），不隔离就会互相污染、
甚至写到真人主目录里去 —— 所以每个测试都拿到自己的临时世界。
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import threading
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_openai import Handler, serve

from maa import paths

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """把"用户主目录"指到临时目录，让测试不再看开发机的脸色。

    `paths.home_dir()` 就是为这个留的替换点（见它自己的 docstring），但技能搜索路径
    里那两个 `~/.claude/skills`、`~/.agents/skills` 是**有意**算进去的生产行为 ——
    测试不该因此随机器而变：本机那两个目录里躺着几十个技能，别人机器上可能是零个。

    环境变量也一起改：子进程（`run_cli`）里是另一份 `paths` 模块，改不到它的全局，
    而 `Path.home()` 认的是 `USERPROFILE`（Windows）/ `HOME`（其他平台）。
    """
    fake = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(paths, "home_dir", lambda: fake)
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    return fake


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


def tiny_png(width: int = 1, height: int = 1) -> bytes:
    """造一张**真的** PNG（默认 1×1）。不用 PIL：手写三个块一共十来行。

    为什么要真图而不是"几个字节的假文件头"：被测的正是"这文件到底是不是图"，
    拿假头去测它等于自己骗自己 —— 哪天嗅探换成真解析，用假头的测试会一直绿。
    """

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # IHDR：8 位色深、色彩类型 2（真彩色 RGB）、无压缩无隔行
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # 每行前面那个 \x00 是"这行不过滤"，少了它连 zlib 都解不出来
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


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
