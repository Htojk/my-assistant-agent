"""一个假的 OpenAI 兼容服务，给端到端和会话恢复的测试用。

假模型只能证明"装配对"，证明不了 配置 → provider → HTTP → 流式解析 → 工具执行
这一路是通的 —— 所以这里真发 HTTP，只是对面是自己人。跑完不需要任何真密钥。

配套的 `fake_openai` fixture 在 conftest 里（夹具放那儿才是全局可见的）。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def sse_tool_call(prose: str = "") -> str:
    """调工具的那一轮。`prose` 是"边说边调"里那句话 —— 真模型常常这么发。"""
    parts = [_chunk({"role": "assistant", "content": ""})]
    for i in range(0, len(prose), 8):
        parts.append(_chunk({"content": prose[i : i + 8]}))
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


def sse_text(text: str) -> str:
    parts = [_chunk({"role": "assistant", "content": ""})]
    for i in range(0, len(text), 8):
        parts.append(_chunk({"content": text[i : i + 8]}))
    parts.append(_chunk({}, finish="stop"))
    return "".join(parts) + "data: [DONE]\n\n"


class Handler(BaseHTTPRequestHandler):
    """第一轮让模型调 write_file，第二轮回一句收尾话。

    收到的请求体全攒在 `requests_seen` 里 —— 想断言"对面到底看见了什么"
    （比如恢复出来的历史有没有真的发过去）就看它。

    `prose` / `final_text` 两个旋钮用来复现别的回话形状（默认就是上面那句）：
    真模型经常**边说边调**（旁白和 `write_file` 在同一条消息里），收尾那条消息
    则是空的 —— 那是"这一轮到底有没有文本回复"判断里最容易看错的一种形状。
    """

    protocol_version = "HTTP/1.1"
    requests_seen: list[dict] = []
    # 调工具那一轮里，模型顺带说的那句话
    prose: str = ""
    # 收尾那一轮说的话；空串 = 这一轮一个字都没说
    final_text: str = FINAL_TEXT

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
            script = sse_tool_call(self.prose) if turns == 0 else sse_text(self.final_text)
            self._send(200, "text/event-stream", script)
            return

        if turns == 0:
            message = {
                "role": "assistant",
                "content": self.prose,
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
            message, finish = {"role": "assistant", "content": self.final_text}, "stop"

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


def serve() -> tuple[ThreadingHTTPServer, str]:
    """起一个服务，返回 `(服务对象, base_url)`。端口交给系统分配。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"
