"""危险工具跑之前先问一句。

`permissions=` 管不住 `execute` —— 文件权限规则管的是文件工具，shell 是后端的
事（见 `backends.build_permissions` 的说明）。所以本地模式下的安全边界只能是图上
的 `interrupt_on`：它在**工具真正执行之前**把图停住，等人点头。停住这件事本身要靠
checkpointer（C7 那个会话库），所以没有会话库就没有审批。

三个默认，取的都是"安全的那个答案"：

- **回车 = 不批准。** manual 模式下没有明确的"可以"就不该动手。
- **问不出来就不批准。** 输入到头了（`maa run` 接到空管道之类），安全的答案是"不"。
- **Ctrl-C 也算拒绝。** 而不是把图停在半路 —— 停在半路的会话下次 `--resume`
  接不上，而"我改主意了"和"这个命令有问题"对模型来说是同一件事。

审批的粒度是**一次调用**。"这个工具后面都批准"（`a`）只记在这次会话里，不落盘：
这次算了和以后都行不是一回事。

readonly 模式下写操作本来就被 `permissions` 拒了，这里还会再问一次 —— 是有点多余。
但两条路各自独立，比互相猜对方在干什么强；readonly 想省这一步，把
`approval.mode` 设成 `yolo` 就行（那边没有 `execute`，写操作照样被拒，放不出事）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import ui
from .config import DEFAULT_GATED_TOOLS, ApprovalConfig

INTERRUPT_KEY = "__interrupt__"
"""中断在 `stream_mode="updates"` 里占的那一格。"""

APPROVE = "approve"
REJECT = "reject"
"""中间件认的两个动作类型。`edit` / `respond` 我们先不做 —— 见模块开头的取舍。"""

PROMPT = "批准？[y] 是 / [a] {name} 后面都不问 / 回车或不批准的理由："

EXHAUSTED = "输入到头了 —— 剩下的都不问自拒（要不问就批，把 approval.mode 设成 yolo）"

NOBODY = "没人能批准（审批提示拿不到回答）"


def gated(cfg: ApprovalConfig) -> dict[str, Any]:
    """配置 → `interrupt_on`。

    `True` 表示这个工具四种决定都允许（中间件自己的语义），我们只用 y / n 两种。

    yolo 给空表 —— `create_deep_agent` 收到空字典当 `None`，连中间件都不挂：
    不问了就是真的不问了，不是问了以后自动放行。
    """
    if cfg.mode != "manual":
        return {}
    return dict.fromkeys(cfg.gated_tools, True)


def describe(cfg: ApprovalConfig) -> str:
    """banner / `maa doctor` 里那一行：审批是开着还是关着。"""
    if cfg.mode != "manual":
        return "yolo · 危险工具不问就执行"
    if not cfg.gated_tools:
        return "manual · 但要审批的工具是空的（等于不问）"
    return f"manual · 要审批：{'、'.join(cfg.gated_tools)}"


def ineffective(cfg: ApprovalConfig, available: Iterable[str]) -> list[str]:
    """名单里**配了却拦不住**、而且看着像打错的名字。

    打错一个工具名不会报错 —— 中间件见了不认识的名字就跳过，于是"我以为 `exec` 被
    拦住了"这种事可以一直没人知道。这个函数就是把它捞出来。

    判据是**既不在这次的工具表里、也不在内置名单里**，第二个条件不能省：
    writer 故意没有 `execute`、planner 连 `delete` 都没有，而默认名单照样写着它们。
    只看"在不在工具表里"的话，每次跑 writer / planner 都会刷一行假警告 ——
    狼喊多了，真有狼那行也没人看。

    所以这里报的是"这个名字我们不认识"，不是"这个工具这次没有"。MCP 工具就在
    `available` 里，给它们配审批不会误报；反过来服务没连上时会报一条 —— 那是真话。

    `yolo` 直接给空表：那时候名单本来就不参与任何判断。
    """
    if cfg.mode != "manual":
        return []
    have = set(available)
    known = set(DEFAULT_GATED_TOOLS)
    return [name for name in cfg.gated_tools if name not in have and name not in known]


def pending(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """从一条 stream update 里捞出待审批的请求。

    中断是 `updates` 里的 `__interrupt__` 一格，值是个元组（一次可能停好几处）。
    批处理模式下每个模型回合最多一个，但按列表收着，将来换 `per_call` 不用改调用方。
    """
    return [
        item.value for item in payload.get(INTERRUPT_KEY, ()) or () if getattr(item, "value", None)
    ]


@dataclass
class Approver:
    """一次会话里的审批人。

    `allow` 是"这个工具后面别再问了"的名单：给 coder profile 一条一条批准每句
    `execute` 是没法用的。它只活在这次会话里 —— 不落盘，理由见模块开头。

    `ask` 只为测试留的。不给就是 `input`，也就是真问人。
    """

    ask: Callable[[str], str] | None = None
    allow: set[str] = field(default_factory=set)
    closed: bool = False
    """输入没了或者被打断过 —— 后面的动作直接拒，不再问。"""

    def decisions(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        """按**位置**给每个动作一个决定 —— 中间件就是这么跟请求对上的。

        所以长度必须跟请求一样，中途 Ctrl-C 也得把剩下的补成拒绝。
        """
        actions = list(request.get("action_requests", []))
        decisions: list[dict[str, Any]] = []
        for action in actions:
            try:
                decisions.append(self.decide(action))
            except KeyboardInterrupt:
                ui.newline()
                ui.warn(f"中断 = 不批准（{action.get('name', '?')} 没执行）")
                self.closed = True
                decisions.append({"type": REJECT, "message": "用户中断了审批"})
        return decisions

    def decide(self, action: Mapping[str, Any]) -> dict[str, Any]:
        """问一个动作要一个决定。"""
        name = str(action.get("name", "?"))
        args = action.get("args") or {}

        if name in self.allow:
            ui.approval(name, arguments(args), note=f"这次会话已经批过 {name}，不问了")
            return {"type": APPROVE}
        if self.closed:
            return {"type": REJECT, "message": f"{NOBODY}，{name} 没执行"}

        ui.approval(name, arguments(args))
        answer = self._read(PROMPT.format(name=name))
        if answer is None:
            self.closed = True
            ui.warn(EXHAUSTED)
            return {"type": REJECT, "message": f"{NOBODY}，{name} 没执行"}

        text = answer.strip()
        low = text.lower()
        if low in {"y", "yes", "是"}:
            return {"type": APPROVE}
        if low in {"a", "all", "always", "都"}:
            self.allow.add(name)
            ui.hint(f"{name} 这次会话里不再问了")
            return {"type": APPROVE}
        if not text or low in {"n", "no", "否"}:
            return {"type": REJECT, "message": f"用户没有批准执行 {name}"}
        # 随手写的一句话当理由转给模型：它比一个光秃秃的"拒绝了"有用得多。
        return {"type": REJECT, "message": f"用户没有批准执行 {name}。用户说：{text}"}

    def _read(self, prompt: str) -> str | None:
        """读一行；输入到头了返回 None（调用方按拒绝处理）。"""
        reader = self.ask or input
        try:
            return reader(prompt)
        except EOFError:
            return None


def arguments(
    args: Mapping[str, Any], *, lines: int = 8, chars: int = 600
) -> list[tuple[str, str]]:
    """把参数拍成"键 → 值"的列表，给 `ui.approval` 渲染。

    值长了要截：一个 `write_file` 的 content 可能是几万字的稿子，全打出来
    审批提示本身就成了一屏噪音。截了会说清截掉多少。
    """
    out: list[tuple[str, str]] = []
    for key, value in (args or {}).items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        rows = text.splitlines() or [""]
        if len(rows) > lines:
            text = "\n".join(rows[:lines]) + f"\n…（还有 {len(rows) - lines} 行）"
        elif len(text) > chars:
            text = f"{text[:chars]}…（还有 {len(text) - chars} 字）"
        out.append((str(key), text))
    return out
