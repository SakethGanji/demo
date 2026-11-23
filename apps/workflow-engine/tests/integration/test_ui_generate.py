"""UI-DESIGN §10.1 — POST /api/projects/{project}/modules/generate (prompt → validated draft; never writes).

Runs against the fake-LLM engine (``fake_api``); no real model is involved, so these prove the endpoint's
plumbing (context, scrub, repair loop, self-test, framing, locking, cancellation), not model quality."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import pytest

import os

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, EngineServer, StudioClient, TestDatabase, fake_env, uniq
from .journeys._helpers import needs_bwrap, new_project

pytestmark = pytest.mark.integration
TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
SECRET = "pay-tok-" + uniq("v")
DEFAULT_MODEL = "claude-sonnet-4-6"

CONFLICT_BAD = '''from studio import api


@api.get("/orders", name="clashes")
def clashes(req):
    return []
'''
CONFLICT_FIXED = '''from studio import api


@api.get("/orders/summary", name="ordersSummary")
def orders_summary(req):
    return {"ok": True}
'''
SYNTAX_BAD = "from studio import api\n\n@api.get('/x')\ndef x(req)\n    return {}\n"
SELFTEST_SRC = '''from studio import api
import studio


@api.get("/a")
def a(req):
    return studio.db.table("things").list()


@api.get("/w")
def w(req):
    return studio.nodes.http_request(method="POST", url="http://127.0.0.1:9/x")


@api.get("/d")
def d(req):
    return studio.db.table("things").insert({"n": 1})


@api.get("/e")
def e(req):
    return studio.db.table("orders").insert({"email": "z@x.io"})


@api.post("/p")
def p(req):
    return {}


@api.get("/p/{pid}")
def p_get(req):
    return {}


@api.get("/l1")
def l1(req):
    return {"l": 1}


@api.get("/l2")
def l2(req):
    return {"l": 2}


@api.get("/l3")
def l3(req):
    return {"l": 3}
'''
SLOW_SRC = '''from studio import api
import time


@api.get("/slow")
def slow(req):
    time.sleep(8)
    return {"done": True}
'''
ORDERS_SRC = '''from studio import api


@api.get("/orders", name="listOrders")
def list_orders(req):
    return []
'''
SECRET_BEARING = "token for this module is " + SECRET


def sandbox_procs() -> list[int]:
    """PIDs of running sandbox jobs: argv[0] is bwrap and an argument names a /tmp/sbx-* job dir.
    (`pgrep -f` would also match any shell whose command line merely mentions the pattern.)"""
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if argv and os.path.basename(argv[0].decode(errors="replace")) == "bwrap" \
                and any(b"/tmp/sbx-" in a for a in argv[1:]) and _descends_from_us(int(pid)):
            out.append(int(pid))
    return out


def _descends_from_us(pid: int) -> bool:
    """Only this pytest's engines (its child processes) count: a concurrent test run's sandboxes are not our leak.
    A leaked bwrap stays a child of the live engine that spawned it (start_new_session keeps the parent pid)."""
    me = os.getpid()
    for _ in range(64):
        try:
            stat = open(f"/proc/{pid}/stat").read()
        except OSError:
            return False
        pid = int(stat.rsplit(")", 1)[1].split()[1])  # field 4, after the parenthesised comm
        if pid == me:
            return True
        if pid <= 1:
            return False
    return False


def _emit(name: str, source: str, tables: list | None = None, summary: str = "x") -> dict:
    return {"tool_calls": [{"name": "emit_module", "args": {"module_name": name, "source": source,
                                                             "summary": summary, "tables": tables or []}}]}


def _custom_rules() -> list[dict]:
    things = [{"name": "things", "columns": [{"name": "n", "type": "integer", "required": False}]}]
    return [
        {"id": "t-slow", "when": {"tool": "emit_module", "last_user_matches": "__slow__"}, "respond": _emit("slow", SLOW_SRC)},
        {"id": "t-never", "when": {"tool": "emit_module", "last_user_matches": "__neverfix__"},
         "respond": _emit("never", SYNTAX_BAD)},
        {"id": "t-never-repair", "when": {"tool": "emit_module", "any_user_matches": "__neverfix__",
                                           "last_user_matches": "^Fix these problems in the module you emitted:"},
         "respond": _emit("never", SYNTAX_BAD)},
        {"id": "t-conflict-fix", "when": {"tool": "emit_module", "any_user_matches": "__conflict__",
                                          "last_user_matches": "^Fix these problems in the module you emitted:"},
         "respond": _emit("orders_summary", CONFLICT_FIXED)},
        {"id": "t-conflict", "when": {"tool": "emit_module", "last_user_matches": "__conflict__"},
         "respond": _emit("orders_summary", CONFLICT_BAD)},
        {"id": "t-selftest", "when": {"tool": "emit_module", "last_user_matches": "__selftest__"},
         "respond": _emit("selftest", SELFTEST_SRC, tables=things)},
        {"id": "t-missing-table", "when": {"tool": "emit_module", "last_user_matches": "__undeclared__"},
         "respond": _emit("undeclared", SELFTEST_SRC.split("@api.get(\"/w\")")[0])},
        {"id": "t-textonly", "when": {"last_user_matches": "__textonly__"},
         "respond": {"text": "Here you go:\n```python\n" + CONFLICT_FIXED + "```\n"}},
        {"id": "t-noimport-fix", "when": {"tool": "emit_module", "any_user_matches": "__noimport__",
                                           "last_user_matches": "^Fix these problems in the module you emitted:"},
         "respond": _emit("noimp", CONFLICT_FIXED)},
        {"id": "t-noimport", "when": {"tool": "emit_module", "last_user_matches": "__noimport__"},
         "respond": _emit("noimp", CONFLICT_FIXED.split("\n\n\n", 1)[1])},
        {"id": "t-nocode", "when": {"last_user_matches": "__nocode__"}, "respond": {"text": "I cannot help"}},
        {"id": "t-nocode-repair", "when": {"any_user_matches": "__nocode__",
                                            "last_user_matches": "^Fix these problems in the module you emitted:"},
         "respond": {"text": "still nothing"}},
        {"id": "t-badname", "when": {"tool": "emit_module", "last_user_matches": "__badname__"},
         "respond": _emit("Bad-Name", CONFLICT_FIXED)},
        {"id": "t-secretcode-fix", "when": {"tool": "emit_module", "any_user_matches": "__secretcode__",
                                             "last_user_matches": "^Fix these problems in the module you emitted:"},
         "respond": _emit("leaky", CONFLICT_FIXED)},
        {"id": "t-secretcode", "when": {"tool": "emit_module", "last_user_matches": "__secretcode__"},
         "respond": _emit("leaky", f"# the model somehow wrote {SECRET}\n" + SYNTAX_BAD)},
        {"id": "t-publicwrite", "when": {"tool": "emit_module", "last_user_matches": "__publicwrite__"},
         "respond": _emit("pw", 'from studio import api\nimport studio\n\n@api.post("/w", auth="none")\ndef w(req):\n'
                                '    t = req.body["t"]\n    s = req.body["s"]\n    studio.db.table(t).insert({})\n'
                                '    return {"k": str(studio.secret(s))}\n')},
    ]


@pytest.fixture
def script(fake_api):
    pinned = json.loads(FAKE_SCRIPT.read_text())
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK,
                     json={**pinned, "rules": _custom_rules() + pinned["rules"]})
    assert r.status_code == 200, r.text
    fake_api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=pinned)
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})


def _project(api, upstream, prefix="gen"):
    P, S = new_project(api, prefix)
    host = urlparse(upstream).hostname
    r = api.post(f"/projects/{P}/secrets", {"key": "PAYMENTS_TOKEN", "value": SECRET, "environment": "dev",
                                            "allowed_hosts": [host]})
    assert r.status_code == 201, r.text
    cols = [{"name": "email", "type": "string", "required": True}, {"name": "total", "type": "number"}]
    r = api.post(f"/projects/{P}/tables", {"name": "orders", "columns": cols})
    assert r.status_code == 201, r.text
    T = r.json()["id"]
    assert api.post(f"/projects/{P}/tables/{T}/rows", {"rows": [{"email": "a@x.io", "total": 1},
                                                                  {"email": "b@x.io", "total": 2}]}).status_code == 201
    return P, S


def _parse_sse(lines) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    ev, data = None, []
    for line in lines:
        if line.startswith(":"):
            continue
        if line == "":
            if ev is not None:
                out.append((ev, json.loads("\n".join(data)) if data else {}))
            ev, data = None, []
        elif line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if ev is not None:
        out.append((ev, json.loads("\n".join(data)) if data else {}))
    return out


def gen(api, P: str, prompt: str, **extra) -> tuple[int, list[dict], Any]:
    """POST the SSE endpoint. Returns (status, message-event payloads, raw events incl. done)."""
    body = {"prompt": prompt, **extra}
    with api.http.stream("POST", f"/api/projects/{P}/modules/generate", content=json.dumps(body),
                         headers={"Content-Type": "application/json", "Accept": "text/event-stream"}) as r:
        if r.status_code != 200 or "text/event-stream" not in r.headers.get("content-type", ""):
            r.read()
            return r.status_code, [], r.json() if r.content else None
        events = _parse_sse(r.iter_lines())
    return 200, [d for e, d in events if e == "message"], events


def gen_json(api, P: str, prompt: str, **extra):
    return api.post(f"/projects/{P}/modules/generate", {"prompt": prompt, **extra}, headers={"Accept": "application/json"})


def calls(api) -> list[dict]:
    return api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()


def terminal(msgs: list[dict]) -> dict:
    t = [m for m in msgs if m["type"] in ("result", "error")]
    assert len(t) == 1, [m["type"] for m in msgs]
    assert msgs[-1] is t[0]
    return t[0]


def assert_framing(msgs: list[dict], raw: list) -> None:
    assert [m["seq"] for m in msgs] == list(range(1, len(msgs) + 1))
    assert [e for e, _ in raw].count("done") == 1 and raw[-1] == ("done", {})
    assert all(e == "message" for e, _ in raw[:-1])


def table_names(api, P) -> list[str]:
    return [t["name"] for t in api.get(f"/projects/{P}/tables").json()]


def writes_snapshot(api, P) -> tuple:
    return (api.get(f"/projects/{P}/modules").json(), api.get(f"/projects/{P}/routes").json(), table_names(api, P),
            [s["key"] for s in api.get(f"/projects/{P}/secrets").json()])


@needs_bwrap
def test_generate_happy_path_framing_context_layout_and_no_writes(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    mod = fake_api.post(f"/projects/{P}/modules", {"name": "orders_api", "source": ORDERS_SRC})
    assert mod.status_code == 201, mod.text
    other = "other_one"
    assert fake_api.post(f"/projects/{P}/modules", {"name": other, "source": 'from studio import api\n\n'
                                                    '@api.get("/o")\ndef o(req):\n    return {}\n'}).status_code == 201
    before = writes_snapshot(fake_api, P)
    conversation = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i}"} for i in range(8)]
    prompt = "Store refunds with create, list and stats. __refunds__"
    status, msgs, raw = gen(fake_api, P, prompt, module_name="refunds", conversation=conversation,
                            current_source=ORDERS_SRC)
    assert status == 200
    assert_framing(msgs, raw)
    types = [m["type"] for m in msgs]
    phases = [m["phase"] for m in msgs if m["type"] == "status"]
    assert phases == ["context", "generating", "analysing", "testing", "testing"], phases
    assert [m["route"] for m in msgs if m["type"] == "status" and m["phase"] == "testing"] == \
        ["GET /refunds", "GET /refunds/stats"]
    assert types[:2] == ["status", "status"] and "draft" in types and "analysis" in types and "self_test" in types
    res = terminal(msgs)
    assert res["type"] == "result" and res["module_name"] == "refunds" and res["attempts"] == 1
    assert res["model"] == DEFAULT_MODEL
    assert res["summary"] == "Refunds table with create, list and stats routes."
    a = res["analysis"]
    assert sorted((r["method"], r["path"]) for r in a["routes"]["created"]) == \
        [("GET", "/refunds"), ("GET", "/refunds/stats"), ("POST", "/refunds")]
    assert set(a["routes"]["created"][0]) >= {"method", "path", "name", "function", "line", "auth"}
    assert a["errors"] == [] and a["conflicts"] == []
    t = {x["name"]: x for x in a["tables"]}
    assert t["refunds"]["exists"] is False and [c["name"] for c in t["refunds"]["columns"]] == ["reason", "amount"]
    assert t["refunds"]["lines"]
    s = {x["key"]: x for x in a["secrets"]}["PAYMENTS_TOKEN"]
    assert s["present_in"] == ["dev"] and s["missing_in"] == ["prod"] and s["bound"] is True
    assert s["allowed_hosts"] == [urlparse(upstream).hostname] and s["lines"]
    # self-test: GET /refunds and GET /refunds/stats ran (virtual table, real secret + upstream), POST skipped
    st = {r["route"]: r for r in res["self_test"]}
    assert st["POST /refunds"] == {"route": "POST /refunds", "skipped": "writes"}
    assert st["GET /refunds"]["ok"] is True and st["GET /refunds"]["status"] == 200
    assert st["GET /refunds"]["execution_id"].startswith("exec_") and st["GET /refunds"]["body_preview"] == "[]"
    assert st["GET /refunds/stats"]["ok"] is True and "available" in st["GET /refunds/stats"]["body_preview"]
    assert SECRET not in json.dumps(msgs)
    # it never writes
    assert writes_snapshot(fake_api, P) == before and "refunds" not in table_names(fake_api, P)
    # the self-test rows are the only rows created
    runs = fake_api.get(f"/projects/{P}/runs?trigger=generate_self_test").json()
    assert {r["id"] for r in runs} == {st["GET /refunds"]["execution_id"], st["GET /refunds/stats"]["execution_id"]}

    # message layout (pinned): system, source pair, last 6 turns, prompt only
    c = [x for x in calls(fake_api) if x["tools"] and x["tools"][0]["name"] == "emit_module"]
    assert len(c) == 1 and c[0]["model"] == DEFAULT_MODEL and c[0]["temperature"] == 0.2
    m = c[0]["messages"]
    assert m[0]["role"] == "system"
    assert m[1]["role"] == "user" and m[1]["content"].split("\n", 1)[0] == "Current module refunds.py:"
    assert "```python\n" + ORDERS_SRC in m[1]["content"]
    assert m[2] == {"role": "assistant", "content": "OK."}
    assert [(x["role"], x["content"]) for x in m[3:-1]] == [(e["role"], e["content"]) for e in conversation[-6:]]
    assert m[-1] == {"role": "user", "content": prompt}
    sysmsg = m[0]["content"]
    for needle in ("http_request", "studio.db.table", "orders", "email", "PAYMENTS_TOKEN", "listOrders",
                   "orders_api", other, "GET", "/orders", "2"):
        assert needle in sysmsg, needle
    assert SECRET not in sysmsg and "a@x.io" not in sysmsg
    tool = c[0]["tools"][0]
    assert tool["parameters"]["required"] == ["module_name", "source", "summary"]
    assert set(tool["parameters"]["properties"]) == {"module_name", "source", "summary", "tables"}


@needs_bwrap
def test_generate_json_mode_equals_sse_result(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    prompt = "Refunds please __refunds__"
    _, msgs, _ = gen(fake_api, P, prompt, module_name="refunds")
    sse = terminal(msgs)
    r = gen_json(fake_api, P, prompt, module_name="refunds")
    assert r.status_code == 200, r.text
    j = r.json()

    def norm(x: dict) -> dict:  # execution ids / durations differ run to run
        x = json.loads(json.dumps({k: v for k, v in x.items() if k not in ("type", "seq")}))
        for res in x["self_test"]:
            res.pop("execution_id", None)
            res.pop("duration_ms", None)
        return x
    assert norm(j) == norm(sse)


def test_generate_secret_scrub_before_every_call_and_warning(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    # attempt 1 (the model's own draft even contains the value) fails → the repair call echoes that draft back
    status, msgs, raw = gen(fake_api, P, "Fix it __secretcode__", self_test=False,
                            current_source=ORDERS_SRC + "\n# " + SECRET_BEARING,
                            conversation=[{"role": "user", "content": "my key is " + SECRET}])
    assert status == 200
    assert_framing(msgs, raw)
    res = terminal(msgs)
    assert res["type"] == "result" and res["attempts"] == 2
    assert {"code": "secret_scrubbed", "message": "A secret value in your code or prompt was removed before sending it to the AI"} \
        in res["analysis"]["warnings"]
    llm_calls = [c for c in calls(fake_api) if c["tools"]]
    assert len(llm_calls) == 2
    for c in llm_calls:
        assert SECRET not in json.dumps(c["messages"]), "secret reached the LLM"
    assert "my key is ***" in json.dumps(llm_calls[0]["messages"])
    assert "# the model somehow wrote ***" in json.dumps(llm_calls[1]["messages"])    # the echoed assistant turn


def test_generate_secret_in_the_prompt_itself_is_scrubbed_in_the_last_message(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, f"use {SECRET} __refunds__", self_test=False, module_name="refunds")
    assert status == 200 and terminal(msgs)["type"] == "result"
    c = [x for x in calls(fake_api) if x["tools"]][0]
    assert c["messages"][-1]["content"] == "use *** __refunds__"
    assert SECRET not in json.dumps(c)


def test_generate_repair_bad_then_good(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, raw = gen(fake_api, P, "Make it work __broken__", self_test=False)
    assert status == 200
    assert_framing(msgs, raw)
    res = terminal(msgs)
    assert res["type"] == "result" and res["attempts"] == 2 and res["module_name"] == "broken"
    drafts = [m for m in msgs if m["type"] == "draft"]
    assert [d["attempt"] for d in drafts] == [1, 2] and "for i in range(3)\n" in drafts[0]["source"]
    analyses = [m for m in msgs if m["type"] == "analysis"]
    assert analyses[0]["errors"] and analyses[0]["errors"][0]["code"] == "syntax_error"
    assert analyses[1]["errors"] == []
    assert [m["phase"] for m in msgs if m["type"] == "status"] == \
        ["context", "generating", "analysing", "repairing", "generating", "analysing"]
    # repair turn layout: assistant tool call, tool result "ok", then the fixed repair line
    c = [x for x in calls(fake_api) if x["tools"]]
    assert len(c) == 2 and c[1]["rule_id"] == "repair"
    tail = c[1]["messages"][-3:]
    assert tail[0]["role"] == "assistant" and tail[0].get("tool_calls")
    assert tail[1]["role"] == "tool" and "ok" in tail[1]["content"]
    assert tail[2]["role"] == "user"
    first, *problems = tail[2]["content"].split("\n")
    assert first == "Fix these problems in the module you emitted:"
    assert len(problems) == 1 and problems[0].startswith("- syntax_error line 7: ")


def test_generate_three_failures_end_in_generation_invalid_with_the_draft(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, raw = gen(fake_api, P, "Never works __neverfix__", self_test=False)
    assert status == 200
    assert_framing(msgs, raw)
    err = terminal(msgs)
    assert err["type"] == "error" and err["code"] == "generation_invalid"
    assert err["draft"]["module_name"] == "never" and "def x(req)\n" in err["draft"]["source"]
    assert err["analysis"]["errors"][0]["code"] == "syntax_error"
    assert len([m for m in msgs if m["type"] == "draft"]) == 3
    assert len([x for x in calls(fake_api) if x["tools"]]) == 3
    r = gen_json(fake_api, P, "Never works __neverfix__", self_test=False)
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "generation_invalid" and d["draft"]["module_name"] == "never" and d["errors"]


def test_generate_route_conflict_is_repaired_with_the_documented_message(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    r = fake_api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders",
                                                "handler": {"type": "workflow", "create": True}})
    assert r.status_code == 201, r.text
    status, msgs, _ = gen(fake_api, P, "Orders summary __conflict__", self_test=False, module_name="orders_summary")
    assert status == 200
    first = [m for m in msgs if m["type"] == "analysis"][0]
    assert first["conflicts"] and first["conflicts"][0]["kind"] == "route"
    c0 = first["conflicts"][0]
    assert (c0["method"], c0["path"], c0["line"]) == ("GET", "/orders", 4)
    assert c0["existing_route_id"] == r.json()["route"]["id"] and c0["handler"]["type"] == "workflow"
    assert [e["code"] for e in first["errors"]] == ["route_conflict"] and first["errors"][0]["line"] == 4
    res = terminal(msgs)
    assert res["type"] == "result" and res["attempts"] == 2
    repair = [x for x in calls(fake_api) if x["tools"]][1]["messages"][-1]["content"].split("\n")
    assert repair[0] == "Fix these problems in the module you emitted:" and len(repair) == 2
    assert re.fullmatch(r"- route_conflict line 4: GET /orders already exists \(workflow '.+'\)", repair[1]), repair[1]
    # the saved routes are untouched (the endpoint never writes)
    assert [x["path"] for x in fake_api.get(f"/projects/{P}/routes").json()] == ["/orders"]


def test_generate_module_name_conflict_for_new_module(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    assert fake_api.post(f"/projects/{P}/modules", {"name": "orders_summary", "source": ORDERS_SRC.replace("/orders", "/o2").replace("listOrders", "o2")}).status_code == 201
    status, msgs, _ = gen(fake_api, P, "Orders summary __conflict__", self_test=False)
    assert status == 200
    first = [m for m in msgs if m["type"] == "analysis"][0]
    assert {"kind": "module_name", "name": "orders_summary"} in first["conflicts"]
    repair = [x for x in calls(fake_api) if x["tools"]][1]["messages"][-1]["content"]
    assert "- module_name: 'orders_summary' is taken, choose another" in repair


def test_generate_missing_undeclared_table_triggers_repair_line(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "Things __undeclared__", self_test=False)
    assert status == 200
    first = [m for m in msgs if m["type"] == "analysis"][0]
    assert first["tables"][0]["name"] == "things" and first["tables"][0]["exists"] is False \
        and first["tables"][0]["columns"] is None
    c = [x for x in calls(fake_api) if x["tools"]]
    assert len(c) >= 2
    assert "- missing_table things: declare it in tables" in c[1]["messages"][-1]["content"]


def test_generate_text_only_reply_uses_the_first_python_fence(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "just code __textonly__", self_test=False, module_name="plain")
    assert status == 200
    res = terminal(msgs)
    assert res["type"] == "result" and res["attempts"] == 1
    assert res["module_name"] == "plain" and res["source"].strip() == CONFLICT_FIXED.strip()
    assert res["analysis"]["routes"]["created"][0]["path"] == "/orders/summary"


def test_generate_no_code_attempts_end_in_generation_invalid(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "nothing __nocode__", self_test=False)
    assert status == 200
    err = terminal(msgs)
    assert err["type"] == "error" and err["code"] == "generation_invalid"
    repair = [x for x in calls(fake_api) if x["tools"] or x["rule_id"]][1]["messages"][-1]["content"]
    assert "- no_code: call emit_module with the full module" in repair


def test_generate_invalid_module_name_from_the_model_is_a_problem_not_a_crash(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "bad name __badname__", self_test=False)
    assert status == 200
    first = [m for m in msgs if m["type"] == "analysis"][0]
    assert any(e["code"] == "invalid_module_name" for e in first["errors"])


def test_generate_warns_on_public_write_and_dynamic_names(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "dynamic __publicwrite__", self_test=False)
    assert status == 200
    codes = {w["code"] for w in terminal(msgs)["analysis"]["warnings"]}
    assert codes == {"public_write_route", "dynamic_table_name", "dynamic_secret_name"}


@needs_bwrap
def test_generate_self_test_is_read_only_virtual_limited_and_never_sent_to_the_llm(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "selftest __selftest__", module_name="selftest")
    assert status == 200
    res = terminal(msgs)
    assert res["type"] == "result", res
    st = {r["route"]: r for r in res["self_test"]}
    assert st["GET /a"]["ok"] is True and st["GET /a"]["body_preview"] == "[]"                # virtual table: empty read
    assert st["GET /w"]["ok"] is False and st["GET /w"]["error_code"] == "read_only"          # http POST denied
    assert st["GET /d"]["ok"] is False and st["GET /d"]["error_code"] == "table_not_found"    # virtual write
    assert st["GET /e"]["ok"] is False and st["GET /e"]["error_code"] == "read_only"          # real table write
    assert st["POST /p"]["skipped"] == "writes" and st["GET /p/{pid}"]["skipped"] == "path_params"
    ran = [k for k, v in st.items() if "ok" in v]
    assert ran == ["GET /a", "GET /w", "GET /d", "GET /e", "GET /l1"]        # at most 5, in order
    assert st["GET /l2"]["skipped"] == "limit" and st["GET /l3"]["skipped"] == "limit"
    assert [r["route"] for r in res["self_test"]][:3] == ["GET /a", "GET /w", "GET /d"]
    for r in st.values():
        if "ok" in r:
            assert set(r) >= {"route", "ok", "status", "duration_ms", "error_code", "body_preview", "execution_id"}
    # self-test failures never trigger a repair, and its output never reaches the LLM
    assert res["attempts"] == 1 and len([x for x in calls(fake_api) if x["tools"]]) == 1
    # the table was never created, the real table was not written to; the self-test rows are the only writes
    assert "things" not in table_names(fake_api, P)
    T = [t["id"] for t in fake_api.get(f"/projects/{P}/tables").json() if t["name"] == "orders"][0]
    rows = fake_api.get(f"/projects/{P}/tables/{T}/rows").json()["rows"]
    assert sorted(r["data"]["email"] for r in rows) == ["a@x.io", "b@x.io"]       # no z@x.io: the write was refused
    runs = fake_api.get(f"/projects/{P}/runs?trigger=generate_self_test").json()
    assert len(runs) == 5 and all(r["mode"] == "sandbox" for r in runs)
    one = fake_api.get(f"/projects/{P}/runs/{st['GET /w']['execution_id']}").json()
    assert one["trigger"]["type"] == "generate_self_test"


@needs_bwrap
def test_generate_self_test_off_runs_nothing(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "selftest __selftest__", self_test=False)
    res = terminal(msgs)
    assert res["self_test"] is None and "self_test" not in [m["type"] for m in msgs]
    assert fake_api.get(f"/projects/{P}/runs?trigger=generate_self_test").json() == []


@needs_bwrap
def test_generate_self_test_is_skipped_when_the_sandbox_is_unavailable(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    try:
        assert fake_api.raw("PUT", "/api/_test/sandbox", headers=TOK, json={"available": False}).status_code == 200
        status, msgs, _ = gen(fake_api, P, "selftest __selftest__")
    finally:
        fake_api.raw("PUT", "/api/_test/sandbox", headers=TOK, json={"available": True})
    res = terminal(msgs)
    assert status == 200 and res["type"] == "result" and res["self_test"] is None


def test_generate_pre_stream_errors(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    for hdr in ({"Accept": "application/json"}, {"Accept": "text/event-stream"}):
        r = fake_api.post("/projects/proj_nope/modules/generate", {"prompt": "x"}, headers=hdr)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "project_not_found"
        r = fake_api.post(f"/projects/{P}/modules/generate", {"prompt": "x", "module_id": "mod_nope"}, headers=hdr)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "module_not_found"
        for name in ("Bad-Name", "new", "generate", "default", "studio", "1abc"):
            r = fake_api.post(f"/projects/{P}/modules/generate", {"prompt": "x", "module_name": name}, headers=hdr)
            assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_module_name", (name, r.text)
    # 503 only here
    try:
        fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": True})
        for hdr in ({"Accept": "application/json"}, {"Accept": "text/event-stream"}):
            r = fake_api.post(f"/projects/{P}/modules/generate", {"prompt": "x"}, headers=hdr)
            assert r.status_code == 503 and r.json()["detail"]["code"] == "llm_unavailable"
    finally:
        fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})
    assert calls(fake_api) == []


def test_generate_request_limits_are_422_invalid_request(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    bad = [{"prompt": ""}, {"prompt": "x" * 8001}, {"prompt": "x", "bogus": 1},
           {"prompt": "x", "current_source": "a" * 200_001},
           {"prompt": "x", "conversation": [{"role": "user", "content": "y" * 8001}]},
           {"prompt": "x", "conversation": [{"role": "system", "content": "y"}]},
           {"prompt": "x", "self_test": "maybe"}, {}]
    for body in bad:
        r = fake_api.post(f"/projects/{P}/modules/generate", body)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_request", (str(body)[:60], r.text)
    # the limits themselves are inclusive; older conversation turns are dropped, not rejected
    marker = " __refunds__"
    prompt = "x" * (8000 - len(marker)) + marker
    assert len(prompt) == 8000
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}"} for i in range(20)]
    r = gen_json(fake_api, P, prompt, self_test=False, conversation=turns, module_name="refunds")
    assert r.status_code == 200, r.text[:300]
    m = [c for c in calls(fake_api) if c["tools"]][-1]["messages"]
    assert [x["content"] for x in m[1:-1]] == [f"t{i}" for i in range(14, 20)]
    assert m[-1]["content"] == prompt
    src = "#" * 200_000
    assert len(src.encode()) == 200_000
    r = gen_json(fake_api, P, "x __refunds__", self_test=False, current_source=src, module_name="refunds")
    assert r.status_code == 200, r.text[:300]


def test_generate_llm_failures_are_terminal_errors_and_json_statuses(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, raw = gen(fake_api, P, "boom __llm_fail__", self_test=False)
    assert status == 200
    assert_framing(msgs, raw)
    err = terminal(msgs)
    assert err["type"] == "error" and err["code"] == "llm_failed" and "draft" not in err
    r = gen_json(fake_api, P, "boom __llm_fail__", self_test=False)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "llm_failed"
    assert SECRET not in r.text


def test_generate_retryable_llm_errors_are_retried_by_the_real_retry_loop(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "flaky __llm_flaky__", self_test=False, module_name="flaky")
    res = terminal(msgs)
    assert status == 200 and res["type"] == "result" and res["module_name"] == "flaky"
    assert [c["rule_id"] for c in calls(fake_api) if c["tools"]] == ["flaky", "flaky", "flaky-ok"]


@needs_bwrap
def test_generate_concurrent_generation_is_409_and_disconnect_cancels_and_releases(fake_api, upstream, script):
    P, _ = _project(fake_api, upstream)
    body = json.dumps({"prompt": "slow __slow__"})
    hdr = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    with fake_api.http.stream("POST", f"/api/projects/{P}/modules/generate", content=body, headers=hdr) as r:
        assert r.status_code == 200
        seen = []
        lines = r.iter_lines()    # NOT `for … break`: closing the iterator would close the connection
        while True:
            line = next(lines)
            if line.startswith("data:"):
                seen.append(json.loads(line[5:]))
                if seen[-1].get("type") == "status" and seen[-1].get("phase") == "testing":
                    break
        assert seen[-1]["phase"] == "testing" and seen[-1].get("route") == "GET /slow"
        time.sleep(0.5)
        # same project + actor → 409; another actor, another project → fine to start
        r2 = fake_api.post(f"/projects/{P}/modules/generate", {"prompt": "x __slow__"})
        assert r2.status_code == 409 and r2.json()["detail"]["code"] == "generation_in_progress"
        deadline = time.time() + 5  # under load bwrap can start a beat after the "testing" status
        while not sandbox_procs() and time.time() < deadline:
            time.sleep(0.1)
        assert sandbox_procs(), "sandbox should be running"
        t0 = time.time()
    # leaving the with-block closed the connection: the sandbox group is killed, the lock released
    released = None
    for _ in range(40):
        time.sleep(0.25)
        r3 = fake_api.post(f"/projects/{P}/modules/generate", {"prompt": "again __refunds__"}, headers={"Accept": "application/json"})
        if r3.status_code != 409:
            released = time.time() - t0
            break
    assert released is not None and released < 6, "lock must be released well before the 8 s sleep would finish"
    assert r3.status_code == 200, r3.text
    time.sleep(0.5)
    assert sandbox_procs() == [], "sandbox leaked"
    # a different actor is independent of the lock
    P2, _ = _project(fake_api, upstream, "gen2")
    assert gen_json(fake_api, P2, "x __refunds__", self_test=False, module_name="refunds").status_code == 200


@pytest.fixture(scope="module")
def short_budget_api(upstream, tmp_path_factory):
    """A second fake-LLM engine whose whole-generation budget is 3 s (the production default is 120 s)."""
    db = TestDatabase()
    db.name = db.name.replace("workflows_test_", "workflows_test_gentimeout_", 1) if not os.environ.get("TEST_DB_NAME") \
        else db.name + "_gentimeout"
    db.create()
    srv = None
    try:
        db.migrate("apply")
        srv = EngineServer(db, extra_env=fake_env(db, E2E_UPSTREAM_URL=upstream, WORKFLOW_MODULE_GEN_TIMEOUT_S="3"),
                           cwd=str(tmp_path_factory.mktemp("gen-timeout-cwd")), label="-gentimeout")
        srv.start()
        pinned = json.loads(FAKE_SCRIPT.read_text())
        api = StudioClient(srv.base_url)
        assert api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK,
                       json={**pinned, "rules": _custom_rules() + pinned["rules"]}).status_code == 200
        yield api
    finally:
        if srv:
            srv.stop()
        if os.environ.get("KEEP_TEST_DB") != "1":
            db.drop()


@needs_bwrap
def test_generate_budget_exhausted_is_a_timeout_error_with_the_draft_and_kills_the_sandbox(short_budget_api, upstream):
    api = short_budget_api
    P, _ = _project(api, upstream)
    t0 = time.time()
    status, msgs, raw = gen(api, P, "slow __slow__")
    assert status == 200 and time.time() - t0 < 7, "must stop at the 3 s budget, not wait for the 8 s sleep"
    assert_framing(msgs, raw)
    err = terminal(msgs)
    assert err["type"] == "error" and err["code"] == "timeout"
    assert err["draft"]["module_name"] == "slow" and "time.sleep(8)" in err["draft"]["source"]
    time.sleep(0.5)
    assert sandbox_procs() == [], "sandbox leaked"
    r = api.post(f"/projects/{P}/modules/generate", {"prompt": "slow __slow__"}, headers={"Accept": "application/json"})
    assert r.status_code == 504 and r.json()["detail"]["code"] == "timeout"
    assert r.json()["detail"]["draft"]["module_name"] == "slow"


def test_generate_missing_sdk_imports_are_caught_statically_and_repaired(fake_api, upstream, script):
    """Found with a real model (it omitted `from studio import api`): it must not reach save."""
    P, _ = _project(fake_api, upstream)
    status, msgs, _ = gen(fake_api, P, "no imports __noimport__", self_test=False)
    assert status == 200
    first = [m for m in msgs if m["type"] == "analysis"][0]
    assert [(e["code"], e["line"]) for e in first["errors"]] == [("undefined_name", 1)]
    assert "'api'" in first["errors"][0]["message"] and "from studio import api" in first["errors"][0]["message"]
    res = terminal(msgs)
    assert res["type"] == "result" and res["attempts"] == 2
    repair = [x for x in calls(fake_api) if x["tools"]][1]["messages"][-1]["content"].split("\n")
    assert repair[1].startswith("- undefined_name line 1: 'api' is used but not imported")
    system = [x for x in calls(fake_api) if x["tools"]][0]["messages"][0]["content"]
    assert "from studio import api" in system
