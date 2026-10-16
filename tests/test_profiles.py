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

from maa import profiles
from maa.config import Config, ConfigError


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
        profiles.build_agent(Config(), "writer", workspace=workspace, model=_stub_model())


def test_模型字符串不能用时给出人话(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="模型起不来"):
        profiles.build_model(Config())


def _stub_model() -> ScriptedChatModel:
    return ScriptedChatModel(replies=[AIMessage(content="嗯")])
