"""终端渲染。rich 的用法全收在这里，其他模块只管调用。

好处是换渲染方式（--json、--quiet）时只需要动这一个文件。

**一条贯穿全文件的规矩：传进来的文字是内容，不是样式。** rich 默认把字符串当
markup 解析，于是 `[models]`（TOML 段名）、`[a-z]`（正则）、`[mcp]` 都会被当成
标签整段吃掉 —— 字没打出来，而且不报错。所以每个出口都得选一边：要么
`markup=False`（`raw` / `stream_delta`），要么把内容 `escape()` 掉（其余全部）。
唯一有意保留 markup 的地方是 `banner` / `tool_call` 自己拼的那几个 `[dim]`、`[bold]`，
以及 `OK` / `WARN` / `BAD` 三个记号 —— 它们是 `Text`，不走字符串这条路。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()
"""正常输出走 stdout。"""

err_console = Console(stderr=True)
"""诊断信息走 stderr，好让 `maa run ... > 文件` 拿到干净内容。"""

OK = Text("✓", style="green")
WARN = Text("!", style="yellow")
BAD = Text("✗", style="red")
"""三个状态记号。

是 `Text` 不是字符串：它们要当**表格单元格**用，而字符串单元格会被 rich 按 markup
解析 —— 拿 `"[green]✓[/green]"` 当单元格是能显示，但同一张表里别的单元格（工具名、
报错原文）就跟着一起被解析了。`Text` 不走那条路，两边都安全。
"""


def info(message: str) -> None:
    """诊断信息。走 stderr。"""
    err_console.print(f"[dim]{escape(message)}[/dim]")


def hint(message: str) -> None:
    """对用户说的提示，走 stdout。

    跟 `info` 只差一个流，但这个差别是有意的：`info` 是诊断，`maa run > 文件` 时
    不该混进结果里；而"这条会话的 id 是 xxx，下次 --resume 用它"是这次交互的
    **产出** —— 用户很可能正是靠重定向把它抄下来的。
    """
    console.print(f"[dim]{escape(message)}[/dim]")


def ok(message: str) -> None:
    console.print(_marked(OK, message, style=""))


def warn(message: str) -> None:
    err_console.print(_marked(WARN, message, style="yellow"))


def error(message: str) -> None:
    err_console.print(_marked(BAD, message, style="red"))


def _marked(mark: Text, message: str, *, style: str) -> Text:
    """`记号 + 空格 + 正文`，逐段 `append`，一个字都不解析。"""
    line = mark.copy()
    line.append(" ")
    line.append(message, style=style or None)
    return line


def banner(
    *,
    profile: str,
    model: str,
    backend: str,
    workspace: str,
    approval: str = "",
    skills: str = "",
    mcp: str = "",
) -> None:
    """开工前把"在什么环境下、用什么模型、动哪个目录"讲清楚。

    local 后端会直接在你机器上跑 shell，这个提醒不能省 —— 审批那一行挨着它，
    因为这两件事是一回事：本地模式下的安全边界就是审批。
    """
    lines = [
        f"[bold]{escape(profile)}[/bold]  ·  {escape(model)}",
        f"[dim]后端[/dim]  {escape(backend)}",
        f"[dim]审批[/dim]  {escape(approval)}",
        f"[dim]目录[/dim]  {escape(workspace)}",
    ]
    for label, value in (("技能", skills), ("MCP", mcp)):
        if value:
            lines.append(f"[dim]{label}[/dim]  {escape(value)}")
    body = Text.from_markup("\n".join(lines))
    console.print(Panel(body, title="maa", title_align="left", border_style="dim"))


def tool_call(name: str, args: dict[str, Any] | None = None) -> None:
    """一行工具调用。参数只挑关键的那个，不然刷屏。"""
    hint = _salient_arg(args or {})
    suffix = f" [dim]{escape(hint)}[/dim]" if hint else ""
    console.print(f"[cyan]→[/cyan] [bold]{escape(name)}[/bold]{suffix}")


def _salient_arg(args: dict[str, Any]) -> str:
    for key in ("file_path", "path", "command", "pattern", "description", "query"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value if len(value) <= 80 else f"{value[:77]}…"
    return ""


def approval(name: str, args: Sequence[tuple[str, str]], *, note: str = "") -> None:
    """一个等着点头的动作。`args` 是 `(键, 值)` 列表（见 `approval.arguments()`）。

    参数**不掐成一行摘要**（跟 `tool_call` 不同）：人要批的就是这些字，得看得见。

    内容用 `Text.append` 逐段拼，不走 markup —— 参数里出现 `[stderr]` 或者正则
    `[a-z]` 都很正常，当成样式标签吃掉了就等于没给人看（同 `raw()` 那条教训）。
    """
    body = Text()
    body.append(name, style="bold")
    for key, value in args:
        body.append(f"\n{key}  ", style="dim")
        body.append(str(value))
    if note:
        body.append(f"\n{note}", style="dim")
    console.print(
        Panel(body, title="要执行", title_align="left", border_style="yellow", expand=False)
    )


def answer(text: str) -> None:
    """最终回复按 markdown 渲染。"""
    console.print()
    console.print(Markdown(text))


def raw(text: str) -> None:
    """原样输出，不解析 markup。

    打印 TOML / 代码这类内容必须走这里 —— 否则 `[models]` 会被 rich 当成
    样式标签整段吃掉。
    """
    console.print(text, end="", markup=False, highlight=False)


def stream_delta(text: str) -> None:
    """流式输出的一个片段，不加换行。"""
    console.print(text, end="", markup=False, highlight=False)


def newline() -> None:
    console.print()


def table(title: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """一张表。

    单元格默认**按内容处理**（`escape` 掉），不按 markup —— 单元格里装的是工具名、
    报错原文、文件路径，这些里面出现方括号是常事（实测：`[mcp]` 就是这么没的）。
    要样式就传 `Text`（`OK` / `WARN` / `BAD` 就是），那条路不经过解析。
    """
    grid = Table(title=title, title_justify="left", header_style="bold")
    for column in columns:
        grid.add_column(column, overflow="fold")
    for row in rows:
        grid.add_row(*[cell if isinstance(cell, Text) else escape(str(cell)) for cell in row])
    console.print(grid)


@contextmanager
def status(message: str) -> Iterator[None]:
    """给"要等很久"的步骤一个转圈提示（不支持的终端自动降级）。"""
    with console.status(f"[dim]{escape(message)}[/dim]"):
        yield
