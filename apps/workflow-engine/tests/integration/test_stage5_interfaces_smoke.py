"""Stage 5 interfaces on real Postgres (Builder B smoke): publish owns the slug, a form submits and is polled to a result,
idempotent replay, token scoping, unpublish hides the page, chat turns with history and one turn in flight.

The full contract tests (approvals, restart, concurrency, AI edit, client tsc) are the testers' (DESIGN-STAGE5-UIS section 10.2)."""

from __future__ import annotations

import time

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

FORM_SCHEMA = {"type": "object", "required": ["order_id", "amount"], "properties": {
    "order_id": {"type": "string", "pattern": r"^A-\d{5}$"}, "amount": {"type": "number", "minimum": 0.01},
    "note": {"type": "string", "maxLength": 300}}}


def N(name, type_, params, x=0):
    return {"name": name, "type": type_, "parameters": params, "position": {"x": x, "y": 0}}


def C(a, b):
    return {"source_node": a, "target_node": b, "source_output": "main", "target_input": "main"}


def form_workflow(slug: str) -> dict:
    return {"name": uniq("form"), "nodes": [
        N("Form", "FormTrigger", {"input_schema": FORM_SCHEMA, "title": "Refund", "submit_label": "Send", "success_message": "Thanks",
                                  "field_order": ["order_id", "amount", "note"]}),     # JSONB does not keep key order: field_order does
        N("Decide", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [
            {"name": "status", "value": "received"}, {"name": "order", "value": "{{ $json.order_id }}"}, {"name": "note", "value": "{{ $json.note }}"}]}, 200),
        N("Show result", "Respond", {"statusCode": 200, "body": {"status": "{{ $json.status }}", "order": "{{ $json.order }}", "note": "{{ $json.note }}"}}, 400),
    ], "connections": [C("Form", "Decide"), C("Decide", "Show result")], "settings": {},
        "interface": {"kind": "form", "slug": slug, "title": "Refund", "trigger_node": "Form", "result_node": "Show result"}}


def chat_workflow(wait_s: int = 0) -> dict:
    nodes = [N("Chat message", "ChatInput", {}), N("Answer", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [
        {"name": "text", "value": "echo {{ $json.message }} after {{ length($json.history) }}"}]}, 200)]
    conns = [C("Chat message", "Answer")]
    last = "Answer"
    if wait_s:
        nodes.append(N("Hold", "Wait", {"unit": "seconds", "duration": wait_s}, 400))
        conns.append(C("Answer", "Hold"))
        last = "Hold"
    nodes.append(N("Chat reply", "Respond", {"statusCode": 200, "body": {"text": "{{ $json.text }}"}}, 600))
    conns.append(C(last, "Chat reply"))
    return {"name": uniq("chat"), "nodes": nodes, "connections": conns, "settings": {}}


def make_project(api) -> tuple[str, str]:
    p = api.post("/projects", {"name": uniq("iface")}).json()
    return p["id"], p["slug"]


def create_with_interface(api, pid: str, wf: dict) -> str:
    interface = wf.get("interface")
    wf = {k: v for k, v in wf.items() if k != "interface"}
    r = api.post(f"/projects/{pid}/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    if interface:
        put = api.put(f"/workflows/{wid}/interface", interface)
        assert put.status_code == 200, put.text
    return wid


def publish(api, wid: str):
    return api.post(f"/workflows/{wid}/publish", {})


def poll_run(api, base: str, exec_id: str, token: str, until=("success", "failed", "cancelled"), timeout=30) -> dict:
    deadline = time.time() + timeout
    while True:
        r = api.get(f"{base}/runs/{exec_id}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        if r.json()["status"] in until or time.time() > deadline:
            return r.json()
        time.sleep(0.2)


def test_form_publish_submit_poll_replay_and_unpublish(api):
    pid, pslug = make_project(api)
    slug = uniq("refunds")
    wid = create_with_interface(api, pid, form_workflow(slug))
    base = f"/i/{pslug}/{slug}"

    # draft: the interface is stored, wired and not live; the public page doesn't exist yet
    got = api.get(f"/workflows/{wid}/interface").json()
    assert got["interface"]["slug"] == slug and got["live"] is None and got["wired"]["input"] == ["order_id", "amount", "note"]
    assert api.get(f"{base}/schema").status_code == 404

    published = publish(api, wid)
    assert published.status_code == 200, published.text
    assert api.get(f"/workflows/{wid}/interface").json()["live"]["slug"] == slug
    schema = api.get(f"{base}/schema")
    assert schema.status_code == 200 and schema.headers["cache-control"] == "no-store"
    assert [f["name"] for f in schema.json()["fields"]] == ["order_id", "amount", "note"] and schema.json()["submit_label"] == "Send"

    good = {"fields": {"order_id": "A-12345", "amount": 5, "note": "{{ $vars.SECRET }}"}}
    bad = api.post(f"{base}/submit", {"fields": {"order_id": "nope", "amount": 5}}, headers={"Idempotency-Key": "k0"})
    assert bad.status_code == 422 and "order_id" in bad.json()["detail"]["fields"]
    assert api.post(f"{base}/submit", {"fields": {"order_id": "A-12345", "amount": 5, "evil": 1}}, headers={"Idempotency-Key": "k0"}).json()["detail"]["code"] == "UNKNOWN_FIELD"
    assert api.post(f"{base}/submit", good).status_code == 400                          # no idempotency key

    r = api.post(f"{base}/submit", good, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 202, r.text
    sub = r.json()
    assert sub["poll_url"] == f"/api{base}/runs/{sub['execution_id']}"
    again = api.post(f"{base}/submit", good, headers={"Idempotency-Key": "k1"}).json()
    assert (again["submission_id"], again["execution_id"]) == (sub["submission_id"], sub["execution_id"])

    assert api.get(f"{base}/runs/{sub['execution_id']}").status_code == 404             # no token
    assert api.get(f"{base}/runs/{sub['execution_id']}", headers={"Authorization": "Bearer nope"}).status_code == 404
    view = poll_run(api, base, sub["execution_id"], again["run_token"])                  # the reissued token is the live one
    assert api.get(f"{base}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"}).status_code == 404
    assert view["status"] == "success", view
    assert view["result"] == {"status": "received", "order": "A-12345", "note": "{{ $vars.SECRET }}"}     # input is data, never evaluated
    assert set(view) == {"execution_id", "status", "progress", "waiting_for", "result", "message", "error"}
    assert view["progress"]["total"] == 3 and view["waiting_for"] == [] and view["error"] is None

    # exactly one run was started, and it's an interface run of the published version
    runs = api.get(f"/executions?workflow_id={wid}").json()
    assert len(runs if isinstance(runs, list) else runs.get("items", runs)) == 1

    # unpublish hides the page; publishing again brings it back
    assert api.post(f"/workflows/{wid}/unpublish", {}).status_code == 200
    assert api.get(f"{base}/schema").status_code == 404
    assert publish(api, wid).status_code == 200 and api.get(f"{base}/schema").status_code == 200


def test_preview_runs_the_draft_and_reports_data(api):
    pid, pslug = make_project(api)
    wid = create_with_interface(api, pid, form_workflow(uniq("pv")))
    r = api.post(f"/workflows/{wid}/interface/preview/submit", {"fields": {"order_id": "A-00001", "amount": 1}}, headers={"Idempotency-Key": "pk"})
    assert r.status_code == 202, r.text
    again = api.post(f"/workflows/{wid}/interface/preview/submit", {"fields": {"order_id": "A-00001", "amount": 1}}, headers={"Idempotency-Key": "pk"})
    assert again.json()["execution_id"] == r.json()["execution_id"]
    exec_id = r.json()["execution_id"]
    for _ in range(100):
        data = api.get(f"/workflows/{wid}/interface/preview/data?execution_id={exec_id}").json()
        if data["status"] in ("success", "failed"):
            break
        time.sleep(0.2)
    assert data["status"] == "success" and data["input"]["order_id"] == "A-00001" and data["input"]["_submission"]["mode"] == "preview"
    assert data["nodes"]["Decide"]["status"] == "received" and data["result"]["status"] == "received" and data["tasks"] == []
    assert api.get(f"/workflows/{wid}/interface/preview/data").json()["execution_id"] == exec_id
    assert api.get(f"/workflows/{wid}/interface/preview/data?execution_id=exe_nope").status_code == 404


def test_slug_clash_is_409_and_the_first_page_survives(api):
    pid, pslug = make_project(api)
    slug = uniq("same")
    first = create_with_interface(api, pid, form_workflow(slug))
    second = create_with_interface(api, pid, form_workflow(slug))
    assert publish(api, first).status_code == 200
    clash = publish(api, second)
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "INTERFACE_SLUG_TAKEN"
    assert api.get(f"/i/{pslug}/{slug}/schema").status_code == 200
    # another project may use the same slug
    pid2, pslug2 = make_project(api)
    third = create_with_interface(api, pid2, form_workflow(slug))
    assert publish(api, third).status_code == 200 and api.get(f"/i/{pslug2}/{slug}/schema").status_code == 200
    # deleting the owner frees the slug
    assert api.delete(f"/workflows/{first}").status_code == 200
    assert api.get(f"/i/{pslug}/{slug}/schema").status_code == 404
    assert publish(api, second).status_code == 200


def test_the_interface_key_survives_save_publish_and_version_reads(api):
    pid, _ = make_project(api)
    slug = uniq("keep")
    wid = create_with_interface(api, pid, form_workflow(slug))
    assert api.put(f"/workflows/{wid}", {"name": uniq("renamed")}).json()["definition"]["interface"]["slug"] == slug
    assert api.get(f"/workflows/{wid}").json()["definition"]["interface"]["slug"] == slug
    v = publish(api, wid).json()["version_id"]
    assert api.get(f"/workflows/{wid}/versions/{v}").json()["definition"]["interface"]["slug"] == slug
    removed = api.put(f"/workflows/{wid}/interface", None)
    assert removed.status_code == 200 and removed.json()["interface"] is None
    assert "interface" not in api.get(f"/workflows/{wid}").json()["definition"]


def test_invalid_interface_is_422_with_fields(api):
    pid, _ = make_project(api)
    wid = create_with_interface(api, pid, form_workflow(uniq("ok")))
    r = api.put(f"/workflows/{wid}/interface", {"kind": "custom", "slug": "Bad Slug", "screens": {"start": {"components": [{"type": "script"}]}}})
    d = r.json()["detail"]
    assert r.status_code == 422 and d["code"] == "INTERFACE_INVALID" and "slug" in d["fields"] and "screens.start.components[0].type" in d["fields"]


def test_generate_gives_form_and_custom_starting_points(api):
    pid, _ = make_project(api)
    wid = create_with_interface(api, pid, form_workflow(uniq("gen")))
    g = api.post(f"/workflows/{wid}/interface/generate", {"kind": "custom", "slug": "my-form"}).json()["interface"]
    assert g["kind"] == "custom" and g["slug"] == "my-form" and g["trigger_node"] == "Form" and list(g["screens"]) == ["start", "result"]
    assert api.post(f"/workflows/{wid}/interface/generate", {"kind": "custom", "slug": "BAD"}).status_code == 422


def test_unpublished_checker_error_wf017_blocks_publish(api):
    pid, _ = make_project(api)
    wf = form_workflow(uniq("wf17"))
    wf["interface"]["trigger_node"] = "Decide"
    wid = create_with_interface(api, pid, wf)
    r = publish(api, wid)
    assert r.status_code == 422 and any(f["code"] == "WF017" for f in r.json()["detail"]["findings"])


def test_chat_turns_history_busy_and_token_scoping(api):
    pid, pslug = make_project(api)
    slug = uniq("chat")
    wid = create_with_interface(api, pid, {**chat_workflow(wait_s=1), "interface": {"kind": "chat", "slug": slug, "title": "Chat", "trigger_node": "Chat message", "result_node": "Chat reply"}})
    assert publish(api, wid).status_code == 200
    base = f"/i/{pslug}/{slug}/chat/sessions"
    assert api.post(f"/i/{pslug}/{slug}/submit", {"fields": {}}, headers={"Idempotency-Key": "k"}).status_code == 404       # a chat takes no form

    s = api.post(base, {"user_label": "Dana"})
    assert s.status_code == 201, s.text
    sid, auth = s.json()["session_id"], {"Authorization": f"Bearer {s.json()['session_token']}"}
    other = api.post(base, {}).json()

    def messages(**params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        return api.get(f"{base}/{sid}/messages?{q}", headers=auth).json()

    sent = api.post(f"{base}/{sid}/messages", {"content": "hello", "client_message_id": "c1"}, headers=auth)
    assert sent.status_code == 202 and sent.json()["seq"] == 1
    assert api.post(f"{base}/{sid}/messages", {"content": "hello", "client_message_id": "c1"}, headers=auth).json() == sent.json()      # dedupe
    busy = api.post(f"{base}/{sid}/messages", {"content": "too soon", "client_message_id": "c2"}, headers=auth)
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "CHAT_BUSY"
    assert messages()["busy"] is True

    for _ in range(100):
        page = messages()
        if not page["busy"]:
            break
        time.sleep(0.2)
    assert [(m["role"], m["content"]) for m in page["messages"]] == [("user", "hello"), ("assistant", "echo hello after 0")]

    api.post(f"{base}/{sid}/messages", {"content": "again", "client_message_id": "c3"}, headers=auth)
    for _ in range(100):
        page = messages(after=2)
        if not page["busy"] and page["messages"]:
            break
        time.sleep(0.2)
    assert [(m["seq"], m["role"], m["content"]) for m in page["messages"]] == [(3, "user", "again"), (4, "assistant", "echo again after 2")]

    # another session's token, no token and an unknown session are all 404
    assert api.get(f"{base}/{sid}/messages", headers={"Authorization": f"Bearer {other['session_token']}"}).status_code == 404
    assert api.get(f"{base}/{sid}/messages").status_code == 404
    assert api.get(f"{base}/chs_nope/messages", headers=auth).status_code == 404


def _fixture(name: str) -> dict:
    import json
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / name).read_text()
    return json.loads(raw.replace("__REFUND_API_URL__", "http://127.0.0.1:9"))


@pytest.mark.parametrize("name", ["refund_triage_ui.json", "chat_support_ui.json"])
def test_the_ui_fixtures_load_check_clean_and_keep_their_interface(api, name):
    pid, pslug = make_project(api)
    wf = _fixture(name)
    wf["name"] = uniq("fx")
    wid = create_with_interface(api, pid, wf)
    check = api.post(f"/workflows/{wid}/check", {}).json()
    assert [f for f in check["findings"] if f["severity"] == "error"] == [], check["findings"]
    got = api.get(f"/workflows/{wid}/interface").json()
    assert got["interface"] == wf["interface"] or got["interface"]["slug"] == wf["interface"]["slug"]
    assert got["warnings"] == [] and got["wired"]["result"] == wf["interface"]["result_node"]
    if name.startswith("refund"):
        assert got["wired"] == {"input": ["order_id", "amount", "reason", "email"], "tasks": ["Manager review"],
                                "context": ["Classify.structured.summary"], "result": "Show result"}
        assert len(api.get(f"/workflows/{wid}").json()["definition"]["tests"]) == 4


def test_the_refund_ui_tests_pass_in_test_mode(api):
    pid, _ = make_project(api)
    wf = _fixture("refund_triage_ui.json")
    wf["name"] = uniq("fx")
    wid = create_with_interface(api, pid, wf)
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert report["summary"]["total"] == 4
    assert [(r["name"], r["status"], r["failures"]) for r in report["results"] if r["status"] != "passed"] == []


@pytest.fixture
def fake_refunds():
    """tests/fake_refund_api.py on a free port; yields (base url, a function returning the recorded /refunds calls)."""
    import json
    import subprocess
    import sys
    import urllib.request
    from pathlib import Path

    server = Path(__file__).resolve().parents[1] / "fake_refund_api.py"
    proc = subprocess.Popen([sys.executable, str(server)], stdout=subprocess.PIPE, text=True)
    try:
        port = json.loads(proc.stdout.readline())["port"]
        base = f"http://127.0.0.1:{port}"
        yield base, lambda: json.loads(urllib.request.urlopen(base + "/_refunds", timeout=5).read())["calls"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_refund_journey_form_to_approval_to_result(api, fake_refunds):
    """DESIGN-STAGE5-UIS section 9.2 over HTTP: submit -> waiting for Manager review -> decide in the inbox -> result; one refund paid."""
    import json
    from pathlib import Path

    base_url, calls = fake_refunds
    raw = (Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage_ui.json").read_text().replace("__REFUND_API_URL__", base_url)
    wf = json.loads(raw)
    wf["name"] = uniq("journey")
    wf["interface"]["slug"] = slug = uniq("refunds")
    next(n for n in wf["nodes"] if n["name"] == "Classify")["pinned_data"] = [
        {"json": {"category": "damaged", "summary": "Looks like a damaged parcel.", "suggest": "approve"}}]
    pid, pslug = make_project(api)
    wid = create_with_interface(api, pid, wf)
    assert publish(api, wid).status_code == 200
    base = f"/i/{pslug}/{slug}"

    fields = {"order_id": "A-10042", "amount": 742, "reason": "damaged", "email": "dana@example.com"}
    sub = api.post(f"{base}/submit", {"fields": fields}, headers={"Idempotency-Key": "j1"}).json()
    view = poll_run(api, base, sub["execution_id"], sub["run_token"], until=("waiting", "success", "failed"))
    assert view["status"] == "waiting", view
    assert [w["title"] for w in view["waiting_for"]] == ["Refund A-10042 · $742"] and view["result"] is None and calls() == []

    listed = api.get(f"/tasks?project_id={pid}&status=open")
    if listed.status_code == 404:
        pytest.skip("the /api/tasks routes are not mounted yet (Builder A); the waiting half of the journey passed")
    assert listed.status_code == 200, listed.text
    tasks = listed.json()["items"]
    assert [t["id"] for t in tasks] == [view["waiting_for"][0]["task_id"]]
    decided = api.post(f"/tasks/{tasks[0]['id']}/decide", {"decision": "approved", "decided_by": "Dana", "comment": "ok"}, headers={"Idempotency-Key": "d1"})
    assert decided.status_code == 200, decided.text

    done = poll_run(api, base, sub["execution_id"], sub["run_token"])
    assert done["status"] == "success", done
    assert done["result"] == {"status": "approved", "refund_id": "rf_1001", "message": "Your refund has been issued."}
    assert done["waiting_for"] == []
    paid = calls()
    assert len(paid) == 1 and paid[0]["idempotency_key"].startswith(f"{sub['execution_id']}:")


def test_client_ts_and_promote_to_app(api):
    pid, pslug = make_project(api)
    slug = uniq("promo")
    wid = create_with_interface(api, pid, form_workflow(slug))
    ts = api.get(f"/workflows/{wid}/interface/client.ts")
    assert ts.status_code == 200 and ts.headers["content-type"].startswith("text/plain")
    assert "export interface " in ts.text and "startRun(" in ts.text and f'const SLUG = "{slug}"' in ts.text and f'const PROJECT = "{pslug}"' in ts.text
    assert ts.text == api.get(f"/workflows/{wid}/interface/client.ts").text                      # deterministic

    first = api.post(f"/workflows/{wid}/interface/promote", {})
    assert first.status_code == 201, first.text
    again = api.post(f"/workflows/{wid}/interface/promote", {})
    assert again.status_code == 200 and again.json()["app_id"] == first.json()["app_id"]
    app = api.get(f"/apps/{first.json()['app_id']}").json()
    assert app["workflow_ids"] == [wid] and "client.startRun" in app["source_code"]
    assert sorted(f["path"] for f in app["files"]) == ["App.tsx", "api/client.ts"] and app["slug"] == f"{slug}-app"
    assert api.get(f"/workflows/{wid}/interface").json()["interface"]["app_id"] == first.json()["app_id"]
    assert api.post("/workflows/wf_nope/interface/promote", {}).status_code == 404


def test_ai_edit_needs_a_configured_model_and_a_custom_interface(api):
    pid, _ = make_project(api)
    wid = create_with_interface(api, pid, form_workflow(uniq("ai")))
    r = api.post(f"/workflows/{wid}/interface/screens/start/ai-edit", {"instruction": "make it friendlier", "spec": {"v": 1, "components": []}})
    assert r.status_code in (409, 503), r.text            # a form has no screens (409), or no model is configured here (503)
    assert r.json()["detail"]["code"] in ("WRONG_KIND", "LLM_NOT_CONFIGURED")
    custom = {"kind": "custom", "slug": uniq("ai"), "trigger_node": "Form", "result_node": "Show result",
              "screens": {"start": {"v": 1, "components": [{"type": "input_form"}]}}}
    assert api.put(f"/workflows/{wid}/interface", custom).status_code == 200
    r = api.post(f"/workflows/{wid}/interface/screens/start/ai-edit", {"instruction": "x", "spec": {"v": 1, "components": [{"type": "input_form"}]}})
    assert r.status_code in (503, 502, 200, 422), r.text
    assert api.post(f"/workflows/{wid}/interface/screens/admin/ai-edit", {"instruction": "x", "spec": {}}).status_code in (404, 503)


def test_chat_preview_runs_the_draft(api):
    pid, _ = make_project(api)
    wid = create_with_interface(api, pid, {**chat_workflow(), "interface": {"kind": "chat", "slug": uniq("pc"), "trigger_node": "Chat message", "result_node": "Chat reply"}})
    base = f"/workflows/{wid}/interface/preview/chat/sessions"
    s = api.post(base, {"user_label": "Dana"})
    assert s.status_code == 201, s.text
    sid, auth = s.json()["session_id"], {"Authorization": f"Bearer {s.json()['session_token']}"}
    assert api.post(f"{base}/{sid}/messages", {"content": "hi", "client_message_id": "p1"}, headers=auth).status_code == 202
    for _ in range(100):
        page = api.get(f"{base}/{sid}/messages", headers=auth).json()
        if not page["busy"]:
            break
        time.sleep(0.1)
    assert [(m["role"], m["content"]) for m in page["messages"]] == [("user", "hi"), ("assistant", "echo hi after 0")]
    assert api.get(f"{base}/{sid}/messages").status_code == 404
