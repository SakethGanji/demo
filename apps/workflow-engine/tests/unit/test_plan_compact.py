"""The compact plan the planner writes and its expansion to the full plan body, the deployment checklist and the
one-round answer flow (suggested picks apply to the plan without a model call). The model is always a fake."""

from __future__ import annotations

import json
import uuid

import pytest

from src.services import plan_checklist as pcl
from src.services import plan_clarify as pc
from src.services import plan_compact
from src.services import project_plan as pp
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.plan_fixture import qa_plan
from tests.unit.test_plan_clarify import PROMPT, _fake_llm, _run

ENTITIES = ["customer", "contact", "product", "quote", "order", "invoice", "payment", "shipment", "ticket", "task", "document",
            "campaign"]


def big_compact() -> tuple[dict, dict]:
    """A 22-feature app in the planner's compact form: 12 tables, ~60 routes, 3 apps."""
    tables, groups, mods = [], [], [{"key": "common", "kind": "shared", "purpose": "audit, paging, errors",
                                     "helpers": ["audit(entity, id, actor, action) write an audit row",
                                                 "page(query, limit, cursor) cursor paging", "fail(status, code) error body"]}]
    for i, e in enumerate(ENTITIES):
        mod = f"m_{e}"
        cols = [f"name:string req idx", f"notes:string", f"amount:number =0", f"status:string req idx"]
        if i:
            cols.append(f"{ENTITIES[i - 1]}_id:string ref:{ENTITIES[i - 1]}s.id idx")
        tables.append({"name": f"{e}s", "owner": mod, "cols": cols,
                       "sm": f"status initial=draft final=done,rejected; draft>review POST /{e}s/{{id}}/submit @member; "
                             f"review>done POST /{e}s/{{id}}/approve @admin; review>rejected POST /{e}s/{{id}}/reject @admin"})
        mods.append({"key": mod, "kind": "routes", "purpose": f"{e} records and approval"})
        groups.append({"module": mod, "routes": [
            f"GET /{e}s | list {e}s | out: id,name,status", f"POST /{e}s | create {e}", f"GET /{e}s/{{id}} | one {e}",
            f"PUT /{e}s/{{id}} | edit {e}", f"POST /{e}s/{{id}}/submit | send for review",
            f"POST /{e}s/{{id}}/approve | approve", f"POST /{e}s/{{id}}/reject | reject",
            f"GET /{e}s/summary | counts by status | out: status,count"]})
    apps = [{"key": f"app{n}", "name": f"App {n}", "users": "",
             "screens": [f"{e.title()} list | browse {e}s | GET /{e}s, GET /{e}s/{{id}}" for e in ENTITIES[n * 4:n * 4 + 4]]
             + [f"{ENTITIES[n * 4].title()} review | approve or reject | POST /{ENTITIES[n * 4]}s/{{id}}/approve"]}
            for n in range(3)]
    core = {"title": "Ops suite", "summary": "Records with approval across twelve areas.", "roles_note": "",
            "tables": tables, "modules": mods, "acceptance": ["A member can submit a draft :: route:POST /customers/{id}/submit"]}
    return core, {"route_groups": groups, "apps": apps}


def test_compact_plan_expands_to_a_valid_full_plan_within_budget():
    core, surface = big_compact()
    compact = len(json.dumps({**core, **surface}))
    body = pc.draft_body(core, surface)
    full = len(json.dumps(body))
    errors = [i for i in pp.validate_plan(body) if i["severity"] == "error"]
    assert not errors, errors[:3]
    assert len(body["routes"]) == 12 * 8 and len(body["data"]["tables"]) == 12
    assert compact <= 20_000, compact
    assert full > compact * 2                          # code wrote the rest
    t = body["data"]["tables"][0]
    assert t["state_machine"]["initial"] == "draft" and t["state_machine"]["final"] == ["done", "rejected"]
    assert next(c for c in t["columns"] if c["name"] == "status")["enum"] == ["draft", "review", "done", "rejected"]
    assert body["build_order"][0] == "common" and body["build_order"][-1].startswith("app:")
    post = next(r for r in body["routes"] if r["key"] == "POST /customers")
    assert post["tables"]["writes"] == ["customers"] and post["idempotency"] and post["errors"]
    assert {f["name"] for f in post["request"]["fields"]} >= {"name", "status"}
    lst = next(r for r in body["routes"] if r["key"] == "GET /customers")
    assert lst["pagination"] is True and [f["name"] for f in lst["response"]["fields"]] == ["id", "name", "status"]
    contact = next(m for m in body["modules"] if m["key"] == "m_contact")
    assert contact["depends_on"] == ["m_customer"] or "m_customer" in contact["reads"] or contact["owns"] == ["contacts"]


def test_the_full_form_passes_through_expand_unchanged():
    plan = qa_plan()
    assert pp.normalize(plan_compact.expand(plan)) == pp.normalize(plan)
    assert plan_compact.expand(plan_compact.expand(plan)) == plan_compact.expand(plan)


def test_a_revision_in_compact_form_replaces_only_its_sections():
    body = pp.normalize(qa_plan())
    rev = {"tables": [{"name": "audit_log", "owner": "common", "cols": ["action:string req", "who:string"]}]}
    out = pc.merge_revision(body, rev)
    assert [t["name"] for t in out["data"]["tables"]] == ["audit_log"]
    assert out["routes"] == body["routes"] and out["modules"] == body["modules"]


def test_checklist_items_have_options_suggestions_and_other():
    plan = pp.normalize(qa_plan())
    plan["roles_note"] = ""
    items = pcl.deployment_checklist(plan, PROMPT + " The tables will hold thousands of rows.")   # limits only when size matters
    topics = [i["topic"] for i in items]
    assert {"roles", "seed", "limits", "reports"} <= set(topics) and len(topics) == len(set(topics))
    for i in items:
        assert i["source"] == "checklist" and i["options"] and i["other"] is True
        assert i["suggested"] and set(i["suggested"]) <= set(i["options"])
        assert i["default"] == pcl.SEP.join(i["suggested"])
        assert i["group"]
    assert next(i for i in items if i["topic"] == "roles")["multi"] is True


def test_suggested_answers_apply_to_the_plan_deterministically():
    plan = pp.normalize(qa_plan())
    plan["roles_note"] = ""
    items = pcl.deployment_checklist(plan, PROMPT + " The tables will hold thousands of rows.")   # limits only when size matters
    answers = pcl.suggested_answers(items)
    out, needs_model = pcl.apply_checklist_answers(plan, items, answers)
    assert needs_model is False
    assert out["roles_note"].startswith("Roles: ") and out["nfr"]["seed_data"] == "Realistic sample data"
    assert out["nfr"]["page_size"] == 25 and out["nfr"]["reports"]
    answers["req_roles"] = "Admin; Auditor"                                  # a free-text pick needs the model
    _, needs_model = pcl.apply_checklist_answers(plan, items, answers)
    assert needs_model is True


def test_a_channel_with_a_credential_opens_one_follow_up_gap():
    plan = pp.normalize(qa_plan())
    items = [i for i in pcl.deployment_checklist(plan, PROMPT) if i["topic"] == "notifications"]
    out, _ = pcl.apply_checklist_answers(plan, items, {"req_notifications": "Slack message; Webhook"})
    names = {s["name"]: s for s in out["secrets"]}
    assert names["SLACK_WEBHOOK_URL"]["allowed_hosts"] == ["hooks.slack.com"]
    assert names["NOTIFY_WEBHOOK_URL"]["allowed_hosts"] == []
    qs = pc.plan_gap_questions(pp.validate_plan(out), out)
    assert [q["topic"] for q in qs if q["topic"] == "secret_hosts"] == ["secret_hosts"]


@pytest.mark.anyio
async def test_one_round_accepting_the_suggestions_needs_no_model_call_and_records_timing(pg, monkeypatch):
    async with pg() as s:
        pid = (await ProjectService(s).create(f"plan-{uuid.uuid4().hex[:8]}")).id
    core, surface = big_compact()
    calls = _fake_llm(monkeypatch, {"emit_plan_core": lambda: {**core, "checklist": [
        {"topic": "tenancy", "question": "Do customers see each other's records?", "options": ["Shared", "Separate"],
         "suggested": ["Separate"]}]}, "emit_plan_surface": lambda: surface})
    res = (await _run(pid, pc.PlanDraftRequest(prompt=PROMPT)))[-1]
    assert res["type"] == "result", res
    assert [c[0] for c in calls] == ["emit_plan_core", "emit_plan_surface"]
    t = res["timing"]
    assert t["model_calls"] == 2 and t["wall_s"] >= 0 and [c["tool"] for c in t["calls"]] == ["emit_plan_core", "emit_plan_surface"]
    assert t["plan_bytes"] > 0
    plan = res["plan"]
    qs = plan["questions"]
    assert any(q["topic"] == "tenancy" and q["suggested"] == ["Separate"] for q in qs)
    topics = [q["topic"] for q in qs]
    assert len(topics) == len(set(topics))
    # the model-placed question needs one revision call; drop it to see the fully deterministic path
    qs = [q for q in qs if q.get("source") in ("checklist", "plan")]
    from src.db import session as dbs
    from src.services import project_plan as ppm
    async with dbs.async_session_factory() as s:
        await ppm.update_draft(s, pid, 1, questions=qs)
    res2 = (await _run(pid, pc.PlanDraftRequest(version=1, accept_suggestions=True)))[-1]
    assert res2["type"] == "result", res2
    assert [c[0] for c in calls] == ["emit_plan_core", "emit_plan_surface"]          # no revision call
    assert res2["timing"]["model_calls"] == 0
    assert res2["plan"]["body"]["nfr"]["seed_data"] and len(res2["plan"]["body"]["requirements"]["answers"]) == len(qs)
    assert len(res2["plan"]["questions"]) <= len(qs)


def test_accept_suggestions_is_a_valid_request_mode():
    pc.PlanDraftRequest(version=1, accept_suggestions=True)
    with pytest.raises(ValueError):
        pc.PlanDraftRequest(accept_suggestions=True)
    with pytest.raises(ValueError):
        pc.PlanDraftRequest(prompt="x", accept_suggestions=True, version=1)


def test_route_fields_tolerate_optional_marks_enums_and_hints():
    from src.services.plan_compact import _field_list
    assert _field_list("email?, status[open,closed], month!:YYYY-MM, n:integer!") == [
        {"name": "email", "type": "string"}, {"name": "status", "type": "string"},
        {"name": "month", "type": "string", "required": True}, {"name": "n", "type": "integer", "required": True}]
