"""Create / drop the throwaway database of the studio's Playwright stack (UI-DESIGN §12.1 step 1).

    python tests/e2e_stack.py up            # TEST_DB_NAME=workflows_test_e2e_<8hex>; creates + migrates; prints one JSON line
    python tests/e2e_stack.py seed --engine-url URL [--state-file PATH]
                                            # after the engine is up: starts tests/fake_refund_api.py, creates the
                                            # "Refund triage" workflow + its table, seeds ONE failed run, points the
                                            # draft back at the OK route; prints one JSON line (see seed())
    python tests/e2e_stack.py down --db NAME [--pid FAKE_SERVER_PID]

Refuses any name that is not ``workflows_test_e2e_<8 hex>`` (so never the live ``workflows`` DB)."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NAME_RE = re.compile(r"^workflows_test_e2e_[0-9a-f]{8}$")
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workflows" / "refund_triage.json"
FAKE_SERVER = Path(__file__).resolve().parent / "fake_refund_api.py"


def _call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"raw": raw.decode(errors="replace")}


def seed(engine_url: str, state_file: str | None) -> dict:
    """Create "Refund triage" (+ its ``refunds`` table) and one failed run. Uses existing endpoints only.

    The fake API is started detached on a free port (kill it with ``down --pid``). The seeded run posts
    amount 900 (> 500) so it takes the HttpRequest branch while the node points at the fake server's ``/drop``
    route (connection closed without a reply -> node fails). Afterwards the draft is updated to ``/ok``."""
    base = engine_url.rstrip("/") + "/api"
    proc = subprocess.Popen([sys.executable, str(FAKE_SERVER)], stdout=subprocess.PIPE, start_new_session=True, text=True)
    port = json.loads(proc.stdout.readline())["port"]  # type: ignore[union-attr]
    fake = f"http://127.0.0.1:{port}"
    ok_url, fail_url = f"{fake}/ok", f"{fake}/drop"

    code, table = _call(base, "POST", "/projects/proj_default/tables", {
        "name": "refunds",
        "columns": [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
                    {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}],
    })
    if code not in (200, 201):
        raise RuntimeError(f"create table failed: {code} {table}")

    raw = FIXTURE.read_text()
    code, wf = _call(base, "POST", "/workflows", json.loads(raw.replace("__REFUND_API_URL__", fail_url)))
    if code != 201:
        raise RuntimeError(f"create workflow failed: {code} {wf}")
    wf_id = wf["id"]
    code, run = _call(base, "POST", f"/workflows/{wf_id}/run",
                      {"input_data": {"order_id": "ord_1042", "amount": 900, "reason": "item arrived damaged"}})
    if code != 200 or run.get("status") != "failed":
        raise RuntimeError(f"expected a failed seeded run, got {code} {json.dumps(run)[:500]}")
    exec_id = run["execution_id"]

    ok_def = json.loads(raw.replace("__REFUND_API_URL__", ok_url))
    code, upd = _call(base, "PUT", f"/workflows/{wf_id}", {"nodes": ok_def["nodes"], "connections": ok_def["connections"]})
    if code != 200:
        raise RuntimeError(f"update workflow failed: {code} {upd}")

    out = {"refund_workflow_id": wf_id, "refund_failed_execution_id": exec_id, "refund_table_id": table.get("id"),
           "fake_api_url": fake, "fake_api_ok_url": ok_url, "fake_api_fail_url": fail_url, "fake_api_pid": proc.pid}
    if state_file and Path(state_file).exists():
        st = json.loads(Path(state_file).read_text())
        st.update(out)
        Path(state_file).write_text(json.dumps(st, indent=2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["up", "down", "seed"])
    ap.add_argument("--db")
    ap.add_argument("--engine-url")
    ap.add_argument("--state-file")
    ap.add_argument("--pid", type=int)
    args = ap.parse_args()
    if args.action == "seed":
        if not args.engine_url:
            print("seed needs --engine-url", file=sys.stderr)
            return 2
        print(json.dumps(seed(args.engine_url, args.state_file)))
        return 0
    name = args.db or os.environ.get("TEST_DB_NAME", "")
    if not NAME_RE.match(name):
        print(f"refusing database name {name!r}", file=sys.stderr)
        return 2
    os.environ["TEST_DB_NAME"] = name
    from tests.integration import conftest as c   # TestDatabase + connection settings (env-overridable)

    db = c.TestDatabase()
    if args.action == "up":
        db.create()
        db.migrate("apply")
        print(json.dumps({"db_name": db.name, "db_host": c.DB_HOST, "db_port": c.DB_PORT,
                          "db_user": c.DB_USER, "db_password": c.DB_PASSWORD}))
    else:
        if args.pid:
            try:
                os.killpg(args.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        db.drop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
