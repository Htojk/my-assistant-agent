"""MCP 服务：从 `.mcp.json` 读配置、校验、发现工具。

配置文件用 MCP 生态通用的 `mcpServers` 结构，`transport` 一般省略、由 FastMCP
按条目形状推断：:

    {"mcpServers": {
        "docs": {"url": "https://docs.langchain.com/mcp"},
        "fs":   {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}}}

两条硬规矩，都是踩出来的：

1. **字符串条目必须是 http(s) URL。** `fastmcp.Client` 解析字符串时先当文件路径
   试、再当 URL 试，于是 `"./server.py"` 会被当脚本在本机拉起来跑。`MCPAdapter`
   自己就拒收非 URL 字符串；这里也拒，而且拒得更早 —— 配置是"从文件里来的、
   没人盯着看"的那一类输入，安全的判断该在离它最近的地方做。
2. **工具名一律加 `{服务名}_` 前缀。** 两个服务各有一个 `search` 是完全正常的，
   不前缀的话 `create_deep_agent` 拿到一堆重名工具，谁留下全看绑定顺序。

`langchain.mcp` 是 beta，导入它会抛一次 `LangChainBetaWarning`，且 API 可能漂。
所以这个模块是它唯一的调用点，真变了只动这一个文件。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import paths
from .config import Config

if TYPE_CHECKING:  # 只是给类型标注用，运行时走 _adapter() 里那次延迟导入
    from langchain.mcp import MCPAdapter

MCP_SERVERS_KEY = "mcpServers"
"""标准结构里的顶层键。"""

MAX_CONFIG_SIZE = 1024 * 1024
"""超过这个大小的 mcp.json 不读 —— 配置文件不该有这么大。"""

_TARGET_KEYS = frozenset({"url", "command", "args", "env", "cwd", "headers", "transport", "type"})
"""条目里认得的键。

其余一律报错，不是洁癖：把 `args` 拼成 `arg` 的后果是参数被静默丢掉，
服务起来了、工具也能调，只是行为不对 —— 这类错最难查。配置文件拼错的成本
必须是一次启动就报错。
"""

_TRANSPORT_ALIASES = {"http": "http", "streamable-http": "http", "sse": "sse"}
"""`transport` / `type` 的取值。省略按 http 处理。"""

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""`${VAR}` 形式的变量引用。"""

_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
"""工具名里不允许出现的字符 —— 模型侧的 function name 只收这一套。"""


@dataclass(frozen=True)
class McpServer:
    """一个 MCP 服务条目，连同它从哪个文件读来的。"""

    name: str
    spec: Mapping[str, Any]
    source: Path

    def describe(self) -> str:
        """一行说清"连去哪儿"。凭据相关的一律不显示。"""
        spec = self.spec
        if "command" in spec:
            args = " ".join(str(item) for item in spec.get("args", []))
            return f"stdio: {spec['command']} {args}".rstrip()
        transport = str(spec.get("transport") or spec.get("type") or "http")
        return f"{transport}: {spec['url']}"


@dataclass
class Discovery:
    """读配置的结果：有哪些服务、读了哪些文件、哪里写错了。"""

    servers: list[McpServer] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    """真正读到的文件。列出来是因为"配置没生效"十次有九次是路径写错了。"""

    problems: list[str] = field(default_factory=list)


@dataclass
class ServerReport:
    """一个服务的连接结果。失败不影响别的服务。"""

    server: McpServer
    tools: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class Loaded:
    """连接之后拿到的东西。"""

    tools: list[Any] = field(default_factory=list)
    reports: list[ServerReport] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return [f"{report.server.name}：{report.error}" for report in self.reports if report.error]

    @property
    def ok_servers(self) -> list[ServerReport]:
        return [report for report in self.reports if not report.error]


def discover(cfg: Config, project: Path | None = None) -> Discovery:
    """读配置、校验，不连网。

    `mcp.config_files` 按顺序读，**后面的覆盖前面的**（默认项目级在前、用户级
    在后，于是用户级赢）。同名服务整条替换而不是按键合并 —— 合并的结果是
    "一半参数来自这个文件、一半来自那个"，出问题时连自己写的是哪份都说不清。
    """
    result = Discovery()
    by_name: dict[str, McpServer] = {}

    for raw in cfg.mcp.config_files:
        path = paths.resolve_path(raw, project)
        servers = _read_file(path, result)
        if servers is None:
            continue
        result.files.append(path)
        for name, spec in servers.items():
            by_name[name] = McpServer(name=name, spec=spec, source=path)

    result.servers = list(by_name.values())
    return result


def _read_file(path: Path, result: Discovery) -> dict[str, Any] | None:
    """读一个 mcp.json。文件不存在返回 None（正常，不是故障）。

    读得到、但里面写错了，是把问题记进 `result.problems` 并返回 None ——
    单个文件坏了不该让其余文件里的服务跟着失踪。
    """
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_CONFIG_SIZE:
            result.problems.append(f"{paths.display(path)}：文件过大，跳过")
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        result.problems.append(f"{paths.display(path)}：读不了（{exc}）")
        return None
    except json.JSONDecodeError as exc:
        result.problems.append(
            f"{paths.display(path)}：不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列："
            f"{exc.msg}）"
        )
        return None

    if not isinstance(data, dict):
        result.problems.append(f"{paths.display(path)}：顶层应该是一个对象")
        return None

    raw_servers = data.get(MCP_SERVERS_KEY)
    if raw_servers is None:
        result.problems.append(
            f"{paths.display(path)}：没有 {MCP_SERVERS_KEY!r} 这一层 —— "
            f'标准结构是 {{"{MCP_SERVERS_KEY}": {{"服务名": {{...}}}}}}'
        )
        return None
    if not isinstance(raw_servers, dict):
        result.problems.append(f"{paths.display(path)}：{MCP_SERVERS_KEY!r} 应该是一个对象")
        return None

    # JSON 没有注释，`_` 开头的键就是留给注释的：认下来但不生效。
    unknown = sorted(key for key in set(data) - {MCP_SERVERS_KEY} if not key.startswith("_"))
    if unknown:
        result.problems.append(
            f"{paths.display(path)}：顶层多了 {MCP_SERVERS_KEY!r} 以外的键 "
            f"（{'、'.join(unknown)}），它们不会生效"
        )

    ok: dict[str, Any] = {}
    for name, spec in raw_servers.items():
        where = f"{paths.display(path)} 的 {name!r}"
        if not isinstance(name, str) or not _NAME_UNSAFE.sub("", name):
            result.problems.append(f"{where}：服务名不能为空，也不能全是非法字符")
            continue
        problem = validate(name, spec)
        if problem:
            result.problems.append(f"{where}：{problem}")
            continue
        ok[name] = spec
    return ok


def validate(name: str, spec: Any) -> str | None:
    """检查一个服务条目。合法返回 None，否则返回给人看的一句话。

    校验放在这儿、不在连的时候做，是因为这里的错都是"写错了"，越早说越好 ——
    等到 connect 阶段才报，用户已经在等一个网络超时了。
    """
    if isinstance(spec, str):
        return _validate_url(spec, bare=True)
    if not isinstance(spec, dict):
        return (
            f"应该是一个对象或一个 URL 字符串，实际是 {type(spec).__name__} —— "
            '本地服务要写成 {"command": "npx", "args": [...]}'
        )

    if unknown := sorted(set(spec) - _TARGET_KEYS):
        return f"不认识的键 {'、'.join(unknown)}（认得的是 {'、'.join(sorted(_TARGET_KEYS))}）"

    has_url, has_command = "url" in spec, "command" in spec
    if has_url and has_command:
        return "`url` 和 `command` 只能给一个 —— 前者是远程服务，后者是本地进程"
    if not has_url and not has_command:
        return '既没 `url` 也没 `command` —— 远程服务写 {"url": "https://..."}，本地服务写 {"command": "npx", "args": [...]}'

    if has_url:
        problem = _validate_url(spec["url"])
        if problem:
            return problem
        transport = spec.get("transport", spec.get("type"))
        if transport is not None and transport not in _TRANSPORT_ALIASES:
            return (
                f"`transport` 只能是 {'、'.join(sorted(_TRANSPORT_ALIASES))}，实际是 {transport!r}"
            )
        return _validate_headers(spec)

    command = spec["command"]
    if not isinstance(command, str) or not command.strip():
        return "`command` 得是一个非空字符串"

    args = spec.get("args", [])
    if not isinstance(args, list):
        return f"`args` 得是一个列表，实际是 {type(args).__name__}"
    for index, item in enumerate(args):
        if not isinstance(item, str):
            return f"`args` 第 {index} 项得是字符串，实际是 {type(item).__name__}"

    if "cwd" in spec and not isinstance(spec["cwd"], str):
        return "`cwd` 得是字符串"

    return _validate_env(spec)


def _validate_url(value: Any, *, bare: bool = False) -> str | None:
    """`bare` 表示这是裸字符串条目 —— 那种情况下要说清为什么不能这么写。"""
    if not isinstance(value, str):
        return f"`url` 得是字符串，实际是 {type(value).__name__}"
    if not value.startswith(("http://", "https://")):
        if bare:
            return (
                f"字符串条目 {value!r} 不是 http(s) URL —— 裸字符串会被 fastmcp 当成"
                "本机脚本路径拉起来执行，所以本地服务必须写成 "
                '{"command": "npx", "args": [...]} 这样的对象'
            )
        return f"`url` 必须以 http:// 或 https:// 开头，实际是 {value!r}"
    return None


def _validate_env(spec: Mapping[str, Any]) -> str | None:
    return _validate_mapping(spec, "env")


def _validate_headers(spec: Mapping[str, Any]) -> str | None:
    return _validate_mapping(spec, "headers")


def _validate_mapping(spec: Mapping[str, Any], key: str) -> str | None:
    """`env` / `headers` 长得一样，检查也一样：一个字符串到字符串的对象。"""
    mapping = spec.get(key, {})
    if not isinstance(mapping, dict):
        return f"`{key}` 得是一个对象，实际是 {type(mapping).__name__}"
    for name, value in mapping.items():
        if not isinstance(value, str):
            return f"`{key}` 里 {name!r} 的值得是字符串"
    return _missing_vars(mapping.values())


def _missing_vars(values: Iterable[str]) -> str | None:
    """`${VAR}` 引用的环境变量在不在。

    在装配前就说，而不是等到发出一个空 `Authorization` 头换来一个 401 ——
    401 还得往回翻配置才知道是自己变量名写错了。
    """
    referenced = {name for value in values for name in _ENV_REF.findall(value)}
    missing = sorted(name for name in referenced if not os.environ.get(name, "").strip())
    if not missing:
        return None
    return (
        f"环境变量 {'、'.join(missing)} 没设"
        "（值可以写 ${名字} 从环境里取，密钥就不用落在配置文件里）"
    )


def _expand(text: str) -> str:
    return _ENV_REF.sub(lambda match: os.environ.get(match.group(1), ""), text)


def _prefix(name: str) -> str:
    return _NAME_UNSAFE.sub("_", name)


def target_for(server: McpServer) -> Any:
    """把条目变成 fastmcp 认的 target。

    这里自己把字符串换成显式的 transport 对象，而不是把 URL 直接丢给
    `MCPAdapter` —— 因为绕开它就意味着绕开它那道"字符串必须是 URL"的守卫，
    所以要求：**能走到这儿的 spec 一定是 `validate()` 过了的**。
    """
    spec = dict(server.spec)
    if "command" in spec:
        from fastmcp.client.transports import StdioTransport

        env = spec.get("env") or None
        return StdioTransport(
            spec["command"],
            list(spec.get("args", [])),
            env={key: _expand(value) for key, value in env.items()} if env else None,
            cwd=spec.get("cwd"),
        )

    from fastmcp.client.transports import SSETransport, StreamableHttpTransport

    headers = spec.get("headers") or None
    expanded = {key: _expand(value) for key, value in headers.items()} if headers else None
    if _TRANSPORT_ALIASES.get(spec.get("transport") or spec.get("type")) == "sse":
        return SSETransport(spec["url"], headers=expanded)
    return StreamableHttpTransport(spec["url"], headers=expanded)


async def connect(servers: Sequence[McpServer], *, timeout: int = 120) -> Loaded:
    """连上每个服务，发现工具。**一个服务挂了不影响其他服务。**

    并行连：串行的话，一个不可达的服务会把整个装配卡满一个超时，用户眼看着光标
    转圈却不知道是在等谁。（网络故障是常态，不是异常情况。）
    """
    loaded = Loaded()
    if not servers:
        return loaded

    outcomes = await asyncio.gather(
        *(_one(server, timeout) for server in servers), return_exceptions=True
    )
    seen: dict[str, str] = {}
    for server, outcome in zip(servers, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            report = ServerReport(server=server, error=_explain(outcome))
        else:
            report = outcome
        loaded.reports.append(report)
        for tool in report.tools:
            if tool.name in seen:
                # 理论上不会重名（前缀带了服务名），但服务名可以只差一个非法
                # 字符 —— 那种情况下两个前缀会被规范化成同一个。
                report.error = report.error or (
                    f"工具 {tool.name!r} 跟 {seen[tool.name]!r} 里的重名，只留下前一个"
                )
                continue
            seen[tool.name] = server.name
            loaded.tools.append(tool)
    return loaded


async def _one(server: McpServer, timeout: int) -> ServerReport:
    adapter = adapter_for(server, timeout=timeout)
    tools = await adapter.list_tools()
    prefix = _prefix(server.name)
    named: list[Any] = []
    for tool in tools:
        named.append(tool.model_copy(update={"name": f"{prefix}_{tool.name}"}))
    return ServerReport(server=server, tools=named)


def adapter_for(server: McpServer, *, timeout: int = 120) -> MCPAdapter:
    """给一个服务造 adapter。beta 警告的压制收在这一处。

    `suppress_langchain_beta_warning` 是官方给的口子（`langchain.mcp` 自己的
    docstring 里点名了它）—— 普通的 `warnings.catch_warnings()` 压不住它，
    那条警告是故意绕开常规过滤器发的。
    """
    from fastmcp import Client
    from langchain_core._api import suppress_langchain_beta_warning

    with suppress_langchain_beta_warning():
        from langchain.mcp import MCPAdapter as cls

    client = Client(target_for(server), name=server.name, timeout=timeout)
    return cls(client)


def _explain(exc: BaseException) -> str:
    """把连接失败翻译成人能看懂的一句话。"""
    detail = str(exc).strip() or type(exc).__name__
    if isinstance(exc, ExceptionGroup):
        detail = "；".join(_explain(inner) for inner in exc.exceptions)
        return detail
    if isinstance(exc, asyncio.TimeoutError):
        return f"连接超时（{detail}）"
    if isinstance(exc, (ConnectionError, OSError)):
        return f"连不上：{detail}"
    return f"{type(exc).__name__}: {detail}"
