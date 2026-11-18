#!/usr/bin/env python3
"""Stand-ins for the two language-model paths of app/llm.py, for scripts/smoke.py (section `llm`). No key, no network.

    python scripts/fake_llm.py cli  [claude flags…]      behaves like `claude -p … --output-format json` (prompt on stdin)
    python scripts/fake_llm.py server --port 8098         a minimal Anthropic Messages API (POST /v1/messages)

Both answer `{"answer": "…"}` (or a JSON map for wording prompts) and obey markers in the prompt's question line, so the
smoke can drive every branch of the router: `[slow:S]` sleeps S seconds first, `[fail]` errors (exit 1 / HTTP 500),
`[nonjson]` replies with prose. Prefix a marker with `sdk_` or `cli_` (`[sdk_slow:5]`, `[cli_fail]`) to make only that
path misbehave. The server's base latency is
FAKE_LLM_LATENCY seconds (default 0.25), the CLI's FAKE_CLI_LATENCY (default 0.1).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time


def _question(prompt: str) -> str:
    m = re.findall(r"^Question:(.*)$", prompt, re.M)
    return m[-1] if m else prompt[-400:]


def _reply(prompt: str, path: str) -> tuple[str, str | None]:
    """(reply text, error or None) for a prompt on one path (sdk | cli), after the markers' sleep."""
    q = _question(prompt)
    for m in re.finditer(r"\[(?:(sdk|cli)_)?slow:([\d.]+)\]", q):
        if m.group(1) in (None, path):
            time.sleep(float(m.group(2)))
    if "[fail]" in q or f"[{path}_fail]" in q:
        return "", "fake failure"
    if "[nonjson]" in q or f"[{path}_nonjson]" in q:
        return "I would rather talk than return JSON.", None
    if "Findings:" in prompt and "Question:" not in prompt:  # Vela's wording prompt: echo each finding's text
        try:
            items = json.loads(prompt.split("Findings:", 1)[1])
            return json.dumps({f["id"]: f["text"] for f in items}), None
        except Exception:  # noqa: BLE001
            pass
    q = re.sub(r"\[[^\]]*\]", "", q).strip()
    nums = re.findall(r"\d+(?:\.\d+)?", q)
    return json.dumps({"answer": f"(fake {path}) You asked: {q[:160]}" + (f" — numbers {', '.join(nums)}" if nums else "")}), None


def cli() -> int:
    prompt = sys.stdin.read()
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "fake"
    time.sleep(float(os.environ.get("FAKE_CLI_LATENCY", "0.1")))
    text, err = _reply(prompt, "cli")
    if err:
        print(err, file=sys.stderr)
        return 1
    print(json.dumps({"type": "result", "is_error": False, "result": text, "total_cost_usd": 0.0,
                      "modelUsage": {f"{model}": {}}, "duration_api_ms": 0}))
    return 0


def server(port: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            prompt = "".join(c if isinstance(c, str) else "".join(b.get("text", "") for b in c)
                             for c in (m.get("content") for m in body.get("messages", [])))
            time.sleep(float(os.environ.get("FAKE_LLM_LATENCY", "0.25")))
            text, err = _reply(prompt, "sdk")
            if err:
                out, code = {"type": "error", "error": {"type": "api_error", "message": err}}, 500
            else:
                out, code = {"id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model"),
                             "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                             "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4}}, 200
            raw = json.dumps(out).encode()
            try:
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):  # the client timed out and hung up
                pass

    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        server(int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8098)
    else:
        sys.exit(cli())
