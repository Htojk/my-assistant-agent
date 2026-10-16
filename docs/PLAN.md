# my-assistant-agent 开发计划

> 版本：v2（2026-10-16）｜技术栈：`deepagents` + LangChain 1.4.x + LangGraph
> 形态：CLI（`maa`）｜仓库：https://github.com/Htojk/my-assistant-agent

---

## 0. 执行顺序（按需求）

```
① 列开发计划  ──►  ② 最小闭环 MVP  ──►  ③ INDEX.md 逐篇技术对照
   本文件           C3–C6 打通           C10 收口验证
```

**② 是重心**：不追求一次做全，要求端到端能跑——输入一句话、agent 干活、落到文件。
**③ 是收口**：MVP 跑通之后，再回头逐篇核对 INDEX.md 里提到的技术到底引入了没有，
产出对照表。**不提前做**，避免写成纸上谈兵。

---

## 1. 目标与需求矩阵

一句话：**跑在终端里的通用助手 agent**——能写文章、能写代码、能做任务规划，
可加载技能、可插拔沙箱、MCP 从配置文件动态接入。

| # | 需求 | 落地方式 | 提交 |
|---|---|---|---|
| R1 | 支持 skill 加载 | `create_deep_agent(skills=[...])` 多源目录 + 渐进式披露 | C4 |
| R2 | 支持沙箱 | `create_deep_agent(backend=...)` 可插拔 | C3 |
| R3 | MCP 可配置 | `.mcp.json` + `langchain.mcp.MCPAdapter` 运行时发现 | C5 |
| F1 | 可写文章 | `writer` profile + `article-writing` skill | C6 |
| F2 | 可编码 | `coder` profile + `coding` skill + `execute` 工具 | C6 |
| F3 | 可任务规划 | `TodoListMiddleware`（`write_todos`）+ `planner` profile | C6 |
| E1 | CLI 入口 | `typer` + `rich` | C3 |
| T1 | 技术栈 | `deepagents` | C3 |

**非目标**：全屏 TUI、自动更新、插件市场、成本账单、Web 前端。

---

## 2. 技术选型（已在本机实测验证，非推断）

### 2.1 依赖

```toml
requires-python = ">=3.11"
dependencies = [
  "deepagents>=0.7.24",           # agent harness 内核
  "langchain[mcp]>=1.4.5",        # 核心 + MCPAdapter（含 fastmcp）
  "langchain-openai>=1.7.1",      # OpenAI 兼容端点
  "langchain-anthropic>=1.7.6",   # Claude
  "langgraph-checkpoint-sqlite",  # 会话持久化
  "typer>=0.12", "rich>=13", "prompt-toolkit>=3",
  "python-dotenv>=1", "pydantic>=2.7", "tomli-w>=1",
]
```

### 2.2 可行性验证结果

| 检查项 | 结果 |
|---|---|
| `deepagents 0.7.24` 对 langchain 的约束 | `langchain>=1.4.4,<2.0.0` |
| 本机 langchain 版本 | `1.4.5` ✅ 满足 |
| `langchain.mcp` 模块是否存在 | ✅ **已内置** `langchain/mcp/adapter.py` |
| 缺什么 | 仅缺 `fastmcp` → `pip install "langchain[mcp]"` |

**结论**：可直接用新的 `MCPAdapter` API，**无需**回退到已废弃的 `langchain-mcp-adapters`。

### 2.3 API 落点（均取自官方文档）

| 能力 | 落点 |
|---|---|
| 组装 agent | `create_deep_agent(model, tools, system_prompt, middleware, subagents, backend, permissions, memory, skills, interrupt_on, checkpointer)` |
| 任务规划 | `middleware=[TodoListMiddleware()]` |
| 技能 | `skills=["<包含技能子目录的父目录>/"]` |
| 本地沙箱 | `backend=LocalShellBackend(root_dir=..., virtual_mode=True)` |
| MCP | `from langchain.mcp import MCPAdapter` → `await adapter.list_tools()` |
| 审批 | `interrupt_on={...}` + checkpointer |

### 2.4 三个必须规避的坑

1. **`permissions=` 管不住 `execute`。** 文档原文：permissions 不适用于沙箱后端。
   → 本地模式靠 `interrupt_on` 审批兜底。
2. **`tools=` 是构图期参数。** MCP 工具只能建图时传入 → **每会话构建一次 agent**，
   `/mcp reload` = 重建（MCP 工具是可重入的，重建成本很低）。
3. **`TodoListMiddleware` 不传就静默消失。** 无报错，`write_todos` 和 `todos` 通道直接不存在。

---

## 3. 架构

```
CLI 层      cli.py / ui.py / session.py / approval.py
组装层      profiles.py / subagents.py  →  create_deep_agent(...)
能力层      skills.py / mcp.py / backends.py
配置层      config.py / paths.py
内核        deepagents SDK（不自研）
```

### 3.1 目录结构

```
my-assistant-agent/
├── .claude/                      # Claude Code 续跑 harness
│   ├── settings.json             #   hooks: SessionStart / Stop
│   └── harness/{tasks.json,progress.py}
├── PROGRESS.md                   # 人可读进度看板
├── docs/{PLAN.md, ARCHITECTURE.md, INDEX-COVERAGE.md}
├── src/maa/
│   ├── __main__.py  cli.py       # typer 入口
│   ├── config.py  paths.py       # 配置 + 多根路径
│   ├── backends.py               # 沙箱后端工厂
│   ├── mcp.py                    # MCP 动态加载
│   ├── skills.py                 # 技能发现
│   ├── profiles.py  subagents.py # 能力定义
│   ├── session.py  approval.py   # 会话 + 审批
│   └── ui.py                     # rich 渲染
├── skills/{article-writing,coding,task-planning}/SKILL.md
├── tests/
└── pyproject.toml  README.md  .mcp.json.example  .env.example
```

### 3.2 配置：`~/.maa/config.toml`（`MAA_HOME` 可重定位）

```toml
[models]
default = "openai:gpt-5.5"          # 可被 --model / MAA_MODEL 覆盖

[models.providers.local]            # OpenAI 兼容端点，对齐 prac/.env 用法
base_url_env = "OPENAI_BASE_URL"
api_key_env  = "OPENAI_API_KEY"

[backend]
mode = "local"                      # local | readonly | remote
workspace = "."

[approval]
mode = "manual"                     # manual | yolo
gated_tools = ["write_file", "edit_file", "delete", "execute"]

[mcp]
config_files = [".mcp.json", "~/.maa/mcp.json"]
tool_timeout = 120

[skills]
extra_dirs = []
```

### 3.3 技能源目录（低 → 高优先级）

```
~/.maa/skills/   ~/.claude/skills/   ~/.agents/skills/
<project>/.maa/skills/   <project>/.agents/skills/   + config.skills.extra_dirs
```

> 读 `~/.claude/skills/` 是刻意的：你本机已有 `~/.claude/skills/swarm/SKILL.md`，可直接复用。

### 3.4 MCP 配置：标准 `mcpServers` 结构，**省略 `transport`**（由 FastMCP 推断）

```json
{
  "mcpServers": {
    "docs-langchain":      { "url": "https://docs.langchain.com/mcp" },
    "reference-langchain": { "url": "https://reference.langchain.com/mcp" },
    "fs": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
  }
}
```

> **坑**：`MCPAdapter` 的字符串 target 必须是 URL。stdio 服务必须写成
> `{"command":..., "args":[...]}` 字典；写成裸字符串会被当脚本路径执行。
> `mcp.py` 做强制校验并给出可读报错。

---

## 4. 【步骤②】最小闭环 MVP 定义

### 4.1 什么叫"闭环"

```
用户输入一句话
   ↓
maa 读取配置 / 技能 / MCP 配置
   ↓
装配 deepagents agent（沙箱后端 + 技能 + MCP 工具）
   ↓
agent 干活（读文件 / 写文件 / 执行命令 / 调 MCP）
   ↓
结果落盘 + 终端输出
   ↓
再次运行，能接着上一次（thread 恢复）
```

**判据**：`maa run "在 out/ 下写一篇 800 字的关于 X 的文章"` 能真的把文件写出来。

### 4.2 MVP 范围（C3–C6 四个提交）

| 提交 | MVP 内交付 | 刻意不做 |
|---|---|---|
| C3 | 配置加载、路径解析、**沙箱后端工厂**、CLI 骨架（`maa run` / `maa chat`）、`general` profile | 多 profile、审批、会话恢复 |
| C4 | **技能加载**（多源目录 + 渐进式披露）+ 3 个内置技能 | 技能热重载 |
| C5 | **MCP 可配置**（`.mcp.json` + `MCPAdapter` 动态发现） | MCP 项目信任、OAuth |
| C6 | **三种能力 profile**（writer / coder / planner）+ 子代理 | auto 审批分类器 |

MVP 完成即满足 R1/R2/R3 + F1/F2/F3 + E1 —— **需求全绿**，剩下的都是加固。

---

## 5. 功能设计细则

### 5.1 四种 profile（能力入口）

入口：`maa --agent <name>`，REPL 内 `/agent <name>` 切换。

| profile | 用途 | 关键差异 |
|---|---|---|
| `general` | 默认通用 | 全工具 + `TodoListMiddleware` |
| `writer` | 写文章 | 工具白名单排除 `execute`；挂 `article-writing` |
| `coder` | 编码 | 需要 `execute`；挂 `coding`；子代理 `code-reviewer` |
| `planner` | 任务规划 | 强约束先 `write_todos` 再执行；挂 `task-planning` |

`writer` 用**工具白名单**而非 permissions：
`FilesystemMiddleware(backend=..., tools=["read_file","ls","glob","grep","write_file","edit_file"])`
—— `read_file` 必须始终在列，否则抛 `ValueError`。

### 5.2 子代理

保留内置 `general-purpose`（`task` 工具）。额外定义：
`researcher`（资料检索）、`code-reviewer`（只读代码审查）、`outliner`（文章大纲）。
规格用 SDK 的 dict 形式：`name`/`description`/`system_prompt`/`tools`/`model`/`skills`。

### 5.3 沙箱：三种模式

| mode | backend | execute | 适用 |
|---|---|---|---|
| `local`（默认） | `CompositeBackend(default=LocalShellBackend(root_dir=ws, virtual_mode=True), routes={"/large_tool_results/": StateBackend(), "/conversation_history/": StateBackend()})` | ✅ | 本机开发 |
| `readonly` | `FilesystemBackend` + `permissions` 全写拒绝 | ❌ | 只读分析 |
| `remote` | `LangSmithSandbox`（可选 extras：E2B / Daytona / Modal） | ✅ | 隔离执行 |

**为什么套一层 `CompositeBackend`**：文档明确指出裸用 `FilesystemBackend`/`LocalShellBackend` 时，
deepagents 的内部数据（`/large_tool_results/`、`/conversation_history/`）会写到真实磁盘、污染用户项目。
把这两个前缀路由到 `StateBackend` 就干净了。

> ⚠️ **安全边界（README 需显著标注）**：`LocalShellBackend` 用 `subprocess.run(shell=True)`
> 直跑在宿主机，**无任何隔离**；`virtual_mode=True` 对 shell 无效。唯一防线是审批循环。

### 5.4 MCP 流程

```
读取 .mcp.json (+ ~/.maa/mcp.json，项目级覆盖用户级)
   ↓ 校验：字符串必须是 http(s) URL；stdio 必须是 command/args 字典
async with MCPAdapter(config) as adapter: tools = await adapter.list_tools(cache_mode="use")
   ↓ 工具带 metadata["mcp"]["server"|"tool"|"annotations"]
create_deep_agent(tools=[*builtin, *mcp_tools])     # 每会话重建一次
```

- 工具名由 FastMCP 自动加 `{server}_` 前缀，无需手工处理。
- 按 `metadata["mcp"]["annotations"]` 的 `read_only_hint`/`destructive_hint` 决定是否进审批门，
  **而非硬编码工具名**（文档明确推荐）。
- 命名空间是 **beta**，导入会每进程抛一次 `LangChainBetaWarning` → 按配置压制。

### 5.5 会话恢复

`SqliteSaver` 落在 `~/.maa/sessions.db`；`maa chat --resume <id>` / `/threads`。
`interrupt_on` 与 permissions 的 `interrupt` 模式**都要求有 checkpointer**，硬依赖。

> 产品层用 thread 恢复，开发流程层用 §7 的 harness —— 同一诉求的两个层面。

---

## 6. 提交计划（单次 ≤1000 行，逐条列功能点）

| # | 提交 | 内容 | 行数 |
|---|---|---|---|
| C1 | `docs: 开发计划` | PLAN.md、README、pyproject、.gitignore、.env.example | ~550 |
| C2 | `chore(harness): Claude Code 续跑 harness` | tasks.json、progress.py、settings.json、PROGRESS.md | ~350 |
| **C3** | `feat(cli): 最小闭环——配置/沙箱后端/CLI` | config.py、paths.py、backends.py、profiles.py(仅 general)、cli.py、ui.py | ~900 |
| **C4** | `feat(skills): 技能加载与内置技能` | skills.py、3×SKILL.md、测试 | ~600 |
| **C5** | `feat(mcp): MCP 可配置与动态加载` | mcp.py、.mcp.json.example、校验、测试 | ~550 |
| **C6** | `feat(agent): 三种能力 profile 与子代理` | profiles.py 补全、subagents.py、测试 | ~650 |
| C7 | `feat(session): 会话持久化与恢复` | session.py、--resume、/threads | ~500 |
| C8 | `feat(approval): HITL 审批循环` | approval.py、gated tools | ~450 |
| C9 | `test: 单元与冒烟测试补全` | tests/ 全覆盖、假 model | ~700 |
| C10 | `docs: INDEX.md 逐篇技术对照` | INDEX-COVERAGE.md、ARCHITECTURE.md、CHANGELOG | ~450 |

**每次提交前必须 `pytest` 通过。** C3–C6 是 MVP 通道。

---

## 7. Claude Code 续跑 Harness

### 7.1 要解决的

> "确保一个任务完成后要开始下一个任务，或者一个 agent 停止工作后，下一次能继续接着上一次的工作，直到项目完成。"

两个失效场景：**① 任务完成即停摆**（做完一个任务会话就结束了）；**② 上下文丢失**（新会话不知道做到哪、还剩什么）。

### 7.2 设计

**唯一事实来源**：`.claude/harness/tasks.json`

```json
{
  "project": "my-assistant-agent",
  "updated_at": "2026-10-16T00:00:00Z",
  "tasks": [
    { "id": "C3", "title": "最小闭环", "status": "pending",
      "commit": "feat(cli): ...", "deliverables": ["src/maa/cli.py"],
      "acceptance": "pytest 通过 + maa run 能写出文件", "notes": "" }
  ]
}
```

`status` ∈ `pending | in_progress | blocked | done`

**三个 hook**（`.claude/settings.json`）：

| hook | 时机 | 作用 |
|---|---|---|
| `SessionStart` | 会话开始 | 注入"进行中任务 + 下一个待办 + 上次备注" → **解决 ②** |
| `Stop` | agent 准备停止 | 若仍有未完成任务，返回 `decision:"block"` 并喂回下一个任务 → **解决 ①** |
| `PostToolUse` | 写文件后 | 提醒同步 tasks.json |

**配套脚本** `.claude/harness/progress.py`：`next` / `start <id>` / `done <id>` / `block <id> --reason` / `status`。
`done` 时同步重写 `PROGRESS.md`（人可读看板）。

**终止条件**：全部 `done` → `Stop` hook 放行。这就是"直到项目完成"。

### 7.3 为什么用 hook

`Stop` hook 是**进程级强制**：agent 想停，hook 说不行，就必须继续。
比在 CLAUDE.md 里写一句"请继续"可靠得多。

---

## 8. 测试策略

| 层级 | 范围 | 手段 |
|---|---|---|
| 单元 | 配置读写、路径解析、MCP 配置校验、技能发现优先级 | `pytest`，无网络 |
| 单元 | 后端工厂：mode → 正确类型 | 断言类型与参数 |
| 集成 | 四种 profile 都能建出 agent；`write_todos` 是否真的存在 | 假 model + 断言工具清单 |
| 集成 | HITL：gated 工具触发 interrupt | `InMemorySaver` + 断言 interrupt |
| 冒烟 | `maa doctor`、`maa --help` | 子进程调用 |

**不用真 model 跑测试**——断言的是"图/工具/中间件装配正确"，不是模型输出质量。

---

## 9. 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| `langchain.mcp` 是 beta | API 可能变 | MCP 代码全收在 `mcp.py` 一个模块 |
| MCP 混用新旧协议 | 整个 fleet 降级旧协议 | 校验时检测；必要时拆多 adapter |
| `LocalShellBackend` 无隔离 | 误删文件 | 默认 manual 审批 + README 警告 |
| `deepagents` 迭代快 | API 漂移 | 锁 `>=0.7.24,<0.8`；升级前跑测试 |
| 官方 `dcode` 不支持 Windows | — | 本项目只用 SDK，不依赖其 CLI |

### 待确认（不阻塞，均可配置改动）

1. **默认模型** `openai:gpt-5.5` + `OPENAI_BASE_URL`（对齐 `prac/.env`）
2. **默认沙箱** `local`（能执行命令，代价是无隔离）
3. **CLI 命令名** `maa`

---

## 10. 参考

- `E:\学习\agent\langchain-graph\deepagents-docs\`（INDEX.md 及 56 篇）
- LangChain MCP：https://docs.langchain.com/oss/python/langchain/mcp
