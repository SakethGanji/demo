"""WP-C: with an approved plan the module generator builds from it and asks nothing: contract in the first message, plan tables
override the model's, unplanned tables/columns/foreign writes/duplicate helpers are repair problems, missing planned routes make
the build partial, the result carries plan_version and the remaining build order."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.services import module_generator as mg
from tests.unit.builder_plan_fixture import BODY, install

RUNS_SRC = '''from studio import api, Response, HTTPError
import studio


@api.post("/runs")
def start_run(req):
    """Start. Returns {id, job_id}"""
    t = studio.db.table("runs")
    return t.insert({"test_id": 1, "status": "queued"})


@api.get("/runs")
def list_runs(req):
    """List runs. Returns {rows, total, limit, offset}"""
    return {"rows": studio.db.table("runs").all(), "total": 0, "limit": 50, "offset": 0}


@api.get("/runs/{id}")
def get_run(req):
    """One run."""
    return studio.db.table("runs").get(int(req.params["id"]))
'''
RUNS_TABLES = [{"name": "runs", "columns": [{"name": "test_id", "type": "integer"}]}]


def _facts(tables=(), modules=()):
    return {"slug": "p", "environments": ["default"], "tables": list(tables), "routes": [], "modules": list(modules),
            "secrets": {}, "egress": [], "jobs": True}


def _drive(monkeypatch, replies, *, req_kw=None, facts=None, shared_source=None, saved=None):
    from src.engine import llm_provider
    from src.services.draft_runner import draft_routes
    from tests.unit.test_builder_wp2 import _Sess

    install(monkeypatch)
    sent: list[dict] = []
    it = iter(replies)
    remembered: list[dict] = []

    async def fake_llm(model, messages, temperature=0.2, tools=None, **kw):
        sent.append({"messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools or []]})
        calls = [SimpleNamespace(name=n, args=a, id=f"t{len(sent)}_{i}") for i, (n, a) in enumerate(next(it))]
        return SimpleNamespace(tool_calls=calls, text=None, output_truncated=False, get_assistant_message=lambda: {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}} for c in calls]})

    async def fake_db(self, fn):
        if fn.__name__ == "load":
            return SimpleNamespace(id="proj"), facts or _facts(), set(), saved
        if fn.__name__ == "load_plan":
            return await fn(None)
        if fn.__name__ == "do_analyse":
            return await fn(_Sess())
        return {"ok": 1}

    async def fake_analyse(s, p, **kw):
        """The real plan checks on the real static scan; the DB-bound parts are not needed here."""
        src, plan = kw["source"], kw["plan"]
        existing = {t["name"]: t for t in kw["facts"]["tables"]}
        problems, warnings = ([], [])
        if plan:
            problems, warnings = mg.plan_problems(plan, source=src, usage=mg.scan_usage(src), existing=existing,
                                                  model_tables=set(plan.get("model_tables") or ()), shared_source=shared_source)
        return {"routes": {"created": [], "updated": [], "removed": []}, "errors": [], "conflicts": [], "tables": [],
                "secrets": [], "warnings": warnings, "_problems": problems, "_decls": draft_routes(src)}

    async def fake_remember(self, project, name, source, spec, answers, decisions, plan, declared, **kw):
        remembered.append({"spec": spec, "decisions": decisions, "declared": declared, **kw})

    monkeypatch.setattr(llm_provider, "call_llm", fake_llm)
    monkeypatch.setattr(mg.Generation, "_db", fake_db)
    monkeypatch.setattr(mg.Generation, "_remember", fake_remember)
    monkeypatch.setattr(mg, "analyse", fake_analyse)
    req = mg.GenerateRequest(prompt="Build the runs module [plan:runs]", self_test=False, **(req_kw or {}))

    async def collect():
        return [ev async for ev in mg.Generation("proj", req, timeout_s=20).events()]
    return asyncio.run(collect()), sent, remembered


def _emit(source=RUNS_SRC, **extra):
    return ("emit_module", {"module_name": "runs", "source": source, "summary": "s", "remaining": [], "tables": RUNS_TABLES, **extra})


def test_clarify_is_skipped_and_the_contract_is_in_the_first_message(monkeypatch):
    events, sent, _ = _drive(monkeypatch, [[_emit()]], req_kw={"clarify": True})
    assert len(sent) == 1 and sent[0]["tools"][0] == "emit_module"            # no spec call, straight to the module
    assert not any(e.get("status") == "clarify" for e in events)
    text = "\n".join(m["content"] for m in sent[0]["messages"] if isinstance(m.get("content"), str))
    assert "Build exactly this" in text and "POST /runs" in text and "test_ids*" in text and "409 illegal_transition" in text
    assert "from common import audit" in text
    res = events[-1]
    assert res["type"] == "result" and res["status"] == "built" and res["plan_version"] == 3 and res["plan_module"] == "runs"


def test_plan_tables_override_the_models_declaration(monkeypatch):
    events, _, rem = _drive(monkeypatch, [[_emit()]])
    res = events[-1]
    runs = next(t for t in res["tables"] if t["name"] == "runs")
    assert {c["name"] for c in runs["columns"]} == {"test_id", "status", "job_id"}      # the plan's columns, not the emitted one
    assert rem[0]["plan_version"] == 3 and rem[0]["spec"] is None and rem[0]["declared"] == []
    assert any("plan's columns were used" in d for d in rem[0]["decisions"])


def test_unplanned_table_gets_a_repair_turn(monkeypatch):
    bad = RUNS_SRC + '\n\ndef _x():\n    return studio.db.table("scratchpad").all()\n'
    events, sent, _ = _drive(monkeypatch, [[_emit(bad, tables=RUNS_TABLES + [{"name": "scratchpad", "columns": [{"name": "a", "type": "string"}]}])],
                                          [_emit()]])
    repair = sent[1]["messages"][-1]["content"]
    assert repair.startswith(mg.REPAIR_HEADER) and "unplanned_table" in repair and "scratchpad" in repair
    assert events[-1]["type"] == "result"


def test_unplanned_column_and_foreign_write_are_repair_problems():
    src = ('import studio\n@studio_api\ndef f(req):\n    studio.db.table("runs").insert({"test_id": 1, "mood": "x"})\n'
           '    studio.db.table("tests").insert({"title": "t"})\n    studio.db.table("audit_log").insert({"event": "e"})\n')
    ctx = mg.plan_context(BODY, 3, "runs")
    problems, _ = mg.plan_problems(ctx, source=src, usage=mg.scan_usage(src), existing={}, model_tables=set(), shared_source=None)
    codes = {(p["code"], p["name"]) for p in problems}
    assert ("unplanned_column", "runs") in codes
    assert ("foreign_write", "tests") in codes                    # tests is owned by tests_library
    assert not any(c == "foreign_write" and n == "audit_log" for c, n in codes)   # runs is a listed writer
    assert not any(c == "foreign_write" and n == "runs" for c, n in codes)


def test_redefining_a_shared_helper_is_a_duplicate_helper_repair(monkeypatch):
    shared = 'def audit(event, entity, entity_id, caller, detail=None):\n    return {"event": event, "entity": entity}\n'
    dup = RUNS_SRC + '\n\ndef audit(event, entity, entity_id, caller, detail=None):\n    return {"event": event, "entity": entity}\n'
    events, sent, _ = _drive(monkeypatch, [[_emit(dup)], [_emit()]], shared_source=shared)
    repair = sent[1]["messages"][-1]["content"]
    assert "duplicate_helper" in repair and "from common import audit" in repair
    assert events[-1]["type"] == "result"


def test_a_helper_with_the_planned_name_is_a_duplicate_even_if_the_body_differs():
    ctx = mg.plan_context(BODY, 3, "runs")
    src = 'def audit(a):\n    return {"x": a, "y": 2}\n'
    problems, _ = mg.plan_problems(ctx, source=src, usage=mg.scan_usage(src), existing={}, model_tables=set(),
                                   shared_source="def audit(a, b):\n    return 1\n")
    assert [p["code"] for p in problems] == ["duplicate_helper"]
    # the shared module itself may define it
    own = mg.plan_context(BODY, 3, "common")
    assert mg.plan_problems(own, source=src, usage=mg.scan_usage(src), existing={}, model_tables=set(), shared_source=None)[0] == []


def test_missing_planned_route_is_partial_and_listed(monkeypatch):
    short = RUNS_SRC.split('@api.get("/runs/{id}")')[0]
    def stall(i):
        return [("patch_module", {"ops": [{"op": "add_function", "source": f'@api.get("/o{i}")\ndef o{i}(req):\n    """O"""\n    return {{}}'}],
                                  "summary": "x", "remaining": []})]
    events, _, _ = _drive(monkeypatch, [[_emit(short)]] + [stall(i) for i in range(mg.MAX_CONTINUATIONS + 1)])
    res = events[-1]
    assert res["status"] == "partial" and res["missing"] == ["GET /runs/{id}"] and res["plan_version"] == 3
    assert any(w["code"] == "partial_build" for w in res["analysis"]["warnings"])


def test_next_modules_is_the_remaining_build_order(monkeypatch):
    facts = _facts(modules=["common", "tests_library"])
    events, _, _ = _drive(monkeypatch, [[_emit(next_modules=["something_else"])]], facts=facts)
    assert events[-1]["next_modules"] == ["triage"]                 # not the model's list, not built ones, not the app


def test_existing_table_missing_planned_columns_is_schema_pending():
    ctx = mg.plan_context(BODY, 3, "runs")
    existing = {"runs": {"name": "runs", "columns": [{"name": "test_id", "type": "integer"}]}}
    src = 'import studio\nstudio.db.table("runs").all()\n'
    problems, warnings = mg.plan_problems(ctx, source=src, usage=mg.scan_usage(src), existing=existing, model_tables=set(),
                                          shared_source=None)
    assert problems == [] and [w["code"] for w in warnings] == ["schema_pending"]
    assert "2 planned column(s)" in warnings[0]["message"] and "job_id" in warnings[0]["message"]


def test_plan_module_resolution_order():
    r = mg.resolve_plan_module
    assert r(BODY, "triage", "runs", "x [plan:common]") == "triage"        # explicit field first
    assert r(BODY, None, "runs", "x [plan:common]") == "runs"              # then the module name
    assert r(BODY, None, "nope", "build [plan:common]") == "common"        # then the prompt token
    assert r(BODY, None, None, "no token") is None and r(BODY, "ghost", None, "") is None


def test_use_plan_false_keeps_the_old_flow(monkeypatch):
    events, sent, _ = _drive(monkeypatch, [[("emit_spec", {"summary": "s", "entities": [], "workflows": [], "roles": [],
                                                           "external_systems": [], "acceptance_criteria": [], "questions": []})]],
                             req_kw={"clarify": True, "use_plan": False})
    assert any("emit_spec" in str(m.get("content")) for m in sent[0]["messages"])
