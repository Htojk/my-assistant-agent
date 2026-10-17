# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

**没有发布过版本。** 下面这条 `0.1.0` 是从空仓库到"能用"的一段连续开发，
按任务切成了十条，每条对应一个 commit —— 免得日后有人对着 `git log` 猜
哪几条是同一件事。日期取的是提交日。

十条做完之后还有一轮：拿真模型跑了一遍（此前一次都没跑过），修掉它暴露的两个问题，
把「文档链接不 404」从手写脚本收成测试，再把 `doctor` 从 `cli.py` 里切出去。
这些单列在「重构」「修复」「验证」三节里 —— 它们不是计划里的任务，
是**跑起来、动起来才知道的**。⑪ ⑫ 两条也是这么来的，编号接着往下排：重编一遍会让
别处已经写下的引用（`c8b21a6`、`ARCHITECTURE §4 第 ⑨ 条`）跟着全改，不划算。

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

**⑪ MCP 工具过滤 + 同步入口** — `feat(mcp)`
`[mcp] include / exclude`（`config.McpConfig`）：匹配**加了服务前缀之后**的全名、`*`
通配、大小写敏感、**排除赢**（于是 `include = ["*"]` + `exclude = ["*_delete"]` 能表达
"全要，除了删除"；反过来的话加一条 include 会悄悄把 exclude 顶掉）。过滤放在
`mcp.connect()` 里做，因为那是工具表的**唯一出口**；筛掉的名字记进 `Loaded.dropped`，
`maa run` / `maa chat` 启动时印一行，`maa mcp test` 把它们标在表里 —— **静默筛掉一批
工具得到的是"什么都干不了但看着正常"的 agent**，跟下面那条审批错别字是同一类错。

同一条 commit 里还修了 MCP 工具**到手调不动**：`langchain.mcp` 造出来的工具只有异步
实现（`StructuredTool(coroutine=...)`，没有 `func`），同步调直接
`NotImplementedError`；而 `fastmcp.Client` 绑在"建它那个循环"上，`asyncio.run` 连完
一返回循环就关，换一条循环再调是 `Client failed to connect: Event loop is closed`。
解法是 `mcp.Loop`（守护线程上一条进程级循环，从连上活到最后一轮对话结束）+
`mcp.bind()`（给每个工具补一个转手调原 `coroutine` 的同步 `func`，`args_schema` /
`handle_tool_error` / `response_format` 全留在原对象上）。**没把整个 CLI 改成异步**，
理由见 [ARCHITECTURE.md §4 第 ⑨ 条](docs/ARCHITECTURE.md#4-几个关键决定以及它们的代价)。

**⑫ 贴图** — `feat(cli)`
`maa run` 的 `--image`（短写 `-i`，可给多次）+ REPL 的 `/image <路径>`。要贴图就得改
消息的**形状**：`content` 从字符串变成内容块列表，图是
`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}` ——
各家 OpenAI 兼容端点认的就是这个形状，图片按 data URI 内联在请求体里，**不走图床**
（本地 CLI 没有图床，也不该为了看张图先上传一次）。**没图时形状一个字都不变**，有图
才换：这样新路不会顺带改掉所有老路（历史消息、恢复出来的上下文、所有旧断言走的都是
字符串那条）。

三件"顺手的事"才是这条的重点：**格式按文件头认，不看后缀** —— 一个叫 `shot.png` 的
文本文件会被当场拒掉，按后缀办则会被标成 PNG 发出去，对面收到一坨不是图的东西；
**上限用一次带上限的读封死**（`read(MAX_BYTES + 1)`，20 MiB）而不是先 `stat()` 再读 ——
后者遇到"大小是 0 但读起来没完"的特殊文件就白搭，而且不多读一个字节也分不清"刚好到
上限"和"超了"；**读图放在装配之前** —— 路径打错当场退，不等连完 MCP 才告诉你文件名
错了。三条都是"错了以后很难查"的类型，所以每条都有测试钉着。

核心 145 行切成了新模块 `attachments.py`，`cli.py` 那边只留三处壳（`--image` 选项、
`_load_images`、REPL 的 `/image`）。判据跟当初 `doctor` 切 `checks.py` 一模一样：
**它跟命令行毫无关系**（输入是一串路径、输出是一个 `HumanMessage`），而 `cli.py` 只能
隔着子进程测 —— 进程内的覆盖率看不见它，偏偏会出错的地方全在这几十行里。切出去之后
它是 **100%**（23 条测试，全同进程）。

REPL 里 `/image` 附的图**只跟下一句**（成功、失败、中断都清）：留着的话下一句会莫名
其妙又带上那张图，而"为什么它又看了一遍"是个很难自己查明白的问题。敲 `/image` 不带
参数是"我附了哪些"，免得只能靠已经滚上去的那行字记。

### 重构

- **`doctor` 从 `cli.py` 切出去，成了 `checks.py`**。理由不是"文件太长了"——
  是它**跟命令行毫无关系**：不解析参数、不认识 typer，输入是四个字段、输出是一张表和
  "全绿没有"。`cli.py` 那边只剩壳（递四个字段、按返回值定退出码），733 行；新模块 230 行。
  顺手捡到的好处：这些检查以前只能隔着**子进程**测（进程内的覆盖率看不见），现在
  同进程直接测，`checks.py` 98% —— "文件太大"和"测不到"往往是同一件事。
  配 `tests/test_checks.py` 16 条（含"密钥的值绝不打出来"和"自检自己不能崩"两条）。

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
- **rich 把输出里的方括号当样式标签吃掉了** — `fix(ui)`。`ui.info("[mcp] 过滤掉了 2 个
  工具")` 印出来是" 过滤掉了 2 个工具"，`include = [demo_*]` 整段消失 —— 不报错、不提示。
  而方括号在正常内容里到处都是：正则 `[a-z]+`、TOML 段名 `[models]`、报错原文里的
  `[Errno 2]`。现在**内容一律 `rich.markup.escape`**，真要上样式的地方用 `Text` 对象
  （`ui.OK` / `WARN` / `BAD` 就是 `Text`，表格里"记号还是记号"另有一条钉住）。
  这类"内容被静默吃掉"的 bug 一条都不该漏，所以 `test_ui.py` 那边是**参数化**钉的：
  每个出口 × 每种方括号。
- **管道里的中文会崩，而且崩在离原因十万八千里的地方** — `fix(cli)`。
  `printf '中文' | maa chat`：Windows 上非终端的 stdin 默认跟着控制台代码页走（中文机器
  是 GBK），UTF-8 字节被解成一串代理对（`\udcad\udc87`），一路带到请求体里才炸
  `UnicodeEncodeError: surrogates not allowed` —— 报错指着模型调用，真正错在读进来那一步。
  `_force_utf8_output()` 原来只管 stdout / stderr，现在把**非终端的 stdin** 也钉成
  UTF-8（终端里的 stdin 不动：那儿的编码由终端定，替它做主只会把能用的弄坏）。
  回归测试 `test_管道里的中文按_utf8_读` 用 `PYTHONIOENCODING=gbk` 复现 —— 英文机器上
  一样能红，因为真机上触发它的是"中文 Windows 的控制台代码页"。

### 文档

**⑩ 逐篇技术对照与说明** — `9874d9c`
- `docs/INDEX-COVERAGE.md`：deepagents 文档索引里 56 篇，每篇提到的技术标注
  「已引入 / 部分引入 / 未引入 + 原因」。其中 **§5 是实测纠正 PLAN.md 的地方** ——
  工具名前缀、`metadata["mcp"]["tool"]["annotations"]` 的真实路径，都是跑起来量的，
  不是照抄文档。
- `docs/ARCHITECTURE.md`：分层、各模块职责与行数、两条主流程、九个关键决定**及其代价**、
  数据落在哪儿、已知限制（分"有意为之"和"应该修"两栏）、测试策略与现状，
  外加 §7.1 **真机冒烟清单**（见下）。
- `tests/test_docs.py`：把「文档里的相对链接不 404」从手写脚本收成测试 —— 文件在不在、
  锚点对不对都验，`docs/` 下新写的 md 自动进表。中文标题的锚点算法单独钉了一条，
  否则它哪天把中文全吃了，检查会永远通过。
- `CHANGELOG.md`：本文件。

### 验证

**2026-10-16 真机跑通。** C1–C10 做完时，这个项目**从来没有对着真模型跑过**——
229 条测试、`doctor` 全绿，全都在假 model 上。补了这一课，用一个 OpenAI 兼容端点
（`deepseek-flash`）跑了六条：读工具的循环、批准写文件（字节对得上）、拒绝写文件
（磁盘上没有）、`chat` 多轮 + `sessions list`、跨进程 `--resume`（换了进程还记着上一轮
说的名字）。**六条都通**，产品端到端是能用的。清单和两条实测（`/v1` 必须带、
`reasoning_content` 被刻意不印）写在
[ARCHITECTURE.md §7.1](docs/ARCHITECTURE.md#71-真机冒烟不连真模型的代价得用手工补回来)。

密钥只进环境变量（`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MAA_MODEL`），
`~/.maa/config.toml` 里照旧只存 `api_key_env` 的名字。

这一轮之后测试是 **261 条**，全绿，进程内代码覆盖率 97%（总 80% —— 差距全在
只能跑子进程的 `cli.py`，见 ARCHITECTURE §7）。

**2026-10-17 又跑了一遍，这次带着真 MCP 服务。** 上一轮只验了内置工具，MCP 那条路
一次都没真跑过。这次在仓库外起了一个 stdio MCP 服务（`echo` / `add` / `delete_thing` /
`secret_thing` 四个工具），配了 `exclude = ["demo_delete_*", "demo_secret_*"]`，跑：

- `maa mcp list` / `maa mcp test` —— 服务连上、工具表出得来、被筛掉的两个在表里标着；
- `maa run "用 demo_echo 回显 hello，再用 demo_add 算 2+3"` —— **模型真调起了这两个
  工具**，两个结果都对；
- `maa chat` —— 跨轮上下文和同步 `SqliteSaver` 一起用没问题，退出给会话 id。

**第一次跑是失败的**：模型调不动工具，`NotImplementedError: StructuredTool does not
support sync invocation`。这条在仓库里躺了很久 —— 过滤写完、单测全绿、`doctor` 全绿，
因为**从没有一条测试真的调过一次 MCP 工具**（测的都是"连上了没有""工具名对不对"）。
顺带还挖出管道中文崩溃那条（`printf '中文' | maa chat`）。两件都是"跑起来才知道的"，
所以 §7.1 的手工清单里加上了 MCP 那三条。

这一轮之后测试是 **287 条**，全绿，进程内代码覆盖率 97%（总 81%）。

**2026-10-17 第三轮：贴图。** 同一个端点，验多模态那条路。造一张 32×32 的图（红底、
正中间一个蓝方块），跑 `maa run "这张图里有什么？看到什么颜色就说什么颜色，一句话"
--image shot.png`：先印一行 `附上一张图：shot.png（106 B，png）`，回答是
**「红色背景正中间有一个蓝色正方形。」**。`maa chat` 里 `/image shot.png` 也验了 ——
下一句答出红蓝两色，再下一句问 `1+1` 答 `2`（图**只跟了那一句**），退出时正常给了会话 id。

这条比"HTTP 200"强得多：它证明的不只是"端点收下了请求体"，而是**对面真把图喂给了模型**。
而且它**只能手工跑** —— 假端点只会照着脚本回话（`tests/fake_openai.py` 就是那么写的），
自动化能证明的只是"我们发出去的形状对"。清单里加了这一条
（[ARCHITECTURE.md §7.1](docs/ARCHITECTURE.md#71-真机冒烟不连真模型的代价得用手工补回来) 第 5 点）。

这一轮之后测试是 **316 条**，全绿，进程内代码覆盖率 97%（总 80% —— 比上一轮低一个点是
**分母变大**：`cli.py` 新加的那几十行跑在子进程里，进程内的覆盖率看不见它们。这也是
贴图的核心要单独切成 `attachments.py` 的原因）。

### 已知限制

完整清单见 [ARCHITECTURE.md §6](docs/ARCHITECTURE.md#6-已知限制诚实清单)，这里是摘要：

- **`local` 模式无隔离** —— `subprocess.run(shell=True)` 直接跑在宿主机，防线只有审批。
- **管道里写操作全被拒** —— 没人能点头就等于不批，这是有意往安全那边倒。
- **profile 启动期选定**，REPL 里不能切（会说不清"这条会话到底是谁在说话"）。
- **提示词零测试** —— 装配测得到，"这句话到底有没有用"测不到，靠人用。
  §7.1 的手工冒烟能证明链路通，证明不了提示词好。
- **多模态只做了输入侧** —— `--image` / `/image` 能贴图给模型看；工具**返回**图
  （多模态输出）没做，那得工具自己产出内容块。贴过的图还会跟着会话一直重发
  （进了 checkpoint，每轮都带），目前没有"把某张图从历史里去掉"的命令。
- **`remote` 模式只给提示不接通** —— 没账号、没计费、没镜像。
- ~~`interrupt_on` 里写错工具名静默不生效~~ —— 已修，见上面「修复」第二条。
- ~~MCP 工具表会撑爆系统提示，工具过滤没做~~ —— 已做，见上面 ⑪。
- ~~MCP 工具到手调不动~~ —— 已修，见上面 ⑪。
- ~~多模态输入没入口~~ —— 已做，见上面 ⑫。

### 未引入（明确不做，不是欠债）

`docs/INDEX-COVERAGE.md` §6 把 **25** 项未引入按原因归成六类：要有服务端 14、
是独立子系统 4、会拆掉已有的边界 3、要额外的模型调用 2、面向图形界面 1、是另一个产品 1。
其中原本有两条值得回头看，现在只剩一条：远程沙箱（`sandboxes.md`）。
MCP 工具过滤（`code/mcp-tools.md`）已经做了 —— 而且回头看它值不值，答案是"值，
但值的地方跟预想的不一样"：过滤本身半天就写完了，真花时间的是"筛掉的东西得能被看见"，
以及写完单测之后**拿真模型跑一次才发现工具根本调不动**。

顺带修了这张统计表本身：它是照印象写的，和逐行点一遍差出十几篇（"部分引入"被记成了
"未引入"）。现在表里的数**是数出来的**，归堆那几组也点名到篇、加起来正好 25 ——
一份声称"逐篇对照"的文档，自己的合计对不上是最没有说服力的一种错。

---

[0.1.0]: https://github.com/Htojk/my-assistant-agent/releases/tag/v0.1.0
