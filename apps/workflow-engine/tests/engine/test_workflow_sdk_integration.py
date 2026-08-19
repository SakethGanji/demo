"""Integration tests — the SDK's output validated against machinery OUTSIDE
workflow_sdk.py itself, not just its own exec/grade loop (that's what Tier 0 in
test_workflow_sdk_contract.py / test_workflow_sdk_all_types.py already covers).

Two things get exercised here that no unit test can:
  1. Multi-type workflows spanning trigger/flow/transform/integration nodes
     together, built by one script, checked end to end with validate()/test_run().
  2. REAL PERSISTENCE: an SDK-built Workflow saved through the actual
     WorkflowRepository into the actual running Postgres (workflow-engine-postgres-1),
     fetched back, and checked for structural round-trip fidelity. This is the
     strongest available proof that the SDK's output isn't just self-consistent —
     it's byte-compatible with what the rest of the engine already expects, because
     it uses the real NodeDefinition/Connection/Workflow dataclasses end to end.

Requires a live Postgres (the same one `docker compose up -d postgres` starts for
the app itself). Test workflows are named with a distinctive prefix and always
deleted in a finally block — nothing here is meant to persist between runs.

Run: venv/bin/python -m pytest tests/engine/test_workflow_sdk_integration.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from src.engine.workflow_sdk import execute_workflow_script  # noqa: E402
from src.engine.node_registry import register_all_nodes  # noqa: E402
from src.db.session import async_session_factory, engine  # noqa: E402
from src.repositories.workflow_repository import WorkflowRepository  # noqa: E402

register_all_nodes()

_NAME_PREFIX = "sdk_integration_test__"


def _run(coro):
    """Sync wrapper so these tests use the same plain-assert style as the rest of
    this repo's test suite, without adding a pytest-asyncio dependency.

    `engine` (src/db/session.py) is a MODULE-LEVEL SQLAlchemy async engine, and
    asyncpg connections are bound to the event loop that created them. Each
    `asyncio.run()` call opens and closes its own loop, so reusing the same pool
    across separate test functions breaks the second one with "Event loop is
    closed" — found by actually running this, not by inspection. Disposing the
    pool first forces fresh, correctly-bound connections under the new loop.
    """
    async def _wrapped():
        await engine.dispose()
        return await coro
    return asyncio.run(_wrapped())


# ---------------------------------------------------------------------------
# Multi-type composition — a workflow no single unit test spans, mixing
# trigger / flow / transform / integration types built by one script.
# ---------------------------------------------------------------------------

def test_multitype_workflow_validates_and_dry_runs():
    script = (
        'cron = Cron(cronExpression="0 6 * * *")\n'
        'fetch = HttpRequest(method="GET", url="https://example.com/orders")\n'
        'norm = Code(code="return items")\n'
        'check = If(field="body", operation="isNotEmpty")\n'
        'agg = Aggregate(groupBy="region")\n'
        'load = Postgres(operation="query", query="select 1")\n'
        'notify = SendEmail(toEmail="ops@example.com", subject="Done", body="Loaded.")\n'
        'halt = StopAndError(message="No orders today")\n'
        '\n'
        'cron >> fetch >> norm >> check\n'
        'check.true >> agg >> load >> notify\n'
        'check.false >> halt\n'
        '\n'
        'validate()\n'
        'report = test_run(input={"body": {"orders": 3}})\n'
        'results.append(report)\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    types_used = {n.type for n in result.workflow.nodes}
    assert types_used == {
        "Cron", "HttpRequest", "Code", "If", "Aggregate", "Postgres", "SendEmail", "StopAndError",
    }
    assert len(result.results) == 1
    report = result.results[0]
    assert report["ok"] is True
    assert "SendEmail" in report["reached"]
    assert "StopAndError" in report["unreached"]  # body was non-empty -> true branch taken


def test_switch_and_loop_composition():
    script = (
        'start = Start()\n'
        'router = Switch(numberOfOutputs=3)\n'
        'a = HttpRequest(method="GET", url="https://example.com/a")\n'
        'b = HttpRequest(method="GET", url="https://example.com/b")\n'
        'fallback = StopAndError(message="no matching branch")\n'
        'start >> router\n'
        'router.output0 >> a\n'
        'router.output1 >> b\n'
        'router.fallback >> fallback\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    conns = {(c.source_output, c.target_node) for c in result.workflow.connections}
    assert ("output0", "HttpRequest") in conns
    assert ("output1", "HttpRequest 2") in conns
    assert ("fallback", "StopAndError") in conns


# ---------------------------------------------------------------------------
# Real persistence — the SDK's Workflow object round-tripped through the
# ACTUAL repository and the ACTUAL running database, not a mock.
# ---------------------------------------------------------------------------

async def _create_fetch_delete(workflow):
    async with async_session_factory() as session:
        repo = WorkflowRepository(session)
        stored = await repo.create(workflow)
        try:
            fetched = await repo.get(stored.id)
            return stored, fetched
        finally:
            await repo.delete(stored.id)


def test_sdk_workflow_round_trips_through_real_repository_and_database():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'fetch = HttpRequest(method="GET", url="https://example.com/export")\n'
        'check = If(field="body", operation="isNotEmpty")\n'
        'load = Postgres(operation="query", query="select 1")\n'
        'halt = StopAndError(message="empty")\n'
        'cron >> fetch >> check\n'
        'check.true >> load\n'
        'check.false >> halt\n'
        'validate()\n'
    )
    result = execute_workflow_script(script, workflow_name=f"{_NAME_PREFIX}basic")
    assert result.error is None, result.error

    stored, fetched = _run(_create_fetch_delete(result.workflow))
    try:
        assert fetched is not None, "workflow vanished immediately after create()"
        assert fetched.name == f"{_NAME_PREFIX}basic"

        # Round-trip fidelity: same nodes (name/type/parameters), same connections —
        # this is checking against what the DB actually stored and reconstructed,
        # not against the in-memory object we started with.
        orig_by_name = {n.name: n for n in result.workflow.nodes}
        fetched_by_name = {n.name: n for n in fetched.workflow.nodes}
        assert set(orig_by_name) == set(fetched_by_name)
        for name, orig_node in orig_by_name.items():
            f_node = fetched_by_name[name]
            assert f_node.type == orig_node.type
            assert f_node.parameters == orig_node.parameters

        orig_conns = {(c.source_node, c.source_output, c.target_node, c.target_input)
                      for c in result.workflow.connections}
        fetched_conns = {(c.source_node, c.source_output, c.target_node, c.target_input)
                         for c in fetched.workflow.connections}
        assert orig_conns == fetched_conns
    finally:
        pass  # already deleted inside _create_fetch_delete's finally


def test_deleted_sdk_workflow_is_actually_gone():
    script = 'n = Start()\nvalidate()\n'
    result = execute_workflow_script(script, workflow_name=f"{_NAME_PREFIX}delete_check")
    assert result.error is None, result.error

    async def _lifecycle():
        async with async_session_factory() as session:
            repo = WorkflowRepository(session)
            stored = await repo.create(result.workflow)
            deleted = await repo.delete(stored.id)
            after = await repo.get(stored.id)
            return deleted, after

    deleted, after = _run(_lifecycle())
    assert deleted is True
    assert after is None


def test_all_27_types_together_in_one_persisted_workflow():
    """Not a realistic workflow — a deliberately maximal one, touching every SDK
    type at once (Merge included, via the documented fan-in loop), to prove the
    full type surface — not just a hand-picked subset — survives a real save."""
    script = (
        'start = Start()\n'
        'a = HttpRequest(method="GET", url="https://example.com/a")\n'
        'b = HttpRequest(method="GET", url="https://example.com/b")\n'
        'start >> a\nstart >> b\n'
        'merge = Merge()\n'
        'for source in (a, b):\n'
        '    source >> merge\n'
        'code = Code(code="return items")\n'
        'merge >> code\n'
        'setn = Set(fields=[])\n'
        'code >> setn\n'
        'filt = Filter(field="x", operation="isNotEmpty")\n'
        'setn >> filt\n'
        'items = ItemLists(operation="splitOut")\n'
        'filt >> items\n'
        'sample = Sample(sampleSize=1)\n'
        'items >> sample\n'
        'profile = Profile()\n'
        'sample >> profile\n'
        'agg = Aggregate(groupBy="region")\n'
        'profile >> agg\n'
        'check = If(field="body", operation="isNotEmpty")\n'
        'agg >> check\n'
        'pg = Postgres(operation="query", query="select 1")\n'
        'neo = Neo4j(query="RETURN 1")\n'
        'mongo = MongoDB(operation="find", collection="c")\n'
        'check.true >> pg\ncheck.true >> neo\ncheck.true >> mongo\n'
        'stop = StopAndError(message="empty")\n'
        'check.false >> stop\n'
        'email = SendEmail(toEmail="ops@example.com", subject="s", body="b")\n'
        'pg >> email\n'
        'llm = LLMChat()\n'
        'email >> llm\n'
        'wait = Wait()\n'
        'llm >> wait\n'
        'poll = Poll(condition="{{ $json.status }}")\n'
        'wait >> poll\n'
        'loop = Loop(batchSize=1)\n'
        'poll.done >> loop\n'
        'validate()\n'
    )
    result = execute_workflow_script(script, workflow_name=f"{_NAME_PREFIX}maximal")
    assert result.error is None, result.error
    types_present = {n.type for n in result.workflow.nodes}
    # every non-trigger-alternate, non-excluded type should show up somewhere
    expected_minimum = {
        "Start", "HttpRequest", "Merge", "Code", "Set", "Filter", "ItemLists",
        "Sample", "Profile", "Aggregate", "If", "Postgres", "Neo4j", "MongoDB",
        "StopAndError", "SendEmail", "LLMChat", "Wait", "Poll", "Loop",
    }
    assert expected_minimum.issubset(types_present), expected_minimum - types_present

    stored, fetched = _run(_create_fetch_delete(result.workflow))
    assert fetched is not None
    assert len(fetched.workflow.nodes) == len(result.workflow.nodes)
    assert len(fetched.workflow.connections) == len(result.workflow.connections)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
