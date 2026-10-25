"""The owner's three Notebook Ask use cases, end to end through the API exactly as the studio calls it (POST .../ask,
POST .../runs, POST /notebook-cells/{id}/apply, GET the notebook), against a real jobs-service + chromium, a real
Postgres "bank" and a small local web app with a login. The fake LLM stands in for the model: each scripted plan is
what a model would send for that request, and the test checks the prompt it was given (notes, last run, tables).

1. Requirements in chat ("balance over 10k gets the premium badge; check the site and the DB agree") -> a query, a
   browser recipe (sign in, then the Users section), an API check -> run -> pass; break the site -> the run says what.
2. "Here are my test cases" (bank.test_cases) -> run them (API + browser per row) -> write pass/fail back to the table
   -> later "what failed? re-run those", answered from the notes + the last-run summary.
3. Back-to-back commands: log in, add a user on the site, check a data table, call the API.

Skips (with the reason) only when node or Playwright's chromium is missing on the host.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import pytest

from .nb_jobs_support import (  # noqa: F401
    _port, add_secret, bank_db, clear_calls, jobs_service, llm_calls, make_project, nbx_jobs_api, nbx_jobs_engine,
    set_script,
)
from .notebook_support import result_of, run_and_wait, wait_run

pytestmark = pytest.mark.integration

LOGIN = {"username": "ana", "password": "pw-1"}

# ── a tiny web app with a login, two sections on the home page, a users page with badges and a JSON API ──────────


class _App(BaseHTTPRequestHandler):
    users: dict[str, dict[str, Any]] = {}
    broken: set[str] = set()  # API paths that answer 500 until fixed

    def _send(self, status: int, body: str, ctype: str = "text/html", headers: dict[str, str] | None = None) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _signed_in(self) -> bool:
        return "sid=ok" in (self.headers.get("cookie") or "")

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/login":
            return self._send(200, """<!doctype html><title>Sign in</title><h1>Sign in</h1>
<form action="/session"><label for="u">Username</label><input id="u" name="u">
<label for="p">Password</label><input id="p" name="p" type="password"><button type="submit">Sign in</button></form>""")
        if u.path == "/session":
            ok = q.get("u") == LOGIN["username"] and q.get("p") == LOGIN["password"]
            return self._send(302, "", headers={"Location": "/home" if ok else "/login",
                                                **({"Set-Cookie": "sid=ok; Path=/"} if ok else {})})
        if u.path.startswith("/api/"):
            if u.path in _App.broken:
                return self._send(500, json.dumps({"error": "boom"}), "application/json")
            if u.path == "/api/users":
                return self._send(200, json.dumps(list(_App.users.values())), "application/json")
            if u.path.startswith("/api/users/"):
                x = _App.users.get(u.path.rsplit("/", 1)[-1])
                return self._send(200 if x else 404, json.dumps(x or {"error": "not found"}), "application/json")
            if u.path == "/api/health":
                return self._send(200, json.dumps({"ok": True}), "application/json")
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        if not self._signed_in():
            return self._send(302, "", headers={"Location": "/login"})
        if u.path == "/home":
            return self._send(200, """<!doctype html><title>Home</title><h1>Home</h1>
<section aria-label="Profile"><h2>Profile</h2><p>Signed in as ana</p></section>
<section aria-label="Directory"><h2>Directory</h2><a href="/users">All users</a></section>""")
        if u.path == "/users":
            rows = "".join(f"<tr><td>{x['id']}</td><td>{x['name']}</td><td>{x['balance']}</td>"
                           f"<td>{'<span>Premium</span>' if x['premium'] else ''}</td></tr>" for x in _App.users.values())
            return self._send(200, f"""<!doctype html><title>Users</title><h1>Users</h1>
<table><thead><tr><th>Id</th><th>Name</th><th>Balance</th><th>Badge</th></tr></thead><tbody>{rows}</tbody></table>
<form action="/users/add"><label for="n">Name</label><input id="n" name="name">
<label for="b">Balance</label><input id="b" name="balance"><button type="submit">Add user</button></form>""")
        if u.path == "/users/add":
            uid = f"U{len(_App.users) + 1}"
            bal = int(q.get("balance") or 0)
            _App.users[uid] = {"id": uid, "name": q.get("name") or "", "balance": bal, "premium": bal > 10000}
            return self._send(302, "", headers={"Location": "/users"})
        return self._send(404, "not found", "text/plain")

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def app_site() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", _port()), _App)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def _fresh_site():
    _App.users = {"U1": {"id": "U1", "name": "Ana", "balance": 15000, "premium": True},
                  "U2": {"id": "U2", "name": "Ben", "balance": 5000, "premium": False},
                  "U3": {"id": "U3", "name": "Cy", "balance": 12000, "premium": True}}
    _App.broken = set()
    yield


@pytest.fixture(scope="module")
def wproj(nbx_jobs_api, bank_db) -> dict[str, Any]:
    bank_db.query("""CREATE TABLE bank.users (user_id TEXT PRIMARY KEY, name TEXT NOT NULL, balance NUMERIC NOT NULL);
        INSERT INTO bank.users VALUES ('U1', 'Ana', 15000), ('U2', 'Ben', 5000), ('U3', 'Cy', 12000);
        CREATE TABLE bank.test_cases (id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
                                      expect_status INT, expect_text TEXT, last_result TEXT);
        INSERT INTO bank.test_cases VALUES
          ('T1', 'Health answers', 'api', '/api/health', 200, NULL, NULL),
          ('T2', 'User list answers', 'api', '/api/users', 200, NULL, NULL),
          ('T3', 'Unknown user is 404', 'api', '/api/users/U99', 404, NULL, NULL),
          ('T4', 'Home shows the profile', 'ui', '/home', NULL, 'Signed in as ana', NULL),
          ('T5', 'Users page lists Cy', 'ui', '/users', NULL, 'Cy', NULL);""")
    p = make_project(nbx_jobs_api, bank_db)
    add_secret(nbx_jobs_api, p, "PORTAL_LOGIN", json.dumps(LOGIN))
    return p


# ── helpers: the studio's calls ──────────────────────────────────────────────────────────────────────────────────


def _nb(api, proj) -> dict[str, Any]:
    r = api.post(f"/projects/{proj['id']}/notebooks", {"title": "walkthrough", "created_by": "ana"})
    assert r.status_code == 201, r.text
    return r.json()


def _get(api, nb) -> dict[str, Any]:
    return api.get(f"/notebooks/{nb['id']}").json()


def _next(api, nb) -> int:
    """The number the next Ask's own cell gets; the planned cells follow it."""
    return max([int(c["name"][1:]) for c in _get(api, nb)["cells"]] + [0]) + 1


def _ask(api, nb, text: str, plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Script the model's reply to this request, ask as the composer does, and return (response, prompt seen)."""
    set_script(api, [{"id": "w", "when": {"tool": "plan_cells", "last_user_matches": re.escape(text[:40])},
                      "respond": {"tool_calls": [{"name": "plan_cells", "args": plan}]}}])
    clear_calls(api)
    r = api.post(f"/notebooks/{nb['id']}/ask", {"text": text, "by": "ana"})
    assert r.status_code == 200, r.text
    calls = llm_calls(api)
    assert len(calls) == 1, "one plan_cells call; notes ride on it"
    prompt = [m for m in calls[0]["messages"] if m["role"] == "user"][0]["content"]
    system = " ".join(m["content"] for m in calls[0]["messages"] if m["role"] == "system")
    assert "Every browser cell starts a fresh browser" in system and "from_table" in system
    out = r.json()
    assert out["ask_cell"]["latest"]["status"] == "success", out["ask_cell"]["latest"]
    return out, prompt


def _run(api, nb, **body) -> dict[str, Any]:
    return run_and_wait(api, nb, triggered_by="ana", **body)


def _sign_in(base: str) -> list[dict[str, Any]]:
    return [{"action": "goto", "url": "/login", "group": "Sign in"},
            {"action": "fill", "target": {"label": "Username"}, "value": "{{$cred.username}}", "group": "Sign in"},
            {"action": "fill", "target": {"label": "Password"}, "value": "{{$cred.password}}", "group": "Sign in"},
            {"action": "click", "target": {"role": "button", "name": "Sign in"}, "group": "Sign in"},
            {"action": "expectVisible", "target": {"role": "heading", "name": "Home"}, "group": "Sign in"}]


def _by_title(nb_full: dict[str, Any], planned: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = {c["id"] for c in planned}
    return {c["title"]: c for c in nb_full["cells"] if c["id"] in ids}


# ── 1. requirements in chat -> browser section A then B -> DB + API -> results ───────────────────────────────────


def test_requirements_to_browser_db_and_api_checks(nbx_jobs_api, wproj, app_site):
    api = nbx_jobs_api
    nb = _nb(api, wproj)
    n = _next(api, nb)  # c1 is the Ask cell; the plan is c2..c6
    q, b, h, p, r = (f"c{n + i}" for i in range(1, 6))
    text = "Users with balance over 10k must get the premium badge; check the site and the DB agree"
    out, prompt = _ask(api, nb, text, {
        "summary": "Find premium users in the DB, check the badge on the site and the API, and compare.",
        "notes": "Premium = bank.users.balance > 10000. Site login secret PORTAL_LOGIN; badge text 'Premium' on "
                 "/users (Directory section on /home). API: GET /api/users has premium flags.",
        "cells": [
            {"kind": "query", "title": "Users over 10k in the DB", "spec": {"connection": "bank", "sql":
             "SELECT user_id AS id, name, balance FROM bank.users WHERE balance > %(min)s ORDER BY user_id",
             "params": {"min": 10000}}},
            {"kind": "browser", "title": "Sign in, then count Premium badges in the Users list", "spec": {
                "base_url": app_site, "credential_slot": "PORTAL_LOGIN", "steps": _sign_in(app_site) + [
                    {"action": "expectText", "target": {"role": "region", "name": "Profile"}, "text": "Signed in as ana",
                     "group": "Profile"},
                    {"action": "click", "target": {"role": "link", "name": "All users"}, "group": "Directory"},
                    {"action": "expectCount", "target": {"text": "Premium"}, "count": f"{{{{cells.{q}.count}}}}",
                     "group": "Directory"}]}},
            {"kind": "http", "title": "The API flags the same users as premium", "spec": {
                "method": "GET", "url": app_site + "/api/users", "assertions": [
                    {"kind": "status", "value": 200},
                    {"kind": "json_path", "path": "$", "op": "has_item", "value": {"id": "U1", "premium": True}},
                    {"kind": "json_path", "path": "$", "op": "has_item", "value": {"id": "U3", "premium": True}}]}},
            {"kind": "rule", "title": "The API's premium users", "spec": {
                "op": "filter", "left": f"{{{{cells.{h}.rows}}}}", "where": [{"field": "premium", "op": "eq", "value": True}]}},
            {"kind": "rule", "title": "DB users over 10k are exactly the API's premium users", "spec": {
                "op": "compare", "left": f"{{{{cells.{q}.rows}}}}", "right": f"{{{{cells.{p}.rows}}}}", "key": "id",
                "fields": [{"left": "name", "right": "name", "op": "eq"}], "fail_on_mismatch": True}},
        ]})
    assert "Your notes from earlier: none yet" in prompt and "PORTAL_LOGIN" in prompt
    planned = out["planned"]
    assert [c["kind"] for c in planned] == ["query", "browser", "http", "rule", "rule"]
    assert [c["name"] for c in planned] == [q, b, h, p, r]
    assert _get(api, nb)["notes"].startswith("Premium = bank.users.balance > 10000")

    run = _run(api, nb)
    full = _get(api, nb)
    res = {c["name"]: result_of(run, c) for c in full["cells"] if c["name"] in (q, b, h, p, r)}
    assert res[q]["output"]["row_count"] == 2
    br = res[b]
    assert br["status"] == "success", br
    assert [s["status"] for s in br["output"]["steps"]] == ["passed"] * 8
    assert res[h]["status"] == "success" and res[h]["output"]["passed"] is True
    assert res[r]["status"] == "success" and res[r]["output"]["matched"] == 2, res[r]["output"]
    assert run["status"] == "done", run
    walk = {"run1": run["status"], "compare": res[r]["output"]["summary"]}

    # the site loses Cy's badge: the run names the browser step and the API check that failed
    _App.users["U3"]["premium"] = False
    run2 = _run(api, nb, continue_on_fail=True)
    full = _get(api, nb)
    res2 = {c["name"]: result_of(run2, c) for c in full["cells"] if c["name"] in (q, b, h, p, r)}
    assert res2[b]["status"] == "error" and res2[b]["error"]["code"] == "browser_failed", res2[b]
    assert "step 8 failed" in res2[b]["error"]["message"]
    assert res2[h]["status"] == "error" and res2[h]["output"]["passed"] is False
    assert run2["status"] == "failed" and run2["stop"]["continued"] is True
    cmp2 = res2[r]  # the DB and the site disagree on U3: the compare fails and says which row
    assert cmp2["status"] == "error" and cmp2["output"]["rows"] == [
        {"id": "U3", "field": "(missing)", "left": "present", "right": None}], cmp2["output"]
    walk["run2"] = {"browser": res2[b]["error"]["message"][:80], "compare": cmp2["output"]["summary"]}

    # "what failed?" is answered from the last-run summary, in notes only
    out3, prompt3 = _ask(api, nb, "what failed and why?", {
        "summary": "Cy (U3) has 12000 in the DB but no Premium badge on the site, and the API says premium=false.",
        "cells": [], "notes": "Premium = balance > 10000. Run 2: U3 lost the badge on site + API; DB still 12000."})
    assert re.search(rf"Last run: run {run2['n']} at .*, failed at {b}\.", prompt3), prompt3
    assert f"{b} FAILED" in prompt3 and f"{h} FAILED" in prompt3 and f"{r} FAILED" in prompt3
    assert "step 8 failed" in prompt3 and "1 mismatch" in prompt3
    assert "has an item with" in prompt3  # the failed API check is named
    assert "Your notes from earlier: Premium = bank.users.balance > 10000" in prompt3
    assert out3["planned"] == [] and out3["ask_cell"]["latest"]["output"]["notes_updated"] is True
    assert "Run 2: U3 lost the badge" in _get(api, nb)["notes"]
    print("\nUSE CASE 1:", json.dumps(walk))


# ── 2. test cases table -> run (API + browser) -> write back -> "what failed? re-run those" ──────────────────────


def _case_children(site: str) -> list[dict[str, Any]]:
    """One body for every test case: an API case is a request, a UI case is a browser check (guards pick)."""
    return [
        {"kind": "http", "title": "API case: call the path", "guard": {"left": "{{row.kind}}", "op": "eq", "right": "api"},
         "spec": {"method": "GET", "url": site + "{{row.path}}",
                  "assertions": [{"kind": "status", "value": "{{row.expect_status}}"}]}},
        {"kind": "browser", "title": "UI case: sign in and check the page",
         "guard": {"left": "{{row.kind}}", "op": "eq", "right": "ui"},
         "spec": {"base_url": site, "credential_slot": "PORTAL_LOGIN", "steps": _sign_in(site) + [
             {"action": "goto", "url": "{{row.path}}", "group": "Case"},
             {"action": "expectText", "target": {"text": "{{row.expect_text}}"}, "text": "{{row.expect_text}}",
              "group": "Case"}]}},
    ]


def test_test_cases_table_run_write_back_and_rerun_failures(nbx_jobs_api, wproj, bank_db, app_site):
    api = nbx_jobs_api
    bank_db.query("UPDATE bank.test_cases SET last_result = NULL")
    _App.broken = {"/api/users"}  # T2 fails this time
    nb = _nb(api, wproj)
    n = _next(api, nb)
    cases, loop = f"c{n + 1}", f"c{n + 2}"  # the loop's two children are c{n+3}, c{n+4}
    ok, bad, w_ok, w_bad = (f"c{n + i}" for i in range(5, 9))
    out, prompt = _ask(api, nb, "Here are my test cases in bank.test_cases. Run them and write the results back.", {
        "summary": "Run every case (API as a request, UI in the browser), then mark each pass or fail.",
        "notes": f"Test cases: bank.test_cases (kind api|ui, path, expect_status, expect_text). Loop {loop}; "
                 f"last_result written by {w_ok} (pass) / {w_bad} (fail).",
        "cells": [
            {"kind": "query", "title": "All test cases", "spec": {"connection": "bank", "sql":
             "SELECT id, title, kind, path, expect_status, expect_text FROM bank.test_cases ORDER BY id"}},
            {"kind": "for_each", "title": "Run each test case", "spec": {"over": f"{{{{cells.{cases}.rows}}}}"},
             "children": _case_children(app_site)},
            {"kind": "rule", "title": "Cases that passed", "spec": {"op": "filter", "left": f"{{{{cells.{loop}.rows}}}}",
             "where": [{"field": "status", "op": "eq", "value": "success"}]}},
            {"kind": "rule", "title": "Cases that failed", "spec": {"op": "filter", "left": f"{{{{cells.{loop}.rows}}}}",
             "where": [{"field": "status", "op": "eq", "value": "error"}]}},
            {"kind": "change", "title": "Mark the passed cases", "guard": {"left": f"{{{{cells.{ok}.count}}}}",
             "op": "gt", "right": 0}, "spec": {"connection": "bank_rw", "table": "bank.test_cases",
             "key": {"id": f"{{{{cells.{ok}.rows.row.id}}}}"}, "set": {"last_result": "pass"}, "reason": "test run"}},
            {"kind": "change", "title": "Mark the failed cases", "guard": {"left": f"{{{{cells.{bad}.count}}}}",
             "op": "gt", "right": 0}, "spec": {"connection": "bank_rw", "table": "bank.test_cases",
             "key": {"id": f"{{{{cells.{bad}.rows.row.id}}}}"}, "set": {"last_result": "fail"}, "reason": "test run"}},
        ]})
    assert "Your notes from earlier: none yet" in prompt
    assert [c["kind"] for c in out["planned"]] == ["query", "for_each", "rule", "rule", "change", "change"]
    assert _get(api, nb)["notes"].startswith("Test cases: bank.test_cases")

    run = _run(api, nb)
    by = {c["name"]: c for c in _get(api, nb)["cells"]}
    loop_res = result_of(run, by[loop])
    assert loop_res["status"] == "success" and loop_res["output"]["ok"] == 4 and loop_res["output"]["failed"] == 1, \
        loop_res["output"]
    bad_row = next(x for x in loop_res["output"]["rows"] if x["status"] == "error")
    assert bad_row["row"]["id"] == "T2"  # the input row rides along with its result
    assert run["status"] == "needs_you" and run["stop"]["cell_id"] == by[w_ok]["id"], run
    prop = result_of(run, by[w_ok])
    assert prop["status"] == "needs_approval" and prop["output"]["row_count"] == 4, prop["output"]
    r = api.post(f"/notebook-cells/{by[w_ok]['id']}/apply", {"approved_by": "ana"})
    assert r.status_code == 200, r.text
    run = wait_run(api, run["id"])
    assert run["status"] == "needs_you" and run["stop"]["cell_id"] == by[w_bad]["id"], run
    assert api.post(f"/notebook-cells/{by[w_bad]['id']}/apply", {"approved_by": "ana"}).status_code == 200
    run = wait_run(api, run["id"])
    assert run["status"] == "done", run
    got = dict(bank_db.query("SELECT id, last_result FROM bank.test_cases ORDER BY id"))
    assert got == {"T1": "pass", "T2": "fail", "T3": "pass", "T4": "pass", "T5": "pass"}, got

    # later: "what failed? re-run those" -> notes + last-run line point at T2; re-run only that row
    _App.broken = set()
    k = _next(api, nb)
    f2, loop2 = f"c{k + 1}", f"c{k + 2}"
    out3, prompt3 = _ask(api, nb, "What failed? Re-run those.", {
        "summary": "T2 (User list answers) failed with a 500; re-running only the failed cases.",
        "cells": [
            {"kind": "rule", "title": "Cases that failed last time", "spec": {"op": "filter",
             "left": f"{{{{cells.{loop}.rows}}}}", "where": [{"field": "status", "op": "eq", "value": "error"}]}},
            {"kind": "for_each", "title": "Run the failed cases again", "spec": {"over": f"{{{{cells.{f2}.rows.row}}}}"},
             "children": _case_children(app_site)},
        ],
        "notes": f"Test cases: bank.test_cases. Loop {loop}. Run 1: T2 failed (500 on /api/users); re-run in {loop2}."})
    assert "Your notes from earlier: Test cases: bank.test_cases (kind api|ui" in prompt3
    assert re.search(rf"Last run: run {run['n']} at .*, done\. Cells: .*{loop} ok \(1 of 5 rows failed\)", prompt3), \
        prompt3
    assert 'rows 1 (id "T2") failed; row 1: ' in prompt3, prompt3
    run3 = _run(api, nb, from_cell_id=out3["planned"][0]["id"])  # the studio's "run from here"
    by = {c["name"]: c for c in _get(api, nb)["cells"]}
    again = result_of(run3, by[loop2])
    assert run3["status"] == "done", run3
    assert again["output"]["ok"] == 1 and again["output"]["row_count"] == 1, again["output"]
    assert again["output"]["rows"][0]["row"]["id"] == "T2"
    assert "Run 1: T2 failed" in _get(api, nb)["notes"]
    print("\nUSE CASE 2: run 1", loop_res["output"]["summary"], "| re-run", again["output"]["summary"])


# ── 3. back-to-back commands ─────────────────────────────────────────────────────────────────────────────────────


def test_back_to_back_commands(nbx_jobs_api, wproj, app_site):
    api = nbx_jobs_api
    nb = _nb(api, wproj)

    # a. "log in in the browser"
    n = _next(api, nb)
    out, _ = _ask(api, nb, "log in in the browser", {
        "summary": "Sign in to the portal.", "notes": "Portal login works with PORTAL_LOGIN.",
        "cells": [{"kind": "browser", "title": "Sign in to the portal", "spec": {
            "base_url": app_site, "credential_slot": "PORTAL_LOGIN", "steps": _sign_in(app_site)}}]})
    run = _run(api, nb, from_cell_id=out["planned"][0]["id"])
    assert run["status"] == "done" and result_of(run, out["planned"][0])["status"] == "success", run

    # b. "now add Dee with balance 20000" (a fresh browser: the plan signs in again, and writes:true)
    out, prompt = _ask(api, nb, "now add a user Dee with balance 20000", {
        "summary": "Sign in and add Dee on the Users page.",
        "notes": "Portal login works with PORTAL_LOGIN. Added Dee (20000) via /users form.",
        "cells": [{"kind": "browser", "title": "Add Dee on the Users page", "spec": {
            "base_url": app_site, "credential_slot": "PORTAL_LOGIN", "writes": True, "steps": _sign_in(app_site) + [
                {"action": "goto", "url": "/users", "group": "Add user"},
                {"action": "fill", "target": {"label": "Name"}, "value": "Dee", "group": "Add user"},
                {"action": "fill", "target": {"label": "Balance"}, "value": "20000", "group": "Add user"},
                {"action": "click", "target": {"role": "button", "name": "Add user"}, "group": "Add user"},
                {"action": "expectVisible", "target": {"role": "cell", "name": "Dee"}, "group": "Add user"}]}}]})
    assert "Portal login works with PORTAL_LOGIN." in prompt and "Last run: run" in prompt
    add_cell = out["planned"][0]
    run = _run(api, nb, from_cell_id=add_cell["id"])
    assert run["status"] == "done" and result_of(run, add_cell)["status"] == "success", result_of(run, add_cell)
    assert any(x["name"] == "Dee" and x["premium"] for x in _App.users.values())

    # c. "now save the users to a table and check it" (store, then read the data table back)
    table = "portal_users_" + nb["id"][-6:]
    n = _next(api, nb)
    get_users, store, read, chk = (f"c{n + i}" for i in range(1, 5))
    out, prompt = _ask(api, nb, "now save the site's users to a table and check the table has Dee as premium", {
        "summary": "Fetch the users from the API, store them, read the table back and check Dee.",
        "cells": [
            {"kind": "http", "title": "Fetch the users", "spec": {"method": "GET", "url": app_site + "/api/users",
                                                                    "assertions": [{"kind": "status", "value": 200}]}},
            {"kind": "store", "title": "Save them to a table", "spec": {"new_table": table, "rows":
                                                                        f"{{{{cells.{get_users}.rows}}}}"}},
            {"kind": "data", "title": "Read the table back", "spec": {"from_table": table}},
            {"kind": "rule", "title": "Dee is premium in the table", "spec": {"op": "count", "left":
             f"{{{{cells.{read}.rows}}}}", "where": [{"field": "name", "op": "eq", "value": "Dee"},
                                                     {"field": "premium", "op": "eq", "value": True}]}},
        ]})
    run = _run(api, nb, from_cell_id=out["planned"][0]["id"])
    full = _get(api, nb)
    by = {c["name"]: c for c in full["cells"]}
    assert run["status"] == "done", [(c["name"], c["kind"], (result_of(run, c) or {}).get("status"),
                                      (result_of(run, c) or {}).get("error")) for c in full["cells"]
                                     if (result_of(run, c) or {}).get("status") not in (None, "success")]
    back = result_of(run, by[read])
    assert back["status"] == "success" and back["output"]["table"] == table and back["output"]["row_count"] == 4, back
    assert result_of(run, by[chk])["output"]["count"] == 1
    assert back["output"]["rows"][-1]["src_id"] == "U4"  # the reserved `id` is kept as src_id

    # d. "now call the API" for Dee; the prompt now lists the data table and the earlier runs
    k = _next(api, nb)
    out, prompt = _ask(api, nb, "now call the API for Dee and check she is premium", {
        "summary": "GET Dee from the API and check premium.",
        "cells": [{"kind": "http", "title": "Dee is premium in the API", "spec": {
            "method": "GET", "url": app_site + f"/api/users/{{{{cells.{read}.rows[-1].src_id}}}}", "assertions": [
                {"kind": "status", "value": 200}, {"kind": "json_path", "path": "$.premium", "value": True},
                {"kind": "json_path", "path": "$.name", "value": "Dee"}]}}]})
    assert f"- {table} (table_id " in prompt and "premium boolean" in prompt, prompt
    assert "Added Dee (20000)" in prompt
    run = _run(api, nb, from_cell_id=out["planned"][0]["id"])
    assert run["status"] == "done" and result_of(run, out["planned"][0])["output"]["passed"] is True, run
    # the whole notebook reads back: every planned cell is there, in order, with its latest result
    full = _get(api, nb)
    assert [c["kind"] for c in full["cells"] if c["parent_cell_id"] is None] == [
        "ask", "browser", "ask", "browser", "ask", "http", "store", "data", "rule", "ask", "http"]
    assert all(c["latest"] and c["latest"]["status"] == "success" for c in full["cells"]), \
        [(c["name"], c["latest"] and c["latest"]["status"]) for c in full["cells"]]
    print("\nUSE CASE 3: notes =", full["notes"])
