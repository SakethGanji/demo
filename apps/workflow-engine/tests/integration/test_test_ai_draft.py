"""Stage 3B: POST /api/workflows/{id}/tests/ai-draft over HTTP on the fake-LLM engine with real Postgres (Refund triage).
Scripted model replies prove the plumbing (prompt, sanitising, repair, real dry run, error mapping, nothing saved);
model quality is checked by tests/live/test_test_ai_gemini.py."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
RISK_MOCK = {"http": {"status": 200, "body": {"decision": "review", "score": 0.7}}}
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "status": "logged"}]}
DEFAULTS = {"mocks": {"Risk API": RISK_MOCK, "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}
LOCKED_ID = "t_a0000001"


def small_test(tid: str, name: str = "small refund auto-approved") -> dict:
    return {
        "id": tid, "name": name, "target": {"kind": "workflow"}, "category": "normal",
        "input": {"items": [{"body": {"order_id": "o-1", "amount": 100, "reason": "late"}}]}, "mocks": {},
        "expect": [{"node": "Amount over 500", "port": "false", "op": "count", "value": 1},
                   {"node": "Log approved", "op": "status", "value": "success"}],
    }


def refund_workflow(api) -> str:
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", "http://risk.example.test/v1/score"))
    wf["name"] = uniq("s3b-aidraft")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    wf["tests"] = [small_test(LOCKED_ID)]
    wf["test_defaults"] = DEFAULTS
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    r = api.post(f"/workflows/{wid}/tests/{LOCKED_ID}/approve", {"approved_by": "sam"})
    assert r.status_code == 200, r.text
    return wid


def stored_tests(api, wid: str) -> list:
    return api.get(f"/workflows/{wid}").json()["definition"]["tests"]


def put_script(fake_api, rules: list) -> None:
    pinned = json.loads(FAKE_SCRIPT.read_text())
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={**pinned, "rules": rules + pinned["rules"]})
    assert r.status_code == 200, r.text
    fake_api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def draft_rule(tests, rid="ai-draft", **when) -> dict:
    return {"id": rid, "when": {"system_matches": "^TEST_AI:DRAFT_TESTS", **when},
            "respond": {"text": json.dumps({"tests": tests})}}


@pytest.fixture
def clean(fake_api):
    yield
    pinned = json.loads(FAKE_SCRIPT.read_text())
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=pinned)
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})


def ext_error_test() -> dict:
    return {
        "name": "Risk API down", "target": {"kind": "node", "node": "Risk API"},
        "input": {"items": [{"order_id": "o-9", "amount": 900, "reason": "late"}]},
        "mocks": {"Risk API": {"error": {"message": "503 Service Unavailable"}}},
        "expect": [{"node": "Risk API", "op": "status", "value": "error"}], "category": "ext_error",
    }


def failing_test() -> dict:
    return {
        "name": "wrong expectation", "target": {"kind": "workflow"}, "category": "normal",
        "input": {"items": [{"body": {"order_id": "o-2", "amount": 100, "reason": "late"}}]},
        "expect": [{"node": "Amount over 500", "port": "true", "op": "count", "value": 1}],
    }


def test_ai_draft_sanitises_dry_runs_and_saves_nothing(fake_api, clean):
    wid = refund_workflow(fake_api)
    before = stored_tests(fake_api, wid)
    # the model reuses the locked id and claims approval; one test names a node that doesn't exist
    sneaky = {**ext_error_test(), "id": LOCKED_ID, "status": "approved", "locked": True, "approved_by": "ai",
              "approved_at": "2026-03-03T00:00:00+00:00", "source": "human"}
    ghost = {**ext_error_test(), "name": "ghost", "mocks": {"Ghost API": {"items": [{"a": 1}]}}}
    put_script(fake_api, [
        draft_rule([failing_test(), sneaky, ghost], "first", nth=1),
        draft_rule([], "repair-gives-nothing", nth=2),
    ])
    r = fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"cells": [{"node": "Risk API", "category": "ext_error"}], "max": 5})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cells"] == [{"node": "Risk API", "category": "ext_error"}] and out["model"]
    drafts = out["drafts"]
    assert len(drafts) == 2
    ids = [d["id"] for d in drafts]
    assert LOCKED_ID not in ids and len(set(ids)) == 2
    for d in drafts:
        assert d["status"] == "draft" and d["locked"] is False and d["source"] == "ai"
        assert "approved_by" not in d and "approved_at" not in d
        assert d["ai_run"]["status"] in ("passed", "failed", "error")
    by_status = {d["name"]: d["ai_run"]["status"] for d in drafts}
    assert by_status == {"Risk API down": "passed", "wrong expectation": "failed"}
    assert [d["ai_run"]["status"] for d in drafts] == ["passed", "failed"]  # passed first
    failed = next(d for d in drafts if d["ai_run"]["status"] == "failed")
    assert failed["ai_run"]["failures"] and failed["ai_run"]["failures"][0]["kind"] == "assertion"
    assert len(out["rejected"]) == 1 and "Ghost API" in out["rejected"][0]["reason"]
    # nothing saved, the locked test is byte-identical
    after = stored_tests(fake_api, wid)
    assert after == before and json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
    assert after[0]["locked"] is True and after[0]["approved_by"] == "sam"
    # the prompt was data, first line fixed, and held the existing test as a summary only
    calls = fake_api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()
    first = calls[0]["messages"]
    assert first[0]["role"] == "system" and first[0]["content"].split("\n")[0] == "TEST_AI:DRAFT_TESTS"
    prompt = json.loads(first[1]["content"])
    assert prompt["target_cells"] == [{"node": "Risk API", "category": "ext_error"}]
    assert prompt["existing_tests"][0]["id"] == LOCKED_ID and "expect" not in prompt["existing_tests"][0]
    assert "Risk API" in prompt["shapes_by_node"] and prompt["test_defaults"]["mocks"]["Risk API"] == RISK_MOCK
    assert {e["type"] for e in prompt["node_catalog"]} == {"Webhook", "Set", "If", "HttpRequest", "DataTable"}
    assert len(calls) == 2  # the repair round
    assert json.loads(calls[1]["messages"][1]["content"])["repair"]["rejected"][0]["test"]["name"] == "ghost"


def test_ai_draft_repair_round_fixes_a_bad_op(fake_api, clean):
    wid = refund_workflow(fake_api)
    bad = {**ext_error_test(), "expect": [{"node": "Risk API", "op": "explode"}]}
    put_script(fake_api, [draft_rule([bad], "first", nth=1), draft_rule([ext_error_test()], "fix", nth=2)])
    out = fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"cells": [{"node": "Risk API", "category": "ext_error"}]}).json()
    assert len(out["drafts"]) == 1 and out["rejected"] == [] and out["drafts"][0]["ai_run"]["status"] == "passed"


def test_ai_draft_defaults_to_grid_gaps_and_unsaved_definition(fake_api, clean):
    wid = refund_workflow(fake_api)
    put_script(fake_api, [draft_rule([ext_error_test()])])
    out = fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"max": 3}).json()
    assert len(out["cells"]) == 3 and all(set(c) == {"node", "category"} for c in out["cells"])
    assert not any(c["node"] == "Webhook" for c in out["cells"])  # triggers have no row
    # an unsaved definition with no tests: the locked-test cell (Log approved / normal etc.) is open again
    d = fake_api.get(f"/workflows/{wid}").json()["definition"]
    d["tests"] = []
    out2 = fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"definition": d, "max": 20}).json()
    assert {"node": "Normalize", "category": "normal"} in out2["cells"]
    prompt = json.loads(fake_api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()[-1]["messages"][1]["content"])
    assert prompt["existing_tests"] == []
    assert stored_tests(fake_api, wid)[0]["id"] == LOCKED_ID  # the stored workflow never changed


def test_ai_draft_error_mapping(fake_api, clean):
    wid = refund_workflow(fake_api)
    body = {"cells": [{"node": "Risk API", "category": "ext_error"}]}
    # not configured -> 503
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": True})
    r = fake_api.post(f"/workflows/{wid}/tests/ai-draft", body)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ai_not_configured"
    fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})
    # the reply isn't JSON -> 502
    put_script(fake_api, [{"id": "junk", "when": {"system_matches": "^TEST_AI:DRAFT_TESTS"}, "respond": {"text": "I am sorry, I cannot."}}])
    r = fake_api.post(f"/workflows/{wid}/tests/ai-draft", body)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "ai_failed"
    # the provider fails -> 502
    put_script(fake_api, [{"id": "down", "when": {"system_matches": "^TEST_AI:DRAFT_TESTS"}, "respond": {"raise": "fatal"}}])
    r = fake_api.post(f"/workflows/{wid}/tests/ai-draft", body)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "ai_failed"
    assert stored_tests(fake_api, wid)[0]["id"] == LOCKED_ID and len(stored_tests(fake_api, wid)) == 1


def test_ai_draft_request_errors(fake_api, clean):
    wid = refund_workflow(fake_api)
    assert fake_api.post(f"/workflows/wf_{uuid.uuid4().hex[:8]}/tests/ai-draft", {}).status_code == 404
    assert fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"cells": [{"node": "Nope", "category": "normal"}]}).status_code == 422
    assert fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"cells": [{"node": "Risk API", "category": "zzz"}]}).status_code == 422
    assert fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"max": 0}).status_code == 422
    assert fake_api.post(f"/workflows/{wid}/tests/ai-draft", {"max": 21}).status_code == 422
