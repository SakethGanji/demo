"""Capability graph Wave 0: the migration, the graph repository, the app-build repository and the ai_proposals pass-through.

Runs on the throwaway Postgres (``tests.unit.conftest.pg``); the migration test makes a second throwaway database of its own.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from src.repositories.ai_proposal_repository import STATUSES as PROPOSAL_STATUSES
from src.repositories.ai_proposal_repository import AIProposalRepository, new_proposal_id
from src.repositories.app_build_repository import AppBuildRepository, new_app_build_id
from src.repositories.app_build_repository import STATUSES as BUILD_STATUSES
from src.repositories.kg_repository import (
    EdgeIntegrityError, KgRepository, app_key, check_edge, concept_key, node_type_key, pattern_key, role_key, workflow_key,
)
from src.repositories.ai_proposal_repository import UnknownColumn
from src.services import kg_ontology

from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

MIGRATION = "20261022120000"


def uid(prefix: str = "x") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# ── helpers ───────────────────────────────────────────────────────────────────────────────────────────────────────────


async def mkproject(sf, team: str | None = None) -> tuple[str, str]:
    """A fresh team and project; returns ``(team_id, project_id)``."""
    team = team or uid("team_")
    pid = uid("proj_")
    async with sf() as s:
        await s.execute(text("INSERT INTO teams (id, name, slug) VALUES (:t, :t, :t) ON CONFLICT (id) DO NOTHING"), {"t": team})
        await s.execute(text("INSERT INTO projects (id, team_id, name, slug) VALUES (:p, :t, :p, :p)"), {"p": pid, "t": team})
        await s.commit()
    return team, pid


async def node(sf, key: str, kind: str = "concept", **kw):
    kw.setdefault("label", key)
    kw.setdefault("source", "seed")
    async with sf() as s:
        res = await KgRepository(s).upsert_node(key=key, kind=kind, **kw)
        await s.commit()
    return res


async def edge(sf, src: dict, rel: str, dst: dict, **kw):
    async with sf() as s:
        res = await KgRepository(s).add_edge(src["id"], rel, dst["id"], source=kw.pop("source", "seed"), **kw)
        await s.commit()
    return res


# ── migration ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_migration_up_down_and_up_again():
    from tests.integration.conftest import TestDatabase

    db = TestDatabase()
    db.create()
    try:
        db.migrate("apply")

        def tables() -> set[str]:
            rows = db.query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'workflow-app'")
            return {r[0] for r in rows}

        def proposal_cols() -> set[str]:
            rows = db.query("SELECT column_name FROM information_schema.columns WHERE table_schema = 'workflow-app' AND table_name = 'ai_proposals'")
            return {r[0] for r in rows}

        def insert_proposal(status: str) -> None:
            db.query("""INSERT INTO ai_proposals (id, project_id, kind, status, actor, request, request_sha256, model, provider)
                        VALUES (%s, 'proj_default', 'build', %s, 'a', 'r', %s, 'm', 'p')""", (new_proposal_id(), status, "a" * 64))

        assert {"kg_nodes", "kg_edges", "kg_learn_events", "app_builds"} <= tables()
        assert {"understanding", "app_build_id", "part_key"} <= proposal_cols()
        insert_proposal("clarify")                      # the widened status CHECK
        with pytest.raises(Exception):
            insert_proposal("nonsense")                 # ... is still a CHECK

        db.migrate("rollback", MIGRATION)
        assert not {"kg_nodes", "kg_edges", "kg_learn_events", "app_builds"} & tables()
        assert not {"understanding", "app_build_id", "part_key"} & proposal_cols()
        assert db.query("SELECT count(*) FROM ai_proposals WHERE status = 'clarify'")[0][0] == 0
        assert db.query("SELECT count(*) FROM ai_proposals WHERE status = 'discarded'")[0][0] == 1   # clarify rows are kept as discarded
        with pytest.raises(Exception):
            insert_proposal("clarify")                  # the old CHECK is back

        db.migrate("apply")                             # up again, cleanly
        assert {"kg_nodes", "kg_edges", "kg_learn_events", "app_builds"} <= tables()
        insert_proposal("clarify")
    finally:
        db.drop()


# ── nodes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_upsert_is_idempotent_and_reports_what_happened(pg):
    key = concept_key(uid("c"))
    first = await node(pg, key, label="Thing", aliases=["thing"], props={"a": 1}, summary="s")
    assert first.action == "created" and first.node["id"].startswith("kn_") and len(first.node["id"]) == 15
    assert first.node["aliases"] == ["thing"] and first.node["props"] == {"a": 1} and first.node["status"] == "active"

    same = await node(pg, key, label="Thing", aliases=["thing"], props={"a": 1}, summary="s")
    assert same.action == "unchanged" and same.node["id"] == first.node["id"]
    assert same.node["updated_at"] == first.node["updated_at"]       # an identical write must not move the cache key

    changed = await node(pg, key, label="Thing 2", aliases=["thing", "stuff"], props={"a": 2}, summary="s")
    assert changed.action == "updated" and changed.node["id"] == first.node["id"]
    assert changed.node["props"] == {"a": 2} and changed.node["created_at"] == first.node["created_at"]
    assert changed.node["updated_at"] > first.node["updated_at"]


async def test_upsert_never_overwrites_a_node_written_by_another_source(pg):
    key = concept_key(uid("c"))
    seed = await node(pg, key, label="Seeded", source="seed")
    clobber = await node(pg, key, label="Extracted", source="extracted")
    assert clobber.action == "skipped" and clobber.node["label"] == "Seeded" and clobber.node["id"] == seed.node["id"]


async def test_upsert_keeps_the_stored_status_unless_one_is_given(pg):
    key = concept_key(uid("c"))
    await node(pg, key, source="llm_suggested", status="proposed")
    async with pg() as s:
        got = await KgRepository(s).get_node_by_key(key)
    assert got["status"] == "proposed"
    again = await node(pg, key, source="llm_suggested", label="Renamed")     # status=None: a human's decision survives a re-run
    assert again.action == "updated" and again.node["status"] == "proposed"
    revived = await node(pg, key, source="llm_suggested", label="Renamed", status="active")
    assert revived.action == "updated" and revived.node["status"] == "active"


async def test_the_same_key_can_live_in_different_scopes(pg):
    team, p1 = await mkproject(pg)
    _, p2 = await mkproject(pg)
    key = f"wf:{uid('w')}"
    a = await node(pg, key, "capability", subkind="workflow", scope="project", project_id=p1, team_id=team)
    b = await node(pg, key, "capability", subkind="workflow", scope="project", project_id=p2)
    g = await node(pg, key, "capability", subkind="workflow")
    assert len({a.node["id"], b.node["id"], g.node["id"]}) == 3 and {a.action, b.action, g.action} == {"created"}
    async with pg() as s:
        r = KgRepository(s)
        assert (await r.get_node_by_key(key, scope="project", project_id=p1))["id"] == a.node["id"]
        assert (await r.get_node_by_key(key, scope="project", project_id=p2))["id"] == b.node["id"]
        assert (await r.get_node_by_key(key))["id"] == g.node["id"]


async def test_upsert_validates_enums_and_scope_owners(pg):
    async with pg() as s:
        r = KgRepository(s)
        for bad in ({"kind": "thing"}, {"scope": "galaxy"}, {"source": "magic"}, {"visibility": "everyone"}, {"status": "dead"},
                    {"scope": "project"}, {"scope": "team"}):
            with pytest.raises(ValueError):
                await r.upsert_node(**{"key": "k", "kind": "concept", "label": "l", "source": "seed", **bad})


async def test_set_node_status_is_conditional(pg):
    n = await node(pg, concept_key(uid("c")), source="llm_suggested", status="proposed")
    async with pg() as s:
        r = KgRepository(s)
        assert await r.set_node_status(n.node["id"], "active", ["stale"]) is None          # guard fails
        assert (await r.set_node_status(n.node["id"], "active", ["proposed"]))["status"] == "active"
        await s.commit()


async def test_mark_stale_touches_only_extracted_nodes_of_that_project_that_disappeared(pg):
    _, p1 = await mkproject(pg)
    _, p2 = await mkproject(pg)
    mk = lambda k, p, src="extracted": node(pg, f"wf:{k}", "capability", subkind="workflow", scope="project", project_id=p, source=src)
    keep, gone, learned, other = uid("a"), uid("b"), uid("c"), uid("d")
    await mk(keep, p1); await mk(gone, p1); await mk(learned, p1, "learned"); await mk(other, p2)
    async with pg() as s:
        n = await KgRepository(s).mark_stale(source="extracted", alive_keys=[f"wf:{keep}"], project_id=p1)
        await s.commit()
        assert n == 1
        r = KgRepository(s)
        st = lambda k, p: r.get_node_by_key(f"wf:{k}", scope="project", project_id=p)
        assert (await st(keep, p1))["status"] == "active" and (await st(gone, p1))["status"] == "stale"
        assert (await st(learned, p1))["status"] == "active" and (await st(other, p2))["status"] == "active"


async def test_bump_validated_is_atomic_under_concurrency(pg):
    n = await node(pg, pattern_key(uid("p").ljust(16, "0")), "pattern", subkind="app", source="learned", visibility="org")

    async def go():
        async with pg() as s:
            await KgRepository(s).bump_validated(n.node["id"], props_patch={"evidence": ["x"]})
            await s.commit()

    await asyncio.gather(*[go() for _ in range(12)])
    async with pg() as s:
        got = await KgRepository(s).get_node(n.node["id"])
    assert got["validated_count"] == 12 and got["last_validated_at"] is not None and got["props"]["evidence"] == ["x"]


async def test_deleting_a_project_removes_its_graph(pg):
    _, p = await mkproject(pg)
    a = await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow", scope="project", project_id=p)
    b = await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow", scope="project", project_id=p)
    await edge(pg, a.node, "composes", b.node)
    async with pg() as s:
        await KgRepository(s).add_learn_event(trigger="rebuild", summary="x", project_id=p)
        await s.execute(text("DELETE FROM projects WHERE id = :p"), {"p": p})
        await s.commit()
    async with pg() as s:
        assert (await s.execute(text("SELECT count(*) FROM kg_nodes WHERE project_id = :p"), {"p": p})).scalar() == 0
        assert (await s.execute(text("SELECT count(*) FROM kg_edges WHERE src_id = :a"), {"a": a.node["id"]})).scalar() == 0
        assert (await s.execute(text("SELECT count(*) FROM kg_learn_events WHERE project_id = :p"), {"p": p})).scalar() == 0


# ── edges: one failing case per integrity rule ────────────────────────────────────────────────────────────────────────


async def test_edge_upsert_is_idempotent_and_updates_props(pg):
    a = (await node(pg, concept_key(uid("a")))).node
    b = (await node(pg, concept_key(uid("b")))).node
    e1 = await edge(pg, a, "requires", b, props={"slot": "x"}, confidence=0.7)
    assert e1.action == "created" and e1.edge["props"] == {"slot": "x"} and e1.edge["confidence"] == pytest.approx(0.7)
    assert (await edge(pg, a, "requires", b, props={"slot": "x"}, confidence=0.7)).action == "unchanged"
    e3 = await edge(pg, a, "requires", b, props={"slot": "y"}, confidence=0.7)
    assert e3.action == "updated" and e3.edge["id"] == e1.edge["id"] and e3.edge["props"] == {"slot": "y"}


async def test_implements_must_end_at_a_concept(pg):
    cap = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow")).node
    role = (await node(pg, role_key(uid("r")), "role")).node
    concept = (await node(pg, concept_key(uid("c")))).node
    await edge(pg, cap, "implements", concept)
    async with pg() as s:
        with pytest.raises(EdgeIntegrityError) as e:
            await KgRepository(s).add_edge(cap["id"], "implements", role["id"], source="seed")
    assert e.value.code == "KGE001"


async def test_requires_permission_must_end_at_a_role(pg):
    cap = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow")).node
    role = (await node(pg, role_key(uid("r")), "role")).node
    concept = (await node(pg, concept_key(uid("c")))).node
    await edge(pg, cap, "requires_permission", role, props={"slot": "approver_role"})
    async with pg() as s:
        with pytest.raises(EdgeIntegrityError) as e:
            await KgRepository(s).add_edge(cap["id"], "requires_permission", concept["id"], source="seed")
    assert e.value.code == "KGE002"


async def test_escalates_to_runs_between_roles_or_from_a_workflow_to_a_role(pg):
    r1 = (await node(pg, role_key(uid("r")), "role")).node
    r2 = (await node(pg, role_key(uid("r")), "role")).node
    wf = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow")).node
    api = (await node(pg, f"api:{uid('a')}", "capability", subkind="api_operation")).node
    concept = (await node(pg, concept_key(uid("c")))).node
    await edge(pg, r1, "escalates_to", r2, props={"after": "48h"})
    await edge(pg, wf, "escalates_to", r2)
    async with pg() as s:
        r = KgRepository(s)
        for src, dst in ((concept, r2), (api, r2), (r1, wf), (wf, concept)):
            with pytest.raises(EdgeIntegrityError) as e:
                await r.add_edge(src["id"], "escalates_to", dst["id"], source="seed")
            assert e.value.code == "KGE003", (src["key"], dst["key"])


async def test_a_project_node_never_points_into_another_project_or_team(pg):
    t1, p1 = await mkproject(pg)
    t2, p2 = await mkproject(pg)
    w1 = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow", scope="project", project_id=p1, team_id=t1)).node
    w1b = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow", scope="project", project_id=p1, team_id=t1)).node
    w2 = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow", scope="project", project_id=p2, team_id=t2)).node
    g = (await node(pg, node_type_key(uid("T")), "capability", subkind="node_type")).node
    team_role_other = (await node(pg, role_key(uid("r")), "role", scope="team", team_id=t2)).node
    await edge(pg, w1, "composes", w1b)             # same project
    await edge(pg, w1, "composes", g)               # project -> global
    async with pg() as s:
        r = KgRepository(s)
        for src, dst in ((w1, w2), (w1, team_role_other)):
            with pytest.raises(EdgeIntegrityError) as e:
                await r.add_edge(src["id"], "composes", dst["id"], source="extracted")
            assert e.value.code == "KGE004"
        with pytest.raises(EdgeIntegrityError) as e:    # a global node (ontology, template, node type) must not depend on a project node
            await r.add_edge(g["id"], "composes", w1["id"], source="extracted")
        assert e.value.code == "KGE004"


async def test_a_learned_pattern_may_cite_its_project_evidence(pg):
    t1, p1 = await mkproject(pg)
    pat = (await node(pg, pattern_key(uuid.uuid4().hex), "pattern", subkind="app", source="learned", visibility="org")).node
    wf = (await node(pg, f"wf:{uid('w')}", "capability", subkind="workflow", scope="project", project_id=p1, team_id=t1)).node
    await edge(pg, pat, "validated_by", wf, props={"tests": 5, "passed": 5}, source="learned")


async def test_unknown_relation_self_loop_and_missing_node(pg):
    a = (await node(pg, concept_key(uid("a")))).node
    async with pg() as s:
        r = KgRepository(s)
        with pytest.raises(EdgeIntegrityError) as e:
            await r.add_edge(a["id"], "likes", a["id"], source="seed")
        assert e.value.code == "KGE005"
        with pytest.raises(EdgeIntegrityError) as e:
            await r.add_edge(a["id"], "requires", a["id"], source="seed")
        assert e.value.code == "KGE005"
        with pytest.raises(EdgeIntegrityError) as e:
            await r.add_edge(a["id"], "requires", "kn_missing00000", source="seed")
        assert e.value.code == "KGE006"


def test_check_edge_is_pure_and_matches_the_database_rules():
    n = lambda **kw: {"id": uid("n"), "key": "k", "kind": "concept", "subkind": None, "scope": "global", "team_id": None, "project_id": None, **kw}
    assert check_edge("is_a", n(), n()) is None
    assert check_edge("implements", n(kind="capability"), n(kind="capability"))[0] == "KGE001"
    assert check_edge("composes", n(scope="project", project_id="a"), n(scope="project", project_id="b"))[0] == "KGE004"


# ── the scoped slice ──────────────────────────────────────────────────────────────────────────────────────────────────


async def test_load_slice_applies_the_scope_rule(pg):
    ta, pa = await mkproject(pg)
    tb, pb = await mkproject(pg)
    gc = (await node(pg, concept_key(uid("g")))).node                                                        # global
    ra = (await node(pg, role_key(uid("ra")), "role", scope="team", team_id=ta)).node                      # team A
    rb = (await node(pg, role_key(uid("rb")), "role", scope="team", team_id=tb)).node                      # team B
    wa = (await node(pg, f"wf:{uid('wa')}", "capability", subkind="workflow", scope="project", project_id=pa, team_id=ta)).node
    wb = (await node(pg, f"wf:{uid('wb')}", "capability", subkind="workflow", scope="project", project_id=pb, team_id=tb)).node
    pat_org = (await node(pg, pattern_key(uuid.uuid4().hex), "pattern", subkind="app", source="learned", visibility="org",
                          scope="project", project_id=pb, team_id=tb)).node
    pat_private = (await node(pg, pattern_key(uuid.uuid4().hex), "pattern", subkind="app", source="learned",
                              scope="project", project_id=pb, team_id=tb)).node
    app_b = (await node(pg, app_key(uid("ab_")), "app", scope="project", project_id=pb, team_id=tb, source="learned")).node
    await edge(pg, wa, "implements", gc)
    await edge(pg, wb, "implements", gc)
    await edge(pg, pat_org, "implements", gc, source="learned")
    await edge(pg, pat_org, "validated_by", wb, source="learned")          # evidence in team B's project
    await edge(pg, wa, "requires_permission", ra)

    async with pg() as s:
        sl = await KgRepository(s).load_slice(pa)
    ids = set(sl.nodes)
    assert sl.project_id == pa and sl.team_id == ta and sl.version
    assert {gc["id"], ra["id"], wa["id"], pat_org["id"]} <= ids
    assert not ids & {rb["id"], wb["id"], pat_private["id"], app_b["id"]}      # other team's role / project nodes stay hidden
    pairs = {(sl.nodes[e["src_id"]]["key"], e["rel"], sl.nodes[e["dst_id"]]["key"]) for e in sl.edges}
    assert (wa["key"], "implements", gc["key"]) in pairs and (pat_org["key"], "implements", gc["key"]) in pairs
    assert (pat_org["key"], "validated_by", wb["key"]) not in pairs and not any(wb["key"] in (a, c) for a, _, c in pairs)
    # helpers
    assert sl.by_key(wa["key"])["id"] == wa["id"] and [n["key"] for n in sl.of_kind("role") if n["id"] == ra["id"]] == [ra["key"]]
    assert [e["dst_id"] for e in sl.out_edges(wa["id"], "implements")] == [gc["id"]]
    assert {e["src_id"] for e in sl.in_edges(gc["id"], "implements")} >= {wa["id"], pat_org["id"]}

    async with pg() as s:
        slb = await KgRepository(s).load_slice(pb)
    assert {rb["id"], wb["id"], pat_private["id"], app_b["id"], pat_org["id"]} <= set(slb.nodes)
    assert not set(slb.nodes) & {ra["id"], wa["id"]}


async def test_load_slice_leaves_out_proposed_and_rejected_nodes_unless_asked(pg):
    _, p = await mkproject(pg)
    proposed = (await node(pg, concept_key(uid("p")), source="llm_suggested", status="proposed")).node
    rejected = (await node(pg, concept_key(uid("r")), source="llm_suggested", status="rejected")).node
    stale = (await node(pg, f"wf:{uid('s')}", "capability", subkind="workflow", scope="project", project_id=p, status="stale")).node
    async with pg() as s:
        default = await KgRepository(s).load_slice(p)
        wide = await KgRepository(s).load_slice(p, statuses=("active", "stale", "proposed"))
    assert proposed["id"] not in default.nodes and rejected["id"] not in default.nodes and stale["id"] in default.nodes
    assert proposed["id"] in wide.nodes and rejected["id"] not in wide.nodes


async def test_load_slice_of_an_unknown_project_still_returns_the_global_graph(pg):
    g = (await node(pg, concept_key(uid("g")))).node
    async with pg() as s:
        sl = await KgRepository(s).load_slice("proj_does_not_exist")
    assert sl.team_id is None and g["id"] in sl.nodes


async def test_neighbours_lists_both_directions(pg):
    a = (await node(pg, concept_key(uid("a")))).node
    b = (await node(pg, concept_key(uid("b")))).node
    c = (await node(pg, concept_key(uid("c")))).node
    await edge(pg, a, "requires", b)
    await edge(pg, c, "is_a", a)
    async with pg() as s:
        ns = await KgRepository(s).neighbours(a["id"])
    assert {(n["rel"], n["outgoing"], n["other"]["key"]) for n in ns} == {("requires", True, b["key"]), ("is_a", False, c["key"])}


# ── learn events ──────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_learn_events_are_newest_first_filtered_and_paged(pg):
    _, p = await mkproject(pg)
    _, other = await mkproject(pg)
    async with pg() as s:
        r = KgRepository(s)
        for i in range(5):
            await r.add_learn_event(trigger="kept_app", summary=f"e{i}", project_id=p, actor="Asha", proposal_id="ab_1",
                                    payload={"i": i, "validated_count": i})
        await r.add_learn_event(trigger="rebuild", summary="elsewhere", project_id=other)
        with pytest.raises(ValueError):
            await r.add_learn_event(trigger="bogus", summary="x")
        await s.commit()
    async with pg() as s:
        r = KgRepository(s)
        page1, nxt = await r.list_learn_events(p, limit=2)
        assert [e["summary"] for e in page1] == ["e4", "e3"] and nxt == page1[-1]["id"]
        assert page1[0]["payload"] == {"i": 4, "validated_count": 4} and page1[0]["at"].tzinfo is not None
        page2, nxt2 = await r.list_learn_events(p, limit=2, before_id=nxt)
        assert [e["summary"] for e in page2] == ["e2", "e1"] and nxt2 is not None
        page3, nxt3 = await r.list_learn_events(p, limit=2, before_id=nxt2)
        assert [e["summary"] for e in page3] == ["e0"] and nxt3 is None


async def test_the_learn_log_is_append_only():
    from src.repositories import kg_repository

    public = [m for m in dir(kg_repository.KgRepository) if not m.startswith("_")]
    assert not [m for m in public if "learn" in m and m not in ("add_learn_event", "list_learn_events")]


# ── the seed ontology in the database ────────────────────────────────────────────────────────────────────────────────


async def test_seeding_the_ontology_is_idempotent(pg):
    async with pg() as s:
        first = await kg_ontology.seed(s)
        await s.commit()
    assert first.nodes["created"] + first.nodes["unchanged"] == 30 and first.nodes["skipped"] == 0
    async with pg() as s:
        second = await kg_ontology.seed(s)
        await s.commit()
    assert second.nodes == {"created": 0, "updated": 0, "unchanged": 30, "skipped": 0}
    assert second.edges["created"] == 0 and second.edges["updated"] == 0 and not second.changed
    # renders_with targets are ui:component nodes that kg_extract writes later: deferred, not an error
    assert {e["dst"] for e in second.deferred_edges} == {"ui:component:table"}
    async with pg() as s:
        r = KgRepository(s)
        esc = await r.get_node_by_key("concept:escalation")
        assert esc["source"] == "seed" and esc["subkind"] == "process" and esc["props"]["slots"][0]["name"] == "target_role"
        assert (await r.get_node_by_key("role:ops_head"))["aliases"][0] == "head of operations"
        ns = await r.neighbours(esc["id"])
        assert {n["other"]["key"] for n in ns if n["outgoing"] and n["rel"] == "requires"} == {"concept:sla", "concept:assignment"}


# ── ai_proposals pass-through and the clarify status ─────────────────────────────────────────────────────────────────


def proposal(**over) -> dict:
    base = {"id": new_proposal_id(), "project_id": "proj_default", "kind": "build", "status": "spec_draft", "actor": "Asha",
            "request": "r", "request_sha256": "a" * 64, "model": "m", "provider": "p"}
    return {**base, **over}


async def test_proposals_carry_the_understanding_app_build_and_part(pg):
    understanding = {"v": 1, "concepts": [{"key": "concept:approval"}], "questions": []}
    async with pg() as s:
        ab = await AppBuildRepository(s).insert({"project_id": "proj_default", "actor": "Asha", "request": "r",
                                                 "request_sha256": "b" * 64, "status": "planned"})
        row = await AIProposalRepository(s).insert(proposal(understanding=understanding, app_build_id=ab["id"], part_key="log_exception"))
        await s.commit()
    async with pg() as s:
        got = await AIProposalRepository(s).get(row["id"])
        listed, _ = await AIProposalRepository(s).list("proj_default", limit=200)
        by_build, _ = await AIProposalRepository(s).list("proj_default", app_build_id=ab["id"])
    assert got["understanding"] == understanding and got["app_build_id"] == ab["id"] and got["part_key"] == "log_exception"
    assert [r["id"] for r in by_build] == [row["id"]] and by_build[0]["part_key"] == "log_exception"
    assert row["id"] in {r["id"] for r in listed}
    plain = await _plain(pg)
    assert plain["understanding"] is None and plain["app_build_id"] is None and plain["part_key"] is None   # old callers unaffected


async def _plain(sf) -> dict:
    async with sf() as s:
        r = await AIProposalRepository(s).insert(proposal())
        await s.commit()
    return r


async def test_the_clarify_status_is_accepted_and_transitions_to_spec_draft(pg):
    assert "clarify" in PROPOSAL_STATUSES
    async with pg() as s:
        repo = AIProposalRepository(s)
        r = await repo.insert(proposal(status="clarify", understanding={"v": 1, "questions": [{"id": "q_x"}]}))
        assert await repo.transition(r["id"], ["spec_draft"], "spec_approved") is None         # wrong from-state
        won = await repo.transition(r["id"], ["clarify"], "spec_draft", {"understanding": {"v": 1, "questions": []}})
        assert won["status"] == "spec_draft" and won["understanding"]["questions"] == []
        d = await repo.insert(proposal(status="clarify"))
        assert (await repo.transition(d["id"], ["clarify"], "discarded"))["status"] == "discarded"
        await s.commit()
    async with pg() as s:
        with pytest.raises(Exception):
            await AIProposalRepository(s).insert(proposal(status="nonsense"))                  # the CHECK is still there


async def test_deleting_an_app_build_keeps_its_part_proposals(pg):
    async with pg() as s:
        ab = await AppBuildRepository(s).insert({"project_id": "proj_default", "actor": "Asha", "request": "r",
                                                 "request_sha256": "c" * 64, "status": "planned"})
        row = await AIProposalRepository(s).insert(proposal(app_build_id=ab["id"], part_key="p"))
        await s.execute(text("DELETE FROM app_builds WHERE id = :i"), {"i": ab["id"]})
        await s.commit()
    async with pg() as s:
        assert (await AIProposalRepository(s).get(row["id"]))["app_build_id"] is None


# ── app builds ────────────────────────────────────────────────────────────────────────────────────────────────────────


def build(**over) -> dict:
    return {**{"id": new_app_build_id(), "project_id": "proj_default", "actor": "Asha", "request": "an app",
               "request_sha256": "d" * 64, "status": "clarify"}, **over}


def test_app_build_ids_and_statuses_have_the_documented_shape():
    i = new_app_build_id()
    assert i.startswith("ab_") and len(i) == 15 and new_app_build_id() != i
    assert BUILD_STATUSES == ("clarify", "planned", "specs", "building", "ready", "kept", "discarded", "failed")


async def test_app_build_roundtrips_json_and_hides_other_projects(pg):
    understanding = {"v": 1, "shape": "app"}
    plan = {"v": 1, "parts": [{"key": "a"}]}
    async with pg() as s:
        r = await AppBuildRepository(s).insert(build(understanding=understanding, plan=plan))
        await s.commit()
    async with pg() as s:
        repo = AppBuildRepository(s)
        got = await repo.get(r["id"], "proj_default")
        assert got["understanding"] == understanding and got["plan"] == plan and got["answers"] == {} and got["bundle"] is None
        assert got["created_at"].tzinfo is not None
        assert await repo.get(r["id"], "proj_other") is None and await repo.get("ab_missing") is None


async def test_app_build_rejects_unknown_columns_missing_fields_and_bad_status(pg):
    async with pg() as s:
        repo = AppBuildRepository(s)
        with pytest.raises(UnknownColumn):
            await repo.insert(build(bogus=1))
        with pytest.raises(UnknownColumn):
            await repo.insert(build(kept_by="x"))                  # only the Keep transition may set these
        with pytest.raises(ValueError):
            await repo.insert(build(actor=None))
        with pytest.raises(ValueError):
            await repo.insert(build(status="nonsense"))


async def test_app_build_transition_has_exactly_one_winner(pg):
    async with pg() as s:
        r = await AppBuildRepository(s).insert(build(status="ready"))
        await s.commit()

    async def go(i):
        async with pg() as s:
            won = await AppBuildRepository(s).transition(r["id"], ["ready"], "kept", {"kept_by": f"u{i}", "install_id": f"tpi_{i}"})
            await s.commit()
            return won

    results = await asyncio.gather(*[go(i) for i in range(10)])
    winners = [w for w in results if w is not None]
    assert len(winners) == 1 and winners[0]["status"] == "kept"
    async with pg() as s:
        assert (await AppBuildRepository(s).get(r["id"]))["kept_by"] == winners[0]["kept_by"]


async def test_app_build_transition_guards_and_update_fields(pg):
    async with pg() as s:
        repo = AppBuildRepository(s)
        r = await repo.insert(build(status="clarify"))
        assert await repo.transition(r["id"], ["planned"], "specs") is None
        assert (await repo.transition(r["id"], ["clarify"], "planned", {"answers": {"q": "category"}}))["answers"] == {"q": "category"}
        assert await repo.update_fields(r["id"], {"plan": {"v": 1}}, only_in=["planned"]) is not None
        assert await repo.update_fields(r["id"], {"plan": {"v": 2}}, only_in=["kept"]) is None
        with pytest.raises(ValueError):
            await repo.transition(r["id"], ["planned"], "bogus")
        with pytest.raises(UnknownColumn):
            await repo.update_fields(r["id"], {"actor": "someone else"})
        await s.commit()
    async with pg() as s:
        assert (await AppBuildRepository(s).get(r["id"]))["plan"] == {"v": 1}


async def test_app_build_list_is_newest_first_filtered_and_paged(pg):
    _, p = await mkproject(pg)
    ids = []
    for i in range(5):                      # one transaction each: created_at is now() of the transaction, so order needs distinct ones
        async with pg() as s:
            ids.append((await AppBuildRepository(s).insert(build(project_id=p, status="planned" if i % 2 else "clarify")))["id"])
            await s.commit()
    async with pg() as s:
        repo = AppBuildRepository(s)
        page1, nxt = await repo.list(p, limit=2)
        assert [r["id"] for r in page1] == ids[::-1][:2] and nxt and "plan" not in page1[0]
        page2, nxt2 = await repo.list(p, limit=2, cursor=nxt)
        assert [r["id"] for r in page2] == ids[::-1][2:4]
        only, _ = await repo.list(p, status="planned")
        assert len(only) == 2
