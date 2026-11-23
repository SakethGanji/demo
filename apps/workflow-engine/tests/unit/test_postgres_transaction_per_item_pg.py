"""Postgres `transaction` runs once per input item, like `query` does.

It used to resolve the statements against item 0 only and emit one result: two managers' outcomes reached an audit
write and only the first one's row was ever written, with the run reporting success.
"""
from __future__ import annotations

from types import SimpleNamespace

import psycopg
import pytest

from src.engine.expression_engine import ExpressionEngine, expression_engine
from src.engine.types import NodeData, NodeDefinition
from src.nodes.integrations.postgres import PostgresNode

pytestmark = pytest.mark.anyio


def conninfo(db) -> str:
    e = db.env()
    return f"postgresql://{e['WORKFLOW_DB_USER']}:{e['WORKFLOW_DB_PASSWORD']}@{e['WORKFLOW_DB_HOST']}:{e['WORKFLOW_DB_PORT']}/{db.name}"


@pytest.fixture
async def table(pg_dbname):
    url = conninfo(pg_dbname)
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        await c.execute("DROP TABLE IF EXISTS public.tx_audit")
        await c.execute("CREATE TABLE public.tx_audit (who text NOT NULL, action text NOT NULL)")
    yield url
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:
        await c.execute("DROP TABLE IF EXISTS public.tx_audit")


async def rows(url):
    async with await psycopg.AsyncConnection.connect(url) as c:
        cur = await c.execute("SELECT who, action FROM public.tx_audit ORDER BY who")
        return await cur.fetchall()


def run(url, items, statements):
    node = PostgresNode()
    nd = NodeDefinition(name="Write audit", type="Postgres", parameters={"operation": "transaction", "statements": statements})
    ctx = SimpleNamespace(execution_id="ex", node_states={})
    data = [NodeData(json=i) for i in items]
    return node._exec_transaction(url, nd, data, data, ctx, expression_engine, ExpressionEngine)


STMTS = [{"query": "INSERT INTO public.tx_audit (who, action) VALUES (%s, %s)", "params": ["{{ $json.who }}", "{{ $json.action }}"]}]


async def test_each_item_gets_its_own_transaction(table):
    res = await run(table, [{"who": "m1", "action": "revoked_some"}, {"who": "m2", "action": "confirmed"}], STMTS)
    out = res.outputs["main"]
    assert [o.json["success"] for o in out] == [True, True]
    assert await rows(table) == [("m1", "revoked_some"), ("m2", "confirmed")]


async def test_a_failing_item_rolls_back_only_itself(table):
    # m2's action is NULL: its insert fails and rolls back; m1 and m3 are still written
    res = await run(table, [{"who": "m1", "action": "a"}, {"who": "m2", "action": None}, {"who": "m3", "action": "c"}], STMTS)
    out = res.outputs["main"]
    assert [o.json["success"] for o in out] == [True, False, True]
    assert out[1].json["rolledBack"] is True
    assert await rows(table) == [("m1", "a"), ("m3", "c")]
