"""端到端：起一个 OpenAI 兼容的假服务，让 `maa run` 走完整条链路。

C3 的验收条件是"真的在磁盘上写出文件"。假模型只能证明装配对，证明不了
配置 → provider → HTTP → 流式解析 → 工具执行 这一路是通的 —— 所以这里
真发 HTTP，只是对面是自己人。跑完不需要任何真密钥。

假服务和夹具都在 conftest / fake_openai 里，会话恢复那组测试共用同一份。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from conftest import run_cli, tiny_png
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


def _user_message(body: dict) -> dict:
    """请求体里最后一条用户消息。前几轮的历史也在里面，要的是这一轮的。"""
    return [m for m in body["messages"] if m["role"] == "user"][-1]


def test_图片真的进了请求体(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """`--image` 这条路得一路走到 HTTP 请求体里。

    断言的是"对面到底收到了什么"：形状对不对（内容块列表）、字节对不对（base64
    解回来是同一串）—— 这两件事只有请求体说了算，中间任何一层都可以悄悄改掉它。
    """
    (project / "shot.png").write_bytes(tiny_png())

    result = run_cli(
        ["run", "这张图里有什么", "--image", "shot.png"],
        home_with_config,
        cwd=project,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    content = _user_message(Handler.requests_seen[0])["content"]
    assert content[0] == {"type": "text", "text": "这张图里有什么"}
    url = content[1]["image_url"]["url"]
    assert url == "data:image/png;base64," + base64.b64encode(tiny_png()).decode()
    # 路径本身只该出现在给用户看的那行诊断里，不该混进请求体
    assert "shot.png" not in result.stdout


def test_不带图时请求体还是老样子(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """反过来说：没有图的时候，用户消息的 `content` 还是**字符串**。

    这条钉的是"贴图没有把老路的形状改掉" —— 老路是历史、恢复、所有旧断言都在走的
    那条。它一旦变成内容块列表，坏的方式会很安静（对面多半照收）。
    """
    run_cli(
        ["run", "写一篇文章"],
        home_with_config,
        cwd=project,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert isinstance(_user_message(Handler.requests_seen[0])["content"], str)


def test_边说边调也算回复了(
    project: Path, home_with_config: Path, fake_openai: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旁白和工具调用在同一条消息里、收尾那条是空的 —— 这不算"没有文本回复"。

    这是**真跑出来的**形状：`writer` profile 写文章时，那两千多字和 `write_file`
    在同一条 AI 消息里发下来，收尾的消息一个字都没有。原来只看"没有工具调用的
    那条消息"，于是 `maa run` 会在一篇长文刚印完之后说"这一轮没有文本回复" ——
    文案本身是错的。**在成功的运行上报错比不报更坏**：它会训练人忽略这行警告，
    跟"静默筛掉一批工具"是同一类错的镜像。
    """
    monkeypatch.setattr(Handler, "prose", "我这就写到 out/article.md 里。")
    monkeypatch.setattr(Handler, "final_text", "")

    result = run_cli(
        ["run", "写一篇文章"],
        home_with_config,
        cwd=project,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "我这就写到" in result.stdout
    # 警告走 stderr（`ui.warn` → `err_console`）—— 盯着 stdout 找它的话，
    # 这条断言永远绿，测的是空气。
    assert "没有文本回复" not in result.stderr
    assert (project / "out" / "article.md").exists()
