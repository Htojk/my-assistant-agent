"""会话持久化：把对话落到 `~/.maa/sessions.db`，下次开机能接着聊。

同一个库里两张账：

- langgraph 自己的 `checkpoints` / `writes` —— 图的完整状态。**恢复只靠它。**
- 我们的 `sessions` —— 给人的索引：id、标题、轮数、最后聊的时间。

为什么要另开一张表：checkpointer 那两张表里根本没有"时间"这一列，连"最近聊过
哪几个"都排不出来，标题更是没处放。但索引**不是真相来源** —— 整张丢了，
`.db` 里的 checkpoint 还在，`--resume` 照样把会话接得回来，只是列表里看不见它。

C8 的 `interrupt_on` 和权限的 `interrupt` 模式都硬依赖这里的 checkpointer。
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from . import paths
from .config import ConfigError

THREAD_PREFIX = "chat-"
"""会话 id 的前缀。一眼能认出这是会话 id，不是别的什么。"""

TITLE_LIMIT = 40
"""标题长度。会话列表一行一条，再长就得折行了。"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id  TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    profile    TEXT NOT NULL DEFAULT '',
    workspace  TEXT NOT NULL DEFAULT '',
    turns      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""

_COLUMNS = "thread_id, title, profile, workspace, turns, created_at, updated_at"


@dataclass(frozen=True)
class Entry:
    """索引里的一条会话。给人看的，恢复时用不上 —— 那些在 checkpoint 里。"""

    thread_id: str
    title: str = ""
    profile: str = ""
    workspace: str = ""
    turns: int = 0
    created_at: str = ""
    updated_at: str = ""

    def ago(self, now: datetime | None = None) -> str:
        """ "多久以前"的人话。

        `now` 只为测试留的：拿真实时钟算相对时间的断言，会在跨零点那一刻偶发失败。
        时间串解析不了就原样返回 —— 表里缺一格，不值得把整个列表打崩。
        """
        moment = _parse(self.updated_at)
        if moment is None:
            return self.updated_at or "—"

        seconds = int(((now or datetime.now(UTC)) - moment).total_seconds())
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        if seconds < 86400 * 2:
            return "昨天"
        if seconds < 86400 * 30:
            return f"{seconds // 86400} 天前"
        return moment.astimezone().strftime("%Y-%m-%d")


def _parse(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _now() -> str:
    """统一存 UTC。带时区的本地时间排序会随夏令时错位，显示时再转回本地。

    精确到微秒是为了**排序**：秒级的账在连着一轮一轮写的时候会打成平手，
    "最近聊过哪几个"就变成随机顺序了。
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def make_title(text: str, limit: int = TITLE_LIMIT) -> str:
    """从第一句话里掐一个标题。

    换行压成空格：列表是一行一条的，标题里带换行会把表撑散。
    """
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def new_thread_id(now: datetime | None = None) -> str:
    """`chat-20261016-153012-a1b2c3`：时间在前，肉眼排序等于时间排序。

    后缀那 6 位十六进制是防"同一秒开了两个会话"的：光靠时间戳，同时敲下两条
    `maa chat` 会撞成一条，两段无关的对话就混进同一个上下文里了。
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{THREAD_PREFIX}{stamp}-{secrets.token_hex(3)}"


class Store:
    """会话库。checkpointer 和索引表共用一条连接。

    共用是刻意的：两条连接各写一半，sqlite 的写锁会让"记完一轮"和"回写 checkpoint"
    变成偶发的锁冲突，而这两个操作本来就是同一个线程里一前一后的顺序关系。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else paths.sessions_db_path()
        self._conn: sqlite3.Connection | None = None
        self._saver: SqliteSaver | None = None

    @property
    def saver(self) -> SqliteSaver:
        """给 langgraph 用的 checkpointer。建表它自己会做（`setup()`）。"""
        if self._saver is None:
            self._saver = SqliteSaver(self._connect())
        return self._saver

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False：langgraph 可能在工作线程里回写 checkpoint。
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.executescript(SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        """放掉连接。不关的话 Windows 上临时目录删不掉，WAL 也停在半路。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._saver = None

    # --------------------------------------------------------------- 索引

    def new_id(self) -> str:
        """生成一个新会话 id。**不落库** —— 一轮都没聊过的会话不该进列表。"""
        for _ in range(5):
            thread_id = new_thread_id()
            if not self.known(thread_id):
                return thread_id
        # 时间戳精确到秒、后缀还有 16 位随机，连着撞五次只能是随机源坏了。
        raise RuntimeError("连着撞了五次会话 id —— 随机数源有问题？")

    def record(
        self, thread_id: str, *, title: str = "", profile: str = "", workspace: str = ""
    ) -> Entry:
        """记一轮。

        标题只在**第一次**写进去：它是"这个会话在聊什么"的第一印象，后面某一轮
        冒出来的字（"接着改"）比它更不像标题。
        """
        conn = self._connect()
        moment = _now()
        conn.execute(
            f"""
            INSERT INTO sessions ({_COLUMNS})
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                turns     = sessions.turns + 1,
                updated_at = excluded.updated_at,
                title     = CASE WHEN sessions.title = ''
                                 THEN excluded.title ELSE sessions.title END,
                profile   = excluded.profile,
                workspace = excluded.workspace
            """,
            (thread_id, make_title(title), profile, workspace, moment, moment),
        )
        conn.commit()
        return self.info(thread_id)

    def get(self, thread_id: str) -> Entry | None:
        row = (
            self._connect()
            .execute(f"SELECT {_COLUMNS} FROM sessions WHERE thread_id = ?", (thread_id,))
            .fetchone()
        )
        return Entry(*row) if row else None

    def info(self, thread_id: str) -> Entry:
        """这条会话的索引信息；索引里没有就给个只剩 id 的壳。"""
        return self.get(thread_id) or Entry(thread_id=thread_id)

    def entries(self, limit: int = 20) -> list[Entry]:
        """最近的会话，新的在前。

        库还不存在时直接返回空 —— `maa sessions list` 是"看一眼"的命令，
        不该顺手在人家主目录里建一个空库出来。
        """
        if not self.path.exists():
            return []
        rows = (
            self._connect()
            .execute(
                f"SELECT {_COLUMNS} FROM sessions ORDER BY updated_at DESC, thread_id DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        return [Entry(*row) for row in rows]

    def match(self, raw: str) -> str:
        """把用户给的短 id 还原成完整 id。

        给全了就直接认（哪怕不在索引里 —— 索引丢了 checkpoint 还在）；给前缀就得
        唯一，撞车时把候选摆出来让他自己挑。
        """
        target = raw.strip()
        if not target:
            raise ConfigError("要给会话 id —— 跑 `maa sessions list` 看有哪些")

        if self.known(target):
            return target

        hits = sorted(
            entry.thread_id
            for entry in self.entries(limit=500)
            if entry.thread_id.startswith(target)
        )
        if not hits:
            raise ConfigError(f"没有这个会话：{target}（跑 `maa sessions list` 看有哪些）")
        if len(hits) > 1:
            raise ConfigError(f"{target} 匹配到 {len(hits)} 个会话：{'、'.join(hits)} —— 多给几位")
        return hits[0]

    def known(self, thread_id: str) -> bool:
        """这条会话存在吗 —— 索引里有，或者 checkpoint 里有。"""
        conn = self._connect()
        if conn.execute("SELECT 1 FROM sessions WHERE thread_id = ?", (thread_id,)).fetchone():
            return True
        try:
            row = conn.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            # checkpointer 还没建过表 = 一条会话都没有过。
            return False
        return row is not None
