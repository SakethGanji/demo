"""Fix: the adapt reference follows the part (ai_builder._reference_workflow picks the workflow whose key is the part key)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from src.services import ai_builder


def _defn(tag: str) -> dict:
    return {"nodes": [{"name": tag, "type": "Start", "parameters": {}}], "connections": []}


class _Slice:
    def __init__(self, bundle):
        self.bundle = bundle

    def by_key(self, key):
        return {"props": {"reference_bundle": self.bundle}}


def _run(monkeypatch, part_key):
    bundle = {"workflows": [{"key": "main", "role": "entry", "definition": _defn("main")},
                            {"key": "assign", "role": "child", "definition": _defn("assign")}]}

    class _Repo:
        def __init__(self, s):
            pass

        async def load_slice(self, pid):
            return _Slice(bundle)

    class _Sess:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    import src.repositories.kg_repository as kr

    monkeypatch.setattr(kr, "KgRepository", _Repo)
    monkeypatch.setattr(ai_builder, "_sf", lambda: (lambda: _Sess()))
    out = asyncio.run(ai_builder._reference_workflow("p", {"adapt_from": {"pattern": "x"}}, part_key))
    return out["nodes"][0]["name"]


def test_matching_part_key_picks_that_workflow(monkeypatch):
    assert _run(monkeypatch, "assign") == "assign"


def test_no_match_or_no_part_falls_back_to_first_entry(monkeypatch):
    assert _run(monkeypatch, "nope") == "main"
    assert _run(monkeypatch, None) == "main"
