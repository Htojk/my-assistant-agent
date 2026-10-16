"""CLI 冒烟。走子进程 —— 顺带验证 `maa` 这个入口本身立得住。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], home: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """在隔离的 MAA_HOME 下跑 `python -m maa`。

    固定 UTF-8 输出，免得断言受 Windows 控制台代码页影响。
    """
    environment = {
        **os.environ,
        "MAA_HOME": str(home),
        "PYTHONIOENCODING": "utf-8",
        **env,
    }
    return subprocess.run(
        [sys.executable, "-m", "maa", *args],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def test_help_能跑(tmp_path: Path) -> None:
    result = _run(["--help"], tmp_path / "home")

    assert result.returncode == 0
    for command in ("run", "chat", "doctor", "config"):
        assert command in result.stdout


def test_doctor_环境齐了就放行(tmp_path: Path) -> None:
    result = _run(
        ["--model", "openai:gpt-5.5", "doctor"],
        tmp_path / "home",
        OPENAI_API_KEY="sk-not-a-real-key",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent 装配" in result.stdout
    assert "write_todos" in result.stdout


def test_doctor_缺密钥时退出码非零(tmp_path: Path) -> None:
    """自检发现问题要能被脚本用上 —— 所以得有退出码，不能只印一行红字。"""
    result = _run(["doctor"], tmp_path / "home", OPENAI_API_KEY="")

    assert result.returncode == 1
    assert "密钥" in result.stdout


def test_config_init_然后_show(tmp_path: Path) -> None:
    home = tmp_path / "home"

    init = _run(["config", "init"], home)
    assert init.returncode == 0
    assert (home / "config.toml").exists()

    show = _run(["config", "show"], home)
    assert show.returncode == 0
    assert "[models.providers.openai]" in show.stdout


def test_config_init_不覆盖已有配置(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(["config", "init"], home)

    again = _run(["config", "init"], home)

    assert again.returncode == 1
    assert "已存在" in again.stdout + again.stderr
