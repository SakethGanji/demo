"""Independent Stage 6 test pass over HTTP on the fake-LLM engine with real Postgres (DESIGN-STAGE6-AI-BUILDER sections 4, 8, 10, 6.8, 2.1).

Written by a tester who did not build the feature. The contract is the design doc; the fake LLM is the hand-written one
(tests/fixtures/fake_llm/ai_builder/*.json). Nothing here calls a live model. A test marked ``xfail(strict=True, reason="BUG: ...")``
documents a real defect in feature code; it flips to a failure the day the defect is fixed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, EngineServer, StudioClient, fake_env, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
SCRIPTS = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder"
REQUEST = "Amounts over 500 are marked for review, the rest are approved automatically, and the caller always gets the status back."
FLAG = "Also flag amounts over 10000 as high value"


# ── helpers ───────────────────────────────────────────────────────────────────────────────────


def rules_of(name: str) -> list[dict]:
    return json.loads((SCRIPTS / f"{name}.json").read_text())["rules"]


def use(api: StudioClient, name_or_rules) -> None:
    rules = rules_of(name_or_rules) if isinstance(name_or_rules, str) else name_or_rules
    r = api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={"rules": rules})
    assert r.status_code == 200, r.text
    api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def calls(api: StudioClient) -> list[dict]:
    return api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()


def sys_line(call: dict) -> str:
    return call["messages"][0]["content"].split("\n")[0]


def lines(api: StudioClient) -> list[str]:
    return [sys_line(c) for c in calls(api)]


def user_payload(call: dict) -> dict:
    return json.loads(call["messages"][-1]["content"])


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})


@pytest.fixture
def pid(fake_api, clean) -> str:
    r = fake_api.post("/projects", {"name": uniq("ab")})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def err(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    d = r.json()["detail"]
    assert d["code"] == code and d["message"], d
    return d


def start(api, pid: str, actor: str = "Asha", request: str | None = None, **extra) -> dict:
    # a unique request each time: spec answers are cached in the engine by prompt hash
    r = api.post("/ai/builder/spec", {"project_id": pid, "request": request or f"{REQUEST} ({uniq('r')})", "actor": actor, **extra})
    assert r.status_code == 201, r.text
    return r.json()["proposal"]


def approve(api, p: dict, actor: str = "Asha") -> dict:
    r = api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": actor})
    assert r.status_code == 200, r.text
    return r.json()


def build(api, p: dict, actor: str = "Asha") -> dict:
    r = api.post(f"/ai/proposals/{p['id']}/build", {"actor": actor})
    assert r.status_code == 200, r.text
    return r.json()


def ready(api, pid: str, script: str = "first_try", **extra) -> dict:
    use(api, script)
    p = start(api, pid, **extra)
    approve(api, p)
    return build(api, p)


def kept(api, pid: str) -> tuple[str, dict]:
    b = ready(api, pid)
    k = api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    return k.json()["workflow_id"], b


def definition(api, wid: str) -> dict:
    r = api.get(f"/workflows/{wid}")
    assert r.status_code == 200, r.text
    return r.json()["definition"]


def put_def(api, wid: str, d: dict, **over) -> object:
    body = {"nodes": d["nodes"], "connections": d["connections"], "settings": d.get("settings") or {}, "tests": d["tests"], **over}
    return api.put(f"/workflows/{wid}", body)


def workflows_of(api, pid: str) -> list[dict]:
    body = api.get(f"/projects/{pid}/workflows").json()
    return body if isinstance(body, list) else body.get("items", [])


def events(api, proposal_id: str) -> list[dict]:
    return api.get(f"/ai/proposals/{proposal_id}/events").json()["items"]


def fee_workflow(api, pid: str) -> str:
    test = {"id": "t_ab000001", "name": "fee is 1% of the amount", "target": {"kind": "workflow"}, "category": "normal",
            "input": {"items": [{"body": {"amount": 1000}, "headers": {}, "query": {}, "method": "POST"}]}, "mocks": {},
            "expect": [{"node": "Compute fee", "path": "fee", "op": "eq", "value": 10}]}
    body = {"name": uniq("fee"), "settings": {}, "tests": [test], "nodes": [
        {"name": "Receive amount", "type": "Webhook", "parameters": {"method": "POST", "path": uniq("fee"), "responseMode": "lastNode"}, "position": {"x": 0, "y": 0}},
        {"name": "Compute fee", "type": "Set", "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "fee", "value": "{{ $json.body.amnt * 0.01 }}"}]},
         "position": {"x": 280, "y": 0}},
        {"name": "Reply", "type": "Respond", "parameters": {"statusCode": 200, "body": {"fee": "{{ $json.fee }}"}}, "position": {"x": 560, "y": 0}}],
        "connections": [{"source_node": "Receive amount", "target_node": "Compute fee", "source_output": "main", "target_input": "main"},
                        {"source_node": "Compute fee", "target_node": "Reply", "source_output": "main", "target_input": "main"}]}
    r = api.post(f"/projects/{pid}/workflows", body)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert api.post(f"/workflows/{wid}/tests/t_ab000001/approve", {"approved_by": "sam"}).status_code == 200
    return wid


def shape(d: dict) -> list:
    """The parts of a definition that identify it, whatever extras (labels, port lists) a view adds to its nodes."""
    return [(n["name"], n["type"], n["parameters"]) for n in d["nodes"]]


def walk_keys(obj, found: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            walk_keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_keys(v, found)
    return found


# ── 1. spec -> approve -> build -> keep / undo / discard ─────────────────────────────────────────────


def test_a_spec_cannot_be_edited_or_approved_twice_after_approval_and_nothing_is_saved_before_keep(fake_api, pid):
    use(fake_api, "first_try")
    before = len(workflows_of(fake_api, pid))
    p = start(fake_api, pid)
    a = approve(fake_api, p)
    assert a["status"] == "spec_approved" and re.fullmatch(r"[0-9a-f]{64}", a["spec_fingerprint"])
    assert a["spec_approved_by"] == "Asha" and all(t["status"] == "approved" and t["locked"] is True for t in a["spec"]["tests"])
    spec = {k: v for k, v in a["spec"].items() if k not in ("issues", "warnings")}
    err(fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"}), 409, "bad_state")
    again = fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"})
    assert again.status_code in (200, 409) and fake_api.get(f"/ai/proposals/{p['id']}").json()["spec_fingerprint"] == a["spec_fingerprint"]
    b = build(fake_api, p)
    assert b["status"] == "ready" and b["workflow_id"] is None and b["base_definition"] is None
    assert len(workflows_of(fake_api, pid)) == before                      # nothing exists until Keep
    err(fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}), 409, "bad_state")     # a built proposal is not built twice


def test_keep_creates_the_workflow_with_locked_approved_tests_that_run_green_and_cannot_be_deleted(fake_api, pid):
    b = ready(fake_api, pid)
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha", "name": "Refund triage"}).json()
    wid = k["workflow_id"]
    d = definition(fake_api, wid)
    assert fake_api.get(f"/workflows/{wid}").json()["name"] == "Refund triage"
    assert [t["status"] for t in d["tests"]] == ["approved"] * len(d["tests"]) and all(t["locked"] for t in d["tests"])
    run = fake_api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert run["summary"]["failed"] == 0 and run["summary"]["passed"] == len(d["tests"])
    # the approved tests are the gate for every later save: removing one is refused by the normal save path
    r = put_def(fake_api, wid, {**d, "tests": d["tests"][1:]})
    assert r.status_code == 409, r.text
    assert len(definition(fake_api, wid)["tests"]) == len(d["tests"])
    ev = [e for e in events(fake_api, b["id"]) if e["event"] == "kept"]
    assert ev and ev[0]["payload"]["workflow_id"] == wid and ev[0]["payload"]["accepted_failing"] is False and ev[0]["actor"] == "Asha"


def test_keep_undo_and_discard_accept_the_actor_in_the_header(fake_api, pid):
    b = ready(fake_api, pid)
    err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {}), 422, "actor_required")
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {}, headers={"X-Actor-Name": "Hana"})
    assert k.status_code == 200 and k.json()["proposal"]["kept_by"] == "Hana"
    err(fake_api.post(f"/ai/proposals/{b['id']}/undo", {}), 422, "actor_required")
    u = fake_api.post(f"/ai/proposals/{b['id']}/undo", {}, headers={"X-Actor-Name": "Hana"})
    assert u.status_code == 200 and u.json()["status"] == "undone"
    assert [e["actor"] for e in events(fake_api, b["id"]) if e["event"] in ("kept", "undone")] == ["Hana", "Hana"]
    b2 = ready(fake_api, pid)
    err(fake_api.post(f"/ai/proposals/{b2['id']}/discard", {}), 422, "actor_required")
    assert fake_api.post(f"/ai/proposals/{b2['id']}/discard", {}, headers={"X-Actor-Name": "Hana"}).status_code == 200


def test_a_kept_proposal_cannot_be_discarded_and_an_undone_one_cannot_be_kept_again(fake_api, pid):
    wid, b = kept(fake_api, pid)
    err(fake_api.post(f"/ai/proposals/{b['id']}/discard", {"actor": "Asha"}), 409, "bad_state")
    assert definition(fake_api, wid)["nodes"]
    assert fake_api.post(f"/ai/proposals/{b['id']}/undo", {"actor": "Asha"}).status_code == 200
    err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    again = fake_api.post(f"/ai/proposals/{b['id']}/undo", {"actor": "Asha"})
    assert again.status_code == 200 and again.json()["status"] == "undone"          # a second Undo is the first one's answer


# ── 2. the proposal view ─────────────────────────────────────────────────────────────────────────────


def test_the_view_never_carries_prompts_and_carries_usage_policy_and_base_definition(fake_api, pid):
    b = ready(fake_api, pid)
    seen = calls(fake_api)
    assert len(seen) >= 2
    shown = json.dumps(fake_api.get(f"/ai/proposals/{b['id']}").json())
    forbidden_keys = {"prompt", "prompts", "system", "user", "messages", "system_prompt", "user_prompt"}
    assert not (walk_keys(json.loads(shown), set()) & forbidden_keys)
    for c in seen:                                                    # no prompt text, not even a long slice of one, is in the view or the events
        sysmsg = c["messages"][0]["content"]
        assert sysmsg[:120] not in shown
        assert sysmsg[:120] not in json.dumps(events(fake_api, b["id"]))
    view = fake_api.get(f"/ai/proposals/{b['id']}").json()
    assert set(view["usage"]) >= {"llm_calls", "input_tokens", "output_tokens", "cost_usd", "duration_ms"}
    assert view["usage"]["llm_calls"] == 2 and view["usage"]["cost_usd"] is None and view["usage"]["duration_ms"] > 0       # O8: no invented prices
    assert view["policy"] == {"model": "gemini-3.5-flash", "provider": "gemini", "region": None}       # WF_AI_REGION unset: recorded as null (ambiguity A1)
    assert "base_definition" in view and view["base_definition"] is None                 # a new workflow has no base
    for k in ("spec", "proposed_definition", "diff", "report", "test_change_requests", "request_sha256", "spec_fingerprint", "base_draft_hash"):
        assert k in view, k
    # the audit holds hashes: one per call, and the hash is the one of the prompt that was really sent (model + system + user)
    llm = [e["payload"] for e in events(fake_api, b["id"]) if e["event"] == "llm_call"]
    assert len(llm) == len(seen) and all(re.fullmatch(r"[0-9a-f]{64}", x["prompt_sha256"]) for x in llm)
    assert not any({"system", "user", "prompt"} & set(x) for x in llm)                    # WF_AI_STORE_PROMPTS is off by default (O5)
    sent = {hashlib.sha256(f"{c['model']}\n{c['messages'][0]['content']}\n{c['messages'][-1]['content']}".encode()).hexdigest() for c in seen}
    assert {x["prompt_sha256"] for x in llm} == sent


def test_the_request_hash_proves_the_original_and_the_view_holds_only_the_scrubbed_request(fake_api, pid):
    use(fake_api, "first_try")
    original = f"{REQUEST} ({uniq('r')}) Customer jo.kim@bank-example.com paid with 4111 1111 1111 1111."
    p = start(fake_api, pid, request=original)
    assert p["request_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert "4111" not in p["request"] and "jo.kim@bank-example.com" not in p["request"] and "500" in p["request"]       # a threshold is not PII


def test_the_audit_list_filters_by_status_and_workflow_and_events_page_by_seq(fake_api, pid):
    wid, b = kept(fake_api, pid)
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    only_wf = fake_api.get("/ai/proposals", params={"project_id": pid, "workflow_id": wid}).json()["items"]
    assert {i["id"] for i in only_wf} == {b["id"], m["id"]}
    assert [i["id"] for i in fake_api.get("/ai/proposals", params={"project_id": pid, "status": "kept"}).json()["items"]] == [b["id"]]
    row = next(i for i in only_wf if i["id"] == b["id"])
    assert row["kind"] == "build" and row["actor"] == "Asha" and row["passed"] is True and row["workflow_id"] == wid and row["llm_calls"] == 2
    evs = fake_api.get(f"/ai/proposals/{b['id']}/events").json()
    assert [e["seq"] for e in evs["items"]] == list(range(1, len(evs["items"]) + 1))
    past = fake_api.get(f"/ai/proposals/{b['id']}/events", params={"after": evs["last_seq"]}).json()
    assert past["items"] == [] and past["last_seq"] >= evs["last_seq"]
    assert fake_api.get(f"/ai/proposals/{b['id']}/events", params={"after": -1}).status_code == 422


def test_builder_config_and_policy_refusal_is_a_403_with_no_model_call(fake_api, pid):
    use(fake_api, "first_try")
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x", "actor": "A", "model": "gpt-4o"}), 403, "model_not_allowed")
    assert calls(fake_api) == []
    cfg = fake_api.get("/ai/builder/config").json()
    assert cfg["allowed_models"] == ["gemini-3.5-flash"] and cfg["send_run_values"] is False


# ── 3. modify mode ───────────────────────────────────────────────────────────────────────────────────


def test_modify_saves_nothing_before_keep_and_the_view_carries_the_exact_base(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    original = fake_api.get(f"/workflows/{wid}").json()
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    assert m["kind"] == "modify" and shape(m["base_definition"]) == shape(original["definition"]) and m["base_draft_hash"]
    # the modify spec lists only the new/changed steps and the model was shown the locked tests of the current workflow
    first = user_payload(calls(fake_api)[0])
    assert first["mode"] == "modify" and {t["id"] for t in first["locked_tests"]} == {t["id"] for t in original["definition"]["tests"]}
    approve(fake_api, m)
    mb = build(fake_api, m)
    assert mb["status"] == "ready" and mb["diff"]["counts"]["added"] == 1 and mb["diff"]["counts"]["removed"] == 0
    assert mb["diff"]["nodes"]["added"] == ["Flag high value"]
    assert fake_api.get(f"/workflows/{wid}").json() == original                  # not one byte of the saved workflow moved
    k = fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}).json()
    assert k["workflow_id"] == wid and k["draft_hash"]
    assert "Flag high value" in [n["name"] for n in definition(fake_api, wid)["nodes"]]
    assert len(workflows_of(fake_api, pid)) == 1                                  # a modify never creates a second workflow


def test_undo_after_keep_restores_the_exact_prior_draft(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    before = definition(fake_api, wid)
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    approve(fake_api, m)
    build(fake_api, m)
    assert fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}).status_code == 200
    assert definition(fake_api, wid) != before
    assert fake_api.post(f"/ai/proposals/{m['id']}/undo", {"actor": "Asha"}).status_code == 200
    assert definition(fake_api, wid) == before


def test_undo_removes_only_the_proposals_own_tests_and_keeps_a_humans_draft_test(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    d = definition(fake_api, wid)
    human = {"id": "t_d00dfeed", "name": "human draft: a zero amount", "target": {"kind": "workflow"}, "category": "boundary", "status": "draft", "locked": False,
             "input": {"items": [{"body": {"amount": 0}, "headers": {}, "query": {}, "method": "POST"}]}, "mocks": {},
             "expect": [{"node": "Reply", "op": "status", "value": "success"}]}
    r = put_def(fake_api, wid, {**d, "tests": d["tests"] + [human]})
    assert r.status_code == 200, r.text
    before = definition(fake_api, wid)
    assert [t["id"] for t in before["tests"]][-1] == "t_d00dfeed"
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    approve(fake_api, m)
    mb = build(fake_api, m)
    own = [t["id"] for t in mb["spec"]["tests"] if t["id"] not in {x["id"] for x in before["tests"]}]
    assert len(own) == 1
    assert fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}).status_code == 200
    after_keep = definition(fake_api, wid)
    assert own[0] in [t["id"] for t in after_keep["tests"]] and "t_d00dfeed" in [t["id"] for t in after_keep["tests"]]
    assert fake_api.post(f"/ai/proposals/{m['id']}/undo", {"actor": "Asha"}).status_code == 200
    after = definition(fake_api, wid)
    assert own[0] not in [t["id"] for t in after["tests"]]                                  # its own test goes
    assert [t["id"] for t in after["tests"]] == [t["id"] for t in before["tests"]]          # the locked ones and the human's draft stay
    assert after["tests"] == before["tests"]


def test_undo_of_a_new_workflow_deletes_it_only_if_it_was_never_published(fake_api, pid):
    wid, b = kept(fake_api, pid)
    assert fake_api.post(f"/ai/proposals/{b['id']}/undo", {"actor": "Asha"}).status_code == 200
    assert fake_api.get(f"/workflows/{wid}").status_code == 404 and workflows_of(fake_api, pid) == []

    wid2, b2 = kept(fake_api, pid)
    pub = fake_api.post(f"/workflows/{wid2}/publish", {})
    assert pub.status_code == 200, pub.text
    d = err(fake_api.post(f"/ai/proposals/{b2['id']}/undo", {"actor": "Asha"}), 409, "bad_state")
    assert "published" in d["message"].lower()
    assert fake_api.get(f"/workflows/{wid2}").status_code == 200
    assert fake_api.get(f"/ai/proposals/{b2['id']}").json()["status"] == "kept"           # the refusal did not flip the row
    # having been published once is enough, even after it is switched off again
    assert fake_api.post(f"/workflows/{wid2}/unpublish", {}).status_code == 200
    err(fake_api.post(f"/ai/proposals/{b2['id']}/undo", {"actor": "Asha"}), 409, "bad_state")
    assert fake_api.get(f"/workflows/{wid2}").status_code == 200


def test_discard_leaves_no_trace_in_the_workflow_store(fake_api, fake_stack, pid):
    _, db = fake_stack
    execs = db.query("SELECT count(*) FROM executions")[0][0]
    b = ready(fake_api, pid)
    assert fake_api.post(f"/ai/proposals/{b['id']}/discard", {"actor": "Asha"}).json()["status"] == "discarded"
    assert workflows_of(fake_api, pid) == [] and db.query("SELECT count(*) FROM executions")[0][0] == execs
    assert db.query("SELECT count(*) FROM workflows WHERE project_id = %s", (pid,))[0][0] == 0

    wid, _ = kept(fake_api, pid)
    snapshot = fake_api.get(f"/workflows/{wid}").json()
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    approve(fake_api, m)
    build(fake_api, m)
    assert fake_api.post(f"/ai/proposals/{m['id']}/discard", {"actor": "Asha"}).status_code == 200
    assert fake_api.get(f"/workflows/{wid}").json() == snapshot
    err(fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}), 409, "bad_state")


def test_a_discarded_spec_draft_cannot_be_approved(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid)
    assert fake_api.post(f"/ai/proposals/{p['id']}/discard", {"actor": "Asha"}).json()["status"] == "discarded"
    err(fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}), 409, "bad_state")
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    err(fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"}), 409, "bad_state")


# ── 4. concurrency and the stale base ────────────────────────────────────────────────────────────────


def test_a_concurrent_build_of_the_same_proposal_runs_once_and_the_others_are_refused(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid)
    approve(fake_api, p)
    with ThreadPoolExecutor(5) as ex:
        rs = list(ex.map(lambda _: fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}), range(5)))
    assert sorted(r.status_code for r in rs).count(200) == 1, [r.text[:200] for r in rs]
    for r in rs:
        if r.status_code != 200:
            assert r.status_code == 409 and r.json()["detail"]["code"] in ("builder_busy", "bad_state"), r.text
    assert lines(fake_api).count("WF_AI:BUILD") == 1                                  # the model was asked to build once
    v = fake_api.get(f"/ai/proposals/{p['id']}").json()
    assert v["status"] == "ready" and v["usage"]["llm_calls"] == 2
    assert [e["event"] for e in events(fake_api, p["id"])].count("build_finished") == 1


def test_builds_of_different_actors_run_in_parallel_and_a_busy_lock_is_always_released(fake_api, pid):
    use(fake_api, "first_try")
    a, b = start(fake_api, pid, actor="Asha"), start(fake_api, pid, actor="Ben")
    approve(fake_api, a, "Asha")
    approve(fake_api, b, "Ben")
    with ThreadPoolExecutor(2) as ex:
        ra, rb = list(ex.map(lambda t: fake_api.post(f"/ai/proposals/{t[0]['id']}/build", {"actor": t[1]}), [(a, "Asha"), (b, "Ben")]))
    assert ra.status_code == 200 and rb.status_code == 200, (ra.text[:200], rb.text[:200])

    # one actor, two proposals at once: a clash is a 409 builder_busy, never a 500, and the loser can be built afterwards
    c, d = start(fake_api, pid, actor="Cy"), start(fake_api, pid, actor="Cy")
    approve(fake_api, c, "Cy")
    approve(fake_api, d, "Cy")
    with ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda t: fake_api.post(f"/ai/proposals/{t['id']}/build", {"actor": "Cy"}), [c, d]))
    assert {r.status_code for r in rs} <= {200, 409}
    for r, prop in zip(rs, (c, d)):
        if r.status_code == 409:
            assert r.json()["detail"]["code"] == "builder_busy"
            assert fake_api.get(f"/ai/proposals/{prop['id']}").json()["status"] == "spec_approved"       # untouched
            assert fake_api.post(f"/ai/proposals/{prop['id']}/build", {"actor": "Cy"}).status_code == 200  # the lock was released


def test_a_stale_base_is_detected_on_keep_and_the_persons_edit_survives(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    approve(fake_api, m)
    build(fake_api, m)
    d = definition(fake_api, wid)
    next(n for n in d["nodes"] if n["name"] == "Reply")["parameters"]["statusCode"] = 202
    assert put_def(fake_api, wid, d).status_code == 200
    edited = fake_api.get(f"/workflows/{wid}").json()
    det = err(fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}), 409, "draft_moved")
    assert "changed" in det["message"].lower() or "moved" in det["message"].lower()
    assert fake_api.get(f"/workflows/{wid}").json() == edited                           # the refusal wrote nothing
    assert fake_api.get(f"/ai/proposals/{m['id']}").json()["status"] == "ready"
    assert fake_api.post(f"/ai/proposals/{m['id']}/discard", {"actor": "Asha"}).status_code == 200


def test_a_stale_base_is_also_detected_for_node_assist_proposals(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    use(fake_api, "node_write_expression")
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai",
                      {"task": "write_expression", "actor": "Asha", "field": "fields[0].value", "instruction": "one percent"})
    assert r.status_code == 201, r.text
    p = r.json()
    d = definition(fake_api, wid)
    next(n for n in d["nodes"] if n["name"] == "Reply")["parameters"]["statusCode"] = 201
    assert put_def(fake_api, wid, d).status_code == 200
    err(fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"}), 409, "draft_moved")


def test_keep_refuses_a_second_writer_race_with_one_workflow(fake_api, pid):
    b = ready(fake_api, pid)
    with ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), range(4)))
    assert all(r.status_code == 200 for r in rs), [r.text[:200] for r in rs]
    assert len({r.json()["workflow_id"] for r in rs}) == 1 and len(workflows_of(fake_api, pid)) == 1
    assert [e["event"] for e in events(fake_api, b["id"])].count("kept") == 1


# ── 5. validation (422) and unknown ids (404) ────────────────────────────────────────────────────────


def test_validation_errors_are_422_with_field_reasons(fake_api, pid):
    use(fake_api, "first_try")
    for body, field in (({"project_id": pid, "request": "", "actor": "A"}, "request"), ({"project_id": pid, "actor": "A"}, "request"),
                        ({"request": "x", "actor": "A"}, "project_id"), ({"project_id": "", "request": "x", "actor": "A"}, "project_id")):
        r = fake_api.post("/ai/builder/spec", body)
        assert r.status_code == 422, (body, r.text)
        assert any(field in ".".join(str(x) for x in e["loc"]) for e in r.json()["detail"]), r.json()
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x" * 200_000, "actor": "A"}), 422, "request_invalid")
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "   ", "actor": "A"}), 422, "request_invalid")
    p = start(fake_api, pid)
    assert fake_api.put(f"/ai/proposals/{p['id']}/spec", {"actor": "A"}).status_code == 422            # no spec
    assert fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": "nope", "actor": "A"}).status_code == 422
    assert fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "A"}).status_code == 422          # no instruction
    assert fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "A", "instruction": ""}).status_code == 422
    assert fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "A", "name": "n" * 300}).status_code == 422
    assert fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "A", "accept_failing": "perhaps"}).status_code == 422
    assert fake_api.get("/ai/proposals").status_code == 422                                               # project_id is required
    assert fake_api.get("/ai/proposals", params={"project_id": pid, "limit": 201}).status_code == 422


def test_spec_errors_carry_a_path_a_code_and_a_message_and_block_approval(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid)
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["steps"].append(dict(spec["steps"][0]))                                      # a duplicate step name: SPEC001
    spec["steps"][2]["node_type"] = "NoSuchNode"                                      # SPEC002
    spec["rule_tests"] = {}                                                           # SPEC004 for every rule
    put = fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"})
    assert put.status_code == 200, put.text
    issues = put.json()["spec"]["issues"]
    assert {"SPEC001", "SPEC002", "SPEC004"} <= {i["code"] for i in issues}
    assert all(i["path"] and i["message"] and i["level"] in ("error", "warning") for i in issues)
    d = err(fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}), 422, "spec_invalid")
    assert {"SPEC001", "SPEC002", "SPEC004"} <= {i["code"] for i in d["issues"]} and all(i["path"] for i in d["issues"])
    assert fake_api.get(f"/ai/proposals/{p['id']}").json()["status"] == "spec_draft"


def test_unknown_ids_are_404_on_every_route(fake_api, pid):
    nope = "p_doesnotexist"
    actor = {"actor": "Asha"}
    err(fake_api.get(f"/ai/proposals/{nope}"), 404, "not_found")
    err(fake_api.get(f"/ai/proposals/{nope}/events"), 404, "not_found")
    err(fake_api.put(f"/ai/proposals/{nope}/spec", {"spec": {}, **actor}), 404, "not_found")
    for tail, body in (("spec/approve", actor), ("build", actor), ("revise", {**actor, "instruction": "x"}), ("keep", actor), ("undo", actor), ("discard", actor)):
        err(fake_api.post(f"/ai/proposals/{nope}/{tail}", body), 404, "not_found")
    err(fake_api.get("/ai/builder/api-refs", params={"project_id": "proj_nope"}), 404, "not_found")
    err(fake_api.get("/ai/proposals", params={"project_id": "proj_nope"}), 404, "not_found")
    err(fake_api.post("/ai/builder/spec", {"project_id": "proj_nope", "request": "x", "actor": "A"}), 404, "not_found")
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x", "actor": "A", "workflow_id": "wf_nope"}), 404, "not_found")
    err(fake_api.post("/workflows/wf_nope/nodes/Anything/ai", {"task": "explain", **actor}), 404, "not_found")


def test_a_proposal_of_another_project_is_a_404_on_every_route(fake_api, pid):
    b = ready(fake_api, pid)
    other = fake_api.post("/projects", {"name": uniq("abother")}).json()["id"]
    q = {"project_id": other}
    actor = {"actor": "Asha"}
    err(fake_api.get(f"/ai/proposals/{b['id']}", params=q), 404, "not_found")
    err(fake_api.get(f"/ai/proposals/{b['id']}/events", params=q), 404, "not_found")
    for tail, body in (("build", actor), ("revise", {**actor, "instruction": "x"}), ("keep", actor), ("undo", actor), ("discard", actor)):
        err(fake_api.post(f"/ai/proposals/{b['id']}/{tail}", body, params=q), 404, "not_found")
    assert fake_api.get(f"/ai/proposals/{b['id']}", params={"project_id": pid}).json()["status"] == "ready"
    assert fake_api.get("/ai/proposals", params={"project_id": other}).json()["items"] == []
    assert workflows_of(fake_api, pid) == []


def test_a_workflow_of_another_project_is_not_shown_to_the_model_or_modified(fake_api, pid):
    other = fake_api.post("/projects", {"name": uniq("abother2")}).json()["id"]
    wid = fee_workflow(fake_api, other)
    use(fake_api, "first_try")
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x", "actor": "A", "workflow_id": wid}), 404, "not_found")
    assert calls(fake_api) == []
    p = start(fake_api, pid)
    assert wid not in json.dumps(user_payload(calls(fake_api)[0])["existing_workflows"])


# ── 6. keep with failing tests ───────────────────────────────────────────────────────────────────────


def test_keep_with_failing_tests_needs_an_explicit_audited_confirmation(fake_api, pid):
    b = ready(fake_api, pid, "unrepairable")
    assert b["status"] == "ready" and b["report"]["passed"] is False
    for body in ({"actor": "Asha"}, {"actor": "Asha", "accept_failing": False}):
        d = err(fake_api.post(f"/ai/proposals/{b['id']}/keep", body), 409, "tests_failing")
        assert d["message"]
    assert workflows_of(fake_api, pid) == [] and fake_api.get(f"/ai/proposals/{b['id']}").json()["status"] == "ready"
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha", "accept_failing": True})
    assert k.status_code == 200, k.text
    assert k.json()["proposal"]["accepted_failing"] is True and k.json()["proposal"]["kept_by"] == "Asha"
    assert [e["payload"]["accepted_failing"] for e in events(fake_api, b["id"]) if e["event"] == "kept"] == [True]
    row = next(i for i in fake_api.get("/ai/proposals", params={"project_id": pid}).json()["items"] if i["id"] == b["id"])
    assert row["passed"] is False and row["status"] == "kept"


# ── 7. api-refs ──────────────────────────────────────────────────────────────────────────────────────

SPEC_A = """openapi: 3.0.3
info: {title: A, version: "1.0"}
servers: [{url: "http://a.bank.test"}]
paths:
  /zeta:
    get: {operationId: zeta, summary: Z, responses: {"200": {description: ok}}}
  /alpha:
    post: {operationId: alpha, summary: A, responses: {"200": {description: ok}}}
"""


def test_api_refs_lists_every_operation_of_the_project_and_nothing_of_another(fake_api, pid):
    assert fake_api.get("/ai/builder/api-refs", params={"project_id": pid}).json()["api_refs"] == []
    assert fake_api.post("/api-specs", {"project_id": pid, "name": "ledger", "spec_text": SPEC_A}).status_code == 200
    got = fake_api.get("/ai/builder/api-refs", params={"project_id": pid}).json()
    assert sorted(got["api_refs"]) == ["ledger.alpha", "ledger.zeta"]
    assert {(i["method"], i["path"]) for i in got["items"]} == {("GET", "/zeta"), ("POST", "/alpha")}
    other = fake_api.post("/projects", {"name": uniq("abrefs")}).json()["id"]
    assert fake_api.get("/ai/builder/api-refs", params={"project_id": other}).json()["api_refs"] == []
    assert fake_api.get("/ai/builder/api-refs").status_code == 422
    # a spec step may use an api_ref from this list; one outside it is SPEC003 and blocks approval
    use(fake_api, "first_try")
    p = start(fake_api, pid)
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["steps"][2] = {**spec["steps"][2], "node_type": "HttpRequest", "api_ref": "ledger.missing"}
    issues = fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "A"}).json()["spec"]["issues"]
    assert "SPEC003" in {i["code"] for i in issues}


# ── 8. node assist ───────────────────────────────────────────────────────────────────────────────────


def test_node_assist_write_code_and_fix_error_stay_in_scope_and_save_nothing_before_keep(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    before = fake_api.get(f"/workflows/{wid}").json()
    use(fake_api, "node_fix_error")
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "fix_error", "actor": "Asha", "instruction": "the fee is always empty"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["kind"] == "node_assist" and p["workflow_id"] == wid and shape(p["base_definition"]) == shape(before["definition"])
    assert fake_api.get(f"/workflows/{wid}").json() == before
    assert p["diff"]["counts"]["changed"] == 1 and p["diff"]["nodes"]["changed"][0]["name"] == "Compute fee"
    assert fake_api.post(f"/ai/proposals/{p['id']}/discard", {"actor": "Asha"}).status_code == 200
    assert fake_api.get(f"/workflows/{wid}").json() == before

    use(fake_api, "node_write_expression_bad")
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "write_expression", "actor": "Asha", "field": "fields[0].value", "instruction": "x"})
    assert r.status_code == 201 and r.json()["report"]["passed"] is False
    err(fake_api.post(f"/ai/proposals/{r.json()['id']}/keep", {"actor": "Asha"}), 409, "tests_failing")


def test_node_assist_write_code_needs_a_code_node_and_explain_makes_an_answered_audit_row(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    use(fake_api, "node_write_code")
    err(fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "write_code", "actor": "Asha", "instruction": "x"}), 422, "request_invalid")
    use(fake_api, "node_explain")
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "explain", "actor": "Asha"})
    assert r.status_code == 200 and r.json()["answer"]
    row = fake_api.get(f"/ai/proposals/{r.json()['proposal_id']}").json()
    assert row["status"] == "answered" and row["workflow_id"] == wid and row["actor"] == "Asha"
    err(fake_api.post(f"/ai/proposals/{row['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    err(fake_api.post(f"/ai/proposals/{row['id']}/build", {"actor": "Asha"}), 409, "bad_state")


# ── 9. chat over SSE ─────────────────────────────────────────────────────────────────────────────────


def route(kind: str, answer: str | None = None) -> dict:
    reply = {"route": kind, "node": None, "reason": "t", **({"answer": answer} if answer else {})}
    return {"id": "route", "when": {"system_matches": "^WF_AI:CHAT_ROUTE\\n"}, "respond": {"text": json.dumps(reply)}}


def only(rule_list: list[dict], rid: str) -> dict:
    return next(r for r in rule_list if r["id"] == rid)


def chat(api, body: dict, headers: dict | None = None) -> tuple[list[dict], list[str]]:
    out, names = [], []
    with api.http.stream("POST", "/api/ai/chat", content=json.dumps(body), headers={"Content-Type": "application/json", **(headers or {})}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("event:"):
                names.append(line[6:].strip())
            if line.startswith("data:") and line[5:].strip():
                out.append(json.loads(line[5:].strip()))
    return out, names


def kinds(ev: list[dict]) -> list[str]:
    return [e["type"] for e in ev]


def test_chat_explain_modify_and_build_routes_and_the_sse_event_names(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    use(fake_api, [route("explain", "It sends amounts over 500 to review.")])
    ev, names = chat(fake_api, {"message": "what does this do?", "workflow_id": wid, "mode_hint": "auto"})
    assert kinds(ev) == ["phase", "route", "text", "done"] and names == kinds(ev) and ev[1]["route"] == "explain"

    use(fake_api, [route("modify"), only(rules_of("modify_flag_high_value"), "build")])
    ev, names = chat(fake_api, {"message": FLAG, "workflow_id": wid, "mode_hint": "auto"}, headers={"X-Actor-Name": "Asha"})
    assert kinds(ev) == ["phase", "route", "phase", "proposal", "done"] and names == kinds(ev) and ev[1]["route"] == "modify"
    pr = ev[3]
    assert pr["status"] == "ready" and pr["workflow_id"] == wid and pr["report"] == {"passed": True} and pr["diff"] == {"counts": pr["diff"]["counts"]}
    assert pr["summary"].startswith("Proposed:") and "Tests pass" in pr["summary"]
    assert "Flag high value" not in [n["name"] for n in definition(fake_api, wid)["nodes"]]            # chat never saves

    use(fake_api, [route("build"), only(rules_of("first_try"), "spec")])
    ev, names = chat(fake_api, {"message": f"{REQUEST} ({uniq('c')})", "project_id": pid, "mode_hint": "auto", "actor": "Asha"})
    assert kinds(ev) == ["phase", "route", "phase", "spec", "done"] and ev[3]["status"] == "spec_draft" and ev[3]["spec"]["rules"]
    assert not {"operations", "proposal_id_missing"} & set(names)

    # a hint skips the router call
    use(fake_api, [only(rules_of("modify_flag_high_value"), "build")])
    ev, _ = chat(fake_api, {"message": FLAG, "workflow_id": wid, "mode_hint": "fix", "actor": "Asha"})
    assert kinds(ev) == ["route", "phase", "proposal", "done"] and ev[0]["route"] == "fix"
    assert "WF_AI:CHAT_ROUTE" not in lines(fake_api)


def test_chat_without_a_workflow_turns_a_change_into_a_spec_and_without_a_project_it_cannot_build(fake_api, pid):
    use(fake_api, [route("modify"), only(rules_of("first_try"), "spec")])
    ev, _ = chat(fake_api, {"message": f"{REQUEST} ({uniq('c')})", "project_id": pid, "mode_hint": "auto", "actor": "Asha"})
    assert ev[1]["route"] == "build" and kinds(ev)[-2:] == ["spec", "done"]            # nothing to modify, so it is a new workflow
    use(fake_api, [route("build")])
    ev, _ = chat(fake_api, {"message": "make me a workflow", "mode_hint": "auto", "actor": "Asha"})
    assert kinds(ev)[-2:] == ["error", "done"] and ev[-2]["code"] == "request_invalid"


def test_chat_errors_are_coded_error_events_then_done(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    use(fake_api, [route("modify"), only(rules_of("modify_flag_high_value"), "build")])
    ev, names = chat(fake_api, {"message": FLAG, "workflow_id": wid, "mode_hint": "modify"})
    assert ev[-2]["type"] == "error" and ev[-2]["code"] == "actor_required" and ev[-1]["type"] == "done" and names[-2:] == ["error", "done"]
    assert "WF_AI:BUILD" not in lines(fake_api)
    ev, _ = chat(fake_api, {"message": "   ", "mode_hint": "auto"})
    assert ev[-2]["code"] == "request_invalid" and ev[-1]["type"] == "done"
    ev, _ = chat(fake_api, {"message": "hi", "workflow_id": "wf_nope", "mode_hint": "auto"})
    assert ev[-2]["code"] == "not_found" and ev[-1]["type"] == "done"
    other = fake_api.post("/projects", {"name": uniq("abchat")}).json()["id"]
    ev, _ = chat(fake_api, {"message": FLAG, "workflow_id": wid, "project_id": other, "mode_hint": "modify", "actor": "Asha"})
    assert ev[-2]["code"] == "not_found"                                                 # another project's workflow is hidden
    use(fake_api, [{"id": "route", "when": {"system_matches": "^WF_AI:CHAT_ROUTE\\n"}, "respond": {"text": "not json at all"}}])
    ev, _ = chat(fake_api, {"message": "what is this?", "workflow_id": wid, "mode_hint": "auto"})
    assert ev[-2]["type"] == "error" and ev[-2]["code"] == "ai_failed" and ev[-1]["type"] == "done"
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": True})
    ev, _ = chat(fake_api, {"message": "what is this?", "workflow_id": wid, "mode_hint": "auto"})
    assert ev[-2]["type"] == "error" and ev[-2]["code"] == "ai_not_configured" and ev[-1]["type"] == "done"


def test_a_chat_modify_can_be_kept_and_undone_like_any_proposal(fake_api, pid):
    wid, _ = kept(fake_api, pid)
    before = definition(fake_api, wid)
    use(fake_api, [route("modify"), only(rules_of("modify_flag_high_value"), "build")])
    ev, _ = chat(fake_api, {"message": FLAG, "workflow_id": wid, "mode_hint": "auto", "actor": "Asha"})
    prop = ev[3]["proposal_id"]
    view = fake_api.get(f"/ai/proposals/{prop}").json()
    assert view["kind"] == "modify" and view["spec_approved_by"] == "Asha" and view["spec"]["tests"] and view["parent_id"] is None
    assert fake_api.post(f"/ai/proposals/{prop}/keep", {"actor": "Asha"}).status_code == 200
    assert definition(fake_api, wid) != before
    assert fake_api.post(f"/ai/proposals/{prop}/undo", {"actor": "Asha"}).status_code == 200
    assert definition(fake_api, wid) == before


# ── 10. the policy / PII scrub ───────────────────────────────────────────────────────────────────────

CARD, CARD2, EMAIL, SSN, IBAN, PHONE = "4111 1111 1111 1111", "4111111111111111", "jo.kim@bank-example.com", "078-05-1120", "GB82WEST12345698765432", "+1 415 555 0132"


def leaked_in(api: StudioClient, secret: str) -> list[str]:
    return [t for t in (secret, secret.replace(" ", ""), secret.replace("-", "")) if t in json.dumps(calls(api))]


def test_card_email_and_secret_never_reach_the_captured_prompt_on_any_ai_path(fake_api, pid):
    secret = "CANARY-9a8b7c6d-1111-4222-8333-0123456789ab"
    r = fake_api.post(f"/projects/{pid}/secrets", {"key": "ABBANK_TOKEN", "value": secret})
    if r.status_code not in (200, 201):
        r = fake_api.post("/variables", {"key": "ABBANK_TOKEN", "value": secret, "type": "secret", "project_id": pid})
    assert r.status_code in (200, 201), r.text
    dirty = f"card {CARD}, also {CARD2}, mail {EMAIL}, ssn {SSN}, iban {IBAN}, phone {PHONE}, token {secret}"
    use(fake_api, "secret_scrub")
    # 1. spec request
    p = start(fake_api, pid, request=f"{REQUEST} ({uniq('r')}) {dirty}")
    for s in (CARD, CARD2, EMAIL, SSN, IBAN, secret):
        assert not leaked_in(fake_api, s), ("spec", s)
    first = user_payload(calls(fake_api)[0])
    assert "500" in first["untrusted_request"]
    approve(fake_api, p)
    build(fake_api, p)
    for s in (CARD, CARD2, EMAIL, SSN, IBAN, secret):
        assert not leaked_in(fake_api, s), ("build", s)
    # 3. a revision instruction
    rv = fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "Asha", "instruction": f"Reply 202; customer {EMAIL} card {CARD}; token {secret}"})
    assert rv.status_code in (201, 502), rv.text
    for s in (CARD, CARD2, EMAIL, secret):
        assert not leaked_in(fake_api, s), ("revise", s)
    # 4. node assist instruction and chat history
    wid = fee_workflow(fake_api, pid)
    use(fake_api, "node_explain")
    fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "explain", "actor": "Asha", "instruction": f"why {CARD} and {EMAIL} and {secret}?"})
    use(fake_api, [route("explain", "ok")])
    chat(fake_api, {"message": f"why was {CARD} declined for {EMAIL}?", "project_id": pid, "mode_hint": "auto",
                    "conversation_history": [{"role": "user", "content": f"token {secret} card {CARD2}"}]})
    for s in (CARD, CARD2, EMAIL, secret):
        assert not leaked_in(fake_api, s), ("node assist / chat", s)
    # 5. nor the stored rows
    stored = json.dumps([fake_api.get(f"/ai/proposals/{i['id']}").json() for i in fake_api.get("/ai/proposals", params={"project_id": pid}).json()["items"]])
    assert secret not in stored and CARD not in stored and EMAIL not in stored


def test_scrubbing_is_audited_by_counts_only_and_shown_as_a_warning(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid, request=f"{REQUEST} ({uniq('r')}) card {CARD} and {CARD2}, mail {EMAIL}")
    assert any("card" in w for w in p["spec"]["warnings"])
    sc = [e["payload"] for e in events(fake_api, p["id"]) if e["event"] == "scrubbed"]
    assert len(sc) == 1 and sc[0]["pii"]["card"] == 2 and not any(CARD in json.dumps(x) or EMAIL in json.dumps(x) for x in sc)


def _edited_spec_with_pii(api, pid: str) -> dict:
    use(api, "first_try")
    p = start(api, pid)
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["rules"][0]["text"] = f"Amounts over 500 are reviewed (customer {EMAIL}, card {CARD}, phone {PHONE})."
    put = api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"})
    assert put.status_code == 200, put.text
    return p


def test_pii_typed_into_a_spec_edit_never_reaches_the_build_prompt(fake_api, pid):
    p = _edited_spec_with_pii(fake_api, pid)
    approve(fake_api, p)
    build(fake_api, p)
    sent = json.dumps(calls(fake_api))
    for s in (CARD, EMAIL):
        assert s not in sent, s


def test_pii_typed_into_a_spec_edit_is_not_stored_or_shown(fake_api, pid):
    p = _edited_spec_with_pii(fake_api, pid)
    shown = json.dumps(fake_api.get(f"/ai/proposals/{p['id']}").json())
    for s in (CARD, EMAIL):
        assert s not in shown, s


# ── 11. budgets and deadline (their own engines: the knobs are read from the environment) ─────────────


@pytest.fixture(scope="module")
def engines(fake_stack, upstream, tmp_path_factory):
    _, db = fake_stack
    started: list[EngineServer] = []

    def spawn(label: str, **env) -> StudioClient:
        srv = EngineServer(db, extra_env=fake_env(db, E2E_UPSTREAM_URL=upstream, **env), cwd=str(tmp_path_factory.mktemp(f"cwd-{label}")), label=f"-{label}")
        srv.start()
        started.append(srv)
        return StudioClient(srv.base_url)

    yield spawn
    for s in started:
        s.stop()


@pytest.fixture(scope="module")
def tight(engines):
    return engines("budget", WF_AI_MAX_CALLS="2")


@pytest.fixture(scope="module")
def hurried(engines):
    return engines("deadline", WF_AI_BUILD_DEADLINE_S="0.001")


def test_the_call_budget_stops_the_loop_keeps_the_best_round_and_keep_needs_a_confirm(tight):
    pid = tight.post("/projects", {"name": uniq("abbud")}).json()["id"]
    assert tight.get("/ai/builder/config").json()["budgets"]["max_calls"] == 2
    use(tight, "call_budget")
    p = start(tight, pid)
    approve(tight, p)
    b = build(tight, p)
    assert b["status"] == "ready" and b["report"]["stopped_because"] == "call_budget" and b["report"]["passed"] is False
    assert b["usage"]["llm_calls"] == 2 and len(b["report"]["rounds"]) == 1
    assert lines(tight) == ["WF_AI:SPEC", "WF_AI:BUILD"]                               # the budget is per proposal and was not exceeded
    err(tight.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"}), 409, "tests_failing")
    assert [e["event"] for e in events(tight, p["id"])][-1] == "build_finished"


def test_a_build_past_its_deadline_is_a_502_failed_proposal_and_the_actor_is_not_left_busy(hurried):
    pid = hurried.post("/projects", {"name": uniq("abdead")}).json()["id"]
    use(hurried, "first_try")
    for _ in range(2):                                                                  # the second try must not hit builder_busy
        p = start(hurried, pid)
        approve(hurried, p)
        d = err(hurried.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}), 502, "ai_failed")
        assert d["ai_code"] == "deadline" and d["proposal_id"] == p["id"]
        v = hurried.get(f"/ai/proposals/{p['id']}").json()
        assert v["status"] == "failed" and v["error_code"] == "deadline" and v["proposed_definition"] is None
        assert hurried.post(f"/ai/proposals/{p['id']}/discard", {"actor": "Asha"}).json()["status"] == "discarded"
        err(hurried.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    assert workflows_of(hurried, pid) == []


# ── 12. the stale reaper ─────────────────────────────────────────────────────────────────────────────


def test_the_stale_reaper_fails_an_abandoned_building_proposal_and_leaves_a_live_one(fake_api, fake_stack, engines, pid):
    _, db = fake_stack
    use(fake_api, "first_try")
    old, live = start(fake_api, pid, actor="Dead"), start(fake_api, pid, actor="Live")
    approve(fake_api, old, "Dead")
    approve(fake_api, live, "Live")
    db.query("UPDATE ai_proposals SET status = 'building', updated_at = now() - interval '10 minutes' WHERE id = %s", (old["id"],))
    db.query("UPDATE ai_proposals SET status = 'building', updated_at = now() WHERE id = %s", (live["id"],))
    # while it is 'building' nothing can be done to it: that is the bug the reaper exists for
    for tail in ("build", "keep", "undo", "spec/approve"):
        err(fake_api.post(f"/ai/proposals/{old['id']}/{tail}", {"actor": "Dead"}), 409, "bad_state")
    err(fake_api.post(f"/ai/proposals/{old['id']}/discard", {"actor": "Dead"}), 409, "bad_state")
    engines("reaper")                                           # a fresh engine's reaper ticks once at start-up
    deadline = time.time() + 30
    while time.time() < deadline and fake_api.get(f"/ai/proposals/{old['id']}").json()["status"] == "building":
        time.sleep(0.5)
    v = fake_api.get(f"/ai/proposals/{old['id']}").json()
    assert v["status"] == "failed" and v["error_code"] == "deadline" and v["error_message"]
    fin = [e["payload"] for e in events(fake_api, old["id"]) if e["event"] == "build_finished"]
    assert fin and fin[-1]["status"] == "failed" and fin[-1]["error_code"] == "deadline"
    assert fake_api.get(f"/ai/proposals/{live['id']}").json()["status"] == "building"          # inside its deadline: left alone
    err(fake_api.post(f"/ai/proposals/{old['id']}/keep", {"actor": "Dead"}), 409, "bad_state")
    assert fake_api.post(f"/ai/proposals/{old['id']}/discard", {"actor": "Dead"}).json()["status"] == "discarded"      # now it can be cleared away
    assert workflows_of(fake_api, pid) == []


# ── 13. revise ───────────────────────────────────────────────────────────────────────────────────────


def test_revise_keeps_the_approved_tests_as_the_gate_and_undo_removes_the_new_workflow(fake_api, pid):
    use(fake_api, "revise")
    p = start(fake_api, pid)
    approve(fake_api, p)
    parent = build(fake_api, p)
    r = fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "Asha", "instruction": "Reply with 202 instead of 200"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["parent_id"] == p["id"] and c["kind"] == "modify" and c["status"] == "ready"
    assert [t["id"] for t in c["spec"]["tests"]] == [t["id"] for t in parent["spec"]["tests"]] and all(t["locked"] for t in c["spec"]["tests"])
    assert c["base_definition"] is None and [n[0] for n in shape(c["proposed_definition"])] == [n[0] for n in shape(parent["proposed_definition"])]      # ambiguity A2
    assert fake_api.get(f"/ai/proposals/{p['id']}").json()["status"] == "discarded"
    err(fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    k = fake_api.post(f"/ai/proposals/{c['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    wid = k.json()["workflow_id"]
    assert next(n for n in definition(fake_api, wid)["nodes"] if n["name"] == "Reply")["parameters"]["statusCode"] == 202
    assert fake_api.post(f"/ai/proposals/{c['id']}/undo", {"actor": "Asha"}).status_code == 200
    assert fake_api.get(f"/workflows/{wid}").status_code == 404                       # the revised new workflow is removed like any new one


# ── 14. Keep goes through the same side effects as a normal save ─────────────────────────────────────


def _call_child_rules(child_id: str) -> list[dict]:
    spec = {"title": "Amount triage", "summary": "Every request also calls the fee workflow.", "trigger": {"kind": "webhook", "fields": []},
            "rules": [{"id": "r1", "text": "Every request also calls the fee workflow."}],
            "steps": [{"name": "Call fee", "node_type": "ExecuteWorkflow", "purpose": "Compute the fee", "rules": ["r1"]}],
            "tests": [{"ref": "T1", "rules": ["r1"], "test_json": json.dumps({
                "name": "The fee workflow is called", "target": {"kind": "workflow"}, "category": "normal",
                "input": {"items": [{"body": {"amount": 900}, "headers": {}, "query": {}, "method": "POST"}]},
                "mocks": {"Call fee": {"items": [{"fee": 9}]}}, "expect": [{"node": "Call fee", "op": "status", "value": "success"}]})}],
            "assumptions": [], "questions": []}
    ops = [{"op": "add_node", "args_json": json.dumps({"name": "Call fee", "type": "ExecuteWorkflow", "parameters": {"workflowId": child_id, "version": "draft"}}), "why": ""},
           {"op": "connect", "args_json": json.dumps({"source": "Receive amount", "target": "Call fee"}), "why": ""}]
    return [{"id": "spec", "when": {"system_matches": "^WF_AI:SPEC\\n"}, "respond": {"text": json.dumps(spec)}},
            {"id": "build", "when": {"system_matches": "^WF_AI:BUILD\\n"}, "respond": {"text": json.dumps({"plan": [], "ops": ops, "notes": ""})}}]


def _refs(db, parent: str) -> list[str]:
    return [r[0] for r in db.query("SELECT child_workflow_id FROM workflow_refs WHERE parent_workflow_id = %s AND parent_version_id IS NULL", (parent,))]


def test_premise_a_normal_save_indexes_sub_workflow_calls(fake_api, fake_stack, pid):
    _, db = fake_stack
    wid, _ = kept(fake_api, pid)
    child = fee_workflow(fake_api, pid)
    d = definition(fake_api, wid)
    d["nodes"].append({"name": "Call fee", "type": "ExecuteWorkflow", "parameters": {"workflowId": child, "version": "draft"}, "position": {"x": 0, "y": 200}})
    assert put_def(fake_api, wid, d).status_code == 200
    assert _refs(db, wid) == [child]


def test_keep_indexes_the_sub_workflow_calls_it_adds_like_a_normal_save(fake_api, fake_stack, pid):
    _, db = fake_stack
    wid, _ = kept(fake_api, pid)
    child = fee_workflow(fake_api, pid)
    use(fake_api, _call_child_rules(child))
    m = start(fake_api, pid, request="Also call the fee workflow " + uniq("r"), workflow_id=wid)
    approve(fake_api, m)
    b = build(fake_api, m)
    assert b["status"] == "ready" and "Call fee" in [n["name"] for n in b["proposed_definition"]["nodes"]], b.get("error_message")
    k = fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha", "accept_failing": True})
    assert k.status_code == 200, k.text
    assert "Call fee" in [n["name"] for n in definition(fake_api, wid)["nodes"]]
    assert _refs(db, wid) == [child]


# ── 15. more HTTP edges ──────────────────────────────────────────────────────────────────────────────


def test_keep_and_undo_after_the_workflow_was_deleted_are_409_not_500(fake_api, pid):
    wid, b = kept(fake_api, pid)
    use(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request=FLAG, workflow_id=wid)
    approve(fake_api, m)
    build(fake_api, m)
    assert fake_api.delete(f"/workflows/{wid}").status_code in (200, 204)
    err(fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}), 409, "draft_moved")
    assert fake_api.get(f"/ai/proposals/{m['id']}").json()["status"] == "ready"
    r = fake_api.post(f"/ai/proposals/{b['id']}/undo", {"actor": "Asha"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "draft_moved"
    assert fake_api.get(f"/ai/proposals/{b['id']}").json()["status"] == "kept"


def test_a_person_cannot_smuggle_an_approved_status_through_a_spec_edit(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid)
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["tests"][0].update({"status": "approved", "locked": True, "approved_by": "someone else", "source": "human"})
    out = fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"}).json()
    t0 = out["spec"]["tests"][0]
    assert t0["status"] == "draft" and t0["locked"] is False and t0["source"] == "ai" and not t0.get("approved_by")
    assert out["status"] == "spec_draft" and out["spec_fingerprint"] is None
    a = approve(fake_api, p)
    assert all(t["status"] == "approved" and t["locked"] for t in a["spec"]["tests"]) and a["spec_approved_by"] == "Asha"


def test_a_spec_with_no_trigger_or_no_tests_cannot_be_approved(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid)
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["steps"] = [s for s in spec["steps"] if s["node_type"] != "Webhook"]
    put = fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"}).json()
    assert "SPEC006" in {i["code"] for i in put["spec"]["issues"]}
    err(fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}), 422, "spec_invalid")
    p2 = start(fake_api, pid)
    spec2 = {k: v for k, v in p2["spec"].items() if k not in ("issues", "warnings")}
    spec2["tests"], spec2["rule_tests"] = [], {}
    put2 = fake_api.put(f"/ai/proposals/{p2['id']}/spec", {"spec": spec2, "actor": "Asha"}).json()
    assert "SPEC004" in {i["code"] for i in put2["spec"]["issues"]}
    err(fake_api.post(f"/ai/proposals/{p2['id']}/spec/approve", {"actor": "Asha"}), 422, "spec_invalid")


def test_a_spec_that_fails_validation_gets_one_repair_call(fake_api, pid):
    use(fake_api, "spec_repair")
    p = start(fake_api, pid)
    assert lines(fake_api) == ["WF_AI:SPEC", "WF_AI:SPEC_REPAIR"] and p["usage"]["llm_calls"] == 2
    assert [i for i in p["spec"]["issues"] if i["level"] == "error"] == []
    sent = user_payload(calls(fake_api)[1])
    assert sent["issues"] and all({"path", "code", "message"} <= set(i) for i in sent["issues"])


def test_a_model_that_sends_no_ops_ends_failed_over_http_and_cannot_be_kept(fake_api, pid):
    b = ready(fake_api, pid, "empty_ops")
    assert b["status"] == "failed" and b["error_code"] == "no_workflow"
    err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha", "accept_failing": True}), 409, "bad_state")
    err(fake_api.post(f"/ai/proposals/{b['id']}/revise", {"actor": "Asha", "instruction": "try again"}), 409, "bad_state")
    assert workflows_of(fake_api, pid) == []
    assert fake_api.post(f"/ai/proposals/{b['id']}/discard", {"actor": "Asha"}).json()["status"] == "discarded"


def test_a_model_that_stops_making_progress_stops_early_and_says_so(fake_api, pid):
    b = ready(fake_api, pid, "no_progress")
    assert b["report"]["stopped_because"] == "no_progress" and b["report"]["passed"] is False and len(b["report"]["rounds"]) < 4
    err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), 409, "tests_failing")


@pytest.mark.parametrize("script", ["missing_step", "renamed_nodes"])
def test_spec_coverage_problems_are_reported_not_hidden(fake_api, pid, script):
    b = ready(fake_api, pid, script)
    assert b["status"] in ("ready", "failed")
    if b["status"] == "ready":
        cov = b["report"]["spec_coverage"]
        assert set(cov) >= {"steps_missing", "steps_wrong_type", "rules_untested"}
        if script == "missing_step":
            assert b["report"]["passed"] is False or cov["steps_missing"] == []


def test_kept_workflows_hold_a_checker_clean_definition_and_the_diff_has_review_flags(fake_api, pid):
    b = ready(fake_api, pid)
    assert b["report"]["checker"]["errors"] == 0
    assert set(b["diff"]) >= {"nodes", "connections", "tests", "interface", "new_destinations", "new_code", "counts"}
    assert b["diff"]["counts"]["added"] == len(b["proposed_definition"]["nodes"]) and b["diff"]["nodes"]["removed"] == []


def test_keep_and_discard_racing_leave_one_consistent_answer(fake_api, pid):
    for _ in range(3):
        b = ready(fake_api, pid)
        with ThreadPoolExecutor(2) as ex:
            fk = ex.submit(lambda: fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}))
            fd = ex.submit(lambda: fake_api.post(f"/ai/proposals/{b['id']}/discard", {"actor": "Asha"}))
            rk, rd = fk.result(), fd.result()
        assert rk.status_code in (200, 409) and rd.status_code in (200, 409), (rk.text, rd.text)
        status = fake_api.get(f"/ai/proposals/{b['id']}").json()["status"]
        assert status in ("kept", "discarded")
        names = [w["id"] for w in workflows_of(fake_api, pid)]
        if status == "kept":
            assert rk.status_code == 200 and rd.status_code == 409 and rk.json()["workflow_id"] in names
        else:
            assert rd.status_code == 200 and rk.status_code == 409 and rk.status_code == 409
            assert fake_api.get(f"/workflows/{rk.json().get('workflow_id', 'wf_none')}").status_code == 404
        for w in names:
            fake_api.post(f"/ai/proposals/{b['id']}/undo", {"actor": "Asha"})


def test_a_secret_the_model_puts_in_an_op_is_refused_and_never_saved(fake_api, pid):
    secret = "CANARY-5555aaaa-bbbb-4ccc-8ddd-eeeeffff0000"
    r = fake_api.post(f"/projects/{pid}/secrets", {"key": "ABOUT_TOKEN", "value": secret})
    if r.status_code not in (200, 201):
        r = fake_api.post("/variables", {"key": "ABOUT_TOKEN", "value": secret, "type": "secret", "project_id": pid})
    assert r.status_code in (200, 201), r.text
    rules = rules_of("first_try")
    build_rule = only(rules, "build")
    reply = json.loads(build_rule["respond"]["text"])
    reply["ops"].append({"op": "set_parameter", "args_json": json.dumps({"node": "Reply", "path": "body.leak", "value": secret}), "why": ""})
    build_rule["respond"]["text"] = json.dumps(reply)
    use(fake_api, rules)
    p = start(fake_api, pid)
    approve(fake_api, p)
    b = build(fake_api, p)
    assert secret not in json.dumps(b) and secret not in json.dumps(fake_api.get(f"/ai/proposals/{p['id']}").json())
    assert any(o["result"] == "error" and "secret" in o["message"].lower() for o in b["ops_log"]), b["ops_log"][-2:]
    assert secret not in json.dumps(events(fake_api, p["id"]))


def test_the_actor_is_trimmed_and_recorded_on_every_event(fake_api, pid):
    use(fake_api, "first_try")
    p = start(fake_api, pid, actor="  Asha  ")
    assert p["actor"] == "Asha"
    approve(fake_api, p, "  Asha ")
    build(fake_api, p, " Asha")
    assert {e["actor"] for e in events(fake_api, p["id"]) if e["event"] in ("spec_requested", "spec_approved")} == {"Asha"}


def test_revise_of_a_node_assist_proposal_is_never_a_server_error(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    use(fake_api, "node_write_expression")
    p = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai",
                      {"task": "write_expression", "actor": "Asha", "field": "fields[0].value", "instruction": "one percent"}).json()
    r = fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "Asha", "instruction": "use two percent"})
    assert r.status_code < 500, r.text
    if r.status_code >= 400:                                                # a refusal must leave the ready proposal as it was
        assert fake_api.get(f"/ai/proposals/{p['id']}").json()["status"] == "ready"


def test_an_explain_is_cached_and_not_billed_twice_but_still_audited(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    use(fake_api, "node_explain")
    why = uniq("why")                                                           # the cache is per process: make this prompt new
    a = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "explain", "actor": "Asha", "instruction": why}).json()
    b = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "explain", "actor": "Ben", "instruction": why}).json()
    assert a["answer"] == b["answer"] and a["proposal_id"] != b["proposal_id"]
    assert len(calls(fake_api)) == 1
    second = [e["payload"] for e in events(fake_api, b["proposal_id"]) if e["event"] == "llm_call"]
    assert second and second[0]["cached"] is True
    assert fake_api.get(f"/ai/proposals/{b['proposal_id']}").json()["usage"]["llm_calls"] == 0
