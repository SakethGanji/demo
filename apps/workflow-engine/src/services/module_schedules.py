"""Scheduled functions in project code modules (``@schedule``).

A module declares ``@schedule("*/5 * * * *")`` or ``@schedule(every_seconds=30)`` (minimum 15 s) on a function. The
decorators are discovered statically on save (no user code runs) and reconciled into ``module_schedules`` rows: idempotent
on re-save (state survives), removed when the decorator or the module goes. The engine's scheduler runs the due ones through
the same sandbox/broker path a route call uses, as a labelled system principal, never overlapping a run of the same schedule.
"""

from __future__ import annotations

import ast
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CodeModuleModel, ProjectModel
from ..db.schedule_models import ModuleScheduleModel
from ..utils.ids import generate_id
from .project_service import ProjectError

logger = logging.getLogger(__name__)

MIN_EVERY_SECONDS = 15
STALE_RUN_SECONDS = 600  # a run flagged in flight for longer than this crashed with its process; it may run again
ERROR_CAP = 500
PRINCIPAL = "system:schedule"
SCHEDULE_KEYWORDS = {"cron", "every_seconds"}


def _bad(dec: ast.AST, message: str) -> None:
    raise ProjectError(422, "invalid_decorator", message,
                       errors=[{"line": dec.lineno, "column": dec.col_offset + 1, "message": message}])


def _is_schedule(dec: ast.AST) -> bool:
    if not isinstance(dec, ast.Call):
        return False
    f = dec.func
    if isinstance(f, ast.Name):
        return f.id == "schedule"
    return isinstance(f, ast.Attribute) and f.attr == "schedule" and isinstance(f.value, ast.Name) and f.value.id in ("api", "studio")


def discover_schedules(source: str) -> list[dict[str, Any]]:
    """[{function, cron, every_seconds, line}] from the ``@schedule`` decorators. Raises ProjectError(422 invalid_decorator)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # discover_routes reports the syntax error
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not _is_schedule(dec):
                continue
            if len(dec.args) > 1:
                _bad(dec, "@schedule takes one positional argument, the cron expression")
            cron: str | None = None
            every: int | None = None
            if dec.args:
                a = dec.args[0]
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                    _bad(dec, "the cron expression must be a string literal")
                cron = a.value
            for k in dec.keywords:
                if k.arg not in SCHEDULE_KEYWORDS:
                    _bad(dec, f"unknown @schedule argument '{k.arg}' (allowed: cron, every_seconds)")
                if k.arg == "cron":
                    if cron is not None or not (isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)):
                        _bad(dec, "cron must be given once, as a string literal")
                    cron = k.value.value
                else:
                    v = k.value
                    if not (isinstance(v, ast.Constant) and isinstance(v.value, int) and not isinstance(v.value, bool)):
                        _bad(dec, "every_seconds must be an integer literal")
                    every = v.value
            if (cron is None) == (every is None):
                _bad(dec, "give @schedule exactly one of a cron expression or every_seconds")
            if every is not None and every < MIN_EVERY_SECONDS:
                _bad(dec, f"every_seconds must be at least {MIN_EVERY_SECONDS}")
            if cron is not None:
                cron = " ".join(cron.split())
                if len(cron.split()) != 5 or not croniter.is_valid(cron):
                    _bad(dec, f"'{cron}' is not a valid 5-field cron expression")
            if node.name in seen:
                _bad(dec, f"function '{node.name}' already has a @schedule (line {seen[node.name]})")
            seen[node.name] = dec.lineno
            out.append({"function": node.name, "cron": cron, "every_seconds": every, "line": dec.lineno})
    return out


def next_after(cron: str | None, every_seconds: int | None, base: datetime) -> datetime:
    if every_seconds:
        return base + timedelta(seconds=every_seconds)
    return croniter(cron, base).get_next(datetime)


async def reconcile(session: AsyncSession, project_id: str, module_id: str, source: str) -> dict[str, list[str]]:
    """Make the module's rows match its decorators. Idempotent: an unchanged schedule keeps its state; a changed one
    gets a new next_run_at; a removed one is deleted. Commits."""
    decls = discover_schedules(source)
    rows = (await session.execute(select(ModuleScheduleModel).where(ModuleScheduleModel.module_id == module_id))).scalars().all()
    by_fn = {r.function: r for r in rows}
    now = datetime.now()
    out: dict[str, list[str]] = {"created": [], "updated": [], "removed": []}
    for d in decls:
        row = by_fn.pop(d["function"], None)
        if row is None:
            session.add(ModuleScheduleModel(
                id=generate_id("sch"), project_id=project_id, module_id=module_id, function=d["function"], cron=d["cron"],
                every_seconds=d["every_seconds"], line=d["line"], next_run_at=next_after(d["cron"], d["every_seconds"], now),
                created_at=now, updated_at=now))
            out["created"].append(d["function"])
        else:
            changed = (row.cron, row.every_seconds) != (d["cron"], d["every_seconds"])
            if changed or row.line != d["line"]:
                row.cron, row.every_seconds, row.line, row.updated_at = d["cron"], d["every_seconds"], d["line"], now
            if changed:
                row.next_run_at = next_after(d["cron"], d["every_seconds"], now)
                out["updated"].append(d["function"])
    for row in by_fn.values():
        await session.delete(row)
        out["removed"].append(row.function)
    await session.commit()
    return out


def to_dict(s: ModuleScheduleModel, module_name: str | None = None) -> dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": s.id, "project_id": s.project_id, "module_id": s.module_id, "module": module_name, "function": s.function,
            "cron": s.cron, "every_seconds": s.every_seconds, "enabled": s.enabled, "next_run_at": iso(s.next_run_at),
            "running": _in_flight(s), "last_run_at": iso(s.last_run_at), "last_status": s.last_status,
            "last_error": s.last_error, "duration_ms": s.duration_ms, "last_execution_id": s.last_execution_id,
            "line": s.line}


def _in_flight(s: ModuleScheduleModel, now: datetime | None = None) -> bool:
    return bool(s.running_since and (now or datetime.now()) - s.running_since < timedelta(seconds=STALE_RUN_SECONDS))


async def claim(session: AsyncSession, schedule_id: str) -> bool:
    """Atomically mark the schedule as running. False when a (non-stale) run is already in flight: the no-overlap rule."""
    now = datetime.now()
    res = await session.execute(
        update(ModuleScheduleModel).where(
            ModuleScheduleModel.id == schedule_id,
            (ModuleScheduleModel.running_since.is_(None)) |
            (ModuleScheduleModel.running_since < now - timedelta(seconds=STALE_RUN_SECONDS)))
        .values(running_since=now))
    await session.commit()
    return res.rowcount == 1


async def execute(session_factory, schedule_id: str, trigger: str = "schedule") -> dict[str, Any]:
    """Run one claimed schedule through the route execution path and record the outcome. Never raises."""
    started = time.monotonic()
    status, error, exec_id = "failed", None, None
    try:
        async with session_factory() as session:
            s = await session.get(ModuleScheduleModel, schedule_id)
            if s is None:
                return {"status": "failed", "error": "schedule not found"}
            m = await session.get(CodeModuleModel, s.module_id)
            project = await session.get(ProjectModel, s.project_id)
            if m is None or project is None:
                raise RuntimeError("module or project is gone")
            from .python_handler import run_python_route
            from .route_dispatcher import RouteDispatcher

            env = project.live_environment
            _, release = await RouteDispatcher(session).route_set(project, env)
            path = f"/__schedule__/{m.name}/{s.function}"
            spec = {"id": s.id, "method": "SCHEDULE", "path": path, "timeout_s": 60}
            handler = {"type": "python", "module_id": s.module_id, "function": s.function}
            req = {"method": "SCHEDULE", "path": path, "route": path, "params": {}, "query": {}, "body": None,
                   "headers": {}, "caller": {"kind": "schedule", "id": PRINCIPAL, "name": PRINCIPAL, "trigger": trigger}}
            trig = {"type": "schedule", "principal": PRINCIPAL, "schedule_id": s.id, "module": m.name,
                    "function": s.function, "cron": s.cron, "every_seconds": s.every_seconds, "run": trigger}
            res = await run_python_route(session, project, spec, handler, {"req": req}, env, trig, release)
            exec_id = res.execution_id
            if res.status < 400:
                status = "success"
            else:
                body = res.body if isinstance(res.body, dict) else {}
                error = str(body.get("message") or body.get("error") or f"HTTP {res.status}")
    except Exception as e:  # noqa: BLE001 - a schedule can never crash the scheduler
        logger.warning("scheduled run %s failed", schedule_id, exc_info=True)
        error = f"{type(e).__name__}: {e}"
    duration = int((time.monotonic() - started) * 1000)
    try:
        async with session_factory() as session:
            await session.execute(update(ModuleScheduleModel).where(ModuleScheduleModel.id == schedule_id).values(
                running_since=None, last_run_at=datetime.now(), last_status=status,
                last_error=(error[:ERROR_CAP] if error else None), duration_ms=duration, last_execution_id=exec_id))
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not record the result of scheduled run %s", schedule_id)
    return {"status": status, "error": error[:ERROR_CAP] if error else None, "duration_ms": duration, "execution_id": exec_id}


async def claim_due(session_factory) -> list[str]:
    """Due, enabled schedules whose previous run is finished: claimed (running_since set) and moved on. A due schedule whose
    previous run is still going is skipped for this tick (its next_run_at still advances)."""
    from sqlalchemy.exc import SQLAlchemyError

    now = datetime.now()
    claimed: list[str] = []
    try:
        async with session_factory() as session:
            rows = (await session.execute(select(ModuleScheduleModel).where(
                ModuleScheduleModel.enabled == True,  # noqa: E712
                ModuleScheduleModel.next_run_at <= now).with_for_update(skip_locked=True))).scalars().all()
            for s in rows:
                try:
                    s.next_run_at = next_after(s.cron, s.every_seconds, now)
                except Exception:  # noqa: BLE001
                    s.enabled = False
                    continue
                if _in_flight(s, now):
                    continue
                s.running_since = now
                claimed.append(s.id)
            await session.commit()
    except SQLAlchemyError:
        logger.exception("module schedule claim failed")
        return []
    return claimed


async def get_for_project(session: AsyncSession, project_id: str, schedule_id: str) -> ModuleScheduleModel:
    s = await session.get(ModuleScheduleModel, schedule_id)
    if not s or s.project_id != project_id:
        raise ProjectError(404, "schedule_not_found", "Schedule not found")
    return s
