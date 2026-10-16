# 架构

一句话：**跑在终端里的通用助手 agent**（写作 / 编码 / 任务规划），
agent 内核用 `deepagents` SDK，**不自研**。这个仓库写的是内核外面那一圈：
配置、路径、沙箱后端、技能发现、MCP 接入、会话、审批、终端渲染。

非目标（PLAN §1 原文）：全屏 TUI、自动更新、插件市场、成本账单、Web 前端。

---

## 1. 分层

```
        ┌──────────────────────────────────────────────────────┐
  入口   │ __main__.py ──► cli.py（typer：run / chat / doctor /  │
        │                config / sessions / skills / mcp）      │
        │ checks.py（doctor 的十一项自检 · 不认 typer）          │
        ├──────────────────────────────────────────────────────┤
  会话   │ session.py（SqliteSaver + 会话索引）                   │
        │ approval.py（问人、理解回答、把决定还给图）              │
        │ ui.py（rich 渲染，stdout / stderr 分家）               │
        ├──────────────────────────────────────────────────────┤
  装配   │ profiles.py ──► create_deep_agent(...)                │
        │ subagents.py                                          │
        ├──────────────────────────────────────────────────────┤
  能力   │ skills.py（技能发现 + 路由）  mcp.py（MCP 动态加载）    │
        │ backends.py（沙箱后端 + 权限）                         │
        ├──────────────────────────────────────────────────────┤
  配置   │ config.py（TOML + 环境变量）  paths.py（文件落在哪）     │
        ├──────────────────────────────────────────────────────┤
  内核   │ deepagents SDK（不改，只调用）                          │
        └──────────────────────────────────────────────────────┘
```

依赖方向**只往下**：`cli` 认识所有下层，下层谁都不认识 `cli`。`profiles` 不认识
`ui` —— 装配层不打印东西，打印是调用方的事（所以它能在测试里被一个假 model 完整
跑一遍而没有任何输出）。

## 2. 各模块（含行数，便于判断改动成本）

| 模块 | 行数 | 职责 | 不认识谁 |
|---|---|---|---|
| `cli.py` | 733 | typer 应用、命令体、REPL、装配流程 | — |
| `mcp.py` | 408 | 读 `.mcp.json`、强制校验、建传输、连服务、工具改名 | `profiles` / `cli` |
| `profiles.py` | 363 | 四种能力预设、建模型、建 agent、技能提示 | `cli` / `ui` |
| `skills.py` | 261 | 技能源发现、优先级去重、frontmatter 解析、后端路由表 | `profiles` / `cli` |
| `session.py` | 261 | 会话索引（sqlite 表）+ `SqliteSaver` | `cli` |
| `checks.py` | 230 | `doctor` 的十一项自检 + 打表 | `cli` / typer |
| `config.py` | 218 | 四个配置段、TOML 读写、`${VAR}` 展开、模板 | 上层全部 |
| `approval.py` | 192 | `interrupt_on` 表、审批人、参数整理 | `cli` |
| `subagents.py` | 182 | 三个子代理规格（工具白名单是冻住的） | `cli` |
| `ui.py` | 156 | rich 渲染（唯一直接碰终端的地方） | 业务层全部 |
| `paths.py` | 125 | `MAA_HOME`、项目根、路径决议与显示 | 上层全部 |
| `backends.py` | 86 | 沙箱后端工厂 + 权限规则 | 上层全部 |

`cli.py` 现在是 733 行。它大是有原因的：typer 的命令体不好往下拆（拆成函数就得一路把
`ctx` 传下去），所以留在这儿的是"只有站在命令行这个位置才看得清"的东西。`doctor` 的
十一项检查本来也在这儿，后来切去了 `checks.py` —— 理由是**它跟命令行毫无关系**：
输入是四个字段、输出是一张表和"全绿没有"，一行 typer 都不认识。切出去之后有个额外
好处：以前它只能隔着子进程测，现在同进程直接测（见 §7）。

---

## 3. 两条主流程

### 3.1 `maa run "写一篇文章"`（一次性）

```
load_environment()              从 .env 补环境变量
  ↓
_load_config                    读 ~/.maa/config.toml（没有就用默认值）
  ↓
resolve_workspace               配置里的相对路径按**项目根**算，不按 cwd
  ↓
mcp.discover → mcp.connect      asyncio.run 连上各服务，拿工具（**并行**连）
  ↓  一个服务连不上：记一条问题，其余照旧
profiles.build_agent            skills.discover → backends.build_backend
  ↓                             → resolve_model → create_deep_agent(...)
  ↓  checkpointer = InMemorySaver（只为让审批能停一下）
_run_turn                       ┌─ graph.stream(stream_mode=["updates","messages"])
  ↓                             │    messages → token 增量直接印
  ↓                             │    updates  → 工具调用印一行；捞出待审批请求
  ↓                             └─ 有待审批 → 问人 → Command(resume=...) 接着跑
ui                               最后一段回复
```

**每会话重建一次 agent**，这是硬约束：`tools=` 是**构图期**参数（PLAN §2.4 坑 2），
MCP 工具只在建图那一刻能塞进去。重建很便宜（工具是可重入的），代价是 MCP 的连接
发生在**启动期** —— 所以连不上的时候人立刻知道，而不是第一次调工具时才知道。

### 3.2 `maa chat`（多轮 + 可恢复）

和上面同一套装配，多两样东西：

- **`session.Store`**：`~/.maa/sessions.db` 里一张 `sessions` 索引表，
  记 `thread_id / title / profile / workspace / turns / created_at / updated_at`。
  它是**给人看的目录**，恢复用不上它 —— 那些在 checkpoint 里。
- **`SqliteSaver` 当 checkpointer**：多轮上下文和跨进程恢复都靠它。
  会话 id 形如 `chat-20261016-153012-a1b2`（时间戳 + 随机尾巴，一眼认得出是会话 id）。

REPL 命令只有五个：`/help` `/threads` `/resume <id>` `/new` `/exit`。
**没有 `/agent`**（PLAN §5.1 提过）—— profile 定了工具白名单、提示词、技能和子代理，
中途换 profile 等于中途换一个 agent，把它做进 REPL 会让"我这条会话到底是谁在说话"
变得说不清。换能力就 `maa chat --agent coder`。

`--resume` 之外，恢复的**语义**是"接着上次的 checkpoint 继续"：图的状态（消息、todos）
全在，不是把历史文本重新喂一遍。

---

## 4. 几个关键决定，以及它们的代价

**① `CompositeBackend` 包一层，而不是直接用 `LocalShellBackend`。**
deepagents 的内部数据（`/large_tool_results/`、`/conversation_history/`）会走后端落地。
不包一层，它们就写进**用户的项目目录**里 —— 用户打开自己的仓库，发现多了两个莫名其妙的
目录。把这两个前缀路由到 `StateBackend` 就干净了（`backends.INTERNAL_ROUTE_PREFIXES`）。
代价：后端是复合的，`describe()` 得手写一句话给人看，不能靠类型名。

**② 审批是本地模式下唯一的安全边界。**
`permissions=` 管不住 `execute`，也管不住沙箱后端（PLAN §2.4 坑 1，官方文档原文）。
所以本地模式的防线只能是在**执行之前停下来问人**（`approval.gated()` → `interrupt_on`）。
代价：管道 / CI 里没人能点头，写操作会被一律拒掉 —— README 里写明了，要跑到底得显式
设 `yolo`。这是有意选的方向：**默认往安全那边倒**。

**③ 审批名单是静态的，不由 MCP 服务的 annotations 决定。**
理由见 [INDEX-COVERAGE.md §5.3](INDEX-COVERAGE.md#53-前缀解决了但-annotations-仍然没接进审批)：
annotations 是服务自报的，信它等于把钥匙交给第三方。

**④ 装配层不打印、不连网。**
`profiles.build_agent()` 只接**现成的**工具（`extra_tools=`），自己不连 MCP。连网是
一次 I/O，塞进装配会让 `doctor` 和每个测试都跟着变慢变飘。连不连、连谁，由 `cli` 决定。
代价：调用方有责任记得先连 —— 但这比"测试和自检偷偷连网"好办。

**⑤ 测试里绝不连真模型。**
断言的是"图 / 工具 / 中间件装对了"，不是"模型写得好不好"。四种 profile 各建一遍、
gated 工具真的拦住写操作、技能正文真的能被 `read_file` 读到 —— 这些都不需要模型。
代价：**提示词本身的效果没人测**。`WRITER_PROMPT` 里那句话到底有没有用，只能靠人用。
这是测试策略里最大的一块盲区，说清楚比假装覆盖了好。

**⑥ 配置只存环境变量名，从不存明文密钥。**
`api_key_env = "OPENAI_API_KEY"` 而不是 `api_key = "sk-..."`；`maa doctor` 的密钥检查
只报"在不在"，从不打印值；`McpServer.describe()` 只显示地址和命令，header 里的 token
一个字都不露（`tests/test_mcp.py::test_描述里不带凭据` 钉住）。MCP 的密钥可以写
`${VAR}` 从环境取，不必落进 `.mcp.json`。

**⑦ beta 警告的压制收在一处。**
`langchain.mcp` 是 beta，导入时会抛一次 `LangChainBetaWarning`。压制写在
`mcp.adapter_for()` 里，用官方给的口子 `suppress_langchain_beta_warning` ——
普通的 `warnings.catch_warnings()` 压不住它。收在一处的意思是：将来它转正，
要删的只有一个 with。

**⑧ `paths.home_dir()` 是留给人替换的接缝。**
生产上它返回 `Path.home()`，测试里被换成一个临时目录；另外 `MAA_HOME` 环境变量能把
整个 `~/.maa` 挪走。这不是为测试而测试：技能搜索路径里那两个 `~/.claude/skills`、
`~/.agents/skills` 是**有意**算进去的生产行为（复用机器上已有的技能），不隔离的话
测试结果就取决于跑它的机器上装了什么。

---

## 5. 数据落在哪儿

| 路径 | 什么 | 谁写 |
|---|---|---|
| `~/.maa/config.toml` | 模型 / 后端 / 审批 / MCP / 技能配置 | `maa config init`，之后归用户 |
| `~/.maa/sessions.db` | `sessions` 索引表 + langgraph 的 checkpoint 表 | `maa chat` 每一轮 |
| `~/.maa/mcp.json` | 用户级 MCP 服务（**覆盖**项目级的同名服务） | 用户手写 |
| `~/.maa/skills/`、`~/.claude/skills/`、`~/.agents/skills/` | 用户级技能 | 用户 / 其他工具 |
| `<项目>/.mcp.json` | 项目级 MCP 服务 | 用户手写 |
| `<项目>/.maa/skills/`、`<项目>/.agents/skills/` | 项目级技能 | 用户手写 |
| `MAA_HOME` | 上面所有 `~/.maa` 的位置都能被它整个挪走 | 环境变量 |

删掉 `sessions.db` = 清空历史；删掉 `config.toml` = 回到内置默认值（`maa config show`
看的就是那份默认值，不需要文件存在）。

**不会落盘的东西**：模型回复的 token 流（只印到终端）、deepagents 的内部数据
（路由到 `StateBackend`，即图的内存里）、任何密钥明文。

---

## 6. 已知限制（诚实清单）

按"知道了但决定不做"和"应该修还没修"分开。

### 6.1 有意为之

1. **`local` 模式无隔离。** `subprocess.run(shell=True)` 直接跑在宿主机，`virtual_mode`
   对 shell 无效。唯一防线是审批。这不是没做完，是当前设计下能做到的上限 ——
   要更硬的边界得上远程沙箱（`sandboxes.md`，未引入）。
2. **管道里写操作全拒。** 见 ② 的代价。
3. **profile 是启动期选定的**，REPL 里不能切（见 3.2）。
4. **技能是"说明"不是"代码"。** 不给技能配工具、不给它执行能力（`skills.md` 的
   add tools / execute code 两节未引入）。
5. **`remote` 模式只给提示不接通**（`backends.REMOTE_HINT`）—— 没账号、没计费、没镜像。
6. **不自动切模型。** 没做回退链：悄悄换一个模型，出错时人查的是"我配的那个模型"。

### 6.2 应该修

1. **提示词零测试。** 见 ⑤ 的代价。§7.1 那条手工清单能证明装配是通的，但证明不了
   `WRITER_PROMPT` 里那句话有用 —— 那需要一个能判断文章好坏的标准，现在没有。
2. **MCP 工具表会撑爆系统提示。** 服务一多，几十个工具全进 system prompt。
   `code/mcp-tools.md` 的工具过滤（黑白名单）没做 —— 这是 INDEX-COVERAGE §6 里
   点名值得回头看的第二条。
3. **多模态输入没入口。** SDK 的 `read_file` 能读图，CLI 里没地方贴图。

**从这份清单上划掉的**（留着是为了记住它们当初为什么能被忽略这么久）：

- ~~`cli.py` 906 行，`doctor` 该切出去~~ —— 切了，现在是 `checks.py` 230 行 +
  `cli.py` 733 行。切完还捡到一个好处：那些检查从"只能隔着子进程测"变成同进程
  可直接测（§7 里那条假缺口因此缩小了）。**这条之所以一直没做**，是因为"文件大"
  本身不疼 —— 直到要给它写测试，才发现大和测不到是同一件事。
- ~~`interrupt_on` 里写错工具名不报错~~ —— 已修（`c8b21a6`）。这类错**只有在真跑一次
  模型之后才会冒出来**：单测全绿、`doctor` 全绿，因为错的不是代码是配置，而配置里
  那个名字从来没有谁去核对。现在启动和自检都会说。
- ~~文档里的相对链接没有自动检查~~ —— 已收成 `tests/test_docs.py`，文件在不在、
  锚点对不对都验，新写的 md 自动进表。

---

## 7. 测试策略与现状

| 层级 | 范围 | 手段 |
|---|---|---|
| 单元 | 配置、路径、MCP 校验、技能发现与优先级 | `pytest`，无网络 |
| 单元 | 后端工厂、渲染层（方括号 / 流的分家） | 断言类型、参数、捕获输出 |
| 单元 | 自检的十一项（依赖 / 密钥 / 工作目录 / 会话库 / 装配 / 审批名单） | 直接调 `checks.run`，看返回值和打出来的表 |
| 集成 | 四种 profile 建得出来、工具表对不对、审批拦不拦得住 | 假 model + 断言工具清单 + 看**磁盘** |
| 集成 | CLI 每个命令通不通、退出码对不对 | **子进程**跑 `python -m maa` |

**为什么冒烟要真起进程**：入口、`__main__.py`、退出码、stdout/stderr 分家，这些直接
调 `cli.main()` 是测不到的。代价是覆盖率报告里 `cli.py` 只有 32% —— 那是**假缺口**，
命令体跑在子进程里，进程内的 coverage 看不见。（这个缺口原本更大：`doctor` 那些检查
也算在里面。它们搬去 `checks.py` 之后就回到同进程了 —— "文件太大"和"测不到"往往是
同一件事。）

现状：**261 条**，全绿，约 3 分钟。覆盖率（临时装 coverage 量的，量完卸掉了）总 **80%**；
扣掉只能靠子进程跑到的 `cli.py` + `__main__.py`，进程内代码 **97%**
（这个数比总数有意义得多）。`config` / `backends` / `approval` / `subagents` / `profiles`
是 100%，`checks` 98%、`session` 99%、`paths` / `skills` 98%。

剩下那些没盖到的基本是**测不动**的分支，不是懒：`checks` 缺的那两行是"目录不可写"
（Windows 上没法用权限位可靠地造出来），`ui` / `mcp` 缺的是真实终端尺寸与网络半途断开。

不连真模型这条红线在 `tests/conftest.py` 里由两层保证：清掉可能影响断言的模型环境变量
（`no_model_env`），以及把"用户主目录"整个指到临时目录（`isolated_home`），
后者顺带钉住了技能搜索路径不随机器而变。

### 7.1 真机冒烟：不连真模型的代价，得用手工补回来

上面那张表测的是"我们的代码对不对"，测不出"对面那个端点收不收我们的请求"。这两件事
之间的缝**很宽**：模型名写错、`base_url` 少了 `/v1`、端点不认 `tools` 字段 —— 每一条
都能让上面 245 条全绿而产品完全不能用。所以下面这六条**手工**跑，跑完把结论记进
CHANGELOG（`0.1.0` 那条底下就是 2026-10-16 的一次记录）：

```bash
export OPENAI_BASE_URL=http://<端点>/v1     # 注意 /v1，见下面第 1 条
export OPENAI_API_KEY=sk-...                # 只进环境变量，别写进 config.toml
export MAA_MODEL=openai:<模型名>

maa doctor                                              # 九项全绿，且「agent 装配」那行有工具表
maa run "看看当前目录有哪些文件，读一个，用一句话总结"      # 工具循环 + 流式
printf 'y\n' | maa run "在当前目录写一个 hello.txt"        # 审批：批了就真写
printf 'n\n' | maa run "在当前目录写一个 nope.txt"         # 审批：拒了就真不写
maa chat                                                # 多轮；退出时给会话 id
maa chat --resume <id>                                  # 换一个进程接着聊，得记得上一轮说的话
```

两点实测，值得写下来：

1. **`/v1` 必须有，而且不加看不出来。** `172.16.3.6:8589` 是个网关，裸路径
   （`http://<host>/chat/completions`）被它当网页路由处理，返回的是前端 HTML，
   **HTTP 200**。所以"请求成功"和"请求打到了接口上"是两回事，看状态码看不出来。
2. **模型会流式吐 `reasoning_content`**（DeepSeek 系的思考字段）。我们只印
   `chunk.content`，所以思考的那几秒屏幕是不动的。这是刻意的：那是模型的内部独白，
   混进回答里反而难读。但它确实会让人以为卡住了 —— 如果哪天有人抱怨，改这里。
3. **启动时可能有一条技能描述超长的警告**（`logger.warning`，走 stderr，说"1169 字符
   超过 1024，已截断"）。这种技能通常来自 `~/.claude/skills` 里的第三方技能，不是我们
   写的。deepagents 按规范截断后照常继续，所以它无害 —— 但它是**库**在说话，不是我们，
   别把它当成产品的报错。
