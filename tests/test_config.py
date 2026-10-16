"""配置读写与校验。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from maa import paths
from maa.config import (
    DEFAULT_CONFIG_TEMPLATE,
    Config,
    ConfigError,
    ModelsConfig,
    ProviderConfig,
    load_config,
    render_config,
    write_config_template,
)


def test_没有配置文件时用内置默认值(maa_home: Path) -> None:
    cfg = load_config()

    assert cfg.source is None
    assert cfg.models.default == "openai:gpt-5.5"
    assert cfg.backend.mode == "local"
    assert cfg.approval.mode == "manual"


def test_显式指定的路径不存在要报错(maa_home: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.toml")


def test_读配置文件(maa_home: Path) -> None:
    target = paths.config_path()
    target.parent.mkdir(parents=True)
    target.write_text(
        '[models]\ndefault = "anthropic:claude-sonnet-5"\n[backend]\nmode = "readonly"\n',
        encoding="utf-8",
    )

    cfg = load_config()

    assert cfg.source == target
    assert cfg.models.default == "anthropic:claude-sonnet-5"
    assert cfg.backend.mode == "readonly"
    # 没写的段落要落回默认值，而不是变成空
    assert cfg.mcp.tool_timeout == 120


def test_环境变量覆盖模型(maa_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAA_MODEL", "openai:gpt-5-mini")

    assert load_config().models.default == "openai:gpt-5-mini"


def test_坏掉的_toml_报错而不是崩栈(maa_home: Path) -> None:
    target = paths.config_path()
    target.parent.mkdir(parents=True)
    target.write_text("这不是 toml ===", encoding="utf-8")

    with pytest.raises(ConfigError, match="合法 TOML"):
        load_config()


def test_写错的键会被指出来(maa_home: Path) -> None:
    """`extra="forbid"` 的意义就在这 —— 拼错的键不该被静默忽略。"""
    target = paths.config_path()
    target.parent.mkdir(parents=True)
    target.write_text('[backend]\nmdoe = "local"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="配置文件有问题"):
        load_config()


def test_模板只多不少(maa_home: Path) -> None:
    """模板是手写的带注释文本，容易跟模型脱节 —— 这条测试盯着它们别分家。

    模板可以多出 `[models.providers.*]` 这类示例段落，但除了它以外，
    每个字段的取值都必须和内置默认值一致。
    """
    target = write_config_template()
    from_template = Config.model_validate(tomllib.loads(target.read_text(encoding="utf-8")))

    assert from_template.model_dump(exclude={"models": {"providers"}}) == Config().model_dump(
        exclude={"models": {"providers"}}
    )


def test_模板里的_provider_段落指向约定的环境变量(maa_home: Path) -> None:
    """模板注释和 README 承诺的是同一套变量名，不能各说各话。"""
    target = write_config_template()
    providers = load_config(target).models.providers

    assert providers["openai"].base_url_env == "OPENAI_BASE_URL"
    assert providers["openai"].api_key_env == "OPENAI_API_KEY"
    assert providers["openai"].use_responses_api is False
    assert providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"


def test_模板不覆盖已有文件(maa_home: Path) -> None:
    target = paths.config_path()
    target.parent.mkdir(parents=True)
    target.write_text('[models]\ndefault = "x:y"\n', encoding="utf-8")

    write_config_template()

    assert "x:y" in target.read_text(encoding="utf-8")


def test_show_出来的_toml_能读回去(maa_home: Path) -> None:
    """`config show` 的输出必须还能被当配置用 —— 否则它就是骗人的。

    空值一律不落盘（TOML 没有 null），所以比对要先把 None 摘掉。
    """
    cfg = load_config()
    dumped = render_config(cfg)

    assert tomllib.loads(dumped) == cfg.model_dump(mode="json", exclude_none=True)


def test_模板内容不为空() -> None:
    assert "[models]" in DEFAULT_CONFIG_TEMPLATE
    assert "MAA_HOME" not in DEFAULT_CONFIG_TEMPLATE


# ------------------------------------------------------------------ provider 接入参数


def test_读不了的配置文件也报错(maa_home: Path) -> None:
    """路径指到一个目录上时给的是"配置读不了"，不是一句 `IsADirectoryError` 栈 ——
    用户看一眼就知道是自己路径写错了，还是文件本身坏了。"""
    paths.config_path().mkdir(parents=True)

    with pytest.raises(ConfigError, match="读不了"):
        load_config()


def test_密钥环境变量是空白当没设(monkeypatch: pytest.MonkeyPatch) -> None:
    """`export OPENAI_API_KEY=` 之后不该被当成"给了一个空密钥" —— 那只会带着空
    Bearer 去撞 401，报错还看不懂。"""
    monkeypatch.setenv("MAA_临时_KEY", "   ")

    assert ProviderConfig(api_key_env="MAA_临时_KEY").resolve_api_key() is None


def test_环境变量里没这个名字就是没设(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAA_没人设过_KEY", raising=False)

    assert ProviderConfig(api_key_env="MAA_没人设过_KEY").resolve_api_key() is None


def test_写死的值盖过环境变量(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个都写了以配置文件为准：写死在文件里的那行是**这次**的选择，环境变量是
    这台机器上的兜底。反过来会让人明明改了配置却不生效。"""
    monkeypatch.setenv("MAA_临时_KEY", "环境里那个")
    provider = ProviderConfig(api_key="写死那个", api_key_env="MAA_临时_KEY")

    assert provider.resolve_api_key() == "写死那个"


def test_地址也走同一套规则(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAA_临时_URL", "http://环境里/v1")

    assert ProviderConfig(base_url_env="MAA_临时_URL").resolve_base_url() == "http://环境里/v1"
    assert ProviderConfig(base_url="http://写死/v1").resolve_base_url() == "http://写死/v1"
    assert ProviderConfig().resolve_base_url() is None


def test_没写_provider_段落就回退到_local() -> None:
    """`local` 是给自建 OpenAI 兼容端点留的通配档位：只写了地址、没挨个登记 provider
    名的人不该被卡住。"""
    models = ModelsConfig(providers={"local": ProviderConfig(base_url="http://自建/v1")})

    assert models.provider_for("openai:gpt-5.5").base_url == "http://自建/v1"


def test_登记过的_provider_优先于_local() -> None:
    """通配只是在**没写**的时候兜底，不能把写了的顶掉。"""
    models = ModelsConfig(
        providers={
            "local": ProviderConfig(base_url="http://自建/v1"),
            "openai": ProviderConfig(base_url="http://官方/v1"),
        }
    )

    assert models.provider_for("openai:gpt-5.5").base_url == "http://官方/v1"


def test_谁都对不上就是_None() -> None:
    """一个 provider 都没配时得能被 `maa doctor` 判成"缺配置"，而不是给个空壳对象。"""
    assert ModelsConfig().provider_for("openai:gpt-5.5") is None
