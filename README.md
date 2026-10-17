# my-assistant-agent

跑在终端里的通用助手 agent：**能写文章、能写代码、能做任务规划**。
可加载技能（skills）、可插拔沙箱、MCP 服务从配置文件动态接入。

基于 [`deepagents`](https://github.com/langchain-ai/deepagents) SDK 构建。

---

## 快速开始

```bash
# 1. 安装（含 MCP 支持）
pip install -e ".[dev]"

# 2. 配置模型（任选其一）
export OPENAI_BASE_URL="https://your-endpoint/v1"
export OPENAI_API_KEY="sk-..."
export MAA_MODEL="openai:gpt-5.5"        # 可选，覆盖配置文件默认值

# 3. 跑
maa run "在 out/ 下写一篇 800 字的文章，主题是 Agent 的上下文工程"
maa chat                                  # 交互式
```

## 命令一览

| 命令 | 说明 |
|---|---|
| `maa run "<prompt>"` | 一次性执行 |
| `maa chat [--resume <thread>]` | 交互式 REPL |
| `maa --agent writer\|coder\|planner\|general` | 指定能力 profile |
| `maa config show\|init` | 配置管理 |
| `maa mcp list\|test` | MCP 服务管理 |
| `maa skills list` | 技能清单 |
| `maa doctor` | 环境自检 |

## 三种能力

| profile | 用途 | 说明 |
|---|---|---|
| `writer` | 写文章 | 排除 shell 工具，专注读写文件 |
| `coder` | 编码 | 带 `execute`，可跑命令、跑测试 |
| `planner` | 任务规划 | 强制先列 `write_todos` 再执行 |
| `general` | 通用 | 默认，全工具 |

## 配置

主配置：`~/.maa/config.toml`（可用 `MAA_HOME` 重定位）
MCP 配置：项目根 `.mcp.json`，或 `~/.maa/mcp.json`
技能目录（低 → 高优先级）：

```
~/.maa/skills/   ~/.claude/skills/   ~/.agents/skills/
<project>/.maa/skills/   <project>/.agents/skills/
```

MCP 工具多了会全塞进系统提示，所以 `[mcp] include / exclude` 按名字筛一遍。
匹配的是**带服务前缀的全名**（`demo_echo`），`*` 通配，两边都命中时 **exclude 赢**：

```toml
[mcp]
include = ["*"]                  # 空着表示都留
exclude = ["*_delete", "demo_*"]  # 去掉删除类、去掉 demo 整个服务
```

被筛掉的名字会在启动时印一行、`maa mcp test` 里也标着 —— 过滤是**看得见**的，
不会静默少几个工具。

## ⚠️ 安全提示

默认沙箱模式 `local` **直接在你的机器上执行命令**（`subprocess.run(shell=True)`），
**没有任何隔离**。`virtual_mode` 对 shell 无效。

- 默认 `approval.mode = "manual"`：`write_file` / `edit_file` / `delete` / `execute` 都会先问你。
  问的时候 `y` 批准、`a` 这次会话里后面都不问、**回车是不批准**（直接写一行字则是拒绝的理由，
  会转给模型）。
- 管道 / CI 里没人能点头，所以写操作会被一律拒掉（`maa run` 接空 stdin 就是这条路）。
  要在自动化里跑到底，得显式设成 `approval.mode = "yolo"`。
- 想更安全：把 `backend.mode` 改成 `readonly`，或接远程沙箱（`remote`）。
- 别在你不信任的目录下用 `approval.mode = "yolo"`。

## 开发

```bash
pytest                # 全量测试
maa doctor            # 环境自检
```

文档：
[`docs/PLAN.md`](docs/PLAN.md) 开发计划（做什么、不做什么）、
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 架构与关键决定的代价、
[`docs/INDEX-COVERAGE.md`](docs/INDEX-COVERAGE.md) 逐篇技术对照（引了什么、为什么）、
[`CHANGELOG.md`](CHANGELOG.md) 变更记录。

## License

MIT
