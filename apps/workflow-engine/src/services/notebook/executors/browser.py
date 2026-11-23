"""Browser cell: a recorded recipe run as a jobs-service ``test-run`` with one UI case (BUILD-PLAN §1.2).

The engine reaches the jobs service only through ``broker/jobs.call`` (config, token, caps and error mapping live
there). ``run`` starts the job, polls ``get`` until it finishes, then reads the events and the artifact list and
returns per-step status, the failed step and the artifact names. While the job runs, ``LIVE[cell_id]`` holds its id so
the frame / control routes can reach it; afterwards they use the result's ``job_id``.

Guard rail: unless the spec says ``writes: true``, ``policy.forbidden_names`` carries the write-ish verbs below, so a
click on "Pay" or "Delete" is blocked by the browser policy (the case comes back ``blocked``). With ``writes: true`` a
short list (closing or deleting an account) is still sent.

A missing / unreachable / unconfigured jobs service gives the cell error ``browser_unavailable``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from . import CellOutcome

#: Sent as ``policy.forbidden_names`` for read-only recipes (word-boundary, case-insensitive in the jobs service).
READ_ONLY_FORBIDDEN = ["pay", "pay now", "transfer", "send money", "approve", "release", "delete", "remove", "cancel payment",
                       "close account", "submit payment", "confirm payment", "wire funds"]
ALWAYS_FORBIDDEN = ["close account", "delete account"]

POLL_S = 0.4
JOB_DEADLINE_S = 240.0
#: While the run is paused or taken over the deadline stands still, up to this much paused time in all.
MAX_PAUSED_S = 1800.0
UNAVAILABLE_CODES = {"jobs_unavailable", "jobs_not_configured", "jobs_timeout", "jobs_auth_failed"}
TERMINAL = {"passed", "failed", "error", "cancelled"}

#: cell_id -> job_id of the browser job running right now (single engine process).
LIVE: dict[str, str] = {}


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if obj is None:
            return default
        obj = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
    return default if obj is None else obj


def _spec_of(cell: Any, inputs: Any) -> dict[str, Any]:
    if isinstance(inputs, dict):
        if isinstance(inputs.get("spec"), dict):
            return inputs["spec"]
        if "base_url" in inputs or "steps" in inputs:
            return inputs
    return dict(_get(cell, "spec") or {})


def case_id_of(cell: Any) -> str:
    name = str(_get(cell, "name") or _get(cell, "id") or "cell")
    return re.sub(r"[^\w.-]", "_", name)[:64] or "cell"


#: ``{{$cred.username}}`` in a step (left alone by the notebook resolver, which skips ``$`` expressions) becomes the
#: jobs-service credential placeholder ``{{username}}``; the secret value itself never enters the spec.
_CRED_RE = re.compile(r"\{\{\s*\$cred(?:s|entials)?\.(\w+)\s*\}\}")


def _cred_placeholders(v: Any) -> Any:
    if isinstance(v, str):
        return _CRED_RE.sub(lambda m: "{{" + m.group(1) + "}}", v)
    if isinstance(v, list):
        return [_cred_placeholders(x) for x in v]
    if isinstance(v, dict):
        return {k: _cred_placeholders(x) for k, x in v.items()}
    return v


#: Credential fields that are not secret: outside a ``fill``/``otp`` value (expectText, a target name, a URL) their
#: value is written into the step. Every other field (password, a TOTP seed, a token) only ever goes to the jobs
#: service as a ``{{name}}`` placeholder, which it fills in typed values only and masks everywhere else.
PUBLIC_CRED_FIELDS = frozenset({"username", "user", "user_name", "login", "email"})
#: Steps whose ``value`` the jobs service fills from the credentials itself.
_TYPED_ACTIONS = frozenset({"fill", "otp"})


def cred_misuse(steps: Any) -> list[str]:
    """``["step 3 (expectText): {{$cred.password}} ..."]`` for each secret credential field used outside a fill/otp
    value. Never includes a value."""
    out: list[str] = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        typed = s.get("action") in _TYPED_ACTIONS
        for k, v in s.items():
            if typed and k == "value":
                continue
            for f in _CRED_RE.findall(json.dumps(v, default=str)):
                if f not in PUBLIC_CRED_FIELDS:
                    out.append(f"step {i + 1} ({s.get('action')}): {{{{$cred.{f}}}}} is secret and may only be the "
                               "value of a fill step")
    return out


def _cred_expand(v: Any, creds: dict[str, str]) -> Any:
    """Outside a typed value: ``{{$cred.username}}`` becomes the username itself (public fields only; anything else
    is left as the jobs-service placeholder)."""
    if isinstance(v, str):
        return _CRED_RE.sub(lambda m: creds[m.group(1)] if m.group(1) in PUBLIC_CRED_FIELDS and m.group(1) in creds
                            else "{{" + m.group(1) + "}}", v)
    if isinstance(v, list):
        return [_cred_expand(x, creds) for x in v]
    if isinstance(v, dict):
        return {k: _cred_expand(x, creds) for k, x in v.items()}
    return v


def _step_creds(s: dict[str, Any], creds: dict[str, str] | None) -> dict[str, Any]:
    typed = s.get("action") in _TYPED_ACTIONS
    return {k: (_cred_placeholders(x) if typed and k == "value" else _cred_expand(x, creds or {}))
            for k, x in s.items()}


def build_job(cell: Any, spec: dict[str, Any], credentials: dict[str, str] | None = None,
              label: str | None = None) -> dict[str, Any]:
    """The jobs-service ``POST /jobs`` body for one browser cell."""
    steps = []
    for s in spec.get("steps") or []:
        s = _step_creds(dict(s), credentials)
        if s.get("action") == "done":
            continue
        steps.append(s)
    target: dict[str, Any] = {"base_url": spec["base_url"]}
    if spec.get("allowed_hosts"):
        target["allowed_hosts"] = list(spec["allowed_hosts"])
    names = ALWAYS_FORBIDDEN if spec.get("writes") else READ_ONLY_FORBIDDEN
    body: dict[str, Any] = {
        "type": "test-run",
        "label": (label or str(_get(cell, "title") or "notebook browser cell"))[:120],
        "target": target,
        "policy": {"forbidden_names": list(names)},
        "cases": [{"id": case_id_of(cell), "kind": "ui", "title": _get(cell, "title"), "steps": steps}],
    }
    if credentials:
        body["credentials"] = credentials
    return body


async def _jobs(params: dict[str, Any]) -> dict[str, Any]:
    from ....broker import jobs

    out = await jobs.call(params)
    return out["items"][0]


def _policy_code(e: Exception) -> str | None:
    return getattr(e, "code", None)


async def _credentials(ctx: Any, slot: str | None) -> dict[str, str] | None:
    """The slot's secret: a project/team variable named ``slot`` in the notebook's environment. A JSON object value
    is used as the credentials map; any other value becomes ``{"password": value}``. Missing gives None."""
    if not slot:
        return None
    from sqlalchemy import select

    from ....db.models import ProjectModel
    from ....db.session import async_session_factory
    from ...variable_loader import load_run_variables

    project_id = _get(ctx, "notebook", "project_id") or _get(ctx, "project_id")
    if not project_id:
        return None
    async with async_session_factory() as s:
        p = (await s.execute(select(ProjectModel).where(ProjectModel.id == project_id))).scalars().first()
    if p is None:
        return None
    env = _get(ctx, "environment") or _get(ctx, "notebook", "environment") or p.default_environment
    values = await load_run_variables(environment=env, team_id=p.team_id, project_id=p.id)
    raw = values.get(slot)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        return {str(k): str(v) for k, v in parsed.items() if re.match(r"^\w+$", str(k))}
    return {"password": str(raw)}


def _summarise(spec: dict[str, Any], job: dict[str, Any], events: list[dict[str, Any]],
               artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [s for s in spec.get("steps") or [] if s.get("action") != "done"]
    status_by_index: dict[int, dict[str, Any]] = {}
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        idx = data.get("index")
        if not isinstance(idx, int):
            continue
        t = ev.get("type")
        if t == "step_start":
            status_by_index.setdefault(idx, {"status": "running"})
        elif t == "step_pass":
            status_by_index[idx] = {"status": "passed", "ms": data.get("ms")}
        elif t == "step_fail":
            status_by_index[idx] = {"status": "failed", "ms": data.get("ms"), "error": data.get("error") or ev.get("text")}
        elif t == "blocked":
            status_by_index[idx] = {"status": "blocked", "error": ev.get("text"), "rule": data.get("rule")}
    case = (job.get("results") or [{}])[0] if job.get("results") else {}
    out_steps = []
    for i, s in enumerate(steps):
        st = status_by_index.get(i, {"status": "not_run"})
        if st["status"] == "running":
            st = {"status": "failed" if case.get("status") in ("failed", "error") else "not_run"}
        out_steps.append({"index": i, "action": s.get("action"), "group": s.get("group"), **st})
    passed = sum(1 for s in out_steps if s["status"] == "passed")
    failed = sum(1 for s in out_steps if s["status"] in ("failed", "blocked"))
    case_status = case.get("status") or job.get("status")
    fs = case.get("failed_step")
    return {
        "status": case_status,
        "job_status": job.get("status"),
        "steps": out_steps,
        "step_count": len(out_steps),
        "passed": passed,
        "failed": failed,
        "failed_step": fs if isinstance(fs, dict) else None,
        "error": case.get("error") or job.get("error"),
        "blocked": [ev.get("text") for ev in events if ev.get("type") == "blocked"][:10],
        "artifacts": [{"name": a.get("name"), "bytes": a.get("bytes"), "content_type": a.get("content_type")}
                      for a in artifacts if isinstance(a, dict)],
        "ms": case.get("ms"),
        # what the runner did not keep, e.g. "video not kept: take over used" (only present when there is one)
        **({"notes": [str(n) for n in case["notes"]]} if isinstance(case.get("notes"), list) and case["notes"] else {}),
    }


async def run(ctx: Any, cell: Any, inputs: Any = None) -> CellOutcome:
    from ....broker.policy import PolicyError

    spec = _spec_of(cell, inputs)
    cell_id = str(_get(cell, "id") or "")
    t0 = time.monotonic()
    if not spec.get("base_url") or not spec.get("steps"):
        return CellOutcome.fail("invalid_spec", "a browser cell needs base_url and at least one step")
    misuse = cred_misuse(spec.get("steps"))
    if misuse:
        return CellOutcome.fail("invalid_spec", "; ".join(misuse[:3]))
    try:
        creds = await _credentials(ctx, spec.get("credential_slot"))
    except Exception as e:  # noqa: BLE001 - a broken secret store must not crash the run loop
        return CellOutcome.fail("internal", f"could not load the credential slot: {type(e).__name__}")
    if spec.get("credential_slot") and creds is None:
        return CellOutcome.fail("unbound_credential", f"no secret named {spec['credential_slot']!r} is set for this "
                                                      "environment; add it under Secrets", slot=spec["credential_slot"])
    nb_id = _get(ctx, "notebook_id") or _get(ctx, "notebook", "id")
    body = build_job(cell, spec, creds, label=f"notebook {nb_id or ''} {_get(cell, 'name') or ''}".strip())
    try:
        started = await _jobs({"operation": "start", "body": body})
    except PolicyError as e:
        if _policy_code(e) in UNAVAILABLE_CODES:
            return CellOutcome.fail("browser_unavailable", f"the browser runner is unavailable: {e}")
        return CellOutcome.fail("browser_failed", f"the browser runner refused the recipe: {e}")
    job_id = started.get("job_id")
    if not job_id:
        return CellOutcome.fail("browser_failed", "the jobs service returned no job id")
    if cell_id:
        LIVE[cell_id] = job_id
    job: dict[str, Any] = {}
    try:
        deadline = time.monotonic() + JOB_DEADLINE_S
        paused_s = 0.0
        while True:
            try:
                job = await _jobs({"operation": "get", "job_id": job_id})
            except PolicyError as e:
                if _policy_code(e) in UNAVAILABLE_CODES:
                    return CellOutcome(status="error", job_id=job_id, ms=int((time.monotonic() - t0) * 1000),
                                       error={"code": "browser_unavailable", "message": f"lost the browser runner: {e}"})
                raise
            if job.get("status") in TERMINAL:
                break
            if (job.get("progress") or {}).get("paused") and paused_s < MAX_PAUSED_S:
                # paused / taken over: the user has the page, so the recipe's own clock stops
                deadline += POLL_S
                paused_s += POLL_S
            if time.monotonic() > deadline:
                await _cancel(job_id)
                return CellOutcome(status="error", job_id=job_id, ms=int((time.monotonic() - t0) * 1000),
                                   error={"code": "timeout", "message": f"the browser recipe ran past {JOB_DEADLINE_S:.0f} s"})
            await asyncio.sleep(POLL_S)
        events: list[dict[str, Any]] = []
        after = 0
        for _ in range(20):
            page = await _jobs({"operation": "events", "job_id": job_id, "after": after})
            events.extend(page.get("events") or [])
            nxt = page.get("next")
            if not page.get("events") or not isinstance(nxt, int) or nxt <= after:
                break
            after = nxt
        artifacts = (await _jobs({"operation": "artifacts", "job_id": job_id})).get("artifacts") or []
    except asyncio.CancelledError:
        await _cancel(job_id)
        raise
    finally:
        if cell_id and LIVE.get(cell_id) == job_id:
            LIVE.pop(cell_id, None)
    out = _summarise(spec, job, events, artifacts)
    ms = int((time.monotonic() - t0) * 1000)
    if out["status"] == "passed":
        return CellOutcome(status="success", output=out, job_id=job_id, ms=ms)
    if out["status"] == "blocked":
        msg = "a step was blocked by the browser policy: " + (out["blocked"][0] if out["blocked"] else "forbidden action")
        code = "browser_blocked"
    elif out["status"] == "cancelled":
        msg, code = "the browser recipe was stopped", "cancelled"
    else:
        fs = out.get("failed_step") or {}
        msg = f"step {int(fs.get('index', -1)) + 1} failed" if fs else "the browser recipe failed"
        if fs.get("description"):
            msg += f": {fs['description']}"
        if out.get("error"):
            msg += f" ({str(out['error'])[:300]})"
        code = "browser_failed"
    return CellOutcome(status="error", output=out, job_id=job_id, ms=ms, error={"code": code, "message": msg})


async def _cancel(job_id: str) -> None:
    try:
        await _jobs({"operation": "cancel", "job_id": job_id})
    except Exception:  # noqa: BLE001 - best effort
        pass


__all__ = ["LIVE", "READ_ONLY_FORBIDDEN", "build_job", "run"]
