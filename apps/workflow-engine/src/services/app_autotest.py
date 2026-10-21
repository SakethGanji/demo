"""Scenario tests for a one-shot app: the model's own ``tests`` (who calls which route with what, and what must come back) run
against the loaded project's routes, in process, each step as one of the preview users.

A scenario is ``{name, steps: [{as, op, input, expect: {status?, error?, fields?}, save?: {var: "path.in.response"}}]}``. Later
steps reference saved values as ``"$var"`` (a whole string keeps the saved value's type); ``$uniq`` is a short token that is the same within one scenario run and differs between runs, for names/codes that must not collide with seed rows. A step is sent the way the Studio
preview sends a request: a preview token plus the ``X-Preview-User*`` headers, so ``req.caller`` is that user.

The runner is split so it can be tested without a database: :func:`run_scenarios` takes ``routes`` (operation name -> method and
path) and ``send`` (one request in, status and body out); :func:`run_project` wires them to a loaded project.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import time
import uuid
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_SCENARIOS = 12
MAX_STEPS = 12
STEP_TIMEOUT = 15.0       # seconds for one request
TOTAL_TIMEOUT = 20.0      # seconds for the whole run
Send = Callable[[str, str, dict[str, Any], dict[str, str], Any], Awaitable[tuple[int, Any]]]

_VAR = re.compile(r"\$([A-Za-z_]\w*)")
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def enabled() -> bool:
    """WORKFLOW_ONESHOT_AUTOTEST: on by default, ``0`` / ``false`` / ``off`` turns the scenario tests off."""
    return os.environ.get("WORKFLOW_ONESHOT_AUTOTEST", "1").strip().lower() not in ("0", "false", "off", "no")


# ── the tests, as the model wrote them ───────────────────────────────────────


def normalize_tests(raw: Any) -> list[dict[str, Any]]:
    """The well-formed scenarios of ``raw`` (anything else is dropped): ``[{name, steps:[{as, op, input, expect, save}]}]``."""
    out: list[dict[str, Any]] = []
    for sc in raw if isinstance(raw, list) else []:
        if not isinstance(sc, dict) or not str(sc.get("name") or "").strip() or not isinstance(sc.get("steps"), list):
            continue
        steps = []
        for st in sc["steps"][:MAX_STEPS]:
            if not isinstance(st, dict) or not str(st.get("op") or "").strip():
                continue
            exp = st.get("expect") if isinstance(st.get("expect"), dict) else {}
            save = st.get("save") if isinstance(st.get("save"), dict) else {}
            steps.append({"as": str(st.get("as") or "").strip(), "op": str(st["op"]).strip(),
                          "input": st.get("input") if isinstance(st.get("input"), dict) else {},
                          "expect": {k: exp[k] for k in ("status", "error", "fields") if k in exp},
                          "save": {str(k): str(v) for k, v in save.items()}})
        if steps:
            out.append({"name": str(sc["name"]).strip()[:120], "steps": steps})
    return out[:MAX_SCENARIOS]


def merge_tests(current: list[dict[str, Any]], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``current`` with the scenarios of ``changes`` applied by name (a changed one replaces, a new one is appended)."""
    out = list(current)
    by = {t["name"]: i for i, t in enumerate(out)}
    for t in changes:
        if t["name"] in by:
            out[by[t["name"]]] = t
        else:
            by[t["name"]] = len(out)
            out.append(t)
    return out


# ── one step ─────────────────────────────────────────────────────────────────


class StepFailure(Exception):
    """A step that did not do what the scenario expected (the message is the one-line reason)."""


def _walk(value: Any, path: str) -> Any:
    """``value`` at a dotted path (``rows.0.id``); a missing key raises :class:`KeyError`."""
    cur = value
    for part in [p for p in path.split(".") if p != ""]:
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise KeyError(part) from None
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


def _subst(value: Any, saved: dict[str, Any]) -> Any:
    """``value`` with ``$var`` references replaced by saved values (a string that is exactly ``$var`` keeps the value's type)."""
    if isinstance(value, str):
        whole = _VAR.fullmatch(value)
        if whole:
            if whole.group(1) not in saved:
                raise StepFailure(f"${whole.group(1)} was never saved by an earlier step")
            return saved[whole.group(1)]

        def one(m: re.Match) -> str:
            if m.group(1) not in saved:
                raise StepFailure(f"${m.group(1)} was never saved by an earlier step")
            return str(saved[m.group(1)])

        return _VAR.sub(one, value)
    if isinstance(value, dict):
        return {k: _subst(v, saved) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, saved) for v in value]
    return value


def _status_ok(want: Any, got: int) -> bool:
    if isinstance(want, bool):
        return False
    if isinstance(want, int):
        return got == want
    m = re.fullmatch(r"\s*([1-5])xx\s*", str(want), re.I)
    if m:
        return got // 100 == int(m.group(1))
    return str(want).strip() == str(got)


def _same(want: Any, got: Any) -> bool:
    if isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(want, bool) and not isinstance(got, bool):
        return float(want) == float(got)
    return want == got


def _brief(body: Any, cap: int = 200) -> str:
    import json

    try:
        text = json.dumps(body, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(body)
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _request(route: dict[str, str], data: dict[str, Any]) -> tuple[str, str, dict[str, Any], Any]:
    """``(method, concrete path, query, body)``: input keys that name a path placeholder fill it; the rest is the query string
    (GET / DELETE) or the JSON body."""
    method, path = route["method"].upper(), route["path"]
    rest = dict(data)

    def fill(m: re.Match) -> str:
        key = m.group(1)
        if key not in rest:
            raise StepFailure(f"input is missing the path value '{key}' for {method} {path}")
        return str(rest.pop(key))

    concrete = _PLACEHOLDER.sub(fill, path)
    if method in ("GET", "DELETE", "HEAD"):
        return method, concrete, {k: v for k, v in rest.items()}, None
    return method, concrete, {}, rest


def preview_headers(user: dict[str, Any], token: str | None = None) -> dict[str, str]:
    """The headers the Studio preview sends for "view as" ``user``."""
    h = {"x-preview-user": user["id"], "x-preview-user-name": user.get("name") or user["id"],
         "x-preview-user-roles": ",".join(user.get("roles") or [])}
    if token:
        h["authorization"] = f"Bearer {token}"
    return h


async def _step(i: int, step: dict[str, Any], routes: dict[str, dict[str, str]], send: Send, users: dict[str, dict[str, Any]],
                saved: dict[str, Any], token: str | None, timeout: float) -> None:
    label = f"step {i + 1} ({step['as'] or '?'} {step['op']})"
    user = users.get(step["as"])
    if user is None:
        raise StepFailure(f"{label}: unknown user '{step['as']}' (use one of {', '.join(sorted(users))})")
    route = routes.get(step["op"])
    if route is None:
        raise StepFailure(f"{label}: the project has no route named '{step['op']}'")
    try:
        method, path, query, body = _request(route, _subst(step["input"], saved))
        expect = _subst(step["expect"], saved)
    except StepFailure as e:
        raise StepFailure(f"{label}: {e}") from None
    try:
        status, resp = await asyncio.wait_for(send(method, path, query, preview_headers(user, token), body), timeout=max(timeout, 0.05))
    except asyncio.TimeoutError:
        raise StepFailure(f"{label}: no answer within {timeout:g} s ({method} {path})") from None
    except Exception as e:  # noqa: BLE001 - a crashing call is the finding
        raise StepFailure(f"{label}: the call crashed: {type(e).__name__}: {str(e)[:150]}") from None
    code = resp.get("error") if isinstance(resp, dict) else None
    want = expect.get("status")
    if want is None and expect.get("error") is None:
        want = "2xx"
    if want is not None and not _status_ok(want, status):
        raise StepFailure(f"{label}: expected status {want}, got {status} {_brief(resp)}")
    if expect.get("error") is not None and code != expect["error"]:
        raise StepFailure(f"{label}: expected error '{expect['error']}', got {('error ' + repr(code)) if code else 'no error'} (status {status}) {_brief(resp)}")
    for key, w in (expect.get("fields") or {}).items() if isinstance(expect.get("fields"), dict) else []:
        try:
            g = _walk(resp, key)
        except KeyError:
            raise StepFailure(f"{label}: expected {key} = {_brief(w, 60)}, but the response has no '{key}' ({_brief(resp)})") from None
        if not _same(w, g):
            raise StepFailure(f"{label}: expected {key} = {_brief(w, 60)}, got {_brief(g, 60)}")
    for var, path_in in step["save"].items():
        try:
            saved[var] = _walk(resp, path_in)
        except KeyError:
            raise StepFailure(f"{label}: cannot save ${var}: the response has no '{path_in}' ({_brief(resp)})") from None


# ── a whole run ──────────────────────────────────────────────────────────────


async def run_scenarios(tests: list[dict[str, Any]], routes: dict[str, dict[str, str]], send: Send, *, token: str | None = None,
                        step_timeout: float = STEP_TIMEOUT, total_timeout: float = TOTAL_TIMEOUT) -> dict[str, Any]:
    """Run ``tests`` in order. ``routes``: operation name -> ``{method, path}``. Returns ``{total, passed, results:[{name,
    passed, reason}]}``; a scenario stops at its first failing step, and one that the time budget leaves no room for fails with
    that reason (never silently skipped)."""
    from .oneshot_builder import PREVIEW_USERS

    users = {u["id"]: u for u in PREVIEW_USERS}
    deadline = time.monotonic() + total_timeout
    results: list[dict[str, Any]] = []
    for sc in normalize_tests(tests):
        saved: dict[str, Any] = {"uniq": uuid.uuid4().hex[:6]}  # `$uniq`: one short token per scenario run
        reason = ""
        for i, step in enumerate(sc["steps"]):
            left = deadline - time.monotonic()
            if left <= 0:
                reason = f"not run: the {total_timeout:g} s time budget for all scenarios was used up"
                break
            try:
                await _step(i, step, routes, send, users, saved, token, min(step_timeout, left))
            except StepFailure as e:
                reason = str(e)
                break
        results.append({"name": sc["name"], "passed": not reason, "reason": reason or "ok"})
    return {"total": len(results), "passed": sum(1 for r in results if r["passed"]), "results": results}


async def run_project(project_id: str, app_id: str | None, tests: list[dict[str, Any]], *, step_timeout: float = STEP_TIMEOUT,
                      total_timeout: float = TOTAL_TIMEOUT) -> dict[str, Any]:
    """Run ``tests`` against the loaded project's routes in its draft environment (the way the builder's route smoke does), as the
    preview users. ``skipped`` carries the reason when nothing could run."""
    from ..core.public_tokens import issue_token
    from ..db.models import ProjectModel
    from ..sandbox.backend import availability
    from . import build_coordinator as bc
    from .api_route_service import ApiRouteService
    from .route_dispatcher import RouteDispatcher, RouteRequest

    tests = normalize_tests(tests)
    if not tests:
        return {"total": 0, "passed": 0, "results": [], "skipped": "the bundle has no tests"}
    if not availability()[0]:
        return {"total": 0, "passed": 0, "results": [], "skipped": "the sandbox is not available"}
    if not app_id:
        return {"total": 0, "passed": 0, "results": [], "skipped": "the project has no app to act as"}
    async with bc._factory()() as s:
        project = await s.get(ProjectModel, project_id)
        routes = {r.name: {"method": r.method, "path": r.path} for r in await ApiRouteService(s).rows(project_id)}
        env = project.default_environment
    token = issue_token(app_id, 0, ttl_seconds=600, env=env, preview=True)

    async def send(method: str, path: str, query: dict[str, Any], headers: dict[str, str], body: Any) -> tuple[int, Any]:
        async with bc._factory()() as s:
            proj = await s.get(ProjectModel, project_id)
            res = await RouteDispatcher(s).handle(proj, RouteRequest(method=method, path=path, query=query, headers=headers, body=body))
        return res.status, res.body

    snap = await _snapshot(bc._factory(), project_id)
    try:
        return await run_scenarios(tests, routes, send, token=token, step_timeout=step_timeout, total_timeout=total_timeout)
    finally:
        try:
            await _restore(bc._factory(), project_id, snap)
        except Exception:
            logger.warning("could not roll back scenario rows for %s", project_id, exc_info=True)


async def _project_rows(s: Any, project_id: str) -> list[Any]:
    from sqlmodel import select

    from ..db.models import DataTableModel, DataTableRowModel

    q = select(DataTableRowModel).join(DataTableModel, DataTableModel.id == DataTableRowModel.table_id) \
        .where(DataTableModel.project_id == project_id)
    return list((await s.execute(q)).scalars().all())


async def _snapshot(factory: Any, project_id: str) -> dict[int, tuple[str, dict[str, Any], Any, Any]]:
    """The project's data rows before the scenarios run, so :func:`_restore` can put them back: scenarios act on the app's
    real draft data, and what they create or change must not show up in the preview afterwards."""
    async with factory() as s:
        return {r.id: (r.table_id, copy.deepcopy(r.data), r.created_at, r.updated_at) for r in await _project_rows(s, project_id)}


async def _restore(factory: Any, project_id: str, snap: dict[int, tuple[str, dict[str, Any], Any, Any]]) -> None:
    from sqlmodel import select

    from ..db.models import DataTableModel, DataTableRowModel

    async with factory() as s:
        live = {r.id: r for r in await _project_rows(s, project_id)}
        for rid, r in live.items():
            if rid not in snap:
                await s.delete(r)
            elif r.data != snap[rid][1]:
                r.data, r.updated_at = snap[rid][1], snap[rid][3]
        tables = set((await s.execute(select(DataTableModel.id).where(DataTableModel.project_id == project_id))).scalars().all())
        for rid, (tid, data, created, updated) in snap.items():
            if rid not in live and tid in tables:
                s.add(DataTableRowModel(id=rid, table_id=tid, data=data, created_at=created, updated_at=updated))
        await s.commit()


def failure_errors(report: dict[str, Any]) -> list[dict[str, str]]:
    """The failed scenarios as build errors (``{where, code, message}``) written for the fix pass: what was run and what came
    back, and that either the code or the test may be the wrong one."""
    return [{"where": f"scenario:{r['name']}", "code": "scenario_failed",
             "message": (f"Scenario '{r['name']}' failed at {r['reason']}. Fix the code so the scenario passes; if the TEST is wrong "
                         "(it contradicts the plan, or expects something the app should not do), return a corrected scenario of the "
                         "same name in `tests` and say in `message` that you changed the test and why.")[:600]}
            for r in report.get("results") or [] if not r["passed"]]
