"""Builder defects from the third blind QA round, each pinned by the failing case that reproduced it:

1. a nullable tool-schema type (``["integer", "null"]``) broke the Gemini declaration and the tool-argument validators;
2. ``{{key}}`` in a docstring and ``body["credentials"] = {...}`` gave false credential findings;
3. rebuilding a planned module that already exists was treated as a new module ("module_name taken");
4. a reply with no tool call after a finished step was a ``no_code`` failure;
5. UI step literals are checked against the Jobs service's real step contract, which the SDK doc now states;
6. a Jobs smoke the smoke run cannot run is reported by the plan validator;
7. the plan drops secrets the team answered away and never asks for secrets in a placeholder environment.

No model is ever called: every LLM call is a fake (a live key is configured in the dev environment)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import builder_gates as bg
from src.services import plan_clarify as pc
from src.services import project_plan as pp
from src.services.backend_templates import checks
from tests.unit.plan_fixture import qa_plan

APPS = Path(__file__).resolve().parents[3]


# ── 1. nullable types in tool schemas ────────────────────────────────────────


def test_gemini_accepts_every_plan_tool_schema():
    from google.genai.types import FunctionDeclaration

    from src.engine.tool_schema import prepare_tools_for_provider

    for tool in (pc.EMIT_PLAN_CORE_TOOL, pc.EMIT_PLAN_SURFACE_TOOL, pc.EMIT_PLAN_REVISION_TOOL):
        for t in prepare_tools_for_provider([tool], "gemini"):
            FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])


def test_gemini_schema_turns_a_type_list_into_nullable_or_any_of():
    from src.engine.tool_schema import prepare_tools_for_provider

    tool = {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {
        "a": {"type": ["integer", "null"]}, "b": {"type": ["string", "number"]}}}}
    props = prepare_tools_for_provider([tool], "gemini")[0]["parameters"]["properties"]
    assert props["a"] == {"type": "INTEGER", "nullable": True}
    assert props["b"] == {"anyOf": [{"type": "STRING"}, {"type": "NUMBER"}]}
    # other providers take JSON Schema type lists as they are
    assert prepare_tools_for_provider([tool], "anthropic")[0]["parameters"]["properties"]["a"]["type"] == ["integer", "null"]


def test_tool_arg_validators_take_type_lists_and_null_optional_keys():
    from src.engine.tool_schema import validate_tool_args
    from src.nodes.ai.ai_agent import AIAgentNode

    schema = {"type": "object", "required": ["n"], "properties": {
        "n": {"type": "integer"}, "v": {"type": ["integer", "null"]}, "s": {"type": "string"}}}
    for validate in (validate_tool_args, AIAgentNode._validate_tool_args):
        assert validate({"n": 1, "v": None}, schema) == []
        assert validate({"n": 1, "v": 3}, schema) == []
        assert validate({"n": 1, "s": None}, schema) == []          # an optional key may be sent as null
        assert validate({"n": 1, "v": "x"}, schema) != []           # a wrong type is still an error
        assert validate({"n": None}, schema) != []                  # a required key may not be null


# ── 2. credential checks: docstrings and item-assigned credentials ───────────

ITEM_ASSIGNED = '''import studio
from studio import api


@api.post("/runs")
def start_run(req):
    """Start a run. Steps sign in with {{user}} / {{pass}} / {{totp:otp}}; credentials are set below."""
    body = {"type": "test-run", "target": {"base_url": "http://localhost:4590"}, "policy": {},
            "cases": [{"id": "c1", "kind": "ui", "steps": [
                {"action": "fill", "target": {"label": "User"}, "value": "{{user}}"},
                {"action": "otp", "value": "{{totp:otp}}"}]}]}
    body["credentials"] = {"user": studio.secret("BANK_USER"), "pass": studio.secret("BANK_PASS"),
                           "otp": studio.secret("BANK_TOTP")}
    return studio.jobs.start(body)
'''


def test_item_assigned_secret_credentials_are_neither_browser_credentials_nor_missing():
    assert [p for p in checks.identity_problems(ITEM_ASSIGNED) if p["code"] == "browser_credentials"] == []
    assert [p for p in checks.job_contract_problems(ITEM_ASSIGNED) if p["code"] == "job_credential_missing"] == []


def test_a_template_only_in_a_docstring_is_not_a_step_reference():
    src = ITEM_ASSIGNED.replace('"otp": studio.secret("BANK_TOTP")', '"x": studio.secret("BANK_TOTP")').replace(
        '{"action": "otp", "value": "{{totp:otp}}"}', '{"action": "click", "target": {"role": "button", "name": "Go"}}')
    # {{pass}} and {{totp:otp}} remain only in the docstring; {{user}} is a real step with a matching key
    assert [p for p in checks.job_contract_problems(src) if p["code"] == "job_credential_missing"] == []


def test_a_missing_credentials_key_and_request_credentials_are_still_flagged():
    missing = ITEM_ASSIGNED.replace('"otp": studio.secret("BANK_TOTP")', '"x": studio.secret("BANK_TOTP")')
    found = [p for p in checks.job_contract_problems(missing) if p["code"] == "job_credential_missing"]
    assert len(found) == 1 and "totp:otp" in found[0]["message"]
    from_request = ITEM_ASSIGNED.replace(
        'body["credentials"] = {"user": studio.secret("BANK_USER"), "pass": studio.secret("BANK_PASS"),\n'
        '                           "otp": studio.secret("BANK_TOTP")}',
        'body["credentials"] = req.body["credentials"]')
    assert [p["code"] for p in checks.identity_problems(from_request)] == ["browser_credentials"]


def test_calling_out_is_read_from_calls_not_from_a_docstring():
    src = '''from studio import api


@api.post("/accounts")
def save_account(req):
    """Stores the account; studio.jobs.start is called elsewhere."""
    return {"password_set": bool(req.body["password"])}
'''
    assert checks.identity_problems(src) == []


# ── 3. rebuilding an existing planned module ─────────────────────────────────

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


def _facts(modules=()):
    return {"slug": "p", "environments": ["default"], "tables": [], "routes": [], "modules": list(modules),
            "secrets": {}, "egress": [], "jobs": True}


def _drive(monkeypatch, replies, *, req_kw=None, saved_rows=(), facts=None):
    """Generation with scripted replies; ``load`` runs for real against a fake session holding ``saved_rows``."""
    from src.db.models import ProjectModel
    from src.engine import llm_provider
    from src.services import module_generator as mg
    from src.services.draft_runner import draft_routes
    from tests.unit.builder_plan_fixture import install

    install(monkeypatch)
    sent: list[dict] = []
    analysed: list[dict] = []
    it = iter(replies)

    async def fake_llm(model, messages, temperature=0.2, tools=None, **kw):
        sent.append({"messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools or []]})
        calls = [SimpleNamespace(name=n, args=a, id=f"t{len(sent)}_{i}") for i, (n, a) in enumerate(next(it))]
        return SimpleNamespace(tool_calls=calls, text="Nothing more is left in this module." if not calls else None,
                               output_truncated=False, get_assistant_message=lambda: {
                                   "role": "assistant", "content": None,
                                   "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
                                                  for c in calls]})

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self.rows)

    class _Session:
        async def get(self, model, key):
            if model is ProjectModel:
                return SimpleNamespace(id="proj", slug="p")
            return next((r for r in saved_rows if r.id == key), None)

        async def execute(self, stmt):
            return _Rows(saved_rows)

    async def fake_db(self, fn):
        if fn.__name__ == "load":
            return await fn(_Session())
        if fn.__name__ == "load_plan":
            return await fn(None)
        if fn.__name__ == "do_analyse":
            return await fn(_Session())
        return {"ok": 1}

    async def fake_analyse(s, p, **kw):
        analysed.append({"module_id": kw["module_id"], "module_name": kw["module_name"]})
        return {"routes": {"created": [], "updated": [], "removed": []}, "errors": [], "conflicts": [], "tables": [],
                "secrets": [], "warnings": [], "_problems": [], "_decls": draft_routes(kw["source"])}

    async def no_memory(self, s, project, saved):
        return None

    async def fake_facts(s, project):
        return facts or _facts()

    async def fake_secrets(s, project):
        return set()

    async def fake_remember(self, *a, **kw):
        return None

    monkeypatch.setattr(llm_provider, "call_llm", fake_llm)
    monkeypatch.setattr(mg.Generation, "_db", fake_db)
    monkeypatch.setattr(mg.Generation, "_remember", fake_remember)
    monkeypatch.setattr(mg.Generation, "_load_memory", no_memory)
    monkeypatch.setattr(mg, "gather_facts", fake_facts)
    monkeypatch.setattr(mg, "secret_values", fake_secrets)
    monkeypatch.setattr(mg, "analyse", fake_analyse)
    req = mg.GenerateRequest(prompt="Build the runs module from the approved plan [plan:runs]", self_test=False, **(req_kw or {}))

    async def collect():
        return [ev async for ev in mg.Generation("proj", req, timeout_s=120).events()]
    return asyncio.run(collect()), sent, analysed


def _emit(source=RUNS_SRC, **extra):
    return ("emit_module", {"module_name": "runs", "source": source, "summary": "s", "remaining": [], "tables": RUNS_TABLES,
                            **extra})


def test_a_plan_build_of_an_existing_module_is_an_update_of_it(monkeypatch):
    saved = SimpleNamespace(id="mod-runs", name="runs", source=RUNS_SRC, project_id="proj")
    events, sent, analysed = _drive(monkeypatch, [[_emit()]], saved_rows=[saved], facts=_facts(["runs"]))
    first = "\n".join(m["content"] for m in sent[0]["messages"] if isinstance(m.get("content"), str))
    assert "Current module runs.py" in first and "patch_module" in sent[0]["tools"]     # the repair loop has the source
    assert analysed[0] == {"module_id": "mod-runs", "module_name": "runs"}             # judged as an edit: no name conflict
    res = events[-1]
    assert res["type"] == "result" and res["mode"] == "patch" and res["module_id"] == "mod-runs"


def test_a_plan_build_of_a_new_module_stays_new(monkeypatch):
    other = SimpleNamespace(id="mod-x", name="triage", source="x = 1\n", project_id="proj")
    events, sent, analysed = _drive(monkeypatch, [[_emit()]], saved_rows=[other])
    assert analysed[0]["module_id"] is None and "patch_module" not in sent[0]["tools"]
    assert events[-1]["mode"] == "new" and "module_id" not in events[-1]


# ── 4. a reply with no tool call after a finished step ───────────────────────


def test_no_tool_call_after_a_finished_part_counts_as_done(monkeypatch):
    events, sent, _ = _drive(monkeypatch, [[_emit(remaining=["triage helpers"])], []])
    assert len(sent) == 2
    res = events[-1]
    assert res["type"] == "result" and res["source"] == RUNS_SRC
    assert not any("no_code" in str(m.get("content")) for m in sent[-1]["messages"])


def test_no_tool_call_when_nothing_was_ever_written_is_still_no_code(monkeypatch):
    events, sent, _ = _drive(monkeypatch, [[], [], []])
    assert events[-1]["type"] == "error" and events[-1]["code"] == "generation_invalid"
    assert "no_code" in sent[1]["messages"][-1]["content"]


def test_the_app_step_rechecks_a_saved_app_when_a_follow_up_writes_nothing():
    from src.services import build_coordinator as bc

    calls: list[str] = []
    replies = iter([{"saved": True, "errors": []}, {"saved": False, "errors": []}])
    checks_ = iter([{"ok": False, "problems": [{"code": "screen_gap", "message": "Library screen is missing"}]},
                    {"ok": True, "problems": []}])

    class B:
        async def build_app(self, *a):
            calls.append("build")
            return next(replies)

        async def check_app(self, *a):
            calls.append("check")
            return next(checks_)

    co = bc.Coordinator.__new__(bc.Coordinator)
    co.b = B()
    co.row = SimpleNamespace(project_id="proj")
    updates: list[dict] = []

    async def update(st, **kw):
        updates.append(kw)
        st.update(kw)

    async def guard(aw):
        return await aw

    async def log(st, m):
        return None

    co._update, co._guard, co._log, co._check = update, guard, log, (lambda: None)
    st = {"started_at": None}
    ok = asyncio.run(co._build_app_attempts(st, "app", "app-1", "build it", 2))
    assert ok and calls == ["build", "check", "build", "check"]
    assert not any(p.get("code") == "no_code" for u in updates for p in u.get("problems") or [])


# ── 5. the Jobs UI step contract ─────────────────────────────────────────────


def _ts_list(src: str, pattern: str) -> set[str]:
    m = re.search(pattern, src, re.S)
    assert m, pattern
    return set(re.findall(r"'([A-Za-z_]+)'", m.group(1)))


def test_the_sdk_step_contract_matches_the_jobs_service_source():
    from src.sdk.static import jobs

    actions_ts = (APPS / "test-builder/src/actions.ts").read_text()
    spec_ts = (APPS / "jobs-service/src/spec.ts").read_text()
    names = _ts_list(actions_ts, r"ACTION_NAMES = \[(.*?)\]")
    assert set(jobs.UI_STEPS) == (names - {"done"}) | {"expectUrl"}          # 'done' is refused by the service; expectUrl is its own
    assert jobs.TARGET_ROLES == _ts_list(actions_ts, r"const ROLES = new Set\(\[(.*?)\]\)")
    aliases = dict(re.findall(r"(\w+): '(\w+)'", re.search(r"const ALIASES[^=]*= \{(.*?)\};", spec_ts, re.S).group(1)))
    assert jobs.STEP_ALIASES == aliases


def test_the_sdk_reference_states_the_step_contract():
    from src.sdk.generate import reference

    ref = json.dumps(reference())
    for needle in ("expectCount", "expectUrl", "waitFor", "ambiguous", "exact", "only in fill.value and otp.value",
                   "never a string"):
        assert needle in ref, needle


STEPS_SRC = '''import studio


def login_steps():
    return [
        {"action": "goto", "url": "/login"},
        {"action": "fill", "target": {"label": "User"}, "value": "{{user}}"},
        {"action": "otp", "value": "{{totp:otp}}"},
        {"action": "click", "target": {"role": "button", "name": "Sign in", "exact": False}},
        {"action": "expect_text", "target": {"role": "heading"}, "text": "Accounts"},
        {"action": "expectUrl", "contains": "/accounts"},
        {"action": "press", "key": "Enter"},
    ]


def audit_row():
    return {"action": "create", "entity": "run"}
'''


def test_valid_steps_and_unrelated_action_dicts_pass():
    assert checks.job_step_problems(STEPS_SRC) == []


@pytest.mark.parametrize("step, needle", [
    ('{"action": "type", "target": {"label": "User"}, "value": "x"}', "unknown UI step action 'type'"),
    ('{"action": "click", "target": "Sign in"}', "target must be an object"),
    ('{"action": "click", "target": {"name": "Sign in"}}', "needs role, label or text"),
    ('{"action": "click", "target": {"role": "submit", "name": "Go"}}', "unsupported role 'submit'"),
    ('{"action": "expectText", "target": {"role": "heading"}}', "needs 'text'"),
    ('{"action": "expectCount", "target": {"role": "row"}, "count": -1}', "count"),
    ('{"action": "goto", "url": "{{base_url}}/x"}', "only in fill.value and otp.value"),
    ('{"action": "waitFor", "target": {"text": "Done"}, "state": "gone"}', "state"),
    ('{"action": "done"}', "not a runnable step"),
])
def test_bad_step_literals_are_reported_with_the_contract(step, needle):
    src = f"def steps():\n    return [{step}]\n"
    found = checks.job_step_problems(src)
    assert len(found) == 1 and found[0]["code"] == "job_step" and needle in found[0]["message"], found
    assert found[0]["line"] == 2


def test_step_problems_run_with_the_other_static_checks():
    src = 'def steps():\n    return [{"action": "type", "target": {"label": "User"}, "value": "x"}]\n'
    assert "job_step" in [p["code"] for p in checks.all_problems(src)]


# ── 6. smoke and the plan validator agree ────────────────────────────────────


def _jobs_smoke(smoke):
    p = qa_plan()
    p["integrations"][0]["smoke"] = smoke
    return [i for i in pp.validate_plan(p) if i["code"] == "PLAN013"]


def test_the_fixture_smoke_is_runnable():
    assert _jobs_smoke(qa_plan()["integrations"][0]["smoke"]) == []


@pytest.mark.parametrize("smoke, needle", [
    ({"start": {"type": "run_tests", "payload": {}}, "poll": "GET job until done", "read_only": True}, "smoke.start"),
    ({"start": "POST /nowhere"}, "not a planned route"),
    ({"start": "POST /runs", "poll": "GET job until done", "until": "status == passed"}, "smoke.poll"),
    ({"start": "POST /runs", "poll": "GET /runs/{id}", "until": "when it is finished"}, "smoke.until"),
])
def test_a_smoke_the_smoke_run_cannot_run_is_a_plan_issue(smoke, needle):
    found = _jobs_smoke(smoke)
    assert len(found) == 1 and needle in found[0]["message"], found
    assert found[0]["severity"] == "warning"


def test_the_plan_tool_describes_a_runnable_smoke():
    smoke = pc.CORE_PROPS["integrations"]["items"]["properties"]["smoke"]
    assert set(smoke["properties"]) >= {"start", "poll", "until", "environment"}
    assert "METHOD /path" in smoke["properties"]["start"]["description"]


# ── 7. secrets answered away; placeholder environments ───────────────────────


def _answered_no_login(body):
    body["requirements"]["answers"] = [
        {"id": "q_creds", "topic": "credentials", "question": "Does the bank app need a login?",
         "answer": "No login for the tests; the bank app under test needs no credentials. No secrets.", "round": 1,
         "source": "model"},
        {"id": "q_prod", "topic": "environments", "question": "Which environments?",
         "answer": "Treat localhost:4590 as dev. Add a prod environment too, but it is a placeholder: "
                   "prod=https://prod.northbank.example (never actually call it).", "round": 1, "source": "model"}]
    return body


def test_secrets_the_team_answered_away_are_dropped():
    body = _answered_no_login(qa_plan())
    body["secrets"].append({"name": "OTHER_TOKEN", "purpose": "", "environments": ["dev"], "allowed_hosts": ["localhost"]})
    body["requirements"]["answers"][0]["answer"] += " The runner itself uses OTHER_TOKEN."
    out = pc.drop_declined_secrets(body)
    assert [s["name"] for s in out["secrets"]] == ["OTHER_TOKEN"]                        # named in an answer: kept
    assert out["integrations"][0]["secrets"] == []
    assert any("BANK_USER" in a for a in out["requirements"]["assumptions"])


def test_secrets_stay_when_no_answer_declines_them():
    body = qa_plan()
    assert pc.drop_declined_secrets(body)["secrets"] == pp.normalize(body)["secrets"]


def test_an_environment_answered_as_a_placeholder_is_marked():
    body = _answered_no_login(qa_plan())
    body["environments"][1]["base_url"] = "https://bank.example.com"
    envs = {e["name"]: e for e in pc.mark_placeholder_envs(body)["environments"]}
    assert envs["prod"]["placeholder"] is True and envs["dev"]["placeholder"] is False


def test_a_reserved_example_host_is_a_placeholder():
    body = qa_plan()
    body["environments"][1]["base_url"] = "https://prod.northbank.example"
    envs = {e["name"]: e for e in pp.normalize(body)["environments"]}
    assert envs["prod"]["placeholder"] is True and envs["dev"]["placeholder"] is False


def test_no_secret_or_host_is_demanded_for_a_placeholder_environment():
    body = qa_plan()
    body["environments"][1]["placeholder"] = True
    rows = [{"key": n, "environment": "dev", "allowed_hosts": ["localhost"], "type": "secret", "overridden": False}
            for n in ("BANK_USER", "BANK_PASS", "BANK_TOTP")]
    found = bg.preflight_findings(pp.normalize(body), sources={}, secret_rows=rows, egress=["localhost"])
    assert [f for f in found if f.get("environment") == "prod"] == []
    body["environments"][1]["placeholder"] = False
    found = bg.preflight_findings(pp.normalize(body), sources={}, secret_rows=rows, egress=["localhost"])
    assert any(f["code"] == "secret_missing" and f["environment"] == "prod" for f in found)   # a real prod still is


def test_a_secret_scoped_to_dev_is_not_demanded_in_prod():
    body = qa_plan()
    for s in body["secrets"]:
        s["environments"] = ["dev"]
    rows = [{"key": n, "environment": "dev", "allowed_hosts": ["localhost"], "type": "secret", "overridden": False}
            for n in ("BANK_USER", "BANK_PASS", "BANK_TOTP")]
    found = bg.preflight_findings(pp.normalize(body), sources={}, secret_rows=rows, egress=[])
    assert [f for f in found if f["code"] == "secret_missing"] == []


def test_a_placeholder_environment_needs_no_base_url():
    body = qa_plan()
    body["environments"][1].update({"base_url": "", "placeholder": True})
    assert [i for i in pp.validate_plan(body) if i["code"] == "PLAN008"] == []
