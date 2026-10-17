"""贴图那条路：路径读不读得动、认不认得出是图、拼出来的消息长什么样。

这一层测的是零件，全在同进程里 —— `attachments.py` 就是为这个切出来的
（`cli.py` 只能隔着子进程跑，进程内覆盖率看不见）。端到端那条（图真的进了
HTTP 请求体）在 `test_cli.py` 和 `test_session.py` 里。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from conftest import tiny_png

from maa import attachments
from maa.config import ConfigError


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------ 认格式


@pytest.mark.parametrize(
    ("head", "want"),
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
    ],
)
def test_按文件头认格式(head: bytes, want: str) -> None:
    """认的是文件头，不是后缀 —— 这条钉的是那张嗅探表本身。

    所以喂的是"真文件头 + 后面随便"，那正是 `sniff()` 看的东西：它不解析图像，
    只认前几个字节。（WebP 的头分成两截：`RIFF` + 长度 + `WEBP`。）
    """
    assert attachments.sniff(head + b"\x00" * 32) == want


def test_认不出来就是_None() -> None:
    assert attachments.sniff(b"#!/bin/sh\n") is None
    assert attachments.sniff(b"") is None
    # 只对了半截的 WebP 不算：`RIFF` 在，`WEBP` 不在（那可能是个 wav）
    assert attachments.sniff(b"RIFF\x00\x00\x00\x00WAVE") is None


def test_后缀骗不了人(tmp_path: Path) -> None:
    """叫 `shot.png` 的文本文件必须被拦下。

    按后缀办的话，我们会把一个 data URI 标成 `image/png` 发给对面 —— 而里面
    装的是别的字节。这种事对面多半只会回一句看不懂，查起来要翻到请求体。
    """
    path = _write(tmp_path, "shot.png", "这其实是一段文本，不是图".encode())

    with pytest.raises(ConfigError, match="不是一张能认出来的图"):
        attachments.load(path)


def test_真图就读得进来(tmp_path: Path) -> None:
    path = _write(tmp_path, "shot.png", tiny_png())

    item = attachments.load(path)

    assert (item.mime, item.size) == ("image/png", len(tiny_png()))
    assert item.data == tiny_png()


# ------------------------------------------------------------------ 读不动


def test_路径不存在时说的是哪个路径(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        attachments.load(tmp_path / "没有这张图.png")

    assert "没有这张图.png" in str(caught.value)


def test_空路径当场报错() -> None:
    with pytest.raises(ConfigError, match="路径是空的"):
        attachments.load("   ")


def test_目录不算图片(tmp_path: Path) -> None:
    """`--image .` 这种手滑很常见，报"找不到"会让人以为是路径拼错了。"""
    with pytest.raises(ConfigError, match="目录"):
        attachments.load(tmp_path)


def test_空文件不算图片(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="空的"):
        attachments.load(_write(tmp_path, "empty.png", b""))


def test_太大了就不读(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上限改成 64 字节再测 —— 真造一个 20 MB 的文件只为了让这条红，不值。

    断言里带上"多大"和"上限多少"：只说"太大了"的话，人不知道该删到多小。
    """
    monkeypatch.setattr(attachments, "MAX_BYTES", 64)
    path = _write(tmp_path, "big.png", tiny_png(64, 64))

    with pytest.raises(ConfigError) as caught:
        attachments.load(path)

    assert "64 B" in str(caught.value)
    assert "超过" in str(caught.value)


def test_刚好到上限是放行的(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """边界：正好等于上限要收。多读的那一个字节就是为了分清"刚好"和"超了"。"""
    data = tiny_png()
    monkeypatch.setattr(attachments, "MAX_BYTES", len(data))

    assert attachments.load(_write(tmp_path, "exact.png", data)).size == len(data)


def test_读不动时说的是哪一步(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """磁盘错误（权限、被别的进程占着、网盘掉线）跟"没有这个文件"是两回事。

    造一个"存在但打不开"的文件各平台办法不一样（Windows 上得靠共享锁），所以
    这里直接把 `open` 换成会抛的那个 —— 要钉的是**这句话说不说得清**：得同时有
    "哪一步不对"和"哪个文件"。
    """
    path = _write(tmp_path, "shot.png", tiny_png())

    def refuse(*args: object, **kwargs: object) -> object:
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(Path, "open", refuse)

    with pytest.raises(ConfigError) as caught:
        attachments.load(path)

    assert "读不动" in str(caught.value)
    assert "拒绝访问" in str(caught.value)


@pytest.mark.parametrize(
    ("size", "want"),
    [(0, "0 B"), (106, "106 B"), (2048, "2 KB"), (1024 * 1024, "1.0 MB")],
)
def test_大小说人话(size: int, want: str) -> None:
    """`_human` 是私有的，但它印出来的字会进报错和 `describe()`。

    所以直接测它：那两处只差"这句格式化对不对"，隔着一整个 `load()` 去测反而
    得先造出 20 MB 的文件和一次权限错误。
    """
    assert attachments._human(size) == want


# ------------------------------------------------------------------ 消息形状


def test_没图还是纯文本消息() -> None:
    """没有图的时候形状一个字都不能变 —— 老路（历史、恢复、所有断言）都指望它。"""
    message = attachments.message("你好")

    assert isinstance(message.content, str)
    assert message.content == "你好"


def test_有图就是内容块_文字在前(tmp_path: Path) -> None:
    path = _write(tmp_path, "shot.png", tiny_png())

    message = attachments.message("这张图里有什么", [attachments.load(path)])

    assert isinstance(message.content, list)
    assert message.content[0] == {"type": "text", "text": "这张图里有什么"}
    url = message.content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # 解回来得是同一串字节 —— 中间任何一步编码错了这里就红
    assert base64.b64decode(url.split(",", 1)[1]) == tiny_png()


def test_多张图都进去_顺序不变(tmp_path: Path) -> None:
    first = _write(tmp_path, "a.png", tiny_png(1, 1))
    second = _write(tmp_path, "b.png", tiny_png(2, 2))

    message = attachments.message("两张图", [attachments.load(first), attachments.load(second)])

    assert isinstance(message.content, list)
    assert len(message.content) == 3
    assert base64.b64decode(message.content[1]["image_url"]["url"].split(",", 1)[1]) == tiny_png(
        1, 1
    )
    assert base64.b64decode(message.content[2]["image_url"]["url"].split(",", 1)[1]) == tiny_png(
        2, 2
    )


def test_描述里不带图的内容(tmp_path: Path) -> None:
    """`describe()` 会被打进终端和 banner，它要是把 base64 带出来就是刷屏。

    跟"密钥的值绝不打印出来"是同一个习惯：顺手能打的字符串里别装大东西。
    """
    item = attachments.load(_write(tmp_path, "shot.png", tiny_png(8, 8)))

    described = item.describe()

    assert "shot.png" in described
    assert "png" in described
    assert base64.b64encode(item.data).decode() not in described
    # 长度基本就是路径本身：多出来的只该是大小和格式那十来个字。
    # （不写死一个数字 —— 路径长短是 tmp_path 说了算，钉死它只会钉出一堆假红。）
    assert len(described) <= len(str(item.path)) + 40
