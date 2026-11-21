"""G10 · the Vela agent's tools: a typed registry over the workbench's own HTTP API.

Every tool is one (or a few chained) calls to an existing route, made in-process through httpx's ASGI transport, so the
agent goes through exactly the validation, refusals and audit the UI goes through. No business logic lives here: a
tool only shapes arguments into the route's body and trims the answer into a short digest the model reads.

`kind`: "read" tools run freely; "write" tools change state (train, patch, register, request approval, score a batch,
upload, save, launch a check) and wait for the user's one-click confirmation. Approving a version and promoting it
to Production are not tools at all, and `_guard_path` refuses them even if a route were reached another way: those
stay human (four-eyes).
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

READ, WRITE = "read", "write"
TERMINAL = ("succeeded", "failed", "cancelled")


class ToolError(Exception):
    """A route refused the call (its status and detail) or the arguments were wrong; the agent reads the message."""


@dataclass
class Ctx:
    client: httpx.AsyncClient
    app: Any
    actor: str  # "Vela agent on behalf of <user>"
    deadline: float  # time.monotonic() by which the session's time budget ends
    progress: Callable[[str], None] = lambda _m: None
    stop: Callable[[], bool] = lambda: False

    def left(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, Any]
    required: list[str]
    kind: str
    run: Callable[[Ctx, dict[str, Any]], Awaitable[dict[str, Any]]]
    link: Callable[[dict[str, Any], dict[str, Any]], str | None] = lambda a, r: None
    group: str = "data"
    extra: bool = False  # additionalProperties allowed (free-form bodies)

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": self.params, "required": self.required,
                "additionalProperties": self.extra}

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "kind": self.kind, "group": self.group,
                "http": HTTP.get(self.name), "input_schema": self.schema()}


# ------------------------------------------------------------------------------------------------------------ http

FORBIDDEN = ("/review",)


def _guard_path(method: str, path: str, body: Any) -> None:
    if any(path.endswith(f) for f in FORBIDDEN):
        raise ToolError("Approving or rejecting a version is a human decision (four-eyes); the agent can only request it.")
    if path.endswith("/transition") and isinstance(body, dict) and body.get("stage") == "Production":
        raise ToolError("Promoting to Production is a human decision; the agent can only request approval.")


async def call(ctx: Ctx, method: str, path: str, *, body: Any = None, params: dict[str, Any] | None = None,
               files: Any = None, data: Any = None, timeout: float = 600) -> Any:
    _guard_path(method, path, body)
    try:
        r = await ctx.client.request(method, path, json=body if files is None else None, params=params, files=files,
                                     data=data, timeout=timeout)
    except httpx.HTTPError as e:
        raise ToolError(f"{method} {path} failed: {type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        if not isinstance(detail, str):
            detail = json.dumps(detail)[:400]
        raise ToolError(f"{method} {path} → {r.status_code}: {detail}")
    ct = r.headers.get("content-type", "")
    return r.json() if "json" in ct else {"text": r.text[:4000]}


def _r(x: Any, n: int = 4) -> Any:
    return round(float(x), n) if isinstance(x, (int, float)) and x is not None else x


def trim(x: Any, depth: int = 3, items: int = 8, chars: int = 240) -> Any:
    """A bounded copy of a JSON answer: the generic digest when a tool has no summariser of its own."""
    if isinstance(x, dict):
        if depth <= 0:
            return f"{{…{len(x)} keys}}"
        return {k: trim(v, depth - 1, items, chars) for k, v in list(x.items())[:24]}
    if isinstance(x, list):
        if depth <= 0:
            return f"[…{len(x)} items]"
        out = [trim(v, depth - 1, items, chars) for v in x[:items]]
        return out + ([f"…{len(x) - items} more"] if len(x) > items else [])
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, str) and len(x) > chars:
        return x[:chars] + "…"
    return x


# ------------------------------------------------------------------------------------------------------- summaries

def run_digest(rec: dict[str, Any]) -> dict[str, Any]:
    h = rec.get("holdout") or {}
    metric = (rec.get("eval_metric") or {}).get("name") or next(iter(h.get("scores") or {}), None)
    lb = rec.get("leaderboard") or []
    cfg = rec.get("config") or {}
    sp = (cfg.get("split_spec") or {}).get("strategy") or (rec.get("split") or {}).get("strategy")
    out = {"run_id": rec["id"], "name": rec.get("name"), "status": rec.get("status"), "dataset_id": rec.get("dataset_id"),
           "label": rec.get("label"), "kind": cfg.get("kind") or "tabular", "problem_type": rec.get("problem_type") or cfg.get("problem_type"),
           "parent_run_id": cfg.get("parent_run_id"), "split": sp, "error": (rec.get("error") or None)}
    if rec.get("status") == "succeeded":
        out.update({"metric": metric, "holdout_score": _r((h.get("scores") or {}).get(metric)), "holdout_rows": h.get("n_rows"),
                    "best_model": rec.get("best_model"), "n_models": len(lb),
                    "holdout_scores": {k: _r(v) for k, v in list((h.get("scores") or {}).items())[:6]},
                    "top_models": [{"model": m.get("model"), "score_test": _r(m.get("score_test"))} for m in lb[:4]]})
        thr = (rec.get("result") or {}).get("threshold") or rec.get("threshold")
        if isinstance(thr, dict) and thr.get("value") is not None:
            out["decision_threshold"] = _r(thr["value"])
    return {k: v for k, v in out.items() if v is not None}


def version_digest(v: dict[str, Any]) -> dict[str, Any]:
    ver = (v.get("result") or {}).get("version") or {}
    return {k: x for k, x in {"version_id": v["id"], "model_name": v.get("model_name"), "version": v.get("version"),
                              "status": v.get("status"), "stage": v.get("stage"), "approval": v.get("approval"),
                              "run_id": v.get("run_id"), "created_by": v.get("created_by"),
                              "problem_type": ver.get("problem_type") or v.get("problem_type"),
                              "served_model": ver.get("best_model") or v.get("best_model"),
                              "decision_threshold": _r(ver.get("decision_threshold") or v.get("decision_threshold")),
                              "error": v.get("error")}.items() if x is not None}


# ------------------------------------------------------------------------------------------------------- the tools

async def list_datasets(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", "/api/datasets")
    return {"datasets": [{"id": x["id"], "rows": x["rows"], "cols": x["n_cols"], "target": x["target"], "kind": x["kind"]}
                         for x in d["datasets"]]}


async def profile_dataset(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/datasets/{a['dataset_id']}")
    p = d["profile"]
    return {"dataset_id": d["id"], "rows": d["rows"], "target": d["target"], "task": p.get("task"),
            "imbalance_ratio": _r(p.get("imbalance_ratio"), 3), "classes": [c.get("name") for c in (p.get("classes") or [])][:10],
            "columns": [{"name": c["name"], "type": c["type"]} for c in p["columns"]][:40]}


async def intake(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/datasets/{a['dataset_id']}/intake")
    roles = {}
    for r in d.get("roles") or []:
        if isinstance(r, dict) and r.get("role") not in (None, "feature"):
            roles[r.get("column")] = r.get("role")
    if not roles and isinstance(d.get("columns"), list):
        roles = {c.get("name"): c.get("role") for c in d["columns"] if isinstance(c, dict) and c.get("role") not in (None, "feature")}
    return {"dataset_id": a["dataset_id"], "rows": d.get("rows"), "roles": roles,
            "suggestions": [{"id": s["id"], "title": s["title"], "score": s.get("score"), "reason": s.get("reason"),
                             "prefill": s.get("prefill")} for s in d.get("suggestions", [])[:6]]}


async def find_rows(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    params = {"value": str(a["value"]), "limit": a.get("limit", 3), **({"column": a["column"]} if a.get("column") else {})}
    return await call(ctx, "GET", f"/api/datasets/{a['dataset_id']}/rows", params=params)


async def _features(ctx: Ctx, vid: str, row: dict[str, Any]) -> dict[str, Any]:
    """The row without the version's label (a looked-up dataset row carries its outcome)."""
    v = await call(ctx, "GET", f"/api/registry/versions/{vid}")
    run = await call(ctx, "GET", f"/api/runs/{v['run_id']}")
    return {k: x for k, x in row.items() if k != run.get("label")}


async def upload_dataset(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    files = {"file": (a["filename"], a["csv_text"].encode(), "text/csv")}
    d = await call(ctx, "POST", "/api/datasets", files=files, data={"target": a.get("target") or ""})
    return {"dataset_id": d["id"], "rows": d["rows"], "cols": d["n_cols"], "target": d["target"]}


async def sql_preview(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    body = {k: a[k] for k in ("url", "query", "limit") if a.get(k) is not None}
    d = await call(ctx, "POST", "/api/sql/preview", body=body)
    return trim(d, 3, 5)


async def sql_save(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    body = {k: a[k] for k in ("url", "query", "limit", "name", "target") if a.get(k) is not None}
    d = await call(ctx, "POST", "/api/sql/datasets", body=body)
    ds = d.get("dataset") or d
    return {"dataset_id": ds.get("id"), "rows": ds.get("rows"), "target": ds.get("target"), "lineage": trim(d.get("lineage"), 2)}


async def sql_refresh(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "POST", f"/api/sql/datasets/{a['dataset_id']}/refresh", body={})
    return trim(d, 3, 6)


def _body_from_intake(dataset_id: str, pf: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    """The Build page's pre-fill → POST /api/runs body (G0): target, dropped IDs, split, F7 cost."""
    split = a.get("split") or pf.get("split") or "random"
    body: dict[str, Any] = {"dataset_id": dataset_id, "label": pf["target"], "presets": a.get("presets") or "medium_quality",
                            "time_limit": a.get("time_limit") or 120, "source": "vela-agent", "feature_importance": False}
    drop = list(pf.get("drop") or [])
    if split in ("time", "time-group") and pf.get("time"):
        drop.append(pf["time"])
        body["split_spec"] = {"strategy": split, "time_column": pf["time"], **({"group_column": pf["group"]} if split == "time-group" and pf.get("group") else {})}
    elif split == "group" and pf.get("group"):
        body["split_spec"] = {"strategy": "group", "group_column": pf["group"]}
    body["drop_columns"] = sorted(set(drop))
    if pf.get("metric") == "expected_cost":
        body.update(cost={"c_fp": 1, "c_fn": 10}, calibrate_threshold="auto")
    body["fit_kwargs"] = {"hyperparameters": a.get("hyperparameters") or {"GBM": {"num_boost_round": 200}, "XGB": {"n_estimators": 200}}}
    return body


async def train_from_intake(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    it = await call(ctx, "GET", f"/api/datasets/{a['dataset_id']}/intake")
    sid = a.get("suggestion_id") or "binary"
    sug = next((s for s in it.get("suggestions", []) if s["id"] == sid), None)
    if not sug or "target" not in (sug.get("prefill") or {}):
        raise ToolError(f"No '{sid}' suggestion with a target on {a['dataset_id']}; suggestions: "
                        + ", ".join(s["id"] for s in it.get("suggestions", [])))
    body = _body_from_intake(a["dataset_id"], sug["prefill"], a)
    v = await call(ctx, "POST", "/api/gym/validate", body=body)
    if not v.get("ok"):
        raise ToolError(f"The Gym refused the body: {v.get('errors')}")
    r = await call(ctx, "POST", "/api/runs", body=body)
    return {"run_id": r["id"], "status": r.get("status"), "body": body, "from_suggestion": sid}


async def start_run(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    body = dict(a["body"])
    body.setdefault("source", "vela-agent")
    v = await call(ctx, "POST", "/api/gym/validate", body=body)
    if not v.get("ok"):
        raise ToolError(f"The Gym refused the body: {v.get('errors')}")
    r = await call(ctx, "POST", "/api/runs", body=body)
    return {"run_id": r["id"], "status": r.get("status")}


async def watch_run(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    t0 = time.monotonic()
    wait = min(float(a.get("max_wait_seconds") or 600), ctx.left())
    while True:
        rec = await call(ctx, "GET", f"/api/runs/{a['run_id']}")
        el = time.monotonic() - t0
        if rec["status"] in TERMINAL:
            return {**run_digest(rec), "waited_seconds": round(el, 1)}
        if el >= wait or ctx.stop():
            return {"run_id": rec["id"], "status": rec["status"], "waited_seconds": round(el, 1),
                    "note": "still running; call watch_run again"}
        ctx.progress(f"{rec['status']} · {el:.0f} s")
        await asyncio.sleep(1.5)


async def get_results(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    return run_digest(await call(ctx, "GET", f"/api/runs/{a['run_id']}"))


async def list_runs(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", "/api/runs", params={k: a[k] for k in ("dataset_id",) if a.get(k)})
    return {"runs": [{"run_id": r["id"], "name": r.get("name"), "status": r.get("status"), "dataset_id": r.get("dataset_id"),
                      "label": r.get("label")} for r in d.get("runs", [])[:15]]}


async def compare_runs(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    ra, rb = [await call(ctx, "GET", f"/api/runs/{i}") for i in (a["base_run_id"], a["other_run_id"])]
    da, db = run_digest(ra), run_digest(rb)
    m = da.get("metric")
    sa, sb = da.get("holdout_score"), (db.get("holdout_scores") or {}).get(m, db.get("holdout_score"))
    return {"metric": m, "base": da, "other": db, "delta": _r(sb - sa) if sa is not None and sb is not None else None,
            "same_holdout_rows": da.get("holdout_rows") == db.get("holdout_rows"),
            "lineage": (rb.get("config") or {}).get("parent_run_id") == ra["id"]}


async def explain_run(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    e = await call(ctx, "GET", f"/api/runs/{a['run_id']}/explain")
    out: dict[str, Any] = {"run_id": a["run_id"]}
    roc = e.get("roc") or {}
    if isinstance(roc, dict):
        out["roc_auc"] = {k: _r(v.get("auc")) for k, v in roc.items() if isinstance(v, dict) and v.get("auc") is not None}
    cm = e.get("confusion_matrix")
    if cm is not None:
        out["confusion_matrix"] = cm if not isinstance(cm, dict) else {k: cm[k] for k in ("labels", "matrix") if k in cm}
    try:
        fi = await call(ctx, "GET", f"/api/runs/{a['run_id']}/feature-importance")
        rows = fi.get("features") or fi.get("importance") or []
        out["top_features"] = [{"feature": r.get("feature"), "importance": _r(r.get("importance"))} for r in rows[:8]] if isinstance(rows, list) else trim(rows, 2)
    except ToolError as err:
        out["feature_importance"] = str(err)[:160]
    return out


def _rx(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    rx = c.get("prescription") or {}
    return {"id": c.get("id"), "title": c.get("title"), "severity": c.get("severity"), "reason": (c.get("reason") or "")[:300],
            "prescription": {"kind": rx.get("kind"), "title": rx.get("title")} if rx else None}


async def model_doctor(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/runs/{a['run_id']}/doctor")
    return {"run_id": a["run_id"], "headline": d.get("headline"), "primary": _rx(d.get("primary")),
            "secondary": [_rx(c) for c in (d.get("secondary") or [])[:4]],
            "missing_checks": [c["id"] for c in d.get("checks", []) if c.get("status") == "missing"],
            "patchable": [c["id"] for c in [d.get("primary"), *(d.get("secondary") or [])] if c and (c.get("prescription") or {}).get("kind") == "gym_patch"]}


def review_digest(rv: dict[str, Any]) -> dict[str, Any]:
    p = rv.get("plan") or {}
    return {"review_id": rv["id"], "run_id": rv["run_id"], "status": rv["status"], "mode": rv.get("mode"), "decider": rv.get("decider"),
            "progress": rv.get("progress"), "stop_reason": rv.get("stop_reason"),
            "journal": [{"step": e["step"], "hypothesis": e["hypothesis"]["title"], "kind": e["experiment"]["kind"],
                         "run_id": e.get("run_id"), "verdict": e.get("verdict") or e.get("status"),
                         "result": e.get("result") and {k: _r(e["result"].get(k)) for k in ("metric", "parent", "child", "delta", "ci")}}
                        for e in rv.get("journal") or []],
            "plan": p and {"summary": p.get("summary"), "next": [{"kind": x["kind"], "title": x["title"], "why": x["why"]} for x in p.get("next") or []][:8],
                           "best_run": p.get("best_run")},
            "domain_gaps": rv.get("domain_gaps"), "guard": rv.get("guard")}


async def review_run(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    """D4: start the review loop (or follow the one running) and wait for it within the time left."""
    rid = a["run_id"]
    cur = (await call(ctx, "GET", f"/api/runs/{rid}/review")).get("reviews") or []
    live = next((r for r in cur if r["status"] in ("queued", "running")), None)
    if live:
        rv_id = live["id"]
    else:
        out = await call(ctx, "POST", f"/api/runs/{rid}/review", body={"by": ctx.actor, **{k: a[k] for k in ("max_experiments", "max_minutes") if a.get(k)}})
        rv_id = out["id"]
    t0 = time.monotonic()
    wait = min(float(a.get("max_wait_seconds") or 1500), ctx.left())
    while True:
        rv = await call(ctx, "GET", f"/api/runs/{rid}/review/{rv_id}")
        el = time.monotonic() - t0
        if rv["status"] not in ("queued", "running"):
            return {**review_digest(rv), "waited_seconds": round(el, 1)}
        if el >= wait or ctx.stop():
            return {**review_digest(rv), "waited_seconds": round(el, 1), "note": "still running; call read_review later"}
        ctx.progress(f"review {rv_id}: {rv.get('progress') or rv['status']} · {el:.0f} s")
        await asyncio.sleep(5)


async def read_review(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    rid = a["run_id"]
    rv_id = a.get("review_id")
    if not rv_id:
        cur = (await call(ctx, "GET", f"/api/runs/{rid}/review")).get("reviews") or []
        if not cur:
            raise ToolError(f"Run {rid} has no review yet; call review_run.")
        rv_id = cur[0]["id"]
    return review_digest(await call(ctx, "GET", f"/api/runs/{rid}/review/{rv_id}"))


async def _patch_for(ctx: Ctx, run_id: str, finding: str | None) -> tuple[dict[str, Any], str]:
    d = await call(ctx, "GET", f"/api/runs/{run_id}/doctor")
    for c in [d.get("primary"), *(d.get("secondary") or [])]:
        rx = (c or {}).get("prescription") or {}
        if rx.get("kind") == "gym_patch" and (rx.get("patch") or {}).get("body") and (not finding or c["id"] == finding):
            return rx["patch"], f"Model Doctor · {c['title']}"
    v = await call(ctx, "GET", f"/api/runs/{run_id}/vela")
    for f in v.get("findings", []):
        if (f.get("patch") or {}).get("body") and (not finding or f.get("id") == finding):
            return f["patch"], f"Vela finding · {f.get('title') or f.get('id')}"
    raise ToolError("Neither the Model Doctor nor Vela's findings have a Gym patch for this run"
                    + (f" with id '{finding}'" if finding else "") + ".")


async def apply_gym_patch(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    patch, src = await _patch_for(ctx, a["run_id"], a.get("finding_id"))
    body = dict(patch["body"])
    body.update({"parent_run_id": a["run_id"], "patch": {k: patch[k] for k in patch if k != "body"}, "source": "vela-agent"})
    if a.get("time_limit"):
        body["time_limit"] = a["time_limit"]
    v = await call(ctx, "POST", "/api/gym/validate", body=body)
    if not v.get("ok"):
        raise ToolError(f"The Gym refused the patched body: {v.get('errors')}")
    r = await call(ctx, "POST", "/api/runs", body=body)
    return {"run_id": r["id"], "parent_run_id": a["run_id"], "patch": patch.get("title") or patch.get("id"), "from": src,
            "changes": trim(patch.get("changes"), 3, 6)}


async def preview_gym_patch(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    patch, src = await _patch_for(ctx, a["run_id"], a.get("finding_id"))
    v = await call(ctx, "POST", "/api/gym/validate", body={**patch["body"], "parent_run_id": a["run_id"]})
    return {"patch": patch.get("title") or patch.get("id"), "from": src, "changes": trim(patch.get("changes"), 3, 6),
            "valid": v.get("ok"), "errors": v.get("errors"), "warnings": trim(v.get("warnings"), 2, 4)}


async def gym_validate(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    v = await call(ctx, "POST", "/api/gym/validate", body=a["body"])
    return {"ok": v.get("ok"), "errors": v.get("errors"), "warnings": trim(v.get("warnings"), 2, 5), "problem_type": v.get("problem_type")}


async def split_twin(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    r = await call(ctx, "POST", f"/api/runs/{a['run_id']}/split-twin", body={"split_spec": a["split_spec"]})
    return {"run_id": r.get("id"), "parent_run_id": a["run_id"], "status": r.get("status")}


CHECKS = {"learning_curve": "learning-curve", "shift": "shift", "slices": "slices"}


async def run_check(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    path = f"/api/runs/{a['run_id']}/{CHECKS[a['check']]}"
    d = await call(ctx, "POST", path, body=a.get("body") or {})
    return trim(d, 2, 5)


async def get_check(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/runs/{a['run_id']}/{CHECKS[a['check']]}")
    return trim(d, 3, 6)


async def register_version(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    body = {"created_by": ctx.actor, **{k: a[k] for k in ("model_name", "description", "serve_model") if a.get(k)}}
    v = await call(ctx, "POST", f"/api/runs/{a['run_id']}/register", body=body)
    t0 = time.monotonic()
    while v.get("status") not in ("ready", "failed") and time.monotonic() - t0 < min(900, ctx.left()) and not ctx.stop():
        ctx.progress(f"building · {time.monotonic() - t0:.0f} s")
        await asyncio.sleep(2)
        v = await call(ctx, "GET", f"/api/registry/versions/{v['id']}")
    return version_digest(v)


async def request_approval(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    v = await call(ctx, "GET", f"/api/registry/versions/{a['version_id']}")
    note = f"Approval requested by {ctx.actor}. {a.get('comment') or ''}".strip()
    if v["stage"] != "Staging":
        out = await call(ctx, "POST", f"/api/registry/versions/{a['version_id']}/transition",
                         body={"stage": "Staging", "actor": ctx.actor, "comment": note})
        v = out["version"]
    return {**version_digest(v), "requested": True,
            "next": "A reviewer other than the registrant approves it in Registry (four-eyes); promotion stays human."}


async def list_versions(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", "/api/registry/versions")
    vs = d.get("versions", [])
    if a.get("model_name"):
        vs = [v for v in vs if v.get("model_name") == a["model_name"]]
    vs = sorted(vs, key=lambda v: v.get("created_at") or "", reverse=True)[:12]
    full = [await call(ctx, "GET", f"/api/registry/versions/{v['id']}") for v in vs]  # the list omits the built result
    return {"versions": [version_digest(v) for v in full]}


async def try_it(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "POST", f"/api/registry/versions/{a['version_id']}/predict",
                   body={"row": await _features(ctx, a["version_id"], a["row"]), "explain": True})
    return {"prediction": d.get("prediction"), "probabilities": {k: _r(v) for k, v in (d.get("probabilities") or {}).items()},
            "threshold": _r(d.get("threshold")),
            "top_reasons": [{k: (_r(c.get(k)) if isinstance(c.get(k), float) else c.get(k)) for k in ("feature", "statement", "value", "phi")}
                            for c in (((d.get("reasons") or {}).get("codes") or [])[:5] if isinstance(d.get("reasons"), dict) else [])]}


async def explain_decision(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    row = await _features(ctx, a["version_id"], a["row"])
    d = await call(ctx, "POST", f"/api/registry/versions/{a['version_id']}/score", body={"rows": [row], "reasons": True})
    rs = d.get("reasons") or {}
    p_pos = (rs.get("p_positive") or [None])[0]
    codes = (rs.get("codes") or [[]])[0] or []
    adverse = (rs.get("adverse") or [None])[0]
    return {"prediction": (d.get("predictions") or [None])[0], "adverse_class": rs.get("adverse_class"),
            "p_adverse_class": _r(p_pos), "threshold": _r(rs.get("threshold")),
            "decision": "declined (adverse)" if adverse else "approved" if adverse is False else None,
            "reason_codes": [{"rank": c.get("rank"), "code": c.get("code"), "feature": c.get("feature"), "statement": c.get("statement"),
                              "value": c.get("value"), "phi": _r(c.get("phi")),
                              **({"for_notice": False, "why": "geography or a protected characteristic: not a reason to give on an "
                                  "adverse-action notice"} if c.get("notice") is False else {})} for c in codes[:5]],
            "method": rs.get("method"), "note": rs.get("note")}


async def counterfactuals(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    row = await _features(ctx, a["version_id"], a["row"])
    d = await call(ctx, "POST", f"/api/registry/versions/{a['version_id']}/recourse", body={"rows": [row]})
    r = (d.get("results") or [{}])[0]
    opts = [{"text": o.get("text"), "changes": [{"feature": c.get("feature"), "from": c.get("from"), "to": c.get("to")} for c in o.get("changes", [])[:5]],
             "new_p_adverse_class": _r(o.get("p")), "flips_decision": o.get("valid"), "distance": _r(o.get("distance"), 3),
             "plausibility": _r(o.get("plausibility"), 3)} for o in (r.get("options") or [])[:3]]
    return {"adverse": r.get("adverse"), "p_adverse_class": _r(r.get("p")), "threshold": _r(d.get("threshold")),
            "adverse_class": d.get("adverse_class"), "options": opts, "note": r.get("note") or r.get("reason")}


async def _dataset_csv(ctx: Ctx, dataset_id: str, drop: list[str], limit: int | None) -> bytes:
    try:
        from .. import frames  # P03: typed (text stays text); masking comes with frames.scan (P20)

        df = await asyncio.to_thread(frames.read, frames.table(dataset_id))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Dataset {dataset_id}: {e}") from e
    df = df.drop(columns=[c for c in drop if c in df.columns])
    if limit:
        df = df.head(int(limit))
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()


async def batch_score(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    v = await call(ctx, "GET", f"/api/registry/versions/{a['version_id']}")
    run = await call(ctx, "GET", f"/api/runs/{v['run_id']}")
    content = await _dataset_csv(ctx, a["dataset_id"], [run.get("label")] if a.get("drop_label", True) else [], a.get("limit"))
    b = await call(ctx, "POST", f"/api/registry/versions/{a['version_id']}/batches",
                   files={"file": (f"{a['dataset_id']}.csv", content, "text/csv")}, data={"created_by": ctx.actor})
    t0 = time.monotonic()
    while b.get("status") not in ("succeeded", "failed") and time.monotonic() - t0 < min(900, ctx.left()) and not ctx.stop():
        ctx.progress(f"scoring · {time.monotonic() - t0:.0f} s")
        await asyncio.sleep(1.5)
        b = await call(ctx, "GET", f"/api/registry/batches/{b['id']}")
    res = b.get("result") or {}
    dr = res.get("drift") or {}
    feats = dr.get("features") if isinstance(dr.get("features"), list) else []
    top = sorted([f for f in feats if isinstance(f, dict) and f.get("psi") is not None], key=lambda f: -f["psi"])[:6]
    return {"batch_id": b["id"], "status": b.get("status"), "rows": b.get("n_rows"), "error": b.get("error"),
            "drift": {"max_psi": _r(dr.get("max_psi")), "rule": "PSI < 0.1 stable · 0.1–0.25 moderate · > 0.25 major",
                      "top_features": [{"feature": f.get("feature"), "psi": _r(f["psi"])} for f in top],
                      "drifted": [f.get("feature") for f in top if f["psi"] > 0.25],
                      # the drift of the version's own output (its scores or predicted classes), as the batch report keys it
                      "prediction_psi": _r((dr.get("prediction") or {}).get("psi"))}}


async def monitoring(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/registry/versions/{a['version_id']}/monitoring")
    return trim(d, 3, 6)


async def credit_report(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/registry/versions/{a['version_id']}/credit-report", params={"on": a.get("on", "holdout")})
    return trim(d, 3, 6)


async def el_start(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    r = await call(ctx, "POST", "/api/expected-loss/runs", body={"dataset_id": a["dataset_id"], **(a.get("options") or {})})
    return {"run_id": r.get("id") or r.get("run_id"), "status": r.get("status")}


async def el_stress(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "POST", f"/api/expected-loss/runs/{a['run_id']}/stress", body={"shocks": a["shocks"], "name": a.get("name")})
    return trim(d, 3, 6)


SPECIAL = {"expected_loss": "expected-loss", "segmented": "segmented", "survival": "survival",
           "reject_inference": "reject-inference", "multilabel": "multilabel"}


async def get_special(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/{SPECIAL[a['kind']]}/runs/{a['run_id']}")
    return trim(d, 3, 6)


def _special_start(kind: str):
    async def run(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
        r = await call(ctx, "POST", f"/api/{SPECIAL[kind]}/runs", body={"dataset_id": a["dataset_id"], **(a.get("options") or {})})
        return {"run_id": r.get("id") or r.get("run_id"), "status": r.get("status"), "kind": kind}
    return run


async def fair_lending(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    d = await call(ctx, "GET", f"/api/runs/{a['run_id']}/fair-lending")
    return trim(d, 3, 6)


async def autopilot_start(ctx: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    body = {"dataset_id": a["dataset_id"], "goal": a.get("goal") or "", "budget_seconds": a.get("budget_seconds", 600),
            "autonomy": "ask", "decider": "auto", **({"requirements": a["requirements"]} if a.get("requirements") else {})}
    d = await call(ctx, "POST", "/api/autopilot", body=body)
    return {"autopilot_id": d["id"], "status": d.get("status")}


# --------------------------------------------------------------------------------------------------------- registry

S = lambda t, d=None, **k: {"type": t, **({"description": d} if d else {}), **k}  # noqa: E731
DS = S("string", "dataset id, e.g. credit-default")
RUN = S("string", "run id, e.g. run_1a2b3c4d")
VER = S("string", "registry version id, e.g. mv_1a2b3c4d")
ROW = S("object", "one row: column → value (features only)")
OPTS = S("object", "optional extra body fields of the route (defaults come from Smart Intake's detected roles)")

_run_link = lambda a, r: f"#/models?run={r.get('run_id') or a.get('run_id')}" if (r.get("run_id") or a.get("run_id")) else None  # noqa: E731
_ver_link = lambda a, r: f"#/registry?version={r.get('version_id') or a.get('version_id')}"  # noqa: E731

TOOLS: list[Tool] = [
    Tool("list_datasets", "List the datasets (id, rows, target, kind).", {}, [], READ, list_datasets, group="data"),
    Tool("profile_dataset", "A dataset's profile: task, class balance, column types.", {"dataset_id": DS}, ["dataset_id"], READ,
         profile_dataset, lambda a, r: f"#/build?dataset={a['dataset_id']}", "data"),
    Tool("intake_suggestions", "Smart Intake (G0): each column's detected role and what can be built, best first, with the "
         "pre-fill for its flow.", {"dataset_id": DS}, ["dataset_id"], READ, intake, lambda a, r: f"#/build?dataset={a['dataset_id']}", "data"),
    Tool("find_rows", "Look up rows of a dataset where column == value (e.g. an applicant id); without column, any text column.",
         {"dataset_id": DS, "column": S("string"), "value": S("string"), "limit": S("integer", minimum=1, maximum=20)},
         ["dataset_id", "value"], READ, find_rows, group="data"),
    Tool("upload_dataset", "Upload a small CSV as a new dataset.", {"filename": S("string"), "csv_text": S("string"),
         "target": S("string", "column to predict (optional)")}, ["filename", "csv_text"], WRITE, upload_dataset,
         lambda a, r: f"#/build?dataset={r.get('dataset_id')}", "data"),
    Tool("sql_preview", "G9: run a read-only SELECT against the demo database (or a given SQLAlchemy URL) and preview rows.",
         {"query": S("string"), "url": S("string"), "limit": S("integer", minimum=1, maximum=100000)}, [], READ, sql_preview, group="data"),
    Tool("sql_save_dataset", "G9: save a SELECT's result as a dataset (the query goes in its lineage).",
         {"query": S("string"), "url": S("string"), "name": S("string"), "target": S("string"), "limit": S("integer", minimum=1)},
         ["query"], WRITE, sql_save, lambda a, r: f"#/build?dataset={r.get('dataset_id')}", "data"),
    Tool("sql_refresh", "G9: re-run a SQL dataset's query into a new version.", {"dataset_id": DS}, ["dataset_id"], WRITE, sql_refresh, group="data"),
    Tool("train_from_intake", "Train a model from a Smart Intake suggestion's pre-fill (target, dropped IDs, split, F7 cost): "
         "validated by the Gym, then POST /api/runs. split: time (out-of-time), time-group, group or random; default the suggestion's.",
         {"dataset_id": DS, "suggestion_id": S("string", "default binary"), "split": S("string", enum=["time", "time-group", "group", "random"]),
          "time_limit": S("number", "seconds, default 120", minimum=10, maximum=3600), "presets": S("string"),
          "hyperparameters": S("object", "AutoGluon hyperparameters; default GBM + XGB")},
         ["dataset_id"], WRITE, train_from_intake, _run_link, "train"),
    Tool("start_run", "Start a run from a full POST /api/runs body (dataset_id, label, presets, time_limit, fit_kwargs, "
         "drop_columns, split_spec, cost …); the Gym validates it first.", {"body": S("object")}, ["body"], WRITE, start_run, _run_link, "train"),
    Tool("watch_run", "Wait for a run to finish (polls its status) and return its results.",
         {"run_id": RUN, "max_wait_seconds": S("number", minimum=1, maximum=1800)}, ["run_id"], READ, watch_run, _run_link, "train"),
    Tool("get_results", "A run's results: holdout metric and score, best model, leaderboard top, threshold.", {"run_id": RUN},
         ["run_id"], READ, get_results, _run_link, "train"),
    Tool("list_runs", "List runs (optionally of one dataset).", {"dataset_id": DS}, [], READ, list_runs,
         lambda a, r: "#/models", "train"),
    Tool("compare_runs", "Compare two runs on the same holdout: metric delta and lineage.",
         {"base_run_id": RUN, "other_run_id": RUN}, ["base_run_id", "other_run_id"], READ, compare_runs,
         lambda a, r: f"#/models?compare={a['base_run_id']},{a['other_run_id']}", "train"),
    Tool("explain_run", "Explain a run: ROC AUC, confusion matrix, top features.", {"run_id": RUN}, ["run_id"], READ, explain_run, _run_link, "diagnose"),
    Tool("model_doctor", "F17 Model Doctor: the primary limiting factor and secondary ones, each with a prescription.",
         {"run_id": RUN}, ["run_id"], READ, model_doctor, lambda a, r: f"#/models?run={a['run_id']}&tab=Doctor", "diagnose"),
    Tool("review_run", "D4 review this run: Claude proposes hypotheses, each proved or rejected by a typed experiment (ablate, "
         "Data Doctor fix, Feature Lab, clean labels, budget, preset, cost ratio, simpler rung, segment, monotone) on the same "
         "holdout; returns the journal (Δ with CI, kept/rejected) and the improvement plan. Trains child runs.",
         {"run_id": RUN, "max_experiments": S("integer", minimum=1, maximum=8), "max_minutes": S("number", minimum=5, maximum=240),
          "max_wait_seconds": S("number", minimum=10, maximum=3600)}, ["run_id"], WRITE, review_run,
         lambda a, r: f"#/models?run={a['run_id']}&tab=Doctor&review=1", "diagnose"),
    Tool("read_review", "Read a run's latest (or a given) review: journal, improvement plan, domain gaps.",
         {"run_id": RUN, "review_id": S("string")}, ["run_id"], READ, read_review,
         lambda a, r: f"#/models?run={a['run_id']}&tab=Doctor&review=1", "diagnose"),
    Tool("preview_gym_patch", "Show the Gym patch the doctor (or a Vela finding) prescribes and validate it, without training.",
         {"run_id": RUN, "finding_id": S("string")}, ["run_id"], READ, preview_gym_patch, group="diagnose"),
    Tool("apply_gym_patch", "Apply the doctor's (or a Vela finding's) Gym patch: retrain a child run with lineage to the parent.",
         {"run_id": RUN, "finding_id": S("string", "a doctor check id or Vela finding id; default the first patchable"),
          "time_limit": S("number", minimum=10, maximum=3600)}, ["run_id"], WRITE, apply_gym_patch, _run_link, "diagnose"),
    Tool("gym_validate", "Validate a POST /api/runs body with the Gym (no training).", {"body": S("object")}, ["body"], READ, gym_validate, group="train"),
    Tool("split_twin", "F6: the same run again under another split (e.g. {strategy: time, time_column}).",
         {"run_id": RUN, "split_spec": S("object")}, ["run_id", "split_spec"], WRITE, split_twin, _run_link, "diagnose"),
    Tool("run_check", "Launch a diagnostic on a run: learning_curve (F12), shift (F15) or slices (F16).",
         {"run_id": RUN, "check": S("string", enum=list(CHECKS)), "body": OPTS}, ["run_id", "check"], WRITE, run_check, _run_link, "diagnose"),
    Tool("get_check", "Read a diagnostic's result: learning_curve, shift or slices.",
         {"run_id": RUN, "check": S("string", enum=list(CHECKS))}, ["run_id", "check"], READ, get_check, _run_link, "diagnose"),
    Tool("register_version", "Register a finished run as a registry version (waits until it is built). Approval stays human.",
         {"run_id": RUN, "model_name": S("string"), "description": S("string"), "serve_model": S("string")}, ["run_id"], WRITE,
         register_version, _ver_link, "registry"),
    Tool("request_approval", "Request a four-eyes review: moves the version to Staging with a note. Never approves or promotes.",
         {"version_id": VER, "comment": S("string")}, ["version_id"], WRITE, request_approval, _ver_link, "registry"),
    Tool("list_versions", "List registry versions (optionally of one model).", {"model_name": S("string")}, [], READ, list_versions,
         lambda a, r: "#/registry", "registry"),
    Tool("try_it", "Try It: score one row with a version, with probabilities and SHAP reasons.", {"version_id": VER, "row": ROW},
         ["version_id", "row"], READ, try_it, _ver_link, "registry"),
    Tool("explain_decision", "F10 reason codes: score one row at the version's threshold; adverse rows get their top reason codes.",
         {"version_id": VER, "row": ROW}, ["version_id", "row"], READ, explain_decision, _ver_link, "registry"),
    Tool("counterfactuals", "G1 recourse: the smallest realistic changes that flip an adverse decision, re-scored by the served model.",
         {"version_id": VER, "row": ROW}, ["version_id", "row"], READ, counterfactuals, _ver_link, "registry"),
    Tool("batch_score", "Score a dataset as a batch with a version (the label column is dropped) and report drift (PSI per feature).",
         {"version_id": VER, "dataset_id": DS, "limit": S("integer", minimum=1, maximum=100000), "drop_label": S("boolean")},
         ["version_id", "dataset_id"], WRITE, batch_score, lambda a, r: f"#/registry?version={a['version_id']}&tab=batch", "registry"),
    Tool("monitoring", "F18 monitoring of a version: batches over time, drift alerts, performance.", {"version_id": VER},
         ["version_id"], READ, monitoring, lambda a, r: f"#/registry?version={a['version_id']}&tab=monitoring", "registry"),
    Tool("credit_report", "G2 credit model report: Gini/KS, score bands, PSI/CSI, vintages.",
         {"version_id": VER, "on": S("string", enum=["holdout", "batches"])}, ["version_id"], READ, credit_report, _ver_link, "registry"),
    Tool("expected_loss_run", "G3: train PD × LGD × EAD (columns detected from roles).", {"dataset_id": DS, "options": OPTS},
         ["dataset_id"], WRITE, el_start, lambda a, r: f"#/models?run={r.get('run_id')}", "credit"),
    Tool("expected_loss_stress", "G3: a stress scenario on an expected-loss run, e.g. shocks [{column: income_usd, op: pct, value: -10}].",
         {"run_id": RUN, "shocks": S("array", items=S("object")), "name": S("string")}, ["run_id", "shocks"], WRITE, el_stress,
         lambda a, r: f"#/models?run={a['run_id']}", "credit"),
    Tool("fair_lending", "G4: fair-lending check with BISG proxies on a binary run (adverse impact ratios with CIs).",
         {"run_id": RUN}, ["run_id"], READ, fair_lending, _run_link, "credit"),
    Tool("segmented_run", "G5: one model per segment vs the global model.", {"dataset_id": DS, "options": OPTS}, ["dataset_id"],
         WRITE, _special_start("segmented"), lambda a, r: f"#/models?run={r.get('run_id')}", "credit"),
    Tool("survival_run", "G6: time-to-event model (Cox, random survival forest).", {"dataset_id": DS, "options": OPTS},
         ["dataset_id"], WRITE, _special_start("survival"), lambda a, r: f"#/models?run={r.get('run_id')}", "credit"),
    Tool("reject_inference_run", "G7: reject inference methods vs approved-only.", {"dataset_id": DS, "options": OPTS},
         ["dataset_id"], WRITE, _special_start("reject_inference"), lambda a, r: f"#/models?run={r.get('run_id')}", "credit"),
    Tool("multilabel_run", "G8: multi-label classifier, one model per label.", {"dataset_id": DS, "options": OPTS},
         ["dataset_id"], WRITE, _special_start("multilabel"), lambda a, r: f"#/models?run={r.get('run_id')}", "credit"),
    Tool("get_special_run", "Read an expected_loss, segmented, survival, reject_inference or multilabel run's results.",
         {"kind": S("string", enum=list(SPECIAL)), "run_id": RUN}, ["kind", "run_id"], READ, get_special, group="credit"),
    Tool("autopilot_start", "Run it end to end: start an Autopilot (baseline, label audit, Ground-Truth Inbox, retrain loop).",
         {"dataset_id": DS, "goal": S("string"), "budget_seconds": S("number", minimum=60, maximum=7200),
          "requirements": S("array", items=S("object"))}, ["dataset_id"], WRITE, autopilot_start,
         lambda a, r: f"#/vela?mode=auto&ap={r.get('autopilot_id')}", "train"),
]

BY_NAME = {t.name: t for t in TOOLS}


def catalog_text() -> str:
    """The compact catalog for JSON-action prompting (CLI path)."""
    lines = []
    for t in TOOLS:
        ps = ", ".join(f"{k}{'' if k in t.required else '?'}: {v.get('type')}" + (f" {v['enum']}" if v.get("enum") else "")
                       for k, v in t.params.items())
        lines.append(f"- {t.name}({ps}) [{t.kind}] {t.description}")
    return "\n".join(lines)


# The route(s) each tool calls, shown on its card (the confirm card shows the exact call before a write runs).
HTTP = {
    "list_datasets": "GET /api/datasets", "profile_dataset": "GET /api/datasets/{dataset_id}",
    "intake_suggestions": "GET /api/datasets/{dataset_id}/intake", "find_rows": "GET /api/datasets/{dataset_id}/rows",
    "upload_dataset": "POST /api/datasets", "sql_preview": "POST /api/sql/preview", "sql_save_dataset": "POST /api/sql/datasets",
    "sql_refresh": "POST /api/sql/datasets/{dataset_id}/refresh",
    "train_from_intake": "GET /api/datasets/{dataset_id}/intake → POST /api/gym/validate → POST /api/runs",
    "start_run": "POST /api/gym/validate → POST /api/runs", "watch_run": "GET /api/runs/{run_id} (polled)",
    "get_results": "GET /api/runs/{run_id}", "list_runs": "GET /api/runs", "compare_runs": "GET /api/runs/{base_run_id}, /api/runs/{other_run_id}",
    "explain_run": "GET /api/runs/{run_id}/explain, /feature-importance", "model_doctor": "GET /api/runs/{run_id}/doctor",
    "review_run": "POST /api/runs/{run_id}/review, GET /api/runs/{run_id}/review/{id}", "read_review": "GET /api/runs/{run_id}/review/{id}",
    "preview_gym_patch": "GET /api/runs/{run_id}/doctor → POST /api/gym/validate",
    "apply_gym_patch": "GET /api/runs/{run_id}/doctor → POST /api/gym/validate → POST /api/runs (parent_run_id {run_id})",
    "gym_validate": "POST /api/gym/validate", "split_twin": "POST /api/runs/{run_id}/split-twin",
    "run_check": "POST /api/runs/{run_id}/{check}", "get_check": "GET /api/runs/{run_id}/{check}",
    "register_version": "POST /api/runs/{run_id}/register", "request_approval": "POST /api/registry/versions/{version_id}/transition (Staging)",
    "list_versions": "GET /api/registry/versions", "try_it": "POST /api/registry/versions/{version_id}/predict",
    "explain_decision": "POST /api/registry/versions/{version_id}/score (reasons)", "counterfactuals": "POST /api/registry/versions/{version_id}/recourse",
    "batch_score": "POST /api/registry/versions/{version_id}/batches", "monitoring": "GET /api/registry/versions/{version_id}/monitoring",
    "credit_report": "GET /api/registry/versions/{version_id}/credit-report", "expected_loss_run": "POST /api/expected-loss/runs",
    "expected_loss_stress": "POST /api/expected-loss/runs/{run_id}/stress", "fair_lending": "GET /api/runs/{run_id}/fair-lending",
    "segmented_run": "POST /api/segmented/runs", "survival_run": "POST /api/survival/runs", "reject_inference_run": "POST /api/reject-inference/runs",
    "multilabel_run": "POST /api/multilabel/runs", "get_special_run": "GET /api/{kind}/runs/{run_id}", "autopilot_start": "POST /api/autopilot",
}
assert set(HTTP) == set(BY_NAME), set(BY_NAME) ^ set(HTTP)


def http_of(name: str, args: dict[str, Any]) -> str | None:
    class _D(dict):
        def __missing__(self, k: str) -> str:
            return "{" + k + "}"
    tpl = HTTP.get(name)
    if not tpl:
        return None
    a = {k: (CHECKS.get(v, v) if k == "check" else SPECIAL.get(v, v) if k == "kind" else v) for k, v in args.items() if isinstance(v, str)}
    return tpl.format_map(_D(a))
