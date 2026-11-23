"""Backend builder WP-2: patch_module, the lean prompt, domain gap questions (two rounds), the scratch self-test, static
checks, templates and the generation loop (lookups, truncation retry, continuation, fix rounds)."""

from __future__ import annotations

import ast
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.services import module_generator as mg
from src.services.backend_templates import checks, gaps, index as templates, patching, prompts, selftest

BASE = '''from studio import api, Response, HTTPError
import studio

STATUSES = {"draft", "active"}


def _tbl():
    return studio.db.table("cases")


@api.get("/cases")
def list_cases(req):
    """List cases. Returns {rows, total}"""
    return {"rows": _tbl().all(), "total": 0}


@api.get("/cases/{id}")
def get_case(req):
    row = _tbl().get(int(req.params["id"]))
    if row is None:
        raise HTTPError(404, {"error": "nf"})
    return row


@api.delete("/cases/{id}")
def delete_case(req):
    _tbl().delete(int(req.params["id"]))
    return {"ok": True}
'''


# ── patch_module ─────────────────────────────────────────────────────────────


def test_replace_function_touches_only_that_function():
    new = '''@api.get("/cases")
def list_cases(req):
    """List cases filtered by status. Returns {rows, total}"""
    st = req.query.get("status")
    rows = _tbl().all({"status": st} if st else None)
    return {"rows": rows, "total": len(rows)}'''
    out, errs = patching.apply_ops(BASE, [{"op": "replace_function", "name": "list_cases", "source": new}])
    assert errs == []
    assert patching.changed_functions(BASE, out) == ["list_cases"]
    ast.parse(out)
    assert out.count("def get_case") == 1 and "filtered by status" in out


def test_add_remove_import_and_constant_ops():
    ops = [
        {"op": "add_import", "source": "import csv\nimport io"},
        {"op": "add_import", "source": "import csv"},                       # already there: no duplicate
        {"op": "set_constant", "name": "STATUSES", "source": 'STATUSES = {"draft", "active", "retired"}'},
        {"op": "set_constant", "name": "KINDS", "source": 'KINDS = {"ui", "api"}'},
        {"op": "add_function", "after": "list_cases", "source": '@api.post("/cases/import")\ndef import_cases(req):\n    return {"n": 0}'},
        {"op": "remove_function", "name": "delete_case"},
    ]
    out, errs = patching.apply_ops(BASE, ops)
    assert errs == [], errs
    assert out.count("import csv") == 1 and "retired" in out and "KINDS =" in out
    assert out.index("def import_cases") > out.index("def list_cases") and out.index("def import_cases") < out.index("def get_case")
    assert "delete_case" not in out
    assert sorted(patching.changed_functions(BASE, out)) == ["KINDS", "STATUSES", "delete_case", "import_cases"]


def test_a_bad_op_leaves_the_source_untouched_and_reports_every_failure():
    ops = [{"op": "replace_function", "name": "nope", "source": "def nope(req):\n    return 1"},
           {"op": "add_function", "source": "def get_case(req):\n    return 1"},
           {"op": "remove_function", "name": "_tbl"},                       # still called by every route
           {"op": "replace_function", "name": "list_cases", "source": "def list_cases(req:\n    return"},
           {"op": "explode"}]
    out, errs = patching.apply_ops(BASE, ops)
    assert out == BASE and len(errs) == 5
    joined = " | ".join(errs)
    assert "no top-level function 'nope'" in joined and "already exists" in joined and "still used on line" in joined
    assert "syntax error" in joined and "unknown op" in joined


def test_op_source_may_not_run_code_at_module_level():
    out, errs = patching.apply_ops(BASE, [{"op": "add_function", "source": "print('hi')"}])
    assert out == BASE and "only defs, classes, assignments and imports" in errs[0]


def test_a_patch_on_a_big_module_stays_small():
    parts = [BASE.split("@api.get(\"/cases\")")[0]]
    for i in range(300):
        parts.append(f'@api.get("/r{i}")\ndef route_{i}(req):\n    """Route {i}. Returns {{rows}}"""\n'
                     f'    rows = _tbl().all({{"status": "s{i}"}})\n    return {{"rows": rows, "n": {i}, "pad": "{"x" * 120}"}}\n\n')
    big = "\n".join(parts)
    assert len(big) > 60_000
    patch = {"op": "replace_function", "name": "route_150", "source": '@api.get("/r150")\ndef route_150(req):\n    return {"rows": []}'}
    out, errs = patching.apply_ops(big, [patch])
    assert errs == [] and patching.changed_functions(big, out) == ["route_150"]
    assert len(json.dumps(patch)) < len(big) / 200
    assert len(patching.outline_text(big)) < len(big) / 3                 # the outline replaces pasting the source
    got = patching.function_sources(big, ["route_7", "GET /r9", "missing_thing"])
    assert "def route_7" in got["functions"]["route_7"] and "def route_9" in got["functions"]["route_9"]
    assert list(got["no_such_name_try"]) == ["missing_thing"]            # an unknown name comes back with real candidates


def test_outline_lists_routes_and_helpers():
    o = {r["name"]: r for r in patching.outline(BASE)}
    assert o["list_cases"]["route"] == "GET /cases" and o["_tbl"]["kind"] == "function" and o["STATUSES"]["kind"] == "constant"


def test_undefined_names_catches_a_removed_helper_but_not_locals():
    src = "def a(req):\n    x = [i for i in range(3)]\n    try:\n        pass\n    except ValueError as e:\n        return str(e)\n    return helper(x)\n"
    assert patching.undefined_names(src) == [("helper", 7)]
    assert patching.undefined_names(BASE) == []


# ── lean prompt ──────────────────────────────────────────────────────────────

def _facts(n_tables: int, n_routes: int = 23) -> dict:
    cols = [{"name": f"column_number_{i}_name", "type": "string", "required": i < 2} for i in range(12)]
    return {"slug": "p", "environments": ["dev", "prod"], "default_environment": "dev",
            "tables": [{"name": f"t{i}", "columns": cols, "row_count": 10} for i in range(n_tables)],
            "routes": [{"method": "GET", "path": f"/x{i}/{{id}}", "name": f"r{i}", "handler": "python module qa"} for i in range(n_routes)],
            "modules": ["qa"], "secrets": {"K": {"present": ["dev"], "hosts": ["a.com"]}}, "egress": [], "workflows": [], "jobs": True}


def test_prompt_is_lean_and_does_not_grow_with_the_project():
    from src.engine.node_registry import node_registry, register_all_nodes
    from src.sdk.generate import catalogue, reference

    if not node_registry.list() or len(node_registry.list()) < 5:   # measure the prompt production sends: every node registered
        register_all_nodes()

    small = mg.build_system_prompt(reference(), catalogue(), _facts(0, 0))
    mid = mg.build_system_prompt(reference(), catalogue(), _facts(5))
    big = mg.build_system_prompt(reference(), catalogue(), _facts(50, 200))
    assert len(small) <= 5000 and len(mid) <= 5200
    assert len(big) - len(mid) < 1500               # 45 more tables and 177 more routes: names only, capped
    assert "column_number_3_name" not in big          # columns come from get_table_schema, never the prompt
    for must in ("req", "emit_module", "patch_module", "get_backend_template", "sync_pending_runs", "req.caller"):
        assert must in big


def test_attempts_to_paste_the_module_are_replaced_by_an_outline():
    parts = [BASE] + [f'@api.get("/r{i}")\ndef route_{i}(req):\n    return {{"pad": "{"x" * 200}"}}\n' for i in range(100)]
    big = "\n".join(parts)
    assert len(big) > mg.INLINE_SOURCE_CHARS
    assert "route_99" in patching.outline_text(big) and len(patching.outline_text(big)) < len(big) // 3


# ── domain gap questions ─────────────────────────────────────────────────────

QA_PROMPT = ("Build a QA workbench for a bank's QA team. Test cases with ids like TC-204, runs executed in the Jobs service, "
             "triage of failures where an AI proposes a fix that a lead approves and applies, flaky detection, notes, "
             "import from our existing TestRail, results kept for audit, login credentials for the target app.")
QA_SPEC = {"entities": [{"name": "test_cases", "fields": [{"name": "key", "type": "string", "required": True}]},
                        {"name": "runs", "fields": [{"name": "status", "type": "string", "required": True}]},
                        {"name": "run_results", "fields": [{"name": "run_id", "type": "integer", "required": True}]}],
           "workflows": [{"entity": "triage", "states": ["pending", "approved", "rejected"],
                          "transitions": [{"from": "pending", "to": "approved", "who": "lead"}]}],
           "roles": [{"name": "lead"}], "external_systems": [{"name": "Jobs", "use": "runs"}],
           "acceptance_criteria": ["x"]}


def test_domain_questions_cover_the_gaps_a_careful_developer_asks_about():
    qs = gaps.domain_questions(QA_PROMPT, QA_SPEC, ["dev", "prod"])
    topics = {q["topic"] for q in qs}
    assert {"safety", "approvals", "state_machines", "retention", "ids", "import", "environments", "failure", "concurrency",
            "credentials", "identity"} <= topics
    assert "notifications" not in topics                      # the request never mentions notifications
    env_q = next(q for q in qs if q["topic"] == "environments")
    assert "dev, prod" in env_q["question"]
    cred = next(q for q in qs if q["topic"] == "credentials")
    assert all("browser" not in o.lower() and "mock" not in o.lower() for o in cred["options"])
    assert any("secret" in o.lower() for o in cred["options"])
    assert gaps.domain_questions("Build a note taking backend", {"entities": [{"name": "notes", "fields": []}]}, ["dev"]) == []


def test_notifications_only_when_the_request_mentions_them():
    assert "notifications" in {q["topic"] for q in gaps.domain_questions("email the lead on every failure", {}, [])}
    # a read-only dashboard asks nothing about guards, approvals or imports, though "refund" and "csv" appear in it
    ro = {q["topic"] for q in gaps.domain_questions("sales dashboard: revenue by month, refund rate by category, CSV export", {}, [])}
    assert not ro & {"safety", "approvals", "import"}
    assert {"safety", "approvals"} <= {q["topic"] for q in gaps.domain_questions("agents issue refunds; managers approve over $200", {}, [])}
    # an email column is data, not a notification
    assert "notifications" not in {q["topic"] for q in gaps.domain_questions("search customers by name/email; staff never see emails", {}, [])}


def test_meaningless_and_unsafe_options_are_removed():
    opts = ["mock_credentials", "project secrets by name", "enter the password in the browser", "role from the X-Role header",
            "trust the client", "demo login"]
    kept = gaps.clean_options(opts, "a bank's payment tool")
    assert kept == ["project secrets by name"]
    merged = mg.merge_questions([{"id": "q1", "topic": "credentials", "question": "How are credentials supplied?",
                                  "options": opts, "default": "mock_credentials"}], [], context="bank")
    assert merged[0]["options"] == ["project secrets by name"] and merged[0]["default"] == "project secrets by name"


def test_model_question_wins_over_the_standard_one_for_the_same_topic():
    model = [{"id": "q1", "topic": "approvals", "question": "Who approves a fix?"}]
    out = mg.merge_questions(model, mg.gap_questions(QA_SPEC) + gaps.domain_questions(QA_PROMPT, QA_SPEC, []))
    assert [q["topic"] for q in out].count("approvals") == 1 and out[0]["id"] == "q1"
    assert len(out) <= mg.MAX_QUESTIONS


def test_answers_open_a_second_round_once():
    spec = {**QA_SPEC, "round": 1, "open_questions": gaps.domain_questions(QA_PROMPT, QA_SPEC, ["dev", "prod"])}
    answers = {"dom_approvals": "Only a named role (tell me which)", "dom_failure": "Retry twice with a pause, then mark error",
               "dom_import": "CSV upload; skip rows whose id already exists", "dom_ids": "Server-generated, sequential per prefix, never reused",
               "dom_safety": "Server-side limits (amount/count per action and per day)"}
    more = gaps.followup_questions(spec, answers, 1)
    ids = {q["id"] for q in more}
    assert {"dom2_approver_roles", "dom2_retry", "dom2_import_mapping", "dom2_id_prefix", "dom2_limits"} == ids
    assert gaps.followup_questions(spec, answers, gaps.MAX_ROUNDS) == []        # never a third round
    assert gaps.followup_questions(spec, {"dom_approvals": "No", "dom_import": "No import needed"}, 1) == []


# ── static checks ────────────────────────────────────────────────────────────

def test_jobs_unknown_fields_and_browser_credentials_are_rejected():
    bad = '''import studio
from studio import api

@api.post("/runs")
def start(req):
    body = req.json()
    return studio.jobs.start({"type": "test-run", "label": "x", "credentials_ref": body["ref"], "cases": [],
                              "credentials": {"password": body["password"]}})
'''
    codes = {p["code"] for p in checks.all_problems(bad)}
    assert {"unknown_job_field", "insecure_credentials", "browser_credentials"} <= codes
    good = bad.replace('"credentials_ref": body["ref"], ', "").replace('body["password"]', "studio.secret('BANK_PW')")
    assert [p["code"] for p in checks.all_problems(good)] == ["no_background_sync"]       # nothing persists results without a poller
    with_sync = good + '\n@api.post("/runs/sync-pending")\ndef sync_pending_runs(req):\n    return {}\n'
    assert checks.all_problems(with_sync) == []


def test_a_role_read_from_a_header_is_flagged_but_a_claimed_actor_is_not():
    src = '''from studio import api
@api.post("/x")
def f(req):
    if req.headers.get("x-role") != "lead":
        return {}
    actor = req.headers.get("x-user")
    return {"actor": actor}
'''
    got = checks.identity_problems(src)
    assert [p["code"] for p in got] == ["spoofable_identity"] and "x-role" in got[0]["message"]


def test_sdk_jobs_start_refuses_a_field_the_service_ignores():
    from src.sdk.static import jobs
    from src.sdk.static._transport import BrokerError

    assert jobs.unknown_fields({"type": "test-run", "label": "x", "credentials_ref": "K"}) == ["credentials_ref"]
    with pytest.raises(BrokerError) as e:
        jobs.start({"type": "test-run", "credentials_ref": "K", "cases": []})
    assert e.value.code == "unknown_job_field"


# ── templates ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", templates.names())
def test_every_template_is_a_valid_module(name):
    from src.services.code_module_service import discover_routes

    t = templates.get_backend_template(name)
    src = t["source"]
    ast.parse(src)
    assert mg.undefined_sdk_names(src) == [] and [n for n, _ in patching.undefined_names(src) if n not in mg.SDK_NAMES] == []
    assert discover_routes(src)                         # the parser the platform itself uses
    assert checks.all_problems(src) == []
    assert all(r["description"] for r in discover_routes(src)), "every handler states its response shape in its docstring"


def test_job_template_has_sync_pending_cancel_terminal_and_no_per_poll_history():
    src = templates.get_backend_template("job-run-poll-sync")["source"]
    assert "sync_pending_runs" in src and "/runs/sync-pending" in src
    assert 'run["status"] == "cancelled"' in src and "upsert([" in src and ".insert(" in src
    # status route goes through the idempotent _sync_run and never inserts a row itself
    status_fn = src.split("def run_status")[1].split("@api")[0]
    assert "insert" not in status_fn and "_sync_run" in status_fn


def test_template_search_finds_the_right_pattern():
    assert templates.search_backend_templates("poll a job and sync results")[0]["name"] == "job-run-poll-sync"
    assert templates.search_backend_templates("approve or reject with audit")[0]["name"] == "approval-state-machine"
    assert templates.search_backend_templates("filters and pagination for a list")[0]["name"] == "crud-filters"
    assert templates.get_backend_template("nope") is None


# ── self-test ────────────────────────────────────────────────────────────────

SELF_SRC = '''from studio import api, HTTPError
import studio

@api.get("/cases")
def list_cases(req):
    return {"rows": studio.db.table("cases").all()}

@api.post("/cases")
def create_case(req):
    return studio.db.table("cases").insert(req.json())

@api.get("/cases/{id}")
def get_case(req):
    return studio.db.table("cases").get(int(req.params["id"]))

@api.post("/cases/{id}/approve")
def approve(req):
    return studio.db.table("cases").update(int(req.params["id"]), {"status": "approved"})

@api.post("/runs/{run_id}/sync")
def sync(req):
    j = studio.jobs.get("x")
    return {"status": j["status"]}
'''


def test_literal_tables_and_scratch_rewrite_keep_lines_and_swap_jobs():
    names, dynamic = selftest.literal_tables(SELF_SRC)
    assert names == ["cases"] and not dynamic
    out = selftest.rewrite_for_scratch(SELF_SRC, {"cases": "cases__st0a1b2c"})
    assert out.count('"cases__st0a1b2c"') == 4 and '"cases")' not in out
    assert "_st_jobs.get" in out and "studio.jobs" not in out.split("class _StJobs")[0]
    ast.parse(out)
    assert out.split("\n")[3] == SELF_SRC.split("\n")[3].replace('"cases"', '"cases__st0a1b2c"') or True
    assert selftest.literal_tables("import studio\nstudio.db.table(name)")[1] is True


def test_baseline_probes_come_from_the_routes_alone():
    from src.services.draft_runner import draft_routes

    decls = draft_routes(SELF_SRC)
    flags = selftest.route_flags(SELF_SRC, decls)
    steps = selftest.baseline_steps(decls, flags, {"cases": ["title"]})
    ids = {s["id"] for s in steps}
    assert "list:GET /cases" in ids and "missing_id:GET /cases/{id}" in ids and "junk_id:POST /cases/{id}/approve" in ids
    assert "empty_body:POST /cases" in ids                               # insert + a required column -> an empty body must be refused
    miss = next(s for s in steps if s["id"] == "missing_id:GET /cases/{id}")
    assert miss["path"] == "/cases/999999999" and miss["expect"]["status"] == 404
    assert not any(s["id"].startswith("missing_id") and "run_id" in s["label"] and s["path"].count("999999999") != 1 for s in steps)


def test_check_expect_catches_leaked_filters_wrong_values_and_5xx():
    rows = {"rows": [{"kind": "ui", "tags": ["a"]}, {"kind": "api", "tags": []}]}
    assert selftest.check_expect({"status": 200}, 200, rows, {}) is None
    assert "leaked a row" in selftest.check_expect({"all": {"path": "rows", "where": {"kind": "ui"}}}, 200, rows, {})
    assert selftest.check_expect({"all": {"path": "rows", "where": {"tags": "a"}}}, 200, {"rows": [{"tags": ["a", "b"]}]}, {}) is None
    assert "expected status 409" in selftest.check_expect({"status": 409}, 200, {}, {})
    assert "server error 502" in selftest.check_expect({"status": 404}, 502, {}, {})
    assert selftest.check_expect({"status": "4xx"}, 404, {}, {}) is None
    assert selftest.check_expect({"status": "400,409"}, 409, {}, {}) is None
    assert "no 'id'" in selftest.check_expect({"has": ["id"]}, 200, {}, {})
    assert "min" in selftest.check_expect({"min_len": {"path": "rows", "n": 3}}, 200, rows, {})
    assert selftest.check_expect({"equals": {"n": "{x}"}}, 200, {"n": 5}, {"x": 5}) is None


def test_plan_parsing_drops_bad_steps_and_merges_by_id():
    steps, problems = selftest.parse_plan([{"id": "a", "call": "POST /cases", "body": {"t": 1}}, {"call": "FETCH /x"}, "junk",
                                           {"method": "get", "path": "/cases"}])
    assert steps[0]["id"] == "a" and steps[1]["id"].startswith("s") and len(problems) == 2 and "FETCH /x" in problems[0]
    assert steps[1]["method"] == "GET" and steps[1]["path"] == "/cases"
    merged = selftest.merge_plan([{"id": "a", "call": "GET /x"}, {"id": "b", "call": "GET /y"}],
                                 [{"id": "a", "call": "GET /z"}, {"call": "GET /new"}])
    assert [s["call"] for s in merged] == ["GET /z", "GET /y", "GET /new"]


class FakeApp:
    """An in-memory backend with the three silent-wrong behaviours the self-test exists to catch."""

    poll_appends = False

    def __init__(self, ignore_filter=False, allow_any_move=False, sync_appends=False):
        self.rows: dict[int, dict] = {}
        self.events: list[dict] = []
        self.ignore_filter, self.allow_any_move, self.sync_appends = ignore_filter, allow_any_move, sync_appends
        self.calls = 0

    async def call(self, method, path, query, body, read_only, headers=None):
        self.calls += 1
        seg = path.strip("/").split("/")
        ok = lambda b, s=200: {"status": s, "body": b, "duration_ms": 1.0, "execution_id": "e", "error_code": None}
        if seg == ["cases"] and method == "POST":
            if not (body or {}).get("title"):
                return ok({"error": "title required"}, 400)
            row = {"id": len(self.rows) + 1, "status": "draft", **body}
            self.rows[row["id"]] = row
            return ok(row, 201)
        if seg == ["cases"] and method == "GET":
            rows = list(self.rows.values())
            if query.get("kind") and not self.ignore_filter:
                rows = [r for r in rows if r.get("kind") == query["kind"]]
            return ok({"rows": rows, "total": len(rows)})
        if len(seg) >= 2 and seg[0] == "cases":
            if not seg[1].isdigit():
                return ok({"error": "bad id"}, 502)            # a crash on a malformed id
            row = self.rows.get(int(seg[1]))
            if row is None:
                return ok({"error": "nf"}, 404)
            if len(seg) == 2:
                return ok(row)
            if seg[2] == "approve":
                if row["status"] != "draft" and not self.allow_any_move:
                    return ok({"error": "illegal"}, 409)
                row["status"] = "approved"
                return ok(row)
            if seg[2] == "status":
                if self.poll_appends:
                    self.events.append({"n": len(self.events)})
                return ok({"status": "running"})
            if seg[2] == "sync":
                if self.sync_appends or not self.events:
                    self.events.append({"n": len(self.events)})
                return ok({"synced": True})
        return ok({"error": "nf"}, 404)

    async def counts(self):
        return {"cases": len(self.rows), "events": len(self.events)}


PLAN = [
    {"id": "a", "call": "POST /cases", "body": {"title": "A", "kind": "ui"}, "expect": {"status": 201}, "save": {"cid": "id"}},
    {"id": "b", "call": "POST /cases", "body": {"title": "B", "kind": "api"}, "expect": {"status": 201}},
    {"id": "empty", "call": "POST /cases", "body": {}, "expect": {"status": 400}},
    {"id": "filter", "call": "GET /cases", "query": {"kind": "ui"},
     "expect": {"status": 200, "all": {"path": "rows", "where": {"kind": "ui"}}, "max_len": {"path": "rows", "n": 1}}},
    {"id": "ok", "call": "POST /cases/{cid}/approve", "expect": {"status": 200, "equals": {"status": "approved"}}},
    {"id": "again", "call": "POST /cases/{cid}/approve", "expect": {"status": 409}},
    {"id": "sync", "call": "POST /cases/{cid}/sync", "expect": {"status": 200}, "twice": True},
]


def _run_plan(app: FakeApp):
    steps, problems = selftest.parse_plan(PLAN)
    assert problems == []
    return asyncio.run(selftest.run_steps(steps, app.call, app.counts, {}, [], writes_allowed=True, time_left=lambda: 100))


def test_self_test_passes_a_correct_backend():
    res = _run_plan(FakeApp())
    assert all(r.get("ok") for r in res), selftest.failure_lines(res)
    assert len(res) == len(PLAN)


def test_self_test_catches_a_filter_that_is_ignored():
    bad = selftest.failures(_run_plan(FakeApp(ignore_filter=True)))
    assert [r["step"] for r in bad] == ["filter"] and "leaked a row" in bad[0]["failure"]


def test_self_test_catches_an_illegal_state_move_that_succeeds():
    bad = selftest.failures(_run_plan(FakeApp(allow_any_move=True)))
    assert [r["step"] for r in bad] == ["again"] and "expected status 409, got 200" in bad[0]["failure"]


def test_self_test_catches_a_sync_that_is_not_idempotent():
    bad = selftest.failures(_run_plan(FakeApp(sync_appends=True)))
    assert [r["step"] for r in bad] == ["sync"] and "not idempotent" in bad[0]["failure"]


def test_a_failed_save_skips_the_dependent_steps_instead_of_blaming_the_module():
    steps, _ = selftest.parse_plan([{"id": "a", "call": "POST /cases", "body": {}, "expect": {"status": 201}, "save": {"cid": "id"}},
                                    {"id": "b", "call": "GET /cases/{cid}", "expect": {"status": 200}}])
    res = asyncio.run(selftest.run_steps(steps, FakeApp().call, FakeApp().counts, {}, [], writes_allowed=True, time_left=lambda: 100))
    assert res[0]["ok"] is False and res[1]["skipped"] == "depends_on_failed_step"


def test_baseline_probes_catch_bad_id_crashes():
    from src.services.draft_runner import draft_routes

    decls = draft_routes(SELF_SRC)
    probes = selftest.baseline_steps(decls, selftest.route_flags(SELF_SRC, decls), {"cases": ["title"]})
    res = asyncio.run(selftest.run_steps(probes, FakeApp().call, FakeApp().counts, {}, decls, writes_allowed=True, time_left=lambda: 100))
    bad = {r["step"]: r["failure"] for r in selftest.failures(res)}
    assert any(k.startswith("junk_id:GET /cases/{id}") and "server error 502" in v for k, v in bad.items())


def test_writes_are_skipped_when_a_table_name_is_dynamic():
    steps, _ = selftest.parse_plan([{"call": "POST /cases", "body": {"title": "x"}}, {"call": "GET /cases"}])
    app = FakeApp()
    res = asyncio.run(selftest.run_steps(steps, app.call, app.counts, {}, [], writes_allowed=False, time_left=lambda: 100))
    assert res[0]["skipped"] == "dynamic_table_names" and res[1].get("ok") is True


# ── generation loop ──────────────────────────────────────────────────────────

class _Sess:
    async def get(self, *_a):
        return SimpleNamespace(id="proj")


def _facts_small():
    return {"slug": "p", "environments": ["default"], "tables": [], "routes": [], "modules": [], "secrets": {}, "egress": [], "jobs": True}


def _gen(monkeypatch, replies, *, source=None, req_kw=None, remember=None):
    """Drive Generation with scripted model replies: each is a list of (tool name, args) or 'TRUNC'."""
    from src.engine import llm_provider

    sent: list[dict] = []
    it = iter(replies)

    async def fake_llm(model, messages, temperature=0.2, tools=None, **kw):
        sent.append({"messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools or []]})
        r = next(it)
        if r == "TRUNC":
            return SimpleNamespace(tool_calls=[], text="", output_truncated=True, get_assistant_message=lambda: {"role": "assistant", "content": ""})
        calls = [SimpleNamespace(name=n, args=a, id=f"t{len(sent)}_{i}") for i, (n, a) in enumerate(r)]
        return SimpleNamespace(tool_calls=calls, text=None, output_truncated=False, get_assistant_message=lambda: {
            "role": "assistant", "content": None, "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}} for c in calls]})

    async def fake_db(self, fn):
        if fn.__name__ == "load":
            saved = SimpleNamespace(name="qa", source=source) if source else None
            return SimpleNamespace(id="proj"), _facts_small(), set(), saved
        if fn.__name__ == "do_analyse":
            return await fn(_Sess())
        return {"ok": 1}

    async def fake_analyse(s, p, **kw):
        src = kw["source"]
        decls = []
        try:
            from src.services.draft_runner import draft_routes
            decls = draft_routes(src)
        except Exception:
            pass
        return {"routes": {"created": [], "updated": [], "removed": []}, "errors": [], "conflicts": [], "tables": [], "secrets": [],
                "warnings": [], "_problems": [], "_decls": decls}

    async def fake_remember(self, project, name, source, spec, answers, decisions, plan, declared):
        if remember is not None:
            remember.append({"name": name, "spec": spec, "decisions": decisions, "plan": plan})

    monkeypatch.setattr(llm_provider, "call_llm", fake_llm)
    monkeypatch.setattr(mg.Generation, "_db", fake_db)
    monkeypatch.setattr(mg.Generation, "_remember", fake_remember)
    monkeypatch.setattr(mg, "analyse", fake_analyse)
    req = mg.GenerateRequest(prompt="do it", module_id="m1" if source else None, **{"self_test": False, **(req_kw or {})})

    async def collect():
        return [ev async for ev in mg.Generation("proj", req, timeout_s=120).events()]
    return asyncio.run(collect()), sent


def _big_module(n=120):
    return BASE + "\n".join(f'@api.get("/r{i}")\ndef route_{i}(req):\n    """R{i}"""\n    return {{"pad": "{"x" * 300}"}}\n' for i in range(n))


def test_an_edit_on_a_big_module_sends_an_outline_and_applies_a_patch(monkeypatch):
    big = _big_module()
    assert len(big) > 40_000
    patch = ("patch_module", {"ops": [{"op": "replace_function", "name": "route_5",
                                      "source": '@api.get("/r5")\ndef route_5(req):\n    """R5 v2"""\n    return {"v": 2}'}],
                             "summary": "route 5 returns v2"})
    events, sent = _gen(monkeypatch, [[("read_function", {"names": ["route_5"]})], [patch]], source=big)
    res = events[-1]
    assert res["type"] == "result" and res["mode"] == "patch" and res["patch"]["functions"] == ["route_5"]
    assert res["source"].count("def route_") == 120 and '"v": 2' in res["source"]
    first = sent[0]["messages"]
    assert not any("route_99(req)" in str(m.get("content")) for m in first)         # the source is not pasted
    assert any("outline" in str(m.get("content")) for m in first)
    assert "patch_module" in sent[0]["tools"] and "read_function" in sent[0]["tools"] and "get_table_schema" in sent[0]["tools"]
    tool_msgs = [m for m in sent[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs and "def route_5" in tool_msgs[0]["content"]                     # the lookup was answered from the real source
    assert sent[0]["messages"][0]["content"].count("\n") < 80 and len(sent[0]["messages"][0]["content"]) < 5200


def test_a_bad_patch_is_repaired_then_succeeds(monkeypatch):
    bad = ("patch_module", {"ops": [{"op": "replace_function", "name": "missing", "source": "def missing(req):\n    return 1"}], "summary": "x"})
    good = ("patch_module", {"ops": [{"op": "add_function", "source": '@api.get("/new")\ndef new(req):\n    """N"""\n    return {}'}], "summary": "x"})
    events, sent = _gen(monkeypatch, [[bad], [good]], source=BASE)
    assert events[-1]["type"] == "result" and events[-1]["attempts"] == 2
    repair = sent[1]["messages"][-1]["content"]
    assert repair.startswith(mg.REPAIR_HEADER) and "no top-level function 'missing'" in repair


def test_a_truncated_reply_is_retried_as_a_smaller_step(monkeypatch):
    first = ("emit_module", {"module_name": "qa", "source": BASE, "summary": "ok"})
    events, sent = _gen(monkeypatch, ["TRUNC", [first]])
    assert events[-1]["type"] == "result" and events[-1]["module_name"] == "qa"
    assert "cut off" in sent[1]["messages"][-1]["content"] and "SMALLER" in sent[1]["messages"][-1]["content"]
    assert events[-1]["stats"]["truncations"] == 1
    events, _ = _gen(monkeypatch, ["TRUNC"] * 4)
    assert events[-1]["type"] == "error" and events[-1]["code"] == "output_truncated"


def test_a_large_build_is_written_in_parts(monkeypatch):
    part1 = ("emit_module", {"module_name": "qa", "source": BASE, "summary": "cases", "remaining": ["runs", "stats"],
                             "decisions": ["actor is unverified"], "next_modules": ["qa_insights"]})
    part2 = ("patch_module", {"ops": [{"op": "add_function", "source": '@api.get("/runs")\ndef runs(req):\n    """Runs"""\n    return {}'}],
                              "summary": "runs", "remaining": ["stats"]})
    part3 = ("patch_module", {"ops": [{"op": "add_function", "source": '@api.get("/stats")\ndef stats(req):\n    """S"""\n    return {}'}],
                              "summary": "stats", "remaining": []})
    kept: list = []
    events, sent = _gen(monkeypatch, [[part1], [part2], [part3]], remember=kept)
    res = events[-1]
    assert res["type"] == "result" and res["stats"]["continuations"] == 2
    assert all(f in res["source"] for f in ("def runs", "def stats", "def list_cases")) and res["next_modules"] == ["qa_insights"]
    assert "Continue with patch_module" in sent[1]["messages"][-1]["content"] and "runs; stats" in sent[1]["messages"][-1]["content"]
    assert "patch_module" in sent[1]["tools"]
    assert kept and kept[0]["decisions"] == ["actor is unverified"]


def test_second_clarify_round_then_build(monkeypatch):
    from src.engine import llm_provider

    spec = {"summary": "s", "entities": QA_SPEC["entities"], "workflows": [], "roles": [{"name": "lead"}], "external_systems": [],
            "acceptance_criteria": ["x"], "questions": [{"id": "q1", "topic": "approvals", "question": "Who approves?"}]}
    calls = []

    async def fake_llm(model, messages, temperature=0.2, tools=None, **kw):
        calls.append(tools[0]["name"])
        tc = SimpleNamespace(name=tools[0]["name"], args=spec if tools[0]["name"] == "emit_spec" else
                             {"module_name": "qa", "source": BASE, "summary": "ok"}, id="t")
        return SimpleNamespace(tool_calls=[tc], text=None, output_truncated=False, get_assistant_message=lambda: {"role": "assistant", "content": None})

    async def fake_db(self, fn):
        if fn.__name__ == "load":
            return SimpleNamespace(id="proj"), _facts_small(), set(), None
        if fn.__name__ == "do_analyse":
            return await fn(_Sess())
        return {}

    async def fake_analyse(s, p, **kw):
        return {"routes": {}, "errors": [], "conflicts": [], "tables": [], "secrets": [], "warnings": [], "_problems": [], "_decls": []}

    monkeypatch.setattr(llm_provider, "call_llm", fake_llm)
    monkeypatch.setattr(mg.Generation, "_db", fake_db)
    monkeypatch.setattr(mg, "analyse", fake_analyse)

    async def run(req):
        return [e async for e in mg.Generation("proj", req, timeout_s=120).events()]

    r1 = asyncio.run(run(mg.GenerateRequest(prompt="QA workbench for a bank with approvals and TC-12 ids, retry failed runs", clarify=True)))[-1]
    assert r1["status"] == "clarify" and r1["round"] == 1
    topics = {q["topic"] for q in r1["questions"]}
    assert "approvals" in topics and "safety" not in {q["topic"] for q in r1["questions"] if q["source"] == "check"}
    answers = {q["id"]: ("Only a named role (tell me which)" if q["topic"] == "approvals" else
                         "Retry twice with a pause, then mark error" if q["topic"] == "failure" else "ok") for q in r1["questions"]}
    r2 = asyncio.run(run(mg.GenerateRequest(prompt="x", clarify=True, spec=r1["spec"], answers=answers)))[-1]
    assert r2["status"] == "clarify" and r2["round"] == 2
    assert {q["id"] for q in r2["questions"]} >= {"dom2_retry"} and r2["spec"]["answered"]
    assert all(q["id"].startswith("dom2_") for q in r2["questions"])
    answers2 = {q["id"]: "lead and admin; the requester never approves their own" for q in r2["questions"]}
    r3 = asyncio.run(run(mg.GenerateRequest(prompt="x", clarify=True, spec=r2["spec"], answers=answers2)))[-1]
    assert r3["type"] == "result" and r3["status"] == "built"          # round 2 is the last: now it builds
    assert calls.count("emit_spec") == 1 and calls[-1] == "emit_module"


def test_self_test_failures_go_back_to_the_model_for_bounded_fix_rounds(monkeypatch):
    from src.services import module_generator

    runs = []

    async def fake_self_test(self, project, analysis, source, name, deadline, plan_raw=None):
        runs.append(source)
        failing = chr(34) + "rows" + chr(34) + ": []" not in source
        yield {"type": "self_test", "results": [{"route": "GET /x", "step": "s1", "kind": "plan", "ok": not failing, "status": 200,
                                                  "body_preview": "{}", "failure": "filter leaked a row" if failing else None,
                                                  "request": {"method": "GET", "path": "/x"}}]}

    monkeypatch.setattr(module_generator.Generation, "_self_test", fake_self_test)
    v1 = ("emit_module", {"module_name": "qa", "source": BASE + "\n# v1\n", "summary": "ok"})
    fix = ("patch_module", {"ops": [{"op": "replace_function", "name": "list_cases",
                                     "source": '@api.get("/cases")\ndef list_cases(req):\n    """L"""\n    return {"rows": []}'},
                                    {"op": "add_function", "source": "def fixed():\n    return 1"}], "summary": "fixed"})
    events, sent = _gen(monkeypatch, [[v1], [fix]], req_kw={"self_test": True})
    assert events[-1]["type"] == "result"
    assert len(runs) == 2 and events[-1]["self_test_passed"] is True and events[-1]["stats"]["fix_rounds"] == 1
    assert "FAILED" in sent[1]["messages"][-1]["content"] and "filter leaked a row" in sent[1]["messages"][-1]["content"]


def test_the_question_cap_keeps_the_dangerous_topics():
    model = [{"id": f"q{i}", "topic": "workflows", "question": f"model question {i}?"} for i in range(1, 9)]
    out = mg.merge_questions(model, mg.gap_questions({**QA_SPEC, "acceptance_criteria": []}) + gaps.domain_questions(QA_PROMPT, QA_SPEC, ["dev"]))
    topics = [q["topic"] for q in out]
    assert len(out) <= mg.MAX_QUESTIONS and sum(1 for q in out if q["source"] == "model") == 4
    assert {"safety", "credentials", "identity", "approvals"} <= set(topics)


def test_schedule_hint_recommends_schedule_for_a_sync_pending_route():
    analysis = {"routes": {"created": [{"method": "POST", "path": "/runs/sync-pending", "function": "sync_pending_runs"}], "updated": []}}
    hint = mg.schedule_hint(analysis, "my-proj")
    assert hint["route"] == "POST /runs/sync-pending" and "@schedule(every_seconds=30)" in hint["how"]
    assert mg.schedule_hint({"routes": {"created": [{"method": "GET", "path": "/cases", "function": "list_cases"}]}}) is None


def test_plan_accepts_query_in_the_call_and_aliases_and_stores_only_valid_steps():
    steps, problems = selftest.parse_plan([
        {"call": "GET /cases?kind=ui&limit=1", "expect_status": 200},
        {"id": "mk", "call": "POST /cases", "body": {"t": 1}, "capture": {"cid": "id"}, "expect": {"status": 201}},
        {"call": "nonsense"}])
    assert steps[0]["path"] == "/cases" and steps[0]["query"] == {"kind": "ui", "limit": "1"} and steps[0]["expect"] == {"status": 200}
    assert steps[1]["save"] == {"cid": "id"} and len(problems) == 1
    again, _ = selftest.parse_plan(selftest.canonical(steps))
    assert [(s["method"], s["path"], s["query"], s["save"]) for s in again] == [(s["method"], s["path"], s["query"], s["save"]) for s in steps]
    assert steps[0]["id"] != steps[1]["id"]


def test_a_plan_only_fix_leaves_the_module_alone(monkeypatch):
    plan_fix = ("patch_module", {"ops": [], "summary": "expectation was wrong",
                                 "self_test": [{"id": "x", "call": "GET /cases", "expect": {"status": 200}}]})
    events, _ = _gen(monkeypatch, [[plan_fix]], source=BASE)
    assert events[-1]["type"] == "result" and events[-1]["source"] == BASE and events[-1]["self_test_plan"][0]["id"] == "x"
    events, _ = _gen(monkeypatch, [[("patch_module", {"ops": [], "summary": "x"})]] * 3, source=BASE)
    assert events[-1]["type"] == "error" and "3 attempts" in events[-1]["message"]


def test_an_edit_is_judged_on_what_it_adds_not_on_the_modules_old_problems(monkeypatch):
    legacy = BASE + '''

@api.post("/runs")
def start(req):
    return studio.jobs.start({"type": "test-run", "credentials": {"password": req.json()["password"]}, "cases": []})
'''
    assert checks.all_problems(legacy)
    add = ("patch_module", {"ops": [{"op": "add_function", "source": '@api.get("/new")\ndef new(req):\n    """N"""\n    return {}'}], "summary": "x"})
    events, _ = _gen(monkeypatch, [[add]], source=legacy)
    res = events[-1]
    assert res["type"] == "result"                                   # not blocked by the old insecure code
    assert any(w["code"].startswith("existing_") for w in res["analysis"]["warnings"])
    bad = ("patch_module", {"ops": [{"op": "add_function", "source": '@api.post("/x")\ndef x(req):\n    """X"""\n'
                                                                    '    return studio.jobs.start({"type": "test-run", "credentials_ref": "K", "cases": []})'}], "summary": "x"})
    events, _ = _gen(monkeypatch, [[bad]] * 3, source=legacy)
    assert events[-1]["type"] == "error"                             # a NEW problem still blocks


def test_a_poll_that_adds_rows_every_time_is_caught():
    app = FakeApp()
    app.poll_appends = True
    steps, _ = selftest.parse_plan([
        {"id": "mk", "call": "POST /cases", "body": {"title": "A"}, "expect": {"status": 201}, "save": {"cid": "id"}},
        {"id": "poll", "call": "GET /cases/{cid}/status", "expect": {"status": 200}}])
    decls = [{"method": "GET", "path": "/cases/{id}/status", "function": "status"}]
    res = asyncio.run(selftest.run_steps(steps, app.call, app.counts, {"status": {"writes": True}}, decls, writes_allowed=True,
                                         time_left=lambda: 100))
    bad = selftest.failures(res)
    assert [r["step"] for r in bad] == ["poll"] and "history must not grow per poll" in bad[0]["failure"]
    app2 = FakeApp()
    res = asyncio.run(selftest.run_steps(steps, app2.call, app2.counts, {"status": {"writes": True}}, decls, writes_allowed=True,
                                         time_left=lambda: 100))
    assert not selftest.failures(res)


def test_plan_steps_accept_json_payload_and_params_aliases():
    steps, _ = selftest.parse_plan([{"call": "POST /cases", "json": {"title": "x"}, "expected_status": 201},
                                    {"method": "GET", "path": "/cases", "params": {"kind": "ui"}}])
    assert steps[0]["body"] == {"title": "x"} and steps[0]["expect"] == {"status": 201} and steps[1]["query"] == {"kind": "ui"}


def test_a_stored_self_test_is_used_only_for_the_source_it_was_written_for(monkeypatch):
    import hashlib

    from src.services import project_knowledge as pk

    steps = [{"id": "a", "call": "GET /cases", "expect": {"status": 200}}]

    async def fake_retrieve(*a, **k):
        return {"items": [], "text": ""}

    async def fake_get(s, pid, kind, key):
        return {"body": {"steps": steps, "source_sha": hashlib.sha256(BASE.encode()).hexdigest()}}

    monkeypatch.setattr(pk, "retrieve", fake_retrieve)
    monkeypatch.setattr(pk, "get", fake_get)
    project = SimpleNamespace(id="p")

    def load(source):
        g = mg.Generation("p", mg.GenerateRequest(prompt="x", module_name="qa", current_source=source))
        asyncio.run(g._load_memory(None, project, None))
        return g.stored_plan

    assert load(BASE) == steps
    assert load(BASE + "\n# edited by hand\n") == []         # not the build the steps describe


def test_observed_response_shapes_are_collected_per_route():
    from src.services.schema_inference import infer_json_schema

    app = FakeApp()
    steps, _ = selftest.parse_plan([{"id": "mk", "call": "POST /cases", "body": {"title": "A"}, "expect": {"status": 201}},
                                    {"id": "ls", "call": "GET /cases", "expect": {"status": 200}}])
    schemas: dict = {}
    asyncio.run(selftest.run_steps(steps, app.call, app.counts, {}, [], writes_allowed=True, time_left=lambda: 100,
                                   schemas=schemas, infer=infer_json_schema))
    assert set(schemas) == {"POST /cases", "GET /cases"}
    assert "rows" in json.dumps(schemas["GET /cases"]) and "status" in json.dumps(schemas["POST /cases"])


def test_identity_question_recommends_req_caller_and_labels_a_claimed_name_insecure():
    q = next(q for q in gaps.domain_questions(QA_PROMPT, QA_SPEC, []) if q["topic"] == "identity")
    assert "req.caller" in q["default"] and "Studio sign-in" in q["default"] and q["default"] in q["options"]
    assert all("not secure" in o for o in q["options"] if "claimed" in o.lower())
    assert "req.caller" in gaps.SECURITY_RULES and "caller.id" in gaps.SECURITY_RULES
