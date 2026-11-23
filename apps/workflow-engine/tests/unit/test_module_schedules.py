"""@schedule in code modules: parse, reconcile on save / re-save / delete, due-tick execution through the real route
execution path (sandbox stubbed at SandboxService.run), no-overlap, error recording and the API. Real Postgres."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from src.db.models import ExecutionModel
from src.db.schedule_models import ModuleScheduleModel
from src.sandbox.backend import SandboxResult
from src.services import module_schedules as ms
from src.services.code_module_service import CodeModuleService
from src.services.project_service import ProjectError, ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

SRC = '''
from studio import api, schedule

@schedule(every_seconds=30)
def sync_pending():
    return {"ok": True}

@schedule("*/5 * * * *")
def nightly(req):
    return {}

@api.get("/ping")
def ping(req):
    return {}
'''


def test_discover_parses_cron_and_interval():
    d = {x["function"]: x for x in ms.discover_schedules(SRC)}
    assert d["sync_pending"]["every_seconds"] == 30 and d["sync_pending"]["cron"] is None
    assert d["nightly"]["cron"] == "*/5 * * * *" and d["nightly"]["every_seconds"] is None
    assert ms.discover_schedules("@api.schedule(every_seconds=60)\ndef f(): pass")[0]["every_seconds"] == 60


@pytest.mark.parametrize("dec", ["@schedule(every_seconds=5)", "@schedule()", "@schedule('* * * *')", "@schedule('x y z a b')",
                                 "@schedule('* * * * *', every_seconds=20)", "@schedule(every_seconds='30')",
                                 "@schedule(foo=1)", "@schedule(CRON)"])
def test_discover_rejects_bad_decorators(dec):
    with pytest.raises(ProjectError) as e:
        ms.discover_schedules(f"{dec}\ndef f(): pass")
    assert e.value.status == 422 and e.value.code == "invalid_decorator"


def test_one_schedule_per_function():
    with pytest.raises(ProjectError):
        ms.discover_schedules("@schedule(every_seconds=20)\n@schedule(every_seconds=30)\ndef f(): pass")


async def mk_project(pg):
    async with pg() as s:
        return (await ProjectService(s).create(f"sch-{uuid.uuid4().hex[:8]}")).id


async def rows(pg, pid):
    async with pg() as s:
        return {r.function: r for r in (await s.execute(select(ModuleScheduleModel).where(ModuleScheduleModel.project_id == pid))).scalars()}


async def save(pg, pid, name, src):
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        return (await CodeModuleService(s).create(p, name, src))["module"]["id"]


async def test_reconcile_on_save_resave_and_delete(pg):
    pid = await mk_project(pg)
    mid = await save(pg, pid, "jobs_sync", SRC)
    r = await rows(pg, pid)
    assert set(r) == {"sync_pending", "nightly"} and r["sync_pending"].next_run_at > datetime.now()
    # re-save, unchanged: same rows, state kept
    async with pg() as s:
        await s.execute(ModuleScheduleModel.__table__.update().where(ModuleScheduleModel.id == r["nightly"].id)
                        .values(last_status="success", enabled=False))
        await s.commit()
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).update(p, mid, SRC + "\n# edit\n")
    r2 = await rows(pg, pid)
    assert {k: v.id for k, v in r2.items()} == {k: v.id for k, v in r.items()}
    assert r2["nightly"].last_status == "success" and r2["nightly"].enabled is False
    # change one, drop one
    new = SRC.replace("every_seconds=30", "every_seconds=45").replace('@schedule("*/5 * * * *")\ndef nightly(req):\n    return {}\n', "")
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).update(p, mid, new)
    r3 = await rows(pg, pid)
    assert set(r3) == {"sync_pending"} and r3["sync_pending"].every_seconds == 45 and r3["sync_pending"].id == r["sync_pending"].id
    # delete the module
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).delete(p, mid)
    assert await rows(pg, pid) == {}


async def test_invalid_schedule_refuses_the_save(pg):
    pid = await mk_project(pg)
    with pytest.raises(ProjectError) as e:
        await save(pg, pid, "bad", "from studio import schedule\n@schedule(every_seconds=3)\ndef f():\n    pass\n")
    assert e.value.code == "invalid_decorator"


class FakeSandbox:
    """Stands in for SandboxService.run (the sandbox/broker boundary); records each call."""

    def __init__(self, monkeypatch, *, ok=True, error=None, delay=0.0):
        self.calls, self.ok, self.error, self.delay = [], ok, error, delay
        fake = self

        async def run(_self, **kw):
            fake.calls.append(kw)
            if fake.delay:
                await asyncio.sleep(fake.delay)
            out = {"ok": True, "result": {"status": 200, "headers": {}, "body": {"ok": True}}} if fake.ok else \
                {"ok": False, "error": {"type": "RuntimeError", "message": fake.error or "boom", "traceback": "tb"}}
            return SandboxResult(exit_code=0, stdout="", stderr="", output=out, duration_ms=3), ["call_1"]

        monkeypatch.setattr("src.sandbox.service.SandboxService.run", run)


async def due(pg, sid):
    async with pg() as s:
        await s.execute(ModuleScheduleModel.__table__.update().where(ModuleScheduleModel.id == sid)
                        .values(next_run_at=datetime.now() - timedelta(seconds=1)))
        await s.commit()


async def test_due_tick_runs_through_the_route_path_and_records(pg, monkeypatch):
    from src.engine.cron_scheduler import CronScheduler

    fake = FakeSandbox(monkeypatch)
    pid = await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    r = await rows(pg, pid)
    await due(pg, r["sync_pending"].id)
    sched = CronScheduler(pg)
    for t in await sched._tick_module_schedules():
        await t
    assert len(fake.calls) == 1  # only the due one
    c = fake.calls[0]
    assert c["kind"] == "route" and c["entry"] == "jobs_sync:sync_pending" and c["project_id"] == pid
    assert c["payload"]["req"]["caller"]["kind"] == "schedule" and "jobs_sync" in c["modules"]
    s = (await rows(pg, pid))["sync_pending"]
    assert s.last_status == "success" and s.last_error is None and s.last_run_at and s.duration_ms is not None
    assert s.running_since is None and s.next_run_at > datetime.now()
    async with pg() as db:
        ex = await db.get(ExecutionModel, s.last_execution_id)
        assert ex.status == "success" and ex.exec_metadata["trigger"]["principal"] == "system:schedule"
    # not due again: nothing runs
    assert await sched._tick_module_schedules() == []


async def test_failure_is_recorded_truncated_and_never_raises(pg, monkeypatch):
    FakeSandbox(monkeypatch, ok=False, error="x" * 2000)
    pid = await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    sid = (await rows(pg, pid))["sync_pending"].id
    async with pg() as s:
        assert await ms.claim(s, sid)
    out = await ms.execute(pg, sid)
    assert out["status"] == "failed"
    s = (await rows(pg, pid))["sync_pending"]
    assert s.last_status == "failed" and s.last_error.startswith("RuntimeError: xxx") and len(s.last_error) <= ms.ERROR_CAP
    assert s.running_since is None


async def test_sandbox_exception_is_recorded_not_raised(pg, monkeypatch):
    async def boom(_self, **kw):
        raise ValueError("kaboom")

    monkeypatch.setattr("src.sandbox.service.SandboxService.run", boom)
    pid = await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    sid = (await rows(pg, pid))["sync_pending"].id
    async with pg() as s:
        await ms.claim(s, sid)
    assert (await ms.execute(pg, sid))["status"] == "failed"
    assert "kaboom" in (await rows(pg, pid))["sync_pending"].last_error


async def test_no_overlap_while_a_run_is_in_flight(pg, monkeypatch):
    from src.engine.cron_scheduler import CronScheduler

    fake = FakeSandbox(monkeypatch, delay=0.6)
    pid = await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    sid = (await rows(pg, pid))["sync_pending"].id
    sched = CronScheduler(pg)
    await due(pg, sid)
    first = await sched._tick_module_schedules()
    assert len(first) == 1
    await asyncio.sleep(0.1)
    await due(pg, sid)  # due again while the first is still running
    assert await sched._tick_module_schedules() == []
    async with pg() as s:
        assert not await ms.claim(s, sid)  # a manual run is refused too
    await asyncio.gather(*first)
    assert len(fake.calls) == 1
    await due(pg, sid)
    for t in await sched._tick_module_schedules():
        await t
    assert len(fake.calls) == 2  # free again after the first finished


async def test_stale_in_flight_flag_does_not_block_forever(pg):
    pid = await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    sid = (await rows(pg, pid))["sync_pending"].id
    async with pg() as s:
        await s.execute(ModuleScheduleModel.__table__.update().where(ModuleScheduleModel.id == sid)
                        .values(running_since=datetime.now() - timedelta(seconds=ms.STALE_RUN_SECONDS + 5)))
        await s.commit()
        assert await ms.claim(s, sid)


async def test_disabled_schedule_does_not_run(pg, monkeypatch):
    from src.engine.cron_scheduler import CronScheduler

    fake = FakeSandbox(monkeypatch)
    pid = await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    sid = (await rows(pg, pid))["sync_pending"].id
    async with pg() as s:
        await s.execute(ModuleScheduleModel.__table__.update().where(ModuleScheduleModel.id == sid).values(enabled=False))
        await s.commit()
    await due(pg, sid)
    assert await CronScheduler(pg)._tick_module_schedules() == [] and not fake.calls


async def client(pg):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.dependencies import get_db_session
    from src.routes import module_schedules

    app = FastAPI()
    app.include_router(module_schedules.router, prefix="/api")

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_api_list_run_patch_and_404s(pg, monkeypatch):
    FakeSandbox(monkeypatch)
    pid, other = await mk_project(pg), await mk_project(pg)
    await save(pg, pid, "jobs_sync", SRC)
    sid = (await rows(pg, pid))["sync_pending"].id
    async with await client(pg) as c:
        lst = (await c.get(f"/api/projects/{pid}/schedules")).json()
        assert {x["function"] for x in lst} == {"sync_pending", "nightly"} and lst[0]["module"] == "jobs_sync"
        assert all(x["last_run_at"] is None and x["enabled"] for x in lst)
        r = await c.post(f"/api/projects/{pid}/schedules/{sid}/run")
        assert r.status_code == 200 and r.json()["run"]["status"] == "success"
        assert r.json()["schedule"]["last_status"] == "success" and r.json()["schedule"]["last_run_at"]
        p = await c.patch(f"/api/projects/{pid}/schedules/{sid}", json={"enabled": False})
        assert p.status_code == 200 and p.json()["enabled"] is False
        # another project cannot see or touch it: 404, same as a missing id
        for call in (c.post(f"/api/projects/{other}/schedules/{sid}/run"),
                     c.patch(f"/api/projects/{other}/schedules/{sid}", json={"enabled": True}),
                     c.post(f"/api/projects/{pid}/schedules/sch_nope/run")):
            assert (await call).status_code == 404
        assert (await c.get(f"/api/projects/{other}/schedules")).json() == []
        assert (await c.get("/api/projects/nope/schedules")).status_code == 404
        async with pg() as s:
            await ms.claim(s, sid)
        busy = await c.post(f"/api/projects/{pid}/schedules/{sid}/run")
        assert busy.status_code == 409 and busy.json()["detail"]["code"] == "already_running"
