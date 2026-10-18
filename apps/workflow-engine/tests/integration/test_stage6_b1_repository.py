"""Stage 6 B1: the proposal repository on the throwaway Postgres (rows, conditional transitions, the append-only event log)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from src.repositories.ai_proposal_repository import AIProposalRepository, UnknownColumn, new_proposal_id

from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


def row(**over) -> dict:
    base = {"id": new_proposal_id(), "project_id": "proj_default", "kind": "build", "status": "spec_draft", "actor": "Asha",
            "request": "r", "request_sha256": "a" * 64, "model": "gemini-3.5-flash", "provider": "gemini"}
    base.update(over)
    return base


async def put(sf, **over) -> dict:
    async with sf() as s:
        r = await AIProposalRepository(s).insert(row(**over))
        await s.commit()
    return r


def test_ids_have_the_documented_shape():
    pid = new_proposal_id()
    assert pid.startswith("p_") and len(pid) == 14 and new_proposal_id() != pid


async def test_insert_roundtrips_json_and_defaults(pg):
    spec = {"spec_version": 1, "title": "T", "tests": [{"id": "t_00000001"}]}
    r = await put(pg, spec=spec, base_definition={"nodes": []}, cost_usd=0.01234)
    async with pg() as s:
        got = await AIProposalRepository(s).get(r["id"])
    assert got["spec"] == spec and got["base_definition"] == {"nodes": []}
    assert got["ops_log"] == [] and got["test_change_requests"] == []
    assert got["llm_calls"] == 0 and got["accepted_failing"] is False and got["cost_usd"] == pytest.approx(0.01234)
    assert got["created_at"].tzinfo is not None and got["workflow_id"] is None


async def test_insert_rejects_unknown_columns_and_missing_required_and_bad_values(pg):
    async with pg() as s:
        repo = AIProposalRepository(s)
        with pytest.raises(UnknownColumn):
            await repo.insert(row(bogus=1))
        with pytest.raises(UnknownColumn):
            await repo.insert(row(kept_by="x"))        # only Keep/Undo may set these, via transition
        with pytest.raises(ValueError):
            await repo.insert(row(actor=None))
    async with pg() as s:
        with pytest.raises(Exception):                  # the CHECK constraints are the last line of defence
            await AIProposalRepository(s).insert(row(status="nonsense"))
    async with pg() as s:
        with pytest.raises(Exception):
            await AIProposalRepository(s).insert(row(kind="nonsense"))


async def test_get_hides_a_proposal_of_another_project(pg):
    r = await put(pg)
    async with pg() as s:
        repo = AIProposalRepository(s)
        assert (await repo.get(r["id"], "proj_default"))["id"] == r["id"]
        assert await repo.get(r["id"], "proj_other") is None
        assert await repo.get("p_missing") is None


async def test_transition_has_exactly_one_winner_among_concurrent_callers(pg):
    r = await put(pg, status="ready")

    async def go(i):
        async with pg() as s:
            won = await AIProposalRepository(s).transition(r["id"], ["ready"], "kept", {"kept_by": f"u{i}"})
            await s.commit()
            return won

    results = await asyncio.gather(*[go(i) for i in range(12)])
    winners = [w for w in results if w is not None]
    assert len(winners) == 1 and winners[0]["status"] == "kept"
    async with pg() as s:
        assert (await AIProposalRepository(s).get(r["id"]))["kept_by"] == winners[0]["kept_by"]


async def test_transition_respects_the_from_set_and_validates_the_target(pg):
    r = await put(pg, status="building")
    async with pg() as s:
        repo = AIProposalRepository(s)
        assert await repo.transition(r["id"], ["ready"], "kept") is None
        assert await repo.transition(r["id"], ["building", "spec_approved"], "ready", {"report": {"passed": True}}) is not None
        with pytest.raises(ValueError):
            await repo.transition(r["id"], ["ready"], "bogus")
        with pytest.raises(UnknownColumn):
            await repo.transition(r["id"], ["ready"], "kept", {"actor": "someone else"})   # the asker never changes
        await s.commit()
    async with pg() as s:
        got = await AIProposalRepository(s).get(r["id"])
    assert got["status"] == "ready" and got["report"] == {"passed": True}


async def test_update_fields_with_a_status_guard(pg):
    r = await put(pg, status="spec_draft")
    async with pg() as s:
        repo = AIProposalRepository(s)
        assert await repo.update_fields(r["id"], {"spec": {"a": 1}}, only_in=["spec_draft"]) is not None
        assert await repo.update_fields(r["id"], {"spec": {"a": 2}}, only_in=["ready"]) is None
        await s.commit()
    async with pg() as s:
        assert (await AIProposalRepository(s).get(r["id"]))["spec"] == {"a": 1}


async def test_usage_counters_are_atomic_under_concurrency(pg):
    r = await put(pg)

    async def go():
        async with pg() as s:
            await AIProposalRepository(s).add_usage(r["id"], llm_calls=1, input_tokens=100, output_tokens=10, duration_ms=5, cost_usd=0.001)
            await s.commit()

    await asyncio.gather(*[go() for _ in range(20)])
    async with pg() as s:
        got = await AIProposalRepository(s).get(r["id"])
    assert (got["llm_calls"], got["input_tokens"], got["output_tokens"], got["duration_ms"]) == (20, 2000, 200, 100)
    assert got["cost_usd"] == pytest.approx(0.02)


async def test_cost_stays_null_when_no_price_is_configured(pg):
    r = await put(pg)
    async with pg() as s:
        await AIProposalRepository(s).add_usage(r["id"], llm_calls=1, input_tokens=5)
        await s.commit()
    async with pg() as s:
        assert (await AIProposalRepository(s).get(r["id"]))["cost_usd"] is None


async def test_ops_log_appends_without_losing_concurrent_entries(pg):
    r = await put(pg)

    async def go(i):
        async with pg() as s:
            await AIProposalRepository(s).append_ops_log(r["id"], [{"round": 0, "op_index": i, "result": "ok"}])
            await s.commit()

    await asyncio.gather(*[go(i) for i in range(10)])
    async with pg() as s:
        log = (await AIProposalRepository(s).get(r["id"]))["ops_log"]
    assert sorted(e["op_index"] for e in log) == list(range(10))


async def test_event_seq_is_gapless_and_unique_under_concurrency(pg):
    r = await put(pg)

    async def go(i):
        async with pg() as s:
            seq = await AIProposalRepository(s).add_event(r["id"], "llm_call", {"i": i})
            await s.commit()
            return seq

    seqs = await asyncio.gather(*[go(i) for i in range(15)])
    assert sorted(seqs) == list(range(1, 16))
    async with pg() as s:
        items, last = await AIProposalRepository(s).list_events(r["id"])
        assert [e["seq"] for e in items] == list(range(1, 16)) and last == 15
        newer, last2 = await AIProposalRepository(s).list_events(r["id"], after=12)
        assert [e["seq"] for e in newer] == [13, 14, 15] and last2 == 15
        assert (await AIProposalRepository(s).list_events(r["id"], after=99))[0] == []


async def test_events_of_two_proposals_have_independent_sequences(pg):
    a, b = await put(pg), await put(pg)
    async with pg() as s:
        repo = AIProposalRepository(s)
        assert await repo.add_event(a["id"], "spec_requested", actor="Asha") == 1
        assert await repo.add_event(b["id"], "spec_requested") == 1
        assert await repo.add_event(a["id"], "spec_drafted", {"rules": 2}) == 2
        await s.commit()
        items, _ = await repo.list_events(a["id"])
    assert items[0]["actor"] == "Asha" and items[1]["payload"] == {"rules": 2}


async def test_the_event_log_is_append_only_in_the_repository():
    public = {n for n in dir(AIProposalRepository) if not n.startswith("_")}
    assert not {n for n in public if "event" in n and any(w in n for w in ("update", "delete", "remove", "edit", "set"))}
    assert {"add_event", "list_events"} <= public


async def test_events_go_with_their_proposal_only_by_cascade(pg):
    r = await put(pg)
    async with pg() as s:
        await AIProposalRepository(s).add_event(r["id"], "x")
        await s.commit()
    async with pg() as s:
        await s.execute(text("DELETE FROM ai_proposals WHERE id = :i"), {"i": r["id"]})
        await s.commit()
        assert (await s.execute(text("SELECT count(*) FROM ai_proposal_events WHERE proposal_id = :i"), {"i": r["id"]})).scalar() == 0


async def test_list_filters_orders_newest_first_and_pages_with_a_cursor(pg):
    project = "proj_default"
    ids = []
    for i in range(7):
        r = await put(pg, status="ready" if i % 2 == 0 else "failed", report={"passed": i % 2 == 0} if i < 6 else None)
        ids.append(r["id"])
    async with pg() as s:
        repo = AIProposalRepository(s)
        seen, cursor, pages = [], None, 0
        while True:
            page, cursor = await repo.list(project, limit=3, cursor=cursor)
            seen += [x["id"] for x in page]
            pages += 1
            if not cursor:
                break
        mine = [i for i in seen if i in ids]
        assert mine == list(reversed(ids)) and pages >= 3
        assert len(seen) == len(set(seen))
        ready, _ = await repo.list(project, status="ready", limit=200)
        assert {x["id"] for x in ready} >= {ids[0], ids[2], ids[4], ids[6]}
        by_id = {x["id"]: x for x in (await repo.list(project, limit=200))[0]}
        assert by_id[ids[0]]["passed"] is True and by_id[ids[1]]["passed"] is False and by_id[ids[6]]["passed"] is None
        assert "spec" not in by_id[ids[0]] and "proposed_definition" not in by_id[ids[0]]
        assert (await repo.list("proj_other"))[0] == []


async def test_stale_building_rows_fail_and_fresh_ones_do_not(pg):
    old = await put(pg, status="building")
    fresh = await put(pg, status="building")
    ready = await put(pg, status="ready")
    async with pg() as s:
        await s.execute(text("UPDATE ai_proposals SET updated_at = now() - interval '10 minutes' WHERE id IN (:a, :b)"), {"a": old["id"], "b": ready["id"]})
        await s.commit()
    async with pg() as s:
        failed = await AIProposalRepository(s).fail_stale_building(210)
        await s.commit()
    assert old["id"] in failed and fresh["id"] not in failed and ready["id"] not in failed
    async with pg() as s:
        got = await AIProposalRepository(s).get(old["id"])
    assert got["status"] == "failed" and got["error_code"] == "deadline"


async def test_the_models_match_the_migrated_tables(pg):
    from src.db.models import AIProposal, AIProposalEvent

    async with pg() as s:
        for model in (AIProposal, AIProposalEvent):
            rows = await s.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = 'workflow-app' AND table_name = :t"),
                                   {"t": model.__tablename__})
            assert {r[0] for r in rows} == {c.name for c in model.__table__.columns}, model.__tablename__


def test_the_migration_sorts_after_stage_five_and_has_up_and_down():
    from pathlib import Path

    d = Path(__file__).resolve().parents[2] / "src" / "db" / "migrations"
    names = sorted(p.name for p in d.glob("*.sql"))
    assert names.index("20261020120000_ai_proposals.sql") > names.index("20261019120000_interfaces_tasks.sql")
    sql = (d / "20261020120000_ai_proposals.sql").read_text()
    assert "-- migrate:up" in sql and "-- migrate:down" in sql
    assert sql.index("DROP TABLE IF EXISTS ai_proposal_events") < sql.index("DROP TABLE IF EXISTS ai_proposals;")
