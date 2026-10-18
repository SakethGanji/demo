"""Stage 5 T1: the anonymous public interface /api/i/{project}/{slug} (DESIGN-STAGE5-UIS 6.1, 7.2, 8, 10.2).

Anonymous submit, validation of every field kind, 404 that hides existence, token scoping, no internals in any
response, redaction, and the rate-limit hook (installed in-process against the real database).
"""

from __future__ import annotations

import asyncio
import json
import unicodedata
import uuid

import httpx
import pytest

from .conftest import StudioClient, uniq
from .test_stage5_t1_support import (  # noqa: F401  (fixtures + helpers)
    C, GOOD_FIELDS, N, Flow, Stack, concurrently, decide, new_project, poll_until, publish_flow, refund_flow, refund_workflow,
    run_view, stack, submit, submit_waiting, wait_run, wait_task,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def anon(stack) -> StudioClient:
    """A client with no studio headers at all: what a browser on the public page sends."""
    c = StudioClient(stack.engine.base_url)
    c.http.headers.pop("Content-Type", None)
    return c


def post_json(c: StudioClient, path: str, body, headers=None, **kw):
    return c.http.post("/api" + path, content=json.dumps(body) if not isinstance(body, (bytes, str)) else body,
                       headers={"Content-Type": "application/json", **(headers or {})}, **kw)


def n_subs(stack: Stack, flow: Flow) -> int:
    return stack.q("SELECT count(*) FROM interface_submissions WHERE workflow_id = %s", (flow.wid,))[0][0]


def n_execs(stack: Stack, flow: Flow) -> int:
    return stack.q("SELECT count(*) FROM executions WHERE workflow_id = %s", (flow.wid,))[0][0]


# ── anonymous submit and the schema ───────────────────────────────────────────


def test_an_anonymous_visitor_can_read_the_schema_and_submit_with_no_studio_headers(stack, anon):
    flow = refund_flow(stack)
    s = anon.http.get(f"/api{flow.base}/schema")
    assert s.status_code == 200 and s.headers["cache-control"] == "no-store"
    r = post_json(anon, f"{flow.base}/submit", {"fields": GOOD_FIELDS}, {"Idempotency-Key": "anon-1"})
    assert r.status_code == 202 and r.headers["cache-control"] == "no-store"
    sub = r.json()
    assert set(sub) == {"submission_id", "execution_id", "run_token", "poll_url"}
    assert sub["submission_id"].startswith("sub_") and sub["execution_id"].startswith("exec_")
    v = anon.http.get(f"/api{flow.base}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"})
    assert v.status_code == 200 and v.headers["cache-control"] == "no-store"
    wait_task(flow, sub)


def test_the_schema_carries_the_fields_in_field_order_and_nothing_of_the_workflow(stack, anon):
    flow = refund_flow(stack)
    body = anon.http.get(f"/api{flow.base}/schema")
    d = body.json()
    names = [f["name"] for f in d["fields"]]
    assert names == ["order_id", "amount", "reason", "email"]                          # field_order, not the JSON key order
    by = {f["name"]: f for f in d["fields"]}
    assert all(f["required"] for f in d["fields"])
    assert by["order_id"]["label"] == "Order number" and by["order_id"]["placeholder"] == "A-12345" and by["order_id"]["widget"] == "text"
    assert by["amount"]["widget"] == "currency" and by["amount"]["min"] == 0.01 and by["amount"]["max"] == 100000
    assert by["email"]["widget"] == "email"
    assert by["reason"]["widget"] == "radio" and [o["value"] for o in by["reason"]["options"]] == ["not_received", "damaged", "wrong_item", "other"]
    assert all({"value", "label"} <= set(o) for o in by["reason"]["options"])
    assert by["order_id"].get("pattern") == r"^A-\d{5}$"
    assert d["kind"] == "custom" and d["submit_label"] == "Request refund" and d["title"] == "Refund request"
    text = body.text
    for secret in ("Classify", "Manager review", "Issue refund", "Auto refund", "Over limit", "Review timed out", "Show result",
                   stack.refunds.url, "parameters", "pinned", "resumeKey", "{{", "Idempotency-Key", "AIAgent", "Approval", "HttpRequest"):
        assert secret not in text, f"/schema leaks {secret!r}"
    assert "task:Manager review" not in text and not any("task:" in k for k in (d.get("screens") or {}))   # task screens are never public


# ── validation ────────────────────────────────────────────────────────────────

BAD = [
    ("missing order_id", {k: v for k, v in GOOD_FIELDS.items() if k != "order_id"}, "order_id"),
    ("missing amount", {k: v for k, v in GOOD_FIELDS.items() if k != "amount"}, "amount"),
    ("missing reason", {k: v for k, v in GOOD_FIELDS.items() if k != "reason"}, "reason"),
    ("missing email", {k: v for k, v in GOOD_FIELDS.items() if k != "email"}, "email"),
    ("null required", {**GOOD_FIELDS, "email": None}, "email"),
    ("amount as string", {**GOOD_FIELDS, "amount": "742"}, "amount"),
    ("amount as bool", {**GOOD_FIELDS, "amount": True}, "amount"),
    ("amount as list", {**GOOD_FIELDS, "amount": [742]}, "amount"),
    ("amount under min", {**GOOD_FIELDS, "amount": 0}, "amount"),
    ("amount negative", {**GOOD_FIELDS, "amount": -5}, "amount"),
    ("amount over max", {**GOOD_FIELDS, "amount": 100001}, "amount"),
    ("order id pattern", {**GOOD_FIELDS, "order_id": "B-10042"}, "order_id"),
    ("order id too short", {**GOOD_FIELDS, "order_id": "A-1"}, "order_id"),
    ("order id with trailing junk", {**GOOD_FIELDS, "order_id": "A-10042; DROP TABLE"}, "order_id"),
    ("order id as number", {**GOOD_FIELDS, "order_id": 10042}, "order_id"),
    ("enum miss", {**GOOD_FIELDS, "reason": "buyers_remorse"}, "reason"),
    ("enum case", {**GOOD_FIELDS, "reason": "Damaged"}, "reason"),
    ("bad email", {**GOOD_FIELDS, "email": "not-an-email"}, "email"),
    ("email too long (default maxLength)", {**GOOD_FIELDS, "email": "a" * 4001 + "@example.com"}, "email"),
    ("unknown field", {**GOOD_FIELDS, "evil": 1}, "evil"),
    ("unknown field named like internals", {**GOOD_FIELDS, "_submission": {"id": "x"}}, "_submission"),
]


@pytest.mark.parametrize("label,fields,field", BAD, ids=[b[0] for b in BAD])
def test_invalid_submissions_are_422_with_the_field_named_and_start_nothing(stack, anon, label, fields, field):
    flow = _validation_flow(stack)
    subs, execs = n_subs(stack, flow), n_execs(stack, flow)
    r = post_json(anon, f"{flow.base}/submit", {"fields": fields}, {"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert field in (d.get("fields") or {}) or d.get("code") == "UNKNOWN_FIELD", d
    if label.startswith("unknown"):
        assert d["code"] == "UNKNOWN_FIELD"
    assert r.headers["cache-control"] == "no-store"
    assert (n_subs(stack, flow), n_execs(stack, flow)) == (subs, execs)            # nothing was recorded or run


_FLOWS: dict[int, Flow] = {}


def _validation_flow(stack: Stack) -> Flow:
    if id(stack) not in _FLOWS:
        _FLOWS[id(stack)] = refund_flow(stack)
    return _FLOWS[id(stack)]


def test_all_field_errors_are_reported_together(stack, anon):
    flow = _validation_flow(stack)
    r = post_json(anon, f"{flow.base}/submit", {"fields": {"order_id": "x", "amount": "y", "reason": "z", "email": "w"}}, {"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 422 and set(r.json()["detail"]["fields"]) == {"order_id", "amount", "reason", "email"}


@pytest.mark.parametrize("body", ['{"fields": []}', '{"fields": "x"}', '{"fields": null}', '{}', '{"fields": {}, "extra": 1}', '[]', '"x"', 'null'])
def test_malformed_bodies_are_rejected_without_starting_a_run(stack, anon, body):
    flow = _validation_flow(stack)
    subs = n_subs(stack, flow)
    r = post_json(anon, f"{flow.base}/submit", body, {"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code in (400, 422) and "Traceback" not in r.text, r.text
    assert n_subs(stack, flow) == subs


def test_not_json_is_400_and_a_missing_or_bad_idempotency_key_is_refused(stack, anon):
    flow = _validation_flow(stack)
    subs = n_subs(stack, flow)
    assert post_json(anon, f"{flow.base}/submit", "{not json", {"Idempotency-Key": "k"}).status_code == 400
    ok = {"fields": GOOD_FIELDS}
    assert post_json(anon, f"{flow.base}/submit", ok).status_code in (400, 422)                       # key is required
    assert post_json(anon, f"{flow.base}/submit", ok, {"Idempotency-Key": ""}).status_code in (400, 422)
    assert post_json(anon, f"{flow.base}/submit", ok, {"Idempotency-Key": "k" * 129}).status_code in (400, 422)   # <= 128 chars
    assert n_subs(stack, flow) == subs
    # R12: the key may come in the body when a proxy strips the header
    r = post_json(anon, f"{flow.base}/submit", {**ok, "idempotency_key": "from-body-" + uuid.uuid4().hex})
    assert r.status_code == 202, r.text


def test_the_body_cap_is_64kb_and_is_a_413_before_anything_runs(stack, anon):
    flow = _validation_flow(stack)
    subs = n_subs(stack, flow)
    just_under = json.dumps({"fields": {**GOOD_FIELDS, "email": "a@b.co"}, "idempotency_key": "x" * 100})
    assert len(just_under) < 64 * 1024
    big = json.dumps({"fields": {**GOOD_FIELDS, "email": "a" * (70 * 1024)}})
    r = post_json(anon, f"{flow.base}/submit", big, {"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    # chunked, with no content-length, is capped too
    def gen():
        for _ in range(80):
            yield b" " * 1024
    r2 = anon.http.post(f"/api{flow.base}/submit", content=gen(), headers={"Idempotency-Key": uuid.uuid4().hex, "Content-Type": "application/json"})
    assert r2.status_code == 413, r2.text
    assert n_subs(stack, flow) == subs


# ── input is data, never code ─────────────────────────────────────────────────


def echo_form(slug: str, *, extra_fields: list | None = None, access: dict | None = None) -> dict:
    schema = {"type": "object", "required": ["note"], "properties": {"note": {"type": "string", "maxLength": 300}, "qty": {"type": "integer", "minimum": 1}}}
    wf = {"name": uniq("echo"), "settings": {}, "nodes": [
        N("Form", "FormTrigger", {"input_schema": schema, "field_order": ["note", "qty"], "success_message": "Thanks, received"}),
        N("Echo", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "note", "value": "{{ $json.note }}"}, {"name": "qty", "value": "{{ $json.qty }}"},
                                                                          *(extra_fields or [])]}, 200),
        N("Show result", "Respond", {"statusCode": 200, "body": {"note": "{{ $json.note }}", "qty": "{{ $json.qty }}", **{f["name"]: "{{ $json." + f["name"] + " }}" for f in (extra_fields or [])}}}, 400),
    ], "connections": [C("Form", "Echo"), C("Echo", "Show result")],
        "interface": {"kind": "form", "slug": slug, "title": "Echo", "trigger_node": "Form", "result_node": "Show result"}}
    if access:
        wf["interface"]["access"] = access
    return wf


@pytest.mark.parametrize("note", ["{{ 1 + 1 }}", "{{ $vars.SECRET_KEY }}", "{{ $execution.id }}", "{{ $env.PATH }}", "{{ __import__('os').getcwd() }}", "${7*7}", "{% raw %}"])
def test_values_that_look_like_expressions_stay_literal_all_the_way_to_the_result(stack, anon, note):
    flow = publish_flow(stack, echo_form(uniq("lit")))
    sub = submit(flow, {"note": note, "qty": 2}, client=anon)
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"]["note"] == note, done


def test_strings_are_trimmed_and_nfc_normalised(stack, anon):
    flow = publish_flow(stack, echo_form(uniq("nfc")))
    decomposed = "café"                                    # e + combining acute
    sub = submit(flow, {"note": f"  {decomposed}  ", "qty": 1}, client=anon)
    done = wait_run(flow, sub)
    assert done["result"]["note"] == unicodedata.normalize("NFC", decomposed) == "café"
    assert len(done["result"]["note"]) == 4


def test_integer_fields_refuse_floats_and_a_whitespace_only_required_string_is_missing(stack, anon):
    flow = publish_flow(stack, echo_form(uniq("int")))
    for fields in ({"note": "x", "qty": 1.5}, {"note": "x", "qty": "2"}, {"note": "x", "qty": 0}, {"note": "   ", "qty": 1}, {"note": "x" * 301, "qty": 1}):
        r = post_json(anon, f"{flow.base}/submit", {"fields": fields}, {"Idempotency-Key": uuid.uuid4().hex})
        assert r.status_code == 422, (fields, r.text)


def test_a_secret_in_a_node_output_is_redacted_in_the_public_result(stack, anon):
    pid, pslug = new_project(stack.api)
    key = "S5_" + uuid.uuid4().hex[:8].upper()
    value = "s3cr3t-Value-" + uuid.uuid4().hex
    for env in ("dev", "prod"):
        r = stack.api.post(f"/projects/{pid}/secrets", {"key": key, "value": value, "environment": env, "type": "secret"})
        assert r.status_code == 201, r.text
    wf = echo_form(uniq("sec"), extra_fields=[{"name": "leak", "value": "{{ $vars." + key + " }}"}])
    flow = publish_flow(stack, wf, project=(pid, pslug))
    sub = submit(flow, {"note": "hello", "qty": 1}, client=anon)
    done = wait_run(flow, sub)
    assert done["status"] == "success"
    assert value not in json.dumps(done) and done["result"]["leak"] != value
    assert done["result"]["note"] == "hello"


# ── what an anonymous visitor may see of a run ────────────────────────────────


def failing_form(slug: str, hide_progress: bool = False) -> dict:
    wf = echo_form(slug, access={"mode": "public", "public_tasks": False, "hide_progress": hide_progress})
    wf["nodes"].insert(1, N("Internal Check", "StopAndError", {"errorType": "error", "message": "INTERNAL-DETAIL-do-not-show db password=hunter2"}, 100))
    wf["connections"] = [C("Form", "Internal Check"), C("Internal Check", "Echo"), C("Echo", "Show result")]
    return wf


def test_a_failed_run_shows_a_generic_error_and_nothing_of_the_node_or_message(stack, anon):
    flow = publish_flow(stack, failing_form(uniq("boom")))
    sub = submit(flow, {"note": "x", "qty": 1}, client=anon)
    done = wait_run(flow, sub)
    assert done["status"] == "failed" and done["result"] is None
    assert done["error"] == {"code": "RUN_FAILED", "message": f"Something went wrong. Reference {sub['execution_id']}"}
    blob = json.dumps({k: v for k, v in done.items() if k != "progress"})     # progress.current is the one place a node label may show (6.1)
    for leak in ("INTERNAL-DETAIL", "hunter2", "Internal Check", "StopAndError", "Traceback", "Echo"):
        assert leak not in blob
    assert "INTERNAL-DETAIL" not in json.dumps(done) and "hunter2" not in json.dumps(done)
    assert set(done) == {"execution_id", "status", "progress", "waiting_for", "result", "message", "error"}


def test_hide_progress_omits_the_progress_block(stack, anon):
    flow = publish_flow(stack, failing_form(uniq("hide"), hide_progress=True))
    sub = submit(flow, {"note": "x", "qty": 1}, client=anon)
    done = wait_run(flow, sub)
    assert not done.get("progress")


def test_a_waiting_run_shows_titles_only_and_a_progress_count(stack, anon):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    v = run_view(flow, sub, client=anon)
    assert v["status"] == "waiting" and v["result"] is None and v["error"] is None
    assert [(w["title"], w["task_id"]) for w in v["waiting_for"]] == [("Refund A-10042 \u00b7 $742", task["id"])]
    assert v["waiting_for"][0]["due_at"]
    assert set(v["waiting_for"][0]) <= {"task_id", "title", "due_at"}                  # titles only: no context, form or assignees
    assert v["progress"]["total"] == 10 and 3 <= v["progress"]["done"] <= 4
    text = json.dumps(v)
    assert "Summary" not in text and "damaged parcel" not in text and "dana@example.com" not in text
    decide(stack.api, task["id"], "rejected")
    done = wait_run(flow, sub)
    assert done["result"] == {"status": "rejected", "refund_id": "", "message": "Your refund was not approved."} and done["waiting_for"] == []


def test_a_run_without_a_result_node_shows_the_success_message(stack, anon):
    wf = echo_form(uniq("msg"))
    wf["interface"].pop("result_node")
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] != "Show result"]
    wf["connections"] = [C("Form", "Echo")]
    flow = publish_flow(stack, wf)
    done = wait_run(flow, submit(flow, {"note": "x", "qty": 1}, client=anon))
    assert done["status"] == "success"
    assert done["result"] is not None or done["message"] == "Thanks, received"        # the visitor is shown something (6.1: result, else success_message)


# ── tokens ────────────────────────────────────────────────────────────────────


def test_run_tokens_are_scoped_to_their_run_and_their_interface(stack, anon):
    a, b = refund_flow(stack), refund_flow(stack)
    sa, sb = submit(a), submit(a)
    other = submit(b)
    h = lambda t: {"Authorization": f"Bearer {t}"}
    get = lambda f, s, hdr=None, params=None: anon.http.get(f"/api{f.base}/runs/{s['execution_id']}", headers=hdr or {}, params=params)
    assert get(a, sa, h(sa["run_token"])).status_code == 200
    assert get(a, sb, h(sa["run_token"])).status_code == 404                  # another run of the same interface
    assert get(b, other, h(sa["run_token"])).status_code == 404               # another interface
    assert get(b, sa, h(other["run_token"])).status_code == 404               # wrong slug for the run
    assert get(a, sa).status_code == 404
    assert get(a, sa, {"Authorization": sa["run_token"]}).status_code == 404  # not a Bearer header
    assert get(a, sa, h(sa["run_token"][:-2] + "xx")).status_code == 404       # tampered
    assert get(a, sa, params={"token": sa["run_token"], "run_token": sa["run_token"], "t": sa["run_token"]}).status_code == 404   # never accepted in the URL
    assert get(a, {"execution_id": "exec_nope"}, h(sa["run_token"])).status_code == 404
    bodies = {get(a, sb, h("nope")).text, get(a, {"execution_id": "exec_nope"}, h(sa["run_token"])).text, get(a, sa).text}
    assert len(bodies) == 1, bodies                                         # one shape for every miss


def test_a_replayed_submit_reissues_a_working_token_for_the_same_run(stack, anon):
    flow = refund_flow(stack)
    one = submit(flow, key="replay-1", client=anon)
    two = submit(flow, key="replay-1", client=anon)
    assert (one["execution_id"], one["submission_id"]) == (two["execution_id"], two["submission_id"])
    assert run_view(flow, two, client=anon)["status"] in ("running", "waiting")


# ── 404 hides existence ───────────────────────────────────────────────────────

ROUTES = [
    ("GET", "/schema", None),
    ("POST", "/submit", {"fields": GOOD_FIELDS}),
    ("GET", "/runs/exec_x", None),
    ("POST", "/chat/sessions", {}),
    ("GET", "/chat/sessions/chs_x/messages", None),
    ("POST", "/chat/sessions/chs_x/messages", {"content": "hi", "client_message_id": "c"}),
]


def hit(anon: StudioClient, project: str, slug: str, method: str, tail: str, body):
    url = f"/api/i/{project}/{slug}{tail}"
    if method == "GET":
        return anon.http.get(url, headers={"Authorization": "Bearer nope"})
    return anon.http.post(url, content=json.dumps(body), headers={"Content-Type": "application/json", "Idempotency-Key": "k", "Authorization": "Bearer nope"})


@pytest.mark.parametrize("method,tail,body", ROUTES, ids=[f"{m} {t}" for m, t, _ in ROUTES])
def test_every_miss_is_the_same_404_whatever_the_reason(stack, anon, method, tail, body):
    never = hit(anon, "no-such-project", "no-such-slug", method, tail, body)
    assert never.status_code == 404 and never.headers["cache-control"] == "no-store"
    reasons = {}
    live = refund_flow(stack)
    reasons["unknown slug in a real project"] = (live.pslug, "no-such-slug")
    never_published = publish_flow(stack, refund_workflow(stack.refunds.url, uniq("draft")), publish=False)
    reasons["never published"] = (never_published.pslug, never_published.slug)
    unpub = refund_flow(stack)
    assert stack.api.post(f"/workflows/{unpub.wid}/unpublish", {}).status_code == 200
    reasons["unpublished"] = (unpub.pslug, unpub.slug)
    studio = refund_flow(stack, access={"mode": "studio", "public_tasks": False, "hide_progress": False})
    reasons["access.mode=studio"] = (studio.pslug, studio.slug)
    for why, (p, s) in reasons.items():
        r = hit(anon, p, s, method, tail, body)
        assert r.status_code == 404, f"{why}: {r.status_code} {r.text}"
        assert r.json() == never.json(), f"{why}: the 404 body differs from a page that never existed"
        assert r.headers["cache-control"] == "no-store"


def test_the_internal_project_id_is_not_a_public_address(stack, anon):
    """7.2 addresses a page by {project_slug}; the internal project id must not also resolve."""
    flow = refund_flow(stack)
    assert anon.http.get(f"/api{flow.base}/schema").status_code == 200
    r = anon.http.get(f"/api/i/{flow.pid}/{flow.slug}/schema")
    assert r.status_code == 404, r.text


def test_a_studio_only_interface_hides_even_a_run_that_started_before_the_switch(stack, anon):
    flow = refund_flow(stack)
    sub = submit(flow, client=anon)
    wait_task(flow, sub)
    iface = stack.api.get(f"/workflows/{flow.wid}/interface").json()["interface"]
    assert stack.api.put(f"/workflows/{flow.wid}/interface", {**iface, "access": {"mode": "studio", "public_tasks": False, "hide_progress": False}}).status_code == 200
    assert stack.api.post(f"/workflows/{flow.wid}/publish", {}).status_code == 200
    assert anon.http.get(f"/api{flow.base}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"}).status_code == 404
    assert anon.http.get(f"/api{flow.base}/schema").status_code == 404


def test_unpublish_hides_the_page_and_its_runs_and_republish_brings_them_back(stack, anon):
    flow = refund_flow(stack)
    sub = submit(flow, client=anon)
    wait_task(flow, sub)
    assert stack.api.post(f"/workflows/{flow.wid}/unpublish", {}).status_code == 200
    assert anon.http.get(f"/api{flow.base}/schema").status_code == 404
    assert post_json(anon, f"{flow.base}/submit", {"fields": GOOD_FIELDS}, {"Idempotency-Key": "late"}).status_code == 404
    assert anon.http.get(f"/api{flow.base}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"}).status_code == 404
    assert stack.api.post(f"/workflows/{flow.wid}/publish", {}).status_code == 200
    assert run_view(flow, sub, client=anon)["status"] == "waiting"


def test_the_public_surface_exposes_no_task_routes_by_default(stack, anon):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    for method, tail, body in (("GET", "/tasks", None), ("GET", f"/tasks/{task['id']}", None), ("POST", f"/tasks/{task['id']}/decide", {"decision": "approved", "decided_by": "x"}),
                               ("GET", f"/tasks/counts", None)):
        r = hit(anon, flow.pslug, flow.slug, method, tail, body)
        assert r.status_code in (404, 405), (tail, r.status_code, r.text)
        assert "Manager review" not in r.text
    from .test_stage5_t1_support import task_row
    assert task_row(stack, task["id"])["status"] == "open"
    decide(stack.api, task["id"], "rejected")
    wait_run(flow, sub)


def test_a_kind_mismatch_is_a_404_too(stack, anon):
    flow = refund_flow(stack)                                     # a custom (form) interface
    assert post_json(anon, f"{flow.base}/chat/sessions", {}).status_code == 404
    assert anon.http.get(f"/api{flow.base}/chat/sessions/chs_x/messages").status_code == 404


def test_unknown_methods_and_paths_do_not_leak_a_stack_trace(stack, anon):
    flow = refund_flow(stack)
    for r in (anon.http.delete(f"/api{flow.base}/schema"), anon.http.put(f"/api{flow.base}/submit", content=b"{}"),
              anon.http.get(f"/api{flow.base}/submit"), anon.http.get(f"/api/i/{flow.pslug}")):
        assert r.status_code in (404, 405) and "Traceback" not in r.text


# ── rate limit hook, in-process against the real database ─────────────────────


def _inprocess_app(db):
    """The public + task routers in a mini app whose sessions point at the throwaway database (never the dev one)."""
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from src.core.dependencies import get_db_session
    from src.routes import public_interfaces, tasks

    e = db.env()
    url = f"postgresql+asyncpg://{e['WORKFLOW_DB_USER']}:{e['WORKFLOW_DB_PASSWORD']}@{e['WORKFLOW_DB_HOST']}:{e['WORKFLOW_DB_PORT']}/{db.name}"
    engine = create_async_engine(url, pool_size=3, connect_args={"server_settings": {"search_path": '"workflow-app"'}})
    sf = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override():
        async with sf() as s:
            yield s

    app = FastAPI()
    app.include_router(public_interfaces.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.dependency_overrides[get_db_session] = override
    return app, engine


def test_the_rate_limit_hook_is_wired_to_every_public_route_and_a_refusal_starts_nothing(stack):
    from src.core import rate_limit

    live = refund_flow(stack)
    sub, task = submit_waiting(live)                   # a real task to try to decide
    subs_before, execs_before = n_subs(stack, live), n_execs(stack, live)
    seen: list[tuple[str, str, int]] = []
    deny: set[str] = set()

    class Spy:
        def check(self, bucket: str, key: str, cost: int = 1) -> None:
            seen.append((bucket, key, cost))
            if any(bucket.startswith(d) for d in deny):
                raise rate_limit.rate_limited(7)

    app, engine = _inprocess_app(stack.db)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://t") as c:
            b = f"/api{live.base}"
            assert (await c.get(f"{b}/schema", headers={"X-Forwarded-For": "6.6.6.6"})).status_code == 200
            assert (await c.get(f"{b}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"})).status_code == 200
            assert (await c.post(f"{b}/chat/sessions", json={})).status_code == 404     # a form: 404, not rate limited first
            deny.update({"iface.submit", "iface.poll", "task.decide"})
            r = await c.post(f"{b}/submit", json={"fields": GOOD_FIELDS}, headers={"Idempotency-Key": "rl-1"})
            assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED" and r.headers["retry-after"] == "7", r.text
            r = await c.get(f"{b}/schema")
            assert r.status_code == 429 and r.headers["retry-after"] == "7"
            r = await c.get(f"{b}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"})
            assert r.status_code == 429
            d = await c.post(f"/api/tasks/{task['id']}/decide", json={"decision": "approved", "decided_by": "Dana"}, headers={"Idempotency-Key": "rl-d"})
            assert d.status_code == 429 and d.headers["retry-after"] == "7"

    old = rate_limit.set_rate_limiter(Spy())
    try:
        asyncio.run(scenario())
    finally:
        rate_limit.set_rate_limiter(old)
        asyncio.run(engine.dispose())
    buckets = [s[0] for s in seen]
    assert f"iface.poll:{live.pslug}:{live.slug}" in buckets and f"iface.submit:{live.pslug}:{live.slug}" in buckets and "task.decide" in buckets
    assert all(k == "203.0.113.9" for _, k, _ in seen), seen                       # X-Forwarded-For is ignored without TRUSTED_PROXY
    assert (n_subs(stack, live), n_execs(stack, live)) == (subs_before, execs_before)        # the refused submit did not run anything
    from .test_stage5_t1_support import task_row
    assert task_row(stack, task["id"])["status"] == "open"                         # the refused decide did not decide
    assert decide(stack.api, task["id"], "rejected").json()["outcome"] == "decided"
    wait_run(live, sub)


def test_chat_routes_call_the_chat_send_bucket(stack):
    from src.core import rate_limit
    from .test_stage5_t1_chat import chat_flow

    flow = chat_flow(stack)
    seen: list[str] = []

    class Spy:
        def check(self, bucket, key, cost=1):
            seen.append(bucket)
            if bucket.startswith("chat.send"):
                raise rate_limit.rate_limited(3)

    app, engine = _inprocess_app(stack.db)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://t") as c:
            r = await c.post(f"/api{flow.base}/chat/sessions", json={})
            assert r.status_code == 429 and r.headers["retry-after"] == "3"

    old = rate_limit.set_rate_limiter(Spy())
    try:
        asyncio.run(scenario())
    finally:
        rate_limit.set_rate_limiter(old)
        asyncio.run(engine.dispose())
    assert seen == [f"chat.send:{flow.pslug}:{flow.slug}"]
    assert stack.q("SELECT count(*) FROM chat_sessions WHERE workflow_id = %s", (flow.wid,))[0][0] == 0
