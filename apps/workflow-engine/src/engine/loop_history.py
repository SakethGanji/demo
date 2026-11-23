"""Per-item loop history and repeat-until tries: extra, bounded records of what each loop pass did.

Nothing here changes what a node outputs, the order of items or what ``$node`` / ``$json`` see. The runner and the
Loop / Poll nodes call these helpers to note, per Loop pass, which items went in, the branch each body step took,
a short sample of each body step's output, and the step's status and error; and, per Poll session, each try.

The records live on ``ExecutionContext.loop_runs`` / ``poll_tries`` while the run goes, and are saved into the
execution's metadata (``loop_runs`` / ``poll_tries``) when it completes. Everything is capped: at most
``MAX_PASSES`` passes per loop, ``MAX_NODE_ITEMS`` items per step, ``MAX_ITEM_BYTES`` per item, ``MAX_TRIES`` tries
per session; going over sets ``truncated``.

A Loop pass is "active" while the Loop's internal state exists (between emitting a batch and emitting ``done``).
The pass index is ``ceil(currentIndex / batchSize) - 1``, so it survives an approval pause (the state is in the
resume snapshot).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

MAX_PASSES = 200
MAX_NODE_ITEMS = 3
MAX_ITEM_BYTES = 2048
MAX_STR = 200
MAX_SESSIONS = 50
MAX_TRIES = 60
MAX_LOOP_ITEMS = 3

LOOP_TYPES = ("Loop",)


def _short(value: Any) -> Any:
    """A JSON-safe copy of ``value`` that fits in ``MAX_ITEM_BYTES`` (long strings cut, deep values summarised)."""
    try:
        raw = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return {"_truncated": True, "preview": str(value)[:MAX_STR]}
    if len(raw) <= MAX_ITEM_BYTES:
        return json.loads(raw)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, str):
                out[k] = v if len(v) <= MAX_STR else v[: MAX_STR - 1] + "…"
            elif isinstance(v, (int, float, bool)) or v is None:
                out[k] = v
            elif isinstance(v, list):
                out[k] = f"{len(v)} items"
            elif isinstance(v, dict):
                out[k] = f"{len(v)} fields"
            else:
                out[k] = str(v)[:MAX_STR]
            if len(json.dumps(out, default=str)) > MAX_ITEM_BYTES:
                out.pop(k, None)
                break
        out["_truncated"] = True
        return out
    return {"_truncated": True, "preview": raw[:MAX_STR]}


def _items_json(items: Any) -> tuple[list[Any], int]:
    """The first ``MAX_NODE_ITEMS`` items' json, shortened, and the total count."""
    if not items:
        return [], 0
    out = []
    for it in list(items)[:MAX_NODE_ITEMS]:
        out.append(_short(getattr(it, "json", it)))
    return out, len(items)


# --------------------------------------------------------------------------------------------------- loop structure


def loop_body(context: Any, loop_name: str) -> frozenset[str]:
    """The steps reachable from the Loop's ``loop`` port without passing back through the Loop (cached per run)."""
    cache = context.loop_bodies
    if loop_name in cache:
        return cache[loop_name]
    conns = context.workflow.connections
    out: dict[str, list[str]] = {}
    for c in conns:
        out.setdefault(c.source_node, []).append(c.target_node)
    start = [c.target_node for c in conns if c.source_node == loop_name and c.source_output == "loop"]
    seen: set[str] = set()
    stack = [n for n in start if n != loop_name]
    while stack:
        n = stack.pop()
        if n in seen or n == loop_name:
            continue
        seen.add(n)
        stack.extend(t for t in out.get(n, []) if t not in seen and t != loop_name)
    body = frozenset(seen)
    cache[loop_name] = body
    return body


def active_pass(context: Any, loop_name: str) -> int | None:
    """The pass a Loop is on (its batch is in the body), or None when it is not iterating."""
    st = context.node_internal_state.get(loop_name)
    if not isinstance(st, dict) or "items" not in st:
        return None
    bs_raw = (st.get("policy") or {}).get("batchSize")
    if bs_raw is None:  # a state saved before the policy was kept (an old resume snapshot): the node's own setting
        nd = next((n for n in context.workflow.nodes if n.name == loop_name), None)
        bs_raw = (nd.parameters or {}).get("batchSize") if nd is not None else 1
    try:
        bs = max(1, int(bs_raw or 1))
    except (TypeError, ValueError):
        bs = 1
    cur = int(st.get("currentIndex") or 0)
    if cur <= 0:
        return None
    return math.ceil(cur / bs) - 1


def enclosing_loops(context: Any, node_name: str) -> list[str]:
    """The Loops whose body holds ``node_name`` and that are iterating now, innermost first."""
    found = []
    for n in context.workflow.nodes:
        if n.type not in LOOP_TYPES or n.name == node_name:
            continue
        if node_name in loop_body(context, n.name) and active_pass(context, n.name) is not None:
            found.append(n.name)
    # innermost = smallest body (an inner loop's body sits inside the outer one's)
    found.sort(key=lambda name: len(loop_body(context, name)))
    return found


def policy(context: Any, loop_name: str) -> dict[str, Any]:
    st = context.node_internal_state.get(loop_name)
    return dict((st or {}).get("policy") or {}) if isinstance(st, dict) else {}


# --------------------------------------------------------------------------------------------------- loop records


def _loop_rec(context: Any, loop_name: str) -> dict[str, Any]:
    rec = context.loop_runs.get(loop_name)
    if rec is None:
        rec = {"passes": {}, "truncated": False, "failed": 0, "skipped": 0, "longestFailStreak": 0, "streak": 0}
        context.loop_runs[loop_name] = rec
    return rec


def start_loop(context: Any, loop_name: str, total_items: int, pol: dict[str, Any]) -> None:
    rec = _loop_rec(context, loop_name)
    rec.update({
        "totalItems": total_items,
        "batchSize": pol.get("batchSize", 1),
        "concurrency": pol.get("concurrency", 1),
        "onItemFailure": pol.get("onItemFailure", "stop"),
        "stopAfterFailures": pol.get("stopAfterFailures", 0),
        "startedAt": datetime.now().isoformat(),
    })
    if pol.get("concurrencyClamped"):
        rec["concurrencyClamped"] = pol["concurrencyClamped"]


def _pass(context: Any, loop_name: str, index: int, create: bool = True) -> dict[str, Any] | None:
    rec = _loop_rec(context, loop_name)
    passes = rec["passes"]
    p = passes.get(index)
    if p is None and create:
        if len(passes) >= MAX_PASSES:
            rec["truncated"] = True
            return None
        p = {"index": index, "status": "running", "nodes": {}, "order": []}
        passes[index] = p
    return p


def open_pass(context: Any, loop_name: str, index: int, items: list[Any]) -> None:
    p = _pass(context, loop_name, index)
    if p is None:
        return
    p["items"] = [_short(getattr(i, "json", i)) for i in items[:MAX_LOOP_ITEMS]]
    p["itemCount"] = len(items)
    p["startedAt"] = datetime.now().isoformat()


def close_pass(context: Any, loop_name: str, index: int) -> None:
    """The Loop came round again: the pass is over. A pass with no failure is ``ok``."""
    p = _pass(context, loop_name, index, create=False)
    if p is None:
        return
    if p["status"] == "running":
        p["status"] = "ok"
        rec = _loop_rec(context, loop_name)
        rec["streak"] = 0
    p.setdefault("completedAt", datetime.now().isoformat())


def finish_loop(context: Any, loop_name: str, stopped_early: bool = False) -> None:
    rec = _loop_rec(context, loop_name)
    rec["completedAt"] = datetime.now().isoformat()
    if stopped_early:
        rec["stoppedEarly"] = True


def record_step(
    context: Any,
    node_name: str,
    *,
    status: str,
    outputs: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Note a body step's result on every Loop pass it runs inside."""
    if not context.workflow or not any(n.type in LOOP_TYPES for n in context.workflow.nodes):
        return
    for loop_name in enclosing_loops(context, node_name):
        idx = active_pass(context, loop_name)
        if idx is None:
            continue
        p = _pass(context, loop_name, idx)
        if p is None:
            continue
        entry: dict[str, Any] = {"status": status}
        if outputs is not None:
            ports = [k for k, v in outputs.items() if v]
            entry["ports"] = ports
            counts = {k: len(v) for k, v in outputs.items() if v}
            if counts:
                entry["portCounts"] = counts
            main = outputs.get("main") or next((v for v in outputs.values() if v), None)
            items, n = _items_json(main)
            entry["items"] = items
            entry["itemCount"] = n
            if n > len(items):
                entry["truncated"] = True
        if metrics:
            if "executionTimeMs" in metrics:
                entry["ms"] = metrics["executionTimeMs"]
            if metrics.get("branchDecision") is not None:
                entry["branchDecision"] = metrics["branchDecision"]
            if metrics.get("retries"):
                entry["retries"] = metrics["retries"]
        if error:
            entry["error"] = error[:500]
        prev = p["nodes"].get(node_name)
        if prev is not None:
            # the same step ran again in this pass (an inner loop / a retry round): keep a count, show the latest
            entry["runs"] = int(prev.get("runs", 1)) + 1
        p["nodes"][node_name] = entry
        if node_name not in p["order"]:
            p["order"].append(node_name)


def fail_pass(context: Any, loop_name: str, node_name: str, error: str, skipped: bool) -> int:
    """Mark the loop's current pass failed (``skipped`` when the loop keeps going). Returns the failure streak."""
    rec = _loop_rec(context, loop_name)
    idx = active_pass(context, loop_name)
    p = _pass(context, loop_name, idx) if idx is not None else None
    already = p is not None and p["status"] in ("failed", "skipped")
    if p is not None:
        p["status"] = "skipped" if skipped else "failed"
        p["failedAt"] = node_name
        p["error"] = error[:500]
        p.setdefault("completedAt", datetime.now().isoformat())
    if not already:
        rec["failed"] += 1
        if skipped:
            rec["skipped"] += 1
        rec["streak"] += 1
        rec["longestFailStreak"] = max(rec["longestFailStreak"], rec["streak"])
    return int(rec["streak"])


# --------------------------------------------------------------------------------------------------- poll tries


def poll_try(context: Any, node_name: str, *, attempt: int, result: str, value: Any, new_session: bool) -> None:
    """One check of a Poll (repeat until): when, the outcome (``pending`` / ``met`` / ``timeout``), a short value."""
    sessions = context.poll_tries.setdefault(node_name, [])
    if new_session or not sessions or sessions[-1].get("outcome"):
        if len(sessions) >= MAX_SESSIONS:
            context.poll_tries.setdefault("_truncated", [])  # marker list, read by serialise
            return
        loops = {}
        for loop_name in enclosing_loops(context, node_name):
            idx = active_pass(context, loop_name)
            if idx is not None:
                loops[loop_name] = idx
        sessions.append({"startedAt": datetime.now().isoformat(), "tries": [], "passes": loops, "truncated": False})
    s = sessions[-1]
    if len(s["tries"]) >= MAX_TRIES:
        s["truncated"] = True
    else:
        s["tries"].append({"n": attempt, "at": datetime.now().isoformat(), "result": result, "value": _short(value)})
    if result in ("met", "timeout"):
        s["outcome"] = result
        s["attempts"] = attempt


# --------------------------------------------------------------------------------------------------- saving


def serialise_loops(context: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, rec in (context.loop_runs or {}).items():
        passes = [rec["passes"][k] for k in sorted(rec["passes"])]
        r = {k: v for k, v in rec.items() if k not in ("passes", "streak")}
        r["passes"] = passes
        out[name] = r
    return out


def serialise_polls(context: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, sessions in (context.poll_tries or {}).items():
        if name == "_truncated":
            continue
        out[name] = {"sessions": sessions, "truncated": "_truncated" in context.poll_tries}
    return out


def merge_saved(old: dict[str, Any] | None, new: dict[str, Any], kind: str) -> dict[str, Any]:
    """A resumed run (after an approval) adds its passes / sessions to the ones saved before the pause."""
    if not old:
        return new
    merged = dict(old)
    for name, rec in new.items():
        prev = merged.get(name)
        if not isinstance(prev, dict):
            merged[name] = rec
            continue
        if kind == "loops":
            by = {p.get("index"): p for p in prev.get("passes") or []}
            for p in rec.get("passes") or []:
                by[p.get("index")] = p
            merged[name] = {**prev, **{k: v for k, v in rec.items() if k != "passes"},
                            "passes": [by[k] for k in sorted(by, key=lambda x: (x is None, x))]}
        else:
            merged[name] = {**rec, "sessions": (prev.get("sessions") or []) + (rec.get("sessions") or [])}
    return merged
