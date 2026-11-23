"""Create / drop the throwaway database of the studio's Playwright stack (UI-DESIGN §12.1 step 1).

    python tests/e2e_stack.py up            # TEST_DB_NAME=workflows_test_e2e_<8hex>; creates + migrates; prints one JSON line
    python tests/e2e_stack.py seed --engine-url URL [--state-file PATH]
                                            # after the engine is up: starts tests/fake_refund_api.py, creates the
                                            # "Refund triage" workflow + its table, seeds ONE failed run, points the
                                            # draft back at the OK route; prints one JSON line (see seed())
    python tests/e2e_stack.py nbseed --engine-url URL
                                            # notebook specs: creates workflows_test_nbdata_<8hex> with bank.wires and
                                            # connections ``bank`` (read-only) + ``bank_rw`` in proj_default; one JSON line
    python tests/e2e_stack.py down --db NAME [--pid FAKE_SERVER_PID] [--nbdb NBDATA_NAME]

Refuses any name that is not ``workflows_test_e2e_<8 hex>`` (so never the live ``workflows`` DB)."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NAME_RE = re.compile(r"^workflows_test_e2e_[0-9a-f]{8}$")
NBDATA_RE = re.compile(r"^workflows_test_nbdata_[0-9a-f]{8}$")
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workflows" / "refund_triage.json"
FAKE_SERVER = Path(__file__).resolve().parent / "fake_refund_api.py"
UI_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workflows" / "refund_triage_ui.json"
CHAT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workflows" / "chat_support_ui.json"


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

    ui = _seed_interfaces(base, raw_url=ok_url)

    out = {**ui, "refund_workflow_id": wf_id, "refund_failed_execution_id": exec_id, "refund_table_id": table.get("id"),
           "fake_api_url": fake, "fake_api_ok_url": ok_url, "fake_api_fail_url": fail_url, "fake_api_pid": proc.pid}
    if state_file and Path(state_file).exists():
        st = json.loads(Path(state_file).read_text())
        st.update(out)
        Path(state_file).write_text(json.dumps(st, indent=2))
    return out


def _seed_interfaces(base: str, raw_url: str) -> dict:
    """Stage 5: the refund form + approval workflow and the chat workflow. Never published here (tests publish them).

    A preview submission is attempted so a run with an open task exists for the editor's live preview; it is
    best-effort (it needs the durable-approvals engine) and its absence is reported as ``refund_ui_execution_id: null``."""
    out: dict = {"refund_ui_workflow_id": None, "refund_ui_slug": None, "refund_ui_execution_id": None,
                 "chat_workflow_id": None, "chat_slug": None, "project_slug": None}
    code, project = _call(base, "GET", "/projects/proj_default")
    if code == 200:
        out["project_slug"] = project.get("slug")
    if UI_FIXTURE.exists():
        body = json.loads(UI_FIXTURE.read_text().replace("__REFUND_API_URL__", raw_url))
        code, wf = _call(base, "POST", "/workflows", body)
        if code != 201:
            raise RuntimeError(f"create refund-ui workflow failed: {code} {wf}")
        out["refund_ui_workflow_id"], out["refund_ui_slug"] = wf["id"], body["interface"]["slug"]
        code, _ = _call(base, "PUT", f"/workflows/{wf['id']}/interface", body["interface"])
        if code != 200:
            raise RuntimeError(f"saving the refund-ui interface failed: {code}")
        # Classify is pinned only while the seeded run passes it (the AI step needs a scripted/real model otherwise)
        pinned = json.loads(json.dumps(body["nodes"]))
        for n in pinned:
            if n["name"] == "Classify":
                n["pinned_data"] = [{"json": {"category": "damaged", "summary": "Customer reports a damaged item; the amount is above the auto-approval limit.", "suggest": "approve"}}]
        _call(base, "PUT", f"/workflows/{wf['id']}", {"nodes": pinned})
        req = urllib.request.Request(
            f"{base}/workflows/{wf['id']}/interface/preview/submit", method="POST",
            data=json.dumps({"fields": {"order_id": "A-10042", "amount": 742, "reason": "damaged", "email": "dana@example.com"}}).encode(),
            headers={"Content-Type": "application/json", "Idempotency-Key": "e2e-seed-1"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out["refund_ui_execution_id"] = json.loads(r.read() or b"{}").get("execution_id")
        except (urllib.error.URLError, ValueError):
            pass
        if out["refund_ui_execution_id"]:
            deadline = time.time() + 30
            while time.time() < deadline:      # wait until the run is past Classify (waiting for the manager, or ended)
                code, data = _call(base, "GET", f"/workflows/{wf['id']}/interface/preview/data?execution_id={out['refund_ui_execution_id']}")
                if code == 200 and data.get("status") in ("waiting", "success", "failed", "cancelled"):
                    break
                time.sleep(0.3)
        _call(base, "PUT", f"/workflows/{wf['id']}", {"nodes": body["nodes"]})
    if CHAT_FIXTURE.exists():
        body = json.loads(CHAT_FIXTURE.read_text())
        code, wf = _call(base, "POST", "/workflows", body)
        if code != 201:
            raise RuntimeError(f"create chat workflow failed: {code} {wf}")
        out["chat_workflow_id"], out["chat_slug"] = wf["id"], body["interface"]["slug"]
        code, _ = _call(base, "PUT", f"/workflows/{wf['id']}/interface", body["interface"])
        if code != 200:
            raise RuntimeError(f"saving the chat interface failed: {code}")
    return out


NB_BANK_SQL = """
CREATE SCHEMA bank;
CREATE TABLE bank.wires (wire_id TEXT PRIMARY KEY, amount NUMERIC NOT NULL, status TEXT NOT NULL,
                         created_at TIMESTAMP NOT NULL);
INSERT INTO bank.wires VALUES ('W1', 100.50, 'stuck', now() - interval '3 days'),
                              ('W2', 12500, 'stuck', now() - interval '4 days'),
                              ('W3', 75, 'sent', now() - interval '5 days'),
                              ('W4', 40, 'stuck', now() - interval '2 hours');
"""


def nbseed(engine_url: str) -> dict:
    """A throwaway bank database for the notebook specs plus two project connections on it (``bank`` read-only,
    ``bank_rw`` writable). Drop it with ``down --nbdb NAME``."""
    import uuid
    from urllib.parse import quote

    import psycopg2

    from tests.integration import conftest as c

    name = f"workflows_test_nbdata_{uuid.uuid4().hex[:8]}"
    if not NBDATA_RE.match(name):
        raise RuntimeError("bad nbdata name")
    a = c._admin_conn()
    with a.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    a.close()
    conn = psycopg2.connect(host=c.DB_HOST, port=c.DB_PORT, user=c.DB_USER, password=c.DB_PASSWORD, dbname=name)
    with conn.cursor() as cur:
        cur.execute(NB_BANK_SQL)
    conn.commit()
    conn.close()
    url = f"postgresql://{quote(c.DB_USER)}:{quote(c.DB_PASSWORD)}@{c.DB_HOST}:{c.DB_PORT}/{name}"
    base = engine_url.rstrip("/") + "/api"
    out: dict = {"nb_db_name": name}
    for key, ro in (("bank", True), ("bank_rw", False)):
        code, body = _call(base, "POST", "/projects/proj_default/connections",
                           {"name": key, "kind": "postgres", "url": url, "readonly": ro})
        if code != 201:
            raise RuntimeError(f"create connection {key} failed: {code} {body}")
        out[f"nb_conn_{key}"] = body.get("key") or key
    return out


def _drop_nbdb(name: str) -> None:
    if not NBDATA_RE.match(name):
        raise RuntimeError(f"refusing to drop {name!r}")
    from tests.integration import conftest as c

    a = c._admin_conn()
    with a.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    a.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["up", "down", "seed", "nbseed"])
    ap.add_argument("--db")
    ap.add_argument("--engine-url")
    ap.add_argument("--state-file")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--nbdb")
    args = ap.parse_args()
    if args.action == "seed":
        if not args.engine_url:
            print("seed needs --engine-url", file=sys.stderr)
            return 2
        print(json.dumps(seed(args.engine_url, args.state_file)))
        return 0
    if args.action == "nbseed":
        if not args.engine_url:
            print("nbseed needs --engine-url", file=sys.stderr)
            return 2
        print(json.dumps(nbseed(args.engine_url)))
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
        if args.nbdb:
            _drop_nbdb(args.nbdb)
        db.drop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
