"""Journey: a bank analyst builds, changes and rolls back a refund workflow with the AI builder, entirely over HTTP.

Run it by path (the file name is not collected by default):

    venv/bin/python -m pytest -q -p no:cacheprovider tests/journeys/ai_builder_journey.py

It uses the fake LLM only; the scripted replies are tests/fixtures/fake_llm/ai_builder/refund_over_500.json (the builder) and a
small inline script for the chat change. The story:

  1. The analyst's project has the refunds and notify APIs; the spec step offers their operations.
  2. She describes the refund rule in plain English (with a customer's email in the text; the model must never see it).
  3. She reviews the spec, edits a rule and a test name, approves it. The approved tests become the gate.
  4. The AI builds it on a copy; she reviews the diff and the test report. Nothing is saved yet.
  5. She keeps it, and runs the workflow's own tests: green. The locked tests cannot be weakened.
  6. In the chat she asks what it does, then asks for a change. The change is a proposal; she keeps it, and the tests stay green.
  7. She undoes it. The workflow is exactly the version from step 5. The audit trail tells the whole story.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..integration.conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
SCRIPTS = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder"
ANALYST = "Priya Nair"
CUSTOMER_EMAIL = "ana.silva@customer-example.com"
CUSTOMER_CARD = "4111 1111 1111 1111"

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


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────


def script(api, rules: list[dict]) -> None:
    assert api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={"rules": rules}).status_code == 200
    api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def builder_rules() -> list[dict]:
    return json.loads((SCRIPTS / "refund_over_500.json").read_text())["rules"]


def sent_to_model(api) -> str:
    return json.dumps(api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json())


def chat(api, body: dict, actor: str = ANALYST) -> list[dict]:
    """POST /api/ai/chat the way the studio does; the parsed ``data:`` payloads in order."""
    out = []
    with api.http.stream("POST", "/api/ai/chat", content=json.dumps(body),
                         headers={"Content-Type": "application/json", "X-Actor-Name": actor}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data:") and line[5:].strip():
                out.append(json.loads(line[5:].strip()))
    return out


def route_rule(kind: str, answer: str | None = None) -> dict:
    reply = {"route": kind, "node": None, "reason": "journey", **({"answer": answer} if answer else {})}
    return {"id": "route", "when": {"system_matches": "^WF_AI:CHAT_ROUTE\\n"}, "respond": {"text": json.dumps(reply)}}


def flag_rule() -> dict:
    """The model's answer to 'flag refunds over 10000 for fraud review': one extra Set node off the form (a dead end, so nothing else moves)."""
    ops = [{"op": "add_node", "why": "", "args_json": json.dumps({
                "name": "Flag for fraud review", "type": "Set",
                "parameters": {"mode": "manual", "keepOnlySet": False, "fields": [{"name": "fraud_review", "value": "{{ $json.amount > 10000 }}"}]}})},
           {"op": "connect", "why": "", "args_json": json.dumps({"source": "Refund form", "target": "Flag for fraud review"})}]
    return {"id": "build", "when": {"system_matches": "^WF_AI:BUILD\\n"}, "respond": {"text": json.dumps({"plan": [], "ops": ops, "notes": ""})}}


def shape(defn: dict) -> dict:
    """What identifies a saved draft, comparable before and after: nodes, connections and the tests with their lock state."""
    return {"nodes": [(n["name"], n["type"], n["parameters"]) for n in defn["nodes"]],
            "connections": sorted((c["source_node"], c["source_output"], c["target_node"], c["target_input"]) for c in defn["connections"]),
            "tests": [(t["id"], t["name"], t["status"], t["locked"], t["expect"]) for t in defn["tests"]],
            "interface": defn.get("interface")}


def workflows_of(api, pid: str) -> list[dict]:
    body = api.get(f"/projects/{pid}/workflows").json()
    return body if isinstance(body, list) else body.get("items", [])


@pytest.fixture
def restore_fake_llm(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


# ── the journey ──────────────────────────────────────────────────────────────────────────────────


def test_analyst_builds_changes_and_rolls_back_the_refund_workflow(fake_api, restore_fake_llm):
    api = fake_api

    # 1. her project, with the two APIs the workflow may call
    pid = api.post("/projects", {"name": uniq("refunds-team")}).json()["id"]
    for name, text in (("refunds", REFUNDS_YAML), ("notify", NOTIFY_YAML)):
        assert api.post("/api-specs", {"project_id": pid, "name": name, "spec_text": text}).status_code == 200
    refs = api.get("/ai/builder/api-refs", params={"project_id": pid}).json()
    assert sorted(refs["api_refs"]) == ["notify.sendMessage", "refunds.issueRefund"]             # the spec step's picker

    # 2. she describes the rule; a customer's email and card are in her text
    script(api, builder_rules())
    request = (f"Refunds over $500 need a manager's approval before they are issued; smaller ones are issued automatically. "
               f"A rejected refund is never issued and the requester is always told the outcome. "
               f"(Case: {CUSTOMER_EMAIL} paid with {CUSTOMER_CARD}.) [{uniq('case')}]")
    r = api.post("/ai/builder/spec", {"project_id": pid, "request": request, "actor": ANALYST})
    assert r.status_code == 201, r.text
    proposal = r.json()["proposal"]
    pid_prop = proposal["id"]
    assert proposal["status"] == "spec_draft" and proposal["kind"] == "build" and proposal["actor"] == ANALYST
    assert "500" in proposal["request"] and CUSTOMER_EMAIL not in proposal["request"] and "4111" not in proposal["request"]
    assert any("card" in w for w in proposal["spec"]["warnings"])                                  # she is told what was removed

    # 3. she reviews the spec: four rules, each with a test, no errors
    spec = proposal["spec"]
    assert [x["level"] for x in spec["issues"] if x["level"] == "error"] == []
    assert len(spec["rules"]) == 4 and len(spec["tests"]) == 4
    assert all(spec["rule_tests"][rule["id"]] for rule in spec["rules"])
    assert [s["node_type"] for s in spec["steps"]][:2] == ["FormTrigger", "If"] and "Approval" in [s["node_type"] for s in spec["steps"]]
    # ... edits a rule's wording and a test's name, and saves the edit
    edited = {k: v for k, v in spec.items() if k not in ("issues", "warnings")}
    edited["rules"][0]["text"] = "Refunds over $500 need a manager's approval before they are issued (policy RF-7)."
    edited["tests"][1]["name"] = "A $900 refund is issued only after the manager approves"
    put = api.put(f"/ai/proposals/{pid_prop}/spec", {"spec": edited, "actor": ANALYST})
    assert put.status_code == 200, put.text
    assert put.json()["spec"]["rules"][0]["text"].endswith("(policy RF-7).") and put.json()["spec"]["tests"][1]["status"] == "draft"
    assert [x for x in put.json()["spec"]["issues"] if x["level"] == "error"] == []
    # ... and approves: the tests are now locked and the spec has a fingerprint
    ap = api.post(f"/ai/proposals/{pid_prop}/spec/approve", {"actor": ANALYST})
    assert ap.status_code == 200 and ap.json()["status"] == "spec_approved" and ap.json()["spec_approved_by"] == ANALYST
    assert all(t["status"] == "approved" and t["locked"] for t in ap.json()["spec"]["tests"]) and ap.json()["spec_fingerprint"]

    # 4. the build: on a copy, tests green, nothing saved
    b = api.post(f"/ai/proposals/{pid_prop}/build", {"actor": ANALYST})
    assert b.status_code == 200, b.text
    built = b.json()
    assert built["status"] == "ready" and built["report"]["passed"] is True and built["report"]["checker"]["errors"] == 0
    assert built["report"]["tests"]["summary"]["total"] == 4 and built["report"]["tests"]["summary"]["passed"] == 4
    assert workflows_of(api, pid) == []                                                             # nothing exists until she keeps it
    # she reviews the diff: seven new nodes, wired as the spec says, with the review flags the bank cares about
    diff = built["diff"]
    assert diff["counts"]["added"] == 7 and diff["counts"]["removed"] == 0 and diff["nodes"]["removed"] == []
    assert set(diff["nodes"]["added"]) == {"Refund form", "Over limit?", "Manager review", "Issue refund", "Notify paid", "Notify rejected", "Show result"}
    assert len(diff["connections"]["added"]) == 8 and ["Over limit?", "true", "Manager review", "main"] in [list(c)[:4] for c in diff["connections"]["added"]]
    assert "new_destinations" in diff and "new_code" in diff and diff["new_code"] == []             # the AI wrote no code
    defn = built["proposed_definition"]
    assert defn["interface"]["kind"] == "form" and defn["interface"]["slug"] == "refund-request"
    assert built["usage"]["llm_calls"] == 2 and built["policy"]["model"] == "gemini-3.5-flash"
    assert CUSTOMER_EMAIL not in sent_to_model(api) and "4111 1111 1111 1111" not in sent_to_model(api)
    shown = json.dumps(api.get(f"/ai/proposals/{pid_prop}", params={"project_id": pid}).json())
    assert '"system"' not in shown and '"messages"' not in shown                                    # the view never carries prompts

    # 5. she keeps it, names it, and runs the workflow's own tests
    k = api.post(f"/ai/proposals/{pid_prop}/keep", {"actor": ANALYST, "name": "Refund approval policy"})
    assert k.status_code == 200, k.text
    wid = k.json()["workflow_id"]
    saved = api.get(f"/workflows/{wid}").json()
    assert saved["name"] == "Refund approval policy" and k.json()["proposal"]["status"] == "kept" and k.json()["proposal"]["kept_by"] == ANALYST
    assert [t["status"] for t in saved["definition"]["tests"]] == ["approved"] * 4 and all(t["locked"] for t in saved["definition"]["tests"])
    run = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert run["summary"]["passed"] == 4 and run["summary"]["failed"] == 0 and run["summary"]["approved_failed"] == 0
    kept_version = shape(saved["definition"])
    # the approved tests are the gate: dropping one through an ordinary save is refused and changes nothing
    d = saved["definition"]
    weaken = api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"], "settings": d.get("settings") or {}, "tests": d["tests"][1:]})
    assert weaken.status_code == 409
    assert shape(api.get(f"/workflows/{wid}").json()["definition"]) == kept_version

    # 6. in the chat she asks a question, then asks for a change
    script(api, [route_rule("explain", "It sends refunds over 500 to a manager and issues the rest automatically.")])
    ev = chat(api, {"message": "What does this workflow do?", "workflow_id": wid, "mode_hint": "auto"})
    assert [e["type"] for e in ev] == ["phase", "route", "text", "done"] and "manager" in ev[2]["content"]
    assert shape(api.get(f"/workflows/{wid}").json()["definition"]) == kept_version                # a question changes nothing

    script(api, [route_rule("modify"), flag_rule()])
    ev = chat(api, {"message": f"Also flag refunds over 10000 for fraud review ({CUSTOMER_EMAIL} complained)", "workflow_id": wid, "mode_hint": "auto"})
    assert [e["type"] for e in ev] == ["phase", "route", "phase", "proposal", "done"] and ev[1]["route"] == "modify"
    change = ev[3]
    assert change["status"] == "ready" and change["report"] == {"passed": True} and change["diff"]["counts"]["added"] == 1
    assert shape(api.get(f"/workflows/{wid}").json()["definition"]) == kept_version                # still the old draft: nothing saved before Keep
    assert CUSTOMER_EMAIL not in sent_to_model(api)
    view = api.get(f"/ai/proposals/{change['proposal_id']}", params={"project_id": pid}).json()
    assert view["kind"] == "modify" and view["workflow_id"] == wid and view["base_draft_hash"]
    assert view["diff"]["nodes"]["added"] == ["Flag for fraud review"] and len(view["base_definition"]["nodes"]) == 7

    k2 = api.post(f"/ai/proposals/{change['proposal_id']}/keep", {"actor": ANALYST})
    assert k2.status_code == 200, k2.text
    after = api.get(f"/workflows/{wid}").json()["definition"]
    assert "Flag for fraud review" in [n["name"] for n in after["nodes"]] and len(after["nodes"]) == 8
    assert api.post(f"/workflows/{wid}/tests/run", {}).json()["summary"]["passed"] == 4            # the gate still holds with the change in

    # 7. she undoes it: the workflow is exactly the version from step 5
    u = api.post(f"/ai/proposals/{change['proposal_id']}/undo", {"actor": ANALYST})
    assert u.status_code == 200 and u.json()["status"] == "undone"
    back = api.get(f"/workflows/{wid}").json()["definition"]
    assert shape(back) == kept_version and len(back["nodes"]) == 7
    run = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert run["summary"]["passed"] == 4 and run["summary"]["failed"] == 0

    # the audit trail: who asked, what was kept, what was undone, and never the prompts
    audit = api.get("/ai/proposals", params={"project_id": pid}).json()["items"]
    assert [(i["kind"], i["status"], i["actor"]) for i in audit] == [("modify", "undone", ANALYST), ("build", "kept", ANALYST)]
    assert all(i["passed"] is True and i["workflow_id"] == wid for i in audit)
    story = [(e["event"], e["actor"]) for e in api.get(f"/ai/proposals/{pid_prop}/events").json()["items"] if e["event"] in ("spec_approved", "kept")]
    assert story == [("spec_approved", ANALYST), ("kept", ANALYST)]
    ev2 = [e["event"] for e in api.get(f"/ai/proposals/{change['proposal_id']}/events").json()["items"]]
    assert ev2[-2:] == ["kept", "undone"]
    llm = [e["payload"] for e in api.get(f"/ai/proposals/{pid_prop}/events").json()["items"] if e["event"] == "llm_call"]
    assert llm and all(len(x["prompt_sha256"]) == 64 and "system" not in x and "user" not in x for x in llm)
