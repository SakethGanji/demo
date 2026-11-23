"""Capability graph: Understand (``services/kg_understand``) - the recognition gate, the optional LLM call, slots, questions, answers.

The gate is a real bar (owner decision: do not lower it): over ``tests/fixtures/knowledge/understand_cases.json`` (the 15 eval requests plus the
two demo requests, labelled by hand from the request text) the keyword recogniser must reach precision >= 0.85 and recall >= 0.80.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.services import kg_understand as U
from tests.unit._kg_support import make_slice, node, pattern, edge, subworkflow

CASES = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "knowledge" / "understand_cases.json").read_text())["cases"]
EVAL_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ai_builder" / "eval"
HOLDOUT = {json.loads(p.read_text())["id"] for p in EVAL_DIR.glob("*.json") if json.loads(p.read_text()).get("holdout")}
DEMO_A = next(c for c in CASES if c["id"] == "demo_team_a_exceptions_app")["request"]


def run(request, *, slice=None, answers=None, llm=None, use_llm=False, extras=None):
    ctx = U.UnderstandContext("proj_a", "t1", slice or make_slice(), ask_model=llm, extras=extras or {})
    return asyncio.run(U.understand(ctx, request, answers=answers, use_llm=use_llm))


def confirmed(u):
    return {c["key"] for c in u["concepts"] if c["confirmed"]}


def score(cases):
    tp = fp = fn = 0
    for c in cases:
        got, want = confirmed(run(c["request"])), set(c["concepts"])
        tp, fp, fn = tp + len(got & want), fp + len(got - want), fn + len(want - got)
    return tp / (tp + fp), tp / (tp + fn)


# ── the recognition gate ──

def test_recognition_gate_precision_and_recall():
    p, r = score(CASES)
    assert p >= 0.85, f"precision {p:.3f}"
    assert r >= 0.80, f"recall {r:.3f}"


def test_recognition_gate_holds_on_the_holdout_requests_too():
    held = [c for c in CASES if c["id"] in HOLDOUT]
    assert len(held) == 5
    p, r = score(held)
    assert p >= 0.85 and r >= 0.80, (p, r)


@pytest.mark.parametrize("case", [c for c in CASES if c["slots"]], ids=lambda c: c["id"])
def test_stated_slots_are_read_from_the_request(case):
    u = run(case["request"])
    for sid, want in case["slots"].items():
        assert U.slot_value(u, sid) == want, (sid, u["slots"])
        assert u["slots"][sid]["from"] == "request"


def test_ordinary_uses_of_alias_words_are_not_concepts():
    u = run("Show the customer a confirmation on screen. In every other case do nothing; we schedule the payment later.")
    assert not confirmed(u) & {"concept:screening", "concept:exception_case", "concept:schedule"}


def test_implied_concepts_are_suggested_not_confirmed():
    u = run("If nobody handles it, escalate it.")
    by = {c["key"]: c for c in u["concepts"]}
    assert by["concept:escalation"]["confirmed"] and by["concept:escalation"]["source"] == "lexical"
    for k in ("concept:sla", "concept:assignment"):   # Escalation requires them
        assert by[k]["source"] == "implied" and not by[k]["confirmed"] and by[k]["score"] < U.CONFIRM_SCORE


def test_concepts_off_switches_a_concept_off():
    u = run("Refunds need a manager to approve.", answers={"concepts_off": ["concept:refund"]})
    assert "concept:refund" not in confirmed(u) and "concept:approval" in confirmed(u)


def test_empty_request_and_no_hits_are_a_well_formed_empty_understanding():
    u = run("")
    assert u["v"] == 1 and u["concepts"] == [] and u["questions"] == [] and u["summary"]["understood"] == []
    assert run("blue sky")["questions"] == []


# ── questions: D3 ──

def test_only_required_empty_slots_of_confirmed_concepts_become_questions():
    u = run("Every approval needs a sign off before we continue.")
    ids = [q["id"] for q in u["questions"]]
    assert ids == ["q_approval.approver_role"]
    q = u["questions"][0]
    assert q["required"] and q["answer"] is None and q["kind"] == "role" and "role:ops_head" in q["options"]
    assert q["concept"] == "concept:approval" and q["slot"] == "approver_role" and q["text"]
    # an optional slot (approval.due_in) is never asked: it is an alias of sla.due_in and there is no default, so no assumption either
    assert all(q["required"] for q in u["questions"] if q["id"] != "q_shape")


def test_a_slot_the_request_states_is_not_asked():
    u = run("An ops manager must approve every refund over 500.")
    assert "q_approval.approver_role" not in [q["id"] for q in u["questions"]]
    assert U.slot_value(u, "approval.approver_role") == "role:ops_manager"


def test_at_most_four_questions_and_the_rest_become_assumptions():
    req = ("Log an incident, route it, approve it, review it, escalate it, report on it, notify the team, and screen it against sanctions, "
           "a dispute and a refund and a threshold.")
    u = run(req)
    qs = [q for q in u["questions"] if q["required"]]
    assert len(qs) == U.MAX_QUESTIONS
    assert any("is not set" in a for a in u["assumptions"])


def test_questions_follow_concept_score_order():
    u = run("Notify the customer. Dispute handling. An approval please.")
    scores = {c["key"]: c["score"] for c in u["concepts"]}
    order = [scores[q["concept"]] for q in u["questions"] if q["concept"]]
    assert order == sorted(order, reverse=True)


def test_alias_slots_are_asked_once_through_their_original():
    # Escalation.after is the same fact as SLA.due_in: one question, id of the original, when nothing states the time
    u = run("Escalate unresolved items to the head of operations. Also state the deadline.")
    ids = [q["id"] for q in u["questions"]]
    assert ids.count("q_sla.due_in") == 1 and not any(i.startswith("q_escalation.after") for i in ids)
    q = next(q for q in u["questions"] if q["id"] == "q_sla.due_in")
    assert q["kind"] == "duration" and q["options"] == ["4h", "24h", "48h"]


def test_a_stated_duration_fills_the_original_and_both_aliases():
    u = run("Escalate to the head of operations if unresolved within 24 hours.")
    assert U.slot_value(u, "sla.due_in") == "24h"
    assert "q_sla.due_in" not in [q["id"] for q in u["questions"]]


def test_defaults_fill_required_slots_and_show_as_assumptions():
    u = run("Anyone can log an exception on a form.")
    assert u["slots"]["intake.channel"] == {"value": "form", "from": "request"} or u["slots"]["intake.channel"]["from"] in ("request", "default")
    assert U.slot_value(u, "exception_case.entity") == "exception"
    assert not any(q["slot"] == "entity" for q in u["questions"])


def test_notification_channel_is_sendemail_when_no_api_implements_it():
    u = run("Notify the customer when it is done.")
    assert U.slot_value(u, "notification.channel") == "node:SendEmail"
    assert not any(q["slot"] == "channel" and q["concept"] == "concept:notification" for q in u["questions"])


def test_notification_channel_prefers_an_api_that_implements_it():
    api = node("api:notify.sendMessage", "capability", "sendMessage", subkind="api_operation", scope="project", project_id="proj_a", source="extracted")
    sl2 = make_slice(extra=[api], extra_edges=[(api, "implements", "concept:notification")])
    u = run("Notify the customer when it is done.", slice=sl2)
    assert U.slot_value(u, "notification.channel") == "api:notify.sendMessage"
    assert u["retrieval"]["apis"] == [{"key": "api:notify.sendMessage", "why": "implements Notification"}]


def test_without_any_graph_the_reviewed_seed_file_is_used():
    from src.repositories.kg_repository import GraphSlice

    u = run("Refunds over 500 need a manager to approve.", slice=GraphSlice("p", None, {}, []))
    assert {"concept:refund", "concept:approval"} <= confirmed(u)


def test_the_app_question_is_asked_for_an_app_and_never_blocks():
    u = run(DEMO_A)
    assert u["shape"] == "app"
    q = next(q for q in u["questions"] if q["id"] == "q_shape")
    assert q["required"] is False and q["options"] == ["app", "workflow"] and "parts" in q["text"]
    assert all(q["id"] != "q_shape" for q in U.open_questions(u))
    assert run(DEMO_A, answers={"q_shape": "workflow"})["shape"] == "workflow"
    assert run("Refund over 500 needs a manager.")["shape"] == "workflow"


def test_demo_team_a_questions_are_deterministic():
    u = run(DEMO_A)
    assert [q["id"] for q in u["questions"] if q["required"]] == ["q_assignment.owners", "q_exception_case.fields"]
    assert u["summary"]["questions"] == len(u["questions"])
    assert {"Escalation", "Report", "Assignment"} <= set(u["summary"]["understood"])
    assert u["warnings"] == []


# ── answers ──

def test_answers_win_and_stay_in_the_question_list():
    first = run("Every approval needs a sign off.")
    assert [q["id"] for q in U.open_questions(first)] == ["q_approval.approver_role"]
    u = run("Every approval needs a sign off.", answers={"q_approval.approver_role": "ops head"})
    assert U.slot_value(u, "approval.approver_role") == "role:ops_head" and u["slots"]["approval.approver_role"]["from"] == "answer"
    q = next(q for q in u["questions"] if q["id"] == "q_approval.approver_role")
    assert q["answer"] == "role:ops_head" and U.open_questions(u) == []
    assert u["summary"]["questions"] == first["summary"]["questions"]   # the count of questions asked is stable


def test_an_answer_beats_what_the_request_says():
    u = run("Escalate to the head of operations after 48 hours.", answers={"escalation.target_role": "role:fraud_lead", "q_sla.due_in": "4h"})
    assert U.slot_value(u, "escalation.target_role") == "role:fraud_lead"
    assert U.slot_value(u, "sla.due_in") == "4h"


def test_answers_to_alias_ids_land_on_the_original():
    u = run("Escalate to the head of operations.", answers={"escalation.after": "2 days"})
    assert U.slot_value(u, "sla.due_in") == "2d"


def test_invalid_or_unknown_answers_are_ignored_with_a_warning_in_understand():
    u = run("Every approval needs a sign off.", answers={"q_approval.approver_role": "the janitor", "q_nope.x": "1"})
    assert U.open_questions(u) and len(u["warnings"]) == 2


def test_check_answers_validates_for_the_route():
    prev = run("Every approval needs a sign off. Escalate if not resolved.")
    sl = make_slice()
    out = U.check_answers(prev, {"approval.approver_role": "ops head", "q_escalation.target_role": "role:fraud_lead", "sla.due_in": "48h"}, sl)
    assert out["q_approval.approver_role"] == "role:ops_head" and out["q_sla.due_in"] == "48h"
    with pytest.raises(Exception) as bad:
        U.check_answers(prev, {"q_approval.approver_role": "the janitor"}, sl)
    assert bad.value.qid == "q_approval.approver_role"
    with pytest.raises(Exception) as unknown:
        U.check_answers(prev, {"q_nope": "x"}, sl)
    assert unknown.value.qid == "q_nope"
    with pytest.raises(Exception) as missing:
        U.check_answers(prev, {"q_approval.approver_role": "ops head"}, sl)   # the other required questions are still open
    assert "needs an answer" in str(missing.value)


# ── the one optional model call ──

def llm_reply(**kw):
    async def ask(payload):
        ask.payload = payload
        return kw
    return ask


def test_the_llm_may_only_name_concepts_of_the_vocabulary():
    ask = llm_reply(concepts=[{"key": "concept:audit_log", "evidence": "log everything"}, {"key": "concept:made_up", "evidence": "x"}, {"nope": 1}])
    u = run("Blue sky thinking.", llm=ask, use_llm=True)
    assert confirmed(u) == {"concept:audit_log"}
    c = u["concepts"][0]
    assert c["source"] == "llm" and c["evidence"] == "log everything"
    vocab = {v["key"] for v in ask.payload["concept_vocabulary"]}
    assert len(vocab) <= U.MAX_VOCABULARY and "concept:made_up" not in vocab
    assert set(ask.payload) == {"untrusted_request", "concept_vocabulary", "roles", "slot_types"}


def test_llm_slot_values_are_revalidated_and_lose_to_the_request():
    ask = llm_reply(concepts=[{"key": "concept:approval"}],
                    slots={"approval.approver_role": "the janitor", "sla.due_in": "soon", "escalation.target_role": "ops manager", "report.schedule": "daily"})
    u = run("An approval is needed. Escalate it.", llm=ask, use_llm=True)
    # an unknown role becomes a new role, an unparsable duration is dropped, a known alias resolves
    assert U.slot_value(u, "approval.approver_role") == {"new_role": "the janitor"}
    assert U.slot_value(u, "sla.due_in") is None
    assert u["slots"]["escalation.target_role"] == {"value": "role:ops_manager", "from": "llm"}
    # the request states 24 hours: the LLM's value cannot replace it
    ask2 = llm_reply(concepts=[], slots={"sla.due_in": "4h"})
    u2 = run("Escalate within 24 hours.", llm=ask2, use_llm=True)
    assert u2["slots"]["sla.due_in"] == {"value": "24h", "from": "request"}


def test_an_llm_slot_fills_what_nothing_else_does_and_an_answer_still_wins():
    ask = llm_reply(concepts=[], slots={"approval.approver_role": "ops head"})
    assert run("Every approval needs a sign off.", llm=ask, use_llm=True)["slots"]["approval.approver_role"] == {"value": "role:ops_head", "from": "llm"}
    u = run("Every approval needs a sign off.", llm=ask, use_llm=True, answers={"q_approval.approver_role": "fraud lead"})
    assert u["slots"]["approval.approver_role"] == {"value": "role:fraud_lead", "from": "answer"}


def test_llm_failure_degrades_to_keywords_with_a_warning():
    async def boom(payload):
        raise RuntimeError("down")

    u = run("Refunds over 500 need a manager to approve.", llm=boom, use_llm=True)
    assert {"concept:refund", "concept:approval"} <= confirmed(u) and len(u["warnings"]) == 1
    async def junk(payload):
        return "not an object"
    assert len(run("Refund it.", llm=junk, use_llm=True)["warnings"]) == 1


def test_no_model_call_without_use_llm_or_a_callable():
    ask = llm_reply(concepts=[{"key": "concept:audit_log"}])
    run("x", llm=ask, use_llm=False)
    assert not hasattr(ask, "payload")
    assert run("x", llm=None, use_llm=True)["warnings"] == []


def test_the_answers_rerun_keeps_what_the_first_llm_call_found_without_a_second_call():
    ask = llm_reply(concepts=[{"key": "concept:audit_log", "evidence": "log it"}], slots={"approval.approver_role": "ops head"}, shape="app")
    first = run("Every approval needs a sign off.", llm=ask, use_llm=True)
    again = run("Every approval needs a sign off.", llm=None, use_llm=False, extras={"prior": first}, answers={"q_nope": 1})
    assert "concept:audit_log" in confirmed(again)
    assert U.slot_value(again, "approval.approver_role") == "role:ops_head" and again["slots"]["approval.approver_role"]["from"] == "llm"


# ── retrieval inside understand: patterns ──

def adapt_slice(**pattern_kw):
    keys = ["concept:escalation", "concept:report", "concept:assignment"]
    pat = pattern("pattern:abc123", "Case management app", keys, shape="app", slots={
        "sla.due_in": "48h", "intake.channel": "webhook", "escalation.target_role": "role:ops_head", "report.schedule": "0 8 * * 1"}, **pattern_kw)
    return make_slice(extra=[pat], extra_edges=[(pat, "implements", k) for k in keys])


def test_a_matching_pattern_is_the_adapt_seed_and_lowers_the_questions():
    req = "Build an app for card fraud alerts: log an alert, assign an analyst by region, escalate if not handled, and send fraud managers a daily summary."
    plain = run(req)
    u = run(req, slice=adapt_slice())
    assert u["adapt_from"] == {"pattern": "pattern:abc123", "label": "Case management app", "fit": u["adapt_from"]["fit"]}
    assert u["retrieval"]["patterns"][0]["adapt"] is True
    # facts a person must state are never copied from another team's pattern
    assert U.slot_value(u, "escalation.target_role") is None
    assert "q_escalation.target_role" in [q["id"] for q in u["questions"]]
    # ... nor its durations or schedules; other facts (here the intake channel) are taken over as editable assumptions
    assert U.slot_value(u, "sla.due_in") is None and "q_sla.due_in" in [q["id"] for q in u["questions"]]
    assert U.slot_value(u, "report.schedule") == "0 8 * * *"   # the request says daily
    assert u["slots"]["intake.channel"] == {"value": "webhook", "from": "pattern"}
    assert any("taken from a similar app" in a for a in u["assumptions"])
    # the pattern replaces the app-or-workflow question
    assert not any(q["id"] == "q_shape" for q in u["questions"]) and any(q["id"] == "q_shape" for q in plain["questions"])


# ── questions only for what the request does not say ──

REQ_19_THRESHOLD = "Amounts over 500 are marked for review, the rest are approved automatically, and the caller always gets the status back."
REQ_19_REFUND = "Classify refund requests, pay small refunds at once and send larger refunds to a manager for approval."
REQ_99_SHOWCASE = "Classify refund requests; pay small refunds at once, send larger ones to a manager, always reply with the status."


@pytest.mark.parametrize("request_text", [
    REQ_19_THRESHOLD, REQ_19_REFUND, REQ_99_SHOWCASE,
    REQ_19_THRESHOLD + " (a1b2c3d4)", REQ_19_REFUND + " (a1b2c3d4)", REQ_99_SHOWCASE + " (a1b2c3d4)",   # the e2e suffixes each request with a token
])
def test_the_e2e_requests_ask_nothing_they_already_state(request_text):
    u = run(request_text)
    assert u["questions"] == [], u["questions"]
    assert U.open_questions(u) == []


def test_over_500_fills_the_threshold_from_the_request():
    u = run(REQ_19_THRESHOLD)
    assert confirmed(u) == {"concept:threshold_check", "concept:review"}     # "approved automatically" is a rule, not an Approval
    assert u["slots"]["threshold_check.limit"] == {"value": "over 500", "from": "request"}


def test_a_manager_for_approval_fills_the_approver_from_the_request():
    u = run(REQ_19_REFUND)
    assert "concept:approval" in confirmed(u)
    assert u["slots"]["approval.approver_role"] == {"value": "role:ops_manager", "from": "request"}


def test_the_showcase_request_understands_refunds_without_a_question():
    u = run(REQ_99_SHOWCASE)
    assert u["summary"]["understood"] == ["Refund"] and u["summary"]["questions"] == 0


@pytest.mark.parametrize("request_text,slot", [
    ("Every expense claim needs approval.", "q_approval.approver_role"),
    ("A refund needs approval before it is paid.", "q_approval.approver_role"),
    ("Flag transfers above the limit.", "q_threshold_check.limit"),     # a limit is mentioned but no amount
])
def test_a_request_that_does_not_say_it_still_asks(request_text, slot):
    u = run(request_text)
    assert slot in [q["id"] for q in u["questions"]], u["questions"]
    assert all(q["answer"] is None for q in u["questions"])


def test_a_named_role_nobody_seeded_is_an_assumption_not_a_question():
    u = run("A refund needs approval from the risk committee.")
    assert "q_approval.approver_role" not in [q["id"] for q in u["questions"]]
    assert u["slots"]["approval.approver_role"] == {"value": {"new_role": "Risk committee"}, "from": "request"}


def test_approval_that_is_automatic_is_not_an_approval_concept():
    for text in ("Small refunds are approved automatically.", "Auto-approve anything under 100.", "It is approved at once if the score is low."):
        assert "concept:approval" not in confirmed(run(text)), text
    assert "concept:approval" in confirmed(run("A manager must approve it."))


@pytest.mark.parametrize("script,request_text", [
    ("showcase", REQ_99_SHOWCASE), ("first_try", REQ_19_THRESHOLD), ("repairable", REQ_19_THRESHOLD),
    ("unrepairable", REQ_19_THRESHOLD), ("test_change_attempt", REQ_19_THRESHOLD),
])
def test_the_e2e_fake_llm_understand_rules_give_a_clean_understanding(script, request_text):
    path = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder" / f"{script}.json"
    rules = json.loads(path.read_text())["rules"]
    rule = next(r for r in rules if r["when"].get("system_matches") == "^WF_AI:UNDERSTAND\\n")
    assert rules.index(rule) == len(rules) - 1 and "nth" not in rule["when"]    # last: no counter-based rule can shift

    async def ask(payload):
        return json.loads(rule["respond"]["text"])

    u = run(request_text, llm=ask, use_llm=True)
    assert u["warnings"] == [] and u["questions"] == [] and u["summary"]["understood"]


def test_a_dataset_profile_report_request_recognises_the_data_concepts():
    concepts, _ = U.seed_vocabulary()
    found = U.recognise("profile a dataset and email a report", concepts)
    assert {"concept:data_profiling", "concept:dataset", "concept:report"} <= set(found)
