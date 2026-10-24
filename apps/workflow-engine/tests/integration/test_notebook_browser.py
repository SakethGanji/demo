"""Browser cells (BUILD-PLAN §1.2 browser.py, §3.1) against a real jobs-service + chromium.

The jobs-service is our own (``JOBS_DEV=1``, ``JOBS_LLM=off``, tmp data dir, free port; never :8095) driving a real
chromium at a local static site. Proven: a recipe with a credential slot passes and the secret is typed but never
stored; the trace and screenshots are downloadable; the live frame answers while the recipe is paused; a "Pay now"
click in a read-only recipe is blocked by the policy; an unset slot is ``unbound_credential``; with no runner the cell
is ``browser_unavailable`` and capabilities say so; the browser cells export to a Playwright ``.spec.ts``.

The jobs-backed tests skip (with the reason) only when node or Playwright's chromium is missing on the host.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from .nb_jobs_support import (  # noqa: F401
    _Site, add_secret, bank_db, jobs_service, make_project, nbx_api, nbx_engine, nbx_jobs_api, nbx_jobs_engine,
    target_site,
)
from .notebook_support import add, new_notebook, result_of, run_and_wait

SECRET = {"username": "ana.ops", "password": "pw-Zq81x"}
LOGIN_STEPS = [
    {"action": "goto", "url": "/login", "group": "Sign in"},
    {"action": "fill", "target": {"label": "Username"}, "value": "{{$cred.username}}", "group": "Sign in"},
    {"action": "fill", "target": {"label": "Password"}, "value": "{{$cred.password}}", "group": "Sign in"},
    {"action": "click", "target": {"role": "button", "name": "Sign in"}, "group": "Sign in"},
    {"action": "expectVisible", "target": {"role": "table"}, "group": "Accounts"},
]


@pytest.fixture(scope="module")
def jproj(nbx_jobs_api) -> dict[str, Any]:
    p = make_project(nbx_jobs_api)
    from .conftest import uniq

    p["slot"] = uniq("portal").replace("-", "_")
    add_secret(nbx_jobs_api, p, p["slot"], json.dumps(SECRET))
    return p


def _login_nb(api, proj, site: str, **spec: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    nb = new_notebook(api, proj)
    cell = add(api, nb, "browser", {"base_url": site, "credential_slot": proj.get("slot"), "steps": LOGIN_STEPS, **spec},
               title="Sign in and see the accounts")
    return nb, cell


# ── with the runner ──────────────────────────────────────────────────────────


def test_recipe_with_a_credential_slot_passes_and_keeps_the_secret_out(nbx_jobs_api, jproj, target_site):
    api = nbx_jobs_api
    assert api.get("/notebooks/capabilities").json()["browser"]["available"] is True
    _Site.seen.clear()
    nb, cell = _login_nb(api, jproj, target_site)
    run = run_and_wait(api, nb)
    res = result_of(run, cell)
    assert run["status"] == "done" and res["status"] == "success", res
    out = res["output"]
    assert out["step_count"] == 5 and out["passed"] == 5 and out["failed"] == 0
    assert [s["status"] for s in out["steps"]] == ["passed"] * 5
    assert res["job_id"]
    # the secret was typed into the form (the site saw it) ...
    assert any(r["path"].startswith("/accounts?") and "u=ana.ops" in r["path"] for r in _Site.seen), _Site.seen
    # ... but never stored in the notebook: not in the result, the cell, or the report
    stored = json.dumps([res, api.get(f"/notebooks/{nb['id']}").json(), api.get(f"/notebooks/{nb['id']}/report").json()])
    assert SECRET["password"] not in stored and SECRET["username"] not in stored

    names = [a["name"] for a in api.get(f"/notebook-cells/{cell['id']}/artifacts").json()]
    trace = [n for n in names if n.endswith("trace.zip")]
    assert trace, names
    assert any(n.endswith(".png") for n in names), names
    got = api.get(f"/notebook-cells/{cell['id']}/artifacts/{trace[0]}")
    assert got.status_code == 200 and got.content[:2] == b"PK" and len(got.content) > 1000
    assert api.get(f"/notebook-cells/{cell['id']}/artifacts/..%2F.env").status_code in (404, 422)
    # not running any more: control is refused, the frame is the last one or nothing
    assert api.post(f"/notebook-cells/{cell['id']}/control", {"action": "pause"}).status_code == 409
    fr = api.get(f"/notebook-cells/{cell['id']}/frame")
    assert fr.status_code in (200, 204)
    if fr.status_code == 200:
        assert fr.headers["content-type"].startswith("image/") and len(fr.content) > 100


def test_live_frame_and_pause_resume_while_running(nbx_jobs_api, jproj, target_site):
    api = nbx_jobs_api
    nb, cell = _login_nb(api, jproj, target_site)
    r = api.post(f"/notebooks/{nb['id']}/runs", {})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    paused = None
    deadline = time.time() + 60
    while time.time() < deadline:
        c = api.post(f"/notebook-cells/{cell['id']}/control", {"action": "pause"})
        if c.status_code == 200:
            paused = c.json()
            break
        assert c.status_code == 409, c.text
        time.sleep(0.05)
    assert paused and paused["job_id"], "the recipe never became controllable"
    frame = None
    deadline = time.time() + 20
    while time.time() < deadline:
        f = api.get(f"/notebook-cells/{cell['id']}/frame")
        if f.status_code == 200:
            frame = f
            break
        assert f.status_code == 204
        time.sleep(0.2)
    assert frame is not None, "no live frame while paused"
    assert frame.headers["content-type"].startswith("image/") and len(frame.content) > 100
    assert api.post(f"/notebook-cells/{cell['id']}/control", {"action": "resume"}).status_code == 200
    from .notebook_support import wait_run

    done = wait_run(api, run_id, timeout=120)
    assert result_of(done, cell)["status"] == "success", done


def test_take_over_relays_click_and_typing_then_hand_back_resumes(nbx_jobs_api, jproj, target_site):
    """Take over: pause the recipe after it opened the page, take it over, click the big button and type into the
    box through the engine, hand back; the scripted expectText passes only if the relayed input reached the page."""
    from .notebook_support import wait_run

    api = nbx_jobs_api
    nb = new_notebook(api, jproj)
    cell = add(api, nb, "browser", {"base_url": target_site, "steps": [
        {"action": "goto", "url": "/takeover"},
        {"action": "expectText", "target": {"role": "status", "name": "Result"}, "text": "clicked=yes typed=hello entered=yes"},
    ]}, title="Do it by hand")
    before = len([x for x in _Site.seen if x["path"] == "/takeover-loaded"])
    # input before a takeover is refused
    r = api.post(f"/notebooks/{nb['id']}/runs", {})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:  # pause as early as possible (before the goto runs)
        c = api.post(f"/notebook-cells/{cell['id']}/control", {"action": "pause"})
        if c.status_code == 200:
            break
        time.sleep(0.05)
    assert c.status_code == 200, c.text
    assert api.post(f"/notebook-cells/{cell['id']}/input", {"type": "key", "key": "Tab"}).status_code == 409
    # run exactly the goto, then wait until the page says it loaded
    assert api.post(f"/notebook-cells/{cell['id']}/control", {"action": "step"}).status_code == 200
    deadline = time.time() + 30
    while time.time() < deadline and len([x for x in _Site.seen if x["path"] == "/takeover-loaded"]) <= before:
        time.sleep(0.05)
    assert len([x for x in _Site.seen if x["path"] == "/takeover-loaded"]) > before, "the page never loaded"
    deadline = time.time() + 20
    while time.time() < deadline:
        t = api.post(f"/notebook-cells/{cell['id']}/control", {"action": "takeover"})
        if t.status_code == 200:
            break
        time.sleep(0.1)
    assert t.status_code == 200, t.text
    for ev in ({"type": "click", "x": 0.5, "y": 0.25}, {"type": "type", "text": "hello"}, {"type": "key", "key": "Enter"}):
        x = api.post(f"/notebook-cells/{cell['id']}/input", ev)
        assert x.status_code == 200, (ev, x.text)
    # bad input is refused by the engine (422) or the runner, never relayed
    assert api.post(f"/notebook-cells/{cell['id']}/input", {"type": "click", "x": 3, "y": 0}).status_code == 422
    assert api.post(f"/notebook-cells/{cell['id']}/input", {"type": "key", "key": "Meta+Q"}).status_code in (409, 422)
    # still taken over: the scripted step has not run (the run is still going)
    time.sleep(0.6)
    assert api.get(f"/notebook-runs/{run_id}").json()["status"] == "running"
    assert api.post(f"/notebook-cells/{cell['id']}/control", {"action": "handback"}).status_code == 200
    done = wait_run(api, run_id, timeout=120)
    res = result_of(done, cell)
    assert res["status"] == "success", res
    assert "hello" not in json.dumps(res["output"]), "typed text must not be stored with the result"
    # nothing kept holds the take-over session: the trace stops at take over, no video, no later screenshots
    import io
    import zipfile

    names = [a["name"] for a in api.get(f"/notebook-cells/{cell['id']}/artifacts").json()]
    assert not any(n.endswith(".webm") for n in names), names
    assert not any(n.endswith(".final.png") for n in names), names
    for n in (x for x in names if x.endswith(".trace.zip")):
        z = zipfile.ZipFile(io.BytesIO(api.get(f"/notebook-cells/{cell['id']}/artifacts/{n}").content))
        for e in z.namelist():
            body = z.read(e)
            assert b"keyboardType" not in body and b"hello" not in body, f"{n}/{e} holds take-over input"
    notes = res["output"].get("notes") or []
    assert any("take over" in x and "trace" in x for x in notes), res["output"]


def test_pay_now_is_blocked_in_a_read_only_recipe(nbx_jobs_api, jproj, target_site):
    api = nbx_jobs_api
    nb = new_notebook(api, jproj)
    steps = [{"action": "goto", "url": "/accounts"},
             {"action": "click", "target": {"role": "button", "name": "Pay now"}}]
    cell = add(api, nb, "browser", {"base_url": target_site, "steps": steps})
    res = result_of(run_and_wait(api, nb), cell)
    assert res["status"] == "error" and res["error"]["code"] == "browser_blocked", res
    assert [s["status"] for s in res["output"]["steps"]] == ["passed", "blocked"]
    assert res["output"]["blocked"]
    # the same recipe marked as writing is allowed to click it
    nb2 = new_notebook(api, jproj)
    cell2 = add(api, nb2, "browser", {"base_url": target_site, "steps": steps, "writes": True})
    res2 = result_of(run_and_wait(api, nb2), cell2)
    assert res2["status"] == "success", res2


def test_a_failing_step_names_the_step(nbx_jobs_api, jproj, target_site):
    api = nbx_jobs_api
    nb = new_notebook(api, jproj)
    cell = add(api, nb, "browser", {"base_url": target_site, "steps": [
        {"action": "goto", "url": "/accounts"},
        {"action": "expectText", "target": {"role": "heading", "name": "Accounts"}, "text": "Payments overview",
         "timeout_ms": 1500}]})
    res = result_of(run_and_wait(api, nb), cell)
    assert res["status"] == "error" and res["error"]["code"] == "browser_failed", res
    assert res["error"]["message"].startswith("step 2 failed"), res["error"]
    assert [s["status"] for s in res["output"]["steps"]] == ["passed", "failed"]


def test_an_unset_slot_is_unbound_credential(nbx_jobs_api, jproj, target_site):
    api = nbx_jobs_api
    nb, cell = _login_nb(api, {**jproj, "slot": "no_such_secret"}, target_site)
    res = result_of(run_and_wait(api, nb), cell)
    assert res["status"] == "error" and res["error"]["code"] == "unbound_credential", res
    assert res["job_id"] is None  # nothing was started


# ── without a runner / export ────────────────────────────────────────────────


def test_no_runner_is_browser_unavailable(nbx_api):
    api = nbx_api
    caps = api.get("/notebooks/capabilities").json()["browser"]
    assert caps["available"] is False and "unreachable" in caps["reason"]
    proj = make_project(api)
    nb = new_notebook(api, proj)
    cell = add(api, nb, "browser", {"base_url": "http://127.0.0.1:9", "steps": [{"action": "goto", "url": "/"}]})
    after = add(api, nb, "data", {"columns": ["x"], "rows": [{"x": 1}]})
    run = run_and_wait(api, nb)
    res = result_of(run, cell)
    assert res["status"] == "error" and res["error"]["code"] == "browser_unavailable", res
    assert result_of(run, after) is None or result_of(run, after)["status"] != "success"  # the run stopped there
    assert api.get(f"/notebook-cells/{cell['id']}/frame").status_code == 204
    assert api.get(f"/notebook-cells/{cell['id']}/artifacts").json() == []
    assert api.post(f"/notebook-cells/{cell['id']}/control", {"action": "stop"}).status_code == 409


def test_export_spec_ts(nbx_api):
    api = nbx_api
    proj = make_project(api)
    nb = new_notebook(api, proj, title="Portal checks")
    assert api.get(f"/notebooks/{nb['id']}/export.spec.ts").status_code == 422  # no browser cells yet
    add(api, nb, "browser", {"base_url": "https://portal.example.test", "credential_slot": "portal_login",
                             "steps": LOGIN_STEPS}, title="Sign in")
    add(api, nb, "browser", {"base_url": "https://portal.example.test",
                             "steps": [{"action": "goto", "url": "/accounts"},
                                       {"action": "expectVisible", "target": {"role": "table"}}]}, title="Accounts")
    r = api.get(f"/notebooks/{nb['id']}/export.spec.ts")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert 'filename="Portal-checks.spec.ts"' in r.headers["content-disposition"]
    text = r.text
    assert text.count("\ntest(") + text.startswith("test(") == 2, text
    import_lines = [ln for ln in text.splitlines() if ln.startswith("import ")]
    assert len(import_lines) == 1 and "from './fixtures.ts'" in import_lines[0] and "secret" in import_lines[0]
    assert "https://portal.example.test/login" in text  # relative gotos are made absolute
    assert "secret('username')" in text or 'secret("username")' in text, text
    assert "$cred" not in text and SECRET["password"] not in text
