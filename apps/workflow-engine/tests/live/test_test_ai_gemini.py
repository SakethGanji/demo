"""Stage 3B live tier (CONTRACTS-STAGE3B §7): the AI test helpers against REAL Gemini, on Refund triage.

Opt-in and billable. Skipped unless WORKFLOW_LIVE_GEMINI=1 and the test-AI model has credentials. Run from
apps/workflow-engine with the ignored env file loaded into the shell (never printed, never copied):

    set -a; . ./.env.local; set +a
    WORKFLOW_LIVE_GEMINI=1 venv/bin/pytest tests/live/test_test_ai_gemini.py -q -s

The draft check goes through the real engine route (its own throwaway DB, no fake LLM) because the dry run needs
a database; that is the same ``draft_tests`` code path. Variations call ``propose_variations`` in-process.
Output is counts and names only.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest

from src.engine import llm_provider
from src.engine.test_ai import TEST_AI_MODEL
from src.schemas.workflow import normalize_tests
from src.services.test_ai_service import ASSERTION_OPS, propose_variations

ENABLED = os.environ.get("WORKFLOW_LIVE_GEMINI") == "1"
pytestmark = [
    pytest.mark.skipif(not ENABLED, reason="set WORKFLOW_LIVE_GEMINI=1 to make real Gemini calls"),
    pytest.mark.skipif(ENABLED and not llm_provider.is_configured(TEST_AI_MODEL), reason="no Gemini credentials configured"),
]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
LOCKED_ID = "t_a0000001"
RISK_MOCK = {"http": {"status": 200, "body": {"decision": "review", "score": 0.7}}}
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "status": "logged"}]}
CELLS = [
    {"node": "Risk API", "category": "ext_error"},
    {"node": "Amount over 500", "category": "boundary"},
    {"node": "Normalize", "category": "missing"},
    {"node": "Log approved", "category": "normal"},
]


def _covers(t: dict[str, Any]) -> set[str]:
    if t.get("covers") is not None:
        return set(t["covers"])
    if t["target"]["kind"] == "node":
        return {t["target"]["node"]}
    return {a["node"] for a in t.get("expect") or [] if a.get("node")}


@pytest.fixture(scope="module")
def engine_and_client() -> Iterator[Any]:
    from tests.integration.conftest import EngineServer, StudioClient, TestDatabase

    db = TestDatabase()
    db.create()
    srv = None
    try:
        db.migrate("apply")
        creds = {k: v for k, v in os.environ.items() if k.startswith(("GEMINI_", "GOOGLE_", "WORKFLOW_GEMINI_", "LLM_PROXY_", "WORKFLOW_TEST_AI_MODEL", "WORKFLOW_MODULE_GEN_MODEL"))}
        srv = EngineServer(db, extra_env=creds, label="-live-gemini")
        srv.start()
        yield srv, StudioClient(srv.base_url)
    finally:
        if srv:
            srv.stop()
        db.drop()


@pytest.fixture(scope="module")
def refund(engine_and_client: Any) -> tuple[str, dict[str, Any]]:
    _, api = engine_and_client
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", "http://risk.example.test/v1/score"))
    wf["name"] = "Refund triage live"
    wf["settings"] = {}
    wf["test_defaults"] = {"mocks": {"Risk API": RISK_MOCK, "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}
    wf["tests"] = [{
        "id": LOCKED_ID, "name": "small refund auto-approved", "target": {"kind": "workflow"}, "category": "normal",
        "input": {"items": [{"body": {"order_id": "o-1", "amount": 100, "reason": "late"}}]}, "mocks": {},
        "expect": [{"node": "Amount over 500", "port": "false", "op": "count", "value": 1},
                   {"node": "Log approved", "op": "status", "value": "success"}],
    }]
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert api.post(f"/workflows/{wid}/tests/{LOCKED_ID}/approve", {"approved_by": "live"}).status_code == 200
    return wid, api.get(f"/workflows/{wid}").json()["definition"]


def test_live_ai_draft_on_refund_triage(engine_and_client: Any, refund: tuple[str, dict[str, Any]]) -> None:
    _, api = engine_and_client
    wid, definition = refund
    before = json.dumps(api.get(f"/workflows/{wid}").json()["definition"]["tests"], sort_keys=True)
    r = api.post(f"/workflows/{wid}/tests/ai-draft", {"cells": CELLS, "max": 6})
    assert r.status_code == 200, f"ai-draft answered {r.status_code}: {r.text[:300]}"
    out = r.json()
    drafts, rejected = out["drafts"], out["rejected"]
    print(f"\n[live] model={out['model']} drafts={len(drafts)} rejected={len(rejected)}")
    for d in drafts:
        print(f"[live]   {d['category']:<10} {d['ai_run']['status']:<7} {d['name'][:70]}")
    for x in rejected:
        print(f"[live]   rejected: {x['reason'][:100]}")
    assert len(drafts) >= 1
    names = {n["name"] for n in definition["nodes"]}
    for d in drafts:
        assert d["status"] == "draft" and d["locked"] is False and d["source"] == "ai" and d["id"] != LOCKED_ID
        assert "approved_by" not in d and "approved_at" not in d
        plain = {k: v for k, v in d.items() if k != "ai_run"}
        assert normalize_tests([plain])[0]["id"] == d["id"]
        assert d["ai_run"]["status"] in ("passed", "failed", "error") and isinstance(d["ai_run"]["failures"], list)
        assert all(a["op"] in ASSERTION_OPS and a["node"] in names for a in d["expect"])
    assert any(d["category"] == c["category"] and c["node"] in _covers(d) for d in drafts for c in CELLS)
    assert json.dumps(api.get(f"/workflows/{wid}").json()["definition"]["tests"], sort_keys=True) == before
    passed = sum(d["ai_run"]["status"] == "passed" for d in drafts)
    print(f"[live] dry-run passed={passed}/{len(drafts)}")


def test_live_propose_variations(engine_and_client: Any, refund: tuple[str, dict[str, Any]]) -> None:
    _, api = engine_and_client
    wid, definition = refund
    r = api.post(f"/workflows/{wid}/check", {"definition": definition})
    shapes = r.json().get("shapes_by_node", {}) if r.status_code == 200 else {}
    base = [{"body": {"order_id": "o-1", "amount": 600, "reason": "late"}}]
    out = asyncio.run(propose_variations(definition, shapes, base, n=8))
    print(f"\n[live] variations={len(out)} sizes={[len(v) for v in out]}")
    assert 1 <= len(out) <= 8
    for items in out:
        assert isinstance(items, list) and items and all(isinstance(i, dict) for i in items)
        json.dumps(items)
