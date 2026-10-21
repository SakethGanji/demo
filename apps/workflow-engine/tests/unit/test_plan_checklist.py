"""The deployment checklist stays quiet about what the plan cannot need (retries without integrations, environments when there is
one), and offers the preview users as seeded roles when the plan has a role table."""

from __future__ import annotations

from src.services import plan_checklist as pcl
from src.services import plan_clarify as pc
from src.services import project_plan as pp

PROMPT = "Build an expense approval tool: staff submit expenses, managers approve them. Call jobs and email when it fails."


def plan(tables=("expenses",), envs=(), secrets=(), integrations=()):
    return pp.normalize({
        "v": 1, "title": "Expenses", "summary": "expense approvals",
        "data": {"tables": [{"name": n, "columns": [{"name": "name", "type": "string"}]} for n in tables]},
        "environments": [dict(e) for e in envs], "secrets": [dict(s) for s in secrets], "integrations": [dict(i) for i in integrations]})


def topics(p, envs=None):
    qs = pc.first_round_questions(PROMPT, p, [], {"environments": envs or [e["name"] for e in p["environments"]], "tables": []})
    return [q["topic"] for q in qs], qs


def test_no_retry_or_environment_questions_without_integrations_and_with_one_environment():
    t, _ = topics(plan(envs=[{"name": "dev", "base_url": ""}]))
    assert "failure" not in t and "environments" not in t


def test_retry_question_returns_with_a_secret_and_environment_question_with_differing_environments():
    p = plan(secrets=[{"name": "JIRA_TOKEN", "purpose": "x", "allowed_hosts": ["example.com"]}],
             envs=[{"name": "dev", "base_url": "https://dev.example.com"}, {"name": "prod", "base_url": "https://prod.example.com"}])
    t, _ = topics(p)
    assert "failure" in t and "environments" in t
    same = plan(envs=[{"name": "dev"}, {"name": "prod"}])        # two environments, nothing differs
    assert not pcl.wants_environment_question(same, ["dev", "prod"])


def test_a_role_table_offers_the_preview_users_as_the_suggested_roles_answer():
    p = plan(tables=("expenses", "approvers"))
    item = next(q for q in pcl.deployment_checklist(p, PROMPT) if q["topic"] == "roles")
    assert pcl.PREVIEW_USERS_OPTION in item["options"] and pcl.PREVIEW_USERS_OPTION in item["suggested"] and item["other"]
    plain = next(q for q in pcl.deployment_checklist(plan(), PROMPT) if q["topic"] == "roles")
    assert pcl.PREVIEW_USERS_OPTION not in plain["options"]
    out, needs_model = pcl.apply_checklist_answers(p, [item], {item["id"]: pcl.PREVIEW_USERS_OPTION})
    assert "alice" in out["roles_note"] and "bob" in out["roles_note"] and not needs_model


SMALL = "Build a small expense app: staff submit expenses, managers approve them, finance sees totals."


def small_topics(prompt):
    p = plan(tables=("expenses", "expense_runs"))
    qs = pc.first_round_questions(prompt, p, [], {"environments": [], "tables": []})
    return [q["topic"] for q in qs]


def test_small_app_skips_paging_retention_and_import_items():
    t = small_topics(SMALL)
    assert "limits" not in t and "retention" not in t and "import" not in t
    assert "roles" in t and "reports" in t
    assert len(t) <= 10


def test_retention_import_and_paging_return_when_the_request_mentions_them():
    t = small_topics(SMALL + " Keep audit history for 7 years for compliance, import last year's expenses from a CSV, "
                     "and the table will have thousands of rows.")
    assert {"retention", "import", "limits"} <= set(t)
