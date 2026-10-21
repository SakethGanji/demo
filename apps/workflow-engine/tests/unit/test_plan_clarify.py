"""The plan conversation: one question per topic, at most two rounds and 20 questions, answers that settle a fact are never
asked again, and after the last round the remaining gaps take their defaults as written-down assumptions. The model is
always a fake (a live key is configured in the dev environment); the conversation tests run on a throwaway Postgres."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.services import plan_clarify as pc
from src.services import project_plan as pp
from src.services.module_generator import gap_questions
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.plan_fixture import qa_plan

PROMPT = ("Build a QA workbench: a library of tests, runs started through the Jobs service against our bank app "
          "(dev and prod), and triage of failed runs with retries. Login uses the bank credentials.")


def _topics(qs):
    return [q["topic"] for q in qs]


# ── pure ─────────────────────────────────────────────────────────────────────


def test_round_one_asks_each_topic_once():
    model_qs = [{"id": "q1", "topic": "credentials", "question": "Where do the bank credentials live?"},
                {"id": "q2", "topic": "credentials", "question": "Is there a TOTP seed?"},
                {"id": "q3", "topic": "retention", "question": "How long are runs kept?"},
                {"id": "q4", "topic": "safety", "question": "May prod runs mutate data?"}]
    plan = qa_plan()
    plan["secrets"] = []                                   # PLAN006 + no hosts -> plan gap questions too
    plan["environments"][0]["base_url"] = ""
    qs = pc.first_round_questions(PROMPT, plan, model_qs, {"environments": ["dev", "prod"], "tables": []})
    topics = _topics(qs)
    assert len(topics) == len(set(topics)), topics
    assert len(qs) <= pc.ROUND1_MAX
    assert "secret_names" in topics and "env_urls" in topics
    creds = next(q for q in qs if q["topic"] == "credentials")
    assert "TOTP" in creds["question"] and creds["parts"] == ["q1", "q2"]


def test_sibling_module_refs_are_not_asked_about():
    plan = qa_plan()                                       # runs.test_id -> tests.id, tests owned by another module
    qs = pc.first_round_questions(PROMPT, plan, [], {})
    assert not [q for q in qs if q["id"].startswith("chk_ref")]
    # the projection itself is a spec gap_questions can read: the ref is to a known table
    assert not [q for q in gap_questions(pc.spec_projection(plan), {"tests", "runs", "audit_log"})
                if q["id"].startswith("chk_ref")]
    plan["data"]["tables"][1]["columns"][0]["ref"] = "suites.id"     # a genuinely unknown target is still asked
    assert any(q["id"] == "chk_ref_runs_test_id" for q in pc.first_round_questions(PROMPT, plan, [], {}))


def test_named_secrets_settle_the_secret_names_gap():
    plan = qa_plan()
    plan["secrets"] = []
    assert any(q["topic"] == "secret_names" for q in pc.plan_gap_questions(pp.validate_plan(plan), plan))
    qs = [{"id": "q1", "topic": "credentials", "question": "Where do the credentials live?", "source": "model"}]
    plan = pc.record_answers(plan, qs, {"q1": "Project secrets BANK_USER, BANK_PASS and BANK_TOTP"}, 1)
    plan = pc.secrets_from_answers(plan)
    assert {s["name"] for s in plan["secrets"]} == {"BANK_USER", "BANK_PASS", "BANK_TOTP"}
    assert all(s["allowed_hosts"] == ["localhost"] for s in plan["secrets"])      # the integration's hosts
    assert not [q for q in pc.plan_gap_questions(pp.validate_plan(plan), plan) if q["topic"] == "secret_names"]


def test_gap_answers_apply_deterministically():
    plan = qa_plan()
    plan["secrets"][0]["allowed_hosts"] = []
    plan["environments"][1]["base_url"] = ""
    plan["data"]["tables"][2]["retention"] = {"policy": "days", "value": None}
    qs = pc.plan_gap_questions(pp.validate_plan(plan), plan)
    by = {q["topic"]: q for q in qs}
    assert set(by) == {"secret_hosts", "env_urls", "retention_numbers"}
    out = pc.apply_gap_answers(plan, qs, {by["secret_hosts"]["id"]: "https://bank.example.com:443, localhost",
                                          by["env_urls"]["id"]: "prod=https://bank.example.com",
                                          by["retention_numbers"]["id"]: "audit_log=30"})
    assert out["secrets"][0]["allowed_hosts"] == ["bank.example.com", "localhost"]
    assert out["environments"][1]["base_url"] == "https://bank.example.com"
    assert pp.table_plan(out, "audit_log")["retention"] == {"policy": "days", "value": 30}
    assert not [i for i in pp.validate_plan(out) if i.get("needs_input")]


def test_after_the_last_round_defaults_become_assumptions():
    plan = qa_plan()
    plan["secrets"][0]["allowed_hosts"] = []
    plan["requirements"]["answers"].append({"id": "gap_secret_hosts", "topic": "secret_hosts", "question": "hosts?",
                                            "answer": "???", "round": 2, "source": "plan"})
    same, more = pc.follow_up(plan, {}, round_no=1)       # topic already asked: never asked again
    assert more == []
    assert same["secrets"][0]["allowed_hosts"]            # took the default
    assert any("Assumed:" in a for a in same["requirements"]["assumptions"])
    fresh = qa_plan()
    fresh["data"]["tables"][2]["retention"] = {"policy": "days", "value": None}
    _, more = pc.follow_up(fresh, {}, round_no=1)          # a new topic, a round left: asked
    assert _topics(more) == ["retention_numbers"]
    out, more = pc.follow_up(fresh, {}, round_no=2)        # no round left: defaulted
    assert more == [] and pp.table_plan(out, "audit_log")["retention"]["value"] == 90
    assert any("Assumed: audit_log=90" in a for a in out["requirements"]["assumptions"])


def test_draft_request_takes_exactly_one_mode():
    pc.PlanDraftRequest(prompt="x")
    pc.PlanDraftRequest(version=1, answers={"q1": "a"})
    pc.PlanDraftRequest(body=qa_plan())
    for bad in ({}, {"prompt": "x", "body": {}}, {"answers": {"q": "a"}}, {"prompt": "  "}):
        with pytest.raises(ValueError):
            pc.PlanDraftRequest(**bad)


def test_merge_revision_keeps_answers_and_migrations():
    plan = qa_plan()
    plan["migrations"] = [{"version": 1, "table": "tests", "ops": [], "refused": []}]
    out = pc.merge_revision(plan, {"title": "QA Bench", "requirements": {"answers": [], "assumptions": ["x"]},
                                   "data": {"tables": plan["data"]["tables"][:2]}})
    assert out["title"] == "QA Bench" and out["requirements"]["assumptions"] == ["x"]
    assert len(out["requirements"]["answers"]) == 2 and out["migrations"] == plan["migrations"]
    assert [t["name"] for t in out["data"]["tables"]] == ["tests", "runs"]


# ── the conversation (fake model, real database) ─────────────────────────────


def _core_and_surface():
    p = qa_plan()
    for i in p["integrations"]:      # credentials unknown at first
        i["secrets"] = []
    core = {k: p[k] for k in ("title", "summary", "data", "modules", "integrations", "environments", "build_order",
                              "roles_note")}
    core["requirements"] = {"assumptions": [], "acceptance": p["requirements"]["acceptance"]}
    core["questions"] = [{"id": "q_creds", "topic": "credentials", "question": "Which credentials log in to the bank app?"},
                         {"id": "q_creds2", "topic": "credentials", "question": "Is there a one-time code?"},
                         {"id": "q_ret", "topic": "retention", "question": "How long are runs kept?",
                          "options": ["forever", "90 days"], "default": "forever"}]
    surface = {k: p[k] for k in ("routes", "apps", "nfr")}
    return core, surface


def _fake_llm(monkeypatch, replies):
    from src.engine import llm_provider

    calls: list[tuple[str, list]] = []

    async def fake_llm(model, messages, temperature=0.2, tools=None, **kw):
        name = tools[0]["name"]
        calls.append((name, messages))
        tc = SimpleNamespace(name=name, args=replies[name](), id="t1")
        return SimpleNamespace(tool_calls=[tc], text=None, get_assistant_message=lambda: {"role": "assistant"})

    monkeypatch.setattr(llm_provider, "call_llm", fake_llm)
    return calls


async def _run(pid, req):
    return [ev async for ev in pc.PlanConversation(pid, req, actor="tester", timeout_s=30).events()]


@pytest.mark.anyio
async def test_conversation_two_rounds(pg, monkeypatch):
    async with pg() as s:
        pid = (await ProjectService(s).create(f"plan-{uuid.uuid4().hex[:8]}")).id
    core, surface = _core_and_surface()

    def revision():   # the model wires the named secrets into the integration, but does not list them under secrets
        p = qa_plan()
        return {"integrations": p["integrations"]}

    calls = _fake_llm(monkeypatch, {"emit_plan_core": lambda: core, "emit_plan_surface": lambda: surface,
                                    "emit_plan_revision": revision})

    events = await _run(pid, pc.PlanDraftRequest(prompt=PROMPT))
    res = events[-1]
    assert res["type"] == "result", events
    assert [c[0] for c in calls] == ["emit_plan_core", "emit_plan_surface"]
    plan1 = res["plan"]
    assert plan1["version"] == 1 and plan1["status"] == "draft" and plan1["phase"] == "questions"
    q1 = plan1["questions"]
    assert len(_topics(q1)) == len(set(_topics(q1))) and 0 < len(q1) <= pc.ROUND1_MAX
    assert "secret_names" not in _topics(q1)
    assert plan1["body"]["requirements"]["request"] == PROMPT.strip()     # a reloaded chat shows what was asked

    answers = {q["id"]: (q.get("default") or "yes") for q in q1}
    answers[next(q["id"] for q in q1 if q["topic"] == "credentials")] = "Project secrets BANK_USER, BANK_PASS, BANK_TOTP"
    events = await _run(pid, pc.PlanDraftRequest(version=1, answers=answers))
    res = events[-1]
    assert res["type"] == "result", events
    assert calls[-1][0] == "emit_plan_revision"
    plan2 = res["plan"]
    body = plan2["body"]
    assert {s["name"] for s in body["secrets"]} == {"BANK_USER", "BANK_PASS", "BANK_TOTP"}
    q2 = plan2["questions"]
    assert "secret_names" not in _topics(q2)
    assert not set(_topics(q2)) & set(_topics(q1))                     # never the same topic twice
    assert len(q1) + len(q2) <= pc.MAX_PLAN_QUESTIONS
    assert len(body["requirements"]["answers"]) == len(q1)
    assert body["requirements"]["request"] == PROMPT.strip()               # answering keeps it
    if q2:      # a second round, then defaults: the conversation always ends after two
        assert plan2["round"] == 2 and plan2["phase"] == "questions"
        events = await _run(pid, pc.PlanDraftRequest(version=1, answers={q["id"]: "???" for q in q2}))
        plan3 = events[-1]["plan"]
        assert plan3["questions"] == [] and plan3["phase"] == "review"
        assert any("Assumed:" in a for a in plan3["body"]["requirements"]["assumptions"])
    else:
        assert plan2["phase"] == "review"


@pytest.mark.anyio
async def test_a_hand_written_body_is_saved_without_the_model(pg, monkeypatch):
    async with pg() as s:
        pid = (await ProjectService(s).create(f"plan-{uuid.uuid4().hex[:8]}")).id
    calls = _fake_llm(monkeypatch, {})
    events = await _run(pid, pc.PlanDraftRequest(body=qa_plan()))
    assert events[-1]["type"] == "result" and calls == []
    assert events[-1]["plan"]["phase"] == "review" and events[-1]["plan"]["version"] == 1
    assert not pp.has_errors(events[-1]["plan"]["issues"])


@pytest.mark.anyio
async def test_conversation_errors_are_terminal_events(pg, monkeypatch):
    async with pg() as s:
        pid = (await ProjectService(s).create(f"plan-{uuid.uuid4().hex[:8]}")).id
    from src.engine import llm_provider

    async def boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(llm_provider, "call_llm", boom)
    events = await _run(pid, pc.PlanDraftRequest(prompt=PROMPT))
    assert events[-1] == {"type": "error", "code": "llm_failed", "message": "RuntimeError: upstream down", "status": 502}
    events = await _run(pid, pc.PlanDraftRequest(version=7, answers={"q": "a"}))
    assert events[-1]["type"] == "error" and events[-1]["code"] == "plan_not_found"
