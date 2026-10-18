"""Stage 6 B4 over HTTP on the fake-LLM engine with real Postgres (DESIGN-STAGE6-AI-BUILDER section 4): the builder routes, their status
codes and error envelopes, Keep / Undo / Discard, modify proposals, node assist, progress events, and what the model was sent.

The model is the hand-written fake (tests/fixtures/fake_llm/ai_builder/*.json); model quality is the lead's live eval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
SCRIPTS = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder"
REQUEST = "Amounts over 500 are marked for review, the rest are approved automatically, and the caller always gets the status back."


def use_script(fake_api, name: str) -> None:
    raw = json.loads((SCRIPTS / f"{name}.json").read_text())
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=raw)
    assert r.status_code == 200, r.text
    fake_api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def llm_calls(fake_api) -> list[dict]:
    return fake_api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})


@pytest.fixture
def pid(fake_api, clean) -> str:
    r = fake_api.post("/projects", {"name": uniq("b4")})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def err(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail["code"] == code and detail["message"], detail
    return detail


def start(api, pid: str, actor: str = "Asha", request: str | None = None, **extra) -> dict:
    # a unique request per call: spec answers are cached in the engine process by prompt hash (that is a feature, not what these tests probe)
    request = request or f"{REQUEST} ({uniq('r')})"
    r = api.post("/ai/builder/spec", {"project_id": pid, "request": request, "actor": actor, **extra})
    assert r.status_code == 201, r.text
    return r.json()["proposal"]


def build_ready(api, pid: str, script: str = "first_try") -> dict:
    use_script(api, script)
    p = start(api, pid)
    r = api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"})
    assert r.status_code == 200, r.text
    r = api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"})
    assert r.status_code == 200, r.text
    return r.json()


def db_count(db, table: str) -> int:
    return db.query(f"SELECT count(*) FROM {table}")[0][0]


# ── config ─────────────────────────────────────────────────────────────────────────────────


def test_config_reports_model_policy_and_budgets(fake_api, clean):
    c = fake_api.get("/ai/builder/config").json()
    assert c["model"] == "gemini-3.5-flash" and c["provider"] == "gemini" and c["configured"] is True
    assert "gemini-3.5-flash" in c["allowed_models"] and c["send_run_values"] is False and c["prices_configured"] is False
    assert c["budgets"]["max_repair_rounds"] == 3 and c["budgets"]["max_calls"] == 8 and c["budgets"]["build_deadline_s"] == 150
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": True})
    assert fake_api.get("/ai/builder/config").json()["configured"] is False


# ── the happy path: spec -> approve -> build -> keep -> run -> undo ────────────────────────────────


def test_create_build_keep_run_and_undo(fake_api, fake_stack, pid):
    _, db = fake_stack
    use_script(fake_api, "first_try")
    p = start(fake_api, pid)
    assert p["status"] == "spec_draft" and p["kind"] == "build" and p["actor"] == "Asha" and p["workflow_id"] is None
    assert [s["name"] for s in p["spec"]["steps"]][:2] == ["Receive amount", "Over limit?"] and len(p["spec"]["tests"]) == 3
    assert p["spec"]["issues"] == [] or all(i["level"] != "error" for i in p["spec"]["issues"])
    assert p["policy"]["model"] == "gemini-3.5-flash" and p["usage"]["llm_calls"] == 1

    # the user edits one rule and one test through PUT, then approves
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["rules"][0]["text"] = "Amounts over 500 go to review (edited by a person)."
    spec["tests"][0]["name"] = "A big amount goes to review"
    r = fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"})
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["rules"][0]["text"].endswith("(edited by a person).") and r.json()["spec"]["tests"][0]["status"] == "draft"
    r = fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"})
    assert r.status_code == 200 and r.json()["status"] == "spec_approved" and r.json()["spec_fingerprint"]

    execs_before = db_count(db, "executions")
    r = fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "ready" and b["report"]["passed"] is True and b["report"]["stopped_because"] == "passed"
    assert db_count(db, "executions") == execs_before                          # persist=False: the proposal's test runs wrote no executions
    assert b["diff"]["counts"]["added"] == 5 and b["usage"]["llm_calls"] == 2
    wf_before = fake_api.get(f"/projects/{pid}/workflows").json()
    assert not any(w["name"] == "Amount triage" for w in (wf_before if isinstance(wf_before, list) else wf_before.get("items", [])))

    # progress events: the studio polls these while the build runs
    ev = fake_api.get(f"/ai/proposals/{p['id']}/events").json()
    names = [e["event"] for e in ev["items"]]
    assert names[0] == "spec_requested" and {"spec_drafted", "spec_edited", "spec_approved", "round_started", "ops_applied", "checked", "tested",
                                              "build_finished"} <= set(names)
    assert ev["last_seq"] == ev["items"][-1]["seq"]
    shape = next(e["payload"] for e in ev["items"] if e["event"] == "round_shape")   # the studio canvas plays this live
    assert shape["round"] == 0 and len(shape["nodes"]) == 5 and "parameters" not in json.dumps(shape)
    assert names.index("round_shape") < names.index("ops_applied")
    tail = fake_api.get(f"/ai/proposals/{p['id']}/events", params={"after": ev["items"][2]["seq"]}).json()
    assert [e["seq"] for e in tail["items"]] == [e["seq"] for e in ev["items"][3:]]

    r = fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha", "name": "Amount triage"})
    assert r.status_code == 200, r.text
    k = r.json()
    wid = k["workflow_id"]
    assert wid.startswith("wf_") and k["draft_hash"] and k["proposal"]["status"] == "kept" and k["proposal"]["kept_by"] == "Asha"
    w = fake_api.get(f"/workflows/{wid}").json()
    assert w["name"] == "Amount triage" and len(w["definition"]["nodes"]) == 5
    assert [t["locked"] for t in w["definition"]["tests"]] == [True, True, True] and all(t["status"] == "approved" for t in w["definition"]["tests"])
    run = fake_api.post(f"/workflows/{wid}/tests/run", {})
    assert run.status_code == 200 and run.json()["summary"]["passed"] == 3 and run.json()["summary"]["approved_failed"] == 0
    # Keep twice is the same answer
    again = fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"})
    assert again.status_code == 200 and again.json()["workflow_id"] == wid

    u = fake_api.post(f"/ai/proposals/{p['id']}/undo", {"actor": "Asha"})
    assert u.status_code == 200 and u.json()["status"] == "undone"
    assert fake_api.get(f"/workflows/{wid}").status_code == 404                  # a new workflow is removed again
    audit = fake_api.get("/ai/proposals", params={"project_id": pid}).json()
    assert [i["id"] for i in audit["items"]] == [p["id"]] and audit["items"][0]["status"] == "undone" and audit["items"][0]["passed"] is True
    final_events = [e["event"] for e in fake_api.get(f"/ai/proposals/{p['id']}/events").json()["items"]]
    assert final_events[-2:] == ["kept", "undone"]


def test_the_actor_comes_from_the_body_or_the_header_and_is_required(fake_api, pid):
    use_script(fake_api, "first_try")
    d = err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": REQUEST}), 422, "actor_required")
    assert "actor" in d["message"]
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": REQUEST, "actor": "  "}), 422, "actor_required")
    r = fake_api.post("/ai/builder/spec", {"project_id": pid, "request": f"{REQUEST} ({uniq('r')})"}, headers={"X-Actor-Name": "Header Hana"})
    assert r.status_code == 201 and r.json()["proposal"]["actor"] == "Header Hana"
    p = r.json()["proposal"]
    err(fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {}), 422, "actor_required")
    assert fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {}, headers={"X-Actor-Name": "Header Hana"}).status_code == 200
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": REQUEST, "actor": "x" * 81}), 422, "actor_required")


# ── repair, giving up, test immutability, injection ───────────────────────────────────────────────


def test_a_broken_first_draft_is_repaired_in_round_one(fake_api, pid):
    b = build_ready(fake_api, pid, "repairable")
    rep = b["report"]
    assert b["status"] == "ready" and rep["passed"] is True and len(rep["rounds"]) == 2 and rep["rounds"][0]["rejected"] >= 1
    ev = fake_api.get(f"/ai/proposals/{b['id']}/events").json()["items"]
    tested = [e["payload"] for e in ev if e["event"] == "tested"]
    assert [t["failed"] for t in tested] == [1, 0]
    assert [c["messages"][0]["content"].split("\n")[0] for c in llm_calls(fake_api)] == ["WF_AI:SPEC", "WF_AI:BUILD", "WF_AI:REPAIR"]


def test_showcase_paces_the_fake_model_and_streams_the_shape_of_each_round(fake_api, pid):
    import time

    t0 = time.monotonic()
    b = build_ready(fake_api, pid, "showcase")               # delay_ms: 2.5 s spec + 2.5 s build + 1.8 s repair
    assert time.monotonic() - t0 >= 6.5
    assert b["status"] == "ready" and b["report"]["passed"] is True and len(b["report"]["rounds"]) == 2
    ev = fake_api.get(f"/ai/proposals/{b['id']}/events").json()["items"]
    names = [e["event"] for e in ev]
    assert names.count("round_shape") == 2 and names.index("round_shape") < names.index("ops_applied")
    tested = [e["payload"] for e in ev if e["event"] == "tested"]
    assert [t["failed"] for t in tested] == [1, 0]
    assert all(r["path"] and r["path"][0] == "Receive refund request" for t in tested for r in t["results"])
    assert len(b["proposed_definition"]["nodes"]) == 7


def test_three_failed_repairs_end_ready_but_red_and_keep_needs_a_confirm(fake_api, pid):
    b = build_ready(fake_api, pid, "unrepairable")
    rep = b["report"]
    assert b["status"] == "ready" and rep["passed"] is False and rep["stopped_because"] == "rounds_exhausted" and len(rep["rounds"]) == 4
    assert b["usage"]["llm_calls"] == 5
    d = err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), 409, "tests_failing")
    assert d["message"]
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha", "accept_failing": True})
    assert k.status_code == 200 and k.json()["proposal"]["accepted_failing"] is True
    assert fake_api.get(f"/workflows/{k.json()['workflow_id']}").status_code == 200


def test_the_model_cannot_change_approved_tests_and_its_request_is_surfaced(fake_api, pid):
    b = build_ready(fake_api, pid, "test_change_attempt")
    assert b["status"] == "ready" and b["report"]["passed"] is True
    refused = {o["op"] for o in b["ops_log"] if o["result"] == "error"}
    assert {"update_test", "add_test", "remove_node"} <= refused
    assert [t["name"] for t in b["proposed_definition"]["tests"]] == [t["name"] for t in b["spec"]["tests"]]
    assert len(b["test_change_requests"]) == 1 and b["test_change_requests"][0]["test_id"] == b["spec"]["tests"][1]["id"]
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}).json()
    w = fake_api.get(f"/workflows/{k['workflow_id']}").json()
    assert {t["name"] for t in w["definition"]["tests"]} == {t["name"] for t in b["spec"]["tests"]}


def test_an_injected_exfil_destination_is_refused_when_the_project_has_an_allowlist(fake_api, pid):
    assert fake_api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": ["api.bank.test"]}}).status_code == 200
    b = build_ready(fake_api, pid, "injection")
    assert b["status"] == "ready" and "exfil.example" not in json.dumps(b["proposed_definition"])
    assert b["diff"]["new_destinations"] == [] and any("allowlist" in o["message"] for o in b["ops_log"] if o["result"] == "error")


def test_pii_and_secrets_never_reach_the_model(fake_api, pid):
    secret = "CANARY-5e1c0de0-aaaa-4bbb-8ccc-0123456789ab"
    r = fake_api.post(f"/projects/{pid}/secrets", {"key": "BANK_TOKEN", "value": secret})
    if r.status_code not in (200, 201):
        r = fake_api.post("/variables", {"key": "BANK_TOKEN", "value": secret, "type": "secret", "project_id": pid})
    assert r.status_code in (200, 201), r.text
    use_script(fake_api, "secret_scrub")
    request = f"{REQUEST} ({uniq('r')}) Customer card 4111 1111 1111 1111, email jo.kim@bank-example.com, token {secret}."
    p = start(fake_api, pid, request=request)
    fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"})
    fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"})
    seen = json.dumps(llm_calls(fake_api))
    for leaked in (secret, "4111 1111 1111 1111", "jo.kim@bank-example.com"):
        assert leaked not in seen, leaked
    shown = fake_api.get(f"/ai/proposals/{p['id']}").json()
    assert secret not in json.dumps(shown) and "4111" not in shown["request"]
    assert any("card number" in w for w in p["spec"]["warnings"])
    scrubbed = [e for e in fake_api.get(f"/ai/proposals/{p['id']}/events").json()["items"] if e["event"] == "scrubbed"]
    assert scrubbed and scrubbed[0]["payload"]["pii"]["card"] == 1


# ── error codes ──────────────────────────────────────────────────────────────────────────────────


def test_error_envelopes_and_status_codes(fake_api, fake_stack, pid):
    use_script(fake_api, "first_try")
    p = start(fake_api, pid)
    # not approved yet: build and keep are bad states carrying the status
    d = err(fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}), 409, "bad_state")
    assert d["status"] == "spec_draft"
    err(fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    err(fake_api.post(f"/ai/proposals/{p['id']}/undo", {"actor": "Asha"}), 409, "bad_state")
    err(fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "Asha", "instruction": "x"}), 409, "bad_state")
    # unknown proposal / project, and 404 hides another project's proposal
    err(fake_api.get("/ai/proposals/p_doesnotexist"), 404, "not_found")
    err(fake_api.get(f"/ai/proposals/{p['id']}", params={"project_id": "proj_default"}), 404, "not_found")
    assert fake_api.get(f"/ai/proposals/{p['id']}", params={"project_id": pid}).status_code == 200
    err(fake_api.get("/ai/proposals/p_doesnotexist/events"), 404, "not_found")
    err(fake_api.post("/ai/builder/spec", {"project_id": "proj_nope", "request": "x", "actor": "A"}), 404, "not_found")
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x", "actor": "A", "workflow_id": "wf_nope"}), 404, "not_found")
    err(fake_api.get("/ai/proposals", params={"project_id": "proj_nope"}), 404, "not_found")
    # policy
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x", "actor": "A", "model": "gpt-4o"}), 403, "model_not_allowed")
    # a spec with errors cannot be approved
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings")}
    spec["steps"][1]["node_type"] = "NoSuchNode"
    assert fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "A"}).status_code == 200
    d = err(fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}), 422, "spec_invalid")
    assert any(i["code"] == "SPEC002" for i in d["issues"])
    # empty request is a 422 from validation
    assert fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "", "actor": "A"}).status_code == 422
    # not configured
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": True})
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x", "actor": "A"}), 503, "ai_not_configured")
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})


def test_a_model_that_never_returns_json_is_a_502_and_the_proposal_is_kept_as_failed(fake_api, pid):
    use_script(fake_api, "bad_json_twice")
    r = fake_api.post("/ai/builder/spec", {"project_id": pid, "request": f"{REQUEST} ({uniq('r')})", "actor": "Asha"})
    d = err(r, 502, "ai_failed")
    assert d["ai_code"] == "bad_json" and d["proposal_id"].startswith("p_")
    row = fake_api.get(f"/ai/proposals/{d['proposal_id']}").json()
    assert row["status"] == "failed" and row["error_code"] == "bad_json"
    err(fake_api.post(f"/ai/proposals/{d['proposal_id']}/spec/approve", {"actor": "Asha"}), 409, "bad_state")
    assert fake_api.post(f"/ai/proposals/{d['proposal_id']}/discard", {"actor": "Asha"}).json()["status"] == "discarded"


def test_a_bad_first_reply_is_fixed_by_the_json_fix_call(fake_api, pid):
    use_script(fake_api, "bad_json_then_fix")
    p = start(fake_api, pid)
    assert p["status"] == "spec_draft" and p["usage"]["llm_calls"] == 2
    ev = fake_api.get(f"/ai/proposals/{p['id']}/events").json()["items"]
    assert [e["payload"]["outcome"] for e in ev if e["event"] == "llm_call"] == ["bad_json", "ok"]


# ── discard, keep races ──────────────────────────────────────────────────────────────────────────


def test_discard_leaves_no_workflow_and_blocks_keep(fake_api, pid):
    b = build_ready(fake_api, pid)
    d = fake_api.post(f"/ai/proposals/{b['id']}/discard", {"actor": "Asha"})
    assert d.status_code == 200 and d.json()["status"] == "discarded"
    assert fake_api.post(f"/ai/proposals/{b['id']}/discard", {"actor": "Asha"}).json()["status"] == "discarded"
    err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    assert [i["status"] for i in fake_api.get("/ai/proposals", params={"project_id": pid, "status": "discarded"}).json()["items"]] == ["discarded"]


# ── modify an existing workflow ──────────────────────────────────────────────────────────────────


def test_modify_proposal_over_http_keep_draft_moved_and_undo(fake_api, pid):
    b = build_ready(fake_api, pid)
    wid = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}).json()["workflow_id"]
    original = fake_api.get(f"/workflows/{wid}").json()["definition"]

    use_script(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request="Also flag amounts over 10000 as high value", workflow_id=wid)
    assert m["kind"] == "modify" and m["workflow_id"] == wid
    fake_api.post(f"/ai/proposals/{m['id']}/spec/approve", {"actor": "Asha"})
    mb = fake_api.post(f"/ai/proposals/{m['id']}/build", {"actor": "Asha"}).json()
    assert mb["status"] == "ready" and mb["report"]["passed"] is True and mb["diff"]["counts"]["added"] == 1
    assert mb["report"]["tests"]["summary"]["approved_total"] == 4
    assert fake_api.get(f"/workflows/{wid}").json()["definition"]["nodes"] == original["nodes"]       # unchanged before Keep

    # the studio forgot to flush an edit: Keep says the draft moved
    moved = fake_api.get(f"/workflows/{wid}").json()
    nodes = moved["definition"]["nodes"]
    next(n for n in nodes if n["name"] == "Reply")["parameters"]["statusCode"] = 201
    up = fake_api.put(f"/workflows/{wid}", {"name": moved["name"], "nodes": nodes, "connections": moved["definition"]["connections"],
                                            "settings": moved["definition"].get("settings") or {}, "tests": moved["definition"]["tests"]})
    assert up.status_code == 200, up.text
    err(fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}), 409, "draft_moved")
    assert fake_api.get(f"/ai/proposals/{m['id']}").json()["status"] == "ready"

    # a fresh proposal on the moved draft keeps cleanly; undo then needs the draft to be where Keep left it
    use_script(fake_api, "modify_flag_high_value")
    m2 = start(fake_api, pid, request="Also flag amounts over 10000 as high value", workflow_id=wid)
    fake_api.post(f"/ai/proposals/{m2['id']}/spec/approve", {"actor": "Asha"})
    mb2 = fake_api.post(f"/ai/proposals/{m2['id']}/build", {"actor": "Asha"}).json()
    assert mb2["status"] == "ready"
    k = fake_api.post(f"/ai/proposals/{m2['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    assert k.json()["workflow_id"] == wid
    kept = fake_api.get(f"/workflows/{wid}").json()["definition"]
    assert any(n["name"] == "Flag high value" for n in kept["nodes"]) and len(kept["tests"]) == 4
    u = fake_api.post(f"/ai/proposals/{m2['id']}/undo", {"actor": "Asha"})
    assert u.status_code == 200 and u.json()["status"] == "undone"
    back = fake_api.get(f"/workflows/{wid}").json()["definition"]
    assert not any(n["name"] == "Flag high value" for n in back["nodes"]) and len(back["tests"]) == 3


def test_undo_after_the_draft_moved_is_a_409(fake_api, pid):
    b = build_ready(fake_api, pid)
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}).json()
    wid = k["workflow_id"]
    w = fake_api.get(f"/workflows/{wid}").json()
    up = fake_api.put(f"/workflows/{wid}", {"name": "Renamed by a person", "nodes": w["definition"]["nodes"],
                                            "connections": w["definition"]["connections"], "settings": {}, "tests": w["definition"]["tests"]})
    assert up.status_code == 200, up.text
    nodes = up.json()["definition"]["nodes"]
    next(n for n in nodes if n["name"] == "Reply")["parameters"]["statusCode"] = 203
    assert fake_api.put(f"/workflows/{wid}", {"nodes": nodes, "connections": w["definition"]["connections"], "settings": {},
                                              "tests": w["definition"]["tests"]}).status_code == 200
    err(fake_api.post(f"/ai/proposals/{b['id']}/undo", {"actor": "Asha"}), 409, "draft_moved")
    assert fake_api.get(f"/workflows/{wid}").status_code == 200


def test_revise_makes_a_child_proposal_and_discards_the_parent(fake_api, pid):
    use_script(fake_api, "revise")
    p = start(fake_api, pid)
    fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"})
    fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"})
    r = fake_api.post(f"/ai/proposals/{p['id']}/revise", {"actor": "Asha", "instruction": "Reply with 202 instead of 200"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["kind"] == "modify" and c["parent_id"] == p["id"] and c["status"] == "ready" and c["report"]["passed"] is True
    assert fake_api.get(f"/ai/proposals/{p['id']}").json()["status"] == "discarded"
    assert fake_api.post(f"/ai/proposals/{c['id']}/keep", {"actor": "Asha"}).status_code == 200


# ── node assist ──────────────────────────────────────────────────────────────────────────────────


def fee_workflow(api, pid: str) -> str:
    test = {"id": "t_b4000001", "name": "fee is 1% of the amount", "target": {"kind": "workflow"}, "category": "normal",
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
    assert api.post(f"/workflows/{wid}/tests/t_b4000001/approve", {"approved_by": "sam"}).status_code == 200
    return wid


def test_node_assist_explain_write_expression_and_keep(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    use_script(fake_api, "node_explain")
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "explain", "actor": "Asha", "instruction": "what is this?"})
    assert r.status_code == 200, r.text
    assert "one percent" in r.json()["answer"] and r.json()["proposal_id"].startswith("p_")
    assert fake_api.get(f"/ai/proposals/{r.json()['proposal_id']}").json()["status"] == "answered"

    use_script(fake_api, "node_write_expression")
    before = fake_api.get(f"/workflows/{wid}").json()["definition"]
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai",
                      {"task": "write_expression", "actor": "Asha", "field": "fields[0].value", "instruction": "one percent of the amount"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["kind"] == "node_assist" and p["status"] == "ready" and p["report"]["passed"] is True and p["workflow_id"] == wid
    assert fake_api.get(f"/workflows/{wid}").json()["definition"] == before                  # nothing saved before Keep
    k = fake_api.post(f"/ai/proposals/{p['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    after = fake_api.get(f"/workflows/{wid}").json()["definition"]
    assert next(n for n in after["nodes"] if n["name"] == "Compute fee")["parameters"]["fields"][0]["value"] == "{{ $json.body.amount * 0.01 }}"
    assert fake_api.post(f"/workflows/{wid}/tests/run", {}).json()["summary"]["passed"] == 1
    assert fake_api.post(f"/ai/proposals/{p['id']}/undo", {"actor": "Asha"}).status_code == 200
    restored = next(n for n in fake_api.get(f"/workflows/{wid}").json()["definition"]["nodes"] if n["name"] == "Compute fee")
    assert "amnt" in restored["parameters"]["fields"][0]["value"]


def test_node_assist_works_for_node_names_with_slashes_and_spaces(fake_api, pid):
    odd = "Fee / cap?"
    body = {"name": uniq("slash"), "settings": {}, "nodes": [
        {"name": "Receive amount", "type": "Webhook", "parameters": {"method": "POST", "path": uniq("odd"), "responseMode": "lastNode"}, "position": {"x": 0, "y": 0}},
        {"name": odd, "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "fee", "value": "1"}]}, "position": {"x": 280, "y": 0}}],
        "connections": [{"source_node": "Receive amount", "target_node": odd, "source_output": "main", "target_input": "main"}]}
    r = fake_api.post(f"/projects/{pid}/workflows", body)
    assert r.status_code == 201, r.text
    use_script(fake_api, "node_explain")
    out = fake_api.post(f"/workflows/{r.json()['id']}/nodes/Fee %2F cap%3F/ai", {"task": "explain", "actor": "Asha"})
    assert out.status_code == 200, out.text


def test_a_workflow_of_another_project_cannot_be_modified_through_this_one(fake_api, pid):
    other = fake_api.post("/projects", {"name": uniq("b4other")}).json()["id"]
    wid = fee_workflow(fake_api, other)
    use_script(fake_api, "first_try")
    err(fake_api.post("/ai/builder/spec", {"project_id": pid, "request": "x" + uniq("r"), "actor": "A", "workflow_id": wid}), 404, "not_found")


def test_the_audit_list_pages_with_a_cursor_newest_first(fake_api, pid):
    use_script(fake_api, "first_try")
    ids = [start(fake_api, pid)["id"] for _ in range(3)]
    page1 = fake_api.get("/ai/proposals", params={"project_id": pid, "limit": 2}).json()
    assert [i["id"] for i in page1["items"]] == ids[::-1][:2] and page1["next_cursor"]
    page2 = fake_api.get("/ai/proposals", params={"project_id": pid, "limit": 2, "cursor": page1["next_cursor"]}).json()
    assert [i["id"] for i in page2["items"]] == ids[::-1][2:] and page2["next_cursor"] is None
    assert fake_api.get("/ai/proposals", params={"project_id": pid, "limit": 0}).status_code == 422
    assert fake_api.get("/ai/proposals", params={"project_id": pid, "workflow_id": "wf_none"}).json()["items"] == []


def test_node_assist_fix_error_and_its_errors(fake_api, pid):
    wid = fee_workflow(fake_api, pid)
    use_script(fake_api, "node_fix_error")
    err(fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "fix_error", "actor": "Asha"}), 422, "no_failed_run")
    r = fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "fix_error", "actor": "Asha", "instruction": "the fee is always empty"})
    assert r.status_code == 201 and r.json()["report"]["passed"] is True and r.json()["report"]["base"]["approved_failed"] == 1
    err(fake_api.post(f"/workflows/{wid}/nodes/Nope/ai", {"task": "explain", "actor": "Asha"}), 404, "not_found")
    err(fake_api.post("/workflows/wf_nope/nodes/Compute fee/ai", {"task": "explain", "actor": "Asha"}), 404, "not_found")
    err(fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "explain"}), 422, "actor_required")
    err(fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "write_expression", "actor": "Asha"}), 422, "request_invalid")
    assert fake_api.post(f"/workflows/{wid}/nodes/Compute fee/ai", {"task": "dance", "actor": "Asha"}).status_code == 422


# ── a bank-style case: form + threshold + human approval + two outside APIs + a screen ──────────────────

REFUNDS_YAML = """openapi: 3.0.3
info: {title: Refunds, version: "1.0"}
servers: [{url: "http://refunds.bank.test"}]
paths:
  /refunds:
    post:
      operationId: issueRefund
      summary: Issue a refund
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [customer_email, amount]
              properties: {customer_email: {type: string}, amount: {type: number}, reason: {type: string}}
      responses:
        "201":
          description: issued
          content:
            application/json:
              schema: {type: object, required: [refund_id, status], properties: {refund_id: {type: string}, status: {type: string}}}
        "422": {description: refused}
"""
NOTIFY_YAML = """openapi: 3.0.3
info: {title: Notify, version: "1.0"}
servers: [{url: "http://notify.bank.test"}]
paths:
  /messages:
    post:
      operationId: sendMessage
      summary: Send a message to a customer
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [to, text]
              properties: {to: {type: string}, text: {type: string}}
      responses:
        "200":
          description: sent
          content:
            application/json:
              schema: {type: object, required: [delivered], properties: {delivered: {type: boolean}}}
"""


def test_refund_case_with_approval_two_apis_and_a_generated_screen(fake_api, pid):
    for name, text in (("refunds", REFUNDS_YAML), ("notify", NOTIFY_YAML)):
        r = fake_api.post("/api-specs", {"project_id": pid, "name": name, "spec_text": text})
        assert r.status_code == 200, r.text
    use_script(fake_api, "refund_over_500")
    p = start(fake_api, pid, request="Refunds over $500 need a manager; always tell the requester. " + uniq("r"))
    assert [i for i in p["spec"]["issues"] if i["level"] == "error"] == [], p["spec"]["issues"]
    ops = {o["api_ref"] for o in json.loads(llm_calls(fake_api)[0]["messages"][-1]["content"])["api_operations"]}
    assert ops == {"refunds.issueRefund", "notify.sendMessage"}           # the model is shown exactly the project's API operations
    assert fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}).status_code == 200
    b = fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}).json()
    assert b["status"] == "ready", b.get("error_message")
    rep = b["report"]
    assert rep["passed"] is True, json.dumps([r["failures"] for r in rep["tests"]["results"]])[:1500]
    assert rep["tests"]["summary"]["total"] == 4 and rep["checker"]["errors"] == 0
    d = b["proposed_definition"]
    node = {n["name"]: n for n in d["nodes"]}
    # the server filled the catalog form of each api_ref, the form's screen and default mocks; the AI did not
    assert set(node["Issue refund"]["parameters"]["api_ref"]) == {"spec_id", "operation_id"} and node["Issue refund"]["parameters"]["method"] == "POST"
    assert d["interface"]["kind"] == "form" and d["interface"]["slug"] == "refund-request" and d["interface"]["trigger_node"] == "Refund form"
    assert d["interface"]["result_node"] == "Show result"
    assert {"Issue refund", "Notify paid", "Notify rejected", "Manager review"} <= set(d["test_defaults"]["mocks"])
    assert [t["status"] for t in d["tests"]] == ["approved"] * 4
    # Keep, then the normal endpoints agree
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    wid = k.json()["workflow_id"]
    run = fake_api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert run["summary"]["passed"] == 4, run["summary"]
    assert fake_api.get(f"/workflows/{wid}").json()["definition"]["interface"]["slug"] == "refund-request"


# ── the test-immutability gate, attacked at each layer (DESIGN 6.4) ─────────────────────────────────────
# layer 1 (per round, in the loop) is covered in tests/unit/test_stage6_b4_loop.py and test_stage6_b4_ops.py;
# layers 2 and 3 are driven here by editing the proposal row behind the API's back, as a bug or an attacker with the database would.


def test_layer_2_keep_recomputes_the_fingerprint_of_the_approved_spec_tests(fake_api, fake_stack, pid):
    _, db = fake_stack
    b = build_ready(fake_api, pid)
    db.query("UPDATE ai_proposals SET spec = jsonb_set(spec, '{tests,0,name}', '\"weakened by someone\"') WHERE id = %s", (b["id"],))
    d = err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), 409, "locked_tests")
    assert d["test_ids"] and b["spec"]["tests"][0]["id"] in d["test_ids"]
    assert fake_api.get(f"/ai/proposals/{b['id']}").json()["status"] == "ready"          # refused, nothing created


def test_layer_2_keep_refuses_a_result_that_lost_an_approved_test(fake_api, fake_stack, pid):
    _, db = fake_stack
    b = build_ready(fake_api, pid)
    db.query("UPDATE ai_proposals SET proposed_definition = jsonb_set(proposed_definition, '{tests}', "
             "(proposed_definition->'tests') - 0) WHERE id = %s", (b["id"],))
    err(fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}), 409, "locked_tests")


def test_layer_3_the_normal_save_path_still_guards_locked_tests_that_were_already_in_the_workflow(fake_api, fake_stack, pid):
    _, db = fake_stack
    b = build_ready(fake_api, pid)
    wid = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}).json()["workflow_id"]
    use_script(fake_api, "modify_flag_high_value")
    m = start(fake_api, pid, request="Also flag amounts over 10000 as high value " + uniq("r"), workflow_id=wid)
    fake_api.post(f"/ai/proposals/{m['id']}/spec/approve", {"actor": "Asha"})
    mb = fake_api.post(f"/ai/proposals/{m['id']}/build", {"actor": "Asha"}).json()
    assert mb["status"] == "ready" and mb["report"]["passed"] is True
    # the first two tests of the proposal are the workflow's locked ones; rewrite one of them in the stored proposal
    db.query("UPDATE ai_proposals SET proposed_definition = jsonb_set(proposed_definition, '{tests,0,expect}', '[]') WHERE id = %s", (m["id"],))
    d = err(fake_api.post(f"/ai/proposals/{m['id']}/keep", {"actor": "Asha"}), 409, "locked_tests")
    assert d["test_ids"]
    w = fake_api.get(f"/workflows/{wid}").json()["definition"]
    assert len(w["tests"]) == 3 and all(t["expect"] for t in w["tests"]) and not any(n["name"] == "Flag high value" for n in w["nodes"])
