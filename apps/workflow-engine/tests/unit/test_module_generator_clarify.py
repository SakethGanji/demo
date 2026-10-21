"""The backend builder's spec-first step: with ``clarify`` it writes a spec and asks about gaps instead of guessing,
and only builds once every question has an answer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.services import module_generator as mg

SPEC = {
    "summary": "QA workbench",
    "entities": [{"name": "test_cases", "fields": [{"name": "title", "type": "string", "required": True}]},
                 {"name": "runs", "fields": [{"name": "case_id", "type": "integer", "ref": "cases.id"}]}],
    "workflows": [{"entity": "test_cases", "states": ["draft", "approved"], "transitions": []}],
    "roles": [], "external_systems": [{"name": "Jobs", "use": "runs"}], "acceptance_criteria": [],
}


def _run(req: mg.GenerateRequest, monkeypatch, reply: dict | None) -> tuple[list[dict], list[list[dict]]]:
    from src.engine import llm_provider

    sent: list[list[dict]] = []

    async def fake_llm(model, messages, temperature=0.2, tools=None, **kw):
        sent.append(messages)
        if reply is None:
            raise RuntimeError("stop here")
        tc = SimpleNamespace(name=tools[0]["name"], args=reply, id="t1")
        return SimpleNamespace(tool_calls=[tc], text=None, get_assistant_message=lambda: {"role": "assistant"})

    async def fake_db(self, fn):
        facts = {"slug": "p", "environments": ["default"], "tables": [], "routes": [], "modules": [], "secrets": {},
                 "egress": [], "jobs": True}
        return SimpleNamespace(id="proj"), facts, set(), None

    monkeypatch.setattr(llm_provider, "call_llm", fake_llm)
    monkeypatch.setattr(mg.Generation, "_db", fake_db)

    async def collect():
        return [ev async for ev in mg.Generation("proj", req, timeout_s=10).events()]
    return asyncio.run(collect()), sent


def test_gap_checks_find_what_a_careful_developer_would_ask():
    ids = {q["id"] for q in mg.gap_questions(SPEC)}
    assert {"chk_required_runs", "chk_ref_runs_case_id", "chk_transitions_test_cases", "chk_roles",
            "chk_acceptance"} <= ids
    assert "chk_required_test_cases" not in ids
    assert {q["id"] for q in mg.gap_questions({})} >= {"chk_entities", "chk_roles", "chk_acceptance"}


def test_clarify_asks_instead_of_building(monkeypatch):
    reply = {**SPEC, "questions": [{"id": "q1", "topic": "roles", "question": "Who approves a case?",
                                    "options": ["lead", "any tester"], "default": "lead"}]}
    events, sent = _run(mg.GenerateRequest(prompt="build a QA workbench", clarify=True), monkeypatch, reply)
    last = events[-1]
    assert last["type"] == "result" and last["status"] == "clarify" and last["needs_answers"] is True
    assert last["source"] is None and len(sent) == 1          # one spec call, no module call
    ids = [q["id"] for q in last["questions"]]
    assert ids[0] == "q1" and "chk_roles" not in ids           # the model already asked about roles
    assert "chk_acceptance" in ids and last["spec"]["open_questions"] == last["questions"]
    assert any("emit_spec" in str(m.get("content")) for m in sent[0])


def test_answers_must_cover_every_question():
    spec = {**SPEC, "open_questions": [{"id": "q1", "question": "?"}, {"id": "chk_roles", "question": "?"}]}
    with pytest.raises(ValidationError, match="unanswered questions: chk_roles"):
        mg.GenerateRequest(prompt="x", spec=spec, answers={"q1": "lead"})
    with pytest.raises(ValidationError, match="need the spec"):
        mg.GenerateRequest(prompt="x", answers={"q1": "lead"})
    with pytest.raises(ValidationError):
        mg.GenerateRequest(prompt="x", spec=spec, answers={"q1": "lead", "chk_roles": "   "})
    mg.GenerateRequest(prompt="x", spec=spec, answers={"q1": "lead", "chk_roles": "testers, leads"})


def test_answered_spec_goes_to_the_build_call(monkeypatch):
    spec = {**SPEC, "open_questions": [{"id": "q1", "question": "Who approves a case?"}]}
    req = mg.GenerateRequest(prompt="build it", clarify=True, spec=spec, answers={"q1": "the QA lead"})
    _events, sent = _run(req, monkeypatch, None)
    assert len(sent) == 1                                      # straight to the module call: no second spec round
    agreed = sent[0][-1]["content"]
    assert "Build exactly this" in agreed and "the QA lead" in agreed and "test_cases" in agreed
    assert "open_questions" not in agreed


def test_without_clarify_nothing_changes(monkeypatch):
    events, sent = _run(mg.GenerateRequest(prompt="build it"), monkeypatch, None)
    assert len(sent) == 1 and sent[0][-1]["content"] == "build it"
    assert not any(e.get("phase") == "specifying" for e in events)


# ── legacy path (no plan): what an earlier module's dialogue settled is not asked again ─────────────────────────────

def _prior(monkeypatch, topics, entities=(), answers=()):
    async def fake_prior(self, project_id):
        return {"topics": set(topics), "answers": list(answers), "entities": set(entities)}
    monkeypatch.setattr(mg.Generation, "_prior_knowledge", fake_prior)


_BIG = {**SPEC, "roles": [{"name": "lead"}], "acceptance_criteria": ["x"], "workflows": [],
        "external_systems": [{"name": "Jobs", "use": "run tests with a password secret"}]}


def test_second_module_does_not_ask_topics_the_first_settled(monkeypatch):
    _prior(monkeypatch, {"credentials", "environments", "failure"},
           answers=[{"id": "runs:dom_credentials", "topic": "credentials", "question": "Where do credentials live?",
                     "answer": "Project secrets", "inherited": True}])
    reply = {**_BIG, "questions": [{"id": "q1", "topic": "credentials", "question": "Which credentials?"}]}
    events, _ = _run(mg.GenerateRequest(prompt="build the triage module that calls the jobs api with a password", clarify=True),
                     monkeypatch, reply)
    qs = events[-1]["questions"]
    topics = {q["topic"] for q in qs}
    assert not topics & {"credentials", "environments", "failure"}
    assert "q1" not in [q["id"] for q in qs]                     # the model's own repeat of a settled topic is dropped too
    assert events[-1]["spec"]["answered"][0]["answer"] == "Project secrets"     # and the earlier answer travels with the spec


def test_first_module_still_asks_everything(monkeypatch):
    _prior(monkeypatch, set())
    events, _ = _run(mg.GenerateRequest(prompt="build the triage module that calls the jobs api with a password", clarify=True),
                     monkeypatch, {**_BIG, "questions": []})
    assert {"credentials", "environments", "failure"} <= {q["topic"] for q in events[-1]["questions"]}


def test_sibling_refs_do_not_ask(monkeypatch):
    spec = {**_BIG, "entities": [{"name": "runs", "fields": [{"name": "case_id", "type": "integer", "required": True,
                                                              "ref": "cases.id"}]}]}
    assert "chk_ref_runs_case_id" in {q["id"] for q in mg.gap_questions(spec)}
    assert "chk_ref_runs_case_id" not in {q["id"] for q in mg.gap_questions(spec, known_entities={"cases"})}
    _prior(monkeypatch, set(), entities={"cases"})
    events, _ = _run(mg.GenerateRequest(prompt="build runs", clarify=True), monkeypatch, {**spec, "questions": []})
    assert "chk_ref_runs_case_id" not in [q["id"] for q in events[-1]["questions"]]


def test_secret_names_followup_is_not_asked_when_names_were_given(monkeypatch):
    from src.services.backend_templates import gaps

    spec = {**_BIG, "open_questions": [{"id": "dom_credentials", "topic": "credentials", "question": "?"}]}
    plain = {"dom_credentials": "Project secrets referenced by name, one per system"}
    assert "dom2_secret_names" in {q["id"] for q in gaps.followup_questions(spec, plain, 1)}
    named = {"dom_credentials": "Project secrets: BANK_USER, BANK_PASS and BANK_TOTP in every environment"}
    assert "dom2_secret_names" not in {q["id"] for q in gaps.followup_questions(spec, named, 1)}
    # or when the project already has the secret and the answers/spec name it
    known = {"dom_credentials": "Project secrets; the Jobs system uses JOBS-key"}
    spec2 = {**spec, "external_systems": [{"name": "Jobs", "use": "uses jobskey"}]}
    assert "dom2_secret_names" not in {q["id"] for q in gaps.followup_questions(spec2, {"dom_credentials": "secrets; jobskey"}, 1,
                                                                             known_secrets={"jobskey"})}
    assert "dom2_secret_names" in {q["id"] for q in gaps.followup_questions(spec2, known, 1, known_secrets={"other"})}


def test_round_two_passes_the_projects_secrets_to_the_followups(monkeypatch):
    seen = {}
    from src.services.backend_templates import gaps

    real = gaps.followup_questions

    def spy(spec, answers, rnd=1, known_secrets=None):
        seen["secrets"] = known_secrets
        return real(spec, answers, rnd, known_secrets)
    monkeypatch.setattr(gaps, "followup_questions", spy)
    spec = {**_BIG, "round": 1, "open_questions": [{"id": "q1", "question": "?"}]}
    _run(mg.GenerateRequest(prompt="x", clarify=True, spec=spec, answers={"q1": "a"}), monkeypatch, None)
    assert seen["secrets"] == set()
