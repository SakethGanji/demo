"""Platform traps found by a one-shot build: module save runs the builder's static checks, the preview "view as" user,
and ``api.op(`` scans that tolerate normal formatting."""

from __future__ import annotations

import uuid

import pytest

from src.core.public_tokens import issue_token
from src.db.models import AppModel
from src.sandbox.backend import SandboxResult
from src.services import app_validate, project_knowledge
from src.services.code_module_service import CodeModuleService, static_checks
from src.services.project_service import ProjectError, ProjectService
from src.services.route_dispatcher import RouteDispatcher, RouteRequest
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

GOOD = '''from studio import api
import studio


@api.post("/things", name="add_thing")
def add_thing(req):
    return studio.db.table("things").insert({"label": "x"})
'''
TABLES = [{"name": "things", "columns": [{"name": "label", "type": "string"}]}]


def test_static_checks_signature_and_names():
    errs, _ = static_checks(GOOD.replace("(req)", "(req, extra)"))
    assert [e["code"] for e in errs] == ["handler_signature"]
    errs, _ = static_checks(GOOD.replace("(req)", "(req, extra=None)").replace("from studio import api\n", "from studio import api\n"))
    assert errs == []
    errs, _ = static_checks(GOOD + "\n\n@api.get('/e')\ndef e(req):\n    return error('nope')\n")
    assert any(e["code"] == "undefined_name" and "error" in e["message"] for e in errs)
    errs, warns = static_checks(GOOD + "\n\n@api.get('/z')\ndef z(req):\n    return mystery()\n")
    assert errs == [] and any("mystery" in w["message"] for w in warns)
    errs, _ = static_checks(GOOD.replace("from studio import api\n", ""))
    assert any("api" in e["message"] for e in errs)


async def _project(pg):
    async with pg() as s:
        p = await ProjectService(s).create(f"trap-{uuid.uuid4().hex[:8]}")
        await s.commit()
        return p.id


async def test_save_refuses_bad_signature_and_undefined_names(pg):
    pid = await _project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "bad", GOOD.replace("(req)", "(req, extra)"), tables=TABLES)
        assert e.value.status == 422 and e.value.code == "module_invalid"
        assert e.value.extra["errors"][0]["code"] == "handler_signature"
        bad = GOOD + "\n\n@api.get('/e')\ndef e(req):\n    return error('nope')\n"
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "bad", bad, tables=TABLES)
        assert e.value.code == "module_invalid"
        # a dry run reports instead of raising
        res = await CodeModuleService(s).create(p, "bad", bad, dry_run=True, tables=TABLES)
        assert res["errors"] and res["errors"][0]["code"] == "undefined_name"


async def test_save_warns_on_unknown_column_and_update_refuses(pg):
    pid = await _project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = CodeModuleService(s)
        res = await svc.create(p, "ok", GOOD.replace('{"label": "x"}', '{"label": "x", "zzz": 1}'), tables=TABLES)
        assert res["errors"] == []
        assert [w for w in res["warnings"] if w["code"] == "unknown_column" and w["column"] == "zzz"]
        clean = await svc.update(p, res["module"]["id"], GOOD)
        assert not [w for w in clean.get("warnings", []) if w["code"] == "unknown_column"]
        with pytest.raises(ProjectError) as e:
            await svc.update(p, res["module"]["id"], GOOD.replace("(req)", "(req, extra)"))
        assert e.value.code == "module_invalid"


# ── preview "view as" ─────────────────────────────────────────────────────────

WHO = '''from studio import api


@api.get("/whoami", auth="none")
def whoami(req):
    return req.caller
'''


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


async def _call(pg, pid, headers):
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        return await RouteDispatcher(s).handle(p, RouteRequest("GET", "/whoami", headers=headers))


async def test_preview_view_as_only_for_preview_tokens(pg, monkeypatch):
    spy = Spy(monkeypatch)
    pid = await _project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "who", WHO)
        app = AppModel(id=f"app_{uuid.uuid4().hex[:8]}", project_id=p.id, name="a", team_id=p.team_id)
        s.add(app)
        await s.commit()
        app_id = app.id
        env = p.default_environment
    view = {"x-preview-user": "ana@x.io", "x-preview-user-name": "Ana", "x-preview-user-roles": "approver, buyer"}

    prev = issue_token(app_id, 0, env=env, preview=True)
    res = await _call(pg, pid, {"authorization": f"Bearer {prev}"})
    assert res.status == 200 and spy.req["caller"]["kind"] == "app_token" and spy.req["caller"]["id"] == app_id

    res = await _call(pg, pid, {"authorization": f"Bearer {prev}", **view})
    assert res.status == 200
    assert spy.req["caller"] == {"kind": "user", "id": "ana@x.io", "name": "Ana", "email": "ana@x.io",
                                 "roles": ["approver", "buyer"], "preview": True}
    assert not any(k.startswith("x-preview-user") for k in spy.req["headers"])

    # a published (non-preview) app token and no credential ignore the headers
    monkeypatch.setattr("src.core.public_tokens.app_token_valid_for", lambda *_a: True)
    live = issue_token(app_id, 1, rk="k")
    await _call(pg, pid, {"authorization": f"Bearer {live}", **view})
    assert spy.req["caller"]["kind"] == "app_token"
    await _call(pg, pid, dict(view))
    assert spy.req["caller"]["kind"] == "anonymous"


# ── api.op( scans ─────────────────────────────────────────────────────────────

SRC = "const a = await api\n  .getX();\nconst b = api . putY ({});\nxapi.nope(); foo.api.nope2();\n"


def test_api_call_scans_tolerate_formatting():
    assert app_validate._API_CALL.findall(SRC) == ["getX", "putY"]
    assert project_knowledge._API_CALL.findall(SRC) == ["getX", "putY"]
    assert app_validate.bindings("const r = await api\n  .getCases();\n")[0][0].op == "getCases"
