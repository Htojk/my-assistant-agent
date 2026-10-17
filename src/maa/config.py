"""配置的读写与校验。

事实来源是 `~/.maa/config.toml`（`MAA_HOME` 可重定位）。
优先级：内置默认值 < 配置文件 < 环境变量（`MAA_MODEL`）< CLI 参数。

这一层只处理数据，不导入 langchain —— 模型实例化在 `profiles.build_model`，
好让配置本身能被无依赖地单测。
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import paths


class ConfigError(RuntimeError):
    """配置不可用：文件坏了、值非法、引用的东西不存在。"""


def _env(name: str | None) -> str | None:
    """读环境变量，空白视为没设。"""
    if not name:
        return None
    return os.environ.get(name, "").strip() or None


class ProviderConfig(BaseModel):
    """一个模型 provider 的接入参数。

    既能直接写死值（`base_url` / `api_key`），也能只写环境变量名
    （`base_url_env` / `api_key_env`）—— 后者优先从环境读，密钥不落配置文件。
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    base_url_env: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    use_responses_api: bool | None = None

    def resolve_base_url(self) -> str | None:
        return self.base_url or _env(self.base_url_env)

    def resolve_api_key(self) -> str | None:
        return self.api_key or _env(self.api_key_env)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str = "openai:gpt-5.5"
    """`provider:model` 形式，可被 `--model` / `MAA_MODEL` 覆盖。"""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    def provider_for(self, spec: str) -> ProviderConfig | None:
        """按 `provider:model` 里的 provider 找接入参数。

        没写该 provider 的段落时回退到 `local` —— 那是给自建 OpenAI 兼容端点留的
        通配档位。
        """
        name = spec.split(":", 1)[0]
        return self.providers.get(name) or self.providers.get("local")


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local", "readonly", "remote"] = "local"
    workspace: str = "."
    timeout: int = Field(default=120, ge=1)
    """`execute` 的单条命令超时（秒）。"""


DEFAULT_GATED_TOOLS: tuple[str, ...] = ("write_file", "edit_file", "delete", "execute")
"""内置的审批名单。

是常量不只是为了少写一遍：`approval.ineffective()` 拿它区分**"名字打错了"**和
**"这个 profile 本来就没这个工具"** —— 后者是默认配置的常态（writer 没有
`execute`、planner 连 `delete` 都没有），不该报警。
"""


class ApprovalConfig(BaseModel):
    """审批策略：哪些工具执行前要先问人。

    接进图的地方在 `approval.gated()` —— 这里只管格式。
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["manual", "yolo"] = "manual"
    gated_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_GATED_TOOLS))


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_files: list[str] = Field(default_factory=lambda: [".mcp.json", "~/.maa/mcp.json"])
    """按顺序读，后面的覆盖前面的 —— 所以项目级放前面。"""

    tool_timeout: int = Field(default=120, ge=1)

    include: list[str] = Field(default_factory=list)
    """只留名字匹配这些模式的工具；空 = 都留。`*` 通配，匹配的是 `服务_工具` 全名。"""

    exclude: list[str] = Field(default_factory=list)
    """去掉名字匹配这些模式的工具。**排除赢**：两边都匹配时按去掉算。"""

    @property
    def filtering(self) -> bool:
        """配没配过滤 —— 用来决定"要不要提一句"。"""
        return bool(self.include or self.exclude)


class SkillsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extra_dirs: list[str] = Field(default_factory=list)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: ModelsConfig = Field(default_factory=ModelsConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)

    source: Path | None = Field(default=None, exclude=True)
    """这份配置从哪个文件读来的；None 表示全是内置默认值（还没 init）。"""


DEFAULT_CONFIG_TEMPLATE = """\
# my-assistant-agent 配置。全部删掉也能跑 —— 删掉的部分走内置默认值。
# 密钥只写环境变量名，不写明文。

[models]
# 可被 `--model` 或环境变量 MAA_MODEL 覆盖
default = "openai:gpt-5.5"

[models.providers.openai]
base_url_env = "OPENAI_BASE_URL"
api_key_env = "OPENAI_API_KEY"
# 自建 OpenAI 兼容端点通常没有 /responses，留 false；
# 用官方 OpenAI 且想走 Responses API 时改 true。
use_responses_api = false

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"

[backend]
mode = "local"      # local（可执行命令，无隔离）| readonly（全量拒写）| remote
workspace = "."     # 相对路径按项目根算
timeout = 120       # execute 单条命令超时（秒）

[approval]
mode = "manual"     # manual：危险工具先问 | yolo：不问
gated_tools = ["write_file", "edit_file", "delete", "execute"]

[mcp]
config_files = [".mcp.json", "~/.maa/mcp.json"]   # 后面的覆盖前面的
tool_timeout = 120
# 工具过滤：`*` 通配，匹配的是「服务名_工具名」全名。服务一多，几十个工具会全进
# 系统提示 —— 这里按名字筛一遍。include 空着表示都留，exclude 跟它冲突时以 exclude 为准。
include = []        # 例：["demo_*"] —— 只留 demo 这个服务的工具
exclude = []        # 例：["*_delete", "*_write"]

[skills]
extra_dirs = []
"""


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 TOML：{path}\n{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"配置文件读不了：{path}\n{exc}") from exc


def load_config(path: Path | None = None, *, env: Mapping[str, str] | None = None) -> Config:
    """读配置。`path=None` 走默认位置，文件不存在就用全默认值。

    显式指定的路径不存在会报错 —— 那是用户打错了，不该被静默忽略。
    """
    env = os.environ if env is None else env
    target = path or paths.config_path()

    if path is not None and not target.exists():
        raise ConfigError(f"配置文件不存在：{target}")

    data = _read_toml(target) if target.exists() else {}
    try:
        cfg = Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"配置文件有问题：{target}\n{exc}") from exc

    cfg.source = target if target.exists() else None

    if override := env.get("MAA_MODEL", "").strip():
        cfg.models.default = override
    return cfg


def render_config(cfg: Config) -> str:
    """把生效中的配置吐成 TOML（`maa config show` 用）。

    `exclude_none` 不是可选的美化 —— TOML 没有 null，`None` 会让序列化直接抛错。
    """
    import tomli_w

    return tomli_w.dumps(cfg.model_dump(mode="json", exclude_none=True))


def write_config_template(path: Path | None = None, *, force: bool = False) -> Path:
    """写出带注释的默认配置。已存在且没给 `force` 就原样返回，不覆盖。"""
    target = path or paths.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return target
    target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return target
