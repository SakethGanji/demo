"""The one-shot builder tells the model about the project's external databases (schema summary + how to query them)."""
from __future__ import annotations

import asyncio

from src.services import connection_service
from src.services import oneshot_builder as ob

SCHEMA = "shop.public.orders (~2000): id int [pk], status order_status, total numeric, created_at timestamptz"


def _run(coro):
    return asyncio.run(coro)


def test_schema_summary_reaches_every_build_and_change_call(monkeypatch):
    seen = {}

    async def fake(project_id, tables=None, keys=None, **_):
        seen["project"], seen["tables"] = project_id, tables
        return SCHEMA

    monkeypatch.setattr(connection_service, "connections_context", fake)

    async def go():
        await ob._load_db_context("proj_1", {"summary": "dashboard of orders by status"})
        return "\n".join(ob.extra_context(None, None))

    text = _run(go())
    assert seen["project"] == "proj_1" and "orders" in seen["tables"]
    assert SCHEMA in text and 'studio.connection("key").query' in text and "never create tables" in text


def test_no_connections_adds_nothing(monkeypatch):
    async def fake(*_a, **_k):
        return ""

    monkeypatch.setattr(connection_service, "connections_context", fake)

    async def go():
        await ob._load_db_context("proj_1")
        return ob.extra_context(None, None)

    assert _run(go()) == []


def test_schema_lookup_failure_never_fails_the_build(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(connection_service, "connections_context", boom)

    async def go():
        await ob._load_db_context("proj_1")
        return ob.extra_context(None, None)

    assert _run(go()) == []
