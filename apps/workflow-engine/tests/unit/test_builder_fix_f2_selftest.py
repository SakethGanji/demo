"""F2: an empty list is a valid answer on empty tables; failures say which step, request, expected and actual."""

from __future__ import annotations

import asyncio

from src.services.backend_templates import selftest

STEP = {"id": "list", "method": "GET", "path": "/cases", "body": None, "query": {}, "save": {}, "twice": False, "kind": "plan",
        "expect": {"status": 200, "min_len": {"path": "rows", "n": 1}, "has": ["rows.0.id"]}}


def _run(body, counted):
    async def call(m, p, q, b, ro, h=None):
        return {"status": 200, "body": body, "duration_ms": 1.0, "error_code": None, "execution_id": "e"}

    async def counts():
        return counted
    return asyncio.run(selftest.run_steps([STEP], call, counts, {}, [], writes_allowed=True, time_left=lambda: 99))


def test_empty_list_on_empty_tables_passes():
    r = _run({"rows": []}, {"cases": 0})[0]
    assert r["ok"] is True and "empty list" in r["note"]


def test_empty_list_when_rows_exist_fails_with_full_detail():
    r = _run({"rows": []}, {"cases": 3})[0]
    assert r["ok"] is False
    assert r["step"] == "list" and r["request"]["path"] == "/cases"
    assert r["expected"]["min_len"]["n"] == 1 and r["actual"]["status"] == 200
    line = selftest.failure_lines([r])[0]
    assert "step 'list'" in line and "expected" in line and "actual status 200" in line


def test_a_wrong_non_empty_answer_still_fails():
    assert _run({"rows": [{"x": 1}]}, {"cases": 0})[0]["ok"] is False
