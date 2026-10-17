"""MCP：配置校验、多文件覆盖、以及"连上之后工具真能用"。

连的部分用 fastmcp 的内存 transport，不开子进程、不碰网络 —— 要验的是我们的
适配逻辑，不是 fastmcp 的协议实现。`target_for` 是唯一被替换的一环，所以
adapter 构造、工具改名、错误隔离这些真正属于本模块的代码都是真的在跑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from maa import mcp, profiles
from maa.config import Config, McpConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_config(root: Path, servers: dict[str, Any], *, name: str = ".mcp.json") -> Path:
    target = root / name
    target.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return target


def _cfg(*files: Path) -> Config:
    return Config(mcp=McpConfig(config_files=[str(path) for path in files]))


# --------------------------------------------------------------------------- 校验


def test_url_字符串能过() -> None:
    assert mcp.validate("x", "https://example.com/mcp") is None


def test_字符串条目必须是_url() -> None:
    """裸字符串会被 fastmcp 先当文件路径试 —— 写成脚本路径就等于让人在本机执行它。"""
    problem = mcp.validate("x", "./server.py")

    assert problem is not None
    assert "http(s) URL" in problem
    assert "command" in problem  # 得告诉人正确的写法


def test_ftp_不算_url() -> None:
    problem = mcp.validate("x", {"url": "ftp://example.com/mcp"})

    assert problem is not None
    assert "http://" in problem


def test_stdio_少了_command_要说清() -> None:
    problem = mcp.validate("x", {"args": ["-y", "pkg"]})

    assert problem is not None
    assert "url" in problem and "command" in problem


def test_stdio_空_command_也不行() -> None:
    assert mcp.validate("x", {"command": "  "}) is not None


def test_args_得是字符串列表() -> None:
    assert "列表" in (mcp.validate("x", {"command": "npx", "args": "-y"}) or "")
    assert "字符串" in (mcp.validate("x", {"command": "npx", "args": [1]}) or "")


def test_不认识的键要报错() -> None:
    """`args` 拼成 `arg` 的话服务照样起得来，只是参数悄悄丢了 —— 必须当场报错。"""
    problem = mcp.validate("x", {"command": "npx", "arg": ["-y"]})

    assert problem is not None
    assert "arg" in problem


def test_url_和_command_只能给一个() -> None:
    problem = mcp.validate("x", {"url": "https://e.com/mcp", "command": "npx"})

    assert problem is not None
    assert "只能给一个" in problem


def test_传输方式只认那几种() -> None:
    spec = {"url": "https://e.com/mcp", "transport": "websocket"}

    assert "websocket" in (mcp.validate("x", spec) or "")


def test_数字条目会被挡住() -> None:
    assert mcp.validate("x", 42) is not None


def test_引用的环境变量没设要说出来(monkeypatch: pytest.MonkeyPatch) -> None:
    """不说的话，用户拿到的是一个 401，还得自己回想变量名是不是拼错了。"""
    monkeypatch.delenv("MCP_TOKEN_FOR_TEST", raising=False)
    spec = {
        "url": "https://e.com/mcp",
        "headers": {"Authorization": "Bearer ${MCP_TOKEN_FOR_TEST}"},
    }

    problem = mcp.validate("x", spec)

    assert problem is not None
    assert "MCP_TOKEN_FOR_TEST" in problem


def test_设了就放行() -> None:
    spec = {"url": "https://e.com/mcp", "headers": {"Authorization": "Bearer ${PATH}"}}

    assert mcp.validate("x", spec) is None


def test_env_里的变量会展开(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOKEN_FOR_TEST", "s3cret")
    server = mcp.McpServer(
        name="x",
        spec={"command": "npx", "args": [], "env": {"TOKEN": "${MCP_TOKEN_FOR_TEST}"}},
        source=Path("mcp.json"),
    )

    transport = mcp.target_for(server)

    assert transport.env["TOKEN"] == "s3cret"


# --------------------------------------------------------------------------- 读配置


def test_读得到配置并列出服务(project: Path) -> None:
    _write_config(project, {"a": {"url": "https://a/mcp"}, "b": {"command": "npx"}})

    discovery = mcp.discover(Config(), project=project)

    assert [server.name for server in discovery.servers] == ["a", "b"]
    assert discovery.problems == []
    assert discovery.files == [(project / ".mcp.json").resolve()]


def test_后面的文件覆盖前面的(project: Path, tmp_path: Path) -> None:
    first = _write_config(project, {"a": {"url": "https://first/mcp"}})
    second = _write_config(tmp_path, {"a": {"url": "https://second/mcp"}}, name="user.json")

    discovery = mcp.discover(_cfg(first, second), project=project)

    assert len(discovery.servers) == 1
    assert discovery.servers[0].describe() == "http: https://second/mcp"


def test_文件不存在不算故障(project: Path) -> None:
    """多数项目里根本没有 .mcp.json —— 报一条问题只会天天吓人。"""
    discovery = mcp.discover(Config(), project=project)

    assert discovery.servers == []
    assert discovery.files == []
    assert discovery.problems == []


def test_坏_json_指出位置(project: Path) -> None:
    (project / ".mcp.json").write_text('{"mcpServers": {', encoding="utf-8")

    discovery = mcp.discover(Config(), project=project)

    assert any("JSON" in problem for problem in discovery.problems)


def test_没有_mcpServers_那层要提示(project: Path) -> None:
    (project / ".mcp.json").write_text('{"servers": {}}', encoding="utf-8")

    discovery = mcp.discover(Config(), project=project)

    assert any("mcpServers" in problem for problem in discovery.problems)


def test_下划线开头的键当注释(project: Path) -> None:
    """JSON 没有注释，留个口子总比逼人删掉说明强。"""
    (project / ".mcp.json").write_text(
        json.dumps({"_note": "随便写点什么", "mcpServers": {"a": {"url": "https://a/mcp"}}}),
        encoding="utf-8",
    )

    discovery = mcp.discover(Config(), project=project)

    assert discovery.problems == []
    assert [server.name for server in discovery.servers] == ["a"]


def test_一个条目写错不连累别的(project: Path) -> None:
    _write_config(project, {"bad": "./x.py", "good": {"url": "https://good/mcp"}})

    discovery = mcp.discover(Config(), project=project)

    assert [server.name for server in discovery.servers] == ["good"]
    assert len(discovery.problems) == 1


def test_示例文件自己是合法的(monkeypatch: pytest.MonkeyPatch) -> None:
    """仓库里给的模板得能被自己的校验吃掉 —— 不然是发给用户的坑。"""
    monkeypatch.setenv("INTERNAL_MCP_TOKEN", "x")

    discovery = mcp.discover(_cfg(REPO_ROOT / ".mcp.json.example"), project=REPO_ROOT)

    assert discovery.problems == []
    assert {server.name for server in discovery.servers} == {
        "docs-langchain",
        "reference-langchain",
        "fs",
        "internal",
        "sse-legacy",
    }


# --------------------------------------------------------------------------- 连接


def _server(name: str, *tools: str) -> FastMCP:
    """内存里的一个 MCP 服务，带几个回显工具。"""
    server = FastMCP(name)

    def register(tool_name: str) -> None:
        @server.tool(name=tool_name, annotations={"readOnlyHint": True})
        def echo(text: str) -> str:
            """回显。"""
            return f"{tool_name}:{text}"

    for tool_name in tools:
        register(tool_name)
    return server


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> dict[str, FastMCP]:
    """把 `target_for` 换成内存 transport —— 只有这一环被替掉。"""
    registry: dict[str, FastMCP] = {}

    def fake_target(server: mcp.McpServer) -> FastMCPTransport:
        return FastMCPTransport(registry[server.name])

    monkeypatch.setattr(mcp, "target_for", fake_target)
    return registry


def _servers(*names: str) -> list[mcp.McpServer]:
    return [
        mcp.McpServer(name=name, spec={"command": "x"}, source=Path(".mcp.json")) for name in names
    ]


async def test_连上之后列得出工具(memory: dict[str, FastMCP]) -> None:
    """C5 验收的前半条：合法配置能列出工具。"""
    memory["demo"] = _server("demo", "echo", "add")

    loaded = await mcp.connect(_servers("demo"), timeout=30)

    assert loaded.problems == []
    assert [tool.name for tool in loaded.tools] == ["demo_echo", "demo_add"]


async def test_工具名带服务前缀(memory: dict[str, FastMCP]) -> None:
    """两个服务各有一个 `search` 是常态，不带前缀的话重名工具会互相盖掉。"""
    memory["alpha"] = _server("alpha", "search")
    memory["beta"] = _server("beta", "search")

    loaded = await mcp.connect(_servers("alpha", "beta"), timeout=30)

    assert [tool.name for tool in loaded.tools] == ["alpha_search", "beta_search"]


async def test_一个服务连不上不影响另一个(memory: dict[str, FastMCP]) -> None:
    """网络故障是常态。一个服务塌了不该让整场会话没有工具可用。"""
    memory["good"] = _server("good", "echo")
    broken = mcp.McpServer(
        name="broken", spec={"command": "no-such-binary-xyz"}, source=Path(".mcp.json")
    )

    loaded = await mcp.connect([broken, *_servers("good")], timeout=5)

    assert [report.server.name for report in loaded.ok_servers] == ["good"]
    assert [tool.name for tool in loaded.tools] == ["good_echo"]
    assert len(loaded.problems) == 1


async def test_没有服务就不连() -> None:
    assert (await mcp.connect([])).reports == []


# --------------------------------------------------------------------------- 工具过滤


def test_不配过滤就都留() -> None:
    assert mcp.keeps("demo_echo")
    assert mcp.keeps("demo_echo", include=[], exclude=[])


def test_include_是白名单() -> None:
    """`include` 非空 = 只留匹配的。空着跟"写了 `*`"是两回事：前者是"没配"。"""
    assert mcp.keeps("demo_echo", include=["demo_*"])
    assert not mcp.keeps("other_echo", include=["demo_*"])
    assert mcp.keeps("other_echo", include=["*"])


def test_exclude_赢() -> None:
    """两边都命中时按去掉算。

    这条不是随手定的：反过来（include 赢）的话，往配置里加一条 include 就会
    悄悄把某条 exclude 顶掉 —— 配置越写越松，而且是静悄悄地松。
    """
    assert not mcp.keeps("demo_delete", include=["demo_*"], exclude=["*_delete"])


def test_匹配的是带前缀的全名() -> None:
    """`demo_echo` 才是模型看见的名字，也是 `gated_tools` 里要写的名字。

    两边写同一种名字，比"这里全名那里短名"少一整类错。
    """
    assert mcp.keeps("demo_echo", include=["demo_echo"])
    assert not mcp.keeps("demo_echo", include=["echo"])


def test_模式是整串匹配不是子串() -> None:
    """`fnmatch` 认的是整串。所以 `exclude = ["echo"]` 筛不掉 `demo_echo`。

    这条得钉住，因为它违反直觉：人想的是"去掉 echo 这个工具"，写下来是
    `echo`，实际什么也没去掉，而且**不报错**。要按后缀筛得写 `*_echo`。
    （不做子串匹配是故意的：子串一开，`read` 就会连 `thread_read_all` 一起带走，
    那种"顺手多删几个"比多写一个 `*` 难查得多。）
    """
    assert mcp.keeps("demo_echo", exclude=["echo"])
    assert not mcp.keeps("demo_echo", exclude=["*_echo"])


def test_大小写敏感() -> None:
    """工具名是标识符不是文件名 —— Windows 上 `fnmatch` 那套大小写折叠不能带进来，
    否则同一份配置在 Windows 和 Linux 上筛出两张不同的表。"""
    assert mcp.keeps("demo_Echo", include=["demo_echo"]) is False


async def test_过滤在连接时生效(memory: dict[str, FastMCP]) -> None:
    """连接这一层就把过滤做完：`Loaded.tools` 永远是"agent 拿到的东西"。"""
    memory["demo"] = _server("demo", "echo", "delete", "add")

    loaded = await mcp.connect(_servers("demo"), timeout=30, exclude=["demo_delete"])

    assert [tool.name for tool in loaded.tools] == ["demo_echo", "demo_add"]
    assert loaded.dropped == ["demo_delete"]
    # 报告里留的是**原始**工具表 —— `maa mcp test` 靠它回答"为什么我的工具没进来"
    assert [tool.name for tool in loaded.reports[0].tools] == [
        "demo_echo",
        "demo_delete",
        "demo_add",
    ]


async def test_过滤不参与重名判断(memory: dict[str, FastMCP]) -> None:
    """被筛掉的那个不该再因为重名去污染 report.error。

    服务名只差一个非法字符时两个前缀会被规范化成同一个（`a.b` 和 `a b` 都是
    `a_b`），而"已经被过滤掉的工具报了重名"是条假警告。
    """
    memory["a.b"] = _server("a.b", "echo", "keep")
    memory["a b"] = _server("a b", "echo")

    loaded = await mcp.connect(_servers("a.b", "a b"), timeout=30, exclude=["a_b_echo"])

    assert [tool.name for tool in loaded.tools] == ["a_b_keep"]
    assert loaded.dropped == ["a_b_echo", "a_b_echo"]
    assert loaded.problems == []


def test_配置里的过滤字段() -> None:
    cfg = Config.model_validate({"mcp": {"include": ["demo_*"], "exclude": ["*_delete"]}})

    assert cfg.mcp.include == ["demo_*"]
    assert cfg.mcp.exclude == ["*_delete"]
    assert cfg.mcp.filtering is True
    assert Config().mcp.filtering is False, "没配过滤的时候不该到处提这两个字"


# --------------------------------------------------------------------------- 同步入口


def _text(result: Any) -> str:
    """工具返回的是内容块列表（`response_format="content_and_artifact"`），
    这里只关心正文那一段。"""
    return "".join(block["text"] for block in result if block.get("type") == "text")


def test_接上同步入口就能同步调(memory: dict[str, FastMCP]) -> None:
    """MCP 工具**只有异步实现**：不接这一手，`maa run` 里模型一调就是
    `NotImplementedError: StructuredTool does not support sync invocation`。

    这条是真跑出来的 —— 过滤做完拿真模型跑 `maa run`，工具调不动才发现。
    光验"工具绑上了"是不够的，绑上了不等于调得动。
    """
    memory["demo"] = _server("demo", "echo")

    loaded = mcp.connect_blocking(_servers("demo"), timeout=30)

    assert _text(loaded.tools[0].invoke({"text": "hi"})) == "echo:hi"


def test_每个工具接的是自己的协程(memory: dict[str, FastMCP]) -> None:
    """接同步入口时是一轮一轮闭包出去的，闭包抓错变量的话所有工具都会跑去调
    最后那一个 —— 而且照样有返回值，看不出错。所以两个一起验。"""
    memory["demo"] = _server("demo", "echo", "add")

    loaded = mcp.connect_blocking(_servers("demo"), timeout=30)

    assert [tool.name for tool in loaded.tools] == ["demo_echo", "demo_add"]
    assert _text(loaded.tools[0].invoke({"text": "hi"})) == "echo:hi"
    assert _text(loaded.tools[1].invoke({"text": "hi"})) == "add:hi"


def test_服务报的错还是一样地兜住(memory: dict[str, FastMCP]) -> None:
    """服务返回 `isError` 时，`handle_tool_error` 把它变成一条给模型看的失败内容，
    而不是把整轮打断。这一环挂在原工具对象上，接同步入口时最容易丢 —— 丢了的话
    一个工具报错就能把整场对话打断。"""
    server = FastMCP("demo")

    @server.tool(name="boom")
    def boom(text: str) -> str:
        """总是失败。"""
        raise ValueError("这个工具炸了")

    memory["demo"] = server
    loaded = mcp.connect_blocking(_servers("demo"), timeout=30)

    assert "这个工具炸了" in _text(loaded.tools[0].invoke({"text": "hi"}))


def test_没服务就不起循环(monkeypatch: pytest.MonkeyPatch) -> None:
    """`mcp test` 在一个服务都没配的时候走这条路 —— 不该为它起一条线程。

    换一条新的循环来验：`_LOOP` 是进程级的，别的测试早就把它起起来了。
    """
    fresh = mcp.Loop()
    monkeypatch.setattr(mcp, "_LOOP", fresh)

    assert mcp.connect_blocking([]).tools == []
    assert fresh._loop is None


def test_本来就同步的工具原样留着() -> None:
    """`bind` 只补异步工具那一手。以后往 `extra_tools` 里塞现成的同步工具时，
    它不该被顺手改坏（`model_copy` 出来的是另一个对象，`==` 是值比较）。"""
    tool = StructuredTool.from_function(lambda text: text, name="plain", description="原样返回。")

    assert mcp.bind([tool]) == [tool]
    assert mcp.bind([tool])[0].func is tool.func


class _FakeModel(BaseChatModel):
    """只为把图建起来 —— 这里不跑模型，只问"工具绑上没有"。"""

    @property
    def _llm_type(self) -> str:
        return "maa-fake"

    def _generate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="嗯"))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> _FakeModel:
        return self


async def test_工具能进到_agent_手里(memory: dict[str, FastMCP], workspace: Path) -> None:
    """配置 → 发现 → 连接 → 装配，整条链走通，工具最后落在模型能调的地方。"""
    memory["demo"] = _server("demo", "echo")
    loaded = await mcp.connect(_servers("demo"), timeout=30)

    built = profiles.build_agent(
        Config(),
        "general",
        workspace=workspace,
        model=_FakeModel(),
        extra_tools=tuple(loaded.tools),
    )

    assert built.extra_tools == tuple(loaded.tools)
    assert "demo_echo" in profiles.tool_names(built)


# --------------------------------------------------------------------------- 读配置：兜底那几条


def test_顶层不是对象要说清(project: Path) -> None:
    """JSON 合法但形状不对 —— 是最容易犯的那种错（照着别的工具抄成数组）。"""
    (project / ".mcp.json").write_text("[1, 2, 3]", encoding="utf-8")

    discovery = mcp.discover(Config(), project=project)

    assert discovery.servers == []
    assert any("顶层应该是一个对象" in problem for problem in discovery.problems)


def test_mcpServers_本身得是对象(project: Path) -> None:
    """层名对了但里面写成列表的话，逐条走下去会撞在 `spec.items()` 上 —— 提前拦住，
    给一句能照着改的话，比抛个 AttributeError 强。"""
    (project / ".mcp.json").write_text('{"mcpServers": ["a", "b"]}', encoding="utf-8")

    discovery = mcp.discover(Config(), project=project)

    assert discovery.servers == []
    assert any("应该是一个对象" in problem for problem in discovery.problems)


def test_多写的顶层键要说一声但不影响服务(project: Path) -> None:
    """人在顶层写个 `"version"` 之类是常事。服务照读，但得告诉他那键不生效 ——
    不然他会以为它在起作用。"""
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"a": {"url": "https://a/mcp"}}, "version": "2"}),
        encoding="utf-8",
    )

    discovery = mcp.discover(Config(), project=project)

    assert [server.name for server in discovery.servers] == ["a"]
    assert any("version" in problem and "不会生效" in problem for problem in discovery.problems)


def test_配置文件太大就跳过(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上限是防手滑（比如把别的东西复制进来）。这里把线压到 10 字节来试，不然这条
    测试自己得先写出一兆的文件。"""
    monkeypatch.setattr(mcp, "MAX_CONFIG_SIZE", 10)
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"a": {"url": "https://a/mcp"}}}), encoding="utf-8"
    )

    discovery = mcp.discover(Config(), project=project)

    assert discovery.servers == []
    assert any("文件过大" in problem for problem in discovery.problems)


def test_读不了的文件记一条不炸(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """权限、编码、坏盘 —— 都得变成一行问题，而不是把整个启动带崩。

    这里得自己让 `read_text` 抛：往配置路径上放个目录是**走不到**这个分支的
    （`_read_file` 开头那句 `is_file()` 就把目录挡回去了），所以只能直接模拟读失败。
    """
    target = _write_config(project, {"a": {"url": "https://a/mcp"}})
    real_read = Path.read_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == target:
            raise PermissionError(13, "拒绝访问")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)

    discovery = mcp.discover(Config(), project=project)

    assert discovery.servers == []
    assert discovery.files == [], "没读成的文件不该记进 files"
    assert any("读不了" in problem for problem in discovery.problems)


def test_stdio_的描述带上参数() -> None:
    """stdio 服务光给命令名看不出它在跑什么 —— `npx` 后面那串才是重点。"""
    server = mcp.McpServer(
        name="fs",
        spec={"command": "npx", "args": ["-y", "@modelcontextprotocol/server-fs", "/tmp"]},
        source=Path("mcp.json"),
    )

    described = server.describe()

    assert described.startswith("stdio: npx -y ")
    assert "/tmp" in described


def test_描述里不带凭据() -> None:
    """`mcp list` 的输出经常被整段粘进聊天窗口问问题 —— 地址、命令可以露，
    header 里的 token 不行。"""
    server = mcp.McpServer(
        name="internal",
        spec={"url": "https://internal/mcp", "headers": {"Authorization": "Bearer s3cret"}},
        source=Path("mcp.json"),
    )

    assert "s3cret" not in server.describe()
    assert "https://internal/mcp" in server.describe()
