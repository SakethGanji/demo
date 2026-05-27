"""End-to-end test for $vars SECRET-REDACTION in UI-visible execution data.

Companion to test_vars_e2e.py (which tests substitution/resolution).
This one specifically tests the UI leak fix:

  1. Spins up a mock HTTP API that ONLY accepts requests carrying a
     specific secret token in ?api_key=<token>.
  2. Registers that token as `type=secret` in the engine.
  3. Hits POST /execution-stream/adhoc — the SAME endpoint the UI's
     "Run with Payload" button calls — with a workflow whose HttpRequest
     URL embeds {{ $vars.<KEY> }} in the query param.
  4. Asserts:
       a. The mock upstream actually received the REAL token (so the
          workflow authenticated — substitution still works end-to-end).
       b. The SSE event stream's `requestUrl` metric shows `***` not the
          plaintext token (so the UI execution-log never sees it).
       c. No event in the stream contains the plaintext token anywhere.

Engine must be running at BASE_URL. Run:
    venv/bin/python scripts/test_vars_redaction_e2e.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx

BASE_URL = os.environ.get("WORKFLOW_BASE_URL", "http://localhost:8002")
SECRET_TOKEN = "sk_live_REDACT_ME_abcdef0123456789"
KEY = f"REDACT_E2E_TOKEN_{int(time.time())}"
ENV = "default"

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "venv" / "bin" / "python"


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_mock_app(port: int) -> Path:
    p = REPO_ROOT / "scripts" / "_mock_token_api.py"
    p.write_text(
        f"""
from fastapi import FastAPI, HTTPException, Request
import uvicorn

EXPECTED = {SECRET_TOKEN!r}
RECEIVED: list[str] = []

app = FastAPI()

@app.get("/protected")
async def protected(request: Request):
    key = request.query_params.get("api_key", "")
    RECEIVED.append(key)
    if key != EXPECTED:
        raise HTTPException(401, detail="bad token")
    return {{"ok": True, "received_key_length": len(key)}}

@app.get("/_received")
async def received():
    return {{"keys": RECEIVED}}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
"""
    )
    return p


def wait_for(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for {url}")


def main() -> int:
    mock_port = free_port()
    print(f"[setup] starting mock token API on :{mock_port}")
    mock_app = write_mock_app(mock_port)
    mock_proc = subprocess.Popen(
        [str(VENV_PY), str(mock_app)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    var_id: int | None = None

    try:
        wait_for(f"http://127.0.0.1:{mock_port}/_received")
        print(f"[setup] mock ready, engine assumed up at {BASE_URL}")
        wait_for(f"{BASE_URL}/health")

        with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
            # 1. Register the secret
            print(f"\n[1] POST /api/variables — store {KEY} as type=secret")
            r = client.post(
                "/api/variables",
                json={
                    "key": KEY,
                    "value": SECRET_TOKEN,
                    "type": "secret",
                    "environment": ENV,
                    "team_id": "default",
                },
            )
            r.raise_for_status()
            created = r.json()
            var_id = created["id"]
            assert created["value"] is None, "create response leaked secret"
            print(f"    created id={var_id}, masked in response: ok")

            # 2. Build the adhoc workflow — same shape the UI would build
            url_template = (
                f"http://127.0.0.1:{mock_port}/protected"
                f"?api_key={{{{ $vars.{KEY} }}}}"
            )
            print(f"\n[2] adhoc workflow URL template: {url_template}")
            workflow = {
                "name": "vars-redaction-e2e",
                "environment": ENV,
                "nodes": [
                    {
                        "name": "Start",
                        "type": "Start",
                        "parameters": {},
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "name": "Call",
                        "type": "HttpRequest",
                        "parameters": {
                            "url": url_template,
                            "method": "GET",
                            "headers": [],
                            "responseType": "json",
                        },
                        "position": {"x": 200, "y": 0},
                    },
                ],
                "connections": [
                    {"source_node": "Start", "target_node": "Call"},
                ],
            }

            # 3. Hit the same endpoint the UI uses
            print(f"\n[3] POST /execution-stream/adhoc (same endpoint as UI)")
            events: list[dict] = []
            with client.stream(
                "POST",
                "/execution-stream/adhoc",
                json=workflow,
                headers={"Accept": "text/event-stream"},
            ) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        events.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
            print(f"    streamed {len(events)} events")

            # 4a. Upstream actually got the REAL token (auth still works)
            received = httpx.get(
                f"http://127.0.0.1:{mock_port}/_received", timeout=5.0
            ).json()["keys"]
            assert SECRET_TOKEN in received, (
                f"upstream never saw real token — keys={received!r}"
            )
            print(f"\n[4a] mock upstream got real token ({len(received)} request(s)): OK")

            # 4b. SSE stream never carried the plaintext token
            full_dump = json.dumps(events)
            assert SECRET_TOKEN not in full_dump, (
                "LEAK: plaintext token found somewhere in SSE event stream"
            )
            print(f"[4b] plaintext token NOT in SSE payload: OK")

            # 4c. Specifically, the HttpRequest's requestUrl metric is redacted
            http_complete = [
                e
                for e in events
                if e.get("nodeName") == "Call"
                and e.get("metrics", {}).get("requestUrl")
            ]
            assert http_complete, (
                f"no Call node completion event with requestUrl seen. "
                f"events: {[e.get('type') for e in events]}"
            )
            displayed = http_complete[-1]["metrics"]["requestUrl"]
            assert "***" in displayed, f"requestUrl not redacted: {displayed!r}"
            assert SECRET_TOKEN not in displayed
            print(f"[4c] requestUrl in SSE = {displayed!r}: OK")

            # 4d. Status code in metrics confirms the upstream accepted us
            status = http_complete[-1]["metrics"].get("responseStatusCode")
            assert status == 200, (
                f"upstream responded {status} — token wasn't substituted correctly"
            )
            print(f"[4d] upstream returned 200 (token was substituted): OK")

        print("\nALL CHECKS PASSED")
        return 0

    finally:
        # Clean up the variable so the engine's state doesn't accumulate
        # test rows across reruns.
        if var_id is not None:
            try:
                httpx.delete(f"{BASE_URL}/api/variables/{var_id}", timeout=5.0)
                print(f"[cleanup] deleted variable id={var_id}")
            except Exception as e:
                print(f"[cleanup] variable delete failed: {e}")
        mock_proc.terminate()
        try:
            mock_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock_proc.kill()
        try:
            mock_app.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        sys.exit(1)
