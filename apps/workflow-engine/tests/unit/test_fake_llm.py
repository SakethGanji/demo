"""UI-DESIGN §10.3 — the test-only fake LLM: activation, script format, matching, retries, normalisation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.engine import fake_llm, llm_provider

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm"
GOOD_ENV = {
    "WORKFLOW_LLM_PROVIDER": "fake", "WORKFLOW_TEST_MODE": "1", "WORKFLOW_DB_NAME": "workflows_test_unit",
    "WORKFLOW_BIND_HOST": "127.0.0.1", "WORKFLOW_FAKE_LLM_TOKEN": "x" * 32, "WORKFLOW_DISABLE_DOTENV": "1",
}


@pytest.fixture
def env(monkeypatch):
    for k in list(GOOD_ENV) + ["WORKFLOW_FAKE_LLM_SCRIPT", "LLM_PROVIDER"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in GOOD_ENV.items():
        monkeypatch.setenv(k, v)
    yield monkeypatch
    fake_llm.uninstall()


def run(coro):
    return asyncio.run(coro)


# ── activation ───────────────────────────────────────────────────────────────


def test_all_conditions_install_and_set_zero_retry_delay(env):
    assert fake_llm.install() is not None and fake_llm.ACTIVE is not None
    assert llm_provider._RETRY_BASE_DELAY == 0


@pytest.mark.parametrize("key,value,reason", [
    ("WORKFLOW_TEST_MODE", None, "WORKFLOW_TEST_MODE"),
    ("WORKFLOW_TEST_MODE", "0", "WORKFLOW_TEST_MODE"),
    ("WORKFLOW_DB_NAME", "workflows", "WORKFLOW_DB_NAME"),
    ("WORKFLOW_DB_NAME", None, "WORKFLOW_DB_NAME"),
    ("WORKFLOW_BIND_HOST", "0.0.0.0", "WORKFLOW_BIND_HOST"),
    ("WORKFLOW_BIND_HOST", None, "WORKFLOW_BIND_HOST"),
    ("WORKFLOW_FAKE_LLM_TOKEN", None, "WORKFLOW_FAKE_LLM_TOKEN"),
    ("WORKFLOW_FAKE_LLM_TOKEN", "x" * 31, "WORKFLOW_FAKE_LLM_TOKEN"),
    ("WORKFLOW_DISABLE_DOTENV", None, "WORKFLOW_DISABLE_DOTENV"),
])
def test_every_refusal(env, key, value, reason):
    if value is None:
        env.delenv(key)
    else:
        env.setenv(key, value)
    with pytest.raises(RuntimeError, match=f"^fake LLM refused: .*{reason}"):
        fake_llm.install()
    assert fake_llm.ACTIVE is None


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_hosts_are_accepted(env, host):
    env.setenv("WORKFLOW_BIND_HOST", host)
    assert fake_llm.install() is not None


def test_raw_llm_provider_fake_does_nothing(env):
    env.delenv("WORKFLOW_LLM_PROVIDER")
    env.setenv("LLM_PROVIDER", "fake")
    assert fake_llm.install() is None and fake_llm.ACTIVE is None
    env.setenv("WORKFLOW_LLM_PROVIDER", "FAKE")  # exact value only
    assert fake_llm.install() is None


def test_dotenv_line_cannot_activate(env, tmp_path):
    """A .env line is never read by install() (os.environ only), and settings ignore .env when disabled."""
    env.delenv("WORKFLOW_LLM_PROVIDER")
    (tmp_path / ".env").write_text("WORKFLOW_LLM_PROVIDER=fake\n")
    env.chdir(tmp_path)
    assert fake_llm.install() is None and fake_llm.ACTIVE is None

    import importlib
    import src.core.config as cfg
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env").write_text("WORKFLOW_ANTHROPIC_API_KEY=from-dotenv\n")
    env.chdir(other)
    env.delenv("WORKFLOW_ANTHROPIC_API_KEY", raising=False)
    try:
        importlib.reload(cfg)
        assert cfg.Settings.model_config.get("env_file") is None
        assert cfg.Settings().anthropic_api_key is None
        env.setenv("WORKFLOW_DISABLE_DOTENV", "0")
        importlib.reload(cfg)
        assert cfg.Settings().anthropic_api_key == "from-dotenv"  # proves the .env would otherwise be read
    finally:
        env.setenv("WORKFLOW_DISABLE_DOTENV", "1")
        env.chdir(Path(__file__).resolve().parents[2])
        env.delenv("WORKFLOW_DISABLE_DOTENV")
        importlib.reload(cfg)


def test_bad_script_file_refuses_startup(env, tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"rules": [{"id": "a", "respond": {"text": "${NOPE_UNKNOWN_VAR}"}}]}))
    env.setenv("WORKFLOW_FAKE_LLM_SCRIPT", str(tmp_path / "s.json"))
    with pytest.raises(RuntimeError, match="fake LLM refused: script: unknown variable"):
        fake_llm.install()


def test_test_mode_requires_test_db(monkeypatch):
    from src.core import test_mode
    monkeypatch.setenv("WORKFLOW_TEST_MODE", "1")
    monkeypatch.setenv("WORKFLOW_DB_NAME", "workflows")
    with pytest.raises(RuntimeError, match="test mode refused"):
        test_mode.check_startup()
    assert test_mode.enabled() is False
    monkeypatch.setenv("WORKFLOW_DB_NAME", "workflows_test_ab")
    test_mode.check_startup()
    assert test_mode.enabled() is True
    monkeypatch.setenv("WORKFLOW_TEST_MODE", "0")
    monkeypatch.setenv("WORKFLOW_DB_NAME", "workflows")
    test_mode.check_startup()  # no test mode → nothing to refuse
    assert test_mode.enabled() is False


# ── script loading ───────────────────────────────────────────────────────────


def test_pinned_script_loads_with_files_and_env(monkeypatch):
    monkeypatch.setenv("E2E_UPSTREAM_URL", "http://127.0.0.1:9999")
    s = fake_llm.load_script_file(str(FIX / "llm-script.json"))
    ids = [r["id"] for r in s["rules"]]
    assert ids == ["flaky", "flaky-ok", "fail", "repair", "gen-broken", "gen-scrub", "gen-secret-missing",
                   "gen-refunds", "builder-write", "builder-done"]
    refunds = next(r for r in s["rules"] if r["id"] == "gen-refunds")["respond"]["tool_calls"][0]["args"]["source"]
    assert "http://127.0.0.1:9999/v1/balance" in refunds and "${" not in refunds  # interpolated inside @file:
    app = next(r for r in s["rules"] if r["id"] == "builder-write")["respond"]["tool_calls"][0]["args"]["files"][0]
    assert app["path"] == "App.tsx" and "refundStats" in app["content"]
    assert s["default"] == {"raise": "unmatched"}


def test_pinned_script_unknown_env_is_a_load_error(monkeypatch):
    monkeypatch.delenv("E2E_UPSTREAM_URL", raising=False)
    with pytest.raises(fake_llm.ScriptError, match=r"unknown variable \$\{E2E_UPSTREAM_URL\} in @file:modules/refunds.py"):
        fake_llm.load_script_file(str(FIX / "llm-script.json"))


@pytest.mark.parametrize("bad", [
    {"rules": [{"id": "a", "when": {"bogus": 1}, "respond": {"text": "x"}}]},
    {"rules": [{"id": "a", "respond": {"raise": "sometimes"}}]},
    {"rules": [{"id": "a", "respond": {"text": "x", "raise": "fatal"}}]},
    {"rules": [{"id": "a", "when": {"last_user_matches": "("}, "respond": {"text": "x"}}]},
    {"rules": [{"id": "a", "when": {"nth": 0}, "respond": {"text": "x"}}]},
    {"rules": [{"id": "a", "respond": {"text": "@file:nope.txt"}}]},
    {"rules": [], "extra": 1},
    [],
])
def test_invalid_scripts_are_rejected(bad):
    with pytest.raises(fake_llm.ScriptError):
        fake_llm.parse_script(bad, FIX)


# ── matching ─────────────────────────────────────────────────────────────────

EMIT = {"name": "emit_module", "description": "emit", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}}}


def _fake(script: dict) -> fake_llm.FakeLLM:
    return fake_llm.FakeLLM(fake_llm.parse_script(script, FIX))


def test_when_keys_and_first_rule_wins():
    f = _fake({"rules": [
        {"id": "tool", "when": {"tool": "write_files"}, "respond": {"text": "t"}},
        {"id": "sys", "when": {"system_matches": r"api\.refundStats"}, "respond": {"text": "s"}},
        {"id": "role", "when": {"last_role": "tool", "last_tool_result_matches": "^ok$"}, "respond": {"text": "r"}},
        {"id": "anyu", "when": {"any_user_matches": "__broken__", "last_user_matches": "^Fix"}, "respond": {"text": "a"}},
        {"id": "model", "when": {"model_matches": "^gpt-"}, "respond": {"text": "m"}},
        {"id": "last", "when": {"last_user_matches": "hello"}, "respond": {"text": "l"}},
    ]})
    call = lambda msgs, model="claude-sonnet-4-6", tools=None: run(f.call(model, msgs, 0.2, tools))
    assert call([{"role": "user", "content": "hello"}]).text == "l"
    assert call([{"role": "system", "content": "use api.refundStats"}, {"role": "user", "content": "hello"}]).text == "s"
    assert call([{"role": "user", "content": "hello"}], tools=[{**EMIT, "name": "write_files"}]).text == "t"
    assert call([{"role": "user", "content": "x"}, {"role": "assistant", "content": None},
                 {"role": "tool", "content": "ok", "tool_call_id": "1"}]).text == "r"
    assert call([{"role": "user", "content": "make __broken__"}, {"role": "assistant", "content": "x"},
                 {"role": "user", "content": "Fix these problems in the module you emitted:\n- x"}]).text == "a"
    # the repair message alone (no __broken__ anywhere) does not match "anyu"
    with pytest.raises(fake_llm.FakeLLMUnmatched):
        call([{"role": "user", "content": "Fix these problems in the module you emitted:"}])
    assert call([{"role": "user", "content": "q"}], model="gpt-4o").text == "m"
    log = list(f.calls)
    assert [c["rule_id"] for c in log] == ["last", "sys", "tool", "role", "anyu", None, "model"]
    assert log[5]["raised"] == "unmatched" and "raised" not in log[0]
    assert [c["seq"] for c in log] == list(range(1, 8))
    assert log[0]["messages"] == [{"role": "user", "content": "hello"}] and log[0]["temperature"] == 0.2


def test_times_and_nth():
    f = _fake({"rules": [
        {"id": "twice", "when": {"last_user_matches": "a"}, "times": 2, "respond": {"text": "1"}},
        {"id": "third", "when": {"last_user_matches": "a", "nth": 3}, "respond": {"text": "3"}},
        {"id": "rest", "when": {"last_user_matches": "a"}, "respond": {"text": "r"}},
    ]})
    out = [run(f.call("m", [{"role": "user", "content": "a"}])).text for _ in range(5)]
    assert out == ["1", "1", "3", "r", "r"]


def test_tool_calls_response_and_tools_logged_normalised():
    def write_files(files: list) -> str:
        """Write files.

        Args:
            files: the files
        """
        return "ok"
    f = _fake({"rules": [{"id": "e", "when": {"tool": "emit_module"},
                          "respond": {"tool_calls": [{"name": "emit_module", "args": {"source": "x"}},
                                                     {"name": "emit_module", "args": {"source": "y"}}]}}]})
    r = run(f.call("claude-sonnet-4-6", [{"role": "user", "content": "go"}], 0.2, [EMIT, write_files]))
    assert [(t.name, t.args) for t in r.tool_calls] == [("emit_module", {"source": "x"}), ("emit_module", {"source": "y"})]
    assert len({t.id for t in r.tool_calls}) == 2
    logged = f.calls[-1]["tools"]
    assert logged == llm_provider._normalize_tools([EMIT, write_files], "anthropic")
    assert [t["name"] for t in logged] == ["emit_module", "write_files"]


def test_raise_kinds_and_retry_loop(env):
    fake = fake_llm.install()
    fake.set_script(fake_llm.parse_script({"rules": [
        {"id": "flaky", "when": {"last_user_matches": "flaky"}, "times": 2, "respond": {"raise": "retryable", "status": 529}},
        {"id": "ok", "when": {"last_user_matches": "flaky"}, "respond": {"text": "fine"}},
        {"id": "fatal", "when": {"last_user_matches": "fatal"}, "respond": {"raise": "fatal"}},
    ]}))
    r = run(llm_provider.call_llm("claude-sonnet-4-6", [{"role": "user", "content": "flaky"}]))
    assert r.text == "fine"
    assert [c["rule_id"] for c in fake.calls] == ["flaky", "flaky", "ok"]
    with pytest.raises(fake_llm.FakeLLMError) as ei:
        run(llm_provider._call_llm_once("claude-sonnet-4-6", [{"role": "user", "content": "fatal"}]))
    assert not llm_provider._is_retryable(ei.value)
    fake.clear_calls()
    with pytest.raises(fake_llm.FakeLLMError):
        run(llm_provider.call_llm("claude-sonnet-4-6", [{"role": "user", "content": "fatal"}]))
    assert len(fake.calls) == 1  # fatal is not retried
    err = fake_llm.FakeLLMError("x", 529)
    assert llm_provider._is_retryable(err)
    fake.clear_calls()
    with pytest.raises(fake_llm.FakeLLMUnmatched):
        run(llm_provider.call_llm("claude-sonnet-4-6", [{"role": "user", "content": "nothing"}]))
    assert len(fake.calls) == 1 and fake.calls[0]["rule_id"] is None


def test_is_configured_follows_fake_state(env):
    fake = fake_llm.install()
    assert llm_provider.is_configured("claude-sonnet-4-6") is True
    fake.unavailable = True
    assert llm_provider.is_configured("claude-sonnet-4-6") is False


def test_is_configured_without_fake_needs_credentials(monkeypatch):
    fake_llm.uninstall()
    for k in ("ANTHROPIC_API_KEY", "WORKFLOW_ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("src.core.config.settings.anthropic_api_key", None)
    assert llm_provider.is_configured("claude-sonnet-4-6") is False
    monkeypatch.setenv("WORKFLOW_ANTHROPIC_API_KEY", "sk-test")
    assert llm_provider.is_configured("claude-sonnet-4-6") is True


def test_call_log_is_a_ring_buffer_of_500():
    f = _fake({"rules": [{"id": "a", "respond": {"text": "x"}}]})
    for _ in range(510):
        run(f.call("m", [{"role": "user", "content": "q"}]))
    assert len(f.calls) == 500 and f.calls[0]["seq"] == 11 and f.calls[-1]["seq"] == 510


# ── /api/_test/* guards (in-process ASGI, so a non-loopback client can be simulated) ─────────


def _client(host: str):
    import httpx
    from fastapi import FastAPI

    from src.routes.test_hooks import LoopbackOnlyTestPaths, router
    app = FastAPI()
    app.add_middleware(LoopbackOnlyTestPaths)
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 5555)), base_url="http://t")


def test_test_routes_need_token_and_loopback(env):
    fake_llm.install()
    tok = {"X-Test-Token": "x" * 32}

    async def go():
        async with _client("127.0.0.1") as c:
            ok = await c.get("/api/_test/health", headers=tok)
            no_tok = await c.get("/api/_test/health")
            bad_tok = await c.get("/api/_test/health", headers={"X-Test-Token": "y" * 32})
            put = await c.put("/api/_test/fake-llm/script", headers=tok,
                              json={"rules": [{"id": "a", "respond": {"text": "hi"}}]})
            bad = await c.put("/api/_test/fake-llm/script", headers=tok, json={"rules": [{"id": "a", "respond": {}}]})
        async with _client("10.1.2.3") as c:
            remote = await c.get("/api/_test/health", headers=tok)
        return ok, no_tok, bad_tok, put, bad, remote
    ok, no_tok, bad_tok, put, bad, remote = run(go())
    assert ok.status_code == 200 and ok.json()["fake_llm"] is True
    assert no_tok.status_code == 404 and bad_tok.status_code == 404 and remote.status_code == 404
    assert put.status_code == 200 and put.json() == {"rules": 1}
    assert bad.status_code == 422


def test_test_routes_404_when_not_installed(env):
    fake_llm.uninstall()

    async def go():
        async with _client("127.0.0.1") as c:
            return await c.get("/api/_test/health", headers={"X-Test-Token": "x" * 32})
    assert run(go()).status_code == 404


# ── respond.delay_ms (demo pacing) ──────────────────────────────────────────


def test_delay_ms_pauses_the_answer_and_zero_does_not():
    import time

    f = fake_llm.FakeLLM(fake_llm.parse_script({"rules": [
        {"id": "slow", "when": {"last_user_matches": "slow"}, "respond": {"text": "late", "delay_ms": 120}},
        {"id": "fast", "when": {"last_user_matches": "fast"}, "respond": {"text": "now", "delay_ms": 0}},
    ]}))
    t0 = time.monotonic()
    assert run(f.call("m", [{"role": "user", "content": "slow"}])).text == "late"
    assert time.monotonic() - t0 >= 0.11
    t1 = time.monotonic()
    assert run(f.call("m", [{"role": "user", "content": "fast"}])).text == "now"
    assert time.monotonic() - t1 < 0.1


@pytest.mark.parametrize("bad", [-1, "10", True, 60001])
def test_delay_ms_must_be_a_sane_number(bad):
    with pytest.raises(fake_llm.ScriptError, match="delay_ms"):
        fake_llm.parse_script({"rules": [{"id": "r", "when": {"last_user_matches": "x"}, "respond": {"text": "t", "delay_ms": bad}}]})
