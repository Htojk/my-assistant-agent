"""端到端：起一个 OpenAI 兼容的假服务，让 `maa run` 走完整条链路。

C3 的验收条件是"真的在磁盘上写出文件"。假模型只能证明装配对，证明不了
配置 → provider → HTTP → 流式解析 → 工具执行 这一路是通的 —— 所以这里
真发 HTTP，只是对面是自己人。跑完不需要任何真密钥。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

ARTICLE = "# 论 X\n\n" + "X 既是问题也是答案。" * 30
TOOL_ARGS = json.dumps({"file_path": "/out/article.md", "content": ARTICLE}, ensure_ascii=False)
FINAL_TEXT = "已经写到 out/article.md 了。"


def _chunk(delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.5",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_tool_call() -> str:
    parts = [_chunk({"role": "assistant", "content": ""})]
    parts.append(
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_fake_1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": ""},
                    }
                ]
            }
        )
    )
    # 参数分片下发 —— 真服务就是这么干的，一次给完就测不出累积逻辑了
    for i in range(0, len(TOOL_ARGS), 32):
        parts.append(
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": TOOL_ARGS[i : i + 32]}}]})
        )
    parts.append(_chunk({}, finish="tool_calls"))
    return "".join(parts) + "data: [DONE]\n\n"


def _sse_text(text: str) -> str:
    parts = [_chunk({"role": "assistant", "content": ""})]
    for i in range(0, len(text), 8):
        parts.append(_chunk({"content": text[i : i + 8]}))
    parts.append(_chunk({}, finish="stop"))
    return "".join(parts) + "data: [DONE]\n\n"


class _Handler(BaseHTTPRequestHandler):
    """第一轮让模型调 write_file，第二轮回一句收尾话。"""

    protocol_version = "HTTP/1.1"
    requests_seen: list[dict] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 —— BaseHTTPRequestHandler 的约定
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.requests_seen.append(body)

        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, "application/json", json.dumps({"error": {"message": self.path}}))
            return

        turns = sum(1 for m in body.get("messages", []) if m.get("role") == "assistant")

        if body.get("stream"):
            self._send(
                200, "text/event-stream", _sse_tool_call() if turns == 0 else _sse_text(FINAL_TEXT)
            )
            return

        if turns == 0:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_fake_1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": TOOL_ARGS},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": FINAL_TEXT}, "stop"

        self._send(
            200,
            "application/json",
            json.dumps(
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-5.5",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                ensure_ascii=False,
            ),
        )

    def _send(self, code: int, ctype: str, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_openai() -> Iterator[str]:
    """返回 base_url。端口交给系统分配，免得跟真服务或并行测试撞车。"""
    _Handler.requests_seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def home_with_config(maa_home: Path) -> Path:
    """照 README 的样子写一份配置 —— 顺带验证模板那套键名是能用的。"""
    maa_home.mkdir(parents=True, exist_ok=True)
    (maa_home / "config.toml").write_text(
        "[models]\n"
        'default = "openai:gpt-5.5"\n'
        "\n"
        "[models.providers.openai]\n"
        'base_url_env = "OPENAI_BASE_URL"\n'
        'api_key_env = "OPENAI_API_KEY"\n'
        "use_responses_api = false\n",
        encoding="utf-8",
    )
    return maa_home


def _run(args: list[str], cwd: Path, home: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "maa", *args],
        cwd=cwd,
        env={
            **os.environ,
            "MAA_HOME": str(home),
            "PYTHONIOENCODING": "utf-8",
            **env,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def test_run_把文章写到磁盘上(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """C3 验收：`maa run "在 out/ 下写一篇文章"` 得真出文件。"""
    result = _run(
        ["run", "在 out/ 下写一篇 800 字的关于 X 的文章"],
        cwd=project,
        home=home_with_config,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    written = project / "out" / "article.md"
    assert written.exists(), result.stdout
    assert written.read_text(encoding="utf-8") == ARTICLE
    assert FINAL_TEXT in result.stdout


def test_run_不往工作目录里倒内部数据(
    project: Path, home_with_config: Path, fake_openai: str
) -> None:
    """大工具结果 / 对话历史该被路由到 StateBackend，不该混进用户项目。"""
    _run(
        ["run", "写一篇文章"],
        cwd=project,
        home=home_with_config,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    leftovers = {p.name for p in project.iterdir()}
    assert leftovers == {".git", "out"}


def test_请求确实走了我们配的端点(project: Path, home_with_config: Path, fake_openai: str) -> None:
    """没走到的端点等于没测 —— 至少得证明对面真收到了带工具的请求。"""
    _run(
        ["run", "写一篇文章"],
        cwd=project,
        home=home_with_config,
        OPENAI_BASE_URL=fake_openai,
        OPENAI_API_KEY="sk-fake",
    )

    assert _Handler.requests_seen, "假服务一个请求都没收到"
    assert any(body.get("tools") for body in _Handler.requests_seen)
