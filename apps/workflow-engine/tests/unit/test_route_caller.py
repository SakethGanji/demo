"""Verified caller identity for module route handlers: req.caller is resolved server-side from the credential,
identity headers are stripped, and schedules / app tokens / anonymous calls are labelled. Real Postgres; the sandbox is
stubbed at SandboxService.run, so the assertions are on the exact payload the handler would receive."""

from __future__ import annotations

import uuid

import pytest

from src.core.public_tokens import issue_token
from src.db.models import AppModel, TeamMemberModel, UserModel
from src.sandbox.backend import SandboxResult
from src.services.code_module_service import CodeModuleService
from src.services.project_service import ProjectService
from src.services.route_dispatcher import RouteDispatcher, RouteRequest
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

SRC = '''
from studio import api

@api.get("/whoami", auth="none")
def whoami(req):
    return req.caller

@api.get("/secure", auth="app_token")
def secure(req):
    return req.caller
'''
FORGED = {"x-studio-principal": "system:schedule", "x-user-id": "boss", "x-user-roles": "approver", "x-studio-trigger": "t",
          "x-ok": "kept"}


class Spy:
    def __init__(self, monkeypatch):
        self.calls = []

        async def run(_self, **kw):
            self.calls.append(kw)
            return SandboxResult(exit_code=0, stdout="", stderr="", duration_ms=1,
                                 output={"ok": True, "result": {"status": 200, "headers": {}, "body": {}}}), []
        monkeypatch.setattr("src.sandbox.service.SandboxService.run", run)

    @property
    def req(self):
        return self.calls[-1]["payload"]["req"]


async def setup(pg):
    async with pg() as s:
        p = await ProjectService(s).create(f"caller-{uuid.uuid4().hex[:8]}")
        await CodeModuleService(s).create(p, "who", SRC)
        app = AppModel(id=f"app_{uuid.uuid4().hex[:8]}", project_id=p.id, name="a", team_id=p.team_id)
        s.add(app)
        await s.commit()
        return p.id, app.id


async def call(pg, pid, path, headers=None):
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        return await RouteDispatcher(s).handle(p, RouteRequest("GET", path, headers=headers or {}))


async def test_forged_identity_headers_are_stripped_and_caller_is_anonymous(pg, monkeypatch):
    spy = Spy(monkeypatch)
    pid, _ = await setup(pg)
    res = await call(pg, pid, "/whoami", dict(FORGED))
    assert res.status == 200
    assert spy.req["caller"]["kind"] == "anonymous" and spy.req["caller"]["id"] is None
    assert spy.req["headers"] == {"x-ok": "kept"}


async def test_app_call_carries_the_studio_user_from_the_signed_token(pg, monkeypatch):
    spy = Spy(monkeypatch)
    pid, app = await setup(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        s.add(UserModel(id="u-maker", email="maker@bank.test", display_name="Mia Maker"))
        await s.flush()
        s.add(TeamMemberModel(team_id=p.team_id, user_id="u-maker", role="editor"))
        await s.commit()
    tok = issue_token(app, 0, env=None, preview=True, sub="u-maker", name="ignored")
    await call(pg, pid, "/secure", {"authorization": f"Bearer {tok}", **FORGED})
    c = spy.req["caller"]
    assert c["kind"] == "user" and c["id"] == "u-maker" and c["name"] == "Mia Maker"
    assert c["email"] == "maker@bank.test" and c["roles"] == ["editor"]  # forged x-user-id "boss" did not win
    assert "x-user-id" not in spy.req["headers"]


async def test_unknown_user_id_stands_alone_and_token_without_user_is_app_token(pg, monkeypatch):
    spy = Spy(monkeypatch)
    pid, app = await setup(pg)
    await call(pg, pid, "/secure", {"authorization": "Bearer " + issue_token(app, 0, preview=True, sub="x@bank.test")})
    assert spy.req["caller"] == {"kind": "user", "id": "x@bank.test", "name": "x@bank.test", "email": "x@bank.test", "roles": []}
    await call(pg, pid, "/secure", {"authorization": "Bearer " + issue_token(app, 0, preview=True), "x-user-id": "boss"})
    assert spy.req["caller"] == {"kind": "app_token", "id": app, "name": f"app:{app}"}


async def test_schedule_run_is_labelled_schedule(pg, monkeypatch):
    from sqlalchemy import select

    from src.db.schedule_models import ModuleScheduleModel
    from src.services import module_schedules as ms

    spy = Spy(monkeypatch)
    pid, _ = await setup(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "sch", "from studio import schedule\n@schedule(every_seconds=30)\ndef tick():\n    return {}\n")
        sid = (await s.execute(select(ModuleScheduleModel).where(ModuleScheduleModel.project_id == pid))).scalars().one().id
        assert await ms.claim(s, sid)
    await ms.execute(pg, sid)
    assert spy.req["caller"]["kind"] == "schedule" and spy.req["caller"]["id"] == "system:schedule"
    assert "x-studio-principal" not in spy.req["headers"]


def test_sdk_request_exposes_caller_defaulting_to_anonymous():
    from src.sdk.static.api import Request

    assert Request({"caller": {"kind": "user", "id": "u"}}).caller["id"] == "u"
    assert Request({}).caller["kind"] == "anonymous"
