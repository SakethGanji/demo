"""Stage 8 B8 over HTTP on the fake-LLM engine with real Postgres: the spec draft offers a matching template (never installs it) and
grounds the model in the project's callable sub-workflows; with neither, the prompt carries no trace of either. Fake LLM only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._stage8_b4_support import child_body, make, publish
from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
SCRIPTS = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder"
PLAIN = "Amounts over 500 are marked for review, the rest are approved automatically, and the caller always gets the status back."
REFUND = "Classify refund requests, pay small refunds at once and send larger refunds to a manager for approval."


def use(api) -> None:
    rules = json.loads((SCRIPTS / "first_try.json").read_text())["rules"]
    assert api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={"rules": rules}).status_code == 200
    api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def spec_prompts(api) -> list[str]:
    calls = api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()
    return ["\n".join(str(m.get("content")) for m in c["messages"]) for c in calls if "WF_AI:SPEC" in json.dumps(c["messages"])[:200]]


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


@pytest.fixture
def pid(fake_api, clean) -> str:
    r = fake_api.post("/projects", {"name": uniq("b8")})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def draft(api, pid, text):
    use(api)
    r = api.post("/ai/builder/spec", {"project_id": pid, "request": f"{text} ({uniq('r')})", "actor": "Asha"})
    assert r.status_code == 201, r.text
    return r.json()["proposal"]


def test_a_matching_request_gets_a_template_suggestion_and_nothing_is_installed(fake_api, pid):
    p = draft(fake_api, pid, REFUND)
    s = p["spec"]["template_suggestion"]
    assert s["kind"] == "template" and s["slug"] == "refund-triage" and s["reason"] and 0.35 <= s["score"] <= 1
    assert fake_api.get(f"/projects/{pid}/template-installs").json() == []
    assert "template_suggestion" not in "\n".join(spec_prompts(fake_api))
    # editing the plan keeps the card; approving it drops it
    spec = {k: v for k, v in p["spec"].items() if k not in ("issues", "warnings", "template_suggestion")}
    assert fake_api.put(f"/ai/proposals/{p['id']}/spec", {"spec": spec, "actor": "Asha"}).json()["spec"]["template_suggestion"] == s


def test_an_unrelated_request_gets_none_and_the_prompt_has_no_sub_workflow_section(fake_api, pid):
    p = draft(fake_api, pid, PLAIN)
    assert "template_suggestion" not in p["spec"]
    prompts = spec_prompts(fake_api)
    assert prompts and all("callable_subworkflows" not in t and "Reusable sub-workflows" not in t for t in prompts)


def test_callable_published_children_are_offered_and_others_are_not(fake_api, pid):
    ok = make(fake_api, child_body("v1", ("amount", "number", True), name=uniq("Refund eligibility"), project_id=pid))
    assert publish(fake_api, ok).status_code == 200
    unpublished = make(fake_api, child_body("v1", name=uniq("Never published"), project_id=pid))
    draft(fake_api, pid, PLAIN)
    text = "\n".join(spec_prompts(fake_api))
    section = json.loads(text[text.index('{"untrusted_request"'):text.rindex("}") + 1])["callable_subworkflows"]
    assert [e["id"] for e in section] == [ok] and unpublished not in json.dumps(section)
    e = section[0]
    assert e["published_version"] == 1 and e["inputs"] == [{"name": "amount", "type": "number", "required": True}] and "Reusable sub-workflows" in text
