# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

**没有发布过版本。** 下面这条 `0.1.0` 是从空仓库到"能用"的一段连续开发，
按任务切成了十条，每条对应一个 commit —— 免得日后有人对着 `git log` 猜
哪几条是同一件事。日期取的是提交日。

十条做完之后还有一轮：拿真模型跑了一遍（此前一次都没跑过），修掉它暴露的两个问题，
把「文档链接不 404」从手写脚本收成测试。这些单列在「修复」和「验证」两节里 ——
它们不是计划里的任务，是**跑起来才知道的**。

---

## [0.1.0] — 未发布

首个可用版本：`maa run` 一次性跑、`maa chat` 多轮可恢复，
支持四种能力 profile、技能、MCP 服务、沙箱后端和写操作审批。

### 新增

**① 项目骨架** — `c9cb763` `docs: 开发计划——需求矩阵、目录结构、提交计划`
先定边界再动手：`docs/PLAN.md` 写清做什么、不做什么（非目标：全屏 TUI、自动更新、
插件市场、成本账单、Web 前端），以及目录结构、依赖清单、提交计划。整个仓库的目标
一句话：agent 内核用 `deepagents` SDK，我们只写外面那一圈。

**② 续跑 harness** — `a2495b0` `chore(harness): Claude Code 续跑 harness`
`.claude/harness/`：任务台账 + 三个 hook（SessionStart 报进度、PostToolUse 提醒
回写交付物、Stop 守卫拦住"还有任务没做完就收工"）。它不属于产品，但它保证了
这个仓库是**一条一条按计划做完的**，不是一把梭。

**③ 最小闭环** — `6817915` `feat(cli): 最小闭环——配置加载、沙箱后端工厂、CLI 骨架`
`config.py`（TOML + `${VAR}` 展开）、`paths.py`（`MAA_HOME`、项目根、路径决议）、
`backends.py`（四种后端工厂 + 权限规则）、`ui.py`（rich 渲染，stdout/stderr 分家）、
`cli.py`。`maa run` 到这里就能跑通一轮对话。

**④ 技能发现与内置技能** — `47391c9` `feat(skills): 技能目录发现与内置技能`
五级优先级的技能源（项目级 > 用户级 > 内置），frontmatter 解析，按"目录名 = 技能名"
校验，重名按优先级去重。技能是**说明**不是代码：agent 通过 `read_file` 逐级取用，
没有 `load_skill` 工具。

**⑤ MCP 可配置与动态加载** — `66c61ee` `feat(mcp): MCP 配置读取、校验与动态加载`
读 `<项目>/.mcp.json` 和 `~/.maa/mcp.json`，强制校验（顶层结构、`mcpServers` 类型、
文件大小上限），按 URL / 命令建显式传输，**并行**连接、单个失败只记一条问题。
工具名带服务前缀（`demo_echo`），prefork 重名会被查出来。

**⑥ 四种 profile 与子代理** — `6012708` `feat(agent): writer / coder / planner 三种 profile 与子代理`
`general` / `writer` / `coder` / `planner`，各自定工具白名单、系统提示、技能和子代理；
`subagents.py` 三个子代理规格（工具白名单冻住，子代理不能提权）。装配层**不打印、
不连网** —— 所以它能在测试里被假 model 完整跑一遍。

**⑦ 会话持久化与恢复** — `2569b84` `feat(session): SqliteSaver 会话持久化与 --resume`
`~/.maa/sessions.db`：一张给人看的 `sessions` 索引表 + langgraph 的 checkpoint 表。
`maa chat` 多轮上下文、`--resume <id>` 跨进程接着跑、REPL 里 `/threads` `/resume` `/new`。

**⑧ HITL 审批循环** — `65a941b` `feat(approval): 危险工具的 HITL 审批循环`
写操作在**执行之前**停下来问人：`interrupt_on` 表 + 审批人 + `Command(resume=...)`
接着跑。本地模式下 `permissions=` 管不住 `execute`，所以这是**唯一**的安全边界；
管道里没人点头就一律拒，要跑到底得显式设 `yolo`。

**⑨ 测试补全** — `cc555ad` `test: 单元、集成与冒烟测试补全`
反过来做：先量"哪里根本没人走过"再补。新增 `test_paths.py` / `test_ui.py`，
`conftest.py` 加 `isolated_home`（技能搜索路径不该随机器而变），CLI 用例全部改走
**真子进程**。顺带抓到一个真 bug：`maa --version` 退出码是 2（Typer 的 group 回调
选项默认不是 eager）。测试从 165 涨到 **229**。

### 修复

- `maa --version` / `-V` 退出码 2、输出「Missing command」——`--version` 挂在 group
  回调上当普通选项，而它后面没有命令，Click 先抛 Missing command 就退了。修法是给它
  挂上 `callback=..., is_eager=True`。（**新写的冒烟测试抓到的**，不是看出来的。）
- **审批名单里的错别字静默失效** — `c8b21a6`。`gated_tools` 里写错一个工具名，
  中间件见了不认识的名字就当没这条：不报错、没日志、图照常建。于是"我以为 `exec`
  被拦住了"可以一直没人知道，直到某天真执行了。现在 `approval.ineffective()` 会在
  **启动时**和 **`doctor`** 两处把它捞出来。判据是"既不在这次的工具表里、也不在内置
  名单里"—— 第二个条件不能省，因为 writer 故意没有 `execute`、planner 连 `delete`
  都没有，而默认名单写着它们，只看工具表会每次都刷假警告。
  （**真机跑出来的**：229 条测试全绿、`doctor` 全绿，因为错的不是代码是配置。）
  顺带删掉了 `cli._report` 里一段执行不到的重复代码。

### 文档

**⑩ 逐篇技术对照与说明** — `9874d9c`
- `docs/INDEX-COVERAGE.md`：deepagents 文档索引里 56 篇，每篇提到的技术标注
  「已引入 / 部分引入 / 未引入 + 原因」。其中 **§5 是实测纠正 PLAN.md 的地方** ——
  工具名前缀、`metadata["mcp"]["tool"]["annotations"]` 的真实路径，都是跑起来量的，
  不是照抄文档。
- `docs/ARCHITECTURE.md`：分层、各模块职责与行数、两条主流程、八个关键决定**及其代价**、
  数据落在哪儿、已知限制（分"有意为之"和"应该修"两栏）、测试策略与现状，
  外加 §7.1 **真机冒烟清单**（见下）。
- `tests/test_docs.py`：把「文档里的相对链接不 404」从手写脚本收成测试 —— 文件在不在、
  锚点对不对都验，`docs/` 下新写的 md 自动进表。中文标题的锚点算法单独钉了一条，
  否则它哪天把中文全吃了，检查会永远通过。
- `CHANGELOG.md`：本文件。

### 验证

**2026-10-16 真机跑通。** C1–C10 做完时，这个项目**从来没有对着真模型跑过**——
245 条测试、`doctor` 全绿，全都在假 model 上。补了这一课，用一个 OpenAI 兼容端点
（`deepseek-flash`）跑了六条：读工具的循环、批准写文件（字节对得上）、拒绝写文件
（磁盘上没有）、`chat` 多轮 + `sessions list`、跨进程 `--resume`（换了进程还记着上一轮
说的名字）。**六条都通**，产品端到端是能用的。清单和两条实测（`/v1` 必须带、
`reasoning_content` 被刻意不印）写在
[ARCHITECTURE.md §7.1](docs/ARCHITECTURE.md#71-真机冒烟不连真模型的代价得用手工补回来)。

密钥只进环境变量（`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MAA_MODEL`），
`~/.maa/config.toml` 里照旧只存 `api_key_env` 的名字。

### 已知限制

完整清单见 [ARCHITECTURE.md §6](docs/ARCHITECTURE.md#6-已知限制诚实清单)，这里是摘要：

- **`local` 模式无隔离** —— `subprocess.run(shell=True)` 直接跑在宿主机，防线只有审批。
- **管道里写操作全被拒** —— 没人能点头就等于不批，这是有意往安全那边倒。
- **profile 启动期选定**，REPL 里不能切（会说不清"这条会话到底是谁在说话"）。
- **提示词零测试** —— 装配测得到，"这句话到底有没有用"测不到，靠人用。
  §7.1 的手工冒烟能证明链路通，证明不了提示词好。
- **MCP 工具表会撑爆系统提示** —— 服务一多的过滤（黑白名单）没做。
- **`remote` 模式只给提示不接通** —— 没账号、没计费、没镜像。
- ~~`interrupt_on` 里写错工具名静默不生效~~ —— 已修，见上面「修复」第二条。

### 未引入（明确不做，不是欠债）

`docs/INDEX-COVERAGE.md` §6 把 24 项未引入按原因归成五类：要有服务端 13、
要额外的模型调用 2、是独立子系统 4、会拆掉已有安全边界 4、面向图形界面 1。
其中只有两条值得回头看：远程沙箱（`sandboxes.md`）和 MCP 工具过滤
（`code/mcp-tools.md`）。

---

[0.1.0]: https://github.com/Htojk/my-assistant-agent/releases/tag/v0.1.0
