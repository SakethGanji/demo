"""External connections: SQL screening, masking, prompt context size/format, plan request shapes (no engine needed)."""

from __future__ import annotations

import asyncio

import pytest

from src.services import external_db as xdb
from src.services.connection_service import render_context, slugify
from tests.fixtures.shopdemo import ensure_shopdemo


@pytest.mark.parametrize("sql", ["select 1", "SELECT * FROM t;", "with a as (select 1) select * from a",
                                 "select 'insert into x' as s, \"update\" from t -- delete", "select updated_at from t"])
def test_check_select_allows_reads(sql):
    assert xdb.check_select(sql)


@pytest.mark.parametrize("sql", ["insert into t values (1)", "update t set a=1", "delete from t", "drop table t", "create table t(a int)",
                                 "with x as (insert into t values (1) returning *) select * from x", "select 1; select 2",
                                 "select * into t2 from t", "select pg_read_file('/etc/passwd')", "set role x", "", "  ", None,
                                 "select dblink('x','y')"])
def test_check_select_refuses_everything_else(sql):
    with pytest.raises(xdb.ExternalDbError) as e:
        xdb.check_select(sql)
    assert e.value.code == "query_not_allowed"


def test_limit_is_clamped():
    assert xdb.clamp_limit(None) == 100 and xdb.clamp_limit(9999) == 500 and xdb.clamp_limit(0) == 1
    assert xdb.clamp_limit(9999, xdb.SDK_MAX_LIMIT) == 5000


def test_sensitive_columns():
    for c in ("email", "Phone", "ssn", "password_hash", "api_token", "billing_address", "dob", "first_name", "customer_name"):
        assert xdb.is_sensitive("orders", c), c
    assert xdb.is_sensitive("customers", "name") and not xdb.is_sensitive("products", "name")
    assert not xdb.is_sensitive("orders", "status") and not xdb.is_sensitive("orders", "channel")


def test_slugify():
    assert slugify("Shop Demo!") == "shop_demo" and slugify("123") == "db_123" and slugify("") == "db"


@pytest.fixture(scope="module")
def snapshot():
    return asyncio.run(xdb.introspect(ensure_shopdemo()))


def test_context_format_and_size(snapshot):
    ctx = render_context([{"key": "shop", "tables": snapshot["tables"]}])
    lines = ctx.splitlines()
    assert lines[0].startswith("External databases") and len(ctx.encode()) <= 6000
    orders = next(line for line in lines if line.startswith("shop.public.orders "))
    assert "(~2000 rows)" in orders and "customer_id integer fk→customers.id" in orders and "id integer pk" in orders
    assert "channel text {mobile,store,web}" in orders
    # an enum lists every label in declared order, however many rows or values there are
    assert "status order_status {pending,paid,shipped,delivered,cancelled,refunded}" in orders
    assert "email" in next(line for line in lines if line.startswith("shop.public.customers ")) and "@example.com" not in ctx


def test_context_prioritises_named_tables_and_respects_budget(snapshot):
    big = [{"schema": "public", "name": f"t{i:03d}", "kind": "table", "row_estimate": i,
            "columns": [{"name": f"c{j}", "type": "text", "nullable": True, "pk": False, "fk": None, "samples": []} for j in range(12)]}
           for i in range(100)] + snapshot["tables"]
    ctx = render_context([{"key": "shop", "tables": big}], tables=["refunds", "shop.orders"])
    lines = ctx.splitlines()
    assert len(ctx.encode()) <= 6000 and "more tables" in lines[-1]
    assert lines[1].startswith(("shop.public.refunds", "shop.public.orders")) and lines[2].startswith(("shop.public.refunds", "shop.public.orders"))
    assert render_context([]) == ""


def test_plan_requests_accept_sources():
    from src.routes.project_plan import PlanRevise
    from src.services.plan_clarify import PlanDraftRequest

    r = PlanDraftRequest.model_validate({"prompt": "dashboard", "sources": [{"connection": "shop", "tables": ["orders"]}]})
    assert r.sources[0].connection == "shop" and r.sources[0].tables == ["orders"]
    assert PlanDraftRequest.model_validate({"prompt": "x"}).sources is None
    assert PlanRevise.model_validate({"prompt": "x", "sources": [{"connection": "shop"}]}).sources[0].tables is None
    with pytest.raises(Exception):
        PlanDraftRequest.model_validate({"prompt": "x", "sources": [{"connection": "shop", "bogus": 1}]})


def test_sdk_reference_lists_connection():
    from src.sdk.generate import reference

    names = {e["name"] for e in reference()["entries"]}
    assert {"studio.connection", "studio.connection(key).query", "studio.connection(key).schema"} <= names
    from src.services.backend_templates import prompts

    assert "studio.connection(" in prompts.SDK_REFERENCE
