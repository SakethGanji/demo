"""Stage 6 B5 over HTTP on the fake-LLM engine with real Postgres: the migrated chat (POST /api/ai/chat), the project's api_ref list
(GET /api/ai/builder/api-refs), and the base_definition of the proposal view. Fake LLM only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
SCRIPTS = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder"
REQUEST = "Amounts over 500 are marked for review, the rest are approved automatically, and the caller always gets the status back."

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
            schema: {type: object, required: [amount], properties: {amount: {type: number}}}
      responses:
        "201":
          description: issued
          content:
            application/json:
              schema: {type: object, properties: {refund_id: {type: string}}}
"""


def rules(name: str) -> list[dict]:
    return json.loads((SCRIPTS / f"{name}.json").read_text())["rules"]


def use(fake_api, rule_list: list[dict]) -> None:
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={"rules": rule_list})
    assert r.status_code == 200, r.text
    fake_api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def calls(fake_api) -> list[dict]:
    return fake_api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()


def route(kind: str, answer: str | None = None) -> dict:
    reply = {"route": kind, "node": None, "reason": "t", **({"answer": answer} if answer else {})}
    return {"id": "route", "when": {"system_matches": "^WF_AI:CHAT_ROUTE\\n"}, "respond": {"text": json.dumps(reply)}}


def only(rule_list: list[dict], rid: str) -> dict:
    return next(r for r in rule_list if r["id"] == rid)


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


@pytest.fixture
def pid(fake_api, clean) -> str:
    r = fake_api.post("/projects", {"name": uniq("b5")})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def kept_workflow(api, pid: str) -> str:
    use(api, rules("first_try"))
    p = api.post("/ai/builder/spec", {"project_id": pid, "request": f"{REQUEST} ({uniq('r')})", "actor": "Asha"}).json()["proposal"]
    assert api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}).status_code == 200
    b = api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}).json()
    k = api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    return k.json()["workflow_id"]


def chat(api, body: dict, headers: dict | None = None) -> list[dict]:
    """POST /api/ai/chat the way the studio does; the parsed ``data:`` payloads in order."""
    out = []
    with api.http.stream("POST", "/api/ai/chat", content=json.dumps(body), headers={"Content-Type": "application/json", **(headers or {})}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data:") and line[5:].strip():
                out.append(json.loads(line[5:].strip()))
    return out


def types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def test_a_question_streams_an_answer_and_makes_no_proposal(fake_api, pid):
    wid = kept_workflow(fake_api, pid)
    n = len(fake_api.get(f"/ai/proposals?project_id={pid}").json()["items"])
    use(fake_api, [route("explain", "It reviews amounts over 500.")])
    ev = chat(fake_api, {"message": "what does this do?", "workflow_id": wid, "mode_hint": "auto", "conversation_history": []})
    assert types(ev) == ["phase", "route", "text", "done"] and ev[2]["content"] == "It reviews amounts over 500."
    assert len(fake_api.get(f"/ai/proposals?project_id={pid}").json()["items"]) == n


def test_a_change_streams_a_proposal_event_and_saves_nothing_until_keep(fake_api, pid):
    wid = kept_workflow(fake_api, pid)
    before = fake_api.get(f"/workflows/{wid}").json()["definition"]
    use(fake_api, [route("modify"), only(rules("modify_flag_high_value"), "build")])
    ev = chat(fake_api, {"message": "Also flag amounts over 10000 as high value", "workflow_id": wid, "project_id": pid, "mode_hint": "auto"},
              headers={"X-Actor-Name": "Asha"})
    assert types(ev) == ["phase", "route", "phase", "proposal", "done"] and "operations" not in types(ev)
    p = ev[3]
    assert p["status"] == "ready" and p["report"]["passed"] is True and p["diff"]["counts"]["added"] == 1
    assert fake_api.get(f"/workflows/{wid}").json()["definition"] == before                 # still the old draft
    view = fake_api.get(f"/ai/proposals/{p['proposal_id']}?project_id={pid}").json()
    assert view["kind"] == "modify" and view["actor"] == "Asha"
    assert [n["name"] for n in view["base_definition"]["nodes"]] == [n["name"] for n in before["nodes"]]   # (a): the view carries the base
    sent = json.loads(next(c for c in calls(fake_api) if c["messages"][0]["content"].startswith("WF_AI:BUILD"))["messages"][-1]["content"])
    assert sent["untrusted_revision_request"] == "Also flag amounts over 10000 as high value"
    k = fake_api.post(f"/ai/proposals/{p['proposal_id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200
    assert any(n["name"] == "Flag high value" for n in fake_api.get(f"/workflows/{wid}").json()["definition"]["nodes"])


def test_a_new_workflow_request_streams_a_spec_to_approve(fake_api, pid):
    use(fake_api, [route("build"), only(rules("first_try"), "spec")])
    ev = chat(fake_api, {"message": REQUEST + " " + uniq("s"), "project_id": pid, "mode_hint": "auto"}, headers={"X-Actor-Name": "Asha"})
    assert types(ev) == ["phase", "route", "phase", "spec", "done"]
    assert ev[3]["status"] == "spec_draft" and ev[3]["spec"]["tests"]
    assert fake_api.post(f"/ai/proposals/{ev[3]['proposal_id']}/spec/approve", {"actor": "Asha"}).status_code == 200


def test_a_change_without_an_actor_is_a_coded_error_event_and_the_model_is_not_asked_to_build(fake_api, pid):
    wid = kept_workflow(fake_api, pid)
    use(fake_api, [route("modify"), only(rules("modify_flag_high_value"), "build")])
    ev = chat(fake_api, {"message": "flag big ones", "workflow_id": wid, "mode_hint": "modify"})
    assert types(ev)[-2:] == ["error", "done"] and ev[-2]["code"] == "actor_required"
    assert not [c for c in calls(fake_api) if c["messages"][0]["content"].startswith("WF_AI:BUILD")]


def test_a_card_number_never_reaches_the_model_over_http(fake_api, pid):
    use(fake_api, [route("explain", "ok")])
    chat(fake_api, {"message": "why was 4111 1111 1111 1111 declined?", "project_id": pid, "mode_hint": "auto",
                    "conversation_history": [{"role": "user", "content": "card 4111111111111111"}]})
    seen = json.dumps(calls(fake_api))
    assert "4111 1111 1111 1111" not in seen and "4111111111111111" not in seen


def test_the_old_operations_event_and_save_tools_are_gone(fake_api, pid):
    use(fake_api, [route("build"), only(rules("first_try"), "spec")])
    ev = chat(fake_api, {"message": REQUEST + " " + uniq("g"), "project_id": pid, "mode_hint": "generate"}, headers={"X-Actor-Name": "Asha"})
    assert "operations" not in types(ev)
    assert all(not c.get("tools") for c in calls(fake_api))                                   # the chat gives the model no tools at all


def test_api_refs_lists_the_projects_operations_as_the_engine_names_them(fake_api, pid):
    empty = fake_api.get(f"/ai/builder/api-refs?project_id={pid}")
    assert empty.status_code == 200 and empty.json()["api_refs"] == []
    r = fake_api.post("/api-specs", {"project_id": pid, "name": "refunds", "spec_text": REFUNDS_YAML})
    assert r.status_code == 200, r.text
    got = fake_api.get(f"/ai/builder/api-refs?project_id={pid}").json()
    assert got["api_refs"] == ["refunds.issueRefund"] and got["items"][0]["method"] == "POST" and got["items"][0]["path"] == "/refunds"
    other = fake_api.post("/projects", {"name": uniq("other")}).json()["id"]
    assert fake_api.get(f"/ai/builder/api-refs?project_id={other}").json()["api_refs"] == []     # another project's refs are not shown
    assert fake_api.get("/ai/builder/api-refs?project_id=proj_nope").status_code == 404
    assert fake_api.get("/ai/builder/api-refs").status_code == 422
