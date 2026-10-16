"""渲染层只钉两件事，但这两件都出过事。

一是**方括号不能被 rich 吃掉**：工具参数里的 `[a-z]`（正则）、`[models]`（TOML 段名）、
`[stderr]` 全都是正常内容，被当成样式标签就等于没给人看。二是**正常输出和诊断不能混流**：
`maa run > 文件` 要拿到干净结果，而警告得留在屏幕上。

用 rich 自己的 `Console.capture()` 抓，不靠 capsys —— 模块级 console 在 import 时就绑好
了输出目标，替换 `sys.stdout` 是抓不到的。
"""

from __future__ import annotations

from typing import Any

import pytest

from maa import ui


def _captured(fn: Any, *args: Any, **kwargs: Any) -> str:
    """抓 `ui.console`（stdout）这一次写入的内容。"""
    with ui.console.capture() as capture:
        fn(*args, **kwargs)
    return capture.get()


def _captured_err(fn: Any, *args: Any, **kwargs: Any) -> str:
    with ui.err_console.capture() as capture:
        fn(*args, **kwargs)
    return capture.get()


@pytest.mark.parametrize("brackets", ["[models]", "[a-z]+", "[stderr]", "[/dim]"])
def test_raw_原样输出方括号(brackets: str) -> None:
    """打印 TOML / 代码必须走 `raw()` —— 它是 `config show` 的输出通道，
    少个方括号就等于给了人一份坏配置。"""
    assert brackets in _captured(ui.raw, f"前半 {brackets} 后半")


@pytest.mark.parametrize("brackets", ["[a-z]+", "[stderr]"])
def test_流式输出也不解析方括号(brackets: str) -> None:
    """模型吐出来的正文里出现方括号是常事（写正则、写 TOML），流式输出这条路上
    尤其不能自作聪明。"""
    assert brackets in _captured(ui.stream_delta, f"看这个 {brackets} 就行")


def test_审批面板照原样显示参数() -> None:
    """审批的全部意义就是"让人看清要执行什么" —— 参数被充成样式标签就等于没问。"""
    shown = _captured(ui.approval, "execute", [("command", "grep -E '[a-z]+' 稿子.md")])

    assert "grep -E '[a-z]+' 稿子.md" in shown
    assert "execute" in shown


def test_审批面板把每个参数都摆出来() -> None:
    """`tool_call` 那行只挑一个关键参数（刷屏），审批不行：批的就是这些字。"""
    shown = _captured(
        ui.approval,
        "write_file",
        [("file_path", "out/a.md"), ("content", "正文")],
        note="这次会话已经批过",
    )

    assert "out/a.md" in shown
    assert "正文" in shown
    assert "这次会话已经批过" in shown


def test_诊断走_stderr_提示走_stdout() -> None:
    """这个分家是有意的：`info` 是诊断，重定向时不该混进结果；`hint` 讲的是这次交互的
    **产出**（比如会话 id），用户很可能正是靠重定向把它抄下来。"""
    assert "诊断" in _captured_err(ui.info, "诊断")
    assert "提示" in _captured(ui.hint, "提示")

    assert "不行" in _captured_err(ui.error, "不行")
    assert "当心" in _captured_err(ui.warn, "当心")
    assert "成了" in _captured(ui.ok, "成了")


def test_banner_把审批那行也带上() -> None:
    """后端和审批挨着印是有意的：本地模式下的安全边界**就是**审批，两件事一起看才
    知道自己现在什么处境。"""
    shown = _captured(
        ui.banner,
        profile="general",
        model="openai:gpt-5.5",
        backend="local · /tmp · 可执行 shell，无隔离",
        workspace="/tmp/ws",
        approval="manual · 要审批：write_file、execute",
    )

    assert "无隔离" in shown
    assert "manual" in shown
