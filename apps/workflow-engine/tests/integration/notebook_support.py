"""Fixtures for the notebook API tests (BUILD-PLAN §3.1).

* ``nb_engine`` / ``nb_api``: an engine on the session's ``workflows_test_*`` DB with its own notebook storage dir.
* ``bank_db``: a throwaway ``workflows_test_nbdata_<hex>`` database with ``bank.wires`` (3 rows), ``bank.big``
  (20 000 rows) and ``bank.limits``; dropped at the end.
* ``nb_project``: a project with a read-only connection ``bank`` and a writable one ``bank_rw`` on that DB.
* Helpers: ``new_notebook``, ``add``, ``run_and_wait``, ``wait_run``.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Iterator
from urllib.parse import quote

import pytest

from .conftest import DB_HOST, DB_PASSWORD, DB_PORT, DB_USER, EngineServer, StudioClient, _admin_conn, uniq

BANK_SQL = """
CREATE SCHEMA bank;
CREATE TABLE bank.wires (wire_id TEXT PRIMARY KEY, amount NUMERIC NOT NULL, status TEXT NOT NULL,
                         created_at TIMESTAMP NOT NULL);
INSERT INTO bank.wires VALUES ('W1', 100.50, 'stuck', now() - interval '3 days'),
                              ('W2', 2500, 'stuck', now() - interval '2 hours'),
                              ('W3', 75, 'sent', now() - interval '5 days');
CREATE TABLE bank.big (id INT PRIMARY KEY, label TEXT NOT NULL);
INSERT INTO bank.big SELECT g, 'row ' || g FROM generate_series(1, 20000) g;
CREATE TABLE bank.limits (account TEXT PRIMARY KEY, daily_limit NUMERIC NOT NULL);
INSERT INTO bank.limits VALUES ('A1', 1000), ('A2', 5000);
"""


class BankDb:
    __test__ = False

    def __init__(self) -> None:
        self.name = f"workflows_test_nbdata_{uuid.uuid4().hex[:8]}"
        if not self.name.startswith("workflows_test_"):
            raise RuntimeError("refusing a non-test database name")

    @property
    def url(self) -> str:
        return f"postgresql://{quote(DB_USER)}:{quote(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{self.name}"

    def _conn(self):
        import psycopg2

        return psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname=self.name)

    def create(self) -> None:
        a = _admin_conn()
        with a.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{self.name}"')
        a.close()
        c = self._conn()
        with c.cursor() as cur:
            cur.execute(BANK_SQL)
        c.commit()
        c.close()

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        c = self._conn()
        try:
            with c.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
            c.commit()
            return rows
        finally:
            c.close()

    def drop(self) -> None:
        if not self.name.startswith("workflows_test_nbdata_"):
            raise RuntimeError("refusing to drop a non-test database")
        a = _admin_conn()
        with a.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        a.close()


@pytest.fixture(scope="module")
def bank_db() -> Iterator[BankDb]:
    db = BankDb()
    db.create()
    try:
        yield db
    finally:
        db.drop()


@pytest.fixture(scope="module")
def nb_engine(test_db, upstream, tmp_path_factory) -> Iterator[EngineServer]:
    store = tmp_path_factory.mktemp("nb-store")
    srv = EngineServer(test_db, extra_env={"WORKFLOW_NOTEBOOK_STORAGE_DIR": str(store)}, label="-nb")
    srv.storage_dir = store  # type: ignore[attr-defined]
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture(scope="module")
def nb_api(nb_engine) -> StudioClient:
    return StudioClient(nb_engine.base_url)


@pytest.fixture(scope="module")
def nb_project(nb_api, bank_db) -> dict[str, Any]:
    p = nb_api.post("/projects", {"name": uniq("nb")}).json()
    ro = nb_api.post(f"/projects/{p['id']}/connections", {"name": "bank", "kind": "postgres", "url": bank_db.url})
    assert ro.status_code == 201, ro.text
    rw = nb_api.post(f"/projects/{p['id']}/connections",
                     {"name": "bank_rw", "kind": "postgres", "url": bank_db.url, "readonly": False})
    assert rw.status_code == 201, rw.text
    assert ro.json()["readonly"] is True and rw.json()["readonly"] is False
    return {**p, "ro": ro.json(), "rw": rw.json()}


def new_notebook(api: StudioClient, project: dict[str, Any], **kw: Any) -> dict[str, Any]:
    r = api.post(f"/projects/{project['id']}/notebooks", {"title": uniq("nb"), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def add(api: StudioClient, nb: dict[str, Any] | str, kind: str, spec: dict[str, Any], expect: int = 201,
        **kw: Any) -> dict[str, Any]:
    nid = nb if isinstance(nb, str) else nb["id"]
    r = api.post(f"/notebooks/{nid}/cells", {"kind": kind, "spec": spec, **kw})
    assert r.status_code == expect, r.text
    return r.json()


def wait_run(api: StudioClient, run_id: str, timeout: float = 60.0,
             until: tuple[str, ...] = ("done", "failed", "stopped_at_write", "needs_you", "cancelled", "interrupted"),
             ) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api.get(f"/notebook-runs/{run_id}").json()
        if r["status"] in until:
            nb = api.get(f"/notebooks/{r['notebook_id']}").json()
            if r["status"] == "needs_you" or nb["active_run_id"] != run_id:
                return r
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish: {api.get(f'/notebook-runs/{run_id}').text}")


def run_and_wait(api: StudioClient, nb: dict[str, Any] | str, **body: Any) -> dict[str, Any]:
    nid = nb if isinstance(nb, str) else nb["id"]
    r = api.post(f"/notebooks/{nid}/runs", body)
    assert r.status_code == 202, r.text
    return wait_run(api, r.json()["id"])


def result_of(run: dict[str, Any], cell: dict[str, Any], row_index: int | None = None) -> dict[str, Any] | None:
    for r in run["results"]:
        if r["cell_id"] == cell["id"] and r["row_index"] == row_index:
            return r
    return None


def cell_in(nb: dict[str, Any], cell_id: str) -> dict[str, Any]:
    for c in nb["cells"]:
        if c["id"] == cell_id:
            return c
        for ch in c.get("children") or []:
            if ch["id"] == cell_id:
                return ch
    raise KeyError(cell_id)
