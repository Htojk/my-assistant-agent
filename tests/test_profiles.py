"""装配层：图和工具对不对。

刻意不连真模型 —— 这里要验的是"装配正确"，不是"模型写得好不好"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from maa import profiles, skills, subagents
from maa.config import Config, ConfigError, ModelsConfig, ProviderConfig


class ScriptedChatModel(BaseChatModel):
    """按剧本回话的假模型：第 n 次生成返回 `replies[n]`。

    最后一个回复会一直重复，所以剧本写短一点也不会崩。
    """

    replies: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "maa-scripted"

    def _generate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        answered = sum(1 for message in messages if isinstance(message, AIMessage))
        reply = self.replies[min(answered, len(self.replies) - 1)]
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self


def _write_call(path: str, content: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": path, "content": content},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )


def test_闭环_agent_真的把文件写到磁盘上(workspace: Path) -> None:
    """C3 的验收条件：`maa run "在 out/ 下写一篇文章"` 得真出文件。"""
    model = ScriptedChatModel(
        replies=[
            _write_call("/out/article.md", "# 标题\n\n正文第一段。\n"),
            AIMessage(content="写好了，在 out/article.md。"),
        ]
    )
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=model)

    built.graph.invoke(
        {"messages": [HumanMessage("在 out/ 下写一篇 800 字的文章")]},
        config={"configurable": {"thread_id": "t1"}},
    )

    written = workspace / "out" / "article.md"
    assert written.exists()
    assert "正文第一段" in written.read_text(encoding="utf-8")


def test_内部数据不落在工作目录里(workspace: Path) -> None:
    """大工具结果 / 对话历史该进 StateBackend，不该混进用户项目。"""
    model = ScriptedChatModel(replies=[_write_call("/a.md", "x"), AIMessage(content="好了")])
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=model)

    built.graph.invoke({"messages": [HumanMessage("写个文件")]})

    leftovers = {p.name for p in workspace.iterdir()}
    assert "large_tool_results" not in leftovers
    assert "conversation_history" not in leftovers


def test_general_带_write_todos(workspace: Path) -> None:
    """`TodoListMiddleware` 不传就静默消失 —— 没有报错，工具直接不存在。"""
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=_stub_model())

    assert "write_todos" in profiles.tool_names(built)


def test_general_给全量文件工具(workspace: Path) -> None:
    names = profiles.tool_names(
        profiles.build_agent(Config(), "general", workspace=workspace, model=_stub_model())
    )

    assert {"read_file", "write_file", "edit_file", "ls", "glob", "grep", "execute"} <= set(names)


def test_readonly_模式下写操作被拒(workspace: Path) -> None:
    """权限规则真的拦得住写 —— 不然 readonly 只是个说法。"""
    from maa.config import BackendConfig

    cfg = Config(backend=BackendConfig(mode="readonly"))
    model = ScriptedChatModel(replies=[_write_call("/nope.md", "x"), AIMessage(content="试过了")])
    built = profiles.build_agent(cfg, "general", workspace=workspace, model=model)

    built.graph.invoke({"messages": [HumanMessage("写个文件")]})

    assert not (workspace / "nope.md").exists()


def test_profile_名字错了给出可选清单(workspace: Path) -> None:
    with pytest.raises(ConfigError, match="general"):
        profiles.build_agent(Config(), "nope", workspace=workspace, model=_stub_model())


def test_模型字符串不能用时给出人话(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="模型起不来"):
        profiles.build_model(Config())


def _stub_model() -> ScriptedChatModel:
    return ScriptedChatModel(replies=[AIMessage(content="嗯")])


# --------------------------------------------------------------------------- C6：四种 profile


@pytest.mark.parametrize("name", sorted(profiles.REGISTRY))
def test_四种_profile_都建得出来(name: str, workspace: Path) -> None:
    """验收的前半条。挑不出名字来就说明 REGISTRY 里那条写坏了。"""
    built = profiles.build_agent(Config(), name, workspace=workspace, model=_stub_model())

    assert built.profile.name == name
    assert profiles.tool_names(built)


def test_planner_的工具表里有_write_todos(workspace: Path) -> None:
    """验收的后半条 —— `TodoListMiddleware` 漏挂是静默的，工具直接不存在。"""
    names = profiles.tool_names(
        profiles.build_agent(Config(), "planner", workspace=workspace, model=_stub_model())
    )

    assert "write_todos" in names


def test_writer_摸不到_shell(workspace: Path) -> None:
    """写文章不需要跑命令。没有 `execute` 就没有"这条提示词被曲解成执行脚本"这条路。"""
    names = profiles.tool_names(
        profiles.build_agent(Config(), "writer", workspace=workspace, model=_stub_model())
    )

    assert "execute" not in names
    assert "delete" not in names
    # 但该有的还得有，否则白名单就成了"什么都没给"
    assert {"read_file", "write_file", "edit_file", "grep"} <= set(names)


def test_planner_也没有_shell(workspace: Path) -> None:
    """规划要能读情况、能把方案写下来，但不该有副作用。"""
    names = profiles.tool_names(
        profiles.build_agent(Config(), "planner", workspace=workspace, model=_stub_model())
    )

    assert "execute" not in names
    assert {"read_file", "write_file"} <= set(names)


def test_coder_要得到_shell(workspace: Path) -> None:
    """coder 是唯一一个必须能跑构建和测试的 —— 这条反过来验证白名单没被串用。"""
    names = profiles.tool_names(
        profiles.build_agent(Config(), "coder", workspace=workspace, model=_stub_model())
    )

    assert "execute" in names


def test_白名单少了_read_file_当场报错() -> None:
    """中间件的硬要求。让它在我们这儿就炸，别等到装配到一半才炸。"""
    with pytest.raises(ValueError, match="read_file"):
        profiles.Profile(
            name="坏掉的",
            description="",
            system_prompt="",
            tools=("ls", "glob"),
        )


def test_readonly_下白名单不绕过权限(workspace: Path) -> None:
    """白名单和后端权限得配在同一套东西上。

    如果白名单自己重造一份不带 permissions 的中间件，readonly 就漏了 ——
    writer 会以为自己不能写，其实能写。
    """
    from maa.config import BackendConfig

    cfg = Config(backend=BackendConfig(mode="readonly"))
    model = ScriptedChatModel(replies=[_write_call("/漏了.md", "x"), AIMessage(content="试过了")])
    built = profiles.build_agent(cfg, "writer", workspace=workspace, model=model)

    built.graph.invoke({"messages": [HumanMessage("写个文件")]})

    assert not (workspace / "漏了.md").exists()


def test_planner_真能把计划写到磁盘上(workspace: Path) -> None:
    """planner 的白名单是四个人里最窄的。

    窄到把 `write_file` 也一起砍掉就完蛋了 —— 它的全部产出就是那个文件。这条锁住它。
    """
    model = ScriptedChatModel(
        replies=[_write_call("/plan.md", "1. 摸情况\n2. 出方案\n"), AIMessage(content="好了")]
    )
    built = profiles.build_agent(Config(), "planner", workspace=workspace, model=model)

    built.graph.invoke(
        {"messages": [HumanMessage("帮我规划一下")]},
        config={"configurable": {"thread_id": "planner-1"}},
    )

    assert (workspace / "plan.md").read_text(encoding="utf-8").startswith("1.")


# --------------------------------------------------------------------------- 子代理


def test_general_也有_task(workspace: Path) -> None:
    """内置的 `general-purpose` 子代理是 SDK 自动加的，不用我们自己挂。

    实测出来的：哪怕 `subagents=None`，`task` 照样在工具表里。想不给都难 ——
    SDK 只在"调用方已经给了同名规格"或"harness profile 明令禁用"时才不加。
    """
    names = profiles.tool_names(
        profiles.build_agent(Config(), "general", workspace=workspace, model=_stub_model())
    )

    assert "task" in names


def test_各_profile_挂的子代理对得上(workspace: Path) -> None:
    expected = {
        "general": (),
        "writer": ("outliner",),
        "coder": ("code-reviewer",),
        "planner": ("researcher",),
    }

    for name, want in expected.items():
        built = profiles.build_agent(Config(), name, workspace=workspace, model=_stub_model())
        assert built.subagents == want, name


def test_子代理真的挂到图上了(workspace: Path) -> None:
    """Banner 里那个 `built.subagents` 是我们自己记的账，不算数。

    从**建好的图**里能摸到的证据只有 `task` 工具的描述 —— 它列出的就是模型能派的
    对象。规格造对了但没挂上去，只有这条能发现。
    """
    expected = {
        "general": {"general-purpose"},
        "writer": {"general-purpose", "outliner"},
        "coder": {"general-purpose", "code-reviewer"},
        "planner": {"general-purpose", "researcher"},
    }

    for name, want in expected.items():
        built = profiles.build_agent(Config(), name, workspace=workspace, model=_stub_model())
        assert _advertised_subagents(built) == want, name


def _advertised_subagents(built: profiles.BuiltAgent) -> set[str]:
    """`task` 描述里列的 agent 类型。

    只看"行首是 `- 名字:`"的那些 —— 描述里还有一堆普通的项目符号说明，
    不筛的话它们会被当成分支名混进来。
    """
    node = built.graph.nodes["tools"]
    bound = getattr(node, "bound", None) or node
    description = bound._tools_by_name["task"].description

    found: set[str] = set()
    for line in description.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        head = line.removeprefix("- ").split(":", 1)[0].strip()
        if head and " " not in head:  # 普通说明句的第一个词后面一定还有词
            found.add(head)
    return found


@pytest.mark.parametrize("name", sorted(subagents.REGISTRY))
def test_子代理规格能变成_sdk_要的_dict(name: str) -> None:
    spec = subagents.build(subagents.get(name), backend=object(), model=_stub_model())

    assert spec["name"] == name
    assert spec["description"] and spec["system_prompt"]
    # 不设 tools 才会继承主 agent 的工具（MCP 工具走的就是这条路）
    assert "tools" not in spec
    assert isinstance(spec["middleware"], list)


def test_审查子代理改不了文件() -> None:
    """`code-reviewer` 的价值全在"它只能读" —— 能写的话它就自己去改了。"""
    spec = subagents.build(subagents.CODE_REVIEWER, backend=object())

    frozen = [tool.name for tool in spec["middleware"][0].tools]

    assert "write_file" not in frozen
    assert "edit_file" not in frozen
    assert "execute" not in frozen
    assert "read_file" in frozen


def test_子代理名字错了给出可选清单() -> None:
    with pytest.raises(ConfigError, match="researcher"):
        subagents.get("code_reviewer")  # 下划线不是连字符 —— 最常见的拼法错误


def test_子代理拿到的是主_agent_那一套后端和权限(workspace: Path, monkeypatch) -> None:
    """子代理得跟主 agent 走同一个沙箱。

    不然 readonly 模式下的子代理会从硬盘另一头把文件写进去 —— 主 agent 被拦住了，
    它派出去的手下没被拦住，这种漏洞比没做权限还糟（看起来是安全的）。
    """
    from maa.config import BackendConfig

    seen: dict[str, Any] = {}
    real = subagents.build_all

    def spy(names: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs)
        return real(names, **kwargs)

    monkeypatch.setattr(profiles.subagents, "build_all", spy)
    cfg = Config(backend=BackendConfig(mode="readonly"))
    profiles.build_agent(cfg, "coder", workspace=workspace, model=_stub_model())

    assert seen["backend"] is not None
    assert any(
        permission.mode == "deny" and "write" in permission.operations
        for permission in seen["permissions"]
    )


# --------------------------------------------------------------------------- 看家技能


def test_看家技能指向真实路径(project: Path, workspace: Path) -> None:
    """提示里给的是**虚拟**路径 —— 后端路由挂上了才读得到。"""
    built = profiles.build_agent(Config(), "writer", workspace=workspace, model=_stub_model())
    hint = profiles._skill_hint(built.profile, skills.scan(built.skill_sources))

    assert "article-writing" in hint
    assert "/skills/" in hint


def test_技能没装就静默跳过(workspace: Path) -> None:
    """用户可能压根没装那个技能。提示里留个空段落比报错好。"""
    profile = profiles.Profile(
        name="x", description="", system_prompt="", preferred_skills=("不存在的技能",)
    )

    assert profiles._skill_hint(profile, skills.ScanResult()) == ""


def test_没点技能就不加那一段(workspace: Path) -> None:
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=_stub_model())

    assert profiles._skill_hint(built.profile, skills.scan(built.skill_sources)) == ""


# ------------------------------------------------------------------ 模型怎么建、名字怎么来


def _keyed_cfg(**provider: object) -> Config:
    """一份"密钥写在环境变量名里"的配置 —— 也就是 README 让用户写的那种。"""
    return Config(
        models=ModelsConfig(
            default="openai:gpt-5.5",
            providers={"openai": ProviderConfig(**provider)},  # type: ignore[arg-type]
        )
    )


def test_provider_的接入参数真的传下去了(monkeypatch: pytest.MonkeyPatch) -> None:
    """config 里写了 base_url / api_key 却没传到模型实例上，等于没配 —— 而且报错会
    晚到第一次请求，那时人早就不看启动了。

    读 SDK 内部字段（`openai_api_base` 之类）是刻意的：这里要盯的正是"传下去了没有"。
    """
    monkeypatch.setenv("MAA_测_URL", "https://自建/v1")
    monkeypatch.setenv("MAA_测_KEY", "sk-测")

    model = profiles.build_model(
        _keyed_cfg(base_url_env="MAA_测_URL", api_key_env="MAA_测_KEY", use_responses_api=False)
    )

    assert str(model.openai_api_base) == "https://自建/v1"  # type: ignore[attr-defined]
    assert model.openai_api_key.get_secret_value() == "sk-测"  # type: ignore[attr-defined]
    assert model.use_responses_api is False  # type: ignore[attr-defined]


def test_没写_use_responses_api_就别替用户做主(monkeypatch: pytest.MonkeyPatch) -> None:
    """自建端点多半没有 /responses，官方 OpenAI 又想走 —— 用户没表态时不能替他关。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    model = profiles.build_model(_keyed_cfg(api_key_env="OPENAI_API_KEY"))

    # 不给就是我们不管，交给 SDK 自己的默认值
    assert model.use_responses_api is None  # type: ignore[attr-defined]


def test_resolve_model_没给模型就用配置里的(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--model` 没写时走 `[models].default` —— 顺便把 spec 带回去给 banner 显示，
    不然人看见的模型名和实际用的可能不是一回事。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    model, spec = profiles.resolve_model(_keyed_cfg(api_key_env="OPENAI_API_KEY"))

    assert spec == "openai:gpt-5.5"
    assert not isinstance(model, ScriptedChatModel)


def test_names_列出所有_profile() -> None:
    assert "general" in profiles.names()
    assert profiles.names() == sorted(profiles.names())


def test_tool_names_挖不出来也不炸() -> None:
    """它是从建好的图里翻 langgraph 内部结构找工具名的（doctor 要用），换版本就可能
    翻空 —— 自检自己崩掉比显示不全难看得多，所以约定失败给空表。"""
    fake = type("FakeBuilt", (), {"graph": None})()

    assert profiles.tool_names(fake) == []  # type: ignore[arg-type]
