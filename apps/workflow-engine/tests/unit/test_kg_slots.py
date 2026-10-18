"""Capability graph: the typed slot extractors and answer validation (``services/kg_slots``). Pure, table-driven."""

from __future__ import annotations

import pytest

from src.services import kg_slots as S

ROLES = [
    {"key": "role:ops_analyst", "label": "Operations analyst", "aliases": ["ops analyst", "operations analyst"]},
    {"key": "role:ops_manager", "label": "Operations manager", "aliases": ["ops manager", "ops managers", "operations manager"]},
    {"key": "role:ops_head", "label": "Head of operations", "aliases": ["head of operations", "head of ops", "ops head"]},
    {"key": "role:fraud_lead", "label": "Fraud lead", "aliases": ["fraud lead", "fraud manager", "fraud managers"]},
]


@pytest.mark.parametrize("text,want", [
    ("if it is not resolved within 48 hours", "48h"),
    ("after 2 business days", "2bd"),
    ("escalate after a day", "1d"),
    ("in 30 minutes", "30m"),
    ("within 4 hrs", "4h"),
    ("after 2 weeks", "14d"),
    ("handled in four hours", "4h"),
    ("no time here", None),
    ("amounts over 500", None),
    ("send it to 5 people", None),
])
def test_duration(text, want):
    assert S.extract_duration(text) == want


@pytest.mark.parametrize("text,want", [
    ("every Monday send a report", "0 8 * * 1"),
    ("every Monday at 9am", "0 9 * * 1"),
    ("each Friday at 5:30 pm", "30 17 * * 5"),
    ("send a daily summary", "0 8 * * *"),
    ("run daily at 6", "0 6 * * *"),
    ("weekly at 8", "0 8 * * 1"),
    ("on weekdays", "0 8 * * 1-5"),
    ("every night", "0 0 * * *"),
    ("every month", "0 8 1 * *"),
    ("hourly", "0 * * * *"),
    ("every 15 minutes", "*/15 * * * *"),
    ("every 2 hours", "0 */2 * * *"),
    ("0 8 * * 1", "0 8 * * 1"),
    ("when a customer asks", None),
])
def test_cron(text, want):
    assert S.extract_cron(text) == want


def test_is_cron():
    assert S.is_cron("0 8 * * 1") and S.is_cron("*/5 * * * *") and S.is_cron("0 8 * * 1-5")
    assert not S.is_cron("daily") and not S.is_cron("0 8 * *") and not S.is_cron("a b c d e")


@pytest.mark.parametrize("text,want", [
    ("escalate it to the head of operations", ["role:ops_head"]),
    ("the Head of Ops and the ops managers", ["role:ops_head", "role:ops_manager"]),
    ("an operations analyst", ["role:ops_analyst"]),
    ("nobody in particular", []),
])
def test_find_roles(text, want):
    assert [m.key for m in S.find_roles(text, ROLES)] == want


def test_roles_do_not_overlap_inside_longer_aliases():
    # "ops managers" must not also report the shorter "ops manager"
    assert [m.key for m in S.find_roles("email the ops managers", ROLES)] == ["role:ops_manager"]


@pytest.mark.parametrize("value,want", [
    ("role:ops_head", "role:ops_head"), ("ops_head", "role:ops_head"), ("Head of operations", "role:ops_head"),
    ("the janitor", None), ("", None), ("ops manager and ops head", None),
])
def test_resolve_role(value, want):
    assert S.resolve_role(value, ROLES) == want


@pytest.mark.parametrize("text,slot,want", [
    ("escalate it to the head of operations", "target_role", "role:ops_head"),
    ("if unresolved escalate to the fraud lead", "target_role", "role:fraud_lead"),
    ("an ops manager must approve it", "approver_role", "role:ops_manager"),
    ("needs the head of operations to sign off", "approver_role", "role:ops_head"),
    ("and every Monday send the ops managers a report", "recipients_role", "role:ops_manager"),
    # the cue must point at the role: a role after "escalate" is not the approver
    ("approved by a manager; escalate after a day to the head of ops", "approver_role", None),
    ("approved by the ops manager; escalate after a day to the head of ops", "approver_role", "role:ops_manager"),
    ("approved by the ops manager; escalate after a day to the head of ops", "target_role", "role:ops_head"),
    ("send the report. The ops head owns it", "recipients_role", None),   # a sentence break stops the cue
    ("nothing about people", "approver_role", None),
])
def test_extract_role_follows_the_cue(text, slot, want):
    assert S.extract_role(text, slot, ROLES) == want


def test_extract_role_skips_roles_already_used():
    t = "the ops manager approves and then send the ops manager and the head of ops a note"
    assert S.extract_role(t, "recipients_role", ROLES, exclude=["role:ops_manager"]) == "role:ops_head"


@pytest.mark.parametrize("text,want", [
    ("each one gets an owner based on its category", "category"),
    ("assign an analyst by region", "region"),
    ("routed per product", "product"),
    ("by the way", None),
    ("by 5", None),
])
def test_field(text, want):
    assert S.extract_field(text) == want


def test_choice_needs_exactly_one_hit():
    assert S.extract_choice("send it from a webhook", ["form", "webhook", "chat"]) == "webhook"
    assert S.extract_choice("a form or a chat", ["form", "webhook", "chat"]) is None
    assert S.extract_choice("nothing", ["form", "webhook"]) is None


# ── answers ──

def slot(**kw):
    return {"name": "x", "type": "text", **kw}


@pytest.mark.parametrize("stype,value,want", [
    ("role", "Head of operations", "role:ops_head"),
    ("role", {"new_role": "Branch lead"}, {"new_role": "Branch lead"}),
    ("duration", "48 hours", "48h"), ("duration", "2d", "2d"),
    ("cron", "every Monday", "0 8 * * 1"), ("cron", "0 6 * * *", "0 6 * * *"),
    ("field", "Customer Region", "customer_region"),
    ("text", "  exception  ", "exception"),
])
def test_validate_value_canonical_forms(stype, value, want):
    assert S.validate_value(slot(type=stype), value, ROLES) == want


@pytest.mark.parametrize("stype,value", [
    ("role", "the janitor"), ("duration", "soon"), ("cron", "whenever"), ("field", "!!"), ("text", ""), ("text", {"a": 1}),
])
def test_validate_value_refuses_what_does_not_fit(stype, value):
    with pytest.raises(S.AnswerInvalid):
        S.validate_value(slot(type=stype), value, ROLES)


def test_choice_and_api_capability():
    ch = slot(type="choice", options=["form", "webhook"])
    assert S.validate_value(ch, "WEBHOOK", ROLES) == "webhook"
    with pytest.raises(S.AnswerInvalid):
        S.validate_value(ch, "sms", ROLES)
    cap = slot(type="api_capability")
    assert S.validate_value(cap, "api:notify.sendMessage", ROLES, allowed_caps={"api:notify.sendMessage"}) == "api:notify.sendMessage"
    with pytest.raises(S.AnswerInvalid):
        S.validate_value(cap, "api:other.op", ROLES, allowed_caps={"api:notify.sendMessage"})
    with pytest.raises(S.AnswerInvalid):
        S.validate_value(cap, "email please", ROLES)


def test_multiple_slots_take_lists_and_role_maps():
    fields = slot(type="field", multiple=True)
    assert S.validate_value(fields, "category, amount", ROLES) == ["category", "amount"]
    owners = slot(type="role", multiple=True)
    assert S.validate_value(owners, {"emea": "ops analyst", "apac": "role:ops_manager"}, ROLES) == {"emea": "role:ops_analyst", "apac": "role:ops_manager"}
    assert S.validate_value(owners, ["ops analyst"], ROLES) == ["role:ops_analyst"]
    with pytest.raises(S.AnswerInvalid):
        S.validate_value(owners, {"emea": "the janitor"}, ROLES)
    with pytest.raises(S.AnswerInvalid):
        S.validate_value(owners, [], ROLES)


# ── stated thresholds, generic job titles and unseeded roles (a fact the request states is never asked) ──

def seed_roles():
    from src.services import kg_understand

    return kg_understand.seed_vocabulary()[1]


@pytest.mark.parametrize("text,want", [
    ("Amounts over 500 are marked for review", "over 500"),
    ("anything above $5,000 needs a look", "over 5000"),
    ("more than 1000", "over 1000"),
    ("when it exceeds 10k", "over 10000"),
    ("it exceeds 1.5 million", "over 1500000"),
    ("at least 500", "at least 500"),
    ("refunds greater than 250.50", "over 250.5"),
    ("under 20 euros is paid at once", "under 20"),
    ("a total below £300", "under 300"),
    ("no more than 75", "at most 75"),
    ("more than 2 days late", None),          # a duration, not an amount
    ("over 90% of cases", None),
    ("send larger ones to a manager", None),   # no number: the request does not say
    ("exceeds the limit", None),
    ("", None),
])
def test_extract_threshold(text, want):
    assert S.extract_threshold(text) == want


@pytest.mark.parametrize("text,want", [
    ("send larger refunds to a manager for approval", "role:ops_manager"),
    ("needs a manager", "role:ops_manager"),
    ("the manager approves it", "role:ops_manager"),
    ("approved by the team lead", "role:ops_manager"),
    ("a supervisor must sign off", "role:ops_manager"),
    ("the head of operations approves", "role:ops_head"),
    ("approved by the fraud manager", "role:fraud_lead"),         # the longer, seeded alias still wins
    ("the finance manager has to approve", "role:finance_controller"),
    ("needs approval from an ops manager", "role:ops_manager"),
])
def test_approver_role_resolves_generic_titles_to_the_closest_seeded_role(text, want):
    assert S.extract_role(text, "approver_role", seed_roles()) == want


@pytest.mark.parametrize("text,slot,want", [
    ("approved by the risk committee", "approver_role", "Risk committee"),
    ("needs sign-off from a senior underwriter", "approver_role", "Senior underwriter"),
    ("a relationship manager must approve it", "approver_role", "Relationship manager"),   # a qualified 'manager' is not the seeded one
    ("escalate it to the regional director", "target_role", "Regional director"),
    ("send the report to the treasury team", "recipients_role", "Treasury team"),
])
def test_a_named_role_nobody_seeded_is_a_new_role(text, slot, want):
    assert S.extract_role(text, slot, seed_roles()) is None
    assert S.extract_new_role(text, slot, seed_roles()) == want


@pytest.mark.parametrize("text,slot", [
    ("needs approval", "approver_role"),
    ("every refund must be approved", "approver_role"),
    ("approved automatically", "approver_role"),
    ("send it to the team", "recipients_role"),                       # 'team' alone names nobody
    ("the customer is told", "recipients_role"),
])
def test_no_role_stated_means_no_role(text, slot):
    assert S.extract_role(text, slot, seed_roles()) is None
    assert S.extract_new_role(text, slot, seed_roles()) is None


def test_an_unseeded_role_is_not_taken_from_inside_a_seeded_one():
    assert S.extract_new_role("the ops manager approves", "approver_role", seed_roles()) is None
