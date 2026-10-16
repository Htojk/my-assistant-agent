# 进度看板

> 本文件由 `.claude/harness/progress.py` 自动生成，**请勿手改**。
> 改状态用：`status` / `next` / `start <id>` / `done <id>` / `block <id> --reason "…"`

**进度：1/10**　`██░░░░░░░░░░░░░░░░░░`
最后更新：2026-10-16T07:47:58Z

| # | 提交 | 任务 | 状态 | commit |
|---|---|---|---|---|
| C1 | `docs: 开发计划——需求矩阵、目录结构、提交计划` | 开发计划与项目骨架 | ✅ done | `c9cb763` |
| C2 | `chore(harness): Claude Code 续跑 harness——任务台账与三个 hook` | Claude Code 续跑 harness | 🔨 in_progress | `—` |
| C3 | `feat(cli): 最小闭环——配置加载、沙箱后端工厂、CLI 骨架` | 最小闭环：配置 / 沙箱后端 / CLI | · pending | `—` |
| C4 | `feat(skills): 技能目录发现与内置技能` | 技能加载与三个内置技能 | · pending | `—` |
| C5 | `feat(mcp): MCP 配置读取、校验与动态加载` | MCP 可配置与动态加载 | · pending | `—` |
| C6 | `feat(agent): writer / coder / planner 三种 profile 与子代理` | 三种能力 profile 与子代理 | · pending | `—` |
| C7 | `feat(session): SqliteSaver 会话持久化与 --resume` | 会话持久化与恢复 | · pending | `—` |
| C8 | `feat(approval): 危险工具的 HITL 审批循环` | HITL 审批循环 | · pending | `—` |
| C9 | `test: 单元、集成与冒烟测试补全` | 单元与冒烟测试补全 | · pending | `—` |
| C10 | `docs: INDEX.md 逐篇技术对照与架构说明` | INDEX.md 逐篇技术对照 | · pending | `—` |

## 当前

**C2 — Claude Code 续跑 harness**（in_progress）

- 提交信息：chore(harness): Claude Code 续跑 harness——任务台账与三个 hook
- 交付物：.claude/harness/tasks.json, .claude/harness/progress.py, .claude/settings.json, PROGRESS.md
- 验收：SessionStart 注入当前任务；Stop 在台账未清零时返回 decision=block 并喂回下一个任务；progress.py 五个子命令可用
- 备注：jq 未安装，hook 的 stdin JSON 必须由 progress.py 用 Python 解析。
