"""Build coordinator: from an approved plan to a built project, one builder at a time (PLAN-STEP ORCHESTRATOR.md).

For approved plan version V a build runs, sequentially:

1. **Modules** in ``build_order`` (``app:*`` entries skipped). A module already built against V whose check passes is skipped
   unless ``rebuild``. Otherwise the backend generator runs in plan mode, the result is saved through the normal module save
   path (schema preflight, migrations, routes) and the module check runs. A partial result (planned routes missing) or a failed
   check gets ONE follow-up generation naming the gaps; still failing marks the step and the build moves on.
2. **Your input** (``input``): plan secrets not set in the dev environment and planned tables that need starting rows. Nothing
   needed: skipped. Otherwise the build waits (``waiting_for_input``) until ``continue`` (or cancel). Continue always continues.
3. **Apps**: each planned app (or only ``app_key``) is created and bound to the plan if needed, then the app builder runs
   headless with ``plan_app`` and its output is saved as a new app version, like the Studio does; then the app check.
4. **Verify**: ``check_plan``; missing routes get one more generation for their owner module, screen gaps one more app builder
   turn; at most ``MAX_VERIFY_ROUNDS``. The final plan check and project check go into the report.

Default path (WORKFLOW_BUILDER_MODE unset or ``oneshot``): the build is write -> load -> check -> fix (``oneshot_builder``): ONE model
call writes the tables, seed rows, the backend module and the React app from the plan; the platform loads them through the normal
save paths, checks them (module self-test, a smoke call per route, TSX type-check) and runs at most one fix call. The steps below
(modules, your input, apps) are kept as the Studio's view of it: the module steps report the written modules' checks, the input step
is skipped (seed data comes with the build), the app step carries the model calls and timing. A plan too big for one call is
written feature by feature, in parallel. ``WORKFLOW_BUILDER_MODE=pipeline`` keeps the role-split pipeline described here.

Fast path (FAST-BUILD A): when a module is not saved yet, its first draft is a deterministic scaffold from the plan
(``backend_templates.scaffold``, no model call): every CRUD, list and state-transition route is a ``studio.resource`` handler. All
scaffolds are saved first (so every planned route exists), then the model runs only on each module's TODO list, as patches, in
parallel by build-order layer (``WORKFLOW_BUILD_CONCURRENCY`` at a time, default 4), while the input step and the UI builder start as
soon as the scaffolds are saved. A save is re-checked only on the routes it touched. Each step records its wall time and model calls.
A module the scaffold cannot be made for (or that is already saved) takes the one-generation-per-module path described above.

The coordinator is one asyncio task per build, owned by this process (``_RUNS``). Every change to a step is persisted (the row
is the source of truth for GET) and published to SSE subscribers. Builders are reached through ``Builders`` (in-process calls
to the generator, the module save path, the app builder and the gates); tests substitute it via ``make_builders``.

Watching the build live (LIVE-FILES §2): besides ``step`` / ``log`` the stream carries ``file`` events (PLAN.md at the start, each
module source as generated and as saved, each table after create / migrate, each UI file as the app builder writes it),
``preview`` events (an app version was saved) and ``route`` events (planned routes: stub, built, passing / failing). Inside a
step, builders reach the stream through :func:`emit` (a context variable names the build and step), and file changes are
recorded in the project's change log with the step's builder as the actor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterator

from .backend_templates.scaffold import leftovers, todo_message
from .project_service import ProjectError

logger = logging.getLogger(__name__)

MAX_VERIFY_ROUNDS = 2
MODULE_ATTEMPTS = 2          # the generation plus one follow-up
APP_ATTEMPTS = 2
LOG_KEEP = 50
CANCEL_WAIT_S = 10.0
SSE_POLL_S = 15.0
TERMINAL = ("done", "partial", "failed", "cancelled")
ACTIVE = ("queued", "running", "waiting_for_input")
SEED_HINT = re.compile(r"\b(reference|catalog|catalogue|lookup|seed|master data|starting data)\b", re.I)
SUGGESTED_MIN_ROWS = 3
DEFAULT_CONCURRENCY = 4      # model passes / module checks at once (WORKFLOW_BUILD_CONCURRENCY)
FILE_CAP = 200 * 1024        # bytes of a file event's content; beyond it the content is cut and ``truncated`` is true

_STEP: ContextVar[dict[str, Any] | None] = ContextVar("build_step", default=None)


class _Cancelled(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat()


def _title(key: str) -> str:
    s = key.replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def _factory() -> Any:
    from ..db import session as dbs

    return dbs.async_session_factory


# ── builders: the in-process seams ───────────────────────────────────────────


class Builders:
    """The real builders and gates, called in-process (never through this engine's own HTTP API)."""

    async def module_state(self, project_id: str, key: str) -> dict[str, Any]:
        """``{module_id, built_against}`` of the saved module named ``key`` (both None when it does not exist)."""
        from sqlalchemy import select

        from ..db.models import CodeModuleModel
        from . import project_knowledge as pk

        async with _factory()() as s:
            m = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project_id,
                                                               CodeModuleModel.name == key))).scalars().first()
            if m is None:
                return {"module_id": None, "built_against": None}
            row = await pk.get(s, project_id, "acceptance", f"selftest:{key}")
            v = (row or {}).get("body", {}).get("plan_version") if isinstance((row or {}).get("body"), dict) else None
            return {"module_id": m.id, "built_against": v}

    async def generate_module(self, project_id: str, key: str, instruction: str, module_id: str | None,
                              log: Callable[[str], Awaitable[None]], is_cancelled: Callable[[], Awaitable[bool]]) -> dict[str, Any]:
        """One plan-mode generation. Returns the terminal ``result`` or ``error`` event. Waits for the project's generation
        lock (an interactive generation in the module editor finishes first)."""
        from . import module_generator as mg

        while any(k[0] == project_id and not k[1].startswith("build:") for k in mg._ACTIVE):
            await log("Waiting for another generation in this project to finish")
            for _ in range(20):
                if await is_cancelled():
                    raise _Cancelled()
                await asyncio.sleep(0.25)
        lock = mg.try_acquire(project_id, f"build:{key}")          # one per module, so modules generate in parallel
        try:
            # a saved module is only patched: a full re-emit of it is refused
            req = mg.GenerateRequest(prompt=instruction[:mg.MAX_PROMPT_CHARS], module_id=module_id, module_name=key,
                                     plan_module=key, self_test=True, clarify=False, patch_only=module_id is not None)
            gen = mg.Generation(project_id, req, is_cancelled)
            last: dict[str, Any] | None = None
            async for ev in gen.events():
                if ev["type"] == "status" and ev.get("message"):
                    await log(str(ev["message"]))
                elif ev["type"] == "draft" and isinstance(ev.get("source"), str):
                    emit_file("backend", f"backend/{key}.py", ev["source"], done=False)
                elif ev["type"] == "self_test":
                    bad = len([r for r in ev.get("results") or [] if r.get("ok") is False])
                    await log(f"Self-test: {len(ev.get('results') or []) - bad} passed, {bad} failed")
                if ev["type"] in ("result", "error"):
                    last = ev
            if await is_cancelled():
                raise _Cancelled()
            if last is None:
                return {"type": "error", "code": "internal", "message": "the generation ended without a result"}
            if last["type"] == "error":
                last = {**last, "problems": [{"code": str(p.get("code") or "problem"), "message": mg.problem_line(p)}
                                             for p in gen.last_problems][:20]}
            return last
        finally:
            mg.release(lock)

    async def scaffold_module(self, project_id: str, key: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """The plan's deterministic first draft of module ``key`` (``source``, ``todo``, ``covered``, ``tables``), or None when
        none can be made (the module is then generated whole)."""
        from . import module_generator as mg
        from .backend_templates import scaffold

        try:
            out = scaffold.scaffold_module(body, key)
            out["tables"] = mg.plan_declared(mg.plan_context(body, None, key))
            compile(out["source"], f"{key}.py", "exec")                      # a scaffold must at least parse
            return out
        except Exception:  # noqa: BLE001 - never a reason to fail a build
            logger.warning("build: no scaffold for module %s", key, exc_info=True)
            return None

    async def save_module(self, project_id: str, key: str, module_id: str | None, source: str,
                          tables: list[dict[str, Any]] | None) -> str:
        """Save through the normal module save path (create or update). Raises ``ProjectError`` when the save is refused."""
        from ..db.models import ProjectModel
        from .code_module_service import CodeModuleService

        async with _factory()() as s:
            project = await s.get(ProjectModel, project_id)
            svc = CodeModuleService(s)
            try:
                if module_id:
                    out = await svc.update(project, module_id, source, actor="build", tables=tables)
                else:
                    out = await svc.create(project, key, source, actor="build", tables=tables)
                await s.commit()
            except Exception:
                await s.rollback()
                raise
            await _emit_tables(s, project, list(out.get("tables_created") or [])
                               + [m.get("table") for m in out.get("tables_migrated") or [] if isinstance(m, dict)])
            return out["module"]["id"]

    async def check_module(self, project_id: str, module_id: str) -> dict[str, Any]:
        from . import builder_gates as bg
        from .backend_templates import selftest

        out = await bg.check_module(project_id, module_id)
        async with _factory()() as s:
            await bg.record_check(s, project_id, "module", out["module"]["name"], out)
        problems = [{"code": str(p.get("code") or "static"), "message": str(p.get("message"))} for p in out["static"]]
        problems += [{"code": str(e.get("code") or "analysis"), "message": str(e.get("message"))} for e in out["analysis"]["errors"]]
        problems += [{"code": "self_test_failed", "message": line.lstrip("- ")} for line in
                     selftest.failure_lines(out["self_test"].get("checks") or [])]
        return {"ok": bool(out["ok"]), "problems": problems, "checks": list(out["self_test"].get("checks") or [])}

    async def check_module_changed(self, project_id: str, module_id: str, routes: set[str] | None) -> dict[str, Any]:
        """``check_module`` with the self-test limited to ``routes`` ("METHOD /path"); None checks every route."""
        from . import module_generator as mg

        token = mg.SELF_TEST_ONLY.set(frozenset(routes) if routes is not None else None)
        try:
            return await self.check_module(project_id, module_id)
        finally:
            mg.SELF_TEST_ONLY.reset(token)

    async def mark_built(self, project_id: str, key: str, source: str, plan_version: Any) -> None:
        """Record that module ``key`` is built against ``plan_version`` (what a generation does itself), for a scaffold that
        needed no generation. Never raises."""
        import hashlib

        from . import project_knowledge as pk

        try:
            async with _factory()() as s:
                await pk.upsert(s, project_id, "acceptance", f"selftest:{key}", title=f"self-test steps for {key}",
                                body={"steps": [], "source_sha": hashlib.sha256(source.encode()).hexdigest(),
                                      "plan_version": plan_version},
                                text=f"self-test {key}", status="approved", source="design", source_ref=f"module:{key}", commit=False)
                await pk.index_module(s, project_id, source, key, commit=False)
                await s.commit()
        except Exception:  # noqa: BLE001 - a convenience: the build result stands
            logger.warning("build: could not record module %s as built", key, exc_info=True)

    async def input_needed(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Plan secrets and their state in the dev environment, and the planned tables that need starting rows."""
        from ..db.models import ProjectModel
        from . import project_plan as pp
        from .data_table_service import DataTableService
        from .secret_service import SecretService

        p = pp.normalize(body)
        async with _factory()() as s:
            project = await s.get(ProjectModel, project_id)
            rows = await SecretService(s).effective(project)
            live = {t["name"]: t for t in await DataTableService(s).list_tables(project)}
        return input_request(p, rows, {n: int(t.get("row_count") or 0) for n, t in live.items()},
                             default_env=getattr(project, "default_environment", None))

    async def ensure_app(self, project_id: str, app: dict[str, Any]) -> str:
        """The studio app implementing plan app ``app``: the bound one when it still exists, else a new one, bound to the plan."""
        from ..db.models import AppModel
        from ..schemas.app import AppCreateRequest
        from . import project_plan as pp

        async with _factory()() as s:
            if app.get("app_id"):
                row = await s.get(AppModel, app["app_id"])
                if row is not None and row.project_id == project_id:
                    return row.id
            svc = _app_service(s)
            created = await svc.create_app(AppCreateRequest(name=app.get("name") or app["key"], definition={"sourceCode": None},
                                                            project_id=project_id), created_by="build")
            await pp.bind_app(s, project_id, app["key"], created.id)
            return created.id

    async def build_app(self, project_id: str, app_id: str, key: str, instruction: str,
                        log: Callable[[str], Awaitable[None]]) -> dict[str, Any]:
        """One headless app builder turn. Its files are saved as a new app version (as the Studio saves a chat turn)."""
        import json

        from ..repositories.api_test_repository import ApiTestRepository
        from ..schemas.app import AppFilePayload, AppUpdateRequest
        from ..schemas.app_builder import AppBuilderChatRequest
        from .app_builder_ai_service import AppBuilderAIService
        from .client_codegen import CLIENT_PATH

        from . import project_files as pf

        saved = False
        errors: list[dict[str, str]] = []
        text = ""
        async with _factory()() as s:
            app_service = _app_service(s)
            svc = AppBuilderAIService(ApiTestRepository(s), app_service)
            slug = ""                                # the app's tree slug, looked up on the first write
            written: dict[str, str] = {}            # path -> content already streamed in this turn

            async def on_files(files: list[dict[str, Any]]) -> None:
                nonlocal slug
                slug = slug or await pf.app_slug(s, project_id, app_id)
                for f in files:
                    if re.sub(r"^\.?/?(src/)?", "", f["path"]) == CLIENT_PATH:
                        continue             # generated, never saved with the app
                    path = f"apps/{slug}/{f['path']}"
                    deleted = f.get("content") is None
                    emit_file("ui", path, "" if deleted else f["content"], op="delete" if deleted else "write")
                    written[path] = f.get("content")

            svc.on_files = on_files
            req = AppBuilderChatRequest(message=instruction, app_id=app_id, project_id=project_id, plan_app=key)
            async for ev in svc.stream_chat(req):
                if ev.get("event") != "message":
                    continue
                try:
                    data = json.loads(ev["data"])
                except (TypeError, ValueError):
                    continue
                kind = data.get("type")
                if kind == "text" and data.get("content"):
                    text = str(data["content"])
                    await log(text.splitlines()[0][:300])
                elif kind == "tool_call" and data.get("tool"):
                    await log(f"UI builder: {data['tool']}")
                elif kind == "clarify":
                    # a headless build cannot answer: the questions become the step's problems (the plan should answer them)
                    qs = [str(q.get("question") if isinstance(q, dict) else q) for q in data.get("questions") or []]
                    errors.append({"code": "app_builder_asked", "message": "the UI builder asked instead of building: "
                                   + "; ".join(qs)[:400]})
                elif kind == "partial":
                    errors += [{"code": "screen_missing", "message": f"not built: {m}"} for m in data.get("missing") or []][:20]
                elif kind == "error":
                    errors.append({"code": str(data.get("code") or "app_builder"), "message": str(data.get("message"))[:500]})
                elif kind == "code":
                    files = [{"path": f["path"], "content": f["content"]} for f in data.get("files") or []
                             if re.sub(r"^\.?/?(src/)?", "", f["path"]) != CLIENT_PATH]
                    entry = next((f for f in files if re.fullmatch(r"(src/)?[Aa]pp\.tsx", f["path"])), files[0] if files else None)
                    detail = await app_service.update_app(app_id, AppUpdateRequest(
                        source_code=entry["content"] if entry else "", files=[AppFilePayload(**f) for f in files],
                        create_version=True, version_trigger="ai", version_prompt=instruction[:2000]))
                    saved = True
                    slug = slug or await pf.app_slug(s, project_id, app_id)
                    for f in files:          # whatever the turn did not stream as it wrote it
                        if written.get(f"apps/{slug}/{f['path']}") != f["content"]:
                            emit_file("ui", f"apps/{slug}/{f['path']}", f["content"])
                    cv = getattr(detail, "current_version", None)
                    emit({"type": "preview", "app_id": app_id, "version_id": str(cv.id) if cv is not None else None})
                    await log(f"Saved {len(files)} file(s) as a new app version")
        return {"saved": saved, "errors": errors, "text": text}

    async def check_app(self, project_id: str, app_id: str) -> dict[str, Any]:
        from . import builder_gates as bg
        from .project_service import resolve_project

        async with _factory()() as s:
            svc = _app_service(s)
            app = await svc.get_app(app_id)
            if app is None:
                return {"ok": False, "problems": [{"code": "app_not_found", "message": "the app no longer exists"}]}
            files = [{"path": f.path, "content": f.content} for f in app.files]
            if not files and app.source_code:
                files = [{"path": "App.tsx", "content": app.source_code}]
            project = await resolve_project(s, project_id)
            out = await bg.check_app_files(s, project, files)
            await bg.record_check(s, project_id, "app", app_id, out)
        problems = [{"code": "tsc", "message": m} for m in out["tsc"]["errors"]]
        for k in ("syntax", "contract", "call_shape", "invented_literals"):
            problems += [{"code": k, "message": str(m)} for m in out[k]]
        return {"ok": bool(out["ok"]), "problems": problems[:40]}

    async def check_plan(self, project_id: str) -> dict[str, Any]:
        from . import builder_gates as bg

        return await bg.check_plan(project_id)

    async def check_project(self, project_id: str) -> dict[str, Any]:
        from . import builder_gates as bg

        out = await bg.check_project(project_id)
        async with _factory()() as s:
            await bg.record_check(s, project_id, "project", project_id, out)
        return out


def _app_service(s: Any) -> Any:
    from .app_service import AppService
    from .bundle_storage import PostgresBundleStorage
    from .file_storage import PostgresFileStorage

    return AppService(s, PostgresFileStorage(s), PostgresBundleStorage(s))


def make_builders() -> Builders:
    return Builders()


# ── pure helpers ─────────────────────────────────────────────────────────────


def dev_environment(p: dict[str, Any], default_env: str | None = None) -> str:
    """The environment the user's input is asked for: the plan's ``dev``, else its first non-live one, else the project's."""
    envs = [e for e in p.get("environments") or [] if isinstance(e, dict) and e.get("name")]
    named = next((e["name"] for e in envs if e["name"] == "dev"), None)
    return named or next((e["name"] for e in envs if not e.get("live")), None) or default_env or "dev"


def input_request(p: dict[str, Any], secret_rows: list[dict[str, Any]], row_counts: dict[str, int],
                  default_env: str | None = None) -> dict[str, Any]:
    """What the build needs from the user (``p`` is a normalized plan).

    Secrets: every plan secret used in the dev environment (plus integration secrets the plan does not list), ``set`` when
    an effective secret row exists there (or in ``default``). Tables: a planned table nothing in the plan writes (no route
    writes it, no state machine moves it) but something reads, or one whose description says it is reference / catalog /
    lookup data, when it holds fewer than ``SUGGESTED_MIN_ROWS`` rows."""
    env = dev_environment(p, default_env)
    wanted: dict[str, str] = {}
    for sec in p.get("secrets") or []:
        if sec.get("name") and (not sec.get("environments") or env in sec["environments"]):
            wanted[sec["name"]] = str(sec.get("purpose") or "")
    for ig in p.get("integrations") or []:
        for n in ig.get("secrets") or []:
            wanted.setdefault(n, f"used by {ig.get('key')}")
    have = {r.get("key") for r in secret_rows if r.get("environment") in (env, "default") and r.get("type", "secret") == "secret"
            and not r.get("overridden")}
    secrets = [{"name": n, "purpose": purpose, "set": n in have} for n, purpose in wanted.items()]
    written = {t for r in p.get("routes") or [] for t in r["tables"]["writes"]}
    read = {t for r in p.get("routes") or [] for t in r["tables"]["reads"]} | {t for m in p.get("modules") or [] for t in m.get("reads") or []}
    tables = []
    for t in p.get("data", {}).get("tables") or []:
        moved = bool(t.get("state_machine")) and any(x.get("route") for x in t["state_machine"].get("transitions") or [])
        seeded = (t["name"] in read and t["name"] not in written and not moved) or bool(SEED_HINT.search(t.get("description") or ""))
        rows = int(row_counts.get(t["name"], 0))
        if seeded and rows < SUGGESTED_MIN_ROWS:
            tables.append({"name": t["name"], "columns": [c["name"] for c in t["columns"]], "rows": rows,
                           "suggested_min": SUGGESTED_MIN_ROWS})
    missing = [s["name"] for s in secrets if not s["set"]]
    parts = []
    if missing:
        parts.append(f"Set {len(missing)} secret(s) in '{env}': {', '.join(missing)}")
    if tables:
        parts.append("Add starting rows to " + ", ".join(f"{t['name']} (at least {t['suggested_min']})" for t in tables))
    return {"environment": env, "secrets": secrets, "tables": tables,
            "message": ("; ".join(parts) + ". Then continue, or skip to build without them.") if parts else ""}


def input_missing(inp: dict[str, Any]) -> list[str]:
    return [f"secret {s['name']}" for s in inp["secrets"] if not s["set"]] + [f"rows for {t['name']}" for t in inp["tables"]]


def plan_check_summary(rep: dict[str, Any]) -> dict[str, Any]:
    """``{ok, missing}`` for the report: the plan check's ``missing`` lists, plus every other non-empty finding list that keeps
    it from passing (preflight errors, drift, duplicates, stale modules, foreign writes, unplanned tables / secrets)."""
    if not rep.get("has_plan", True):
        return {"ok": True, "missing": {}}
    missing: dict[str, list[Any]] = {k: list(v) for k, v in (rep.get("missing") or {}).items() if v}
    extra = {"preflight": [f for f in rep.get("preflight") or [] if f.get("severity") == "error"],
             "differs": rep.get("differs") or [], "drift": [d for d in rep.get("drift") or [] if d.get("ops") or d.get("refused")],
             "duplicates": rep.get("duplicates") or [], "stale": rep.get("stale") or [],
             "foreign_writes": rep.get("foreign_writes") or [],
             "unplanned_tables": (rep.get("unplanned") or {}).get("tables") or [],
             "unplanned_secrets": (rep.get("unplanned") or {}).get("secrets") or []}
    missing.update({k: v for k, v in extra.items() if v})
    return {"ok": bool(rep.get("ok")), "missing": missing}


def named_gaps(errors: list[dict[str, str]]) -> dict[str, list[str]]:
    """A one-shot build's remaining errors as the report's named gap lists, so a build that is not clean never shows an unnamed gap:
    ``scenarios`` (a scenario test still failing) and ``checks`` (every other error, with where it was found)."""
    scen = [f"{e['where'][9:]}: {e['message'].split('. Fix the code')[0]}"[:300] for e in errors if e["where"].startswith("scenario:")]
    other = [f"[{e['where']}] {e['message']}"[:300] for e in errors if not e["where"].startswith("scenario:")]
    return {**({"scenarios": scen[:10]} if scen else {}), **({"checks": other[:10]} if other else {})}


def _merge_autotest(parts: list[dict[str, Any] | None]) -> dict[str, Any]:
    """The scenario results of every app build of one run as one report (``total``, ``passed``, ``results``, ``skipped``)."""
    parts = [p for p in parts if p]
    results = [r for p in parts for r in p.get("results") or []]
    skipped = next((p["skipped"] for p in parts if p.get("skipped") and not p.get("total")), None)
    return {"total": len(results), "passed": sum(1 for r in results if r["passed"]), "results": results,
            **({"skipped": skipped} if skipped and not results else {})}


def plan_problems(rep: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for k, items in plan_check_summary(rep)["missing"].items():
        for x in items[:20]:
            if isinstance(x, dict):
                msg = x.get("message") or ", ".join(f"{a}={b}" for a, b in x.items() if isinstance(b, (str, int)))
            else:
                msg = str(x)
            out.append({"code": f"missing_{k}" if k in ("modules", "routes", "tables", "columns", "screens", "apps", "jobs") else k,
                        "message": str(msg)[:300]})
    return out[:60]


def public_build(row: Any, *, with_log: bool = True) -> dict[str, Any]:
    def iso(v: Any) -> str | None:
        return v.isoformat() if v else None

    steps = [dict(s) for s in row.steps or []]
    if not with_log:
        for s in steps:
            s.pop("log", None)
    return {"id": row.id, "project_id": row.project_id, "plan_version": row.plan_version, "app_key": row.app_key,
            "status": row.status, "reason": row.reason, "created_at": iso(row.created_at), "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at), "steps": steps, "report": row.report}


def _step(key: str, kind: str, title: str, builder: str) -> dict[str, Any]:
    return {"key": key, "kind": kind, "title": title, "builder": builder, "status": "pending", "attempt": 0,
            "started_at": None, "finished_at": None, "summary": None, "problems": [], "module_id": None, "app_id": None,
            "duration_s": None, "model_calls": 0, "log": []}


def initial_steps(body: dict[str, Any], app_key: str | None) -> list[dict[str, Any]]:
    from . import project_plan as pp

    p = pp.normalize(body)
    names = {a["key"]: a["name"] or a["key"] for a in p["apps"]}
    mods = [k for k in pp.build_order(p) if not str(k).startswith("app:")]
    steps = [_step(f"module:{k}", "module", f"Backend · {_title(k)}", "backend") for k in mods]
    steps.append({**_step("input", "input", "Your input", "check"), "input": None})
    steps += [_step(f"app:{a}", "app", f"UI · {names[a]}", "ui") for a in names if app_key is None or a == app_key]
    return steps


# ── the registry (this process) ──────────────────────────────────────────────


class _Run:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.cancel = asyncio.Event()
        self.resume = asyncio.Event()
        self.skip = False


_RUNS: dict[str, _Run] = {}
_SUBS: dict[str, set[asyncio.Queue]] = {}


def _publish(build_id: str, ev: dict[str, Any]) -> None:
    for q in list(_SUBS.get(build_id, ())):
        q.put_nowait(ev)


def subscribe(build_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _SUBS.setdefault(build_id, set()).add(q)
    return q


def unsubscribe(build_id: str, q: asyncio.Queue) -> None:
    subs = _SUBS.get(build_id)
    if subs is not None:
        subs.discard(q)
        if not subs:
            _SUBS.pop(build_id, None)


# ── the live stream: file / preview / route events ───────────────────────────


@contextlib.contextmanager
def step_scope(build_id: str, step_key: str | None, actor: str) -> Iterator[None]:
    """Inside: :func:`emit` publishes to ``build_id`` for ``step_key``, and file changes are recorded with ``actor``."""
    from . import project_files as pf

    token = _STEP.set({"build_id": build_id, "step_key": step_key})
    try:
        with pf.change_context(actor, build_id, step_key):
            yield
    finally:
        _STEP.reset(token)


@contextlib.contextmanager
def sink_scope(sink: Callable[[dict[str, Any]], None], step_key: str | None, actor: str) -> Iterator[None]:
    """Like :func:`step_scope`, but :func:`emit` hands the events to ``sink`` (the app builder chat stream) instead of a build."""
    from . import project_files as pf

    token = _STEP.set({"build_id": None, "step_key": step_key, "sink": sink})
    try:
        with pf.change_context(actor, None, step_key):
            yield
    finally:
        _STEP.reset(token)


def emit(ev: dict[str, Any]) -> None:
    """Publish ``ev`` to the build of the current step (``step_key`` filled in). Outside a build step: nothing."""
    ctx = _STEP.get()
    if ctx is None:
        return
    out = ev if ev.get("type") == "preview" else {"step_key": ctx["step_key"], **ev}
    if ctx.get("sink") is not None:
        ctx["sink"](out)
    else:
        _publish(ctx["build_id"], out)


def file_event(builder: str, path: str, content: str, *, done: bool = True, op: str = "write",
               step_key: Any = ...) -> dict[str, Any]:
    raw = content.encode()
    cut = len(raw) > FILE_CAP
    ev = {"type": "file", "builder": builder, "path": path, "op": op,
          "content": raw[:FILE_CAP].decode(errors="ignore") if cut else content, "done": done, "truncated": cut}
    if step_key is not ...:
        ev["step_key"] = step_key
    return ev


def emit_file(builder: str, path: str, content: str, *, done: bool = True, op: str = "write") -> None:
    emit(file_event(builder, path, content, done=done, op=op))


async def _emit_tables(s: Any, project: Any, names: list[Any]) -> None:
    """A ``tables`` file event per table a module save created or migrated (its tree JSON). Never raises."""
    if _STEP.get() is None:
        return
    from . import project_files as pf
    from . import project_plan as pp
    from .backend_templates import selftest
    from .data_table_service import DataTableService

    try:
        rec = await pp.load_approved(s, project.id)
        owners = {t["name"]: t.get("owner") for t in pp.normalize(rec["body"])["data"]["tables"]} if rec else {}
        svc = DataTableService(s)
        for n in dict.fromkeys(x for x in names if isinstance(x, str) and x and not selftest.SCRATCH_RE.search(x)):
            t = await svc.table_by_name(project.id, n)
            if t is not None:
                emit_file("tables", pf.table_path(n), pf.table_json(n, t.columns, owners.get(n), await svc.row_count(t.id)))
    except Exception:  # noqa: BLE001 - the live view is a convenience, never a reason to fail the save
        logger.warning("could not stream the tables of %s", project.id, exc_info=True)


def _shape(path: str) -> list[str]:
    return ["{}" if ("{" in seg or seg.startswith(("$", ":"))) else seg for seg in path.strip("/").split("/")]


def route_statuses(routes: list[tuple[str, str]], checks: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """``{(method, path): 'passing'|'failing'}`` for the routes some self-test check exercised (a check's ``route`` label is
    ``METHOD /path`` with placeholders or concrete values); a route with any failed check is failing. Skipped checks count
    for nothing."""
    out: dict[tuple[str, str], str] = {}
    shapes = [(m, p, _shape(p)) for m, p in routes]
    for c in checks or []:
        if c.get("skipped") or "ok" not in c:
            continue
        m = re.match(r"^\s*([A-Za-z]+)\s+(/\S*)", str(c.get("route") or ""))
        if not m:
            continue
        method, segs = m.group(1).upper(), _shape(m.group(2).split("?")[0])
        fits = [(sum(a == b for a, b in zip(rs, segs)), rm, rp) for rm, rp, rs in shapes
                if rm == method and len(rs) == len(segs) and all(a == "{}" or b == "{}" or a == b for a, b in zip(rs, segs))]
        if fits:            # the most specific route the check fits
            _, rm, rp = max(fits, key=lambda f: f[0])
            if out.get((rm, rp)) != "failing":
                out[(rm, rp)] = "passing" if c.get("ok") else "failing"
    return out


def drop_stub_failures(chk: dict[str, Any], stubs: list[str]) -> dict[str, Any]:
    """A module check without the failures of routes that are still scaffold TODO stubs (they answer 501 by design until the
    model writes them). ``stubs``: "METHOD /path" labels."""
    shapes = [(m.upper(), _shape(p)) for m, p in (x.split(" ", 1) for x in stubs if " " in x)]
    checks = chk.get("checks") or []
    if not shapes or not checks:
        return chk

    def is_stub(c: dict[str, Any]) -> bool:
        m = re.match(r"^\s*([A-Za-z]+)\s+(/\S*)", str(c.get("route") or ""))
        if not m:
            return False
        segs = _shape(m.group(2).split("?")[0])
        return any(m.group(1).upper() == sm and len(segs) == len(ss) and all(a == "{}" or b == "{}" or a == b for a, b in zip(ss, segs))
                   for sm, ss in shapes)

    kept = [c for c in checks if not (c.get("ok") is False and is_stub(c))]
    if len(kept) == len(checks):
        return chk
    from .backend_templates import selftest

    problems = [p for p in chk.get("problems") or [] if p.get("code") != "self_test_failed"]
    problems += [{"code": "self_test_failed", "message": line.lstrip("- ")} for line in selftest.failure_lines(kept)]
    return {**chk, "checks": kept, "problems": problems, "ok": not problems}


def concurrency() -> int:
    try:
        return max(1, min(int(os.environ.get("WORKFLOW_BUILD_CONCURRENCY") or DEFAULT_CONCURRENCY), 16))
    except ValueError:
        return DEFAULT_CONCURRENCY


# ── the coordinator ──────────────────────────────────────────────────────────


class Coordinator:
    def __init__(self, build_id: str, run: _Run, builders: Builders | None = None) -> None:
        self.id = build_id
        self.run = run
        self.b = builders or make_builders()
        self.row: Any = None
        self.body: dict[str, Any] = {}
        self.targets: dict[str, str] = {}       # step key -> "module:<k>" / "app:<k>" it builds or fixes
        self._lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(concurrency())
        self.info: dict[str, dict[str, Any]] = {}   # module key -> scaffold state of the fast path
        self.timing_extra: dict[str, Any] | None = None   # per-phase wall time / model calls of a one-shot build

    # persistence
    async def _load(self) -> None:
        from ..db.build_models import ProjectBuildModel

        async with _factory()() as s:
            self.row = await s.get(ProjectBuildModel, self.id)
            if self.row is None:
                raise RuntimeError(f"build {self.id} not found")
            self.row.steps = [dict(x) for x in self.row.steps or []]

    async def _save(self) -> None:
        from ..db.build_models import ProjectBuildModel

        async with self._lock:
            async with _factory()() as s:
                row = await s.get(ProjectBuildModel, self.id)
                for f in ("status", "reason", "report", "started_at", "finished_at"):
                    setattr(row, f, getattr(self.row, f))
                row.steps = [dict(x) for x in self.row.steps]
                row.updated_at = datetime.now()
                s.add(row)
                await s.commit()

    def _get(self, key: str) -> dict[str, Any]:
        return next(x for x in self.row.steps if x["key"] == key)

    async def _update(self, step: dict[str, Any], **kw: Any) -> None:
        step.update(kw)
        if kw.get("finished_at") and step.get("started_at"):
            try:
                step["duration_s"] = round((datetime.fromisoformat(step["finished_at"])
                                            - datetime.fromisoformat(step["started_at"])).total_seconds(), 2)
            except ValueError:
                pass
        await self._save()
        _publish(self.id, {"type": "step", "step": {k: v for k, v in step.items() if k != "log"}})

    async def _log(self, step: dict[str, Any], message: str) -> None:
        step["log"] = (list(step.get("log") or []) + [message])[-LOG_KEEP:]
        await self._save()
        _publish(self.id, {"type": "log", "step_key": step["key"], "message": message})

    def _add_step(self, key: str, kind: str, title: str, builder: str) -> dict[str, Any]:
        st = _step(key, kind, title, builder)
        self.row.steps.append(st)
        return st

    # cancellation
    async def _is_cancelled(self) -> bool:
        return self.run.cancel.is_set()

    def _check(self) -> None:
        if self.run.cancel.is_set():
            raise _Cancelled()

    async def _guard(self, coro: Awaitable[Any]) -> Any:
        """Await ``coro``; a cancel request cancels it where it is (an LLM call, a sandbox run) and raises ``_Cancelled``."""
        self._check()
        task = asyncio.ensure_future(coro)
        stop = asyncio.ensure_future(self.run.cancel.wait())
        try:
            await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
        if task.done():
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise _Cancelled()

    # the run
    async def execute(self) -> None:
        from . import project_plan as pp

        await self._load()
        try:
            self.row.status, self.row.started_at = "running", datetime.now()
            await self._save()
            async with _factory()() as s:
                rec = await pp.load_approved(s, self.row.project_id)
            if rec is None:
                return await self._finish("failed", reason="no_approved_plan")
            self.body = pp.normalize(rec["body"])
            self._announce(rec)
            from . import oneshot_builder

            if oneshot_builder.builder_mode() == "oneshot":
                return await self._oneshot(oneshot_builder)
            if not await self._fast_modules():
                for st in [x for x in self.row.steps if x["kind"] == "module"]:
                    await self._module_step(st)
                await self._input_step(self._get("input"))
                for st in [x for x in self.row.steps if x["kind"] == "app"]:
                    await self._app_step(st)
            await self._verify()
        except _Cancelled:
            await self._finish("cancelled")
        except asyncio.CancelledError:          # engine shutdown
            with contextlib.suppress(Exception):
                await asyncio.shield(self._finish("failed", reason="interrupted"))
            raise
        except Exception as e:  # noqa: BLE001 - a hard error ends the build, never the engine
            logger.exception("build %s failed", self.id)
            await self._finish("failed", reason=f"{type(e).__name__}: {str(e)[:300]}")

    # the default path: one model call writes the app, the platform loads and checks it
    async def _oneshot(self, ob: Any) -> None:
        pid = self.row.project_id
        timer = ob.Timer()
        mod_steps = [x for x in self.row.steps if x["kind"] == "module"]
        app_steps = [x for x in self.row.steps if x["kind"] == "app"]
        known = {x["key"].split(":", 1)[1] for x in mod_steps}
        for st in mod_steps:
            self.targets[st["key"]] = st["key"]
            await self._update(st, status="running", started_at=_now(), summary="Written with the app, in one pass")
        inp = self._get("input")
        await self._update(inp, status="skipped", started_at=_now(), finished_at=_now(), input=None,
                           summary="Nothing to wait for: seed data comes with the build, real data is added later")
        existing: dict[str, Any] | None = None
        results: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for st in app_steps:
            key = st["key"].split(":", 1)[1]
            self.targets[st["key"]] = st["key"]
            app = next(a for a in self.body["apps"] if a["key"] == key)
            await self._update(st, status="running", started_at=_now(), summary="Writing the app in one pass")
            app_id = await self._guard(self.b.ensure_app(pid, app))
            await self._update(st, app_id=app_id)
            try:
                with step_scope(self.id, st["key"], "ui_builder"):
                    res = await self._guard(ob.build(pid, self.body, app_key=key, app_id=app_id, existing=existing, known_modules=known,
                                                     log=lambda m, st=st: self._log(st, m), timer=timer))
            except ob.WriteError as e:
                for x in mod_steps + [st]:
                    await self._update(x, status="failed", finished_at=_now(), summary=f"The app was not written: {e}"[:300],
                                       problems=[{"code": "write_failed", "message": str(e)[:500]}])
                return await self._finish("failed", reason=f"write_failed: {str(e)[:200]}")
            existing = res["bundle"]
            results.append((st, res))
            await self._oneshot_steps(st, mod_steps, res)
        if not app_steps:                       # a plan without apps: the backend alone
            try:
                with step_scope(self.id, mod_steps[0]["key"] if mod_steps else None, "backend_builder"):
                    res = await self._guard(ob.build(pid, self.body, known_modules=known, timer=timer,
                                                     log=lambda m: self._log(mod_steps[0], m) if mod_steps else asyncio.sleep(0)))
            except ob.WriteError as e:
                return await self._finish("failed", reason=f"write_failed: {str(e)[:200]}")
            await self._oneshot_steps(None, mod_steps, res)
            results.append(({}, res))
        if results and app_steps:
            app_steps[0]["model_calls"] = timer.calls
        self.timing_extra = timer.summary()
        errors = [e for _, r in results for e in r["errors"]]
        autotest = _merge_autotest([r.get("autotest") for _, r in results])
        st = self._add_step("verify:final", "verify", "Check · Final", "check")
        await self._update(st, status="running", attempt=1, started_at=_now(), summary="Summing up the build")
        t = self.timing_extra
        await self._log(st, f"Timing: {t['total_seconds']} s, {t['model_calls']} model call(s); "
                            + ", ".join(f"{k} {v['seconds']} s" + (f"/{v['model_calls']}" if v["model_calls"] else "") for k, v in t["phases"].items()))
        calls = sum(len(r["routes"]) for _, r in results)
        failing = sum(1 for _, r in results for x in r["routes"] if x["status"] != "passing")
        scen = [e for e in errors if e["where"].startswith("scenario:")]
        code_errors = [e for e in errors if not e["where"].startswith("scenario:")]
        tested = (f"; {autotest['passed']}/{autotest['total']} scenarios pass" if autotest["total"] else "")
        if not errors:
            summary = f"{calls} route(s) built, all passing; the app type-checks{tested}"
        elif not code_errors:
            summary = f"{calls} route(s) built, all passing, but {len(scen)} scenario(s) still fail: " + ", ".join(e["where"][9:] for e in scen[:4])
        else:
            summary = f"{len(code_errors)} problem(s) remain after the fix pass; {failing} of {calls} route(s) failing{tested}"
        await self._update(st, status="passed" if not errors else "partial", finished_at=_now(),
                           problems=[{"code": e["code"], "message": f"[{e['where']}] {e['message']}"} for e in errors[:30]],
                           summary=summary)
        await self._finish("done" if not errors else "partial",
                           plan_check={"ok": not errors, "missing": named_gaps(errors)},
                           project_check={"ok": not errors, "summary": f"{calls} route(s), {failing} failing; {t['model_calls']} model call(s)" + tested},
                           autotest=autotest)

    async def _oneshot_steps(self, app_step: dict[str, Any] | None, mod_steps: list[dict[str, Any]], res: dict[str, Any]) -> None:
        """Settle the module steps and the app step from one build's result."""
        errors = res["errors"]
        written = {m["name"]: m for m in res["modules"]}
        shared = {m["key"] for m in self.body["modules"] if m.get("kind") == "shared"}
        route_mod = {(r["method"], r["path"]): r["module"] for r in res["routes"]}

        def of_module(e: dict[str, str], key: str) -> bool:
            if e["where"] == f"module:{key}":
                return True
            m = re.match(r"route:(\w+) (\S+)", e["where"])
            return bool(m) and route_mod.get((m.group(1), m.group(2))) == key

        from . import project_plan as pp

        built_routes = {pp.route_norm(r["method"], r["path"]) for r in res["routes"]}
        planned: dict[str, set[str]] = {}
        for r in self.body.get("routes") or []:
            m = re.match(r"(\w+)\s+(\S+)", r.get("key") or "")
            if m and r.get("module"):
                planned.setdefault(r["module"], set()).add(pp.route_norm(m.group(1), m.group(2)))

        def planned_present(key: str) -> bool:
            return bool(planned.get(key)) and planned[key] <= built_routes

        def of_planned_route(e: dict[str, str], key: str) -> bool:
            m = re.match(r"route:(\w+) (\S+)", e["where"])
            return bool(m) and pp.route_norm(m.group(1), m.group(2)) in planned[key]

        for st in mod_steps:
            key = st["key"].split(":", 1)[1]
            if key in written:
                probs = [{"code": e["code"], "message": e["message"]} for e in errors if of_module(e, key)]
                n = len(written[key]["routes"])
                await self._update(st, status="passed" if not probs else "partial", finished_at=_now(), module_id=written[key]["id"],
                                   problems=probs[:30], summary=(f"Saved {key}.py with {n} route(s); its checks pass" if not probs
                                                                  else f"Saved {key}.py, but {len(probs)} problem(s) remain"))
            elif key in shared:
                await self._update(st, status="skipped", finished_at=_now(), summary="Shared helpers are part of the app's module")
            elif planned_present(key):      # the model put this module's routes in another module: the routes are what count
                probs = [{"code": e["code"], "message": e["message"]} for e in errors if of_planned_route(e, key)]
                await self._update(st, status="passed" if not probs else "partial", finished_at=_now(),
                                   problems=probs[:30], summary=(f"Its {len(planned[key])} route(s) were built (in another module); its checks pass"
                                                                  if not probs else f"Its routes were built, but {len(probs)} problem(s) remain"))
            else:
                await self._update(st, status="failed", finished_at=_now(), summary=f"The build wrote no module named {key}",
                                   problems=[{"code": "module_missing", "message": f"module {key} was not written"}])
        if app_step is not None:
            own = [e for e in errors if not e["where"].startswith(("module:", "route:", "scenario:"))]
            probs = [{"code": e["code"], "message": f"[{e['where']}] {e['message']}"} for e in own]
            await self._update(app_step, status="passed" if not own else "partial", finished_at=_now(), problems=probs[:30],
                               summary=("Built and saved the app; its checks pass" if not own
                                        else f"Saved the app, but {len(own)} problem(s) remain"))

    # the fast path: scaffolds first, the model only on what they cannot cover
    async def _all(self, coros: list[Awaitable[Any]]) -> None:
        """Run ``coros`` together; the first failure (a cancel included) cancels the rest and is raised."""
        tasks = [asyncio.ensure_future(c) for c in coros]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    def _layers(self, keys: list[str]) -> list[list[str]]:
        """``keys`` (build order) grouped so that every module comes after the modules it depends on; a group is independent."""
        from . import project_plan as pp

        graph = pp._module_graph(self.body)
        level: dict[str, int] = {}
        for k in keys:
            level[k] = 1 + max([level[d] for d in graph.get(k, ()) if d in level] or [-1])
        return [[k for k in keys if level[k] == n] for n in sorted(set(level.values()))]

    async def _fast_modules(self) -> bool:
        """The whole module / input / app part of the build on the fast path. False (nothing done) when no module has a scaffold."""
        pid = self.row.project_id
        steps = [x for x in self.row.steps if x["kind"] == "module"]
        scaffolds: dict[str, dict[str, Any]] = {}
        for st in steps:
            key = st["key"].split(":", 1)[1]
            state = await self._guard(self.b.module_state(pid, key))
            if state["module_id"] is None:
                sc = await self._guard(self.b.scaffold_module(pid, key, self.body))
                if sc:
                    scaffolds[key] = sc
        if not scaffolds:
            return False
        t0 = time.monotonic()
        for st in steps:                          # 1. save every scaffold, in build order: all planned routes exist after this
            key = st["key"].split(":", 1)[1]
            sc = scaffolds.get(key)
            if sc is None:
                continue
            self._check()
            self.targets[st["key"]] = f"module:{key}"
            await self._update(st, status="running", started_at=_now(), summary="Scaffolding from the plan")
            with step_scope(self.id, st["key"], "backend_builder"):
                emit_file("backend", f"backend/{key}.py", sc["source"])
                try:
                    module_id = await self._guard(self.b.save_module(pid, key, None, sc["source"], sc.get("tables")))
                except ProjectError as e:
                    await self._log(st, f"The scaffold of {key} was refused ({e.code}); generating it whole")
                    continue
                st["module_id"] = module_id
                self.info[key] = {"sc": sc, "module_id": module_id, "source": sc["source"], "routes": self._routes_built(st, sc["source"])}
                await self._log(st, f"Saved the scaffold of {key}.py: {len(sc['covered'])} of {sc['routes']} planned routes built "
                                    f"from the plan with no model call; {len(sc['todo'])} TODO item(s) left")
        await self._log(steps[0], f"Scaffolds saved for {len(self.info)} module(s) in {time.monotonic() - t0:.1f} s")
        order = [x["key"].split(":", 1)[1] for x in steps]
        by_key = {x["key"].split(":", 1)[1]: x for x in steps}

        async def layers() -> None:
            for layer in self._layers(order):     # 2. module checks and the model's TODO passes, a layer at a time
                await self._all([self._fast_module(by_key[k]) for k in layer])

        async def ui() -> None:                   # 3. the input step and the UI do not wait for the TODO passes
            await self._input_step(self._get("input"))
            for st in [x for x in self.row.steps if x["kind"] == "app"]:
                await self._app_step(st)

        await self._all([layers(), ui()])
        return True

    async def _fast_module(self, st: dict[str, Any]) -> None:
        key = st["key"].split(":", 1)[1]
        inf = self.info.get(key)
        async with self.sem:
            if inf is None:                       # no scaffold (or an existing module): one generation, as before
                return await self._module_step(st)
            await self._scaffolded(st, key, inf)

    async def _scaffolded(self, st: dict[str, Any], key: str, inf: dict[str, Any]) -> None:
        pid, sc = self.row.project_id, inf["sc"]
        await self._update(st, summary="Checking the scaffold")
        chk = drop_stub_failures(await self._guard(self.b.check_module(pid, inf["module_id"])), leftovers(sc["source"]))
        self._routes_checked(st, inf["routes"], chk)
        problems = list(chk["problems"])
        if not sc["todo"] and not problems:
            await self._guard(self.b.mark_built(pid, key, sc["source"], self.row.plan_version))
            await self._update(st, status="passed", finished_at=_now(), problems=[],
                               summary=f"Built {sc['routes']} route(s) from the plan with no model call; its check passes")
            return
        lines = [todo_message(sc["todo"])] if sc["todo"] else []
        if problems:
            lines.append("The scaffold's check found these problems; fix them with patches:\n"
                         + "\n".join(f"- {p['message']}" for p in problems[:20]))
        await self._update(st, problems=problems)
        await self._build_module(st, key, inf["module_id"], f"[plan:{key}] " + "\n".join(lines), 1, base_source=sc["source"])

    async def _module_step(self, st: dict[str, Any]) -> None:
        self._check()
        key = st["key"].split(":", 1)[1]
        self.targets[st["key"]] = f"module:{key}"
        await self._update(st, status="running", started_at=_now())
        state = await self._guard(self.b.module_state(self.row.project_id, key))
        if state["module_id"] and state["built_against"] == self.row.plan_version and not self.row.rebuild:
            await self._log(st, f"{key} is already built against plan v{self.row.plan_version}; checking it")
            chk = await self._guard(self.b.check_module(self.row.project_id, state["module_id"]))
            if chk["ok"]:
                await self._update(st, status="skipped", finished_at=_now(), module_id=state["module_id"],
                                   summary=f"Already built against plan v{self.row.plan_version} and its check passes")
                return
        await self._build_module(st, key, state["module_id"],
                                 f"Build the {key} module from the approved plan v{self.row.plan_version}. [plan:{key}]",
                                 MODULE_ATTEMPTS)

    # the live stream
    def _announce(self, rec: dict[str, Any]) -> None:
        """At the start: PLAN.md, and every planned route as a stub."""
        from . import project_files as pf
        from .api_route_service import default_name

        try:
            _publish(self.id, file_event("plan", "PLAN.md", pf.plan_markdown(rec["body"], rec["version"]), step_key=None))
            for r in self.body["routes"]:
                method, path = r["key"].split(" ", 1)
                _publish(self.id, {"type": "route", "step_key": f"module:{r['module']}" if r.get("module") else None,
                                   "method": method, "path": path, "name": default_name(method, path), "status": "stub"})
        except Exception:  # noqa: BLE001 - the live view is a convenience, never a reason to fail the build
            logger.warning("build %s: could not announce the plan", self.id, exc_info=True)

    def _routes_built(self, st: dict[str, Any], source: str) -> list[tuple[str, str, str]]:
        """``(method, path, name)`` of the routes the saved source declares, each published as built."""
        from .api_route_service import default_name
        from .code_module_service import discover_routes

        try:
            decls = discover_routes(source)
        except ProjectError:
            return []
        out = [(d["method"], d["path"], d["name"] or default_name(d["method"], d["path"])) for d in decls]
        for method, path, name in out:
            _publish(self.id, {"type": "route", "step_key": st["key"], "method": method, "path": path, "name": name,
                               "status": "built"})
        return out

    def _routes_checked(self, st: dict[str, Any], routes: list[tuple[str, str, str]], chk: dict[str, Any]) -> None:
        """Per route from the check's self-test results; without them, a passing check passes every built route."""
        found = route_statuses([(m, p) for m, p, _ in routes], chk.get("checks") or [])
        for method, path, name in routes:
            status = found.get((method, path)) or ("passing" if chk.get("ok") else None)
            if status:
                _publish(self.id, {"type": "route", "step_key": st["key"], "method": method, "path": path, "name": name,
                                   "status": status})

    async def _build_module(self, st: dict[str, Any], key: str, module_id: str | None, instruction: str, attempts: int,
                            base_source: str | None = None) -> bool:
        with step_scope(self.id, st["key"], "backend_builder"):
            return await self._build_module_attempts(st, key, module_id, instruction, attempts, base_source)

    async def _build_module_attempts(self, st: dict[str, Any], key: str, module_id: str | None, instruction: str,
                                     attempts: int, base_source: str | None = None) -> bool:
        """``base_source``: the saved scaffold this pass patches. With it, the check after a save covers only the routes the save
        touched, and a TODO the model left is a problem (a gap), not a pass."""
        saved_any = False
        problems: list[dict[str, str]] = []
        for attempt in range(1, attempts + 1):
            self._check()
            await self._update(st, status="running", attempt=attempt, started_at=st["started_at"] or _now(),
                               summary="Generating" if attempt == 1 else "Follow-up generation for the problems found")
            res = await self._guard(self.b.generate_module(self.row.project_id, key, instruction, module_id,
                                                           lambda m: self._log(st, m), self._is_cancelled))
            st["model_calls"] = int(st.get("model_calls") or 0) + int((res.get("stats") or {}).get("model_calls") or 0)
            problems = []
            saved = False
            if res.get("type") == "error":
                problems = [{"code": str(res.get("code") or "generation_failed"), "message": str(res.get("message"))[:500]}]
                problems += list(res.get("problems") or [])[:20]
            else:
                path = f"backend/{key}.py"
                emit_file("backend", path, res["source"])
                routes: list[tuple[str, str, str]] = []
                try:
                    # a plan build saves its scaffold first, so the result can name a module this step has not seen yet
                    module_id = await self._guard(self.b.save_module(self.row.project_id, key, res.get("module_id") or module_id,
                                                                     res["source"], res.get("tables")))
                    saved = saved_any = True
                    st["module_id"] = module_id
                    emit_file("backend", path, res["source"])
                    routes = self._routes_built(st, res["source"])
                    await self._log(st, f"Saved {key}.py")
                    left = leftovers(res["source"]) if base_source is not None else []
                    problems += [{"code": "not_built", "message": f"{x} is still a scaffold TODO (not built)"} for x in left]
                except ProjectError as e:
                    problems.append({"code": e.code, "message": e.message[:500]})
                problems += [{"code": "route_missing", "message": f"{r} is planned but not built"} for r in res.get("missing") or []]
                if saved:
                    await self._log(st, "Running the module check")
                    if base_source is not None:
                        chk = drop_stub_failures(await self._guard(self.b.check_module_changed(
                            self.row.project_id, module_id, self._touched(base_source, res["source"]))), left)
                        base_source = res["source"]
                    else:
                        chk = await self._guard(self.b.check_module(self.row.project_id, module_id))
                    self._routes_checked(st, routes, chk)
                    problems += chk["problems"]
                    if not chk["ok"] and not chk["problems"]:
                        problems.append({"code": "check_failed", "message": "the module check did not pass"})
            if not problems:
                await self._update(st, status="passed", finished_at=_now(), problems=[],
                                   summary=f"Saved {key}.py; built to plan v{self.row.plan_version} and its check passes")
                return True
            await self._update(st, problems=problems)
            instruction = (f"Build the {key} module from the approved plan v{self.row.plan_version} [plan:{key}]. "
                           + ("The saved module is only patched (patch_module). " if base_source is not None else "")
                           + "The last attempt left these problems; fix them and build what is missing:\n"
                           + "\n".join(f"- {p['message']}" for p in problems[:30]))
        n = len(problems)
        await self._update(st, status="partial" if saved_any else "failed", finished_at=_now(),
                           summary=(f"Saved {key}.py, but {n} problem(s) remain" if saved_any
                                    else f"Could not build {key}: {problems[0]['message'][:160]}" if problems else f"Could not build {key}"))
        return False

    @staticmethod
    def _touched(old: str, new: str) -> set[str] | None:
        """The routes a save from ``old`` to ``new`` can have changed ("METHOD /path"); None = all of them."""
        from .backend_templates import patching
        from .code_module_service import discover_routes

        try:
            return patching.touched_routes(old, new, discover_routes(new))
        except Exception:  # noqa: BLE001 - when in doubt the whole module is checked
            return None

    async def _input_step(self, st: dict[str, Any]) -> None:
        self._check()
        await self._update(st, status="running", started_at=_now(), summary="Checking what the build needs from you")
        inp = await self._guard(self.b.input_needed(self.row.project_id, self.body))
        if not input_missing(inp):
            n = len(inp["secrets"])
            await self._update(st, status="skipped", finished_at=_now(), input=inp,
                               summary=f"Nothing needed from you{f'; all {n} secret(s) are set' if n else ''}")
            return
        self.run.resume.clear()
        self.row.status = "waiting_for_input"
        await self._update(st, status="waiting_for_input", input=inp, summary=inp["message"][:300])
        await self._guard(self.run.resume.wait())
        skip = self.run.skip
        self.row.status = "running"
        await self._update(st, status="running", summary="Re-checking your input")
        inp = await self._guard(self.b.input_needed(self.row.project_id, self.body))
        still = input_missing(inp)
        if not still:
            await self._update(st, status="passed", finished_at=_now(), input=inp, summary="Everything the build needs is in place")
        else:
            lead = "Skipped" if skip else "Continued"
            await self._update(st, status="skipped" if skip else "partial", finished_at=_now(), input=inp,
                               summary=f"{lead}; still missing: {', '.join(still)[:240]}")

    async def _app_step(self, st: dict[str, Any]) -> None:
        self._check()
        key = st["key"].split(":", 1)[1]
        self.targets[st["key"]] = f"app:{key}"
        app = next(a for a in self.body["apps"] if a["key"] == key)
        await self._update(st, status="running", started_at=_now(), summary="Preparing the app")
        app_id = await self._guard(self.b.ensure_app(self.row.project_id, app))
        await self._update(st, app_id=app_id)
        await self._build_app(st, key, app_id, f"Build the {app['name'] or key} app from the approved plan.", APP_ATTEMPTS)

    async def _build_app(self, st: dict[str, Any], key: str, app_id: str, instruction: str, attempts: int) -> bool:
        with step_scope(self.id, st["key"], "ui_builder"):
            return await self._build_app_attempts(st, key, app_id, instruction, attempts)

    async def _build_app_attempts(self, st: dict[str, Any], key: str, app_id: str, instruction: str, attempts: int) -> bool:
        saved_any = False
        problems: list[dict[str, str]] = []
        for attempt in range(1, attempts + 1):
            self._check()
            await self._update(st, status="running", attempt=attempt, app_id=app_id, started_at=st["started_at"] or _now(),
                               summary="Building the UI" if attempt == 1 else "Follow-up turn for the problems found")
            res = await self._guard(self.b.build_app(self.row.project_id, app_id, key, instruction, lambda m: self._log(st, m)))
            problems = list(res.get("errors") or [])
            saved_any = saved_any or bool(res.get("saved"))
            if res.get("saved"):
                await self._log(st, "Running the app check")
                chk = await self._guard(self.b.check_app(self.row.project_id, app_id))
                problems += chk["problems"]
                if not chk["ok"] and not chk["problems"]:
                    problems.append({"code": "check_failed", "message": "the app check did not pass"})
            elif not problems and saved_any:
                # a follow-up turn that changes nothing: the saved app stands, so it is judged by its check, not as "no code"
                await self._log(st, "No new files; running the app check on the saved app")
                chk = await self._guard(self.b.check_app(self.row.project_id, app_id))
                problems += chk["problems"]
                if not chk["ok"] and not chk["problems"]:
                    problems.append({"code": "check_failed", "message": "the app check did not pass"})
            elif not problems:
                problems.append({"code": "no_code", "message": "the app builder wrote no files"})
            if not problems:
                await self._update(st, status="passed", finished_at=_now(), problems=[],
                                   summary=f"Built and saved the app; its check passes")
                return True
            await self._update(st, problems=problems)
            instruction = ("Fix these problems in the app, keeping to the approved plan:\n"
                           + "\n".join(f"- {p['message']}" for p in problems[:30]))
        await self._update(st, status="partial" if saved_any else "failed", finished_at=_now(),
                           summary=(f"Saved the app, but {len(problems)} problem(s) remain" if saved_any
                                    else f"The app was not built: {problems[0]['message'][:160]}"))
        return False

    async def _verify(self) -> None:
        pid = self.row.project_id
        for rnd in range(1, MAX_VERIFY_ROUNDS + 1):
            self._check()
            st = self._add_step(f"verify:{rnd}", "verify", f"Check · Plan, round {rnd}", "check")
            await self._update(st, status="running", attempt=1, started_at=_now(), summary="Comparing the project with the plan")
            rep = await self._guard(self.b.check_plan(pid))
            conf = rep.get("conformance") or {}
            await self._update(st, status="passed" if rep.get("ok") else "partial", finished_at=_now(), problems=plan_problems(rep),
                               summary=("The project matches the plan" if rep.get("ok") else
                                        "Gaps: " + ", ".join(f"{k} {v}" for k, v in conf.items()) if conf else "The plan check lists gaps"))
            if rep.get("ok"):
                break
            missing = rep.get("missing") or {}
            plan_mods = [m["key"] for m in self.body["modules"]]
            fix_mods: dict[str, list[str]] = {}
            for r in missing.get("routes") or []:
                if r.get("module") in plan_mods:
                    fix_mods.setdefault(r["module"], []).append(f"{r['route']} ({r.get('reason') or 'missing'})")
            for m in missing.get("modules") or []:
                if m in plan_mods:
                    fix_mods.setdefault(m, [])
            wanted_apps = [a["key"] for a in self.body["apps"] if self.row.app_key in (None, a["key"])]
            fix_apps: dict[str, list[str]] = {}
            for g in missing.get("screens") or []:
                if g.get("app") in wanted_apps:
                    fix_apps.setdefault(g["app"], []).append(str(g.get("screen")))
            for a in missing.get("apps") or []:
                if a.get("app") in wanted_apps:
                    fix_apps.setdefault(a["app"], [])
            if not fix_mods and not fix_apps:
                break
            for m in [k for k in pp_order(self.body) if k in fix_mods]:
                self._check()
                fst = self._add_step(f"fix:{m}:{rnd}", "fix", f"Fix · Backend · {_title(m)}", "backend")
                self.targets[fst["key"]] = f"module:{m}"
                await self._update(fst, status="running", started_at=_now())
                state = await self._guard(self.b.module_state(pid, m))
                routes = fix_mods[m]
                instr = (f"Build the {m} module from the approved plan v{self.row.plan_version} [plan:{m}]. "
                         + ("The plan check says these planned routes are not built: " + "; ".join(routes) + ". Build them."
                            if routes else "The plan check says this module is not built yet."))
                await self._build_module(fst, m, state["module_id"], instr, 1)
            for a in fix_apps:
                self._check()
                app = next(x for x in self.body["apps"] if x["key"] == a)
                fst = self._add_step(f"fix:app:{a}:{rnd}", "fix", f"Fix · UI · {app['name'] or a}", "ui")
                self.targets[fst["key"]] = f"app:{a}"
                await self._update(fst, status="running", started_at=_now())
                app_id = await self._guard(self.b.ensure_app(pid, {**app, "app_id": self._app_id(a) or app.get("app_id")}))
                gaps = fix_apps[a]
                instr = (f"The plan check says these screens of the {app['name'] or a} app are missing or incomplete: "
                         + ", ".join(gaps) + ". Build them from the approved plan." if gaps
                         else f"Build the {app['name'] or a} app from the approved plan.")
                await self._build_app(fst, a, app_id, instr, 1)
        await self._final()

    def _app_id(self, key: str) -> str | None:
        st = next((x for x in self.row.steps if x["key"] == f"app:{key}"), None)
        return (st or {}).get("app_id")

    async def _final(self) -> None:
        self._check()
        st = self._add_step("verify:final", "verify", "Check · Final", "check")
        await self._update(st, status="running", attempt=1, started_at=_now(), summary="Final plan check and project check")
        project_check: dict[str, Any] | None = None
        plan_rep: dict[str, Any] | None = None
        try:
            out = await self._guard(self.b.check_project(self.row.project_id))
            st_sum = out.get("self_test") or {}
            project_check = {"ok": bool(out.get("ok")),
                             "summary": f"self-test {st_sum.get('passed', 0)} passed, {st_sum.get('failed', 0)} failed"
                                        + (f", {len(out.get('static') or [])} static finding(s)" if out.get("static") else "")}
            plan_rep = out.get("plan")
        except _Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 - the project check is a report line, not a reason to fail the build
            logger.warning("build %s: project check failed", self.id, exc_info=True)
            project_check = {"ok": False, "summary": f"the project check could not run ({type(e).__name__})"}
        if plan_rep is None:
            plan_rep = await self._guard(self.b.check_plan(self.row.project_id))
        plan_check = plan_check_summary(plan_rep)
        timing = self._timing()
        await self._log(st, f"Timing: {timing['total_seconds']} s so far, {timing['model_calls']} backend model call(s); "
                            + ", ".join(f"{x['key']} {x['seconds']} s/{x['model_calls']}" for x in timing["steps"] if x["key"].startswith("module:")))
        await self._update(st, status="passed" if plan_check["ok"] else "partial", finished_at=_now(),
                           problems=plan_problems(plan_rep),
                           summary=("The project matches the plan" if plan_check["ok"] else "The plan check still lists gaps")
                                   + (f"; project check: {project_check['summary']}" if project_check else ""))
        _, partial, failed, _ = self._report_lists()
        await self._finish("done" if plan_check["ok"] and not partial and not failed else "partial",
                           plan_check=plan_check, project_check=project_check)

    def _timing(self) -> dict[str, Any]:
        """Wall time and model calls per step (the backend generator's; the UI builder's are not counted) for timing a build."""
        total = (self.row.finished_at or datetime.now()) - self.row.started_at if self.row.started_at else None
        steps = [{"key": x["key"], "seconds": x.get("duration_s"), "model_calls": int(x.get("model_calls") or 0)}
                 for x in self.row.steps if x.get("started_at")]
        return {"total_seconds": round(total.total_seconds(), 2) if total is not None else None,
                "model_calls": sum(x["model_calls"] for x in steps), "steps": steps,
                **({"phases": self.timing_extra["phases"]} if self.timing_extra else {})}

    def _report_lists(self) -> tuple[list[str], list[str], list[str], list[str]]:
        """``built``, ``partial`` (saved, with problems left), ``failed`` (not built) and the app ids."""
        last: dict[str, str] = {}
        for x in self.row.steps:
            t = self.targets.get(x["key"]) or (x["key"] if x["kind"] in ("module", "app") else None)
            if t and x["status"] != "pending":
                last[t] = x["status"]
        built = [t.split(":", 1)[1] for t, s in last.items() if s in ("passed", "skipped")]
        partial = [t.split(":", 1)[1] for t, s in last.items() if s == "partial"]
        failed = [t.split(":", 1)[1] for t, s in last.items() if s == "failed"]
        app_ids = list(dict.fromkeys(x["app_id"] for x in self.row.steps if x.get("app_id")))
        return built, partial, failed, app_ids

    async def _finish(self, status: str, *, reason: str | None = None, plan_check: dict[str, Any] | None = None,
                      project_check: dict[str, Any] | None = None, autotest: dict[str, Any] | None = None) -> None:
        now = _now()
        for x in self.row.steps:
            if x["status"] in ("running", "waiting_for_input"):
                x.update(status="failed", finished_at=now,
                         summary="Cancelled" if status == "cancelled" else
                         "Interrupted" if reason == "interrupted" else (x.get("summary") or "Stopped by an error"))
            elif x["status"] == "pending":
                x.update(status="skipped", summary="Not run (cancelled)" if status == "cancelled" else "Not run")
        built, partial, failed, app_ids = self._report_lists()
        self.row.status, self.row.reason, self.row.finished_at = status, reason, datetime.now()
        self.row.report = {"built": built, "partial": partial, "failed": failed,
                           "plan_check": plan_check or {"ok": status == "done", "missing": {}}, "app_ids": app_ids,
                           "timing": self._timing(),
                           **({"project_check": project_check} if project_check else {}),
                           **({"autotest": autotest} if autotest else {})}
        await self._save()
        from ..db.build_models import ProjectBuildModel

        async with _factory()() as s:
            row = await s.get(ProjectBuildModel, self.id)
            _publish(self.id, {"type": "done", "build": public_build(row)})


def pp_order(body: dict[str, Any]) -> list[str]:
    from . import project_plan as pp

    return [k for k in pp.build_order(body) if not str(k).startswith("app:")]


# ── public API (routes call these) ───────────────────────────────────────────


async def _active(session: Any, project_id: str) -> Any:
    from sqlalchemy import select

    from ..db.build_models import ProjectBuildModel

    return (await session.execute(select(ProjectBuildModel).where(ProjectBuildModel.project_id == project_id,
                                                                  ProjectBuildModel.status.in_(ACTIVE)))).scalars().first()


def _spawn(build_id: str) -> _Run:
    run = _Run()
    _RUNS[build_id] = run

    async def go() -> None:
        try:
            await Coordinator(build_id, run).execute()
        finally:
            _RUNS.pop(build_id, None)

    run.task = asyncio.create_task(go(), name=f"build:{build_id}")
    return run


async def start_build(session: Any, project_id: str, *, app_key: str | None = None, rebuild: bool = False,
                      actor: str | None = None) -> dict[str, Any]:
    from sqlalchemy.exc import IntegrityError

    from ..db.build_models import ProjectBuildModel
    from ..utils.ids import generate_id
    from . import project_plan as pp

    rec = await pp.load_approved(session, project_id)
    if rec is None:
        raise ProjectError(409, "no_approved_plan", "approve a plan before building it")
    if app_key is not None and app_key not in {a["key"] for a in pp.normalize(rec["body"])["apps"]}:
        raise ProjectError(404, "plan_app_not_found", f"the approved plan has no app '{app_key}'")
    busy = await _active(session, project_id)
    if busy is not None:
        raise ProjectError(409, "build_in_progress", "a build is already running for this project", build_id=busy.id)
    now = datetime.now()
    row = ProjectBuildModel(id=generate_id("build"), project_id=project_id, plan_version=rec["version"], app_key=app_key,
                            rebuild=rebuild, status="queued", steps=initial_steps(rec["body"], app_key), created_by=actor,
                            created_at=now, updated_at=now)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise ProjectError(409, "build_in_progress", "a build is already running for this project") from e
    await session.refresh(row)
    out = public_build(row)
    _spawn(row.id)
    return out


async def get_row(session: Any, project_id: str, build_id: str) -> Any:
    from ..db.build_models import ProjectBuildModel

    row = await session.get(ProjectBuildModel, build_id)
    if row is None or row.project_id != project_id:
        raise ProjectError(404, "build_not_found", "Build not found")
    await session.refresh(row)
    return row


async def list_builds(session: Any, project_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.build_models import ProjectBuildModel

    rows = (await session.execute(select(ProjectBuildModel).where(ProjectBuildModel.project_id == project_id)
                                  .order_by(ProjectBuildModel.created_at.desc()))).scalars().all()
    return [public_build(r, with_log=False) for r in rows]


async def _orphan(session: Any, row: Any, status: str, reason: str | None) -> None:
    """An active row with no task in this process (its process died): end it in place."""
    now = datetime.now().isoformat()
    steps = []
    for x in row.steps or []:
        x = dict(x)
        if x.get("status") in ("running", "waiting_for_input"):
            x.update(status="failed", finished_at=now, summary="Cancelled" if status == "cancelled" else "Interrupted by an engine restart")
        elif x.get("status") == "pending":
            x.update(status="skipped", summary="Not run")
        steps.append(x)
    row.steps, row.status, row.reason, row.finished_at, row.updated_at = steps, status, reason, datetime.now(), datetime.now()
    session.add(row)
    await session.commit()


async def cancel_build(session: Any, project_id: str, build_id: str) -> dict[str, Any]:
    row = await get_row(session, project_id, build_id)
    if row.status in TERMINAL:
        return public_build(row)
    run = _RUNS.get(build_id)
    if run is None:
        await _orphan(session, row, "cancelled", None)
    else:
        run.cancel.set()
        if run.task is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(run.task), CANCEL_WAIT_S)
    return public_build(await get_row(session, project_id, build_id))


async def continue_build(session: Any, project_id: str, build_id: str, *, skip: bool = False) -> dict[str, Any]:
    row = await get_row(session, project_id, build_id)
    run = _RUNS.get(build_id)
    if row.status != "waiting_for_input" or run is None:
        if row.status == "waiting_for_input":
            await _orphan(session, row, "failed", "interrupted")
        raise ProjectError(409, "build_not_waiting", "this build is not waiting for your input", build_status=row.status)
    run.skip = skip
    run.resume.set()
    for _ in range(40):          # until the coordinator has picked it up
        await asyncio.sleep(0.05)
        row = await get_row(session, project_id, build_id)
        if row.status != "waiting_for_input":
            break
    return public_build(row)


async def startup_sweep(factory: Any) -> int:
    """Builds left active by a previous process are failed with reason ``interrupted`` (start a new build to resume; finished
    modules are skipped)."""
    from sqlalchemy import select

    from ..db.build_models import ProjectBuildModel

    n = 0
    async with factory() as s:
        rows = (await s.execute(select(ProjectBuildModel).where(ProjectBuildModel.status.in_(ACTIVE)))).scalars().all()
        for row in rows:
            if row.id in _RUNS:
                continue
            await _orphan(s, row, "failed", "interrupted")
            n += 1
    return n


async def shutdown(timeout: float = 5.0) -> None:
    tasks = [r.task for r in _RUNS.values() if r.task is not None and not r.task.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
