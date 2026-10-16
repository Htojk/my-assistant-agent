"""会话持久化：存下来了没有、接得回来没有。

验收那条（`maa chat` 结束后 `--resume` 接上上下文）在最后一段，走真 HTTP 跑两个
真进程 —— 中间能传话的东西只有那个 `.db` 文件。中间那些测的是零件。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli
from fake_openai import Handler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from maa import profiles, session
from maa.config import Config, ConfigError

THREAD = "chat-20261016-153012-a1b2"


class SpyModel(BaseChatModel):
    """只回一句话，但把"这一轮我看见了什么"记下来。

    恢复成没成功，看的就是第二次收到的消息里有没有第一次说过的那句 —— 图状态
    接没接上，模型比谁都清楚。
    """

    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "maa-spy"

    def _generate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        humans = sum(1 for message in messages if isinstance(message, HumanMessage))
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=f"我看到了 {humans} 条用户消息"))]
        )

    def bind_tools(self, tools: Any, **kwargs: Any) -> SpyModel:
        return self


def _turn(workspace: Path, db: Path, thread_id: str, text: str) -> SpyModel:
    """用**全新**的库连接、图、模型跑一轮 —— 进程内模拟"另一次启动"。

    中间只留一个 .db 文件，跟真换个进程没有区别。
    """
    store = session.Store(db)
    try:
        model = SpyModel()
        built = profiles.build_agent(
            Config(), "general", workspace=workspace, model=model, checkpointer=store.saver
        )
        built.graph.invoke(
            {"messages": [HumanMessage(text)]}, config={"configurable": {"thread_id": thread_id}}
        )
        return model
    finally:
        store.close()


# --------------------------------------------------------------------- id 与标题


def test_新_id_看得出是会话() -> None:
    assert re.fullmatch(r"chat-\d{8}-\d{6}-[0-9a-f]{6}", session.new_thread_id())


def test_时间在前_肉眼排序就是时间排序() -> None:
    """id 里的时间戳是给人排序用的：`sessions list` 一眼能看出哪条早。"""
    early = session.new_thread_id(datetime(2026, 10, 16, 9, 0, 0))
    late = session.new_thread_id(datetime(2026, 10, 16, 15, 30, 12))

    assert early < late


def test_id_撞了就换一个(maa_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一秒开两个会话不能共用一个 id —— 那会把两段对话混进一个上下文。"""
    generated = iter(["chat-20261016-153012-a1b2", "chat-20261016-153012-bbbb"])
    monkeypatch.setattr(session, "new_thread_id", lambda now=None: next(generated))

    store = session.Store()
    try:
        store.record("chat-20261016-153012-a1b2", title="先来的")
        assert store.new_id() == "chat-20261016-153012-bbbb"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("写一篇关于 X 的文章", "写一篇关于 X 的文章"),
        ("  前后都有空格  ", "前后都有空格"),
        ("第一行\n第二行", "第一行 第二行"),
        ("很长" * 30, "很长" * 19 + "很…"),
    ],
)
def test_标题掐得刚好一行(raw: str, want: str) -> None:
    title = session.make_title(raw)

    assert len(title) <= session.TITLE_LIMIT
    assert title == want


def test_时间串坏了不炸() -> None:
    assert session.Entry(thread_id="x", updated_at="乱码").ago() == "乱码"
    assert session.Entry(thread_id="x").ago() == "—"


@pytest.mark.parametrize(
    ("ago", "want"),
    [
        (timedelta(seconds=30), "刚刚"),
        (timedelta(minutes=5), "5 分钟前"),
        (timedelta(hours=2), "2 小时前"),
        (timedelta(hours=30), "昨天"),
        (timedelta(days=3), "3 天前"),
    ],
)
def test_多久以前说人话(ago: timedelta, want: str) -> None:
    now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
    entry = session.Entry(thread_id="x", updated_at=(now - ago).isoformat())

    assert entry.ago(now) == want


def test_超过一个月就写日期() -> None:
    now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
    entry = session.Entry(thread_id="x", updated_at=(now - timedelta(days=40)).isoformat())

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.ago(now))


# ----------------------------------------------------------------------- 索引


def test_记一轮才建库(maa_home: Path) -> None:
    store = session.Store()
    try:
        assert not store.path.exists()

        entry = store.record(THREAD, title="暗号是香蕉", profile="general", workspace=".")

        assert store.path.exists()
        assert (entry.title, entry.turns, entry.profile) == ("暗号是香蕉", 1, "general")
    finally:
        store.close()


def test_轮数会累加(maa_home: Path) -> None:
    store = session.Store()
    try:
        for _ in range(3):
            entry = store.record(THREAD, title="第一句")
        assert entry.turns == 3
    finally:
        store.close()


def test_标题只认第一次(maa_home: Path) -> None:
    """标题是第一印象。后面某一轮冒出来的字（"接着改"）比它更不像标题。"""
    store = session.Store()
    try:
        store.record(THREAD, title="帮我规划一下发布流程")
        entry = store.record(THREAD, title="接着改")

        assert entry.title == "帮我规划一下发布流程"
    finally:
        store.close()


def test_列表新的在前(maa_home: Path) -> None:
    store = session.Store()
    try:
        store.record("chat-20261016-090000-aaaa", title="早上那条")
        store.record("chat-20261016-153012-bbbb", title="下午那条")

        assert [entry.title for entry in store.entries()] == ["下午那条", "早上那条"]
    finally:
        store.close()


def test_开过没聊过的不进列表(maa_home: Path) -> None:
    """敲开 `maa chat` 又直接退出，不该在历史里留一条没头没尾的空会话。"""
    store = session.Store()
    try:
        store.new_id()

        assert store.entries() == []
    finally:
        store.close()


def test_库不存在时列表不建库(maa_home: Path) -> None:
    """`sessions list` 是"看一眼"的命令，不该顺手在人家主目录里建文件。"""
    store = session.Store()
    try:
        assert store.entries() == []
        assert not store.path.exists()
    finally:
        store.close()


# ------------------------------------------------------------------- 认 id


def test_前缀唯一就用它(maa_home: Path) -> None:
    store = session.Store()
    try:
        store.record("chat-20261016-090000-aaaa", title="早上那条")
        store.record("chat-20261016-153012-bbbb", title="下午那条")

        assert store.match("chat-20261016-1530") == "chat-20261016-153012-bbbb"
    finally:
        store.close()


def test_前缀撞车把候选摆出来(maa_home: Path) -> None:
    """撞车了就得让人自己挑，猜一个更省事但会聊错会话。"""
    store = session.Store()
    try:
        store.record("chat-20261016-090000-aaaa")
        store.record("chat-20261016-090012-bbbb")

        with pytest.raises(ConfigError) as caught:
            store.match("chat-20261016-09")

        assert "多给几位" in str(caught.value)
        assert "chat-20261016-090000-aaaa" in str(caught.value)
    finally:
        store.close()


def test_认不出来时指条路(maa_home: Path) -> None:
    store = session.Store()
    try:
        with pytest.raises(ConfigError, match="sessions list"):
            store.match("chat-不是这条")
        with pytest.raises(ConfigError, match="sessions list"):
            store.match("   ")
    finally:
        store.close()


def test_索引丢了_全量_id_照样认(maa_home: Path, workspace: Path) -> None:
    """索引是给人看的，不是真相来源。

    它整张丢了（或者换了台机器只搬了 .db），checkpoint 还在，会话就还接得回来 ——
    只是列表里看不见它。
    """
    db = maa_home / "sessions.db"
    _turn(workspace, db, THREAD, "暗号是香蕉")

    store = session.Store(db)
    try:
        assert store.entries() == []
        assert store.match(THREAD) == THREAD
        assert store.info(THREAD).turns == 0  # 索引里没有，只剩个壳
    finally:
        store.close()


# -------------------------------------------------------- 验收：跨进程把上下文接回来


def test_下一段能接上上一段的上下文(maa_home: Path, workspace: Path) -> None:
    """C7 的验收条件。

    两段之间只有那个 .db 文件：图、模型、连接全是新造的，跟换个进程没有区别。
    """
    db = maa_home / "sessions.db"
    _turn(workspace, db, THREAD, "暗号是香蕉")
    second = _turn(workspace, db, THREAD, "暗号是什么")

    assert len(second.seen) == 1, "一句话一轮，只该问模型一次"
    seen = [message.content for message in second.seen[0] if isinstance(message, HumanMessage)]
    assert seen == ["暗号是香蕉", "暗号是什么"]


def test_状态真的落在磁盘上(maa_home: Path, workspace: Path) -> None:
    """不是内存里蒙对的 —— 另开一条连接直接查库，看有没有这行。"""
    db = maa_home / "sessions.db"
    _turn(workspace, db, THREAD, "暗号是香蕉")

    conn = sqlite3.connect(db)
    try:
        threads = {row[0] for row in conn.execute("SELECT thread_id FROM checkpoints")}
    finally:
        conn.close()

    assert THREAD in threads


def test_不带_checkpointer_就没历史(maa_home: Path, workspace: Path) -> None:
    """反过来说：多轮能记住上下文这件事**全靠** checkpointer。

    哪天真把它摘了，这条会红 —— 而不是等用户聊着聊着发现它失忆了。
    """
    model = SpyModel()
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=model)
    config = {"configurable": {"thread_id": THREAD}}

    built.graph.invoke({"messages": [HumanMessage("暗号是香蕉")]}, config=config)
    built.graph.invoke({"messages": [HumanMessage("暗号是什么")]}, config=config)

    seen = [message.content for message in model.seen[-1] if isinstance(message, HumanMessage)]
    assert seen == ["暗号是什么"], "没有 checkpointer，上一轮就该看不见"


# ---------------------------------------------------------------------- CLI


def _both(result: Any) -> str:
    """两个流合起来看。

    "看哪个流"在 maa 里是有意的：正常输出（表格、会话 id 这类产出）走 stdout，
    警告和诊断走 stderr。这里断言的是"话说出口了没有"，不挑流。
    """
    return result.stdout + result.stderr


def test_sessions_list_空库不给脸色(tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = run_cli(["sessions", "list"], home)

    assert result.returncode == 0, _both(result)
    assert "还没有聊过" in _both(result)
    assert not (home / "sessions.db").exists()


def test_resume_写错了当场报错(tmp_path: Path) -> None:
    """id 写错不该先等一次模型和 MCP 的初始化 —— 所以这里连密钥都不给。

    真去装配的话，没密钥会报"模型起不来"，这条断言的就是它没走到那一步。
    """
    result = run_cli(["chat", "--resume", "chat-不存在的"], tmp_path / "home")

    assert result.returncode == 1
    assert "sessions list" in result.stdout + result.stderr
    assert "模型起不来" not in result.stdout + result.stderr


def test_chat_能读管道里的命令(tmp_path: Path) -> None:
    """stdin 不是终端时得退成内置 input()，否则 `maa chat < 脚本` 直接崩。"""
    result = run_cli(
        ["chat"],
        tmp_path / "home",
        stdin="/threads\n/nope\n/exit\n",
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, _both(result)
    assert "还没有聊过的会话" in _both(result)
    assert "没有这个命令：/nope" in _both(result)


# ------------------------------------------------- 验收：真 HTTP、真两个进程


def _thread_id_from(stdout: str) -> str:
    found = re.search(r"chat-\d{8}-\d{6}-[0-9a-f]{6}", stdout)
    assert found, f"没打印出会话 id：{stdout}"
    return found.group()


def test_新进程接着上一轮聊(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """验收那条，一路走到 HTTP。

    第一个进程聊完退出，第二个进程只拿到一个 id。断言的是第二个进程**发给模型
    的请求体**里有第一个进程说过的话 —— 历史真进了上下文，不是只记了条账。
    """
    env = {"OPENAI_BASE_URL": fake_openai, "OPENAI_API_KEY": "sk-fake"}
    first = run_cli(["chat"], home_with_config, cwd=project, stdin="暗号是香蕉\n/exit\n", **env)

    assert first.returncode == 0, first.stdout + first.stderr
    thread_id = _thread_id_from(first.stdout)
    assert (project / "out" / "article.md").exists(), first.stdout

    before = len(Handler.requests_seen)
    second = run_cli(
        ["chat", "--resume", thread_id],
        home_with_config,
        cwd=project,
        stdin="暗号是什么\n/exit\n",
        **env,
    )

    assert second.returncode == 0, second.stdout + second.stderr
    assert "接着" in second.stdout
    resumed = Handler.requests_seen[before:]
    assert resumed, "接着聊之后一个请求都没发出去"
    sent = json.dumps(resumed[-1], ensure_ascii=False)
    assert "暗号是香蕉" in sent, "上一轮说过的话没进这次的请求"
    assert "暗号是什么" in sent
