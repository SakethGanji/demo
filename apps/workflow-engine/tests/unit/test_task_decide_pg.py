"""Deciding tasks (Stage 5, DESIGN-STAGE5-UIS 5.4, 7.1): idempotency, exactly-one-winner races, validation, the inbox."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from src.routes.tasks import router as tasks_router
from src.services.task_service import TaskError, TaskService, validate_form_data

from ._pg_world import CALLS, World, approval_flow, install_counter, make_world, remove_counter

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(pg):
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        remove_counter()


@pytest.fixture
async def api(pg):
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def paused(world: World, **approval_params):
    stored = await world.create(approval_flow(**approval_params))
    _, ctx = await world.run(stored.id)
    return stored, ctx.execution_id, await world.task(ctx.execution_id)


# ── idempotency ───────────────────────────────────────────────────────────────

async def test_deciding_twice_with_the_same_key_is_a_retry_of_the_same_click(world):
    _, exec_id, task = await paused(world)
    first = await world.decide(task["id"], "approved", key="click-1")
    again = await world.decide(task["id"], "approved", key="click-1")
    assert first["outcome"] == "decided" and again["outcome"] == "decided"
    assert again["task"]["status"] == "approved"
    await world.settle()
    assert CALLS == ["up", "yes"]  # one resume, not two


async def test_a_different_key_after_the_decision_reports_the_winner(world):
    _, _, task = await paused(world)
    await world.decide(task["id"], "approved", key="a", by="Dana")
    late = await world.decide(task["id"], "rejected", key="b", by="Eli")
    assert late["outcome"] == "already_decided"
    t = late["task"]
    assert t["status"] == "approved" and t["decided_by"] == "Dana" and t["decision"]["decision"] == "approved"
    await world.settle()
    assert CALLS == ["up", "yes"]


async def test_two_concurrent_decides_exactly_one_wins_and_the_run_resumes_once(world):
    _, exec_id, task = await paused(world)
    results = await asyncio.gather(*[world.decide(task["id"], "approved", by=f"user{i}") for i in range(2)])
    assert sorted(r["outcome"] for r in results) == ["already_decided", "decided"]
    assert len({r["task"]["decided_by"] for r in results}) == 1  # both responses name the same winner
    await world.settle()
    assert CALLS == ["up", "yes"]
    assert (await world.execution(exec_id))["status"] == "success"


async def test_many_concurrent_decides_still_one_winner(world):
    _, _, task = await paused(world)
    results = await asyncio.gather(*[world.decide(task["id"], "approved", by=f"u{i}") for i in range(8)])
    assert [r["outcome"] for r in results].count("decided") == 1
    await world.settle()
    assert CALLS.count("yes") == 1


async def test_two_approvers_with_different_decisions_one_wins_and_the_loser_sees_the_winner(world):
    _, exec_id, task = await paused(world)
    a, b = await asyncio.gather(world.decide(task["id"], "approved", by="Dana"), world.decide(task["id"], "rejected", by="Eli"))
    winner = a if a["outcome"] == "decided" else b
    loser = b if winner is a else a
    assert loser["outcome"] == "already_decided"
    assert loser["task"]["status"] == winner["task"]["status"] and loser["task"]["decided_by"] == winner["task"]["decided_by"]
    await world.settle()
    branch = "yes" if winner["task"]["status"] == "approved" else "no"
    assert CALLS == ["up", branch]


async def test_same_key_concurrent_retries_are_all_decided_with_one_resume(world):
    _, _, task = await paused(world)
    results = await asyncio.gather(*[world.decide(task["id"], "approved", key="same") for _ in range(4)])
    assert {r["outcome"] for r in results} == {"decided"}
    await world.settle()
    assert CALLS == ["up", "yes"]


async def test_deciding_a_timed_out_or_cancelled_task_is_already_closed(world):
    _, exec_id, task = await paused(world)
    await world.sql("UPDATE human_tasks SET status = 'timed_out' WHERE id = :i", i=task["id"])
    res = await world.decide(task["id"], "approved")
    assert res["outcome"] == "already_closed" and res["task"]["status"] == "timed_out"


# ── validation ────────────────────────────────────────────────────────────────

async def test_unknown_task_is_404(world):
    with pytest.raises(TaskError) as e:
        await world.decide("tsk_nope")
    assert e.value.status == 404 and e.value.code == "TASK_NOT_FOUND"


async def test_decision_decided_by_and_key_are_validated(world):
    _, _, task = await paused(world)
    for kwargs, field in [({"decision": "maybe"}, "decision"), ({"by": "   "}, "decided_by"), ({"by": "x" * 201}, "decided_by")]:
        with pytest.raises(TaskError) as e:
            await world.decide(task["id"], kwargs.get("decision", "approved"), by=kwargs.get("by", "dana"))
        assert e.value.status == 422 and field in e.value.fields
    async with world.sf() as s:
        with pytest.raises(TaskError) as e:
            await TaskService(s).decide(task["id"], "approved", None, None, "dana", "")
    assert e.value.status == 422 and "Idempotency-Key" in e.value.fields
    assert (await world.task(task["execution_id"]))["status"] == "open"  # nothing was decided by the invalid calls


async def test_comment_rules(world):
    _, _, strict = await paused(world, require_comment_on_reject=True)
    with pytest.raises(TaskError) as e:
        await world.decide(strict["id"], "rejected")
    assert e.value.status == 422 and "comment" in e.value.fields
    ok = await world.decide(strict["id"], "approved")  # approving needs no comment
    assert ok["outcome"] == "decided"
    _, _, mute = await paused(world, allow_comment=False)
    with pytest.raises(TaskError) as e:
        await world.decide(mute["id"], "approved", comment="hi")
    assert "comment" in e.value.fields
    await world.settle()


async def test_form_data_is_validated_against_the_task_form(world):
    form = [{"name": "note", "type": "text", "required": True}, {"name": "level", "type": "select", "options": ["a", "b"]},
            {"name": "n", "type": "number"}, {"name": "ok", "type": "checkbox"}]
    _, _, task = await paused(world, form=form)
    for bad, field in [({}, "note"), ({"note": "x", "level": "z"}, "level"), ({"note": "x", "n": "7"}, "n"),
                       ({"note": "x", "ok": "yes"}, "ok"), ({"note": "x", "extra": 1}, "extra"), ({"note": 5}, "note")]:
        with pytest.raises(TaskError) as e:
            await world.decide(task["id"], "approved", form_data=bad)
        assert e.value.status == 422 and field in e.value.fields, bad
    res = await world.decide(task["id"], "approved", form_data={"note": "fine", "level": "a", "n": 3, "ok": True})
    assert res["outcome"] == "decided" and res["task"]["decision"]["form_data"]["level"] == "a"
    await world.settle()


def test_validate_form_data_is_pure():
    assert validate_form_data([], None) == {} and validate_form_data([], {"x": 1}) == {"x": "is not a field of this form"}
    assert validate_form_data([{"name": "a", "type": "text", "required": True}], {"a": ""}) == {"a": "is required"}
    assert validate_form_data([{"name": "a", "type": "checkbox", "required": True}], {}) == {}
    assert validate_form_data([], [1]) == {"form_data": "must be an object"}


async def test_the_decision_form_data_reaches_the_workflow(world):
    stored, exec_id, task = await paused(world, form=[{"name": "note", "type": "text"}])
    await world.decide(task["id"], "approved", comment="c", form_data={"note": "ship it"})
    await world.settle()
    async with world.sf() as s:
        from src.repositories.node_output_repository import NodeOutputRepository

        rows = {r.node_name: r for r in await NodeOutputRepository(s).get_outputs(exec_id)}
    a = rows["yes"].output[0]["json"]["approval"]
    assert a["form_data"] == {"note": "ship it"} and a["comment"] == "c" and a["decided_by"] == "dana"


# ── detail view: what the studio shows ────────────────────────────────────────

async def test_task_detail_has_context_input_screen_and_no_internals(world):
    from ._pg_world import approval_flow

    w = approval_flow(title="T", context={"Why": "{{ $json.amount }}"})
    w.interface = {"kind": "custom", "slug": "s", "trigger_node": "start",
                   "screens": {"task:review": {"v": 1, "components": [
                       {"type": "field", "bind": "node.up.via"}, {"type": "field", "bind": "input.amount"},
                       {"type": "actions", "bind": "task"}]}}}
    stored = await world.create(w)
    _, ctx = await world.run(stored.id, item={"amount": 742})
    task = await world.task(ctx.execution_id)
    async with world.sf() as s:
        d = await TaskService(s).get(task["id"])
    assert d["title"] == "T" and d["context"]["Why"] == 742 and d["context"]["_bindings"] == {"up.via": "up"}
    assert d["input"]["amount"] == 742 and "_input" not in d["context"]
    assert d["screen"]["components"][0]["bind"] == "node.up.via"
    assert d["allow_comment"] is True and d["require_comment_on_reject"] is False
    assert d["run_status"] == "waiting" and d["workflow_name"] == "approval-flow" and d["node_name"] == "review"
    assert "snapshot" not in d and d["decision"] is None


# ── the inbox ─────────────────────────────────────────────────────────────────

async def seed_inbox(world: World, n: int = 60) -> tuple[str, str]:
    """n tasks in a fresh project: created_at in groups of 3 (ties), alternating status, two workflows."""
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    await world.sql("INSERT INTO projects (id, team_id, name, slug) VALUES (:p, 'default', 'p', :p)", p=pid)
    exec_id = f"exec_{uuid.uuid4().hex}"
    await world.sql(
        "INSERT INTO executions (id, workflow_id, workflow_name, status, mode, project_id) VALUES (:e, 'wf_x', 'x', 'waiting', 'manual', :p)",
        e=exec_id, p=pid)
    await world.sql(
        """INSERT INTO human_tasks (id, project_id, workflow_id, execution_id, node_name, title, assignees, status, created_at, snapshot)
           SELECT 'tsk_' || :tag || lpad(g::text, 3, '0'), :p, CASE WHEN g % 2 = 0 THEN 'wf_even' ELSE 'wf_odd' END, :e,
                  'n' || g, 'Task ' || g,
                  CASE WHEN g % 5 = 0 THEN '["dana@bank.example"]'::jsonb ELSE '[]'::jsonb END,
                  CASE WHEN g % 4 = 0 THEN 'approved' ELSE 'open' END,
                  timestamptz '2026-01-01 00:00:00+00' + ((g / 3) || ' minutes')::interval, '{}'::jsonb
             FROM generate_series(1, :n) g""",
        tag=exec_id[-6:], p=pid, e=exec_id, n=n)
    return pid, exec_id


async def page_all(world: World, **filters) -> list[dict]:
    out, cursor = [], None
    async with world.sf() as s:
        while True:
            page = await TaskService(s).list(cursor=cursor, limit=7, **filters)
            out.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                return out


async def test_inbox_keyset_paging_has_no_duplicates_or_gaps_even_with_equal_timestamps(world):
    pid, _ = await seed_inbox(world, 60)
    rows = await page_all(world, project_id=pid, status="all")
    ids = [r["id"] for r in rows]
    assert len(ids) == 60 and len(set(ids)) == 60
    keys = [(r["created_at"], r["id"]) for r in rows]
    assert keys == sorted(keys, reverse=True)  # newest first, ties broken by id


async def test_inbox_filters_and_counts(world):
    pid, _ = await seed_inbox(world, 60)
    open_rows = await page_all(world, project_id=pid, status="open")
    closed = await page_all(world, project_id=pid, status="closed")
    assert len(open_rows) == 45 and len(closed) == 15 and {r["status"] for r in closed} == {"approved"}
    assert {r["workflow_id"] for r in await page_all(world, project_id=pid, status="all", workflow_id="wf_even")} == {"wf_even"}
    mine = await page_all(world, project_id=pid, status="all", assignee="dana@bank.example")
    assert len(mine) == 12 and all("dana@bank.example" in r["assignees"] for r in mine)
    async with world.sf() as s:
        counts = await TaskService(s).counts(pid)
    assert counts["open"] == 45 and sum(counts["by_workflow"].values()) == 45
    assert set(counts["by_workflow"]) == {"wf_even", "wf_odd"}


async def test_inbox_rejects_bad_parameters(world):
    async with world.sf() as s:
        svc = TaskService(s)
        for kw in ({"status": "weird"}, {"limit": 0}, {"limit": 101}, {"cursor": "not-a-cursor"}):
            with pytest.raises(TaskError) as e:
                await svc.list(**kw)
            assert e.value.status == 422


# ── HTTP ──────────────────────────────────────────────────────────────────────

async def test_http_list_get_decide_and_counts(world, api):
    pid, _ = await seed_inbox(world, 10)
    r = await api.get("/api/tasks", params={"project_id": pid, "limit": 4})
    assert r.status_code == 200 and len(r.json()["items"]) == 4 and r.json()["next_cursor"]
    r2 = await api.get("/api/tasks", params={"project_id": pid, "limit": 4, "cursor": r.json()["next_cursor"]})
    assert not set(i["id"] for i in r.json()["items"]) & set(i["id"] for i in r2.json()["items"])
    assert (await api.get("/api/tasks/counts", params={"project_id": pid})).json()["open"] == 8
    assert (await api.get("/api/tasks/tsk_missing")).status_code == 404

    _, exec_id, task = await paused(world)
    d = await api.get(f"/api/tasks/{task['id']}")
    assert d.status_code == 200 and d.json()["status"] == "open"
    body = {"decision": "approved", "decided_by": "dana", "comment": "go"}
    res = await api.post(f"/api/tasks/{task['id']}/decide", json=body, headers={"Idempotency-Key": "k1"})
    assert res.status_code == 200 and res.json()["outcome"] == "decided" and res.json()["task"]["decided_by"] == "dana"
    again = await api.post(f"/api/tasks/{task['id']}/decide", json=body, headers={"Idempotency-Key": "k1"})
    assert again.json()["outcome"] == "decided"
    other = await api.post(f"/api/tasks/{task['id']}/decide", json={**body, "decided_by": "eli"}, headers={"Idempotency-Key": "k2"})
    assert other.json()["outcome"] == "already_decided" and other.json()["task"]["decided_by"] == "dana"
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success"


async def test_http_decide_errors(world, api):
    _, _, task = await paused(world)
    url = f"/api/tasks/{task['id']}/decide"
    ok = {"decision": "approved", "decided_by": "dana"}
    r = await api.post(url, json=ok)  # no key
    assert r.status_code == 422 and r.json()["detail"]["code"] == "VALIDATION_FAILED" and "Idempotency-Key" in r.json()["detail"]["fields"]
    r = await api.post(url, json={**ok, "idempotency_key": "from-body"})  # body key accepted (a proxy may strip the header)
    assert r.status_code == 200 and r.json()["outcome"] == "decided"
    _, _, t2 = await paused(world)
    h = {"Idempotency-Key": "k"}
    assert (await api.post(f"/api/tasks/{t2['id']}/decide", json={**ok, "bogus": 1}, headers=h)).status_code == 422
    assert (await api.post(f"/api/tasks/{t2['id']}/decide", json={"decision": "approved"}, headers=h)).status_code == 422
    big = {**ok, "comment": "x" * 40_000}
    r = await api.post(f"/api/tasks/{t2['id']}/decide", content=json.dumps(big), headers={**h, "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    assert (await api.post("/api/tasks/tsk_nope/decide", json=ok, headers=h)).status_code == 404
    await world.settle()


async def test_http_header_key_wins_over_the_body_key(world, api):
    _, _, task = await paused(world)
    ok = {"decision": "approved", "decided_by": "dana", "idempotency_key": "body"}
    await api.post(f"/api/tasks/{task['id']}/decide", json=ok, headers={"Idempotency-Key": "header"})
    row = await world.task(task["execution_id"])
    assert row["decide_key"] == "header"
    await world.settle()
