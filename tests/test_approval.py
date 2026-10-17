"""审批：危险工具真的问了吗，问了之后真的按回答办了吗。

分两层。上面一层测决定本身（问什么、怎么理解回答），下面一层把真图跑起来 ——
"问"只有在**工具没执行**这个意义上才算数，所以下面那层的断言是看磁盘、看模型的
输入，不是看我们自己的返回值。

验收那两条在最后：manual 会拦、yolo 不拦，都走 CLI 真发 HTTP。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli, write_config
from fake_openai import ARTICLE, FINAL_TEXT
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from maa import approval, cli, profiles, ui
from maa.config import ApprovalConfig, Config

GATED = ("write_file", "edit_file", "delete", "execute")


def _request(*names: str) -> dict[str, Any]:
    """拼一个跟中间件给的一样的请求。形状是实测出来的，不是照文档猜的。"""
    return {
        "action_requests": [
            {"name": name, "args": {"path": f"{name}.txt"}, "description": f"Tool: {name}"}
            for name in names
        ],
        "review_configs": [
            {"action_name": name, "allowed_decisions": ["approve", "edit", "reject", "respond"]}
            for name in names
        ],
    }


class Scripted(BaseChatModel):
    """先发一串工具调用，收到结果之后收工。

    故意**不**看回答决定下一步 —— 审批链路的测试里，"模型被叫了第二次"本身
    就是信息（说明中断解除了），让它随机应变反而不好断言。
    """

    calls: list[tuple[str, dict[str, Any]]] = []
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "maa-scripted"

    def _generate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        already = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
        if already:
            message = AIMessage(content="收工")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": args, "id": f"call_{i}"}
                    for i, (name, args) in enumerate(type(self).calls)
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Scripted:
        return self


def _args_for(name: str) -> dict[str, Any]:
    """给每个工具一套像样的参数 —— 审批面板上看见的不该是一坨占位符。"""
    if name == "execute":
        return {"command": "echo hi"}
    if name == "edit_file":
        return {"file_path": "draft.md", "old_string": "旧", "new_string": "新"}
    return {"file_path": f"{name}.txt", "content": "x"}


def _drive(
    workspace: Path,
    *names: str,
    interrupt_on: dict[str, Any] | None = None,
    answers: str | Sequence[str] = "y",
) -> tuple[str, list[str], Scripted]:
    """跑一轮真图，返回（回复文本, 问过的提示, 模型）。

    `answers` 是喂给审批提示的回答，**一个动作一个**，用完了就空回答（= 不批准）。
    给字符串时按一个字符拆 —— `"yy"` 就是"两个都点头"，`y`/`n`/`a` 这种单键回答
    这么写最省事；要喂整句话当理由就传列表，别指望字符拆能表达一句话。
    """
    Scripted.calls = [(name, _args_for(name)) for name in names]
    Scripted.seen = []
    model = Scripted()
    built = profiles.build_agent(
        Config(),
        "general",
        workspace=workspace,
        model=model,
        checkpointer=InMemorySaver(),
        interrupt_on=interrupt_on if interrupt_on is not None else approval.gated(ApprovalConfig()),
    )

    typed = list(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return typed.pop(0) if typed else ""

    asked: list[str] = []
    text = cli._run_turn(built, "动手吧", "t-approval", approver=approval.Approver(ask=ask))
    return text, asked, model


# ------------------------------------------------------------------ 从配置到 interrupt_on


def test_manual_表上四个工具都要问() -> None:
    assert approval.gated(ApprovalConfig()) == dict.fromkeys(GATED, True)


def test_yolo_不挂审批() -> None:
    """空表不是"问了再放行"，是**连中间件都不挂** —— 不问了就是真的不问了。"""
    assert approval.gated(ApprovalConfig(mode="yolo")) == {}


def test_要审批哪些工具可以自己定() -> None:
    cfg = ApprovalConfig(gated_tools=["execute"])

    assert approval.gated(cfg) == {"execute": True}


def test_banner_那行说清楚开没开() -> None:
    assert "yolo" in approval.describe(ApprovalConfig(mode="yolo"))
    described = approval.describe(ApprovalConfig())
    assert "execute" in described and "manual" in described
    # 表是空的时候别让人以为拦住了什么
    assert "等于不问" in approval.describe(ApprovalConfig(gated_tools=[]))


# ------------------------------------------------------------------ 名单里的名字认不认识

WRITER_TOOLS = ("edit_file", "glob", "grep", "ls", "read_file", "task", "write_file", "write_todos")
PLANNER_TOOLS = ("glob", "grep", "ls", "read_file", "task", "write_file", "write_todos")
"""实测各 profile 的工具表（`maa doctor` 里那串）。默认名单**不是**人人都有这些工具，
这正是这一节存在的原因。"""


def test_默认名单不算打错() -> None:
    """writer 没有 `execute`、planner 连 `delete` 都没有，而默认名单照样写着它们。

    按"在不在工具表里"报警的话，这两个 profile 每次启动都会刷一行假警告 ——
    喊多了，真有狼那行也没人看。
    """
    assert approval.ineffective(ApprovalConfig(), WRITER_TOOLS) == []
    assert approval.ineffective(ApprovalConfig(), PLANNER_TOOLS) == []


def test_打错的名字要报出来() -> None:
    """`exec` 少写三个字母，中间件就当没这条 —— 门看着装上了，其实一直是开的。"""
    cfg = ApprovalConfig(gated_tools=["write_file", "exec"])

    assert approval.ineffective(cfg, ("write_file", "execute")) == ["exec"]


def test_表上有的工具不报() -> None:
    assert approval.ineffective(ApprovalConfig(gated_tools=["execute"]), ("execute", "ls")) == []


def test_MCP_工具配审批不算打错() -> None:
    """MCP 工具就在工具表里（名字带服务前缀），给它配审批是这个功能的正当用法。"""
    cfg = ApprovalConfig(gated_tools=["demo_echo"])

    assert approval.ineffective(cfg, ("demo_echo", "read_file")) == []
    # 服务这次没连上就照实说：现在写的这条拦不住任何东西
    assert approval.ineffective(cfg, ("read_file",)) == ["demo_echo"]


def test_yolo_下名单不参与判断() -> None:
    """yolo 是连中间件都不挂 —— 这时候说"你的名单里有不认识的名字"是废话一句。"""
    assert (
        approval.ineffective(ApprovalConfig(mode="yolo", gated_tools=["exec"]), ("read_file",))
        == []
    )


def test_启动时把错别字说一声() -> None:
    """自检里看得见是一回事，真跑起来也得说 —— 大多数人不会主动去跑 `doctor`。"""
    cfg = Config(approval=ApprovalConfig(gated_tools=["write_file", "excute"]))
    built = profiles.build_agent(cfg, "writer", model=Scripted())

    Scripted.seen = []
    with ui.err_console.capture() as captured:
        cli._warn_ineffective(cfg, built)

    said = captured.get()
    assert "excute" in said, "名单里的名字打错了要出声，不然它永远静默不生效"
    assert "write_file" not in said, "认识的名字别跟着报，报多了这行就没人看了"


def test_工具表翻不到就闭嘴() -> None:
    """`tool_names()` 翻的是 SDK 内部结构，换版本可能翻空 —— 那时候报的每条都可能是假的。"""

    class Nothing:
        graph = None

    with ui.err_console.capture() as captured:
        cli._warn_ineffective(Config(approval=ApprovalConfig(gated_tools=["exec"])), Nothing())

    assert captured.get() == ""


def test_配了工具过滤就多说一种可能() -> None:
    """名字认不出来有**两个**可能的原因：打错了，或者被 `[mcp] include/exclude` 筛掉了。

    只说"名字打错了"会让人去翻一个自己根本没打错的名字 —— 加了过滤就得把第二种
    可能也说出来。没配过滤时不带这一句：每次启动都提一个用不上的功能，那行就没人看了。
    """
    assert (
        approval.why(["demo_echo"])
        == "demo_echo 不在这次的工具表里 —— 写了也拦不住（名字打错了？）"
    )
    assert "被 [mcp] include/exclude 过滤掉了" in approval.why(["demo_echo"], filtered=True)


def test_自检和启动说的是同一句话() -> None:
    """同一句话在 `cli`（启动时）和 `checks`（自检）两处都要说 —— 分头写就会漂，
    而"自检说 A、启动说 B"比两处都不说更难查。所以那句话只该有一处出处。"""
    cfg = Config(approval=ApprovalConfig(gated_tools=["write_file", "excute"]))
    built = profiles.build_agent(cfg, "writer", model=Scripted())

    Scripted.seen = []
    with ui.err_console.capture() as captured:
        cli._warn_ineffective(cfg, built)

    said = " ".join(captured.get().split())
    # 挑的是 `why()` 独有的半句（"名字打错了？"那句两个调用方都可能自己写一份）
    assert "excute 不在这次的工具表里" in said


def test_从_stream_里捞出待审批的请求() -> None:
    request = _request("execute")

    class Fake:
        value = request

    assert approval.pending({"__interrupt__": (Fake(),)}) == [request]
    assert approval.pending({"model": {}}) == []
    assert approval.pending({"__interrupt__": ()}) == []


# ------------------------------------------------------------------------- 怎么理解回答


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "是"])
def test_点头就批准(answer: str) -> None:
    who = approval.Approver(ask=lambda _: answer)

    assert who.decide({"name": "execute", "args": {}}) == {"type": "approve"}


@pytest.mark.parametrize("answer", ["", "n", "no", "否", "   "])
def test_其它一律不批准(answer: str) -> None:
    """**回车是不批准。** 没有明说"可以"就不动手 —— 这个默认反了就是安全事故。"""
    who = approval.Approver(ask=lambda _: answer)

    decision = who.decide({"name": "execute", "args": {}})

    assert decision["type"] == "reject"
    assert "execute" in decision["message"]


def test_写一句话当理由转给模型() -> None:
    who = approval.Approver(ask=lambda _: "那个文件是我的稿子，别删")

    decision = who.decide({"name": "delete", "args": {}})

    assert decision["type"] == "reject"
    assert "我的稿子" in decision["message"], "理由要原样转给模型，不然它只会换个姿势再来一次"


def test_a_之后这个工具不再问() -> None:
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "a"

    who = approval.Approver(ask=ask)

    assert who.decide({"name": "execute", "args": {}})["type"] == "approve"
    assert who.decide({"name": "execute", "args": {}})["type"] == "approve"

    assert len(asked) == 1, "说了后面都批还问第二次就没意义了"
    assert who.allow == {"execute"}


def test_输入到头了当作拒绝() -> None:
    """没人能回答的时候，安全的答案是"不" —— `maa run` 接空管道就是这条路。"""

    def ask(prompt: str) -> str:
        raise EOFError

    who = approval.Approver(ask=ask)

    assert who.decide({"name": "execute", "args": {}})["type"] == "reject"
    assert who.closed


def test_问不出来之后剩下的直接拒() -> None:
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        raise EOFError

    who = approval.Approver(ask=ask)
    decisions = who.decisions(_request("execute", "write_file"))

    assert [d["type"] for d in decisions] == ["reject", "reject"]
    assert len(asked) == 1, "输入都没了还接着问，只会刷一屏同样的警告"


def test_决定个数必须跟请求对上() -> None:
    """中间件是按**位置**对的 —— 少给一个就是给错的工具放行。"""
    who = approval.Approver(ask=lambda _: "y")

    assert len(who.decisions(_request("execute", "write_file", "delete"))) == 3


def test_中途打断也要补完决定(monkeypatch: pytest.MonkeyPatch) -> None:
    """中途 Ctrl-C 时剩下的用拒绝补上：数量对不上比"少批一个"更危险。"""
    calls = {"n": 0}

    def ask(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt
        return "y"

    who = approval.Approver(ask=ask)
    decisions = who.decisions(_request("execute", "write_file"))

    assert [d["type"] for d in decisions] == ["reject", "reject"]
    assert who.closed


def test_参数太长就截掉() -> None:
    """几万字的稿子全打出来，审批提示本身就成了噪音；截了要说清截了多少。"""
    pairs = dict(approval.arguments({"file_path": "a.md", "content": "字" * 2000}))

    assert pairs["file_path"] == "a.md"
    assert "还有" in pairs["content"]
    assert len(pairs["content"]) < 700

    rows = dict(approval.arguments({"content": "\n".join(f"第{i}行" for i in range(50))}))
    assert "还有 42 行" in rows["content"]


def test_参数不是字符串也能看() -> None:
    pairs = approval.arguments({"timeout": 30, "env": {"A": "1"}})

    assert pairs == [("timeout", "30"), ("env", '{"A": "1"}')]


# ------------------------------------------------------------- 真图：问了没问、跑了没跑


def test_一句话里两个调用各问各的(workspace: Path) -> None:
    """批处理模式一个模型回合只停一次，但请求里有几个动作就得回答几个。

    这里一次问一个动作（`decisions` 挨个过），所以两个调用 = 两问两答 ——
    要紧的是两个都被拦下、两个都真跑了，不是弹了几次窗。
    """
    text, asked, _model = _drive(workspace, "write_file", "delete", answers="yy")

    assert len(asked) == 2
    assert "write_file" in asked[0] and "delete" in asked[1]
    assert (workspace / "write_file.txt").exists(), "批过的那个该真跑"
    assert text == "收工"


@pytest.mark.parametrize("name", GATED)
def test_四个工具都会拦下来(name: str, workspace: Path) -> None:
    """C8 的验收条件：manual 模式下这四个工具都得先问。"""
    text, asked, _model = _drive(workspace, name, answers="n")

    assert [prompt for prompt in asked if name in prompt], f"{name} 没被拦下来"
    assert text == "收工", "拒了之后这一轮要照常收尾，不是把图吊在那儿"


def test_不在表上的工具不问(workspace: Path) -> None:
    """反向：只审点名的那些。全审的话这个功能会被人关掉，等于没有。

    表上只有 `execute`，却去写、去改、去删 —— 一个提示都不该弹，而且照样跑掉。
    """
    _text, asked, _model = _drive(
        workspace, "write_file", "edit_file", "delete", interrupt_on={"execute": True}
    )

    assert asked == []
    assert (workspace / "write_file.txt").exists(), "不在表上的就该照常落地"


def test_拒了就是真没执行(workspace: Path) -> None:
    """这条是审批的**全部意义**：说了不行，磁盘上就不能有东西。"""
    _drive(workspace, "write_file", answers="")

    assert list(workspace.iterdir()) == [], "回车是不批准，文件不该出现"


def test_批了才真执行(workspace: Path) -> None:
    _drive(workspace, "write_file", answers="y")

    assert (workspace / "write_file.txt").read_text(encoding="utf-8") == "x"


def test_拒绝的理由进了模型的上下文(workspace: Path) -> None:
    _text, _asked, model = _drive(workspace, "write_file", answers=["n 那是我的稿子"])

    tool_messages = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert tool_messages, "拒绝也得给模型一个 ToolMessage，不然它以为工具还在跑"
    assert tool_messages[-1].status == "error"
    assert "我的稿子" in str(tool_messages[-1].content)


def test_yolo_一次都不问(workspace: Path) -> None:
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    Scripted.calls = [("write_file", {"file_path": "yolo.txt", "content": "x"})]
    Scripted.seen = []
    built = profiles.build_agent(
        Config(),
        "general",
        workspace=workspace,
        model=Scripted(),
        checkpointer=InMemorySaver(),
        interrupt_on=approval.gated(ApprovalConfig(mode="yolo")),
    )
    cli._run_turn(built, "写个文件", "t-yolo", approver=approval.Approver(ask=ask))

    assert asked == []
    assert (workspace / "yolo.txt").exists()


# ------------------------------------------------------------------------- 验收：走 CLI


def test_manual_管道里没人点头就不落地(maa_home: Path, project: Path, fake_openai: str) -> None:
    """默认配置（manual）下，非交互地跑就是没人能批准 —— 写操作不能落地。

    这条同时钉住"问不出来就拒"这个默认：把 EOF 当批准，等于给所有流水线开了
    后门。
    """
    write_config(maa_home)

    result = run_cli(
        ["run", "在 out/ 下写一篇文章"],
        maa_home,
        cwd=project,
        stdin="",
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / "out" / "article.md").exists(), "没人批准还是写下去了"
    assert "输入到头" in result.stdout + result.stderr
    assert FINAL_TEXT in result.stdout, "拒了之后这一轮要正常收尾"


def test_yolo_一路到底(maa_home: Path, project: Path, fake_openai: str) -> None:
    """关掉审批就一路到底 —— 这两条一起才说明开关真的接上了。"""
    write_config(maa_home, approval="yolo")

    result = run_cli(
        ["run", "在 out/ 下写一篇文章"],
        maa_home,
        cwd=project,
        stdin="",
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    written = project / "out" / "article.md"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == ARTICLE


def test_chat_里点头就放行(maa_home: Path, project: Path, fake_openai: str) -> None:
    """管道里也能点头：审批提示跟 REPL 读的是同一个 stdin，`y` 就是下一行。"""
    write_config(maa_home)

    result = run_cli(
        ["chat"],
        maa_home,
        cwd=project,
        stdin="在 out/ 下写一篇文章\ny\n/exit\n",
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "要执行" in result.stdout, "得让人看见在批准什么"
    assert (project / "out" / "article.md").exists()


def test_chat_里拒绝就不放行(maa_home: Path, project: Path, fake_openai: str) -> None:
    write_config(maa_home)

    result = run_cli(
        ["chat"],
        maa_home,
        cwd=project,
        stdin="在 out/ 下写一篇文章\nn\n/exit\n",
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / "out" / "article.md").exists()
