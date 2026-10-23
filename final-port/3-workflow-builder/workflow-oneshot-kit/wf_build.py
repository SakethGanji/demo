"""One-shot workflow builder, tested against the engine's 15 bank eval cases.

    cd apps/workflow-engine && PYTHONPATH=.:prototypes/workflow-oneshot venv/bin/python -W ignore prototypes/workflow-oneshot/wf_build.py [case_id ...] [--out DIR]

One model call writes workflow.json (nodes, connections, tests). The platform lays it out and builds the form
screen, then checks it in-process (structure, checker, the model's own tests run with mocks); at most 2 fix
calls. Then the case's HIDDEN tests (never shown to the model) are bound and run: that's the score.
"""
import asyncio
import os
os.environ.setdefault("WORKFLOW_CODE_NODE_LEGACY", "1")  # the bwrap sandbox needs the DB broker; in-process here
import copy
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
from llm import call_model  # noqa: E402

import catalog  # noqa: E402  (registers nodes)
from src.engine.types import NodeData  # noqa: E402,F401
from src.repositories.workflow_repository import definition_to_workflow  # noqa: E402
from src.schemas.interface import generate_interface, slugify  # noqa: E402
from src.schemas.workflow import normalize_tests  # noqa: E402
from src.services import auto_layout  # noqa: E402
from src.services import test_runner as tr  # noqa: E402
from src.services.openapi_import import import_openapi  # noqa: E402
from src.services.proposal_sandbox import _check_structure  # noqa: E402
from src.services.workflow_checker import StaticLookups, check_workflow  # noqa: E402
from tests.support import ai_eval  # noqa: E402

# Simulates a 3-line engine change: in a test run, Respond records its reply as its request ({statusCode, body}).
from src.nodes.flow.respond import RespondNode  # noqa: E402

_respond = RespondNode.execute


async def _respond_recorded(self, context, node_definition, input_data):
    res = await _respond(self, context, node_definition, input_data)
    if getattr(context, "test_options", None) is not None or context.mode == "test":
        r = context.webhook_response
        context.test_requests.setdefault(node_definition.name, []).append({"statusCode": r.status_code, "body": r.body})
    return res


RespondNode.execute = _respond_recorded

# Simulates a second small engine change: SendEmail sends once per item, and DataTable writes (insert/update/upsert/
# delete/get) should too, so a test run records one request per item. Today the runner records one per job, resolved
# from item 0 (hides "emailed 5 times instead of once"), and DataTable itself only acts on item 0 (a real engine bug).
from src.engine import workflow_runner as _wr  # noqa: E402
from src.engine import test_mode as _tm  # noqa: E402
from src.engine.expression_engine import ExpressionEngine, expression_engine  # noqa: E402

_plan_mock = _wr.plan_mock
PER_ITEM = {"SendEmail", "Postgres"}  # Postgres already runs once per item for real


# Simulates engine fix 7: an assertion path may index a list with a dot (`statements.0.params.1`) as well as `[0]`;
# models write both, and "path not found" on a correct workflow costs a whole fix call.
from src.engine import assertions as _as  # noqa: E402


def _resolve_path_dots(obj, path):
    return _resolve_path0(obj, re.sub(r"\.(\d+)(?=\.|$)", r"[\1]", path or ""))


_resolve_path0 = _as.resolve_path
_as.resolve_path = _resolve_path_dots


class _Recorded(list):
    """A node's recorded requests. plan_mock runs BEFORE the runner records item 0, so the extra per-item requests wait in
    ``pending`` and follow item 0 when the runner appends it; Postgres requests also get ``sql``/``values`` (judge-only)."""
    def __init__(self, *a, pg=False):
        super().__init__(*a)
        self.pending, self.pg = [], pg

    def append(self, r):
        if self.pg and isinstance(r, dict) and "params" in r:
            parts = _pg_parts(r["params"])
            r.setdefault("sql", " ; ".join(q for q, _ in parts).lower())
            r.setdefault("values", [v for _, ps in parts for v in ps])
            r.setdefault("text", (r["sql"] + " | " + " | ".join(str(v) for v in r["values"])).lower())
        super().append(r)
        pending, self.pending = self.pending, []
        for x in pending:
            self.append(x)


def _plan_mock_recorded(mock, node, node_type, input_items, context):
    plan = _plan_mock(mock, node, node_type, input_items, context)
    node_def = sys._getframe(1).f_locals["node_def"]
    # Simulates engine fix 5: a deadline that passes on an Approval with on_timeout reject/approve comes out of the rejected/
    # approved port (that is what the real task expiry does, task_repository), not timed_out as the test mock did.
    auto = (node_def.parameters or {}).get("on_timeout") if node_type == "Approval" else None
    if plan is not None and auto in ("reject", "approve") and plan.outputs.get("timed_out"):
        port = "rejected" if auto == "reject" else "approved"
        moved = plan.outputs.pop("timed_out")
        for it in moved:
            it.json["approval"] = {**it.json.get("approval", {}), "decision": port, "decided_by": "system:timeout"}
        plan.outputs.setdefault(port, []).extend(moved)
    # Engine fidelity (not a fix): a real Approval makes ONE task whatever number of items reach it, and resumes all of them
    # with that one decision. The test mock decided per item, which would pass a workflow that asks once for ten managers.
    one_task = str((node_def.parameters or {}).get("per_item")).lower() not in ("true", "1")  # per_item: a task per item
    if plan is not None and node_type == "Approval" and one_task and len(input_items) > 1 and plan.kind == "items":
        ports = [p for p, its in plan.outputs.items() if its]  # item 0 is planned first, so its port is the first key
        if len(ports) > 1:
            dec = plan.outputs[ports[0]][0].json.get("approval", {})
            moved = [it for p in ports for it in plan.outputs[p]]
            for it in moved:
                it.json["approval"] = copy.deepcopy(dec)
            plan.outputs = {ports[0]: moved}
    # Simulates engine fix 6: Postgres and SendEmail answer once per input item, so a one-item static mock repeats per item
    # (today it emits one item for the whole job: 3 accounts closed -> 1 item -> "closed 1").
    if (plan is not None and node_type in PER_ITEM and plan.kind == "items" and len(input_items) > 1
            and len(plan.outputs.get("main") or []) == 1 and not _tm.has_expression(mock.get("items") if isinstance(mock, dict) else None)):
        one = plan.outputs["main"][0]
        plan.outputs["main"] = [copy.deepcopy(one) for _ in input_items]
    rec = context.test_requests.get(node)
    if not isinstance(rec, _Recorded):
        rec = context.test_requests[node] = _Recorded(rec or [], pg=node_type == "Postgres")
    # the runner now records Postgres/SendEmail/per_item Approval once per item itself; only DataTable writes are simulated here
    per_item = node_type == "DataTable" and (node_def.parameters or {}).get("operation", "list") != "list"
    if per_item and len(input_items) > 1:
        for idx in range(1, len(input_items)):
            ectx = ExpressionEngine.create_context(input_items, context.node_states, context.execution_id, idx,
                                                  variables=context.variables)
            params = expression_engine.resolve(copy.deepcopy(node_def.parameters), ectx)
            rec.pending.append({"params": {k: v for k, v in params.items() if k != "_credentials"}})
    return plan


def _as_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _pg_parts(params: dict) -> list:
    """[(sql, [values])] of a Postgres node's parameters: the query, or each transaction statement."""
    if params.get("operation", "query") == "transaction":
        st = _as_json(params.get("statements"))
        return [(str(x.get("query", "")), list(_as_json(x.get("params")) or [])) for x in st or [] if isinstance(x, dict)] if isinstance(st, list) else []
    qp = _as_json(params.get("queryParameters"))
    return [(str(params.get("query", "")), list(qp.values()) if isinstance(qp, dict) else list(qp or []))]


_wr.plan_mock = _plan_mock_recorded

# Simulates a third engine fix: the runner pre-resolves every expression that doesn't use $json, so a Switch rule field like
# {{ $node["Work out risk"].json.risk }} reaches Switch as "low", which it then reads as a field PATH (json["low"] -> None,
# no rule matches; a number crashes with 'int' has no attribute 'split'). Fix: leave Switch's rules raw; Switch resolves
# each rule per item itself.
from src.engine.workflow_runner import WorkflowRunner  # noqa: E402

_resolve_params = WorkflowRunner._resolve_node_parameters


def _resolve_params_keep_rules(self, context, node_def, input_data):
    resolved = _resolve_params(self, context, node_def, input_data)
    if node_def.type == "Switch" and "rules" in (node_def.parameters or {}):
        resolved.parameters["rules"] = copy.deepcopy(node_def.parameters["rules"])
    return resolved


WorkflowRunner._resolve_node_parameters = _resolve_params_keep_rules

# $op:<Type>.<operation>[|<operation>]: every node of that type doing one of those operations (e.g. DataTable updates).
_OP = re.compile(r"^\$op:(\w+)\.([\w|]+)(\?)?$")
_resolve0 = ai_eval.Resolver.resolve


_WF = re.compile(r"^\$wf:([\w-]+)(\?)?$")  # $wf:<id>: the ExecuteWorkflow nodes calling that sub-workflow


_HAS = re.compile(r"^\$has:(\w+):([^?]+)(\?)?$")  # $has:<Type>:<text>: nodes of that type whose parameters mention the text
_PG = re.compile(r"^\$pg([wr]?):(\w+)(?:!(\w+))?(\?)?$")  # $pg:<table>: Postgres nodes whose SQL names the table; $pgw:<table>: that write it


def _pg_writes(sql: str, table: str) -> bool:
    return re.search(rf"\b(insert\s+into|update|delete\s+from)\s+{table}\b", sql, re.I) is not None


_NTH = re.compile(r"^\$nth:(\w+):([^:]*):(\d+)(\?)?$")  # the k-th node (by distance from the trigger) of that type mentioning text


def _distances(nodes, conns):
    targets = {c["target_node"] for c in conns}
    dist = {n["name"]: 0 for n in nodes if n["name"] not in targets}
    frontier = list(dist)
    while frontier:
        nxt = []
        for c in conns:
            if c["source_node"] in frontier and c["target_node"] not in dist:
                dist[c["target_node"]] = dist[c["source_node"]] + 1
                nxt.append(c["target_node"])
        frontier = nxt
    return dist


def _resolve_op(self, key):
    m = _NTH.match(key)
    if m:
        typ, text, k, opt = m.group(1), m.group(2).lower(), int(m.group(3)), bool(m.group(4))
        dist = _distances(self.nodes, self.conns)
        cands = sorted((n for n in self.nodes if n.get("type") == typ and text in json.dumps(n.get("parameters") or {}).lower()),
                       key=lambda n: (dist.get(n["name"], 999), n["name"]))
        if len(cands) < k:
            if opt:
                return [], True, True
            raise ai_eval.BindingError(f"{key} matches no node ({len(cands)} candidates)")
        return [cands[k - 1]["name"]], True, opt
    h = _HAS.match(key)
    if h:
        names = [n["name"] for n in self.nodes if n.get("type") == h.group(1)
                 and h.group(2).lower() in json.dumps(n.get("parameters") or {}).lower()]
        if not names and not h.group(3):
            raise ai_eval.BindingError(f"{key} matches no node")
        return names, True, bool(h.group(3))
    g = _PG.match(key)
    if g:
        mode, table, without, opt = g.group(1), g.group(2), g.group(3), bool(g.group(4))
        names = []
        for n in self.nodes:
            if n.get("type") != "Postgres":
                continue
            sql = " ; ".join(q for q, _ in _pg_parts(n.get("parameters") or {}))
            if without and re.search(rf"\b{without}\b", sql, re.I):
                continue
            names_it = re.search(rf"\b{table}\b", sql, re.I) is not None
            if (_pg_writes(sql, table) if mode == "w" else names_it and not _pg_writes(sql, table) if mode == "r" else names_it):
                names.append(n["name"])
        if not names and not opt:
            raise ai_eval.BindingError(f"{key} matches no node")
        return names, True, opt
    w = _WF.match(key)
    if w:
        names = [n["name"] for n in self.nodes if n.get("type") == "ExecuteWorkflow" and (n.get("parameters") or {}).get("workflowId") == w.group(1)]
        if not names and not w.group(2):
            raise ai_eval.BindingError(f"{key} matches no node")
        return names, True, bool(w.group(2))
    m = _OP.match(key)
    if not m:
        return _resolve0(self, key)
    typ, ops, opt = m.group(1), m.group(2).split("|"), bool(m.group(3))
    names = [n["name"] for n in self.nodes if n.get("type") == typ and (n.get("parameters") or {}).get("operation", "list") in ops]
    if not names and not opt:
        raise ai_eval.BindingError(f"{key} matches no node")
    return names, True, opt


ai_eval.Resolver.resolve = _resolve_op

EVAL = Path("tests/fixtures/ai_builder/eval")
APIS = Path("tests/fixtures/ai_builder/apis")
if "--complex" in sys.argv:
    EVAL, APIS = HERE / "complex" / "eval", HERE / "complex" / "apis"
if "--tough" in sys.argv:
    EVAL, APIS = HERE / "tough" / "eval", HERE / "tough" / "apis"
if "--crazy" in sys.argv:
    EVAL, APIS = HERE / "crazy" / "eval", HERE / "crazy" / "apis"
MAX_FIXES = 2
BAR = 1.0 if any(f in sys.argv for f in ("--complex", "--tough", "--crazy")) else 0.8  # complex cases: every hidden test must pass


# ── context ─────────────────────────────────────────────────────────────────────────────────

def _shape(s, depth=0) -> str:
    if not isinstance(s, dict):
        return "any"
    t = s.get("type")
    if t == "object" and s.get("properties"):
        req = set(s.get("required") or [])
        inner = ", ".join(f"{k}{'' if k in req else '?'}: {_shape(v, depth + 1)}" for k, v in s["properties"].items())
        return "{" + inner + "}"
    if t == "array":
        return "[" + _shape(s.get("items"), depth + 1) + "]"
    if s.get("enum"):
        return "|".join(json.dumps(e) for e in s["enum"])
    return t or "any"


def load_apis(files: list[str]) -> dict:
    """{spec_id: {"name", "base_url", "operations"}} with spec_id = the API's name."""
    out = {}
    for f in files:
        d = import_openapi((APIS / f).read_text())
        out[f.rsplit(".", 1)[0]] = {"name": f.rsplit(".", 1)[0], "base_url": d["base_url"], "operations": d["operations"]}
    return out


def render_apis(apis: dict) -> str:
    lines = []
    for sid, a in apis.items():
        for o in a["operations"]:
            lines.append(f'- {sid}.{o["operation_id"]}: {o["method"]} {o["path"]}: {o.get("summary") or ""}'.rstrip(": "))
            lines.append(f'    api_ref: {{"spec_id": "{sid}", "operation_id": "{o["operation_id"]}"}}')
            if o.get("request_shape"):
                lines.append(f'    request body: {_shape(o["request_shape"])}')
            for code, r in (o.get("responses") or {}).items():
                lines.append(f'    reply {code}: {_shape(r.get("shape"))}')
    return "\n".join(lines) or "(none)"


def render_tables(tables: dict) -> str:
    return "\n".join(f'- {t}: ' + ", ".join(f'{c["name"]} ({c["type"]})' for c in cols) + " (plus id, created_at, updated_at)"
                     for t, cols in tables.items()) or "(none in this project)"


def render_dbs(dbs: dict) -> str:
    """Project database connections: key, read-only or writable, and the tables/columns the builder may use."""
    out = []
    for key, c in dbs.items():
        out.append(f'- {key} ({"writable" if c.get("writable") else "read-only"}): {c.get("description", "")}'.rstrip(": "))
        for t, cols in c["tables"].items():
            out.append(f'    {t}(' + ", ".join(f"{n} {ty}" for n, ty in cols) + ")")
    return "\n".join(out) or "(none)"


def db_lint(d: dict, ctx: dict) -> list:
    """What the platform can check without running: the connection exists, a read-only connection only reads,
    and values reach SQL as parameters, never pasted into the SQL text (injection, quoting bugs)."""
    dbs = ctx.get("databases") or {}
    fails = []
    for n in d.get("nodes") or []:
        if n.get("type") != "Postgres":
            continue
        p = n.get("parameters") or {}
        key = p.get("connection")
        if key not in dbs:
            fails.append(f'"{n["name"]}": connection {key!r} is not one of the project connections ({", ".join(dbs) or "none"})')
            continue
        op = p.get("operation", "query")
        sqls = [p.get("query") or ""] if op == "query" else [str((s or {}).get("query", "")) for s in (p.get("statements") or [])
                                                             if isinstance(p.get("statements"), list)]
        if isinstance(p.get("statements"), str) and op == "transaction":
            sqls = [p["statements"]]
        if any("{{" in q for q in sqls):
            fails.append(f'"{n["name"]}": an expression is pasted into the SQL text; put values in queryParameters/params and use %s')
        if not dbs[key].get("writable") and (op != "query" or not str(sqls[0]).lstrip().lower().startswith(("select", "with"))):
            fails.append(f'"{n["name"]}": connection {key!r} is read-only; it only runs one SELECT/WITH query')
    return fails


def render_vars(variables: dict) -> str:
    """Names, plus the value of every NON-secret variable (the model needs "ATM_DAILY_LIMIT = 1000" to write boundary tests);
    secrets are names only and their values never reach the model. (The real builder passes names only: change that.)"""
    return "\n".join(f'- {k}' + (" (secret: value hidden)" if v.get("secret") else f' = {json.dumps(str(v.get("value", "")))}')
                     + (f': {v["description"]}' if v.get("description") else "")
                     for k, v in sorted(variables.items())) or "(none defined yet)"


def render_subs(subs: list) -> str:
    """The real builder's callable_subworkflows entries: {id, name, untrusted_description, published_version, inputs, output_fields}."""
    out = []
    for w in subs:
        ins = ", ".join(f'{i["name"]} ({i["type"]}{", required" if i.get("required") else ""})' for i in w["inputs"])
        out.append(f'- {w["id"]}: "{w["name"]}": {w["description"]}\n    inputs: {ins}\n    output fields: {", ".join(w["output_fields"])}')
    return "\n".join(out) or "(none)"


def prompt(case: dict, apis: dict) -> str:
    return ((HERE / "PROMPT.md").read_text()
            .replace("{{CATALOG}}", catalog.render())
            .replace("{{APIS}}", render_apis(apis))
            .replace("{{TABLES}}", render_tables(case.get("tables") or {}))
            .replace("{{VARIABLES}}", render_vars(case.get("variables") or {}))
            .replace("{{DATABASES}}", render_dbs(case.get("databases") or {}))
            .replace("{{SUBWORKFLOWS}}", render_subs(case.get("subworkflows") or []))
            .replace("{{REQUEST}}", case["request"]))


# ── platform: parse, finish, check ──────────────────────────────────────────────────────────

def parse(reply: str) -> dict:
    m = re.search(r'```json[^\n]*\n(.*?)\n```', reply, re.S)
    if not m:
        raise ValueError('no ```json file="workflow.json" block in the reply')
    raw = re.sub(r",\s*([}\]])", r"\1", m.group(1))
    d = json.loads(raw)
    for c in d.get("connections") or []:
        c.setdefault("source_output", "main")
        c.setdefault("target_input", "main")
    for n in d.get("nodes") or []:
        n.setdefault("parameters", {})
        n.pop("position", None)
        ref = n["parameters"].get("api_ref")
        if isinstance(ref, str) and "." in ref:  # "payments.retry"
            ref = dict(zip(("spec_id", "operation_id"), ref.split(".", 1)))
        if isinstance(ref, dict):
            sid = str(ref.get("spec_id") or "")
            if "." in sid and not ref.get("operation_id"):
                sid, ref["operation_id"] = sid.split(".", 1)
            ref["spec_id"] = sid.removesuffix(".yaml").removesuffix(".json")
            n["parameters"]["api_ref"] = ref
            n["parameters"].setdefault("url", "")
    return d


def finish(d: dict) -> dict:
    """What the platform owns: layout and the form screen."""
    d = auto_layout.auto(d, keep=[])
    if any(n.get("type") == "FormTrigger" for n in d["nodes"]):
        try:
            d["interface"] = generate_interface(d, d.get("name") or "Workflow", "form", slugify(d.get("name") or "workflow"))
        except Exception as e:  # noqa: BLE001
            d["_interface_error"] = str(e)
    return d


from src.services.workflow_checker import ChildWorkflow  # noqa: E402


def lookups_for(apis: dict, ctx: dict) -> StaticLookups:
    """What the platform knows about the project: APIs, data tables, variable names, callable sub-workflows."""
    variables = ctx.get("variables") or {}
    return StaticLookups(api_specs={sid: {o["operation_id"]: o for o in a["operations"]} for sid, a in apis.items()},
                         tables=ctx.get("tables") or {}, variables=set(variables),
                         secrets={k for k, v in variables.items() if v.get("secret")},
                         children={w["id"]: ChildWorkflow(id=w["id"], name=w["name"], versions={1: w["definition"]}, published_version=1,
                                                          draft=w["definition"]) for w in ctx.get("subworkflows") or []})


async def run_tests(d: dict, tests: list, apis: dict, ctx: dict) -> list:
    """Run ``tests`` (already normalised) on definition ``d`` in-process, no DB."""
    workflow = definition_to_workflow(copy.deepcopy(d), d.get("name") or "wf")
    setup = tr._Setup()
    setup.extras = {"egress_allowlist": [], "api_specs": apis}
    setup.lookups = lookups_for(apis, ctx)
    setup.variables = {k: str(v.get("value", "")) for k, v in (ctx.get("variables") or {}).items()}  # test runs see the values
    setup.definition = d
    setup.shapes = check_workflow(d, lookups=setup.lookups).get("shapes_by_node", {})
    out = []
    for t in tests:
        out.append(await tr._run_one(None, None, workflow, d, t, d.get("test_defaults") or {}, setup, False))
    return out


def _expr_errors(r: dict) -> list:
    bad = []
    for node, ports in (r.get("outputs") or {}).items():
        if "[Expression Error" in json.dumps(ports, default=str):
            bad.append(node)
    return bad


def check(d: dict, apis: dict, ctx: dict) -> list:
    """Failures as plain sentences (empty = good)."""
    fails = []
    try:
        _check_structure(definition_to_workflow(copy.deepcopy(d), d.get("name") or "wf"))
    except Exception as e:  # noqa: BLE001
        return [f"structure: {getattr(e, 'message', None) or e}"]
    for f in check_workflow(d, lookups=lookups_for(apis, ctx))["findings"]:
        if f["severity"] == "error" and not (f["code"] == "WF006" and "Variable" in f["message"]):
            fails.append(f'checker {f["code"]} at {f.get("node")}{"." + f["param"] if f.get("param") else ""}: {f["message"]}')
    fails += db_lint(d, ctx)
    try:
        tests = normalize_tests(d.get("tests"))
    except Exception as e:  # noqa: BLE001
        return fails + [f"tests are not valid: {e}"]
    if not tests:
        fails.append("no tests")
    for r in asyncio.run(run_tests(d, tests, apis, ctx)):
        if r["status"] != "passed":
            for f in [f for f in r["failures"] if f["kind"] != "contract"][:4]:
                fails.append(f'test "{r["name"]}": {f["kind"]}: {f.get("message")}')
        for node in _expr_errors(r):
            fails.append(f'test "{r["name"]}": an expression in "{node}" failed: {json.dumps(r["outputs"][node], default=str)[:300]}')
    return fails


# ── score with the hidden tests ─────────────────────────────────────────────────────────────

def score(case: dict, d: dict, apis: dict, ctx: dict) -> dict:
    spec_ids = {k: k for k in apis}
    struct = ai_eval.check_expect(case, d, spec_ids, {"diff": {}})
    bound = ai_eval.bind_all(case, d, spec_ids)
    runnable = [b["test"] for b in bound if b["test"]]
    results = {r["test_id"]: r for r in asyncio.run(run_tests(d, normalize_tests(runnable), apis, ctx))} if runnable else {}
    judged = [ai_eval.judge_hidden(b, results.get((b["test"] or {}).get("id"))) for b in bound]
    secrets = [str(v["value"]) for v in (case.get("variables") or {}).values() if v.get("secret")]
    leaked = [n for r in results.values() for n, reqs in (r.get("requests") or {}).items()
              if any(sv in json.dumps(reqs, default=str) for sv in secrets)]
    struct.append({"name": "no secret in any request", "ok": not leaked, "detail": f"secret value sent by {sorted(set(leaked))}"})
    lint = db_lint(d, case)
    struct.append({"name": "database rules", "ok": not lint, "detail": "; ".join(lint)})
    for typ, n in ((case.get("expect") or {}).get("min_types") or {}).items():
        have = sum(1 for x in d.get("nodes") or [] if x.get("type") == typ)
        struct.append({"name": f"min_types {typ}", "ok": have >= n, "detail": f"{have} < {n}"})
    return {"struct": [s for s in struct if not s["ok"]], "hidden": [sum(j["ok"] for j in judged), len(judged)],
            "hidden_fail": [j for j in judged if not j["ok"]]}


# ── one case ────────────────────────────────────────────────────────────────────────────────

def build(case: dict, out: Path) -> dict:
    t0 = time.time()
    apis = load_apis(case["apis"])
    ctx = case  # the project context: tables, variables, subworkflows
    base = prompt(case, apis)
    (out / f'{case["id"]}.prompt.md').write_text(base)
    reply = call_model(base)
    (out / f'{case["id"]}.first.md').write_text(reply)
    first = None
    fixes = 0
    d = None
    while True:
        try:
            d = finish(parse(reply))
            fails = check(d, apis, ctx)
        except Exception as e:  # noqa: BLE001
            fails = [f"reply could not be read: {e}"]
        if first is None:
            first = fails
            first_score = score(case, d, apis, ctx) if d else None
        if not fails or fixes >= MAX_FIXES:
            break
        fixes += 1
        reply = call_model(base + "\n\nYour previous workflow is below. When we ran it, these failed:\n"
                           + "\n".join(f"- {f}" for f in fails)
                           + "\n\nFix them and reply again in the same format with the full workflow.json. If a test itself "
                           "was wrong (it expects something the request doesn't say), fix the test, never weaken a rule.\n\nPrevious reply:\n" + reply)
    (out / f'{case["id"]}.reply.md').write_text(reply)
    if d:
        (out / f'{case["id"]}.json').write_text(json.dumps(d, indent=1))
    s = score(case, d, apis, ctx) if d else {"struct": [{"name": "definition"}], "hidden": [0, 5], "hidden_fail": []}
    hp, hn = s["hidden"]
    ok = not fails and not s["struct"] and hp / hn >= BAR
    fs = first_score or {"struct": [1], "hidden": [0, 5]}
    first_ok = not first and not fs["struct"] and fs["hidden"][0] / fs["hidden"][1] >= BAR
    return {"id": case["id"], "pass": ok, "first_pass": first_ok, "fixes": fixes, "seconds": round(time.time() - t0, 1),
            "nodes": len((d or {}).get("nodes") or []), "own_tests": len((d or {}).get("tests") or []),
            "first_fails": first, "final_fails": fails, "hidden": s["hidden"], "first_hidden": fs["hidden"],
            "struct": s["struct"], "hidden_fail": s["hidden_fail"],
            "needs": next((l for l in reply.splitlines() if l.startswith("Needs")), "")}


def main():
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else HERE / "out"
    ids = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--out")]
    out.mkdir(parents=True, exist_ok=True)
    cases = [json.loads(p.read_text()) for p in sorted(EVAL.glob("*.json"))]
    cases = [c for c in cases if not ids or c["id"] in ids]
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(lambda c: _safe(c, out), cases))
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    for r in results:
        print(f'[{"PASS" if r.get("pass") else "FAIL"}] {r["id"]}: first={r.get("first_pass")} fixes={r.get("fixes")} '
              f'hidden {r.get("first_hidden")}->{r.get("hidden")} {r.get("nodes")} nodes, {r.get("seconds")}s')
        for f in (r.get("first_fails") or [])[:3]:
            print("    first:", str(f)[:220])
        for f in (r.get("final_fails") or [])[:3]:
            print("    final:", str(f)[:220])
        for s in r.get("struct") or []:
            print("    struct:", s.get("name"), s.get("detail"))
        for h in (r.get("hidden_fail") or [])[:3]:
            print("    hidden:", h["name"], "--", h["reason"][:200])
        if r.get("error"):
            print("    error:", r["error"][:400])
    n = len(results)
    print(f'\nfirst call: {sum(bool(r.get("first_pass")) for r in results)}/{n}   final: {sum(bool(r.get("pass")) for r in results)}/{n}   '
          f'hidden tests: {sum(r.get("hidden", [0])[0] for r in results)}/{sum(r.get("hidden", [0, 0])[1] for r in results)}')


def _safe(c, out):
    try:
        return build(c, out)
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"id": c["id"], "pass": False, "error": traceback.format_exc()}


if __name__ == "__main__":
    main()
