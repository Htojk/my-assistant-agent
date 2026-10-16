# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

**没有发布过版本。** 下面这条 `0.1.0` 是从空仓库到"能用"的一段连续开发，
按任务切成了十条，每条对应一个 commit —— 免得日后有人对着 `git log` 猜
哪几条是同一件事。日期取的是提交日。

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

### 文档

**⑩ 逐篇技术对照与说明** — 本次提交
- `docs/INDEX-COVERAGE.md`：deepagents 文档索引里 56 篇，每篇提到的技术标注
  「已引入 / 部分引入 / 未引入 + 原因」。其中 **§5 是实测纠正 PLAN.md 的地方** ——
  工具名前缀、`metadata["mcp"]["tool"]["annotations"]` 的真实路径，都是跑起来量的，
  不是照抄文档。
- `docs/ARCHITECTURE.md`：分层、各模块职责与行数、两条主流程、八个关键决定**及其代价**、
  数据落在哪儿、已知限制（分"有意为之"和"应该修"两栏）、测试策略。
- `CHANGELOG.md`：本文件。

### 已知限制

完整清单见 [ARCHITECTURE.md §6](docs/ARCHITECTURE.md#6-已知限制诚实清单)，这里是摘要：

- **`local` 模式无隔离** —— `subprocess.run(shell=True)` 直接跑在宿主机，防线只有审批。
- **管道里写操作全被拒** —— 没人能点头就等于不批，这是有意往安全那边倒。
- **profile 启动期选定**，REPL 里不能切（会说不清"这条会话到底是谁在说话"）。
- **提示词零测试** —— 装配测得到，"这句话到底有没有用"测不到，靠人用。
- **MCP 工具表会撑爆系统提示** —— 服务一多的过滤（黑白名单）没做。
- **`remote` 模式只给提示不接通** —— 没账号、没计费、没镜像。
- `interrupt_on` 里写错工具名**静默不生效**（deepagents 不校验未知工具名）。

### 未引入（明确不做，不是欠债）

`docs/INDEX-COVERAGE.md` §6 把 24 项未引入按原因归成五类：要有服务端 13、
要额外的模型调用 2、是独立子系统 4、会拆掉已有安全边界 4、面向图形界面 1。
其中只有两条值得回头看：远程沙箱（`sandboxes.md`）和 MCP 工具过滤
（`code/mcp-tools.md`）。

---

[0.1.0]: https://github.com/Htojk/my-assistant-agent/releases/tag/v0.1.0
