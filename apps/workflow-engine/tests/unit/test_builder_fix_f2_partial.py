"""F2: a build that delivers less than it planned is continued, then reported partial, never plain success."""

from __future__ import annotations

from src.services import module_generator as mg
from tests.unit.test_builder_wp2 import BASE, _gen

PLAN = ["GET /cases", "GET /runs", "GET /stats"]


def _add(path):
    return ("patch_module", {"ops": [{"op": "add_function", "source": f'@api.get("{path}")\ndef r{path.strip("/")}(req):\n    """R"""\n    return {{}}'}],
                             "summary": "more", "remaining": []})


def test_missing_planned_routes_force_a_continuation_until_delivered(monkeypatch):
    first = ("emit_module", {"module_name": "qa", "source": BASE, "summary": "s", "remaining": [], "planned_routes": PLAN})
    events, sent = _gen(monkeypatch, [[first], [_add("/runs")], [_add("/stats")]])
    res = events[-1]
    assert res["type"] == "result" and res.get("status") != "partial" and "missing" not in res
    assert "You still owe" in sent[1]["messages"][-1]["content"] and "GET /runs" in sent[1]["messages"][-1]["content"]
    assert res["stats"]["continuations"] == 2


def test_still_missing_after_the_budget_is_partial_with_the_list(monkeypatch):
    first = ("emit_module", {"module_name": "qa", "source": BASE, "summary": "s", "remaining": [], "planned_routes": PLAN})
    stall = ("patch_module", {"ops": [{"op": "add_function", "source": '@api.get("/other")\ndef other(req):\n    """O"""\n    return {}'}],
                              "summary": "x", "remaining": []})
    events, _ = _gen(monkeypatch, [[first]] + [[("patch_module", {"ops": [{"op": "add_function", "source":
                     f'@api.get("/o{i}")\ndef o{i}(req):\n    """O"""\n    return {{}}'}], "summary": "x", "remaining": []})]
                     for i in range(mg.MAX_CONTINUATIONS)])
    res = events[-1]
    assert res["type"] == "result" and res["status"] == "partial"
    assert res["missing"] == ["GET /runs", "GET /stats"] and "PARTIAL" in res["summary"]
    assert any(w["code"] == "partial_build" for w in res["analysis"]["warnings"])


def test_leftover_remaining_is_partial_too(monkeypatch):
    first = ("emit_module", {"module_name": "qa", "source": BASE, "summary": "s", "remaining": ["triage"]})
    more = lambda i: [("patch_module", {"ops": [{"op": "add_function", "source": f'@api.get("/o{i}")\ndef o{i}(req):\n    """O"""\n    return {{}}'}],
                                        "summary": "x", "remaining": ["triage"]})]
    events, _ = _gen(monkeypatch, [[first]] + [more(i) for i in range(mg.MAX_CONTINUATIONS)])
    assert events[-1]["status"] == "partial" and events[-1]["missing"] == ["triage"]


def test_provider_without_the_flag_is_still_seen_as_truncated():
    from types import SimpleNamespace as S
    assert mg._truncated(S(stop_reason="max_tokens")) and mg._truncated(S(finish_reason="length"))
    assert mg._truncated(S(usage=S(output_tokens=mg.MAX_OUTPUT_TOKENS)))
    assert not mg._truncated(S(usage=S(output_tokens=100)))


def test_result_exposes_declared_tables_for_the_save(monkeypatch):
    first = ("emit_module", {"module_name": "qa", "source": BASE, "summary": "s",
                             "tables": [{"name": "cases", "columns": [{"name": "k", "type": "string"}]}]})
    events, _ = _gen(monkeypatch, [[first]])
    assert events[-1]["tables"][0]["name"] == "cases"
