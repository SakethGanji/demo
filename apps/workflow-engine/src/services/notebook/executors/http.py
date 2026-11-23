"""http: one request through the engine's HttpRequest node (egress allowlist, API catalog, ``$vars`` secrets), run as
a two-node ad-hoc workflow so it shows up as an execution with the notebook attribution.

- ``expect.status`` / ``expect.json_path_equals`` (``{"$.a.b": value}``) decide pass/fail; without ``expect`` a status
  of 400 or more fails the cell (``http_failed``).
- ``api_ref`` is ``"<spec_id>:<operation_id>"`` (``/`` or ``#`` also accepted).
- ``credential_slot`` names a secret sent as ``Authorization: Bearer <secret>`` (the value never enters the notebook).

API testing (``apitest.py``): ``params`` (query string), ``auth`` (bearer / basic / api_key in a header or the query,
the secret filled in by the engine), ``body_type: form``, sentence ``assertions`` (each result carries the actual
value), ``extract`` (values saved as variables for later cells). The output adds the response ``headers``, ``ms``,
``size``, the ``request`` as sent (secrets as ``{{ $vars.KEY }}``) and ``last`` (the previous run's response, for a
diff). Every secret value of the environment is redacted from what is stored.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .. import apitest
from . import CellOutcome
from ._common import ms_since


def _api_ref(ref: Any) -> dict[str, str] | None:
    if isinstance(ref, dict):
        return ref if ref.get("spec_id") else None
    if not isinstance(ref, str) or not ref:
        return None
    m = re.match(r"^([^:/#]+)[:/#](.+)$", ref)
    return {"spec_id": m.group(1), "operation_id": m.group(2)} if m else {"spec_id": ref, "operation_id": ""}


def definition(spec: dict[str, Any]) -> dict[str, Any]:
    req = apitest.build_request(spec)
    params: dict[str, Any] = {"method": req["method"], "headers": req["headers"], "responseType": "json"}
    ref = _api_ref(spec.get("api_ref"))
    if ref:
        params["api_ref"] = ref
    else:
        params["url"] = req["url"]
    if req["body"] is not None:
        params["body"] = req["body"]
    return {"name": "notebook http", "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "HTTP", "type": "HttpRequest", "parameters": params, "position": {"x": 200, "y": 0}},
    ], "connections": [{"source_node": "Start", "target_node": "HTTP", "source_output": "main", "target_input": "main"}],
        "settings": {}}


_HIDDEN_HEADERS = {"set-cookie", "authorization", "proxy-authorization", "cookie"}
LAST_BODY_MAX = 30_000


def _shown_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in list((headers or {}).items())[:60]:
        out[str(k)] = "(hidden)" if str(k).lower() in _HIDDEN_HEADERS else str(v)[:500]
    return out


async def _last_result(ctx: Any, cell: Any) -> dict[str, Any] | None:
    """The cell's previous response (another run, same row), for the "what changed since last run" diff."""
    from sqlalchemy import select

    from ....db.notebook_models import NotebookCellResultModel as Res

    try:
        async with ctx.session() as s:
            q = select(Res).where(Res.cell_id == cell.id, Res.run_id != ctx.run_id,
                                  Res.status.in_(("success", "error")))
            q = q.where(Res.row_index.is_(None) if ctx.row_index is None else Res.row_index == ctx.row_index)
            prev = (await s.execute(q.order_by(Res.created_at.desc()).limit(1))).scalars().first()
    except Exception:  # noqa: BLE001 - the diff is a nicety; never fail the request over it
        return None
    o = (prev.output if prev is not None else None) or {}
    if not isinstance(o, dict) or o.get("status") is None:
        return None
    body = o.get("body") if o.get("body") is not None else o.get("rows")
    last: dict[str, Any] = {"status": o.get("status"), "ms": o.get("ms"), "size": o.get("size"),
                            "at": prev.created_at.isoformat() if prev.created_at else None, "body": body}
    if len(json.dumps(body, default=str)) > LAST_BODY_MAX:
        last["body"], last["body_truncated"] = None, True
    return last


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ...vars_substitution import redact_deep
    from ..minirun import run_definition
    from ..storage import preview

    t0 = time.perf_counter()
    try:
        defn = definition(inputs)
    except apitest.ApiTestError as e:
        return CellOutcome.fail("invalid_spec", str(e))
    try:
        context = await run_definition(defn, project_id=ctx.project_id, environment=ctx.environment,
                                       metadata={"attribution": ctx.attribution_for(cell.id)}, timeout_s=60.0)
    except TimeoutError:
        return CellOutcome(status="error", error={"code": "timeout", "message": "the request took longer than 60s"},
                           ms=ms_since(t0))
    except Exception as e:  # noqa: BLE001 - a bad definition is a cell error
        return CellOutcome(status="error", error={"code": "http_failed", "message": str(e)[:500]}, ms=ms_since(t0))
    exec_id = context.execution_id
    secrets = set(getattr(context, "nb_secrets", None) or set())
    auth = inputs.get("auth") or {}
    if auth.get("type") == "basic" and auth.get("username"):  # the echoed header is base64("user:secret")
        import base64

        secrets |= {base64.b64encode(f"{auth['username']}:{v}".encode()).decode() for v in list(secrets)}
    if context.errors:
        msg = redact_deep(context.errors[0].error[:500], secrets)
        return CellOutcome(status="error", error={"code": "http_failed", "message": msg},
                           execution_id=exec_id, ms=ms_since(t0))
    items = context.node_states.get("HTTP") or []
    j = (items[0].json if items else {}) or {}
    status, body, headers = j.get("statusCode"), j.get("body"), j.get("headers") or {}
    metrics = (getattr(context, "node_metrics", None) or {}).get("HTTP") or {}
    took = metrics.get("responseTimeMs")
    took = round(float(took), 1) if isinstance(took, (int, float)) else ms_since(t0)
    size = metrics.get("responseSizeBytes")
    req = defn["nodes"][1]["parameters"]
    results, problems = apitest.check_all(inputs, status, headers, body, took)
    saved, xprobs = apitest.extract(inputs.get("extract"), status, headers, body)
    problems += xprobs
    rows = body if isinstance(body, list) and all(isinstance(r, dict) for r in body) else \
        ([body] if isinstance(body, dict) else [])
    extra: dict[str, Any] = {"headers": _shown_headers(headers), "ms": took, "size": size,
                             "request": {"method": req["method"], "url": req.get("url") or None,
                                         "headers": [h["name"] for h in req["headers"]],
                                         "body_type": inputs.get("body_type") or "json"}}
    if results:
        extra["assertions"] = results
    if inputs.get("extract"):
        extra["vars"] = saved
    last = await _last_result(ctx, cell)
    if last is not None:
        extra["last"] = last
    out = preview(rows, None, status=status, body=body if not isinstance(body, list) else None,
                  method=str(inputs.get("method") or "GET").upper(), url=req.get("url") or inputs.get("url"), **extra)
    out["passed"] = not problems
    out = redact_deep(out, secrets)
    rows = redact_deep(rows, secrets)
    if problems:
        return CellOutcome(status="error", output=out, full_rows=rows, execution_id=exec_id, ms=ms_since(t0),
                           error={"code": "http_failed", "message": redact_deep("; ".join(problems), secrets)[:1000],
                                  "status": status})
    return CellOutcome(output=out, full_rows=rows, execution_id=exec_id, ms=ms_since(t0))
