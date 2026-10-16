"""终端渲染。rich 的用法全收在这里，其他模块只管调用。

好处是换渲染方式（--json、--quiet）时只需要动这一个文件。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()
"""正常输出走 stdout。"""

err_console = Console(stderr=True)
"""诊断信息走 stderr，好让 `maa run ... > 文件` 拿到干净内容。"""

OK = "[green]✓[/green]"
WARN = "[yellow]![/yellow]"
BAD = "[red]✗[/red]"


def info(message: str) -> None:
    """诊断信息。走 stderr。"""
    err_console.print(f"[dim]{message}[/dim]")


def hint(message: str) -> None:
    """对用户说的提示，走 stdout。

    跟 `info` 只差一个流，但这个差别是有意的：`info` 是诊断，`maa run > 文件` 时
    不该混进结果里；而"这条会话的 id 是 xxx，下次 --resume 用它"是这次交互的
    **产出** —— 用户很可能正是靠重定向把它抄下来的。
    """
    console.print(f"[dim]{message}[/dim]")


def ok(message: str) -> None:
    console.print(f"{OK} {message}")


def warn(message: str) -> None:
    err_console.print(f"{WARN} [yellow]{message}[/yellow]")


def error(message: str) -> None:
    err_console.print(f"{BAD} [red]{message}[/red]")


def banner(
    *,
    profile: str,
    model: str,
    backend: str,
    workspace: str,
    skills: str = "",
    mcp: str = "",
) -> None:
    """开工前把"在什么环境下、用什么模型、动哪个目录"讲清楚。

    local 后端会直接在你机器上跑 shell，这个提醒不能省。
    """
    lines = [
        f"[bold]{profile}[/bold]  ·  {model}",
        f"[dim]后端[/dim]  {backend}",
        f"[dim]目录[/dim]  {workspace}",
    ]
    for label, value in (("技能", skills), ("MCP", mcp)):
        if value:
            lines.append(f"[dim]{label}[/dim]  {value}")
    body = Text.from_markup("\n".join(lines))
    console.print(Panel(body, title="maa", title_align="left", border_style="dim"))


def tool_call(name: str, args: dict[str, Any] | None = None) -> None:
    """一行工具调用。参数只挑关键的那个，不然刷屏。"""
    hint = _salient_arg(args or {})
    suffix = f" [dim]{hint}[/dim]" if hint else ""
    console.print(f"[cyan]→[/cyan] [bold]{name}[/bold]{suffix}")


def _salient_arg(args: dict[str, Any]) -> str:
    for key in ("file_path", "path", "command", "pattern", "description", "query"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value if len(value) <= 80 else f"{value[:77]}…"
    return ""


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
    grid = Table(title=title, title_justify="left", header_style="bold")
    for column in columns:
        grid.add_column(column, overflow="fold")
    for row in rows:
        grid.add_row(*[str(cell) for cell in row])
    console.print(grid)


@contextmanager
def status(message: str) -> Iterator[None]:
    """给"要等很久"的步骤一个转圈提示（不支持的终端自动降级）。"""
    with console.status(f"[dim]{message}[/dim]"):
        yield
