"""Pytest fixtures — in-process ASGI client against the real app + Postgres.

These are integration tests: they require the ``accelerator`` Postgres schema
(run migrations) and, for the ``s3`` storage param, the MinIO container.
Identity is the POC ``X-User-Id`` header; the seeded System superuser
(DEFAULT_USER_ID) plays the admin role, so no bootstrap is needed.

Architecture (see HANDOFF.md):
- ``_db_cleanup`` (autouse) clears every mutable domain table before each test,
  so tests can assert exact counts. Seeded auth rows and the append-only
  ``audit_log`` are preserved. Skipped for ``tests/unit/`` (no DB there).
- ``_fresh_environment`` (session, autouse) drops what ``_db_cleanup``
  deliberately preserves — test-created users/teams, the audit trail, and the
  blobs in both storage backends. None of that is reachable by a later test,
  but all of it made the suite slower every time it ran.
- ``storage_backend`` parametrizes ``client`` over ``local`` and ``s3``
  (MinIO), so the whole integration suite runs against both backends.
- Workbook factories and API helpers used by several files live here.
"""

from __future__ import annotations

import os
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import text

# Auth must be on for the RBAC tests to be meaningful.
os.environ.setdefault("ACCELERATOR_AUTH_ENABLED", "true")

from app.infra.config import settings  # noqa: E402
from app.infra.db.postgres.session import engine  # noqa: E402
from app.infra.db.storage import (  # noqa: E402
    ARTIFACT_ROOT,
    LocalStorageBackend,
    S3StorageBackend,
    get_storage,
    init_storage,
)
from app.main import app  # noqa: E402

DEFAULT_TEAM_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"  # seeded System superuser
SAMPLE_CSV = os.environ.get(
    "TEST_SAMPLE_CSV",
    "/home/saketh/Projects/playground/work/demo/brands_accountmanagement_sample_dataset.csv",
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The S3 prefix is env-overridable so several suite runs can share one MinIO
# without seeing each other's blobs. Endpoints that scan a whole prefix
# (GET /storage/usage, the GC sweeps) otherwise fail spuriously when a
# concurrent run's objects land under the same prefix. Default is unchanged,
# so a normal single run behaves exactly as before.
MINIO = {
    "bucket": "analytics",
    "prefix": os.environ.get("TEST_S3_PREFIX", "tests"),
    "endpoint_url": "http://localhost:9000",
    "region": "us-east-1",
    "access_key_id": "minioadmin",
    "secret_access_key": "minioadmin",
    "force_path_style": True,
}


# ---------------------------------------------------------------------------
# DB isolation — clear the mutable domain tables before every test
# ---------------------------------------------------------------------------

# The full mutable domain set. Preserved: users/teams/team_members/
# refresh_tokens (seeded superuser + Default team), audit_log (append-only
# trigger), schema_migrations. Every FK into this set comes from within it, so
# clearing it never reaches the preserved tables (verified via
# information_schema).
#
# DELETE, not TRUNCATE. TRUNCATE recreates each table's file and its indexes,
# which costs ~7 ms per table regardless of how empty it is — 190 ms for this
# list, on EVERY test, which was ~3.5 minutes of a 12-minute run. DELETE over
# the same (almost always empty) tables is ~11 ms.
#
# The order below is child→parent, but DELETE does not actually depend on it:
# datasets and dataset_versions reference each other (a genuine FK cycle, which
# is why TRUNCATE ... CASCADE was used originally), and every inbound FK in the
# set is ON DELETE CASCADE or SET NULL, so any order resolves. The order is
# kept because it documents the dependency shape.
_TRUNCATE = [
    "webhook_deliveries", "webhook_subscriptions",
    "dataset_relationships",
    "transformation_runs", "transformation_definitions",
    "dataset_column_metadata",
    "chart_definitions", "profile_insights", "profile_runs", "dataset_views",
    "validation_rule_results", "validation_runs", "quality_rules",
    "analytics_runs", "analytics_definitions", "artifacts",
    "dataset_lineage", "dataset_favorites", "dataset_sheet_metadata",
    "dataset_tag_history", "dataset_version_tags", "dataset_version_sheets",
    "dataset_sheets", "dataset_versions", "datasets", "jobs",
]


async def _clear_domain_tables() -> None:
    async with engine.begin() as conn:
        for table in _TRUNCATE:
            await conn.exec_driver_sql(f"DELETE FROM accelerator.{table}")


@pytest_asyncio.fixture(autouse=True)
async def _db_cleanup(request):
    if "unit" in request.node.path.parts:  # tests/unit/ never touches Postgres
        yield
        return
    await _clear_domain_tables()
    yield


# ---------------------------------------------------------------------------
# Session reset — drop what the per-test cleanup deliberately preserves
# ---------------------------------------------------------------------------

# `_db_cleanup` keeps users/teams/team_members (the seeded identity) and
# audit_log (append-only). That is right per test, but every run leaves behind
# the users and teams `create_team_user` made, every audited request, and every
# blob written to either backend. Nothing ever removed them, so the suite got
# measurably slower each time it ran: 7k users, 5.7k teams, 79k audit rows and
# 24k objects had accumulated, and clearing them cut an identity-heavy file
# from 8s to 5s.
#
# None of this is test isolation — no test can reach the previous run's rows.
# It is purely keeping the fixture cost flat over time.


async def _reset_identity() -> None:
    # Domain rows from a prior session's last test (never cleaned up — the
    # per-test fixture runs BEFORE each test, not after) still reference
    # non-default teams/users. Clear them first: deleting teams directly hit a
    # real FK gap (dataset_version_sheets.logical_sheet_id -> dataset_sheets is
    # the only logical_sheet_id FK in the schema with no ON DELETE action — every
    # sibling is CASCADE or SET NULL). Ordinary dataset deletion never trips
    # this because the whole subtree cascades together in one statement; a
    # bare team-scoped DELETE across unrelated leftover datasets can.
    await _clear_domain_tables()

    async with engine.begin() as conn:
        # TRUNCATE, not DELETE: the append-only trigger rejects row deletes,
        # and it fires per row, not on TRUNCATE.
        await conn.exec_driver_sql("TRUNCATE accelerator.audit_log")
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM accelerator.team_members WHERE team_id <> :t"),
            {"t": DEFAULT_TEAM_ID})
        await conn.execute(
            text("DELETE FROM accelerator.teams WHERE id <> :t"),
            {"t": DEFAULT_TEAM_ID})
        await conn.execute(
            text("DELETE FROM accelerator.users WHERE id <> :u"),
            {"u": DEFAULT_USER_ID})


def _purge_storage() -> None:
    """Drop every blob both backends hold, so bucket scans stay cheap."""
    for configure in (_use_local, _use_s3):
        try:
            configure()
            backend = get_storage()
            for prefix in ("datasets", ARTIFACT_ROOT):
                backend.delete_prefix(prefix)
        except Exception as e:  # a backend being down must not block the run
            print(f"  (storage purge skipped: {type(e).__name__}: {e})")
    _use_local()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _fresh_environment():
    """Runs once, before any test. Set ACCELERATOR_KEEP_TEST_STATE to skip.

    Session-scoped and async so it shares the tests' event loop — pytest.ini
    sets `asyncio_default_fixture_loop_scope = session`. Running it on its own
    loop leaves the engine's pooled connections bound to a dead one.
    """
    if not os.environ.get("ACCELERATOR_KEEP_TEST_STATE"):
        await _reset_identity()
        _purge_storage()
    yield


# ---------------------------------------------------------------------------
# Storage backend parametrization — the whole suite runs on local AND s3
# ---------------------------------------------------------------------------


def _use_local() -> None:
    settings.storage_backend = "local"
    init_storage(LocalStorageBackend(base_dir=settings.storage_dir))


def _use_s3() -> None:
    # load_data/duckdb_s3_statements read settings at call time, so mutating
    # the (plain pydantic) instance switches every consumer at once.
    settings.storage_backend = "s3"
    settings.s3_bucket = MINIO["bucket"]
    settings.s3_prefix = MINIO["prefix"]
    settings.s3_endpoint_url = MINIO["endpoint_url"]
    settings.s3_region = MINIO["region"]
    settings.s3_access_key_id = MINIO["access_key_id"]
    settings.s3_secret_access_key = MINIO["secret_access_key"]
    settings.s3_force_path_style = MINIO["force_path_style"]
    init_storage(S3StorageBackend(**MINIO))


@pytest_asyncio.fixture(params=["local", "s3"])
async def storage_backend(request):
    """Configure the storage singleton + settings for the requested backend."""
    _use_s3() if request.param == "s3" else _use_local()
    yield request.param
    _use_local()  # restore the default so non-param code isn't affected


@pytest_asyncio.fixture
async def client(storage_backend):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def auth(user_id: str) -> dict:
    """Identity headers for the POC header-auth scheme."""
    return {"X-User-Id": user_id}


def rid() -> str:
    """A unique suffix for test-created resources."""
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
def admin_id() -> str:
    """The seeded System superuser's id."""
    return DEFAULT_USER_ID


async def create_team_user(client, admin_id, role, team_id=None):
    """Provision a user with *role*; creates a fresh team unless one is given.

    Returns ``(user_id, team_id)``. New users start as viewers; other roles
    are granted via the membership PATCH, the way an admin console would.
    """
    h = auth(admin_id)
    if team_id is None:
        r = await client.post("/api/v1/teams", headers=h,
                              json={"name": f"t-{rid()}"})
        assert r.status_code == 201, r.text
        team_id = r.json()["id"]
    r = await client.post("/api/v1/auth/users", headers=h,
                          json={"email": f"{role}-{rid()}@bank.com",
                                "name": role.title(), "team_id": team_id})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    if role != "viewer":
        r = await client.patch(f"/api/v1/teams/{team_id}/members/{uid}",
                               headers=h, json={"role": role})
        assert r.status_code == 200, r.text
    return uid, team_id


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------


async def upload_file(client, user_id, path, *, name=None, dataset_id=None,
                      team_id=DEFAULT_TEAM_ID, content_type=None, **form):
    """Upload *path* as *user_id*; return the response JSON (asserts 200)."""
    fname = name or os.path.basename(str(path))
    if content_type is None:
        content_type = ("text/csv" if fname.endswith(".csv")
                        else "application/octet-stream")
    data = dict(form)
    if dataset_id:
        data["dataset_id"] = dataset_id
    headers = auth(user_id)
    if team_id:
        headers["X-Team-Id"] = team_id
    with open(path, "rb") as f:
        r = await client.post("/api/v1/upload", headers=headers,
                              files={"file": (fname, f, content_type)},
                              data=data)
    assert r.status_code == 200, r.text
    return r.json()


async def upload_inline(client, user_id, json_str, *, dataset_id=None,
                        team_id=DEFAULT_TEAM_ID):
    """Upload inline JSON rows; return the response JSON (asserts 200)."""
    data = {"data": json_str}
    if dataset_id:
        data["dataset_id"] = dataset_id
    headers = auth(user_id)
    if team_id:
        headers["X-Team-Id"] = team_id
    r = await client.post("/api/v1/upload", headers=headers, data=data)
    assert r.status_code == 200, r.text
    return r.json()


async def poll_status(client, user_id, version_id):
    """Fetch upload status for *version_id*; return the response JSON."""
    r = await client.get(f"/api/v1/upload/status/{version_id}",
                         headers=auth(user_id))
    assert r.status_code == 200, r.text
    return r.json()


@pytest_asyncio.fixture
async def admin_dataset(client, admin_id):
    """Upload a throwaway CSV dataset to the Default team; return its id."""
    body = await upload_file(client, admin_id, SAMPLE_CSV, name="sample.csv")
    return body["dataset_id"]


# ---------------------------------------------------------------------------
# Workbook factories (shared across test files)
# ---------------------------------------------------------------------------


def make_workbook(path, *, quarter_col=False, second_sheet="Expenses",
                  extra_revenue_row=False):
    """Payments workbook: Revenue (dup/blank headers), a costs sheet, hidden Secrets."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    header = ["Amount", "Amount", None, "Region"] + (["Quarter"] if quarter_col else [])
    ws.append(header)
    rows = [[100, 1, "a", "EU"], [200, 2, "b", "US"], [300, 3, "c", "APAC"]]
    if extra_revenue_row:
        rows.append([400, 4, "d", "EU"])
    for r in rows:
        ws.append(r + (["Q1"] if quarter_col else []))
    costs = wb.create_sheet(second_sheet)
    costs.append(["Item", "Cost"])
    costs.append(["rent", 50])
    costs.append(["power", 20])
    sec = wb.create_sheet("Secrets")
    sec.append(["K", "V"])
    sec.append(["k1", "v1"])
    sec.sheet_state = "hidden"
    wb.save(path)


def make_orders_workbook(path, *, clean):
    """Customers + Orders. The dirty variant has a NULL id, a dup id, and an orphan FK."""
    wb = Workbook()
    cust = wb.active
    cust.title = "Customers"
    cust.append(["customer_id", "tier"])
    rows = [[1, "gold"], [2, "silver"], [3, "gold"]]
    if not clean:
        rows += [[None, "bronze"], [2, "copper"]]  # NULL id + duplicate id
    for r in rows:
        cust.append(r)
    orders = wb.create_sheet("Orders")
    orders.append(["order_id", "customer_id", "total"])
    order_rows = [[10, 1, 99.5], [11, 2, 15.0]]
    if not clean:
        order_rows.append([12, 999, 5.0])  # orphan customer_id
    for r in order_rows:
        orders.append(r)
    wb.save(path)


def make_holdings_workbook(path):
    """Single Holdings sheet with portfolio/cusip columns (discovery tests)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Holdings"
    ws.append(["portfolio_id", "cusip_number", "market_value"])
    ws.append([1, "037833100", 1000.5])
    ws.append([2, "17275R102", 250.0])
    wb.save(path)


def make_crm_workbook(path):
    """Customers + Orders + hidden Scratch (advanced/coordinated tests)."""
    wb = Workbook()
    cust = wb.active
    cust.title = "Customers"
    cust.append(["customer_id", "tier"])
    for r in ([1, "gold"], [2, "silver"], [3, "gold"]):
        cust.append(r)
    orders = wb.create_sheet("Orders")
    orders.append(["order_id", "customer_id", "total"])
    for r in ([10, 1, 100.0], [11, 2, 40.0], [12, 1, 60.0]):
        orders.append(r)
    scratch = wb.create_sheet("Scratch")
    scratch.append(["junk"])
    scratch.append(["x"])
    scratch.sheet_state = "hidden"
    wb.save(path)
