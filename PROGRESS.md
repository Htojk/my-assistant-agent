# 进度看板

> 本文件由 `.claude/harness/progress.py` 自动生成，**请勿手改**。
> 改状态用：`status` / `next` / `start <id>` / `done <id>` / `block <id> --reason "…"`

**进度：3/10**　`██████░░░░░░░░░░░░░░`
最后更新：2026-10-16T08:20:05Z

| # | 提交 | 任务 | 状态 | commit |
|---|---|---|---|---|
| C1 | `docs: 开发计划——需求矩阵、目录结构、提交计划` | 开发计划与项目骨架 | ✅ done | `c9cb763` |
| C2 | `chore(harness): Claude Code 续跑 harness——任务台账与三个 hook` | Claude Code 续跑 harness | ✅ done | `a2495b0` |
| C3 | `feat(cli): 最小闭环——配置加载、沙箱后端工厂、CLI 骨架` | 最小闭环：配置 / 沙箱后端 / CLI | ✅ done | `681791560d94b29b9eeaef1afaddbd0f6a32b443` |
| C4 | `feat(skills): 技能目录发现与内置技能` | 技能加载与三个内置技能 | 🔨 in_progress | `—` |
| C5 | `feat(mcp): MCP 配置读取、校验与动态加载` | MCP 可配置与动态加载 | · pending | `—` |
| C6 | `feat(agent): writer / coder / planner 三种 profile 与子代理` | 三种能力 profile 与子代理 | · pending | `—` |
| C7 | `feat(session): SqliteSaver 会话持久化与 --resume` | 会话持久化与恢复 | · pending | `—` |
| C8 | `feat(approval): 危险工具的 HITL 审批循环` | HITL 审批循环 | · pending | `—` |
| C9 | `test: 单元、集成与冒烟测试补全` | 单元与冒烟测试补全 | · pending | `—` |
| C10 | `docs: INDEX.md 逐篇技术对照与架构说明` | INDEX.md 逐篇技术对照 | · pending | `—` |

## 当前

**C4 — 技能加载与三个内置技能**（in_progress）

- 提交信息：feat(skills): 技能目录发现与内置技能
- 交付物：src/maa/skills.py, skills/article-writing/SKILL.md, skills/coding/SKILL.md, skills/task-planning/SKILL.md, tests/test_skills.py
- 验收：多源技能目录按优先级去重；agent 能用 read_file 读到 SKILL.md（无 load_skill 工具）
- 备注：技能目录低→高优先级：~/.maa/skills → ~/.claude/skills → ~/.agents/skills → <project>/.maa/skills → <project>/.agents/skills → config.skills.extra_dirs
