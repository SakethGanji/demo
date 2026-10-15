"""Characterization: AI chat SSE endpoints (no LLM key configured) and file browser."""

from __future__ import annotations

import json

import httpx

import pytest

pytestmark = pytest.mark.integration


def _sse(api, path: str, body: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    event = "message"
    try:
        with api.http.stream("POST", path, content=json.dumps(body), headers={"Content-Type": "application/json"}, timeout=90) as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and line[5:].strip():
                    out.append((event, json.loads(line[5:].strip())))
    except httpx.RemoteProtocolError:
        # Without an LLM key the provider may raise mid-stream (environment-dependent).
        out.append(("aborted", {}))
    return out


def test_app_builder_chat_streams_and_terminates_without_llm(api):
    # useAppBuilderChat.ts:49
    events = _sse(api, "/api/ai/app-builder/chat",
                  {"message": "hi", "api_execution_ids": [], "conversation_history": []})
    assert events and events[0][1].get("type") == "phase"
    assert events[-1][0] in ("done", "aborted")


def test_files_browse(api, tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    r = api.get(f"/files/browse?path={tmp_path}")
    assert r.status_code == 200
    assert any(e["name"] == "a.csv" for e in r.json()["entries"])
