"""端到端：起一个 OpenAI 兼容的假服务，让 `maa run` 走完整条链路。

C3 的验收条件是"真的在磁盘上写出文件"。假模型只能证明装配对，证明不了
配置 → provider → HTTP → 流式解析 → 工具执行 这一路是通的 —— 所以这里
真发 HTTP，只是对面是自己人。跑完不需要任何真密钥。

假服务和夹具都在 conftest / fake_openai 里，会话恢复那组测试共用同一份。
"""

from __future__ import annotations

from pathlib import Path

from conftest import run_cli
from fake_openai import ARTICLE, FINAL_TEXT, Handler


def test_run_把文章写到磁盘上(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """C3 验收：`maa run "在 out/ 下写一篇文章"` 得真出文件。"""
    result = run_cli(
        ["run", "在 out/ 下写一篇 800 字的关于 X 的文章"],
        home_with_config,
        cwd=project,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    written = project / "out" / "article.md"
    assert written.exists(), result.stdout
    assert written.read_text(encoding="utf-8") == ARTICLE
    assert FINAL_TEXT in result.stdout


def test_run_不往工作目录里倒内部数据(
    project: Path, home_with_config: Path, fake_openai: str
) -> None:
    """大工具结果 / 对话历史该被路由到 StateBackend，不该混进用户项目。"""
    run_cli(
        ["run", "写一篇文章"],
        home_with_config,
        cwd=project,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    leftovers = {p.name for p in project.iterdir()}
    assert leftovers == {".git", "out"}


def test_请求确实走了我们配的端点(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """没走到的端点等于没测 —— 至少得证明对面真收到了带工具的请求。"""
    run_cli(
        ["run", "写一篇文章"],
        home_with_config,
        cwd=project,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert Handler.requests_seen, "假服务一个请求都没收到"
    assert any(body.get("tools") for body in Handler.requests_seen)
