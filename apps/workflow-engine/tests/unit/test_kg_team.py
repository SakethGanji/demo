"""Capability graph, team-wide mode: one knowledge graph across every project of a team (Postgres).

The team slice is the global nodes, the team's nodes, the project nodes of every project of the team and org-visible patterns.
Project isolation is unchanged: a project slice never gains another project's nodes, and a project of another team never
appears in a team slice (not its nodes, not its edges, and its node detail is a 404)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from src.db.models import WorkflowModel
from src.repositories.kg_repository import KgRepository
from src.routes.knowledge import router as knowledge_router
from src.services import authz, kg_extract, kg_service
from src.services.project_service import ProjectService

from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

ESCALATE = "escalate unresolved exceptions to the head of operations after 48 hours"


def uid(p: str) -> str:
    return f"{p}{uuid.uuid4().hex[:8]}"


def escalation() -> dict:
    """FormTrigger -> Approval (due_in, timed_out -> a second Approval): implements escalation, approval and sla."""
    def c(a: str, b: str, port: str = "main") -> dict:
        return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}

    return {"nodes": [{"name": "Log", "type": "FormTrigger", "parameters": {"title": "Log"}},
                      {"name": "Resolve", "type": "Approval", "parameters": {"title": "Resolve", "assignees": ["role:ops_analyst"], "due_in": "48h"}},
                      {"name": "Escalated", "type": "Approval", "parameters": {"title": "Escalated", "assignees": ["role:ops_head"]}},
                      {"name": "Done", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "x", "value": "1"}]}}],
            "connections": [c("Log", "Resolve"), c("Resolve", "Done", "approved"), c("Resolve", "Escalated", "timed_out"),
                            c("Escalated", "Done", "approved")]}


async def team(sf) -> str:
    t = uid("team_")
    async with sf() as s:
        await s.execute(text("INSERT INTO teams (id, name, slug) VALUES (:t, :t, :t)"), {"t": t})
        await s.commit()
    return t


async def project(sf, team_id: str, name: str | None = None) -> str:
    pid = uid("proj_")
    async with sf() as s:
        await s.execute(text("INSERT INTO projects (id, team_id, name, slug) VALUES (:p, :t, :n, :p)"),
                        {"p": pid, "t": team_id, "n": name or pid})
        await s.commit()
    return pid


async def workflow(sf, team_id: str, pid: str, name: str = "Escalate exceptions") -> str:
    wid = uid("wf_")
    async with sf() as s:
        s.add(WorkflowModel(id=wid, team_id=team_id, project_id=pid, name=name, draft_definition=escalation()))
        await s.commit()
    return wid


async def rebuild(sf, pid: str) -> None:
    async with sf() as s:
        await kg_extract.rebuild(s, pid)


async def world(sf) -> dict:
    """Team T with projects A and B (one escalation workflow each) and an empty project E; team U with project C (one workflow).
    Every project is rebuilt per project."""
    async with sf() as s:
        await kg_extract.seed_global(s)
    t, u = await team(sf), await team(sf)
    a, b, e = await project(sf, t, "Alpha"), await project(sf, t, "Bravo"), await project(sf, t, "Empty")
    c = await project(sf, u, "Charlie")
    wa, wb, wc = await workflow(sf, t, a), await workflow(sf, t, b), await workflow(sf, u, c)
    for p in (a, b, e, c):
        await rebuild(sf, p)
    return {"t": t, "u": u, "a": a, "b": b, "e": e, "c": c, "wa": wa, "wb": wb, "wc": wc}


def keys(sl) -> set[str]:
    return {n["key"] for n in sl.nodes.values()}


# ── the slice ─────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_team_slice_holds_every_project_of_the_team_and_project_slices_stay_isolated(pg):
    w = await world(pg)
    async with pg() as s:
        repo = KgRepository(s)
        ts = await repo.load_team_slice(w["t"])
        sa, sb = await repo.load_slice(w["a"]), await repo.load_slice(w["b"])
    assert ts.project_id is None and ts.team_id == w["t"]
    assert {f"wf:{w['wa']}", f"wf:{w['wb']}"} <= keys(ts)
    assert "concept:escalation" in keys(ts) and "node:Approval" in keys(ts)              # the global nodes are there too
    # isolation is unchanged: each project still sees only its own workflows
    assert f"wf:{w['wa']}" in keys(sa) and f"wf:{w['wb']}" not in keys(sa) and f"wf:{w['wc']}" not in keys(sa)
    assert f"wf:{w['wb']}" in keys(sb) and f"wf:{w['wa']}" not in keys(sb)
    assert {n["project_id"] for n in sa.nodes.values() if n["scope"] == "project"} == {w["a"]}
    # the team slice is exactly the union: no node of a project outside the team
    assert {n["project_id"] for n in ts.nodes.values() if n["scope"] == "project"} == {w["a"], w["b"]}


async def test_a_project_of_another_team_never_appears_in_a_team_slice(pg):
    w = await world(pg)
    async with pg() as s:
        repo = KgRepository(s)
        ts = await repo.load_team_slice(w["t"])
        sc = await repo.load_slice(w["c"])
        detail_c = sc.by_key(f"wf:{w['wc']}")
    assert detail_c is not None
    c_ids = {n["id"] for n in sc.nodes.values() if n["scope"] == "project"}
    assert c_ids and not (c_ids & set(ts.nodes))
    assert all(e["src_id"] not in c_ids and e["dst_id"] not in c_ids for e in ts.edges)
    ids = set(ts.nodes)
    assert all(e["src_id"] in ids and e["dst_id"] in ids for e in ts.edges)
    async with pg() as s:
        g = await kg_service.graph(s, None, team_id=w["t"])
        assert detail_c["id"] not in {n["id"] for n in g["nodes"]}
        assert all(detail_c["id"] not in (e["src_id"], e["dst_id"]) for e in g["edges"])
        with pytest.raises(kg_service.KnowledgeError) as err:
            await kg_service.node_detail(s, None, detail_c["id"], team_id=w["t"])
        assert err.value.status == 404
        with pytest.raises(kg_service.KnowledgeError) as missing:
            await kg_service.node_detail(s, None, "kn_000000000000", team_id=w["t"])
        assert missing.value.detail == err.value.detail                                  # the same answer as a node that does not exist
        with pytest.raises(kg_service.KnowledgeError) as focus:
            await kg_service.graph(s, None, team_id=w["t"], focus=detail_c["id"])
        assert focus.value.status == 404
        # and a node of the team is found, with its neighbours
        na = (await KgRepository(s).load_slice(w["a"])).by_key(f"wf:{w['wa']}")
        d = await kg_service.node_detail(s, None, na["id"], team_id=w["t"])
        assert d["node"]["key"] == f"wf:{w['wa']}" and "concept:escalation" in {n["node"]["key"] for n in d["neighbours"]["implements"]}


async def test_projects_attribution_lists_the_team_projects_that_own_a_returned_node(pg):
    w = await world(pg)
    async with pg() as s:
        g = await kg_service.graph(s, None, team_id=w["t"])
        ga = await kg_service.graph(s, w["a"])
        roles_only = await kg_service.graph(s, None, team_id=w["t"], kinds=["role"])
    assert g["projects"] == [{"id": w["a"], "slug": w["a"], "name": "Alpha"}, {"id": w["b"], "slug": w["b"], "name": "Bravo"}]
    owners = {n["project_id"] for n in g["nodes"] if n["project_id"]}
    assert owners == {p["id"] for p in g["projects"]}                                   # the empty project owns nothing: not listed
    assert ga["projects"] == []                                                         # project mode: always empty
    assert roles_only["projects"] == []                                                 # only roles returned: no project owns one


# ── capabilities and memory ───────────────────────────────────────────────────────────────────────────────────────────


async def test_team_capabilities_sum_the_projects_of_the_team(pg):
    w = await world(pg)
    async with pg() as s:
        tm = await kg_service.capabilities(s, None, team_id=w["t"])
        ma = await kg_service.capabilities(s, w["a"])
        mc = await kg_service.capabilities(s, w["c"])
    rows = {r["concept"]["key"]: r for r in tm["rows"]}
    assert rows["concept:escalation"]["implemented_by"] == {"workflow": 2} and rows["concept:escalation"]["gap"] is False
    assert {r["concept"]["key"]: r for r in ma["rows"]}["concept:escalation"]["implemented_by"] == {"workflow": 1}
    assert {r["concept"]["key"]: r for r in mc["rows"]}["concept:escalation"]["implemented_by"] == {"workflow": 1}  # C is not counted in T
    assert set(tm) == {"rows", "totals"} and tm["totals"]["concepts"] == ma["totals"]["concepts"]
    with pytest.raises(kg_service.KnowledgeError) as err:
        async with pg() as s:
            await kg_service.capabilities(s, None, team_id="team_nope")
    assert err.value.status == 404 and err.value.detail["code"] == "TEAM_NOT_FOUND"


async def test_team_memory_lists_the_team_projects_and_global_events_newest_first(pg):
    w = await world(pg)
    async with pg() as s:
        repo = KgRepository(s)
        seed = await repo.add_learn_event(trigger="seed", summary="a seed")
        mine = await repo.add_learn_event(trigger="human", team_id=w["t"], summary="team T note")
        theirs = await repo.add_learn_event(trigger="human", team_id=w["u"], summary="team U note")
        await s.commit()
        events, _ = await repo.list_learn_events(limit=200)
    c_events = {e["id"] for e in events if e["project_id"] == w["c"]}
    t_events = {e["id"] for e in events if e["project_id"] in (w["a"], w["b"])}
    assert c_events and len(t_events) >= 2
    async with pg() as s:
        page = await kg_service.memory(s, None, team_id=w["t"], limit=200)
    got = [e["id"] for e in page["items"]]
    assert got == sorted(got, reverse=True)
    assert t_events <= set(got) and {seed["id"], mine["id"]} <= set(got)
    assert not (c_events & set(got)) and theirs["id"] not in got
    assert all(e["project_id"] in (w["a"], w["b"], None) for e in page["items"])
    # paging: the same cursor contract as project mode
    async with pg() as s:
        p1 = await kg_service.memory(s, None, team_id=w["t"], limit=2)
        p2 = await kg_service.memory(s, None, team_id=w["t"], limit=2, cursor=p1["next_cursor"])
    assert len(p1["items"]) == 2 and p1["next_cursor"] and [e["id"] for e in p1["items"]] == got[:2]
    assert [e["id"] for e in p2["items"]] == got[2:4]


# ── rebuild ───────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_team_rebuild_covers_every_project_of_the_team_and_is_idempotent(pg):
    async with pg() as s:
        await kg_extract.seed_global(s)
    t, u = await team(pg), await team(pg)
    a, b, c = await project(pg, t, "Alpha"), await project(pg, t, "Bravo"), await project(pg, u, "Charlie")
    wa, wb, wc = await workflow(pg, t, a), await workflow(pg, t, b), await workflow(pg, u, c)
    async with pg() as s:
        first = await kg_service.rebuild(s, None, False, team_id=t)
    assert set(first) == {"added", "updated", "staled", "tagged", "proposed", "warnings", "projects"}
    per = {p["project_id"]: p for p in first["projects"]}
    assert set(per) == {a, b} and all(set(p) == {"project_id", "added", "updated", "staled"} for p in per.values())
    assert per[a]["added"] > 0 and per[b]["added"] > 0
    assert first["added"] >= per[a]["added"] + per[b]["added"]
    async with pg() as s:
        ts = await KgRepository(s).load_team_slice(t)
        sc = await KgRepository(s).load_slice(c)
        events, _ = await KgRepository(s).list_learn_events(limit=200)
    assert {f"wf:{wa}", f"wf:{wb}"} <= keys(ts)
    assert f"wf:{wc}" not in keys(sc)                                                   # the other team was not rebuilt
    assert {e["project_id"] for e in events if e["trigger"] == "rebuild"} >= {a, b}     # one rebuild event per project
    async with pg() as s:
        again = await kg_service.rebuild(s, None, False, team_id=t)
    assert (again["added"], again["updated"], again["staled"]) == (0, 0, 0)
    assert all((p["added"], p["updated"], p["staled"]) == (0, 0, 0) for p in again["projects"])
    async with pg() as s:
        proj = await kg_service.rebuild(s, a, False)
    assert proj["projects"] == [] and (proj["added"], proj["updated"], proj["staled"]) == (0, 0, 0)


# ── understand ────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_understand_works_in_team_mode_and_writes_nothing(pg):
    w = await world(pg)
    async with pg() as s:
        before, _ = await KgRepository(s).list_learn_events(limit=5)
        out = await kg_service.understand(s, None, ESCALATE, team_id=w["t"])
        after, _ = await KgRepository(s).list_learn_events(limit=5)
    assert out["v"] == 1 and "concept:escalation" in {c["key"] for c in out["concepts"]}
    assert out["retrieval"]["subworkflows"] == [] and out["retrieval"]["apis"] == []    # nothing of one project is offered to call
    assert before == after


# ── routes ────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def api(pg, monkeypatch):
    calls: list[tuple[str, dict]] = []
    real = authz.authorize

    def spy(request, action, resource=None):
        calls.append((action, resource))
        return real(request, action, resource)

    monkeypatch.setattr(authz, "authorize", spy)
    app = FastAPI()
    app.include_router(knowledge_router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.authz_calls = calls  # type: ignore[attr-defined]
        yield c


async def test_routes_without_a_project_resolve_to_the_default_team(pg, api):
    async with pg() as s:
        await kg_extract.seed_global(s)
    async with pg() as s:
        d = await ProjectService(s).create(f"kg-team-{uuid.uuid4().hex[:8]}")
    other_team = await team(pg)
    o = await project(pg, other_team, "Other")
    wd, wo = await workflow(pg, "default", d.id), await workflow(pg, other_team, o)
    await rebuild(pg, d.id)
    await rebuild(pg, o)
    r = await api.get("/api/knowledge/graph")
    assert r.status_code == 200, r.text
    g = r.json()
    assert set(g) == {"nodes", "edges", "truncated", "projects"}
    k = {n["key"]: n for n in g["nodes"]}
    assert f"wf:{wd}" in k and f"wf:{wo}" not in k
    assert {"id": d.id, "slug": d.slug, "name": d.name} in g["projects"] and o not in {p["id"] for p in g["projects"]}
    assert api.authz_calls[-1] == ("knowledge.read", {"team_id": "default"})
    # an explicit team, and project_id wins over team_id
    r2 = await api.get(f"/api/knowledge/graph?team_id={other_team}")
    assert r2.status_code == 200 and f"wf:{wo}" in {n["key"] for n in r2.json()["nodes"]}
    assert [p["id"] for p in r2.json()["projects"]] == [o]
    rp = (await api.get(f"/api/knowledge/graph?project_id={d.id}&team_id={other_team}")).json()
    assert f"wf:{wd}" in {n["key"] for n in rp["nodes"]} and f"wf:{wo}" not in {n["key"] for n in rp["nodes"]}
    assert rp["projects"] == []
    assert (await api.get("/api/knowledge/graph?team_id=team_nope")).status_code == 404
    # node detail: a node of another team is a 404 in the default team
    other_node = (await api.get(f"/api/knowledge/graph?team_id={other_team}")).json()
    oid = next(n["id"] for n in other_node["nodes"] if n["key"] == f"wf:{wo}")
    assert (await api.get(f"/api/knowledge/nodes/{oid}")).status_code == 404
    assert (await api.get(f"/api/knowledge/nodes/{oid}?team_id={other_team}")).status_code == 200
    assert (await api.get(f"/api/knowledge/nodes/{k[f'wf:{wd}']['id']}")).status_code == 200
    # capabilities, memory, understand
    caps = await api.get("/api/knowledge/capabilities")
    assert caps.status_code == 200 and set(caps.json()) == {"rows", "totals"}
    mem = await api.get("/api/knowledge/memory?limit=5")
    assert mem.status_code == 200 and len(mem.json()["items"]) <= 5
    assert all(e["project_id"] != o for e in mem.json()["items"])
    assert (await api.get("/api/knowledge/memory?cursor=abc")).status_code == 422
    und = await api.post("/api/knowledge/understand", json={"request": ESCALATE})
    assert und.status_code == 200, und.text
    assert und.json()["v"] == 1
    assert api.authz_calls[-1] == ("knowledge.read", {"team_id": "default"})
    # rebuild of an explicit team over HTTP
    rb = await api.post("/api/knowledge/rebuild", json={"team_id": other_team})
    assert rb.status_code == 200, rb.text
    assert [p["project_id"] for p in rb.json()["projects"]] == [o]
    assert api.authz_calls[-1] == ("knowledge.write", {"team_id": other_team})
    # project mode keeps its shape (projects is always empty there)
    rbp = await api.post("/api/knowledge/rebuild", json={"project_id": d.id})
    assert rbp.status_code == 200 and rbp.json()["projects"] == []
    assert (await api.post("/api/knowledge/rebuild", json={"team_id": "team_nope"})).status_code == 404
