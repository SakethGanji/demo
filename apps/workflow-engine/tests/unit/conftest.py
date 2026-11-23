import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── a throwaway Postgres database for tests that need real row locks / SKIP LOCKED (Stage 5 tasks) ──────────────


@pytest.fixture(scope="session")
def pg_dbname():
    """One ``workflows_test_<hex>`` database per run with every migration applied (same helper as the integration harness)."""
    from tests.integration.conftest import TestDatabase

    db = TestDatabase()
    try:
        db.create()
    except Exception as e:  # no Postgres reachable: the DB-backed tests skip instead of failing the whole unit suite
        pytest.skip(f"Postgres not reachable: {e}")
    try:
        db.migrate("apply")
        yield db
    finally:
        db.drop()


@pytest.fixture
async def pg(pg_dbname, monkeypatch):
    """A session factory bound to the throwaway database (one engine per test, so connections never cross event loops).

    ``src.db.session.async_session_factory`` is swapped for it, so everything that looks the factory up at call time
    (the runner's node-output writes, variable loading, the resumer) uses the throwaway database, never the dev one."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    db = pg_dbname
    e = db.env()
    url = f"postgresql+asyncpg://{e['WORKFLOW_DB_USER']}:{e['WORKFLOW_DB_PASSWORD']}@{e['WORKFLOW_DB_HOST']}:{e['WORKFLOW_DB_PORT']}/{db.name}"
    engine = create_async_engine(url, pool_size=10, max_overflow=20, connect_args={"server_settings": {"search_path": '"workflow-app"'}})
    sf = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("src.db.session.async_session_factory", sf)
    try:
        yield sf
    finally:
        from src.engine import task_resumer

        pending = [t for t in list(task_resumer._inflight) if not t.done()]
        for t in pending:
            t.cancel()
        await engine.dispose()
