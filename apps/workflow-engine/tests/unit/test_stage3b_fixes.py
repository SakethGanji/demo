"""Stage 3B audit fixes: prompt redaction, fuzz time cap, live-node limits, smoke schedule floor, drift guard."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.services import fuzz, smoke
from src.services import test_ai_service as svc

pytestmark = pytest.mark.anyio

SECRET = "sk-live-9f8e7d6c5b4a"


def _definition() -> dict[str, Any]:
    return {
        "name": "Refunds",
        "nodes": [
            {"name": "Webhook", "type": "Webhook", "parameters": {}},
            {"name": "Risk API", "type": "HttpRequest", "parameters": {
                "method": "POST", "url": f"https://api.test/x?api_key={SECRET}&q=1",
                "headers": {"Authorization": f"Bearer {SECRET}", "X-Api-Key": SECRET, "Accept": "application/json"},
                "headerParameters": [{"name": "Cookie", "value": SECRET}, {"name": "X-Trace", "value": "ok"}],
                "body": "{{ $secrets.RISK_KEY }}", "_credentials": {"httpHeaderAuth": {"id": "cred_1"}},
            }},
        ],
        "connections": [],
        "tests": [],
        "test_defaults": {"mocks": {"Risk API": {"http": {"status": 200, "headers": {"Set-Cookie": SECRET, "x-ok": "1"},
                                                           "body": {"token": SECRET, "score": 3}}}}},
    }


def test_redaction_removes_credentials_from_the_draft_prompt():
    prompt = svc.build_draft_prompt(_definition(), {}, [], [{"node": "Risk API", "category": "ext_error"}], 5, {SECRET})
    assert SECRET not in prompt and "cred_1" not in prompt and "RISK_KEY" not in prompt
    data = json.loads(prompt)
    params = next(n for n in data["definition"]["nodes"] if n["name"] == "Risk API")["parameters"]
    assert params["headers"]["Authorization"] == "***" and params["headers"]["X-Api-Key"] == "***"
    assert params["headers"]["Accept"] == "application/json"  # harmless values stay
    assert [p["value"] for p in params["headerParameters"]] == ["***", "ok"]
    assert "_credentials" not in params and params["body"] == "***"
    assert "api_key=***" in params["url"] and "q=1" in params["url"]
    mock = data["test_defaults"]["mocks"]["Risk API"]["http"]
    assert mock["headers"]["Set-Cookie"] == "***" and mock["body"]["token"] == "***" and mock["body"]["score"] == 3


def test_known_secret_values_are_scrubbed_anywhere():
    d = _definition()
    d["nodes"][1]["parameters"]["note"] = f"call with {SECRET} please"
    assert SECRET not in svc.build_draft_prompt(d, {}, [], [], 5, {SECRET})


async def test_variations_prompt_redacts_base_items(monkeypatch):
    seen = {}

    async def fake(task, system, user, *, model=None):
        seen["user"] = user
        return {"variations": []}

    monkeypatch.setattr(svc, "ask_json", fake)
    await svc.propose_variations(_definition(), {}, [{"amount": 5, "password": SECRET, "auth": {"authorization": SECRET}}], 3)
    assert SECRET not in seen["user"] and '"amount": 5' in seen["user"]


def test_replay_inputs_never_seed_fuzzing():
    d = {"tests": [
        {"id": "t_a0000001", "name": "replay", "source": "replay", "target": {"kind": "workflow"},
         "input": {"items": [{"ssn": "123-45-6789"}]}, "expect": []},
        {"id": "t_b0000002", "name": "human", "source": "human", "target": {"kind": "workflow"},
         "input": {"items": [{"amount": 5}]}, "expect": []},
    ]}
    base, _ = fuzz._base_input(d, {"kind": "workflow"}, {}, "Webhook")
    assert base == {"amount": 5}
    d["tests"] = d["tests"][:1]
    base, _ = fuzz._base_input(d, {"kind": "workflow"}, {}, "Webhook")
    assert "ssn" not in base


async def test_fuzz_time_cap_bounds_a_single_hanging_run(monkeypatch):
    wf = SimpleNamespace(description="", nodes=[])
    stored = SimpleNamespace(id="wf_1", name="W", project_id="p", workflow=wf)

    class Repo:
        _session = None

        async def get(self, _):
            return stored

    async def lookups(*a, **k):
        return None

    async def setup(*a, **k):
        return None

    async def hang(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(fuzz, "workflow_to_definition", lambda w: {"nodes": [], "tests": []})
    monkeypatch.setattr(fuzz, "definition_to_workflow", lambda *a, **k: wf)
    monkeypatch.setattr(fuzz, "target_run_options", lambda *a, **k: {"start_node_name": "Webhook"})
    monkeypatch.setattr(fuzz, "build_lookups", lookups)
    monkeypatch.setattr(fuzz, "check_workflow", lambda *a, **k: {"shapes_by_node": {}})
    monkeypatch.setattr(fuzz, "_setup", setup)
    monkeypatch.setattr(fuzz, "_run_one", hang)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    out = await fuzz.fuzz_workflow(Repo(), "wf_1", count=5, time_budget_s=0.3)
    assert loop.time() - t0 < 3
    assert out["runs"] == 1 and out["findings"][0]["kind"] == "crash" and "too long" in out["findings"][0]["error"]["message"]


# ── smoke config limits ──────────────────────────────────────────────────────────────────────


def test_only_http_nodes_may_go_live():
    d = {"nodes": [{"name": "A", "type": "HttpRequest"}, {"name": "B", "type": "Postgres"}, {"name": "C", "type": "SendEmail"}]}
    assert smoke._live_types_problem(d, ["A"]) is None
    msg = smoke._live_types_problem(d, ["A", "B", "C"])
    assert msg and "B" in msg and "C" in msg


@pytest.mark.parametrize("cron", ["* * * * *", "*/4 * * * *", "0,1 * * * *", "*/10 * * * * */20"])
def test_too_frequent_schedules_are_refused(cron):
    with pytest.raises(smoke.SmokeError) as e:
        smoke.check_schedule(cron)
    assert e.value.status == 422


@pytest.mark.parametrize("cron", ["*/5 * * * *", "0 * * * *", "30 6 * * 1-5"])
def test_sane_schedules_pass(cron):
    smoke.check_schedule(cron)


def test_live_client_record_hides_auth_header_values():
    from src.engine.test_mode import _safe_headers

    h = _safe_headers({"Authorization": "Bearer abc", "x-api-key": "k", "Accept": "a/b", "Cookie": "s=1"})
    assert h == {"Authorization": "***", "x-api-key": "***", "Accept": "a/b", "Cookie": "***"}


# ── drift can't change a run ─────────────────────────────────────────────────────────────────


async def test_a_raising_drift_check_is_swallowed(monkeypatch):
    from src.services import drift

    async def boom(session, execution_id):
        raise RuntimeError("drift exploded")

    rolled = []

    class Session:
        async def rollback(self):
            rolled.append(1)

    monkeypatch.setattr(drift, "check_execution", boom)
    await drift.guarded_check_execution(Session(), "exec_1")  # does not raise
    assert rolled == [1]


async def test_repository_hook_survives_a_raising_drift_check(monkeypatch):
    from src.repositories.execution_repository import ExecutionRepository
    from src.services import drift

    async def boom(session, execution_id):
        raise RuntimeError("drift exploded")

    class Session:
        async def rollback(self):
            pass

    monkeypatch.setattr(drift, "check_execution", boom)
    repo = ExecutionRepository.__new__(ExecutionRepository)
    repo._session = Session()
    ctx = SimpleNamespace(execution_id="e", workflow=SimpleNamespace(nodes=[SimpleNamespace(type="HttpRequest")]))
    await repo._check_drift(ctx)  # does not raise
