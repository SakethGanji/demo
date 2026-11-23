"""UI-DESIGN §10.3 — the fake LLM through the real engine: startup refusals, /api/_test/* endpoints,
the sandbox override, and that a raw/.env switch does nothing."""

from __future__ import annotations

import subprocess
import sys
import time

import httpx
import pytest

from .conftest import ENGINE_DIR, FAKE_LLM_TOKEN, EngineServer, _base_env, _free_port, fake_env
from .journeys._helpers import needs_bwrap, new_project

pytestmark = pytest.mark.integration
TOK = {"X-Test-Token": FAKE_LLM_TOKEN}


def _start_and_wait_exit(env: dict, cwd: str) -> tuple[int | None, str]:
    port = _free_port()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.main:app", "--app-dir", str(ENGINE_DIR),
                          "--host", "127.0.0.1", "--port", str(port)],
                         cwd=cwd, env={**_base_env(), "SEED": "0", **env}, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = p.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        return None, out
    return p.returncode, out


@pytest.mark.parametrize("case,override,reason", [
    ("db_workflows", {"WORKFLOW_DB_NAME": "workflows"}, "fake LLM refused: WORKFLOW_DB_NAME"),
    ("bind_any", {"WORKFLOW_BIND_HOST": "0.0.0.0"}, "fake LLM refused: WORKFLOW_BIND_HOST"),
    ("no_token", {"WORKFLOW_FAKE_LLM_TOKEN": ""}, "fake LLM refused: WORKFLOW_FAKE_LLM_TOKEN"),
    ("no_test_mode", {"WORKFLOW_TEST_MODE": "0"}, "fake LLM refused: WORKFLOW_TEST_MODE"),
    ("dotenv_on", {"WORKFLOW_DISABLE_DOTENV": "0"}, "fake LLM refused: WORKFLOW_DISABLE_DOTENV"),
    ("test_mode_live_db", {"WORKFLOW_LLM_PROVIDER": "", "WORKFLOW_DB_NAME": "workflows"}, "test mode refused"),
])
def test_engine_startup_is_refused(test_db, tmp_path, case, override, reason):
    rc, out = _start_and_wait_exit({**fake_env(test_db), **override}, str(tmp_path))
    assert rc not in (None, 0), out[-2000:]
    assert reason in out, out[-2000:]
    assert "Application startup complete" not in out


def test_raw_provider_and_dotenv_line_do_nothing(test_db, tmp_path):
    (tmp_path / ".env").write_text("WORKFLOW_LLM_PROVIDER=fake\n")
    env = {**fake_env(test_db), "WORKFLOW_LLM_PROVIDER": "", "LLM_PROVIDER": "fake"}
    srv = EngineServer(test_db, extra_env=env, cwd=str(tmp_path), label="-dotenv")
    srv.start()
    try:
        assert httpx.get(srv.base_url + "/api/_test/health", headers=TOK).status_code == 404
        assert httpx.get(srv.base_url + "/health").status_code == 200
    finally:
        srv.stop()


def test_test_endpoints_absent_on_the_normal_engine(api):
    assert api.raw("GET", "/api/_test/health", headers=TOK).status_code == 404
    assert api.raw("PUT", "/api/_test/sandbox", json={"available": False}, headers=TOK).status_code == 404


def test_fake_health_token_script_and_call_log(fake_api, fake_stack):
    _, db = fake_stack
    r = fake_api.raw("GET", "/api/_test/health", headers=TOK)
    assert r.status_code == 200 and r.json() == {"fake_llm": True, "db": db.name}
    for method, path in [("GET", "/api/_test/health"), ("GET", "/api/_test/fake-llm/calls"),
                         ("DELETE", "/api/_test/fake-llm/calls"), ("PUT", "/api/_test/fake-llm/state")]:
        assert fake_api.raw(method, path).status_code == 404
        assert fake_api.raw(method, path, headers={"X-Test-Token": "wrong" * 10}).status_code == 404
    assert fake_api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK).json() == {"success": True}
    assert fake_api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json() == []
    # the pinned script (loaded at install from WORKFLOW_FAKE_LLM_SCRIPT) can be replaced and restored
    import json as _json

    from .conftest import FAKE_SCRIPT
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK,
                     json={"rules": [{"id": "x", "respond": {"text": "hi"}}], "default": {"raise": "unmatched"}})
    assert r.status_code == 200 and r.json() == {"rules": 1}
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK,
                     json={"rules": [{"id": "x", "respond": {"text": "${NOT_A_REAL_VAR_X}"}}]})
    assert r.status_code == 422
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=_json.loads(FAKE_SCRIPT.read_text()))
    assert r.status_code == 200 and r.json() == {"rules": 10}
    r = fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})
    assert r.status_code == 200
    assert fake_api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"bogus": 1}).status_code == 422


@needs_bwrap
def test_sandbox_override_drives_status_and_python_dispatch(fake_api):
    P, S = new_project(fake_api, "sbx")
    src = 'from studio import api\n\n@api.get("/hi")\ndef hi(req):\n    return {"hi": 1}\n'
    assert fake_api.post(f"/projects/{P}/modules", {"name": "m", "source": src}).status_code == 201
    assert fake_api.raw("GET", f"/api/p/{S}/hi").json() == {"hi": 1}
    try:
        r = fake_api.raw("PUT", "/api/_test/sandbox", headers=TOK, json={"available": False})
        assert r.status_code == 200 and r.json() == {"available": False}
        st = fake_api.get("/sandbox/status").json()
        assert st["available"] is False and "test harness" in st["reason"]
        r = fake_api.raw("GET", f"/api/p/{S}/hi")
        assert r.status_code == 503 and r.json()["error"] == "sandbox_unavailable"
    finally:
        assert fake_api.raw("PUT", "/api/_test/sandbox", headers=TOK, json={"available": True}).json() == {"available": True}
    assert fake_api.get("/sandbox/status").json()["available"] is True
    time.sleep(0)
    assert fake_api.raw("GET", f"/api/p/{S}/hi").json() == {"hi": 1}
