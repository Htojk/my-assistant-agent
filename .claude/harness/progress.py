#!/usr/bin/env python3
"""my-assistant-agent 的 Claude Code 续跑 harness。

`.claude/harness/tasks.json` 是任务台账的**唯一事实来源**，本脚本是它唯一的读写入口。

人 / agent 用：

    python .claude/harness/progress.py status
    python .claude/harness/progress.py next
    python .claude/harness/progress.py start C3
    python .claude/harness/progress.py done  C3 --commit abc1234 --note "跑通了"
    python .claude/harness/progress.py block C3 --reason "缺 API key"

hook 入口（由 `.claude/settings.json` 调用，走 stdin/stdout 的 JSON 协议）：

    hook-session-start   会话开始 → 注入进度，解决「新会话不知道做到哪」
    hook-stop            agent 想停 → 有未完成任务就 block，解决「做完一个就停摆」
    hook-post-tool       写文件后 → 命中当前任务交付物时提醒回写台账

环境变量：

    MAA_HARNESS_OFF=1          本次不拦 Stop（想自己收工）
    MAA_HARNESS_MAX_BLOCKS=N   连续拦截上限，默认 12；超了就放行，避免死循环
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HARNESS_DIR.parents[1]
LEDGER = HARNESS_DIR / "tasks.json"
STATE = HARNESS_DIR / ".state.json"
BOARD = PROJECT_ROOT / "PROGRESS.md"

DEFAULT_MAX_BLOCKS = 12

STATUS_ICON = {
    "done": "✅",
    "in_progress": "🔨",
    "blocked": "⛔",
    "pending": "·",
}


# --------------------------------------------------------------------------- #
# 台账读写
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load() -> dict:
    with LEDGER.open(encoding="utf-8") as fh:
        return json.load(fh)


def save(data: dict) -> None:
    data["updated_at"] = _now()
    tmp = LEDGER.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(LEDGER)


def load_state() -> dict:
    try:
        with STATE.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        with STATE.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def reset_guard() -> None:
    """任务状态一有推进就重置拦截计数与提醒去重。"""
    save_state({"consecutive_blocks": 0, "reminded": []})


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def find(data: dict, tid: str) -> dict:
    for task in data["tasks"]:
        if task["id"].lower() == tid.lower():
            return task
    known = ", ".join(t["id"] for t in data["tasks"])
    raise SystemExit(f"未知任务 id：{tid}（台账里只有：{known}）")


def unfinished(data: dict) -> list[dict]:
    return [t for t in data["tasks"] if t.get("status") != "done"]


def current(data: dict) -> dict | None:
    """进行中的任务；没有则取第一个 pending。blocked 不参与。"""
    for task in data["tasks"]:
        if task.get("status") == "in_progress":
            return task
    for task in data["tasks"]:
        if task.get("status") == "pending":
            return task
    return None


def brief(task: dict) -> list[str]:
    lines = [f"{task['id']} — {task['title']}（{task.get('status')}）"]
    if task.get("commit"):
        lines.append(f"  提交信息：{task['commit']}")
    if task.get("deliverables"):
        lines.append(f"  交付物：{', '.join(task['deliverables'])}")
    if task.get("acceptance"):
        lines.append(f"  验收：{task['acceptance']}")
    if task.get("notes"):
        lines.append(f"  备注：{task['notes']}")
    return lines


# --------------------------------------------------------------------------- #
# 人可读看板
# --------------------------------------------------------------------------- #
def render_board(data: dict) -> str:
    tasks = data["tasks"]
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") == "done")
    width = 20
    filled = round(width * done / total) if total else width
    bar = "█" * filled + "░" * (width - filled)

    out = [
        "# 进度看板",
        "",
        "> 本文件由 `.claude/harness/progress.py` 自动生成，**请勿手改**。",
        "> 改状态用：`status` / `next` / `start <id>` / `done <id>` / `block <id> --reason \"…\"`",
        "",
        f"**进度：{done}/{total}**　`{bar}`",
        f"最后更新：{data.get('updated_at', '—')}",
        "",
        "| # | 提交 | 任务 | 状态 | commit |",
        "|---|---|---|---|---|",
    ]
    for task in tasks:
        icon = STATUS_ICON.get(task.get("status", "pending"), "?")
        out.append(
            f"| {task['id']} | `{task.get('commit') or '—'}` | {task['title']} "
            f"| {icon} {task.get('status')} | `{task.get('sha') or '—'}` |"
        )

    cur = current(data)
    out += ["", "## 当前", ""]
    if cur:
        out += [f"**{cur['id']} — {cur['title']}**（{cur.get('status')}）", ""]
        for item in brief(cur)[1:]:
            out.append("- " + item.strip())
    else:
        out.append("（没有进行中 / 待办任务）")

    blocked = [t for t in tasks if t.get("status") == "blocked"]
    if blocked:
        out += ["", "## 受阻", ""]
        for task in blocked:
            out.append(f"- **{task['id']}** {task['title']} — {task.get('notes') or '（未记录原因）'}")

    if total and done == total:
        out += ["", "---", "", "🎉 **全部任务已完成**，Stop 守卫已放行。"]

    out.append("")
    return "\n".join(out)


def write_board(data: dict) -> None:
    BOARD.write_text(render_board(data), encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- #
# hook 输出协议
# --------------------------------------------------------------------------- #
def emit(payload: dict) -> None:
    """hook 的 stdout 必须是单个 JSON 对象。用 ASCII 转义，规避 Windows 控制台编码。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.stdout.write("\n")


def read_payload() -> dict:
    """读 hook 的 stdin。

    这里必须读**原始字节**再显式按 UTF-8 解码，不能走文本模式：
    Windows 上 sys.stdin 的默认编码是 gbk、errors=surrogateescape，
    而 Claude Code 送来的是 UTF-8。只要路径含非 ASCII 字符（中文目录名、
    中文用户名都算），文本模式就会把它解成别的字符且**不抛异常**，
    于是 relative_to() 得到错的相对路径、fnmatch 匹配不上，
    PostToolUse 钩子就静默失效了 —— 这正是 selftest 要守住的东西。
    （本项目最初放在 E:\\学习\\... 下，是真踩到过这个坑才加的防护。）
    """
    stream = getattr(sys.stdin, "buffer", None)
    try:
        raw = stream.read() if stream is not None else sys.stdin.read().encode("utf-8", "replace")
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    for codec in ("utf-8", "gbk"):
        try:
            return json.loads(raw.decode(codec))
        except (UnicodeDecodeError, ValueError):
            continue
    return {}


def hook_session_start(_args: argparse.Namespace) -> None:
    try:
        data = load()
    except Exception:
        return  # harness 自身出问题，绝不阻塞开发
    reset_guard()

    tasks = data["tasks"]
    done = sum(1 for t in tasks if t.get("status") == "done")

    lines = [
        f"[harness] 项目 {data.get('project', '?')}：{done}/{len(tasks)} 个任务已完成。",
        "台账：.claude/harness/tasks.json　　看板：PROGRESS.md",
        "开工前先认领「当前任务」；完工后必须回写台账，否则 Stop 守卫会拦住你。",
        "",
    ]

    cur = current(data)
    if cur:
        lines.append("▸ 当前任务")
        lines += brief(cur)
    else:
        lines.append("▸ 当前任务：无（已全部完成，或剩余的全部受阻）")

    pending = [t for t in tasks if t.get("status") == "pending"]
    if pending:
        lines += ["", "▸ 后续排期"]
        lines += [f"  {t['id']} — {t['title']}" for t in pending[:4]]

    blocked = [t for t in tasks if t.get("status") == "blocked"]
    if blocked:
        lines += ["", "▸ 受阻（先解决这些）"]
        lines += [f"  {t['id']} — {t['title']}：{t.get('notes') or '（未记录原因）'}" for t in blocked]

    lines += [
        "",
        "▸ 台账操作",
        "  python .claude/harness/progress.py next",
        "  python .claude/harness/progress.py start <id>",
        '  python .claude/harness/progress.py done <id> --commit <sha> --note "<一句话>"',
        '  python .claude/harness/progress.py block <id> --reason "<原因>"',
    ]

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "\n".join(lines),
            }
        }
    )


def hook_stop(_args: argparse.Namespace) -> None:
    if os.environ.get("MAA_HARNESS_OFF") == "1":
        return
    try:
        data = load()
    except Exception:
        return

    left = unfinished(data)
    if not left:
        reset_guard()
        emit({"systemMessage": "[harness] 台账已清零，停止守卫放行。"})
        return

    try:
        cap = int(os.environ.get("MAA_HARNESS_MAX_BLOCKS", DEFAULT_MAX_BLOCKS))
    except ValueError:
        cap = DEFAULT_MAX_BLOCKS

    state = load_state()
    rounds = int(state.get("consecutive_blocks", 0)) + 1
    state["consecutive_blocks"] = rounds
    save_state(state)

    if rounds > cap:
        emit(
            {
                "systemMessage": (
                    f"[harness] 已连续拦截 {rounds - 1} 次，达到上限 {cap}，本次放行。"
                    f"仍有 {len(left)} 个任务未完成 —— 请人工确认是否卡住。"
                )
            }
        )
        return

    cur = current(data)
    target = cur["id"] if cur else "<id>"
    lines = [f"[harness] 不要停：还有 {len(left)} 个任务未完成（第 {rounds}/{cap} 次拦截）。", ""]

    if cur:
        lines.append("▸ 接着做")
        lines += brief(cur)
    else:
        lines.append("▸ 剩余任务全部 blocked，没有可推进项。请解决受阻原因，或把任务改回 pending。")

    lines += [
        "",
        "▸ 收尾动作（做完任何一个都要回写台账）",
        f'  python .claude/harness/progress.py done {target} --commit <sha> --note "<一句话>"',
        f'  python .claude/harness/progress.py block {target} --reason "<原因>"',
        "  python .claude/harness/progress.py status",
        "",
        "台账清零（全部 done 或 blocked）后，守卫会自动放行。",
    ]
    emit({"decision": "block", "reason": "\n".join(lines)})


def hook_post_tool(_args: argparse.Namespace) -> None:
    payload = read_payload()
    if payload.get("tool_name") not in ("Write", "Edit", "NotebookEdit"):
        return

    raw_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not raw_path:
        return

    try:
        rel = Path(raw_path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except (OSError, ValueError):
        return

    try:
        data = load()
    except Exception:
        return

    cur = current(data)
    if not cur or cur.get("status") != "in_progress":
        return
    if not any(fnmatch.fnmatch(rel, pat) for pat in cur.get("deliverables", [])):
        return

    key = f"{cur['id']}:{rel}"
    state = load_state()
    reminded = set(state.get("reminded", []))
    if key in reminded:
        return
    reminded.add(key)
    state["reminded"] = sorted(reminded)
    save_state(state)

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"[harness] 刚写的 {rel} 是任务 {cur['id']}（{cur['title']}）的交付物之一。"
                    "该任务做完后记得回写台账：\n"
                    f'  python .claude/harness/progress.py done {cur["id"]} '
                    '--commit <sha> --note "<一句话>"\n'
                    f"  验收标准：{cur.get('acceptance', '—')}"
                ),
            }
        }
    )


# --------------------------------------------------------------------------- #
# 人用子命令
# --------------------------------------------------------------------------- #
def cmd_status(_args: argparse.Namespace) -> None:
    data = load()
    write_board(data)
    print(render_board(data))
    print(f"看板已写入 {BOARD}")


def cmd_next(_args: argparse.Namespace) -> None:
    data = load()
    cur = current(data)
    if not cur:
        print("没有可推进的任务。跑 status 看全貌。")
        return
    print("\n".join(brief(cur)))


def cmd_start(args: argparse.Namespace) -> None:
    data = load()
    task = find(data, args.id)
    for other in data["tasks"]:
        if other is not task and other.get("status") == "in_progress":
            other["status"] = "pending"
    task["status"] = "in_progress"
    if args.note:
        task["notes"] = args.note
    save(data)
    reset_guard()
    write_board(data)
    print(f"▶ {task['id']} 开始")
    print("\n".join(brief(task)))


def cmd_done(args: argparse.Namespace) -> None:
    data = load()
    task = find(data, args.id)
    task["status"] = "done"
    if args.commit:
        task["commit"] = args.commit
    if args.sha:
        task["sha"] = args.sha
    if args.note:
        task["notes"] = args.note
    save(data)
    reset_guard()
    print(f"✅ {task['id']} — {task['title']} 完成")

    if not args.no_advance:
        nxt = next((t for t in data["tasks"] if t.get("status") == "pending"), None)
        if nxt:
            nxt["status"] = "in_progress"
            save(data)
            print(f"▶ 自动接续 {nxt['id']} — {nxt['title']}")
            print("\n".join(brief(nxt)))
        else:
            left = unfinished(data)
            if left:
                print(f"⚠ 剩余 {len(left)} 个任务全部 blocked，未自动接续。")
            else:
                print("🎉 全部任务已完成。")
    write_board(data)


def cmd_block(args: argparse.Namespace) -> None:
    data = load()
    task = find(data, args.id)
    task["status"] = "blocked"
    task["notes"] = f"受阻：{args.reason}"
    save(data)
    reset_guard()
    write_board(data)
    print(f"⛔ {task['id']} — {task['title']} 已标记受阻：{args.reason}")


def cmd_unblock(args: argparse.Namespace) -> None:
    data = load()
    task = find(data, args.id)
    task["status"] = "pending"
    task["notes"] = args.note or ""
    save(data)
    write_board(data)
    print(f"· {task['id']} 已回到 pending")


def cmd_sync(_args: argparse.Namespace) -> None:
    data = load()
    write_board(data)
    print(f"看板已按台账重写：{BOARD}")


# --------------------------------------------------------------------------- #
# 自检：hook 静默失效是最危险的故障，这里把它变成会报错的
# --------------------------------------------------------------------------- #
class _FakeStdin:
    """喂给 hook 函数用的假 stdin，只暴露 .buffer（read_payload 读的就是它）。"""

    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


def _capture_hook(fn, stdin_bytes: bytes) -> tuple[str, str]:
    """跑一次真实的 hook 函数，返回 (stdout, stderr)。"""
    old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    out, err = io.StringIO(), io.StringIO()
    try:
        sys.stdin, sys.stdout, sys.stderr = _FakeStdin(stdin_bytes), out, err
        fn(None)
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err
    return out.getvalue(), err.getvalue()


def cmd_selftest(_args: argparse.Namespace) -> None:
    results: list[tuple[bool, str, str]] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        results.append((bool(cond), label, detail))

    # 跑 hook 会动 .state.json（拦截计数 / 提醒去重），自检不该有副作用，先存后还。
    snapshot = STATE.read_bytes() if STATE.exists() else None
    try:
        _run_checks(check)
    finally:
        if snapshot is None:
            STATE.unlink(missing_ok=True)
        else:
            STATE.write_bytes(snapshot)
        reset_guard()

    width = max((len(label) for _, label, _ in results), default=0)
    for ok, label, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<{width}}  {detail}")
    failed = [r for r in results if not r[0]]
    print()
    if failed:
        print(f"✗ {len(failed)}/{len(results)} 项失败 —— harness 有问题，先修它。")
        raise SystemExit(1)
    print(f"✓ {len(results)}/{len(results)} 项通过。")


def _run_checks(check) -> None:
    # --- 1. 台账本身 ---
    data = None
    try:
        data = load()
        check("tasks.json 可解析", True, f"{len(data['tasks'])} 个任务")
    except Exception as exc:
        check("tasks.json 可解析", False, repr(exc))

    if data:
        ids = [t["id"] for t in data["tasks"]]
        check("任务 id 不重复", len(ids) == len(set(ids)), f"{len(ids)} 个")
        valid = {"pending", "in_progress", "blocked", "done"}
        bad = [t["id"] for t in data["tasks"] if t.get("status") not in valid]
        check("状态取值合法", not bad, f"越界：{bad}" if bad else "")
        live = [t["id"] for t in data["tasks"] if t.get("status") == "in_progress"]
        check("in_progress 至多一个", len(live) <= 1, f"当前：{live}")
        missing = [t["id"] for t in data["tasks"] if not t.get("deliverables")]
        check("每个任务都有交付物", not missing, f"缺失：{missing}" if missing else "")

    # --- 2. settings.json 的三个 hook ---
    settings = PROJECT_ROOT / ".claude" / "settings.json"
    try:
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        events = cfg.get("hooks", {})
        for name in ("SessionStart", "Stop", "PostToolUse"):
            check(f"settings.json 注册了 {name}", name in events)
    except Exception as exc:
        check("settings.json 可解析", False, repr(exc))

    # --- 3. 中文路径：UTF-8 stdin 往返（本机默认 gbk，最易踩的坑）---
    cur = current(data) if data else None
    if cur and cur.get("deliverables"):
        rel = cur["deliverables"][0]
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(PROJECT_ROOT / rel)},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        check("payload 含中文" if any(b > 127 for b in raw) else "payload 纯 ASCII", True)
        try:
            got = (
                Path(json.loads(raw.decode("utf-8"))["tool_input"]["file_path"])
                .resolve()
                .relative_to(PROJECT_ROOT)
                .as_posix()
            )
            check("中文路径 UTF-8 往返无损", got == rel, f"{got!r} != {rel!r}" if got != rel else rel)
        except Exception as exc:
            check("中文路径 UTF-8 往返无损", False, repr(exc))

        # --- 4. 真的跑一遍 PostToolUse，且必须输出纯 ASCII ---
        out, err = _capture_hook(hook_post_tool, raw)
        hit = bool(out.strip())
        check("PostToolUse 命中交付物时有输出", hit, "" if hit else "静默失效！")
        check("PostToolUse 无异常", not err.strip(), err.strip()[:120])
        non_ascii = sum(1 for b in out.encode("utf-8") if b > 127)
        check("hook 输出为纯 ASCII", non_ascii == 0, f"{non_ascii} 个非 ASCII 字节")
        if out.strip():
            try:
                check("输出是合法 JSON", isinstance(json.loads(out), dict))
            except ValueError as exc:
                check("输出是合法 JSON", False, repr(exc))
    else:
        check("当前任务有交付物可测", False, "台账里没有 in_progress / pending 任务")

    # --- 5. Stop 守卫 ---
    out, _ = _capture_hook(hook_stop, b"{}")
    try:
        decision = json.loads(out).get("decision")
    except ValueError:
        decision = None
    left = unfinished(data) if data else []
    if left:
        check("有未完成任务时 Stop 拦截", decision == "block", f"decision={decision!r}")
    else:
        check("任务清零时不拦截", decision != "block", f"decision={decision!r}")


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="progress.py",
        description="my-assistant-agent 续跑 harness 的任务台账入口",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="打印进度并用台账重写 PROGRESS.md").set_defaults(fn=cmd_status)
    sub.add_parser("next", help="打印当前该做的任务").set_defaults(fn=cmd_next)
    sub.add_parser("sync", help="只用台账重写 PROGRESS.md").set_defaults(fn=cmd_sync)
    sub.add_parser("selftest", help="自检台账 / hook 配置 / 中文路径 / 钩子输出").set_defaults(
        fn=cmd_selftest
    )

    p_start = sub.add_parser("start", help="认领任务")
    p_start.add_argument("id")
    p_start.add_argument("--note", default="")
    p_start.set_defaults(fn=cmd_start)

    p_done = sub.add_parser("done", help="完成任务（默认自动接续下一个 pending）")
    p_done.add_argument("id")
    p_done.add_argument("--commit", default="", help="提交信息主题，如 'feat(cli): 最小闭环'")
    p_done.add_argument("--sha", default="", help="实际 commit sha")
    p_done.add_argument("--note", default="")
    p_done.add_argument("--no-advance", action="store_true", help="不自动把下一个 pending 置为进行中")
    p_done.set_defaults(fn=cmd_done)

    p_block = sub.add_parser("block", help="标记受阻")
    p_block.add_argument("id")
    p_block.add_argument("--reason", required=True)
    p_block.set_defaults(fn=cmd_block)

    p_unblock = sub.add_parser("unblock", help="解除受阻，回到 pending")
    p_unblock.add_argument("id")
    p_unblock.add_argument("--note", default="")
    p_unblock.set_defaults(fn=cmd_unblock)

    sub.add_parser("hook-session-start", help=argparse.SUPPRESS).set_defaults(fn=hook_session_start)
    sub.add_parser("hook-stop", help=argparse.SUPPRESS).set_defaults(fn=hook_stop)
    sub.add_parser("hook-post-tool", help=argparse.SUPPRESS).set_defaults(fn=hook_post_tool)

    return parser


def main(argv: list[str] | None = None) -> int:
    # 本机 locale 是 cp936；中文输出/看板不被 gbk 卡住。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    args = build_parser().parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
