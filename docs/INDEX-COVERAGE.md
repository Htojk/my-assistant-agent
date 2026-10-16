# deepagents 文档逐篇技术对照

对照对象：`E:\学习\agent\langchain-graph\deepagents-docs\INDEX.md` 索引的全部 **56 篇**
（Python 核心 35 篇 + Frontend 4 篇 + Deep Agents Code CLI 17 篇，另有 JS 目录）。

**口径**：结论以**本仓库当前代码**为准，不以 PLAN.md 的打算为准 —— 计划和落地过了一周
会漂，漂的地方在第 5 节单独列出来。每条「落在哪」都能在代码里点得到。

**四种标记**：

| 标记 | 意思 |
|---|---|
| `已引入` | 这篇讲的技术我们在用，且能指出落在哪个文件 |
| `部分引入` | 用了其中一部分，没用的那部分在「为什么」里逐条说明 |
| `未引入` | 没做。给的是**理由**，不是「暂不支持」这种话 |
| `参考` | 不是可落地的技术（对比/变更日志/教程），读了当输入，不产生代码 |

---

## 1. Python 核心（35 篇）

| 文档 | 文档里的技术点 | 结论 | 落在哪 / 为什么 |
|---|---|---|---|
| `overview.md` | 核心能力、执行环境、上下文管理、委派、steering | 已引入 | 分层就是照它搭的：`backends.py`（执行环境）、`skills.py`（上下文管理）、`subagents.py`（委派）、`profiles.py`（steering）。steering 落在启动期的 `--agent` |
| `quickstart.md` | 建 agent、API key、自定义工具、LangSmith tracing、streaming | 部分引入 | 建 agent / key / streaming 都在（`profiles.build_agent`、`config.py`、`cli._drain`）。**未引入**：自定义 search 工具（我们没做联网检索）、LangSmith tracing（本地 CLI，没有观测后端） |
| `customization.md` | Model / Tools / Prompt / Middleware / Subagents / Backends / HITL / Skills / Memory / Profiles / Structured output | 部分引入 | 十一个参数的九个都用了，见 `profiles.build_agent()` 那一次 `create_deep_agent(...)`。**未引入**：`memory=`（见 `memory.md`）、`response_format`（结构化输出，CLI 的产物是文件和终端文字，不需要 schema） |
| `models.md` | 支持的模型、模型参数、运行时选模型 | 已引入 | `profiles.build_model()` 走 `init_chat_model(spec, **kwargs)`，spec 的 `provider:model` 由 LangChain 解析；运行时切换是 `--model` / `MAA_MODEL`（`cli.py`），provider 的 `base_url` / `api_key` / `use_responses_api` 走 `[models.providers]` |
| `tools.md` | 自定义工具、MCP 工具、内置 harness 工具、多模态工具输出 | 部分引入 | MCP 工具走 `mcp.connect()` → `extra_tools=`；内置工具按 profile 白名单给（`profiles.WRITER_TOOLS` 等）。**未引入**：自定义 Python 工具（本地 CLI 里加自定义工具等于把工具集冻在代码里，用户改不了；MCP 已经是那条可配置的路）、多模态工具输出 |
| `subagents.md` | 配置、自定义、`SubAgent`/`CompiledSubAgent`、forked、dynamic、结构化输出、`general-purpose` | 部分引入 | dict 形式自定义（`subagents.build()`）+ 内置 `general-purpose` 都在；子代理继承主 agent 的后端与权限（`subagents.build_all` 收 `backend`/`permissions`）。**未引入**：`CompiledSubAgent`（没有现成的图要挂）、forked 子代理（要多一份对话历史，token 代价对 CLI 不划算）、子代理的结构化输出 |
| `dynamic-subagents.md` | 运行中派生子代理 | 未引入 | 子代理是**静态**四种（`subagents.REGISTRY`）：模型能派的对象从启动就定死，人能一眼看全。动态派生会让"这次会话能干什么"变成运行时才知道的事，对本地工具来说得不偿失 |
| `async-subagents.md` | 跨进程 / 远程异步子代理、传输、拓扑 | 未引入 | 这套的前提是有一台长期在线的服务来托管子代理。我们是单进程 CLI，进程退出会话就结束 —— 没有可以托的地方 |
| `backends.md` | 内置后端、路由、自定义后端、权限、policy hooks、协议参考 | 已引入 | `backends.build_backend()`：`CompositeBackend(default=LocalShellBackend(virtual_mode=True), routes={"/large_tool_results/": StateBackend(), "/conversation_history/": StateBackend()})`，readonly 走 `FilesystemBackend`。路由是**必须**的：裸用会把 deepagents 的内部数据写进用户项目（`tests/test_profiles.py::test_内部数据不落在工作目录里` 钉住这条） |
| `permissions.md` | 规则结构、暂停待批、子代理权限、composite backend | 已引入 | `backends.build_permissions()` 造规则、`profiles.Profile.permissions` 叠 profile 自己的、子代理拿同一套（`subagents.build_all`）。"Pause for human approval" 那条见 `human-in-the-loop.md` |
| `sandboxes.md` | 远程沙箱、提供方、生命周期、集成、安全考量 | 未引入 | `backend.mode = "remote"` 只差一句 `describe()` 提示（`backends.REMOTE_HINT`），没真接通。远程沙箱要账号、要计费、要拉镜像；README 里已经把「local 无隔离」写清楚了，先用审批当边界 |
| `interpreters.md` | 代码解释器、程序化工具调用（PTC）、持久化 | 未引入 | `execute` 已经是 shell 通道，解释器是**另一条**代码执行通道。多一条通道 = 多一个要审批、要隔离、要接后端的口子，而它的收益（PTC：一次调用里循环调工具）对我们这种单轮产出文件的场景用不上 |
| `skills.md` | 多源发现、渐进披露、子代理技能、技能权限、加工具、重载、pin、`SKILL.md` 写法 | 部分引入 | 多源发现 + 优先级去重（`skills.discover`/`scan`）+ 渐进披露（`_skill_hint` 把 `/skills/<源>/<名>/SKILL.md` 写进系统提示，正文靠 `read_file` 拿）都在，子代理可点技能。**未引入**：技能自带工具、技能级权限、运行时重载、pin 版本 —— 本地 CLI 里技能是"一段可读的说明"，给说明配上代码执行能力是另一套信任模型 |
| `memory.md` | 长期记忆、scoped memory | 未引入 | `memory=` 没传。记忆会往**用户项目**里写文件（进程自己决定写什么），而 `write_file` 恰恰在审批门的另一边 —— 让中间件绕过审批写盘，就把 C8 刚建立起来的边界拆了。跨会话的连续性由 `--resume` 的线程恢复提供 |
| `context-engineering.md` | 输入上下文、运行时上下文、自定义 state、压缩、子代理隔离、长期记忆 | 部分引入 | 压缩走 SDK 默认的 summarize 中间件（长会话不会撑爆窗口），上下文隔离靠子代理（子代理只有自己的消息）。**未引入**：自定义 state schema（我们不需要图里多带状态）、长期记忆 |
| `profiles.md` | harness profiles、注册键、合并语义、provider profiles、从配置文件加载、作为插件发布 | 部分引入 | **registry 那条是 deepagents 的 beta 概念，我们没用**；用了的是 provider profile 那一半（`[models.providers.<名>]` → `build_model` 的 kwargs）。我们自己的 `Profile` 是另一层东西：能力预设（工具白名单 + 提示词 + 点名的技能 + 子代理），见 `profiles.REGISTRY` —— 两者名字撞了但不冲突 |
| `multimodal.md` | 多模态输入、`read_file` 读图、自定义工具输出、压缩与多模态 | 未引入 | 输入面是终端文本。`read_file` 读图是 SDK 自带能力，没有主动关掉，但 CLI 里没有"贴一张图"的入口，也就没有对应的验收 |
| `human-in-the-loop.md` | 基本配置、决定类型、条件中断、处理中断、流式处理、多工具调用、拒绝理由、改参数、子代理中断、权限中断 | 部分引入 | 审批循环整个在 `approval.py` + `cli._run_turn`：中断从 `stream_mode="updates"` 的 `__interrupt__` 里捞，`Command(resume={"decisions": [...]})` **按位置**对齐，多工具调用时决定数量不足就拿 reject 补满，拒绝理由原样转给模型，子代理里的 gated 工具照样问。**未引入**：让用户**改**参数再批（edit）、把 `permissions` 的 `interrupt` 模式也接成对话框 |
| `rubric.md` | `RubricMiddleware`、LLM-as-a-judge、迭代到达标 | 未引入 | 需要**第二个模型**当裁判并可能多轮重跑。"写一篇 800 字的文章"的"达标"由人看，不由裁判模型看；把它打开等于每次任务翻倍或三倍的 token，换一个对人来说不透明的判据 |
| `fault-tolerance.md` | 错误处理策略、限流、重试、回退 | 部分引入 | 重试由 provider SDK 兜底；我们自己做的容错是 **MCP 按服务隔离**（`mcp.connect` 并行连 + 一个坏服务不连累别的，`cli._mcp_summary` 把失败原因印出来）。**未引入**：显式限流、模型回退链（配了 `[models.providers]` 的用户自己知道主备，我们不自作主张切模型 —— 悄悄换模型比报错更难查） |
| `going-to-production.md` | LangSmith 部署、生产考量、记忆、执行环境、护栏、前端 | 未引入 | 整篇的前提是"把 agent 当成一个服务部署出去"。本项目是**本地 CLI**，没有部署面、没有多租户、没有计费方 |
| `streaming.md` | 子图流、命名空间、子代理进度、LLM token、工具调用、多模式、chunk 形状 | 部分引入 | 用了 `stream_mode=["updates", "messages"]`：`messages` 出 token 增量（`ui.stream_delta` 边到边印），`updates` 出工具调用与中断（`ui.tool_call` / `approval.pending`）。**未引入**：子图命名空间与子代理的实时进度 —— 终端里并发打印几路子代理会糊成一团，等它回来再印反而读得清 |
| `event-streaming.md` | 结构化的子代理生命周期事件、并发消费 | 未引入 | 面向消费者是**前端**（要一个个卡片）。终端里没有卡片可更新，只有一行行滚过去 |
| `retrieval.md` | 知识库构建、RAG 架构 | 未引入 | 要向量库、嵌入模型、索引管线，是一个独立子系统。CLI 助手的"知识"由技能（人写的一小段说明）+ 工作目录里的文件提供 |
| `rag.md` | RAG 完整示例、索引、委派、综合 | 未引入 | 同上。这是教程级的完整应用，不是可以"顺手引入"的一项能力 |
| `mcp.md` | 安装、快速开始、传输 | 已引入 | `mcp.py` 全模块。额外做了一层官方文档没提的**强制校验**：字符串条目必须是 http(s) URL（裸字符串会被 fastmcp 先当文件路径拉起本机进程）、`args` 必须是字符串列表、`${VAR}` 引用的环境变量必须在场；传输由我们显式构造成 `StdioTransport`/`StreamableHttpTransport`/`SSETransport` 再交给 `MCPAdapter` |
| `acp.md` | Agent Client Protocol、客户端 | 未引入 | ACP 是让**编辑器**当客户端的协议。我们自己是客户端（人在终端里敲），没有第二个宿主来接 |
| `ag-ui.md` | AG-UI 协议、前端事件、程序化 API | 未引入 | 同上，而且是给 Web 前端的 |
| `a2a.md` | A2A 端点、Agent Server、agent 卡片、分布式追踪 | 未引入 | 要有 Agent Server。我们没有服务端，也没有别的 agent 要互相发现 |
| `openwiki.md` | 用 agent 维护代码库 wiki 的独立 CLI | 未引入 | 这是一个**独立产品**（`npm install -g openwiki`），不是 SDK 里的一顶能力。有意思的是它的思路（让 agent 先读 wiki 再读源码）和我们的 `skills` 是同一类：把"该知道什么"预先写成人可读的文本 |
| `comparison.md` | 与 Claude Agent SDK 的取舍对比 | 参考 | 文档性质。它确认了一件对本项目要紧的事：执行后端可插拔 + 模型不限厂商，正是我们选 `deepagents` 而不是直接绑某个 SDK 的原因 |
| `content-builder.md` | 教程：内容构建 agent | 部分引入 | 目标与我们 `writer` profile 高度重合（写作 + 大纲子代理 + 结构化产出），但它是单文件教程脚本。我们照的是它的**思路**（先大纲再正文、产物落到文件），代码没抄 |
| `data-analysis.md` | 教程：数据分析 agent | 未引入 | 要一套数据分析工具（读表、算、画图）和数据后端。这是另一个应用方向，不在 CLI 助手的三个能力面里 |
| `deep-research.md` | 教程：深度研究 agent | 未引入 | 要搜索 API（Tavily 之类）和检索管线 —— 和我们没引 RAG 是同一个取舍 |
| `changelog-py.md` | 版本变更记录 | 参考 | 升 SDK 前读。PLAN 的风险表里已经写了「锁 `>=0.7.24,<0.8`，升级前跑测试」 |

---

## 2. Frontend（4 篇）

| 文档 | 技术点 | 结论 | 为什么 |
|---|---|---|---|
| `frontend/overview.md` | 前端 SDK 暴露什么、模式 | 未引入 | 非目标（PLAN §1 写明了「非目标：全屏 TUI / Web 前端」） |
| `frontend/subagent-streaming.md` | 用选择器订阅子代理流、卡片式渲染 | 未引入 | 同上，且它需要 `event-streaming` 那套结构化事件（见上） |
| `frontend/todo-list.md` | 用 `useStream` 渲染 todo 列表与进度条 | 未引入 | 同上。`write_todos` 的状态我们照拿（`planner` profile 就靠它），但没有图形化的进度条 ——终端里它显示成一次工具调用 |
| `frontend/sandbox.md` | 前端 + API server + 远程沙箱的架构 | 未引入 | 需要 API server，见 `a2a.md` / `sandboxes.md` 两条 |

---

## 3. Deep Agents Code（官方 CLI，17 篇）

官方那个 CLI 叫 `dcode`，和本项目**功能面高度重叠**（也是 typer 风格、也有 profile /
审批模式 / MCP / 技能）。区别在于官方 CLI 不支持 Windows（PLAN §9 风险表里已经记了），
而这项目是跑在 Windows 上的。所以这一节的口径是：**它有的、我们有的，算已引入；
它有的、我们没有的，逐条给理由。**

| 文档 | 技术点 | 结论 | 落在哪 / 为什么 |
|---|---|---|---|
| `code/overview.md` | 能力清单、上手 | 参考 | 对照用。它列的能力面（HITL、技能、MCP、子代理、远程沙箱）我们覆盖了前四项 |
| `code/quickstart.md` | 首个任务、交互模式、管道模式、LangSmith 追踪 | 部分引入 | 交互模式（`maa chat`）和管道模式（`maa run` + stdin）都在，管道里审批会一律拒 —— 这条在 README 里写明了。LangSmith 追踪未引入 |
| `code/configuration.md` | 设置解析顺序、环境变量、技能目录白名单、profile 位置、主题、自动更新、显示项、自动记忆、历史保留、压缩、卸载、托管配置、`dcode doctor`、数据位置 | 部分引入 | 我们有：解析顺序（`config.load_config`：文件 → 环境变量）、`MAA_HOME` 重定位（对应 `DEEPAGENTS_HOME`）、`maa doctor`、数据位置（`paths.py`）。**未引入**：主题、自动更新、自动记忆、历史保留策略、托管配置（企业面） |
| `code/config-file.md` | 配置文件全项：默认/最近模型、摘要模型、成本警告、缓存警告、reasoning 显示、diff 行号、标签标题、恢复期限、允许的模型、追踪脱敏、provider 配置、模型构造参数、重试、启动审批模式、分类器超时、profile 覆盖、自定义 base URL、兼容 API、任意 provider | 部分引入 | 我们有的：默认模型、provider 配置（自定义 base URL / 兼容 API / 任意 provider 都走 `[models.providers]` + `init_chat_model` 前缀）、启动审批模式。**未引入**：摘要模型的显式指定（用 SDK 默认）、成本/缓存警告、reasoning 显示、标签标题、恢复期限、模型白名单、追踪脱敏、重试次数、auto 分类器的超时 —— 大多是 `dcode` 这个 TUI 特有的显示项与团队管控项 |
| `code/cli-reference.md` | 全套命令与选项：选模型、会话、管道、启动时带技能、脚本里用 rubric、HITL 与 shell、限制文件工具、诊断审计、启动命令与初始提示、远程沙箱、MCP 旗标、凭据管理 | 部分引入 | 对应关系：`--model` / `chat --resume` / `run` + stdin / `--agent` / 工具白名单（profile）/ `maa doctor` / `maa sessions list` / `maa mcp list\|test` / `maa config show\|init`。**未引入**：启动命令与初始提示、rubric、远程沙箱旗标、`dcode auth`（凭据存在环境变量名里，不落盘） |
| `code/providers.md` | provider 参考、切换模型、高级配置 | 已引入 | `[models.providers.<名>]` + `profiles.build_model()`；实测过 base_url / api_key / `use_responses_api` 真的传到实例上（`tests/test_profiles.py::test_provider_的接入参数真的传下去了`） |
| `code/credentials.md` | `/auth`、`dcode auth`、环境变量、key 解析顺序、Tavily | 部分引入 | 环境变量那条路我们在用，且配置里存的是**变量名**（`api_key_env`）不是明文；`maa doctor` 的密钥检查只报"在不在"，从不打印值。**未引入**：`/auth` 与 `dcode auth`（交互式凭据库）、Tavily |
| `code/approval-modes.md` | 选模式、Auto、YOLO、Auto 怎么工作 | 部分引入 | `manual` / `yolo` 两种（`approval.gated()`）。**未引入** "Auto"（用分类器模型判断这个动作危不危险）—— 那是**模型**在替你决定要不要问你，而这里的整个安全边界恰恰是"问你"。多一层模型判断，出错时人不知道是模型放的行，还是自己没看清 |
| `code/subagents.md` | 子代理文件格式、继续父对话、动态子代理、成本 | 部分引入 | 我们的是 Python 里的 dataclass（`subagents.REGISTRY`）而不是 Markdown 文件 —— 好处是工具白名单能跟着走（`spec["middleware"]` 里那把冻住的只读工具），坏处是用户改不了。**未引入**：forked / 继续父对话（同 `subagents.md`）、动态子代理 |
| `code/memory-and-skills.md` | Memory（`AGENTS.md` 之类）与 Skills | 部分引入 | 技能半节等同 `skills.md` 那条；记忆半节见 `memory.md` |
| `code/goals-and-rubrics.md` | 目标与评分标准 | 未引入 | 同 `rubric.md` |
| `code/hooks.md` | 生命周期 hook：事件、输入负载、handler 输出、安全 | 未引入 | `dcode` 的 hook 是**在 CLI 里配置的脚本钩子**（用户往配置文件里写一段程序，agent 到了某个事件就跑它）。这等于给配置文件加了代码执行能力 —— 我们刻意没有：配置文件只描述，不执行 |
| `code/mcp-tools.md` | MCP 快速开始、自动发现、配置格式、多服务、工具超时、工具过滤、OAuth、服务状态、项目信任、系统提示感知 | 部分引入 | 自动发现（`.mcp.json` + `~/.maa/mcp.json`，后者覆盖前者）、多服务、工具超时（`tool_timeout`）、服务状态（`maa mcp test`）、系统提示感知（banner 里报"接上了几个服务几个工具"）都在。**未引入**：工具过滤（黑白名单）、OAuth 登录、项目级信任确认 |
| `code/plugins.md` | 插件与市场 | 未引入 | 非目标（插件市场）。而且插件 = 装进来就能执行的第三方代码，一个本地工具装插件系统等于把信任边界整个交出去 |
| `code/extensions.md` | Python 扩展、能力注册、来源选择、信任项目扩展、打包进插件 | 未引入 | 同 `plugins.md`。要"给 agent 加能力"的用户，现在有两条已经在的路：写技能（说明）、配 MCP 服务（工具） |
| `code/remote-sandboxes.md` | 远程沙箱旗标、可插拔提供方、启动脚本 | 未引入 | 同 `sandboxes.md` |
| `code/changelog.md` | 版本变更 | 参考 | 对照功能面用 |

---

## 4. TypeScript / JavaScript

`js/` 目录结构与 Python 基本一致，同一套概念的另一门语言实现。

**未引入**：本项目是 Python 包（`pyproject.toml` + `src/maa`），CLI、测试、打包全在 Python
这一侧。JS SDK 只在"要不要把前端也做了"这个问题上才有意义，而前端是非目标。

---

## 5. 与 PLAN.md 不符的地方（实测纠正）

§5.4「MCP 流程」里有两句话是照文档推的，落地时实测出来对不上。**以这里为准**：

### 5.1 工具名**没有**被自动加前缀

PLAN 原文：「工具名由 FastMCP 自动加 `{server}_` 前缀，无需手工处理。」

实测：`MCPAdapter.list_tools()` 回来的工具名**就是** MCP 服务里那个名字。起一个名叫
`demo` 的服务、里面注册 `echo`，拿到的工具叫 `echo`，不是 `demo_echo`。所以前缀是我们
自己加的 —— 见 `mcp._one()`：

```python
prefix = _prefix(server.name)                      # 服务名里的非法字符换成 _
named.append(tool.model_copy(update={"name": f"{prefix}_{tool.name}"}))
```

顺带多做了一件文档没提的事：`connect()` 里查重名。服务名可以只差一个非法字符（`a.b` 和
`a/b` 都会被规范成 `a_b`），那时两个前缀会撞上，重名工具会互相盖掉 —— 现在报一条问题
而不是静默丢一个。

覆盖率钉在 `tests/test_mcp.py::test_工具名带服务前缀`。

### 5.2 annotations 在 `metadata["mcp"]["tool"]["annotations"]`，不是 `metadata["mcp"]["annotations"]`

PLAN 原文：「工具带 `metadata["mcp"]["server"|"tool"|"annotations"]`」以及「按
`metadata["mcp"]["annotations"]` 的 `read_only_hint`/`destructive_hint` 决定是否进审批门」。

实测（起一个带 `annotations={"readOnlyHint": True}` 的工具，把 `tool.metadata` 整个打出来）：

```json
{
  "mcp": {
    "tool": {
      "annotations": { "read_only_hint": true, "destructive_hint": false },
      "_meta": { "fastmcp": { "tags": [] } }
    },
    "server": { "name": "demo", "version": "4.1.0" }
  }
}
```

`annotations` 不是 `server`/`tool` 的**同级**键，它嵌在 `tool` 里面。所以
`tool.metadata["mcp"]["annotations"]` 是 `None` —— 照着 PLAN 那句写，拿到的是空值，
而且**不报错**：表现是"按 annotations 决定的审批门一次都没生效"，最难查的那种。

另外 `_meta` 是 MCP 的线上字段名，`model_dump()` 出来的键也是 `_meta`（带下划线）。

### 5.3 前缀解决了，但 annotations **仍然没接进审批**

PLAN 原本说「按 annotations 决定是否进审批门，**而非硬编码工具名**」。实际做出来的是硬编码
名单：`approval.gated_tools`（`write_file` / `edit_file` / `delete` / `execute`）。

不接的理由是实测之后才看清的：**annotations 是服务自己报的**。一个 MCP 服务声明自己
"只读"就真的放行，等于把审批门的钥匙交给了第三方服务 —— 而校验这件事的动机恰恰是"不要
信配置里的东西"。所以真要按危险程度分类，也该由**用户**在配置里说，而不是让服务自报。

这一条不在「已验证」里，是**设计取舍**，写在这里免得下一个人以为漏了。

---

## 6. 统计

| | 篇数 | 已引入 | 部分引入 | 未引入 | 参考 |
|---|---|---|---|---|---|
| Python 核心 | 35 | 7 | 13 | 13 | 2 |
| Frontend | 4 | 0 | 0 | 4 | 0 |
| Deep Agents Code | 17 | 1 | 8 | 7 | 1 |
| **合计** | **56** | **8** | **21** | **24** | **3** |

「未引入」的 24 篇里，按原因归堆：

- **要有服务端才有意义**（13 篇）：`async-subagents`、`going-to-production`、`acp`、
  `ag-ui`、`a2a`、`retrieval`、`rag`、`deep-research`、4 篇 Frontend、`plugin`/`extensions`。
  本地 CLI 没有可部署的面，也没有第二个宿主。
- **要额外的模型调用**（2 篇）：`rubric`、`approval-modes` 的 Auto 模式。
- **是一个独立子系统**（4 篇）：`interpreters`、`data-analysis`、`sandboxes`、
  `code/remote-sandboxes`（远程沙箱的后半条）。
- **会拆掉已有的安全边界**（4 篇）：`memory`、`code/hooks`、`code/plugins`、
  `code/extensions` —— 共同点是"让进程或第三方在不问人的情况下动手"。
- **面向图形界面**（1 篇）：`event-streaming`。

**这不是一份"欠债清单"。** 56 篇里有 24 篇没做，绝大多数是因为它们解决的问题不是这个
项目要解决的问题。真正值得回头看的只有两条：`sandboxes.md`（本地无隔离这件事，最终
要有个比审批更硬的答案）和 `code/mcp-tools.md` 的工具过滤（服务一多，工具表会撑爆
系统提示）。
