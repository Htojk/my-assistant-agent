"""技能库：目录发现、优先级去重、以及 agent 是不是真读得到。

`paths.home_dir()` 每个用例都会被换掉 —— 不换的话测试会去扫开发机真实的
`~/.claude/skills`，结果随机器而变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from maa import paths, profiles, skills
from maa.config import Config, SkillsConfig


def _write_skill(
    root: Path, name: str, description: str, body: str = "", *, dirname: str = ""
) -> Path:
    """造一个技能目录。`dirname` 跟 `name` 不同时就是在造一个坏技能。"""
    directory = root / (dirname or name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / skills.SKILL_FILENAME).write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """假的用户主目录 —— `~/.claude/skills` 之类都落在这儿。"""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(paths, "home_dir", lambda: fake)
    return fake


@pytest.fixture
def builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """替掉仓库自带的内置技能目录。"""
    root = tmp_path / "builtin"
    _write_skill(root, "demo", "演示技能", "关键暗号：紫色河马。")
    monkeypatch.setattr(skills, "builtin_dir", lambda: root)
    return root


def test_不存在的目录不进清单(home: Path, builtin: Path, tmp_path: Path) -> None:
    """挂上不存在的目录只会让中间件每次启动报一条加载失败。"""
    sources = skills.discover(Config(), project=tmp_path)

    assert [source.path for source in sources] == [builtin.resolve()]


def test_优先级由低到高(home: Path, builtin: Path, project: Path) -> None:
    """内置垫底，然后是用户级、项目级 —— 中间件靠这个顺序定同名技能谁赢。"""
    for path in (
        home / ".maa" / "skills",
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        project / ".maa" / "skills",
        project / ".agents" / "skills",
    ):
        path.mkdir(parents=True)

    sources = skills.discover(Config(), project=project)

    assert [source.label for source in sources] == [
        "内置",
        "用户",
        "用户 Claude",
        "用户 Agents",
        "项目",
        "项目 Agents",
    ]
    assert len({source.route for source in sources}) == len(sources), "虚拟前缀撞车了"


def test_extra_dirs_排在最前面(maa_home: Path, home: Path, builtin: Path, project: Path) -> None:
    """配置里指定的技能目录优先级最高 —— 用户显式点名的，就该盖过其他一切。"""
    extra = project / "my-skills"
    extra.mkdir()
    cfg = Config(skills=SkillsConfig(extra_dirs=[str(extra)]))

    sources = skills.discover(cfg, project=project)

    assert sources[-1].path == extra.resolve()


def test_同一个目录只出现一次(maa_home: Path, home: Path, builtin: Path, project: Path) -> None:
    """extra_dirs 里重复填一个已有目录，不该把同名技能自己盖掉自己。"""
    claude = home / ".claude" / "skills"
    claude.mkdir(parents=True)
    cfg = Config(skills=SkillsConfig(extra_dirs=[str(claude)]))

    sources = skills.discover(cfg, project=project)

    assert [source.path for source in sources].count(claude.resolve()) == 1


def test_技能源各自路由到自己的目录(
    maa_home: Path, home: Path, builtin: Path, project: Path
) -> None:
    (home / ".claude" / "skills").mkdir(parents=True)
    sources = skills.discover(Config(), project=project)

    table = skills.routes(sources)

    assert set(table) == {source.route for source in sources}
    assert all(source.route.startswith(skills.ROUTE_ROOT) for source in sources)


def test_同名技能高的赢(home: Path, builtin: Path, project: Path) -> None:
    _write_skill(home / ".maa" / "skills", "demo", "用户自己的版本")
    sources = skills.discover(Config(), project=project)

    result = skills.scan(sources)

    assert [skill.name for skill in result.skills] == ["demo"]
    assert result.skills[0].source.label == "用户"
    assert any("覆盖" in problem for problem in result.problems), "被盖掉的要留个痕"


def test_name_跟目录名不一致要说出来(home: Path, builtin: Path, project: Path) -> None:
    """中间件对这种情况是静默跳过的 —— 技能写着却永远不生效，最难查。"""
    _write_skill(home / ".maa" / "skills", "改过的名字", "描述", dirname="demo")
    sources = skills.discover(Config(), project=project)

    result = skills.scan(sources)

    assert [skill.name for skill in result.skills] == ["demo"]  # 只剩内置那个
    assert any("目录名不一致" in problem for problem in result.problems)


def test_缺_description_要说出来(home: Path, builtin: Path, project: Path) -> None:
    directory = home / ".maa" / "skills" / "bare"
    directory.mkdir(parents=True)
    (directory / skills.SKILL_FILENAME).write_text("---\nname: bare\n---\n正文\n", encoding="utf-8")
    sources = skills.discover(Config(), project=project)

    result = skills.scan(sources)

    assert "bare" not in [skill.name for skill in result.skills]
    assert any("description" in problem for problem in result.problems)


def test_内置技能齐三件() -> None:
    """仓库自带的三个技能得能过自己的解析 —— 自己写的东西先自己验一遍。"""
    builtin = skills.builtin_dir()
    assert builtin is not None

    result = skills.scan([skills.SkillSource(route="/skills/builtin/", label="内置", path=builtin)])

    assert [skill.name for skill in result.skills] == ["article-writing", "coding", "task-planning"]
    assert result.problems == []


def test_frontmatter_没有也能忍() -> None:
    assert skills.parse_frontmatter("# 就是一篇普通文档\n") == {}
    assert skills.parse_frontmatter("---\nname: x\n") == {}
    assert skills.parse_frontmatter("---\nname: x\n---\n正文") == {"name": "x"}


class RecordingChatModel(BaseChatModel):
    """按剧本回话，顺便把每次送进来的消息原样记下来。

    记下来是为了验系统提示里"有没有这些技能" —— 那是中间件的产物，
    不看一眼就只能靠猜。
    """

    replies: list[AIMessage]
    seen: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "maa-recording"

    def _generate(self, messages: list[Any], **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        answered = sum(1 for message in messages if isinstance(message, AIMessage))
        reply = self.replies[min(answered, len(self.replies) - 1)]
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> RecordingChatModel:
        return self


def _system_prompt(model: RecordingChatModel) -> str:
    first = model.seen[0]
    return "\n".join(str(getattr(message, "content", "")) for message in first)


def test_技能名和路径进了系统提示(
    maa_home: Path, home: Path, builtin: Path, workspace: Path
) -> None:
    """agent 得先"知道有这么个技能"，才谈得上用不用。"""
    model = RecordingChatModel(replies=[AIMessage(content="嗯")])
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=model)

    built.graph.invoke({"messages": [HumanMessage("随便聊聊")]})

    prompt = _system_prompt(model)

    assert "demo" in prompt
    assert "/skills/builtin/demo/SKILL.md" in prompt


def test_agent_用_read_file_读得到技能正文(
    maa_home: Path, home: Path, builtin: Path, workspace: Path
) -> None:
    """C4 的验收条件。

    技能目录在工作目录之外，走的是 CompositeBackend 的路由 —— 这条测试验的
    就是那条路真的通，而且是靠 `read_file`，不是靠什么 `load_skill` 工具。
    """
    model = RecordingChatModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/skills/builtin/demo/SKILL.md", "limit": 1000},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="读到了"),
        ]
    )
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=model)

    state = built.graph.invoke({"messages": [HumanMessage("看看 demo 技能")]})

    tool_outputs = [
        str(message.content) for message in state["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_outputs, "read_file 一个结果都没返回"
    assert any("紫色河马" in output for output in tool_outputs), tool_outputs


def test_没有_load_skill_工具(maa_home: Path, home: Path, builtin: Path, workspace: Path) -> None:
    """渐进披露靠的是 read_file —— 多一个专用工具是另一套设计了。"""
    built = profiles.build_agent(
        Config(),
        "general",
        workspace=workspace,
        model=RecordingChatModel(replies=[AIMessage(content="嗯")]),
    )

    assert "load_skill" not in profiles.tool_names(built)


def test_技能目录没被倒进工作目录(
    maa_home: Path, home: Path, builtin: Path, workspace: Path
) -> None:
    """路由是为了够得着，不是为了把技能搬过来。"""
    model = RecordingChatModel(replies=[AIMessage(content="嗯")])
    built = profiles.build_agent(Config(), "general", workspace=workspace, model=model)

    built.graph.invoke({"messages": [HumanMessage("随便聊聊")]})

    assert {path.name for path in workspace.iterdir()} == set()
