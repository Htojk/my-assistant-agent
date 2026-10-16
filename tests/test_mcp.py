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
