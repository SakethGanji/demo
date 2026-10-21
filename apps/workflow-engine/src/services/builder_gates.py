"""Public, read-only gate runs on SAVED artifacts, so code written by something other than the builder LLM (a human, another
agent) faces the same gates the builders run inside their loops. Nothing here reimplements a gate: it calls
``module_generator`` (analyse, ``_extra_problems``, ``Generation._self_test``) and ``app_builder_ai_service`` (``typecheck_errors``,
``_generated_syntax_errors``, ``_op_sigs``) and ``app_validate``.

Project check (``check_project``) runs a scenario ACROSS all of a project's modules in one scratch copy of all its tables, with
seed fixtures / cloned real rows and a fake Jobs service that returns realistic failed results, events and triage output,
plus a built-in standing scenario for job-backed modules (start run -> job finishes -> sync -> triage -> approve -> the case's
steps changed). Real tables are only ever read (to clone rows).

Plan check (``check_plan``) compares the saved project with the approved ProjectPlan (modules, routes, tables and columns,
shared helpers, apps and screens, integrations, module versions) and runs the static integration preflight; ``check_project``
embeds it, so "all green" cannot mean "a third of the plan was never built". ``smoke_run`` is the opt-in, user-triggered
real run of one integration's flow on scratch tables. Neither of the first two calls a model.

Module check = static findings + the generator's scratch-table self-test (scratch tables are created and dropped by
``Generation._self_test`` itself). The self-test plan is the caller's ``scenario``, else the plan the last build stored for exactly
this source (``acceptance`` row ``selftest:<name>``); the generator's baseline probes are always added. No LLM is involved.
"""

from __future__ import annotations

import asyncio
import ast
import contextlib
import json
import logging
import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from ..db.models import CodeModuleModel, DataTableModel, ProjectModel
from .backend_templates import selftest
from .project_service import ProjectError

logger = logging.getLogger(__name__)

SELF_TEST_DEADLINE_S = 120.0
PROJECT_DEADLINE_S = 420.0
MAX_CLONE_ROWS = 50


async def resolve_module(session: Any, project: ProjectModel, ref: str) -> CodeModuleModel:
    m = await session.get(CodeModuleModel, ref)
    if m is None or m.project_id != project.id:
        m = (await session.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id,
                                                                  CodeModuleModel.name == ref))).scalars().first()
    if m is None:
        raise ProjectError(404, "module_not_found", "Module not found")
    return m


def summarize_self_test(results: list[dict[str, Any]]) -> dict[str, Any]:
    failed = selftest.failures(results)
    skipped = [r for r in results if r.get("skipped")]
    passed = [r for r in results if r.get("ok") and not r.get("skipped")]
    return {"passed": len(passed), "failed": len(failed), "skipped": len(skipped), "checks": results}


async def check_module(project_id: str, module_ref: str, scenario: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from ..db.session import async_session_factory
    from ..sandbox.backend import availability
    from . import module_generator as mg

    async with async_session_factory() as s:
        project = await s.get(ProjectModel, project_id)
        if project is None:
            raise ProjectError(404, "project_not_found", "Project not found")
        module = await resolve_module(s, project, module_ref)
        source, name, mid = module.source, module.name, module.id
        facts = await mg.gather_facts(s, project)
        analysis = await mg.analyse(s, project, source=source, module_id=mid, module_name=name, name_from_model=False,
                                    declared=[], declared_errors=[], scrubbed=False, facts=facts)
        gen = mg.Generation(project_id, mg.GenerateRequest(prompt="check", module_id=mid, module_name=name, current_source=source),
                            timeout_s=SELF_TEST_DEADLINE_S)
        await gen._load_memory(s, project, module)

    static = mg._extra_problems(source)
    out: dict[str, Any] = {
        "module": {"id": mid, "name": name, "source_sha": module.source_sha},
        "static": static,
        "analysis": {"errors": analysis["errors"], "warnings": analysis["warnings"], "conflicts": analysis["conflicts"]},
    }
    plan_problems: list[str] = []
    if scenario is not None:
        _steps, plan_problems = selftest.parse_plan(scenario)
        plan_raw, plan_source = selftest.canonical(_steps), "request"
    else:
        plan_raw, plan_source = list(gen.stored_plan), ("stored" if gen.stored_plan else "baseline_only")
    ok, reason = availability()
    results: list[dict[str, Any]] = []
    if not ok:
        out["self_test"] = {**summarize_self_test([]), "available": False, "reason": reason}
    elif analysis["errors"]:
        out["self_test"] = {**summarize_self_test([]), "available": True,
                            "reason": "the module has analysis errors; the generator never self-tests such a draft"}
    else:
        deadline = asyncio.get_running_loop().time() + SELF_TEST_DEADLINE_S
        async for ev in gen._self_test(project, analysis, source, name, deadline, plan_raw):
            if ev["type"] == "self_test":
                results = ev["results"]
        out["self_test"] = {**summarize_self_test(results), "available": True}
    out["self_test"]["plan"] = {"source": plan_source, "steps": len(plan_raw), "problems": plan_problems}
    out["ok"] = not static and not analysis["errors"] and out["self_test"]["failed"] == 0
    return out


def _clone_n(clone_rows: Any, table: str) -> int:
    if isinstance(clone_rows, dict):
        n = clone_rows.get(table, clone_rows.get("*", 0))
    else:
        n = clone_rows or 0
    try:
        return max(0, min(int(n), MAX_CLONE_ROWS))
    except (TypeError, ValueError):
        return 0


async def check_project(project_id: str, *, scenario: list[dict[str, Any]] | None = None, fixtures: dict[str, list[dict[str, Any]]] | None = None,
                        clone_rows: Any = 0, standing: Any = None, jobs: dict[str, Any] | None = None,
                        baseline: bool = True) -> dict[str, Any]:
    """Project-level gate. ``scenario``: plan steps (as for the module check) that may call any module's routes.
    ``fixtures``: ``{table: [row, ...]}`` seeded into scratch. ``clone_rows``: N (or ``{table: N}``) real rows copied per
    table (ids are not remapped). ``standing``: ``None``/``True`` = run the built-in job flow when the project starts jobs,
    ``False`` = skip, a dict = per-role overrides ``{start|sync|run_read|triage|finding_read|approve|case_read: {call, body, expect}}``.
    ``jobs``: fake Jobs behaviour ``{fail: "first"|"all"|"none"|[case ids], failed_index}``."""
    from ..db.session import async_session_factory
    from ..sandbox.backend import availability
    from . import module_generator as mg
    from .data_table_service import DataTableService
    from .draft_runner import run_draft
    from .route_dispatcher import match_route
    from .schema_inference import infer_json_schema

    fixtures = fixtures if isinstance(fixtures, dict) else {}
    async with async_session_factory() as s:
        project = await s.get(ProjectModel, project_id)
        if project is None:
            raise ProjectError(404, "project_not_found", "Project not found")
        mods = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project_id)
                                .order_by(CodeModuleModel.name))).scalars().all()
        sources = [(m.id, m.name, m.source, m.source_sha) for m in mods]
        tables = {t.name: (t.columns or []) for t in (await s.execute(
            select(DataTableModel).where(DataTableModel.project_id == project_id))).scalars().all()
            if not selftest.SCRATCH_RE.search(t.name)}
        facts = await mg.gather_facts(s, project)
        analyses = {}
        for mid, name, src, _sha in sources:
            analyses[name] = await mg.analyse(s, project, source=src, module_id=mid, module_name=name, name_from_model=False,
                                              declared=[], declared_errors=[], scrubbed=False, facts=facts)
    out: dict[str, Any] = {"project": {"id": project_id}, "modules": []}
    plan_report = await check_plan(project_id)
    plan_ok = True
    if plan_report.get("has_plan"):
        out["plan"] = plan_report
        plan_ok = bool(plan_report["ok"])
    static_all: list[dict[str, Any]] = []
    for mid, name, src, sha in sources:
        st = mg._extra_problems(src)
        static_all += [{**p, "module": name} for p in st]
        a = analyses[name]
        out["modules"].append({"id": mid, "name": name, "source_sha": sha, "static": st,
                               "analysis": {"errors": a["errors"], "warnings": a["warnings"], "conflicts": a["conflicts"]}})
    errors = [e for m in out["modules"] for e in m["analysis"]["errors"]]
    out["static"] = static_all
    ok_sandbox, reason = availability()
    plan_problems: list[str] = []
    user_steps: list[dict[str, Any]] = []
    if scenario is not None:
        user_steps, plan_problems = selftest.parse_plan(scenario)

    def finish(st: dict[str, Any]) -> dict[str, Any]:
        out["self_test"] = st
        out["ok"] = not static_all and not errors and st["failed"] == 0 and plan_ok
        return out

    if not ok_sandbox:
        return finish({**summarize_self_test([]), "available": False, "reason": reason, "plan": {"problems": plan_problems}})
    if errors:
        return finish({**summarize_self_test([]), "available": True, "plan": {"problems": plan_problems},
                       "reason": "a module has analysis errors; the generator never self-tests such a draft"})

    # ── what to scratch ──
    used: list[str] = []
    dynamic: list[str] = []
    for _mid, name, src, _sha in sources:
        names, dyn = selftest.literal_tables(src)
        used += [n for n in names if n in tables and n not in used]
        if dyn:
            dynamic.append(name)
    unknown_fx = sorted(set(fixtures) - set(tables))
    for t in fixtures:
        if t in tables and t not in used:
            used.append(t)
    token = selftest.new_token()
    mapping = {n: selftest.scratch_name(n, token) for n in used}
    jobs_cfg = jobs if isinstance(jobs, dict) else {}
    scratch_src = {name: selftest.rewrite_for_scratch(src, mapping, jobs_cfg) for _mid, name, src, _sha in sources}
    decls_of = {name: analyses[name]["_decls"] for _mid, name, _src, _sha in sources}
    flags_of = {name: selftest.route_flags(src, decls_of[name]) for _mid, name, src, _sha in sources}
    merged_decls: list[dict[str, Any]] = []
    merged_flags: dict[str, dict[str, Any]] = {}
    for _mid, name, _src, _sha in sources:
        for d in decls_of[name]:
            key = f"{name}.{d['function']}"
            merged_decls.append({**d, "function": key, "_module": name})
            merged_flags[key] = flags_of[name].get(d["function"], {})
    required = {n: [c["name"] for c in tables[n] if c.get("required")] for n in used}
    module_of = {name: (mid, decls_of[name]) for mid, name, _s, _h in sources}

    steps: list[dict[str, Any]] = list(user_steps)
    standing_info: dict[str, Any] = {"ran": False}
    starts_jobs = any(re.search(r"\b(studio\.)?jobs\.start\(", src) for _m, _n, src, _h in sources)
    if standing is not False and (starts_jobs or isinstance(standing, dict)):
        roles = selftest.standing_roles([(name, src, decls_of[name]) for _mid, name, src, _h in sources])
        plan_raw, missing = selftest.standing_plan(roles, sorted(tables), standing if isinstance(standing, dict) else None)
        if missing:
            standing_info = {"ran": False, "reason": "could not find the route(s) for: " + ", ".join(missing)
                             + " (pass standing:{role:{call,body}} to name them)", "roles": {k: f"{v['method']} {v['path']}" for k, v in roles.items()}}
        else:
            sp, _prob = selftest.parse_plan(plan_raw)
            for st in sp:
                st["kind"] = "standing"
            steps += sp
            standing_info = {"ran": True, "steps": [st["id"] for st in sp],
                             "roles": {k: f"{v['method']} {v['path']}" for k, v in roles.items()}}
    if baseline:
        steps += selftest.baseline_steps(merged_decls, merged_flags, required)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROJECT_DEADLINE_S
    created: list[str] = []
    seeded: dict[str, int] = {}
    cloned: dict[str, int] = {}
    fx_problems: list[str] = [f"fixture table '{t}' does not exist in the project" for t in unknown_fx]
    init_vars: dict[str, Any] = {}
    real_before: dict[str, int] = {}
    real_after: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    leftover: list[str] = []
    schemas: dict[str, Any] = {}

    async def with_db(fn):
        async with async_session_factory() as s:
            return await asyncio.wait_for(fn(s), timeout=max(5.0, deadline - loop.time()))

    try:
        async def make(s: Any) -> None:
            svc = DataTableService(s)
            p = await s.get(ProjectModel, project_id)
            await mg.Generation._sweep_scratch(s, svc, p)
            for real, scratch in mapping.items():
                await svc.create_table(p, {"name": scratch, "columns": tables[real], "description": "builder project check (temporary)"})
                created.append(scratch)
            for real, scratch in mapping.items():
                t = await svc.table_by_name(project_id, scratch)
                rows: list[dict[str, Any]] = []
                n = _clone_n(clone_rows, real)
                if n:
                    src_t = await svc.table_by_name(project_id, real)
                    page = await svc.list_rows(src_t, sort="id", limit=n)          # read-only
                    got = [dict(r["data"]) for r in page["rows"]]
                    if got:
                        cloned[real] = len(got)
                    rows += got
                fx = fixtures.get(real) or []
                if fx and isinstance(fx, list):
                    rows += [r for r in fx if isinstance(r, dict)]
                if not rows:
                    continue
                try:
                    inserted = await svc.insert_rows(t, rows)
                except ProjectError as e:
                    fx_problems.append(f"table '{real}': {e.message} {e.details if hasattr(e, 'details') else ''}".strip())
                    await s.rollback()
                    continue
                seeded[real] = len(inserted)
                for i, row in enumerate(inserted, 1):
                    init_vars[f"{real}_id" if i == 1 else f"{real}_id_{i}"] = row.id
                    if i == 1:
                        init_vars[f"{real}_id_1"] = row.id
            for real in mapping:
                t = await svc.table_by_name(project_id, real)
                real_before[real] = await svc.row_count(t.id)
        await with_db(make)

        async def counts() -> dict[str, int]:
            async def count(s: Any) -> dict[str, int]:
                svc = DataTableService(s)
                res = {}
                for real, scratch in mapping.items():
                    t = await svc.table_by_name(project_id, scratch)
                    res[real] = await svc.row_count(t.id) if t is not None else 0
                return res
            return await with_db(count)

        async def call(method: str, path: str, query: dict[str, Any], body: Any, read_only: bool,
                       headers: dict[str, str] | None = None) -> dict[str, Any]:
            target = next(((n, mid, dc) for n, (mid, dc) in module_of.items() if match_route(dc, method.upper(), path)[0] is not None), None)
            if target is None:
                return {"status": 404, "body": {"error": "route_not_found", "message": f"no module declares {method} {path}"},
                        "duration_ms": 0.0, "error_code": "route_not_found", "execution_id": None}
            name, mid, dc = target

            async def one(s: Any) -> dict[str, Any]:
                p = await s.get(ProjectModel, project_id)
                try:
                    r = await run_draft(s, p, source=scratch_src[name], module_name=name, module_id=mid, method=method, path=path,
                                        query=query or None, body=body, headers=headers, virtual_tables=None,
                                        trigger_type="generate_self_test", origin="generate_self_test", read_only=read_only,
                                        max_calls=mg.SELF_TEST_MAX_CALLS * 3, timeout_s=mg.SELF_TEST_ROUTE_TIMEOUT_S, decls=dc,
                                        extra_modules=scratch_src)
                except ProjectError as e:
                    return {"status": e.status, "body": {"error": e.message}, "duration_ms": 0.0, "error_code": e.code, "execution_id": None}
                err = r.get("error")
                return {"status": r["status"], "body": r.get("body"), "duration_ms": r["duration_ms"],
                        "error_code": (err or {}).get("code") if err else None, "execution_id": r["execution_id"]}
            return await with_db(one)

        results = await selftest.run_steps(steps, call, counts, merged_flags, merged_decls, writes_allowed=not dynamic,
                                           time_left=lambda: deadline - loop.time(), schemas=schemas, infer=infer_json_schema,
                                           initial_vars=init_vars)
        for r in results:
            if r.get("ok"):
                r.pop("request", None)
            elif r.get("skipped") and r.get("kind") == "standing":
                r.update(ok=False, failure=f"standing scenario incomplete: {r['skipped']} ({r.get('detail', '')})")
                r.pop("skipped")
    finally:
        if created:
            async def drop(s: Any) -> None:
                await mg.Generation._drop_scratch(s, project_id, created)
            try:
                await asyncio.shield(_run_db(async_session_factory, drop))
            except Exception as e:  # noqa: BLE001 - swept at the next check
                logger.warning("project check: scratch cleanup deferred (%s)", type(e).__name__)
            try:
                async with async_session_factory() as s:
                    rows = (await s.execute(select(DataTableModel.name).where(DataTableModel.project_id == project_id))).scalars().all()
                    leftover = sorted(n for n in rows if n in created)
                    svc = DataTableService(s)
                    for real in mapping:
                        t = await svc.table_by_name(project_id, real)
                        real_after[real] = await svc.row_count(t.id) if t is not None else 0
            except Exception:  # noqa: BLE001
                leftover = ["<could not verify>"]
    summary = summarize_self_test(results)
    summary.update(available=True, plan={"problems": plan_problems, "steps": len(user_steps)}, standing=standing_info,
                   seeds={"fixtures": {k: v for k, v in seeded.items()}, "cloned": cloned, "problems": fx_problems})
    out["scratch"] = {"tables": sorted(mapping.values()), "created": len(created), "leftover": leftover,
                      "real_row_counts_before": real_before, "real_row_counts_after": real_after,
                      "real_tables_changed": sorted(t for t in real_before if real_before[t] != real_after.get(t, real_before[t]))}
    if dynamic:
        summary["note"] = f"module(s) with dynamic table names ({', '.join(dynamic)}): write steps were skipped"
    out["self_test"] = summary
    out["ok"] = not static_all and not errors and summary["failed"] == 0 and not leftover and not fx_problems and plan_ok
    return out


# ── plan conformance (read-only, no model) ───────────────────────────────────


def _route_key(method: str, path: str) -> str:
    from .backend_templates import coverage

    return coverage.norm(method, path)


def _split_route(key: str) -> tuple[str, str] | None:
    parts = str(key or "").split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 and parts[1].startswith("/") else None


def _defined_functions(source: str) -> set[str]:
    try:
        return {n.name for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    except SyntaxError:
        return set()


def _imports_from(source: str, module: str) -> set[str]:
    """Names a source imports from ``module`` (``*`` for ``import module`` / ``from module import *``)."""
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == module:
            out |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {"*" for a in n.names if a.name == module}
    return out


def _called_names(source: str) -> set[str]:
    try:
        return {n.func.id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    except SyntaxError:
        return set()


def plan_conformance(plan: dict[str, Any], version: Any, *, sources: dict[str, str], routes: list[dict[str, Any]],
                     tables: dict[str, dict[str, Any]], apps: list[dict[str, Any]] | None = None,
                     built_against: dict[str, Any] | None = None, smoke: dict[str, str] | None = None,
                     affected_since: dict[Any, set[str]] | None = None) -> dict[str, Any]:
    """Pure comparison of the saved project with an approved plan body.

    ``sources``: code module name -> source. ``routes``: ``[{method, path, name, handler_type, module}]``.
    ``tables``: name -> ``{columns, rows}``. ``apps``: ``[{key, bound, exists, screens: [{name}], screen_gaps, unused_routes}]``
    for the plan's apps. ``built_against``: module -> plan version of its last build. ``affected_since``: built-against
    version -> modules its diff to ``version`` touches; when given, a module the revisions did not touch is not stale."""
    from .module_generator import scan_usage, scan_writes

    pmods = [m for m in plan.get("modules") or [] if isinstance(m, dict) and m.get("key")]
    pmod_keys = [m["key"] for m in pmods]
    shared = next((m["key"] for m in pmods if m.get("kind") == "shared"), None)
    proutes = [r for r in plan.get("routes") or [] if isinstance(r, dict) and _split_route(r.get("key"))]
    ptables = [t for t in (plan.get("data") or {}).get("tables") or [] if isinstance(t, dict) and t.get("name")]
    missing: dict[str, list[Any]] = {"modules": [], "routes": [], "tables": [], "columns": [], "screens": [], "apps": [], "jobs": []}

    # modules and routes
    by_key = {_route_key(r["method"], r["path"]): r for r in routes}
    planned_keys: set[str] = set()
    routes_ok = 0
    folded_into: dict[str, set[str]] = {}   # saved module -> planned modules it serves in their place
    for r in proutes:
        method, path = _split_route(r["key"])  # type: ignore[misc]
        nk = _route_key(method, path)
        planned_keys.add(nk)
        have = by_key.get(nk)
        if have is None:
            missing["routes"].append({"route": r["key"], "module": r.get("module"), "reason": "missing"})
        elif have.get("handler_type") == "stub":
            missing["routes"].append({"route": r["key"], "module": r.get("module"), "reason": "stub"})
        elif r.get("module") and have.get("module") and have["module"] != r["module"] and r["module"] in sources:
            missing["routes"].append({"route": r["key"], "module": r["module"], "reason": "wrong_module",
                                      "actual_module": have["module"]})
        else:
            routes_ok += 1
            if r.get("module") and have.get("module") and have["module"] != r["module"]:
                folded_into.setdefault(have["module"], set()).add(r["module"])
    # a planned module that was never saved is delivered when its routes are served by another module (a one-shot build
    # folds the plan's modules into one); a module with no routes of its own then counts once every planned route is served
    unserved = {m.get("module") for m in missing["routes"]}
    all_served = bool(proutes) and not missing["routes"]
    routed = {r.get("module") for r in proutes}
    for k in pmod_keys:
        if k not in sources and not ((k in routed and k not in unserved) or (k not in routed and all_served)):
            missing["modules"].append(k)
    folded_everywhere = {k for k in pmod_keys if k not in sources and k not in routed and k not in missing["modules"]}
    extra_routes = sorted(f"{r['method']} {r['path']}" for k, r in by_key.items()
                          if k not in planned_keys and r.get("handler_type") == "python")

    # tables and columns
    differs: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    cols_total = cols_ok = tables_ok = 0
    for t in ptables:
        live = tables.get(t["name"])
        pcols = [c for c in t.get("columns") or [] if isinstance(c, dict) and c.get("name")]
        cols_total += len(pcols)
        if live is None:
            missing["tables"].append(t["name"])
            continue
        have = {c["name"]: c for c in live.get("columns") or []}
        t_ok = True
        for pc in pcols:
            ac = have.get(pc["name"])
            if ac is None:
                missing["columns"].append({"table": t["name"], "column": pc["name"]})
                t_ok = False
                continue
            bad = []
            if ac.get("type") != pc.get("type"):
                bad.append({"what": "type", "planned": pc.get("type"), "actual": ac.get("type")})
            if pc.get("required") and not ac.get("required"):
                bad.append({"what": "required", "planned": True, "actual": False})
            lost = [v for v in pc.get("enum") or [] if v not in (ac.get("enum") or [])]
            if lost:
                bad.append({"what": "enum", "planned": pc.get("enum"), "actual": ac.get("enum"), "missing_values": lost})
            if pc.get("unique") and not ac.get("unique"):
                bad.append({"what": "unique", "planned": True, "actual": False})
            if bad:
                differs += [{"table": t["name"], "column": pc["name"], **b} for b in bad]
                t_ok = False
            else:
                cols_ok += 1
        tables_ok += t_ok
        try:
            from .schema_migrations import diff_table
            d = diff_table(list(live.get("columns") or []), t, int(live.get("rows") or 0))
            if d.get("ops") or d.get("refused"):
                drift.append({"table": t["name"], "ops": list(d.get("ops") or []), "refused": list(d.get("refused") or [])})
        except ModuleNotFoundError as e:
            if e.name != f"{__package__}.schema_migrations":
                raise
    # tables used in code that the plan does not know, and writes into someone else's table
    pnames = {t["name"] for t in ptables}
    owners = {t["name"]: (t.get("owner"), set(t.get("writers") or [])) for t in ptables}
    unplanned_tables: dict[str, list[str]] = {}
    foreign: list[dict[str, Any]] = []
    secrets_used: dict[str, set[str]] = {}
    seen_foreign: set[tuple[str, str]] = set()
    for mod, src in sorted(sources.items()):
        use = scan_usage(src)
        for tn in use["tables"]:
            if tn not in pnames:
                unplanned_tables.setdefault(tn, []).append(mod)
        for sn in use["secrets"]:
            secrets_used.setdefault(sn, set()).add(mod)
        for w in scan_writes(src):
            owner, writers = owners.get(w["table"], (None, set()))
            if (owner and owner != mod and mod not in writers and owner not in folded_into.get(mod, set()) | folded_everywhere
                    and (mod, w["table"]) not in seen_foreign):
                seen_foreign.add((mod, w["table"]))
                foreign.append({"module": mod, "table": w["table"], "owner": owner, "line": w["line"],
                                "message": f"{mod} writes '{w['table']}', which belongs to {owner}; add {mod} to its writers in the plan "
                                           f"or write through {owner}'s routes"})
    # shared helpers
    duplicates: list[dict[str, Any]] = []
    helper_names = {h["name"] for m in pmods if m.get("kind") == "shared" for h in m.get("helpers") or [] if isinstance(h, dict) and h.get("name")}
    try:
        from .backend_templates.helpers_dup import duplicates as find_dups

        duplicates = [d for d in find_dups(dict(sources), shared) if d.get("modules")]
    except ModuleNotFoundError as e:
        if not str(e.name or "").endswith("helpers_dup"):
            raise
    dup_names = {(d["name"], m) for d in duplicates for m in d["modules"]}
    for mod, src in sorted(sources.items()):
        if mod == shared or (shared and shared not in sources):   # the shared module was folded in: its helpers live here
            continue
        for name in sorted(_defined_functions(src) & helper_names):
            if (name, mod) not in dup_names:
                duplicates.append({"name": name, "modules": [shared or "common", mod], "shared": True})
        if shared:
            imported = _imports_from(src, shared)
            for name in sorted((_called_names(src) & helper_names) - _defined_functions(src)):
                if "*" not in imported and name not in imported:
                    duplicates.append({"name": name, "modules": [mod], "shared": True, "not_imported": True,
                                       "message": f"{mod} calls {name}() without importing it: `from {shared} import {name}`"})
    # apps
    apps_out: list[dict[str, Any]] = []
    screens_total = screens_ok = 0
    for a in apps or []:
        n_sc = len(a.get("screens") or [])
        screens_total += n_sc
        if not a.get("bound") or not a.get("exists"):
            missing["apps"].append({"app": a["key"], "reason": "not_bound" if not a.get("bound") else "not_found"})
            continue
        gaps = list(a.get("screen_gaps") or [])
        missing["screens"] += [{"app": a["key"], "screen": g} for g in gaps]
        screens_ok += n_sc - len(gaps)
        apps_out.append({"app": a["key"], "unused_routes": list(a.get("unused_routes") or [])})
    # integrations
    unplanned_secrets = sorted(set(secrets_used) - {s.get("name") for s in plan.get("secrets") or [] if isinstance(s, dict)})
    for ig in plan.get("integrations") or []:
        if isinstance(ig, dict) and ig.get("kind") == "jobs" and not any(re.search(r"\b(?:studio\.)?jobs\.start\(", src) for src in sources.values()):
            missing["jobs"].append(ig.get("key"))
    # staleness
    stale = []
    for mod in pmod_keys:
        v = (built_against or {}).get(mod)
        if mod in sources and v is not None and version is not None and v != version:
            if affected_since is not None and v in affected_since and mod not in affected_since[v]:
                continue
            stale.append({"module": mod, "built_against": v, "approved": version,
                          "affected_by": f"plan v{v} -> v{version}; rebuild or re-save against the approved plan"})
    out: dict[str, Any] = {
        "plan_version": version,
        "conformance": {"routes": f"{routes_ok}/{len(proutes)}", "tables": f"{tables_ok}/{len(ptables)}",
                        "columns": f"{cols_ok}/{cols_total}", "screens": f"{screens_ok}/{screens_total}",
                        "modules": f"{len(pmod_keys) - len(missing['modules'])}/{len(pmod_keys)}"},
        "missing": missing,
        "extra": {"routes": extra_routes},
        "unplanned": {"tables": [{"table": t, "modules": m} for t, m in sorted(unplanned_tables.items())], "secrets": unplanned_secrets},
        "differs": differs, "drift": drift, "foreign_writes": foreign, "duplicates": duplicates, "stale": stale,
        "apps": apps_out, "smoke": dict(smoke or {}),
    }
    out["ok"] = not (any(missing.values()) or differs or any(d["ops"] or d["refused"] for d in drift) or foreign or duplicates
                     or stale or unplanned_tables or unplanned_secrets)
    return out


async def check_plan(project_id: str) -> dict[str, Any]:
    """Read-only, model-free: how much of the approved plan the saved project delivers (``POST /projects/{p}/plan/check``).
    ``{has_plan: False, ok: True}`` when the project has no approved plan."""
    from ..db.models import AppFileModel, AppModel
    from ..db.session import async_session_factory
    from . import project_knowledge as pk
    from .api_route_service import ApiRouteService
    from . import project_plan
    from .code_module_service import approved_plan
    from .data_table_service import DataTableService

    async with async_session_factory() as s:
        project = await s.get(ProjectModel, project_id)
        if project is None:
            raise ProjectError(404, "project_not_found", "Project not found")
        approved = await approved_plan(s, project_id)
        if approved is None:
            return {"has_plan": False, "ok": True}
        body, version = approved["body"], approved.get("version")
        mods = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project_id)
                                .order_by(CodeModuleModel.name))).scalars().all()
        names = {m.id: m.name for m in mods}
        routes = [{"method": r.method, "path": r.path, "name": r.name, "handler_type": r.handler_type,
                   "module": names.get(r.handler_module_id)} for r in await ApiRouteService(s).rows(project_id)]
        svc = DataTableService(s)
        tables = {t["name"]: {"columns": t["columns"], "rows": t.get("row_count", 0)} for t in await svc.list_tables(project)
                  if not selftest.SCRATCH_RE.search(t["name"])}
        built: dict[str, Any] = {}
        for m in mods:
            row = await pk.get(s, project_id, "acceptance", f"selftest:{m.name}")
            if row and isinstance(row.get("body"), dict) and row["body"].get("plan_version") is not None:
                built[m.name] = row["body"]["plan_version"]
        # A revision only makes the modules it touches stale, the same `affected` list /plan/revise reports.
        affected: dict[Any, set[str]] = {}
        for v in {v for v in built.values() if v != version}:
            try:
                old = await project_plan.get_version(s, project_id, v) if isinstance(v, int) else None
            except ProjectError:    # an unknown built-against version stays stale
                old = None
            if old is not None:
                affected[v] = set(project_plan.diff_plans(old.body, body)["affected"]["modules"])
        smoke: dict[str, str] = {}
        for ig in body.get("integrations") or []:
            if isinstance(ig, dict) and ig.get("key") and ig.get("smoke"):
                row = await pk.get(s, project_id, "decision", f"check:smoke:{ig['key']}")
                smoke[ig["key"]] = "not_run" if not row else ("passed" if (row.get("body") or {}).get("ok") else "failed")
        app_rows = (await s.execute(select(AppModel).where(AppModel.project_id == project_id))).scalars().all()
        route_ops = {_route_key(r["method"], r["path"]): r["name"] for r in routes}
        apps: list[dict[str, Any]] = []
        for pa in body.get("apps") or []:
            if not isinstance(pa, dict) or not pa.get("key"):
                continue
            app = next((a for a in app_rows if a.id == pa.get("app_id")), None) if pa.get("app_id") else None
            if app is None and not pa.get("app_id"):
                nm = str(pa.get("name") or pa["key"]).lower()
                app = next((a for a in app_rows if (a.name or "").lower() == nm), None)
            entry: dict[str, Any] = {"key": pa["key"], "bound": bool(pa.get("app_id")) or app is not None, "exists": app is not None,
                                     "screens": [sc for sc in pa.get("screens") or [] if isinstance(sc, dict)]}
            if app is not None:
                from .app_validate import screen_gaps, unused_planned_routes

                files: list[dict[str, str]] = []
                if app.current_version_id:
                    files = [{"path": f.path, "content": f.content} for f in (await s.execute(
                        select(AppFileModel).where(AppFileModel.version_id == app.current_version_id))).scalars().all()]
                if not files and app.draft_source_code:
                    files = [{"path": "App.tsx", "content": app.draft_source_code}]
                entry["screen_gaps"] = screen_gaps(entry["screens"], files)
                wanted = []
                for sc in entry["screens"]:
                    for rk in sc.get("routes") or []:
                        sp = _split_route(rk)
                        op = route_ops.get(_route_key(*sp)) if sp else None
                        if op and {"method": sp[0], "path": sp[1], "operation": op} not in wanted:
                            wanted.append({"method": sp[0], "path": sp[1], "operation": op})
                entry["unused_routes"] = unused_planned_routes(wanted, files)
            apps.append(entry)
        sources = {m.name: m.source for m in mods}
        pre = await _preflight(s, project, body)
    out = plan_conformance(body, version, sources=sources, routes=routes, tables=tables, apps=apps, built_against=built, smoke=smoke,
                           affected_since=affected)
    out["has_plan"] = True
    out["preflight"] = pre
    out["ok"] = bool(out["ok"]) and not any(f["severity"] == "error" for f in pre)
    return out


# ── integration preflight (static) ───────────────────────────────────────────

_CASE_ID_DEFAULT = r"^[A-Za-z0-9_.-]+$"


def _finding(code: str, where: str, message: str, fix: str, severity: str = "error", environment: str | None = None) -> dict[str, Any]:
    out = {"code": code, "severity": severity, "where": where, "message": message, "fix": fix}
    if environment:
        out["environment"] = environment
    return out


def _case_id_findings(sources: dict[str, str], pattern: str) -> list[dict[str, Any]]:
    """String literals and f-strings that build a Jobs ``cases[].id``: a literal part outside the allowed characters is a
    certain rejection by the service. Anything computed (a bare name, a call) is not guessed at."""
    default = pattern == _CASE_ID_DEFAULT
    try:
        rx = re.compile(pattern)
    except re.error:
        rx, default = None, True
    out: list[dict[str, Any]] = []
    for mod, src in sorted(sources.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for d in ast.walk(tree):
            if not isinstance(d, ast.Dict):
                continue
            keys = {k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "id" not in keys or not keys & {"kind", "steps", "requests", "title"}:
                continue            # a Jobs case: {id, kind|steps|requests|title, ...}
            v = next(val for k, val in zip(d.keys, d.values) if isinstance(k, ast.Constant) and k.value == "id")
            bad = None
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                if rx is not None and not rx.match(v.value):
                    bad = v.value
            elif isinstance(v, ast.JoinedStr) and default:
                for part in v.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str) and not re.fullmatch(r"[A-Za-z0-9_.-]*", part.value):
                        bad = ast.unparse(v)
            if bad is not None:
                chars = "".join(sorted({c for c in bad if not re.match(r"[A-Za-z0-9_.{}()\[\]'\"-]", c)})) or "?"
                out.append(_finding(
                    "case_id_invalid", f"{mod}.py line {d.lineno}", f"a Jobs case id is built as {bad!r}, which contains {chars!r}; "
                    f"the service only accepts {pattern}",
                    "build the case id from letters, digits, '_', '.' and '-' only (for example `f\"{run_key}-{case_key}\"`)"))
    return out


def preflight_findings(plan: dict[str, Any], *, sources: dict[str, str], secret_rows: list[dict[str, Any]],
                       egress: list[str]) -> list[dict[str, Any]]:
    """Pure core of ``preflight_integrations``. ``secret_rows``: effective project secrets
    (``{key, environment, allowed_hosts, type}``). Each finding carries a ``fix`` sentence."""
    from ..broker.policy import host_matches
    from .module_generator import scan_usage
    from .project_plan import is_placeholder_url

    out: list[dict[str, Any]] = []
    # a placeholder environment is never called: nothing is sent there, so it needs no URL, egress or secrets
    envs = [e for e in plan.get("environments") or [] if isinstance(e, dict) and e.get("name")
            and not (e.get("placeholder") or is_placeholder_url(str(e.get("base_url") or "")))]
    planned_secrets = {s["name"]: s for s in plan.get("secrets") or [] if isinstance(s, dict) and s.get("name")}
    for env in envs:
        url = str(env.get("base_url") or "")
        host = urlparse(url).hostname if url else None
        if not host:
            out.append(_finding("env_no_base_url", f"environment {env['name']}", f"environment '{env['name']}' has no usable base_url",
                                f"set a base_url like http://host:port for '{env['name']}' in the plan", environment=env["name"]))
            continue
        if egress and not host_matches(host, egress):
            out.append(_finding("egress_blocked", f"environment {env['name']}", f"'{host}' ({env['name']}) is not on the project's egress allowlist",
                                f"add `{host}` to the project's egress allowlist (hostname only, no port)", environment=env["name"]))
        for ig in plan.get("integrations") or []:
            if not isinstance(ig, dict):
                continue
            for sname in ig.get("secrets") or []:
                scoped = (planned_secrets.get(sname) or {}).get("environments") or []
                if scoped and env["name"] not in scoped:
                    continue                    # the plan scopes this secret to other environments
                rows = [r for r in secret_rows if r.get("key") == sname and r.get("environment") in (env["name"], "default")
                        and r.get("type") == "secret" and not r.get("overridden")]
                where = f"{sname} ({env['name']})"
                if not rows:
                    out.append(_finding("secret_missing", where, f"secret {sname} is not set in environment '{env['name']}'",
                                        f"create secret {sname} in '{env['name']}' with allowed_hosts `{host}`", environment=env["name"]))
                    continue
                hosts = [str(h) for h in rows[0].get("allowed_hosts") or []]
                ported = [h for h in hosts if ":" in h or "/" in h]
                if ported:
                    parsed = [urlparse(h if "://" in h else f"//{h}") for h in ported]
                    clean = sorted({u.hostname or h for u, h in zip(parsed, ported)})
                    ports = sorted({f":{u.port}" for u in parsed if u.port}) or [":port"]
                    out.append(_finding("host_has_port", where, f"allowed_hosts on {sname} has {ported}, but hosts are matched by hostname only",
                                        f"set allowed_hosts on {sname} to `{', '.join(clean)}` (hostname only, no {', '.join(ports)})", environment=env["name"]))
                elif not hosts:
                    out.append(_finding("secret_no_allowed_hosts", where, f"{sname} has no allowed_hosts, so a run that sends it is refused",
                                        f"set allowed_hosts on {sname} to `{host}`", environment=env["name"]))
                elif not host_matches(host, hosts):
                    out.append(_finding("host_not_allowed", where, f"{sname} may only be sent to {hosts}, but {env['name']} is {host}",
                                        f"add `{host}` to allowed_hosts on {sname}", environment=env["name"]))
    for sname, ps in planned_secrets.items():
        if not ps.get("allowed_hosts"):
            out.append(_finding("plan_secret_no_hosts", sname, f"the plan lists secret {sname} without allowed_hosts",
                                f"add allowed_hosts (hostnames only) to {sname} in the plan", "warning"))
    used = {n for src in sources.values() for n in scan_usage(src)["secrets"]}   # studio.secret("X") only: a {{var}} is not a secret
    for sname in sorted(used - set(planned_secrets)):
        out.append(_finding("secret_not_in_plan", sname, f"the code reads secret {sname}, which is not in the plan",
                            f"add {sname} to the plan's secrets (with allowed_hosts) or stop using it"))
    for ig in plan.get("integrations") or []:
        if isinstance(ig, dict) and ig.get("kind") == "jobs":
            pat = ((ig.get("contract") or {}).get("test-run") or {}).get("case_id_pattern") or _CASE_ID_DEFAULT
            out += _case_id_findings(sources, pat)
    return out


async def _preflight(s: Any, project: ProjectModel, plan: dict[str, Any]) -> list[dict[str, Any]]:
    from .secret_service import SecretService

    mods = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id))).scalars().all()
    rows = await SecretService(s).effective(project)
    return preflight_findings(plan, sources={m.name: m.source for m in mods}, secret_rows=rows,
                              egress=list((project.settings or {}).get("egress_allowlist") or []))


async def preflight_integrations(project_id: str) -> list[dict[str, Any]]:
    """Static and cheap: hosts, secrets, egress and Jobs case ids for each planned environment and integration. Empty when the
    project has no approved plan."""
    from ..db.session import async_session_factory
    from . import project_plan
    from .code_module_service import approved_plan

    async with async_session_factory() as s:
        project = await s.get(ProjectModel, project_id)
        if project is None:
            raise ProjectError(404, "project_not_found", "Project not found")
        approved = await approved_plan(s, project_id)
        return [] if approved is None else await _preflight(s, project, approved["body"])


# ── smoke run (opt-in; starts one real job) ─────────────────────────────────

SMOKE_DEADLINE_S = 420.0


def classify_smoke_error(status: int | None, body: Any, error_code: str | None = None) -> dict[str, str]:
    """A failed start / poll as ``{code, message, fix}``: unbound secret, host not allowed, an upstream validation error
    (its text, verbatim), a timeout, else the raw error."""
    text = json.dumps(body, default=str)[:1500] if body is not None else ""
    low = f"{error_code or ''} {text}".lower()
    msg = ""
    if isinstance(body, dict):
        msg = str(body.get("message") or body.get("error") or body.get("detail") or "")
        if isinstance(body.get("error"), dict):
            msg = str(body["error"].get("message") or body["error"])
    msg = msg or text or f"HTTP {status}"
    if "secret_not_bound" in low or "not bound" in low or "secret_not_found" in low or "no such secret" in low:
        return {"code": "secret_not_bound", "message": msg,
                "fix": "the secret the code reads is not set for this environment, or not bound to it: create it in the project's "
                       "secrets for the smoke environment (allowed_hosts = the target hostname)"}
    if "host_not_allowed" in low or "allowed_hosts" in low or "egress" in low:
        return {"code": "host_not_allowed", "message": msg,
                "fix": "the target host is not allowed for this secret or for the project: add its hostname (no scheme, no port) to "
                       "the secret's allowed_hosts and to the project's egress allowlist"}
    if "timeout" in low or "timed out" in low:
        return {"code": "timeout", "message": msg, "fix": "the call did not answer in time; check the target is up and reachable"}
    if status is not None and 400 <= status < 500 or "jobs_rejected" in low or "validation" in low:
        return {"code": "upstream_validation", "message": msg,
                "fix": "the service rejected the request body; fix what the message names (shown verbatim)"}
    return {"code": "error", "message": msg, "fix": "see the message; run the module's own route check for its logs"}


def _until(expr: str, body: Any) -> bool:
    """``"status in [passed,failed]"`` / ``"status == done"`` against a response body."""
    m = re.fullmatch(r"\s*([\w.~|]+)\s+in\s+\[(.*)\]\s*", expr or "")
    if m:
        got = selftest.dig_any(body, m.group(1))
        return got is not selftest._MISSING and str(got) in {x.strip().strip("'\"") for x in m.group(2).split(",")}
    m = re.fullmatch(r"\s*([\w.~|]+)\s*(==|!=)\s*(.+?)\s*", expr or "")
    if m:
        got = selftest.dig_any(body, m.group(1))
        eq = got is not selftest._MISSING and str(got) == m.group(3).strip("'\"")
        return eq if m.group(2) == "==" else not eq
    return False


async def smoke_run(project_id: str, integration_key: str, environment: str | None = None, *, call: Any = None,
                    poll_interval: float = 2.0) -> dict[str, Any]:
    """Start ONE real job for a planned integration on scratch copies of the project's tables, poll it to the end and classify
    the outcome. User-triggered only. Real tables are never written (checked: ``scratch.real_tables_changed``); the module
    sources run with their table names rewritten to scratch and ``studio.jobs`` left real.

    ``call``: test seam, an async ``(method, path, query, body, read_only, headers) -> {status, body, error_code}`` replacing the
    sandbox. Result: ``{ok, outcome, integration, environment, started, job, error?, findings?, steps, scratch}`` with outcome
    ``passed | failed | timeout | preflight_failed | unavailable``."""
    from ..db.session import async_session_factory
    from ..sandbox.backend import availability
    from . import module_generator as mg
    from . import project_plan
    from .code_module_service import approved_plan
    from .data_table_service import DataTableService
    from .draft_runner import draft_routes, run_draft
    from .route_dispatcher import match_route

    async with async_session_factory() as s:
        project = await s.get(ProjectModel, project_id)
        if project is None:
            raise ProjectError(404, "project_not_found", "Project not found")
        approved = await approved_plan(s, project_id)
        if approved is None:
            raise ProjectError(422, "no_plan", "this project has no approved plan, so there is no smoke flow to run")
        body = approved["body"]
        ig = next((i for i in body.get("integrations") or [] if isinstance(i, dict) and i.get("key") == integration_key), None)
        if ig is None:
            raise ProjectError(404, "integration_not_found", f"the plan has no integration '{integration_key}'")
        smoke = ig.get("smoke") if isinstance(ig.get("smoke"), dict) else None
        sm_call = _split_route((smoke or {}).get("start"))
        if not smoke or not sm_call:
            raise ProjectError(422, "no_smoke", (f"integration '{integration_key}' has no runnable smoke in the plan: smoke.start is "
                                                 f"{repr((smoke or {}).get('start'))[:120]}, but the smoke run calls one of this project's "
                                                 "routes, so it must be 'METHOD /path' (like 'POST /runs', with poll 'GET /runs/{id}' "
                                                 "and until 'status in [passed,failed]'); revise the plan's smoke"))
        env_name = environment or smoke.get("environment") or project.default_environment
        if env_name not in {e.get("name") for e in body.get("environments") or [] if isinstance(e, dict)}:
            raise ProjectError(422, "unknown_environment", f"the plan has no environment '{env_name}'")
        mods = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project_id)
                                .order_by(CodeModuleModel.name))).scalars().all()
        sources = [(m.id, m.name, m.source) for m in mods]
        tables = {t.name: (t.columns or []) for t in (await s.execute(
            select(DataTableModel).where(DataTableModel.project_id == project_id))).scalars().all()
            if not selftest.SCRATCH_RE.search(t.name)}
        findings = [f for f in await _preflight(s, project, body) if f["severity"] == "error" and f.get("environment") in (None, env_name)]
    out: dict[str, Any] = {"integration": integration_key, "environment": env_name, "started": False, "steps": [], "job": None}

    def done(outcome: str, **extra: Any) -> dict[str, Any]:
        out.update(extra, outcome=outcome, ok=outcome == "passed")
        return out

    if findings:
        return done("preflight_failed", findings=findings,
                    error={"code": "preflight_failed", "message": f"{len(findings)} integration problem(s) would fail the run",
                           "fix": findings[0]["fix"]})
    if call is None:
        ok_sb, reason = availability()
        if not ok_sb:
            return done("unavailable", error={"code": "sandbox_unavailable", "message": reason, "fix": "the sandbox backend is not available here"})
    fixtures = smoke.get("fixtures") if isinstance(smoke.get("fixtures"), dict) else {}
    used: list[str] = []
    for _mid, _name, src in sources:
        used += [n for n in selftest.literal_tables(src)[0] if n in tables and n not in used]
    used += [t for t in fixtures if t in tables and t not in used]
    token = selftest.new_token()
    mapping = {n: selftest.scratch_name(n, token) for n in used}
    scratch_src = {name: selftest.rewrite_for_scratch(src, mapping, {"mode": "real"}) for _mid, name, src in sources}
    module_of = {name: (mid, draft_routes(src)) for mid, name, src in sources}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SMOKE_DEADLINE_S
    created: list[str] = []
    init_vars: dict[str, Any] = {}
    real_before: dict[str, int] = {}
    real_after: dict[str, int] = {}
    leftover: list[str] = []

    async def with_db(fn: Any) -> Any:
        async with async_session_factory() as s2:
            return await asyncio.wait_for(fn(s2), timeout=max(5.0, deadline - loop.time()))

    async def sandbox_call(method: str, path: str, query: dict[str, Any], body_: Any, read_only: bool,
                           headers: dict[str, str] | None = None) -> dict[str, Any]:
        target = next(((n, mid, dc) for n, (mid, dc) in module_of.items() if match_route(dc, method.upper(), path)[0] is not None), None)
        if target is None:
            return {"status": 404, "body": {"error": "route_not_found", "message": f"no module declares {method} {path}"},
                    "error_code": "route_not_found"}
        name, mid, dc = target

        async def one(s2: Any) -> dict[str, Any]:
            p = await s2.get(ProjectModel, project_id)
            try:
                r = await run_draft(s2, p, source=scratch_src[name], module_name=name, module_id=mid, method=method, path=path,
                                    query=query or None, body=body_, headers=headers, virtual_tables=None,
                                    trigger_type="generate_self_test", origin="generate_self_test", read_only=read_only,
                                    max_calls=mg.SELF_TEST_MAX_CALLS * 3, timeout_s=float(smoke.get("call_timeout_s") or 60), decls=dc,
                                    extra_modules=scratch_src)
            except ProjectError as e:
                return {"status": e.status, "body": {"error": e.message}, "error_code": e.code}
            err = r.get("error")
            return {"status": r["status"], "body": r.get("body"), "error_code": (err or {}).get("code") if err else None}
        return await with_db(one)

    do_call = call or sandbox_call
    timeout_s = float(smoke.get("timeout_s") or 180)
    try:
        async def make(s2: Any) -> None:
            svc = DataTableService(s2)
            p = await s2.get(ProjectModel, project_id)
            await mg.Generation._sweep_scratch(s2, svc, p)
            for real, scratch in mapping.items():
                await svc.create_table(p, {"name": scratch, "columns": tables[real], "description": "smoke run (temporary)"})
                created.append(scratch)
            for real, scratch in mapping.items():
                rows = [r for r in fixtures.get(real) or [] if isinstance(r, dict)]
                if rows:
                    t = await svc.table_by_name(project_id, scratch)
                    for i, row in enumerate(await svc.insert_rows(t, rows), 1):
                        init_vars[f"{real}_id" if i == 1 else f"{real}_id_{i}"] = row.id
                real_before[real] = await svc.row_count((await svc.table_by_name(project_id, real)).id)
        try:
            await with_db(make)
        except ProjectError as e:
            return done("failed", error={"code": "fixture_invalid", "message": e.message, "fix": "fix smoke.fixtures in the plan: the rows must fit the table"})

        missing: list[str] = []
        req_body = selftest._fill(smoke.get("body") or {}, init_vars, missing)
        out["started"] = True
        r = await do_call(sm_call[0], sm_call[1], {}, req_body, False, None)
        bad = selftest.check_expect(smoke.get("expect") or {"status": "2xx"}, int(r.get("status") or 0), r.get("body"), init_vars)
        out["steps"].append({"step": "start", "call": f"{sm_call[0]} {sm_call[1]}", "status": r.get("status"), "ok": bad is None,
                             **({"failure": bad} if bad else {})})
        if bad:
            return done("failed", error=classify_smoke_error(r.get("status"), r.get("body"), r.get("error_code")))
        run_id = selftest.dig_any(r.get("body"), "id|run_id|job_id")
        out["job"] = None if run_id is selftest._MISSING else run_id
        poll = _split_route(smoke.get("poll") or "")
        if not poll or not smoke.get("until"):
            return done("passed")
        if out["job"] is None:
            return done("failed", error={"code": "no_run_id", "message": "the start response has no id / run_id / job_id to poll with",
                                         "fix": "return the run id from the start route"})
        poll_path = re.sub(r"\{\w+\}", str(out["job"]), poll[1])
        end = loop.time() + timeout_s
        polls = 0
        while True:
            pr = await do_call(poll[0], poll_path, {}, None, False, None)
            polls += 1
            if int(pr.get("status") or 0) >= 400:
                out["steps"].append({"step": "poll", "polls": polls, "status": pr.get("status"), "ok": False})
                return done("failed", error=classify_smoke_error(pr.get("status"), pr.get("body"), pr.get("error_code")))
            if _until(smoke["until"], pr.get("body")):
                out["steps"].append({"step": "poll", "polls": polls, "status": pr.get("status"), "ok": True})
                out["final"] = pr.get("body")
                return done("passed")
            if loop.time() >= end:
                out["steps"].append({"step": "poll", "polls": polls, "status": pr.get("status"), "ok": False})
                return done("timeout", error={"code": "timeout", "message": f"'{smoke['until']}' was not true after {timeout_s:g}s",
                                              "fix": "the job did not finish in time: check the target is reachable, or raise smoke.timeout_s"},
                            final=pr.get("body"))
            await asyncio.sleep(poll_interval)
    finally:
        if created:
            async def drop(s2: Any) -> None:
                await mg.Generation._drop_scratch(s2, project_id, created)
            try:
                await asyncio.shield(_run_db(async_session_factory, drop))
            except Exception as e:  # noqa: BLE001 - swept at the next check
                logger.warning("smoke run: scratch cleanup deferred (%s)", type(e).__name__)
            try:
                async with async_session_factory() as s2:
                    rows = (await s2.execute(select(DataTableModel.name).where(DataTableModel.project_id == project_id))).scalars().all()
                    leftover = sorted(n for n in rows if n in created)
                    svc2 = DataTableService(s2)
                    for real in mapping:
                        t = await svc2.table_by_name(project_id, real)
                        real_after[real] = await svc2.row_count(t.id) if t is not None else 0
            except Exception:  # noqa: BLE001
                leftover = ["<could not verify>"]
        out["scratch"] = {"tables": sorted(mapping.values()), "leftover": leftover, "real_row_counts_before": real_before,
                          "real_row_counts_after": real_after,
                          "real_tables_changed": sorted(t for t in real_before if real_before[t] != real_after.get(t, real_before[t]))}


async def _run_db(factory: Any, fn: Any) -> Any:
    async with factory() as s:
        return await fn(s)


def _lines(text: str | None) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


async def check_app_files(session: Any, project: ProjectModel | None, files: list[dict[str, str]]) -> dict[str, Any]:
    from . import app_builder_ai_service as ab
    from .app_validate import call_shape_issues, contract_issues, literal_issues
    from .client_codegen import CLIENT_PATH

    # the same working set the builder checks: the saved files + the Studio-owned kit + the project's generated typed client
    files = [{"path": f["path"], "content": f["content"]} for f in files if f["path"] != CLIENT_PATH]
    user_paths = [f["path"] for f in files]
    ab.inject_kit(files)
    if project is not None:
        from ..routes.api_routes import client_source
        from .api_route_service import ApiRouteService
        if await ApiRouteService(session).rows(project.id):
            src, _ = await client_source(session, project, project.default_environment)
            files.append({"path": CLIENT_PATH, "content": src})
    tc = ab._typecheck_toolchain()
    if tc is None:
        tsc: dict[str, Any] = {"ran": False, "reason": "no tsc toolchain (WORKFLOW_APP_TYPECHECK_NODE_MODULES or the studio's node_modules)",
                               "errors": []}
    elif not ab.clarify_enabled():
        tsc = {"ran": False, "reason": "type-check is switched off (WORKFLOW_APP_BUILDER_CLARIFY / scripted LLM)", "errors": []}
    else:
        errs = await ab.typecheck_errors(files)
        tsc = {"ran": True, "errors": _lines(errs)}
    syntax = _lines(await ab._generated_syntax_errors([{**f} for f in files]))
    contract: list[str] = []
    call_shape: list[str] = []
    sig_note = None
    if project is not None:
        svc = ab.AppBuilderAIService.__new__(ab.AppBuilderAIService)
        svc._app_service = SimpleNamespace(_session=session)
        svc._project = project
        sigs = await svc._op_sigs()
        contract, call_shape = contract_issues(files, sigs), call_shape_issues(files, sigs)
    else:
        sig_note = "app has no resolvable project; route signatures unavailable"
    literals = literal_issues(files)
    out = {"tsc": tsc, "syntax": syntax, "contract": contract, "call_shape": call_shape, "invented_literals": literals,
           "files": user_paths}
    if sig_note:
        out["note"] = sig_note
    out["ok"] = not (tsc["errors"] or syntax or contract or call_shape or literals)
    return out


async def record_check(session: Any, project_id: str, kind: str, target: str, result: dict[str, Any]) -> None:
    """One ``decision`` row per target (latest result wins), so the builders' memory knows what was last found. Never raises."""
    from . import project_knowledge as pk
    try:
        if kind in ("module", "project"):
            st = result["self_test"]
            summary = (f"{len(result['static'])} static finding(s); self-test {st['passed']} passed, {st['failed']} failed, "
                       f"{st['skipped']} skipped")
            body = {"decision": f"check {kind} {target}: {summary}", "origin": "builder_gates", "ok": result["ok"],
                    "static": [p["message"] for p in result["static"]][:10],
                    "failed_checks": [r.get("step") for r in selftest.failures(st["checks"])][:10]}
        elif kind == "smoke":
            err = result.get("error") or {}
            body = {"decision": f"smoke {target}: {result['outcome']}" + (f" ({err.get('code')})" if err else ""),
                    "origin": "builder_gates", "ok": result["ok"], "outcome": result["outcome"], "error": err or None,
                    "environment": result.get("environment")}
        else:
            n = {k: len(result[k]) for k in ("syntax", "contract", "call_shape", "invented_literals")}
            n["tsc"] = len(result["tsc"]["errors"])
            summary = ", ".join(f"{v} {k}" for k, v in n.items())
            body = {"decision": f"check {kind} {target}: {summary}", "origin": "builder_gates", "ok": result["ok"],
                    "findings": (result["contract"] + result["call_shape"] + result["invented_literals"])[:10]}
        await pk.upsert(session, project_id, "decision", f"check:{kind}:{target}", title=f"check {kind} {target}", body=body,
                        source="human", source_ref="builder_gates")
    except Exception as e:  # noqa: BLE001 - logging a check is a convenience
        logger.warning("builder gates: could not record the check (%s)", type(e).__name__)
        with contextlib.suppress(Exception):
            await session.rollback()
