"""把用户给的图片路径变成消息里的内容块。

CLI 的输入面本来是纯文本（`HumanMessage(text)`）。要贴图就得改消息的**形状**：
内容从 `str` 变成内容块列表，图片是
`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}` ——
各家 OpenAI 兼容端点认的就是这个形状，图片走 data URI 直接塞在请求体里，
不用先传到一个图床上（本地 CLI 没有图床，也不该为了看张图先上传）。

单独成模块的理由跟 `checks.py` 一样：**它跟命令行毫无关系**。输入是一串路径，
输出是一个消息对象，一行 typer 都不认识。好处是它能在同进程里被测到 ——
`cli.py` 只能隔着子进程测（进程内覆盖率看不见），而这里恰恰是出错的地方：
路径认不对、文件不是图、图太大撑爆请求体，全发生在这几十行里。
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from . import paths
from .config import ConfigError

MAX_BYTES = 20 * 1024 * 1024
"""单张图的上限。

取 20 MiB 是因为这是 OpenAI 兼容端点自己写明的单图上限 —— 比它再紧就是在替
对面拒绝一个它本来会收的图。它的作用是拦住"路径打错，指到一个几百兆的文件上"：
base64 之后体积还要涨三分之一，那是能直接把请求体撑爆的量级。
"""

SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
"""按**文件头**认格式，不看后缀。

后缀是用户打的字，文件头是文件自己说的话 —— 一个叫 `shot.png` 的文本文件，
按后缀办会被当成 PNG 发出去，对面收到一坨不是图的东西。嗅探比它多不了几行。
"""

WEBP_MAGIC = (b"RIFF", b"WEBP")
"""WebP 的头分成两截：`RIFF` + 4 字节长度 + `WEBP`，中间那截是变长的。"""


@dataclass(frozen=True)
class Attachment:
    """一张已经读进内存、认得出格式的图。"""

    path: Path
    mime: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    def describe(self) -> str:
        """给人看的一行。**只有路径、大小和格式，没有内容。**

        图是几十万个字符的 base64，顺手打进日志或者 banner 就等于把整个终端刷掉 ——
        跟"密钥的值绝不打印出来"是同一个习惯（见 `checks._check_credentials`）。
        """
        kind = self.mime.removeprefix("image/")
        return f"{paths.display(self.path)}（{_human(self.size)}，{kind}）"


def _human(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def sniff(head: bytes) -> str | None:
    """从头几个字节认出图片格式；认不出来返回 `None`。"""
    for signature, mime in SIGNATURES:
        if head.startswith(signature):
            return mime
    if head[:4] == WEBP_MAGIC[0] and head[8:12] == WEBP_MAGIC[1]:
        return "image/webp"
    return None


def load(raw: str | Path) -> Attachment:
    """读一张图。读不动就抛 `ConfigError`，错误里说清是哪一步不对。

    读的时候**只读上限多一个字节**（`read(MAX_BYTES + 1)`）：先 `stat()` 看大小
    再读，遇到那种"大小是 0 但读起来没完"的特殊文件就白搭；一次带上限的读是
    实打实封死的，而且不多读一个字节也不知道是"刚好到上限"还是"超了"。
    """
    path = Path(raw).expanduser()
    where = paths.display(path)

    if not str(raw).strip():
        raise ConfigError("图片路径是空的")
    if not path.exists():
        raise ConfigError(f"找不到这个文件：{where}")
    if not path.is_file():
        raise ConfigError(f"这是个目录，不是图片：{where}")

    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ConfigError(f"读不动这个文件：{where}（{exc}）") from exc

    if not data:
        raise ConfigError(f"这个文件是空的：{where}")
    if len(data) > MAX_BYTES:
        raise ConfigError(f"图片超过 {_human(MAX_BYTES)} 了：{where}")

    mime = sniff(data)
    if mime is None:
        raise ConfigError(f"这不是一张能认出来的图（支持 PNG / JPEG / GIF / WebP）：{where}")
    return Attachment(path=path, mime=mime, data=data)


def block(item: Attachment) -> dict[str, Any]:
    """一个内容块。图片按 data URI 内联 —— 没有中间那一跳。"""
    encoded = base64.b64encode(item.data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{item.mime};base64,{encoded}"}}


def message(text: str, items: Sequence[Attachment] = ()) -> HumanMessage:
    """拼出这一轮的用户消息。

    **没图的时候还是原来那个纯文本消息**，形状一个字都不变 —— 有图才换成内容块。
    这样"贴图"这条新路不会顺带改掉所有老路的行为（历史消息、测试里的断言、
    恢复出来的上下文，都还是 `content` 为 `str` 的样子）。

    文字在前、图在后：模型先看到"这张图里有什么"这个问题，再看到图。
    """
    if not items:
        return HumanMessage(text)
    content: list[Any] = [{"type": "text", "text": text}]
    content.extend(block(item) for item in items)
    return HumanMessage(content)
