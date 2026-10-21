"""Scenario auto-tests act on the app's real draft data; afterwards the rows they created, changed or deleted are put back,
so the preview never shows "Scenario suite A" leftovers."""
from __future__ import annotations

import asyncio

from .journeys._helpers import needs_bwrap, new_project


def _factory(test_db):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    env = test_db.env()
    e = create_async_engine(f"postgresql+asyncpg://{env['WORKFLOW_DB_USER']}:{env['WORKFLOW_DB_PASSWORD']}"
                            f"@{env['WORKFLOW_DB_HOST']}:{env['WORKFLOW_DB_PORT']}/{test_db.name}",
                            connect_args={"server_settings": {"search_path": '"workflow-app"'}})
    return e, sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


def test_scenario_rows_are_rolled_back(api, test_db):
    from src.services import app_autotest

    pid, _ = new_project(api, "rb")
    other, _ = new_project(api, "rbo")
    cols = [{"name": "name", "type": "string"}]
    tid = api.post(f"/projects/{pid}/tables", {"name": "suites", "columns": cols}).json()["id"]
    oid = api.post(f"/projects/{other}/tables", {"name": "suites", "columns": cols}).json()["id"]
    assert api.post(f"/projects/{pid}/tables/{tid}/rows", {"rows": [{"name": "Auth"}, {"name": "Billing"}]}).status_code == 201
    before = sorted(test_db.query("select r.id, r.data::text from data_table_rows r where r.table_id = %s", (tid,)))
    keep, gone = before[0][0], before[1][0]

    async def go() -> None:
        e, f = _factory(test_db)
        try:
            snap = await app_autotest._snapshot(f, pid)
            # what a scenario run does: create, edit and delete rows; another project writes at the same time
            test_db.query("insert into data_table_rows (table_id, data) values (%s, '{\"name\": \"Scenario suite A\"}') returning id", (tid,))
            test_db.query("insert into data_table_rows (table_id, data) values (%s, '{\"name\": \"other\"}') returning id", (oid,))
            test_db.query("update data_table_rows set data = '{\"name\": \"renamed\"}' where id = %s returning id", (keep,))
            test_db.query("delete from data_table_rows where id = %s returning id", (gone,))
            await app_autotest._restore(f, pid, snap)
        finally:
            await e.dispose()

    asyncio.run(go())
    assert sorted(test_db.query("select r.id, r.data::text from data_table_rows r where r.table_id = %s", (tid,))) == before
    assert test_db.query("select count(*) from data_table_rows where table_id = %s", (oid,))[0][0] == 1


CSV_MODULE = '''from studio import api, Response

@api.get("/export.csv")
def export(req):
    return Response(200, '"id","name"\\r\\n"1","Ann"\\r\\n', {"Content-Type": "text/csv; charset=utf-8",
                                                          "Content-Disposition": 'attachment; filename="x.csv"'})


@api.get("/plain")
def plain(req):
    return {"ok": True}
'''


@needs_bwrap
def test_a_route_that_sets_its_own_content_type_is_not_json_encoded(api):
    pid, slug = new_project(api, "csv")
    assert api.post(f"/projects/{pid}/modules", {"name": "exports", "source": CSV_MODULE}).status_code in (200, 201)
    r = api.raw("GET", f"/api/p/{slug}/export.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv") and r.headers["content-disposition"].endswith('"x.csv"')
    assert r.text == '"id","name"\r\n"1","Ann"\r\n'
    j = api.raw("GET", f"/api/p/{slug}/plain")
    assert j.headers["content-type"].startswith("application/json") and j.json() == {"ok": True}
