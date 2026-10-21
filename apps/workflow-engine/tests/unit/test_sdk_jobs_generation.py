"""``studio.jobs`` is generated like the other SDK namespaces, is callable under the default token, and the backend
builder's prompt teaches it. Also: the builder's write check is per function scope (a complex module reuses names)."""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache


@lru_cache
def _load_sdk():
    from src.sdk.generate import generate_python_sdk, sdk_dir

    root = sdk_dir()
    name = "_studio_jobs_test_" + generate_python_sdk()["version"][:12]
    spec = importlib.util.spec_from_file_location(name, root / "studio" / "__init__.py",
                                                  submodule_search_locations=[str(root / "studio")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_generated_package_has_a_jobs_namespace():
    from src.sdk.generate import generate_python_sdk

    files = generate_python_sdk()["files"]
    assert "studio/jobs.py" in files
    assert "from . import jobs\n" in files["studio/__init__.py"] and '"jobs"' in files["studio/__init__.py"]
    for fn in ("start", "get", "events", "frame", "artifacts", "artifact", "cancel"):
        assert f"def {fn}(" in files["studio/jobs.py"]


def test_jobs_is_a_broker_function_every_default_token_allows():
    from src.broker.policy import snake
    from src.broker.service import function_index, resolve_function

    assert function_index()["jobs"] == "Jobs" and snake("Jobs") == "jobs"
    assert resolve_function("jobs", None) == "Jobs"


def test_the_sdk_calls_send_the_contract_operations():
    studio = _load_sdk()
    sent = []

    def transport(node, parameters, items):
        sent.append((node, parameters))
        op = parameters["operation"]
        item = {"frame": {"content_type": "image/jpeg", "base64": "AA=="}} if op == "frame" else \
            {"artifacts": [{"name": "a.png"}]} if op == "artifacts" else {"job_id": "job_1", "op": op}
        return {"ok": True, "items": [item]}

    tok = studio._transport.use(transport)
    try:
        assert studio.jobs.start({"type": "test-run"}) == {"job_id": "job_1", "op": "start"}
        assert studio.jobs.get("job_1")["op"] == "get"
        studio.jobs.events("job_1", after=7)
        assert studio.jobs.frame("job_1") == {"content_type": "image/jpeg", "base64": "AA=="}
        assert studio.jobs.artifacts("job_1") == [{"name": "a.png"}]
        studio.jobs.artifact("job_1", "a.png")
        studio.jobs.cancel("job_1")
    finally:
        studio._transport._current.reset(tok)
    assert {n for n, _ in sent} == {"Jobs"}
    assert [p for _, p in sent] == [
        {"operation": "start", "body": {"type": "test-run"}}, {"operation": "get", "job_id": "job_1"},
        {"operation": "events", "job_id": "job_1", "after": 7}, {"operation": "frame", "job_id": "job_1"},
        {"operation": "artifacts", "job_id": "job_1"}, {"operation": "artifact", "job_id": "job_1", "name": "a.png"},
        {"operation": "cancel", "job_id": "job_1"}]


def test_the_frame_is_none_when_there_is_none():
    studio = _load_sdk()
    tok = studio._transport.use(lambda n, p, i: {"ok": True, "items": [{"frame": None}]})
    try:
        assert studio.jobs.frame("job_1") is None
    finally:
        studio._transport._current.reset(tok)


def test_db_all_pages_by_id_and_page_sorts_and_slices():
    studio = _load_sdk()
    rows = [{"id": i, "name": f"r{i % 7}", "created_at": f"2026-01-{1 + i % 28:02d}"} for i in range(1, 2501)]
    calls = []

    def transport(node, p, items):
        calls.append(p)
        last = p["filter"]["$and"][1]["id"]["$gt"] if "$and" in p["filter"] else p["filter"]["id"]["$gt"]
        out = [r for r in rows if r["id"] > last][: p["limit"]]
        return {"ok": True, "items": out}

    tok = studio._transport.use(transport)
    try:
        assert len(studio.db.table("t").all()) == 2500 and len(calls) == 3
        assert studio.db.table("t").count({"name": "r1"}) == 2500   # the stub ignores the filter; the shape is what counts
        assert calls[-1]["filter"]["$and"][0] == {"name": "r1"} and calls[-1]["sort"] == "id"
        page = studio.db.table("t").page(sort="-id", limit=10, offset=20)
        assert page["total"] == 2500 and [r["id"] for r in page["rows"]] == list(range(2480, 2470, -1))
    finally:
        studio._transport._current.reset(tok)


def test_the_reference_and_builder_prompt_teach_jobs_and_complex_backends():
    from src.sdk.generate import catalogue, reference
    from src.services.module_generator import RULES, build_system_prompt

    ref = reference()
    names = {e["name"] for e in ref["entries"]}
    assert {f"studio.jobs.{op}" for op in ("start", "get", "events", "frame", "artifacts", "artifact", "cancel", "control")} <= names
    assert {"studio.db.table(name).page", "studio.db.table(name).all", "studio.db.table(name).count"} <= names
    facts = {"slug": "p", "environments": ["default"], "tables": [], "routes": [], "modules": [], "secrets": {},
             "egress": [], "jobs": True}
    prompt = build_system_prompt(ref, catalogue(), facts)
    # the prompt is lean: names only; exact signatures come from get_sdk_symbol and patterns from the templates
    assert "studio.jobs.start/get/events/frame" in prompt and "jobs service (studio.jobs): configured" in prompt
    for must in ("job_id", "poll", "upsert", "TRANSITIONS", "sync_pending_runs", "get_backend_template"):
        assert must in RULES
    from src.services.backend_templates import index as templates

    assert {"job-run-poll-sync", "dashboard-stats", "approval-state-machine", "crud-filters"} <= set(templates.names())
    assert "table(name).page" in templates.get_backend_template("crud-filters")["source"] or \
        "def list_items" in templates.get_backend_template("crud-filters")["source"]
    assert "run_workflow" not in RULES       # the jobs pattern never routes through workflows


def test_scan_writes_checks_each_function_against_its_own_table():
    from src.services.module_generator import scan_writes

    src = '''
import studio

def a(req):
    t = studio.db.table("runs")
    t.insert({"label": "x"})

def b(req):
    t = studio.db.table("cases")
    t.update(1, {"title": "y"})

def c(req):
    t = studio.db.table("runs")
    t = studio.db.table("cases")
    t.insert({"ambiguous": 1})
'''
    got = {(w["table"], w["column"]) for w in scan_writes(src)}
    assert got == {("runs", "label"), ("cases", "title")}
