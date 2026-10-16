"""子代理规格：主 agent 用 `task` 工具把活儿派下去。

子代理不是"小一号的 agent"，是**上下文隔离**：派出去的活儿在自己的上下文里跑完，
只把结论交回来，中间读过的几十个文件不进主对话的窗口。这就是它存在的唯一理由 ——
如果一件事主 agent 顺手就能做完（读一两个文件、答一句话），派子代理只会更慢更贵。

代价是它**看不到主对话里刚说过的话**。所以 `description` 是它唯一的招牌：模型
只凭这一句判断"这活儿该不该派出去"，写含糊了等于没有这个子代理。

文件工具白名单靠替换子代理自己的 `FilesystemMiddleware` 实现，见 `_filesystem()`。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware

from .config import ConfigError

READ_ONLY_TOOLS: tuple[str, ...] = ("read_file", "ls", "glob", "grep")
"""只读的那几件。

`read_file` 必须在列 —— 中间件对白名单的第一件事就是检查它在不在，少一个直接
`ValueError: read_file must be included in tools`。这不是我们的规矩，是它的。
"""

WRITE_TOOLS: tuple[str, ...] = (*READ_ONLY_TOOLS, "write_file")
"""只读 + 落盘。

子代理能把结论写成文件，主 agent 再 `read_file` 取回来 —— 这就是"把大段中间过程
挡在自己上下文之外"的具体做法。给到 `edit_file` 就够了，不给 `delete`：子代理
没有理由删东西。
"""


@dataclass(frozen=True)
class SubagentSpec:
    """一个子代理的静态定义。运行时才补 backend / model。"""

    name: str
    description: str
    """什么时候该派它。模型只看得到这一句，所以要写"什么活儿"而不是"它是什么"。"""

    system_prompt: str
    tools: tuple[str, ...] = READ_ONLY_TOOLS
    """它的文件工具白名单。`read_file` 必须在里面。"""


RESEARCHER = SubagentSpec(
    name="researcher",
    description=(
        "资料调研：需要翻一批文件、查技能库、调用外部检索工具才能答上一个问题时派它。"
        "它把结论连同出处写成一份笔记交回来。顺手读一两个文件就能答的，别派 —— "
        "它看不到你手上已有的上下文，你得把背景在任务里重新讲一遍。"
    ),
    system_prompt="""\
你是资料调研员。派给你的问题通常一句话就能说完，但它需要读一堆东西才能答。

做法：
- 先把问题拆成几个具体的小问题，再逐个去找证据，别一上来就通读。
- 技能库里如果有相关的技能，先读它（`read_file` 读 SKILL.md 全文，别只读前 100 行）。
- 有检索类的工具就用，没有就靠 `grep` / `glob` 在文件里找。
- 找到什么、没找到什么，都要说。**没找到证据就说没找到，不要用常识补上看起来合理
  的答案** —— 派你来就是为了要一个有出处的结论，编一个和直接猜没有区别。
- 结论写成一份笔记落到文件里（工作目录下的 `notes/` 里，文件名点明主题），
  然后在回复里给出：文件路径 + 三五句结论 + 关键出处的文件路径和行号。

写作规范：中文，短句，不要复述过程，只给结论和依据。
""",
    tools=WRITE_TOOLS,
)

CODE_REVIEWER = SubagentSpec(
    name="code-reviewer",
    description=(
        "只读代码审查：想让别人挑挑刚写完的代码的毛病、或者动手改一块陌生代码前先摸清"
        "底细时派它。它**改不了任何文件**，所以交回来的是一份意见清单，不是补丁 —— "
        "要不要采纳由你决定。"
    ),
    system_prompt="""\
你是代码审查员。你**只能读，不能改** —— 没有给你写文件的能力，这是刻意的：
审查和修改分开，改坏了责任才清楚。

做法：
- 先把代码读完整。只看了片段就下结论是最常见的错。
- 按严重程度排，先说会出错的，再说不好看的。风格问题放最后，并且标明"无关紧要"。
- 每条意见写清楚三件事：在哪（文件路径 + 行号）、为什么会出问题（什么输入下会错）、
  怎么改。只有"这里不太好"的意见等于没提。
- **没发现问题就直说没发现**。为了显得有价值而硬凑意见，比什么都不说更糟。
- 不确定的地方标成"疑似"，并说明需要什么才能确认。

不要输出完整的重写版本；只写该改哪几处、改成什么样。
""",
    tools=READ_ONLY_TOOLS,
)

OUTLINER = SubagentSpec(
    name="outliner",
    description=(
        "列文章大纲：要写长文（八百字以上、有分节的那种）之前，先派它把结构和每节要点"
        "定下来。它只定骨架不写正文，正文还是你来写 —— 所以它的产出是拿来当图纸用的。"
    ),
    system_prompt="""\
你是文章结构设计师。派给你的一般是一个题目和一份背景材料，要的是一份能照着写的
大纲。

做法：
- 先定**这篇东西是给谁看的、看完要记住哪一句话**。这两条定不下来，结构怎么排都是错的。
- 大纲列到"每节写什么"为止，不写正文。每节给：小节标题、这一节要讲的要点（两三条）、
  以及它为什么必须排在这个位置。
- 开头的吸引力、结尾的落点、段落之间的接续，各想清楚再说。
- 客户有字数要求就按字数分配各节的篇幅，标在每节后面。
- 拿不准的地方在结构上标出来（比如"这里的例子需要确认"），不要自己编例子填进去。

大纲写成文件（`outline.md`），然后在回复里给路径 + 三五句话说明整体思路。
写作规范：中文。
""",
    tools=WRITE_TOOLS,
)


REGISTRY: dict[str, SubagentSpec] = {
    spec.name: spec for spec in (RESEARCHER, CODE_REVIEWER, OUTLINER)
}
"""现有子代理。新增一个应该是往这里加一条，不是改 `build()`。"""


def get(name: str) -> SubagentSpec:
    """按名字取，名字错了给出可选清单而不是 KeyError。"""
    try:
        return REGISTRY[name]
    except KeyError:
        known = "、".join(sorted(REGISTRY))
        raise ConfigError(f"没有这个子代理：{name}。可选：{known}") from None


def build(
    spec: SubagentSpec,
    *,
    backend: Any,
    permissions: Sequence[Any] = (),
    model: Any | None = None,
) -> dict[str, Any]:
    """把静态定义补成 `create_deep_agent(subagents=[...])` 要的 dict。

    刻意**不设** `tools` 那一栏：不设才会继承主 agent 的工具（MCP 工具走的就是
    这条路）。设了 `tools=[]` 反而把继承切断了 —— 它表示"这个子代理不要额外工具"，
    不是"什么工具都不给"。

    `backend` 和 `permissions` 必须由调用方传进来，不能在这里自己造：子代理得跟主
    agent 走同一个沙箱，否则 readonly 模式下的子代理会从硬盘另一头把文件写进去。
    """
    entry: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": spec.system_prompt,
        # spec 顶层的 permissions 是公开参数（graph.py 走 spec.get("permissions")），
        # 不像主 agent 那样得去够 FilesystemMiddleware 的私有 `_permissions`。
        "permissions": list(permissions),
        # 名字跟 deepagents 自己搭的那层一样，于是这一条在它的中间件栈里原地顶掉
        # 原来的全量 FilesystemMiddleware —— 白名单就是这么生效的。
        "middleware": [FilesystemMiddleware(backend=backend, tools=list(spec.tools))],
    }
    if model is not None:
        entry["model"] = model
    return entry


def build_all(
    names: Iterable[str],
    *,
    backend: Any,
    permissions: Sequence[Any] = (),
    model: Any | None = None,
) -> list[dict[str, Any]]:
    """按 profile 声明的名字一次造好。"""
    return [
        build(get(name), backend=backend, permissions=permissions, model=model) for name in names
    ]
