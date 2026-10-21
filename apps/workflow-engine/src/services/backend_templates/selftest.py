"""The builder's self-test: scenario steps + deterministic probes run against SCRATCH copies of the module's tables.

Why scratch tables: a test that creates, updates and approves rows must not touch the project's data. Before a run the
module's literal ``studio.db.table("x")`` names are rewritten to a throw-away ``x__stNNNNNN`` copy (same columns, no rows)
and ``studio.jobs`` is replaced by a deterministic fake, so nothing reaches the Jobs service. The copies are deleted
afterwards (and any left behind by a crashed run are swept at the next start).

What it catches that a "does GET return 200" test cannot: a create that accepts an empty body, an update that ignores a
filter, an illegal state move that succeeds, a sync that adds rows the second time, a missing id that answers 502
instead of 404, a filter that returns rows it should not.

Pure functions here; ``Scratch`` and the runner glue live next to the database calls.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs

MAX_PLAN_STEPS = 40
MAX_PROBES = 24
MAX_FIX_ROUNDS = 3
MAX_POLL_CHECKS = 8
SCRATCH_RE = re.compile(r"__st[0-9a-f]{6}$")
BAD_ID = 999_999_999
ID_PARAM = re.compile(r"^(id|key|[a-z0-9_]*_id)$")

STEP_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": ("Self-test scenario, run against empty scratch copies of your tables, in order. Each step: "
                    "{id?, call:'POST /cases', body?, query?, expect:{status: 201 | [400,409] | '4xx', has:[paths], "
                    "not_has:[paths], equals:{path: value}, all:{path:'rows', where:{field: value}}, min_len:{path, n}, "
                    "max_len:{path, n}}, save:{var: 'path.in.response'}, twice?: true}. `{var}` in call/body/query is replaced "
                    "by a saved value. Cover: create -> read -> update -> every state transition (valid AND an invalid one "
                    "expecting 409) -> each list filter (create 2 rows that differ, assert all rows match and the other "
                    "is absent) -> sync/approve called twice (twice: true asserts the second call changes no table's row "
                    "count) -> an unknown id (404). At most 40 steps."),
    "items": {"type": "object", "required": ["call"], "properties": {
        "id": {"type": "string"}, "call": {"type": "string"}, "body": {"type": "object"}, "query": {"type": "object"}, "headers": {"type": "object"},
        "expect": {"type": "object", "properties": {
            "status": {"type": "string", "description": "e.g. '201', '4xx' or '400,409'"},
            "has": {"type": "array", "items": {"type": "string"}}, "not_has": {"type": "array", "items": {"type": "string"}},
            "equals": {"type": "object"}, "not_equals": {"type": "object"}, "all": {"type": "object"}, "min_len": {"type": "object"}, "max_len": {"type": "object"}}},
        "save": {"type": "object"}, "twice": {"type": "boolean"}}}}

# ── source rewriting ──────────────────────────────────────────────────────────

FAKE_JOBS = '''

class _StJobs:
    """Self-test stand-in for studio.jobs: deterministic, no network, shaped like the real Jobs service (apps/jobs-service/README.md).

    A job id carries its own spec (compressed), so any later call in any process can answer for it. get() reports a finished
    job: a test-run with one result per case (the failing ones carry ``error`` and ``failed_step``), a triage with ``output``
    (verdict, evidence, ``proposed_fix.steps_before/steps_after`` with the normalised ``state`` field the real service adds)."""
    CFG = __ST_CFG__

    @staticmethod
    def _enc(spec):
        import base64 as _b, json as _j, zlib as _z
        return "stjob-" + _b.urlsafe_b64encode(_z.compress(_j.dumps(spec, sort_keys=True, default=str).encode(), 9)).decode().rstrip("=")
    @staticmethod
    def _dec(job_id):
        import base64 as _b, json as _j, zlib as _z
        raw = str(job_id).split("stjob-", 1)[-1]
        try:
            return _j.loads(_z.decompress(_b.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))).decode())
        except Exception:
            return None
    def start(self, body):
        body = body if isinstance(body, dict) else {}
        t = body.get("type")
        if t == "test-run":
            cases = [{"id": c.get("id"), "kind": c.get("kind", "ui"), "title": c.get("title"),
                      "steps": (c.get("steps") if c.get("kind", "ui") == "ui" else c.get("requests")) or []}
                     for c in (body.get('cases') or []) if isinstance(c, dict)]
            spec = {"t": t, "label": body.get("label"), "cases": cases}
        elif t == "triage":
            src = self._dec(body.get("job_id"))
            case = next((c for c in (src or {}).get("cases", []) if c.get("id") == body.get("case_id")), None)
            spec = {"t": t, "job_id": body.get("job_id"), "case": case or {"id": body.get("case_id"), "kind": "ui", "steps": []}}
        else:
            spec = {"t": t or "explore", "goal": body.get("goal")}
        return {"job_id": self._enc(spec), "status": "queued"}
    def _failing(self, spec):
        fail = self.CFG.get("fail", "first")
        if "verif" in str(spec.get("label") or "").lower() and not self.CFG.get("verify_fails"):
            return set()          # a re-run of a proposed fix goes green (set verify_fails to test the red path)
        ids = [c.get("id") for c in spec.get("cases", [])]
        if fail == "all":
            return set(ids)
        if fail == "none":
            return set()
        if isinstance(fail, list):
            return {i for i in ids if i in fail}
        return set(ids[:1])
    def _results(self, spec):
        bad = self._failing(spec)
        out = []
        for c in spec.get("cases", []):
            steps = c.get("steps") or []
            r = {"case_id": c.get("id"), "kind": c.get("kind", "ui"), "title": c.get("title"), "duration_ms": 120,
                 "steps": [{"index": i, "status": "passed"} for i in range(len(steps))]}
            if c.get("id") in bad:
                idx = min(int(self.CFG.get("failed_index", 0)), len(steps) - 1) if steps else -1
                step = steps[idx] if 0 <= idx < len(steps) else {}
                name = ((step.get("target") or {}).get("name") if isinstance(step, dict) else None) or "the element"
                r.update(status="failed", error="Step %d failed: no element matched %r" % (idx, name),
                         failed_step={"index": idx, "step": step, "description": "%s %s" % (step.get("action", "step") if isinstance(step, dict) else "step", name)})
                if 0 <= idx < len(r["steps"]):
                    r["steps"][idx]["status"] = "failed"
            else:
                r["status"] = "passed"
            out.append(r)
        return out
    def get(self, job_id):
        spec = self._dec(job_id)
        base = {"job_id": job_id, "label": (spec or {}).get("label"), "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z"}
        if spec is None:
            return {**base, "type": "test-run", "status": "passed", "progress": {"done": 1, "total": 1}, "results": []}
        if spec["t"] == "test-run":
            res = self._results(spec)
            failed = any(r["status"] == "failed" for r in res)
            return {**base, "type": "test-run", "status": "failed" if failed else "passed",
                    "progress": {"done": len(res), "total": len(res), "current_case_id": None, "current_step": None}, "results": res}
        if spec["t"] == "triage":
            case = spec["case"]
            raw = case.get("steps") or []
            before, after = [], []
            for st in raw:                                    # the service reports the steps it saw NORMALISED (state: visible)
                st = dict(st) if isinstance(st, dict) else st
                if isinstance(st, dict) and isinstance(st.get("target"), dict):
                    st["target"] = dict(st["target"])
                    st.setdefault("state", "visible")
                before.append(st)
            for i, st in enumerate(raw):
                st = dict(st) if isinstance(st, dict) else st
                if isinstance(st, dict) and isinstance(st.get("target"), dict):
                    tg = dict(st["target"])
                    if i == int(self.CFG.get("failed_index", 0)):          # the heuristic fix: the target was renamed in the app
                        key = next((k for k in ("name", "label", "text", "placeholder") if tg.get(k)), "name")
                        tg[key] = (str(tg[key]) + " (renamed)") if tg.get(key) else "Renamed"
                    st["target"] = tg
                    st.setdefault("state", "visible")          # the real planner normalises targets with an explicit state
                after.append(st)
            if not after:
                after = [{"action": "assert_visible", "target": {"role": "heading", "name": "Home"}, "state": "visible"}]
            return {**base, "type": "triage", "status": "passed", "progress": {"done": 1, "total": 1}, "results": [],
                    "output": {"verdict": "app_changed", "confidence": 0.95, "summary": "The target was renamed in the app.",
                              "evidence": [{"kind": "failed_step", "text": "no element matched"}],
                              "proposed_fix": {"steps_before": before, "steps_after": after, "explanation": "Rename the target to the new accessible name."},
                              "source": "heuristic", "heuristic": {"verdict": "app_changed", "confidence": 0.8}}}
        return {**base, "type": spec["t"], "status": "passed", "progress": {"done": 1, "total": 1}, "results": [],
                "output": {"steps": [], "notes": "", "source": "heuristic", "status": "passed", "blocked": [], "spec_preview": ""}}
    def events(self, job_id, after=0):
        spec = self._dec(job_id) or {}
        evs = []
        if spec.get("t") == "test-run":
            for r in self._results(spec):
                evs.append({"case_id": r["case_id"], "type": "case_start", "text": r.get("title") or r["case_id"]})
                if r["status"] == "failed":
                    evs.append({"case_id": r["case_id"], "type": "step_fail", "text": r["error"]})
                evs.append({"case_id": r["case_id"], "type": "case_end", "text": r["status"], "data": {"status": r["status"]}})
        evs = [{"seq": i + 1, "ts": "2026-01-01T00:00:00Z", **e} for i, e in enumerate(evs)]
        after = int(after or 0)
        part = [e for e in evs if e["seq"] > after]
        return {"events": part, "next": part[-1]["seq"] if part else after, "status": self.get(job_id)["status"]}
    def frame(self, job_id):
        return None
    def artifacts(self, job_id):
        return []
    def artifact(self, job_id, name):
        return {"name": name, "content_type": "text/plain", "size": 0, "base64": ""}
    def cancel(self, job_id):
        return {"job_id": job_id, "status": "cancelled", "cancel_requested": True}

_st_jobs = _StJobs()
'''


def fake_jobs_source(cfg: dict[str, Any] | None = None) -> str:
    """The fake ``studio.jobs`` module text. ``cfg``: ``fail`` = ``"first"`` (default) | ``"all"`` | ``"none"`` | [case ids];
    ``failed_index`` = which step of a failing case fails; ``verify_fails`` = a job labelled "verify..." fails too."""
    return FAKE_JOBS.replace("__ST_CFG__", repr(dict(cfg or {})))


def _chain(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def literal_tables(source: str) -> tuple[list[str], bool]:
    """(literal table names the module uses, whether any table name is dynamic)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], True
    names: list[str] = []
    dynamic = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _chain(n.func) in ("studio.db.table", "db.table"):
            a = n.args[0] if n.args else None
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                if a.value not in names:
                    names.append(a.value)
            else:
                dynamic = True
    return names, dynamic


def rewrite_for_scratch(source: str, mapping: dict[str, str], jobs_cfg: dict[str, Any] | None = None) -> str:
    """Replace each literal table name with its scratch name and ``studio.jobs`` with the fake. Position-exact, so line
    numbers in a traceback match the original. ``jobs_cfg={"mode": "real"}`` keeps ``studio.jobs`` as it is (the smoke run
    starts a real job); only the table names are rewritten."""
    real_jobs = (jobs_cfg or {}).get("mode") == "real"
    tree = ast.parse(source)
    lines = source.split("\n")
    edits: list[tuple[int, int, int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _chain(n.func) in ("studio.db.table", "db.table") and n.args:
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value in mapping and a.lineno == a.end_lineno:
                edits.append((a.lineno, a.col_offset, a.end_col_offset, json.dumps(mapping[a.value])))
        elif not real_jobs and isinstance(n, ast.Attribute) and _chain(n) == "studio.jobs" and n.lineno == n.end_lineno:
            edits.append((n.lineno, n.col_offset, n.end_col_offset, "_st_jobs"))
    # ast columns are UTF-8 byte offsets
    for ln, a, b, text in sorted(edits, reverse=True):
        raw = lines[ln - 1].encode("utf-8")
        lines[ln - 1] = (raw[:a] + text.encode("utf-8") + raw[b:]).decode("utf-8")
    text = "\n".join(lines).rstrip("\n") + "\n"
    return text if real_jobs else text + fake_jobs_source(jobs_cfg)


def scratch_name(table: str, token: str) -> str:
    return f"{table[:48]}__st{token}"


def new_token() -> str:
    return secrets.token_hex(3)


# ── what a route does (from the AST) ─────────────────────────────────────────


def route_flags(source: str, decls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per handler function: whether it reaches outside (studio.nodes / studio.secret), writes rows, and its tables."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out: dict[str, dict[str, Any]] = {}
    for d in decls:
        fn = fns.get(d["function"])
        flags = {"external": False, "inserts": False, "writes": False, "tables": []}
        if fn is not None:
            seg = ast.get_source_segment(source, fn) or ""
            flags["external"] = bool(re.search(r"\bstudio\.nodes\b|\bnodes\.\w+\(", seg)) or (
                bool(re.search(r"\bstudio\.secret\b", seg)) and not re.search(r"\bstudio\.jobs\b|\bjobs\.\w+\(", seg))
            flags["inserts"] = bool(re.search(r"\.(insert|upsert)\(", seg))
            flags["writes"] = bool(re.search(r"\.(insert|upsert|update|delete)\(", seg))
            flags["tables"] = sorted(literal_tables(seg)[0])
        out[d["function"]] = flags
    return out


# ── steps ──────────────────────────────────────────────────────────────────────

_CALL_RE = re.compile(r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+(\S+)\s*$", re.IGNORECASE)


def body_for_id(s: dict[str, Any]) -> Any:
    return next((s[k] for k in ("body", "json", "payload", "data", "request_body") if k in s), None)


def parse_plan(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a model-written plan: ``(steps, problems)``. A bad step is reported and dropped, never run."""
    steps: list[dict[str, Any]] = []
    problems: list[str] = []
    if raw in (None, []):
        return steps, problems
    if not isinstance(raw, list):
        return steps, ["self_test must be a list of steps"]
    for i, s in enumerate(raw[:MAX_PLAN_STEPS], 1):
        if isinstance(s, dict) and not s.get("call") and isinstance(s.get("method"), str) and isinstance(s.get("path"), str):
            s = {**s, "call": f"{s['method']} {s['path']}"}             # a model that wrote method/path keys means the same
        if not isinstance(s, dict) or not isinstance(s.get("call"), str) or not _CALL_RE.match(s["call"]):
            problems.append(f"self_test step {i} ({json.dumps(s, default=str)[:100]}): 'call' must look like 'POST /cases/{{case_id}}'")
            continue
        m = _CALL_RE.match(s["call"])
        path, _, qs = m.group(2).partition("?")
        query = {k: v[0] for k, v in parse_qs(qs).items()} if qs else {}
        if isinstance(s.get("query"), dict):
            query.update(s["query"])
        exp = s.get("expect") if isinstance(s.get("expect"), dict) else {}
        for k in ("expect_status", "expected_status", "status"):
            if exp.get("status") is None and isinstance(s.get(k), (int, str, list)):
                exp = {**exp, "status": s[k]}
        body = next((s[k] for k in ("body", "json", "payload", "data", "request_body") if k in s), None)
        if isinstance(s.get("params"), dict) and not isinstance(s.get("query"), dict):
            query.update(s["params"])
        save = next((s[k] for k in ("save", "capture", "store", "extract") if isinstance(s.get(k), dict)), {})
        auto = "s" + hashlib.sha1(f"{s['call']}{json.dumps(body_for_id(s), sort_keys=True, default=str)}".encode()).hexdigest()[:6]
        steps.append({"id": str(s.get("id") or auto)[:40], "method": m.group(1).upper(), "path": path,
                      "body": body, "query": query, "expect": exp, "save": save,
                      "headers": {str(k): str(v) for k, v in s["headers"].items()} if isinstance(s.get("headers"), dict) else {},
                      "twice": bool(s.get("twice")), "kind": "plan"})
    if len(raw) > MAX_PLAN_STEPS:
        problems.append(f"self_test has {len(raw)} steps; only the first {MAX_PLAN_STEPS} run")
    return steps, problems


def canonical(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parsed steps back to the model-facing shape (what is stored and merged): only valid steps survive."""
    return [{"id": s["id"], "call": f"{s['method']} {s['path']}",
             **({"body": s["body"]} if s.get("body") is not None else {}), **({"query": s["query"]} if s.get("query") else {}),
             **({"headers": s["headers"]} if s.get("headers") else {}),
             "expect": s["expect"], **({"save": s["save"]} if s.get("save") else {}), **({"twice": True} if s.get("twice") else {})}
            for s in steps]


def merge_plan(old: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """A patch adds steps; a step with an existing id replaces it."""
    out = list(old or [])
    ids = {str(s.get("id")): i for i, s in enumerate(out) if isinstance(s, dict) and s.get("id")}
    for s in new or []:
        if isinstance(s, dict) and s.get("id") and str(s["id"]) in ids:
            out[ids[str(s["id"])]] = s
        else:
            out.append(s)
    return out[:MAX_PLAN_STEPS]


def _fill(v: Any, vars_: dict[str, Any], missing: list[str]) -> Any:
    if isinstance(v, str):
        m = re.fullmatch(r"\{(\w+)\}", v)
        if m and m.group(1) in vars_:
            return vars_[m.group(1)]          # whole-string reference keeps its type (an integer id stays an integer)

        def sub(mm: re.Match[str]) -> str:
            if mm.group(1) in vars_:
                return str(vars_[mm.group(1)])
            missing.append(mm.group(1))
            return mm.group(0)
        return re.sub(r"\{(\w+)\}", sub, v)
    if isinstance(v, dict):
        return {k: _fill(x, vars_, missing) for k, x in v.items()}
    if isinstance(v, list):
        return [_fill(x, vars_, missing) for x in v]
    return v


def dig(body: Any, path: str) -> Any:
    """``rows.0.id`` into a response body; missing -> the ``_MISSING`` sentinel."""
    cur = body
    for part in [p for p in str(path).split(".") if p != ""]:
        if part.startswith("~"):                      # ~key: the first `key` anywhere below (depth-first)
            cur = _deep(cur, part[1:])
            if cur is _MISSING:
                return _MISSING
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _deep(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            r = _deep(v, key)
            if r is not _MISSING:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _deep(v, key)
            if r is not _MISSING:
                return r
    return _MISSING


def dig_any(body: Any, paths: str) -> Any:
    """``a|b|~c``: the first alternative that exists."""
    for p in str(paths).split("|"):
        v = dig(body, p.strip())
        if v is not _MISSING:
            return v
    return _MISSING


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


_MISSING = _Missing()


def _status_ok(want: Any, got: int) -> bool:
    if want is None:
        return got < 400
    if isinstance(want, str):
        parts = [w.strip() for w in want.split(",") if w.strip()]
        if len(parts) > 1:
            return any(_status_ok(w, got) for w in parts)
        w = parts[0] if parts else ""
        if re.fullmatch(r"[1-5]xx", w, re.IGNORECASE):
            return got // 100 == int(w[0])
        return w.isdigit() and int(w) == got
    if isinstance(want, list):
        return any(_status_ok(w, got) for w in want)
    return int(want) == got


def check_expect(expect: dict[str, Any], status: int, body: Any, vars_: dict[str, Any]) -> str | None:
    """None when the response meets ``expect``, else one sentence saying how it does not."""
    if status >= 500 and not _status_ok(expect.get("status"), status):
        return f"server error {status} (a bad request must answer 4xx, never 5xx)"
    if not _status_ok(expect.get("status"), status):
        return f"expected status {expect.get('status', '2xx')}, got {status}"
    for p in expect.get("has") or []:
        if dig(body, p) is _MISSING:
            return f"response has no '{p}'"
    for p in expect.get("not_has") or []:
        if dig(body, p) is not _MISSING:
            return f"response should not have '{p}'"
    for p, want in (expect.get("equals") or {}).items():
        want = _fill(want, vars_, [])
        got = dig(body, p)
        if got is _MISSING or (got != want and str(got) != str(want)):
            return f"'{p}' is {got!r}, expected {want!r}"
    for p, want in (expect.get("not_equals") or {}).items():
        want = _fill(want, vars_, [])
        got = dig_any(body, p)
        if got is _MISSING:
            return f"response has no '{p}'"
        if got == want or str(got) == str(want):
            return f"'{p}' did not change (still {str(got)[:120]})"
    all_ = expect.get("all")
    if isinstance(all_, dict):
        rows = dig(body, all_.get("path", ""))
        where = _fill(all_.get("where") or {}, vars_, [])
        if not isinstance(rows, list):
            return f"'{all_.get('path')}' is not a list"
        for r in rows:
            for k, w in where.items():
                have = dig(r, k) if isinstance(r, dict) else _MISSING
                if isinstance(have, list) and not isinstance(w, list):
                    ok = w in have or str(w) in map(str, have)
                else:
                    ok = have == w or str(have) == str(w)
                if not ok:
                    return f"filter leaked a row: {k} is {have!r}, expected {w!r}"
    for key, cmp in (("min_len", lambda n, w: n >= w), ("max_len", lambda n, w: n <= w)):
        spec = expect.get(key)
        if isinstance(spec, dict):
            seq = dig(body, spec.get("path", ""))
            if not isinstance(seq, (list, dict, str)):
                return f"'{spec.get('path')}' has no length"
            if not cmp(len(seq), int(spec.get("n", 0))):
                return f"'{spec.get('path')}' has {len(seq)} item(s); {key.replace('_', ' ')} {spec.get('n')}"
    return None


def _under_empty_list(body: Any, path: str) -> bool:
    cur = body
    for part in [p for p in str(path).split(".") if p]:
        if isinstance(cur, list):
            return not cur and part.lstrip("-").isdigit()
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return False


def empty_tolerant(expect: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """``expect`` without the assertions that can only fail because a list is empty (``min_len`` 1 on [], ``has: rows.0.id``
    on []), or None when nothing was dropped. An empty list is the right answer when no row was ever created."""
    out = dict(expect)
    dropped = False
    ml = out.get("min_len")
    if isinstance(ml, dict) and int(ml.get("n", 0) or 0) >= 1:
        seq = dig(body, ml.get("path", ""))
        if isinstance(seq, list) and not seq:
            out.pop("min_len")
            dropped = True
    has = [p for p in out.get("has") or [] if not (dig(body, p) is _MISSING and _under_empty_list(body, p))]
    if len(has) != len(out.get("has") or []):
        out["has"], dropped = has, True
    eq = {p: w for p, w in (out.get("equals") or {}).items() if not (dig(body, p) is _MISSING and _under_empty_list(body, p))}
    if len(eq) != len(out.get("equals") or {}):
        out["equals"], dropped = eq, True
    return out if dropped else None


def baseline_steps(decls: list[dict[str, Any]], flags: dict[str, dict[str, Any]],
                   required_cols: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Probes every module needs, derived from the routes alone (no model involved)."""
    out: list[dict[str, Any]] = []
    for d in decls:
        method, path, fl = d["method"], d["path"], flags.get(d["function"], {})
        params = re.findall(r"\{(\w+)\}", path)
        label = f"{method} {path}"
        id_params = [p for p in params if ID_PARAM.match(p)]
        if id_params:
            for tag, val in (("missing_id", BAD_ID), ("junk_id", "abc")):
                p = path
                for name in params:
                    p = p.replace("{" + name + "}", str(val if name in id_params else 1))
                want: Any = 404 if (tag == "missing_id" and method in ("GET", "DELETE")) else [400, 404, 422]
                if tag == "junk_id":
                    want = [400, 404, 422]
                out.append({"id": f"{tag}:{label}", "method": method, "path": p, "body": {} if method in ("POST", "PUT", "PATCH") else None,
                            "query": {}, "expect": {"status": want}, "save": {}, "twice": False, "kind": "probe",
                            "label": label, "why": ("an unknown id must answer 404" if tag == "missing_id"
                                                    else "a malformed id must answer 4xx, not crash")})
        elif method == "GET" and not fl.get("external"):
            out.append({"id": f"list:{label}", "method": "GET", "path": path, "body": None, "query": {},
                        "expect": {"status": 200}, "save": {}, "twice": False, "kind": "probe", "label": label,
                        "why": "a list route answers 200 on empty tables"})
        if method in ("POST", "PUT", "PATCH") and not params and fl.get("inserts") and not fl.get("external"):
            req = [c for t in fl.get("tables", []) for c in required_cols.get(t, [])]
            if req:
                out.append({"id": f"empty_body:{label}", "method": method, "path": path, "body": {}, "query": {},
                            "expect": {"status": [400, 409, 422]}, "save": {}, "twice": False, "kind": "probe",
                            "label": label,
                            "why": f"an empty body must be refused (required: {', '.join(sorted(set(req))[:4])})"})
    return out[:MAX_PROBES]


Caller = Callable[..., Awaitable[dict[str, Any]]]     # call(method, path, query, body, read_only, headers=None)
Counter = Callable[[], Awaitable[dict[str, int]]]


def _preview(body: Any, limit: int = 2048) -> str:
    return json.dumps(body, default=str)[:limit]


async def run_steps(steps: list[dict[str, Any]], call: Caller, counts: Counter, flags_by_route: dict[str, dict[str, Any]],
                    decls: list[dict[str, Any]], *, writes_allowed: bool, time_left: Callable[[], float],
                    on_step: Callable[[str], Awaitable[None]] | None = None,
                    schemas: dict[str, Any] | None = None, infer: Callable[[Any], Any] | None = None,
                    initial_vars: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run plan steps, then probes. ``call(method, path, query, body, read_only)`` returns
    ``{status, body, execution_id, duration_ms, error_code}``. Results use the legacy self_test shape plus ``kind``,
    ``step`` and ``failure``; skipped steps carry ``skipped``."""
    vars_: dict[str, Any] = dict(initial_vars or {})
    polled = 0
    results: list[dict[str, Any]] = []
    failed_vars: set[str] = set()
    route_of = {}
    for d in decls:
        route_of[(d["method"], re.sub(r"\{\w+\}", "{}", d["path"]))] = d["function"]

    def flags_for(method: str, concrete: str) -> dict[str, Any]:
        segs = concrete.strip("/").split("/")
        for (m, shape), fn in route_of.items():
            ss = shape.strip("/").split("/")
            if m == method and len(ss) == len(segs) and all(a == "{}" or a == b for a, b in zip(ss, segs)):
                return flags_by_route.get(fn, {})
        return {}

    for st in steps:
        label = st.get("label") or f"{st['method']} {st['path']}"
        base = {"route": label, "step": st["id"], "kind": st["kind"]}
        if time_left() < 5:
            results.append({**base, "skipped": "time"})
            continue
        missing: list[str] = []
        path = _fill(st["path"], vars_, missing)
        body = _fill(st["body"], vars_, missing)
        query = _fill(st["query"], vars_, missing)
        if missing:
            skip = "depends_on_failed_step" if set(missing) & failed_vars else "unresolved_variable"
            results.append({**base, "skipped": skip, "detail": ", ".join(sorted(set(missing)))})
            continue
        fl = flags_for(st["method"], path)
        if st["method"] != "GET" and not writes_allowed:
            results.append({**base, "skipped": "dynamic_table_names"})
            continue
        if fl.get("external") and st["kind"] == "plan" and st["method"] != "GET":
            results.append({**base, "skipped": "external_effects"})
            continue
        if on_step:
            await on_step(f"{st['method']} {path}")
        read_only = bool(fl.get("external")) or not writes_allowed
        hdrs = st.get("headers") or None
        r = await call(st["method"], path, query, body, read_only, hdrs)
        if st["kind"] == "probe" and int(r["status"]) in (401, 403):
            results.append({**base, "skipped": "auth_required", "status": r["status"]})    # a guard answered: nothing to judge
            continue
        failure = check_expect(st["expect"], int(r["status"]), r.get("body"), vars_)
        note = None
        if failure is not None and st["method"] == "GET" and int(r["status"]) < 300:
            relaxed = empty_tolerant(st["expect"], r.get("body"))
            if relaxed is not None and check_expect(relaxed, int(r["status"]), r.get("body"), vars_) is None:
                have = await counts()
                read = fl.get("tables") or list(have)
                if sum(have.get(t, 0) for t in read) == 0:      # nothing was ever created: an empty list is correct
                    failure, note = None, "empty list accepted: the tables this route reads hold no rows at this point"
        if failure is None and st.get("twice"):
            after_first = await counts()
            r2 = await call(st["method"], path, query, body, read_only, hdrs)
            after_second = await counts()
            if int(r2["status"]) >= 500:
                failure = f"second identical call answered {r2['status']}"
            elif after_first != after_second:
                diff = {t: f"{after_first.get(t)} -> {after_second.get(t)}" for t in after_second
                        if after_first.get(t) != after_second.get(t)}
                failure = f"not idempotent: the second identical call changed row counts {diff}"
            elif int(r2["status"]) != int(r["status"]) and int(r2["status"]) >= 400:
                failure = f"second identical call answered {r2['status']} (first {r['status']})"
        if failure is None and st["method"] == "GET" and st["kind"] == "plan" and fl.get("writes") and polled < MAX_POLL_CHECKS:
            polled += 1          # a read that writes (lazy sync on poll) may write ONCE; the next identical read must add nothing
            after_first = await counts()
            r2 = await call(st["method"], path, query, body, read_only, hdrs)
            after_second = await counts()
            if int(r2["status"]) < 500 and after_first != after_second:
                diff = {t: f"{after_first.get(t)} -> {after_second.get(t)}" for t in after_second if after_first.get(t) != after_second.get(t)}
                failure = f"a repeated identical read changed row counts {diff} (history must not grow per poll)"
        ok = failure is None
        if ok and schemas is not None and infer is not None and 200 <= int(r["status"]) < 300 and label not in schemas \
                and isinstance(r.get("body"), (dict, list)):
            schemas[label] = infer(r["body"])          # the observed response shape of this route, for callers that need one
        if ok:
            for var, p in st["save"].items():
                val = dig_any(r.get("body"), str(p))
                if val is _MISSING:
                    failure, ok = f"cannot save '{var}': response has no '{p}'", False
                    failed_vars.add(var)
                else:
                    vars_[var] = val
        else:
            failed_vars.update(st["save"])
        results.append({**base, "ok": ok, "status": r["status"], "duration_ms": r.get("duration_ms", 0.0),
                        "error_code": r.get("error_code"), "body_preview": _preview(r.get("body")),
                        "execution_id": r.get("execution_id"),
                        **({"note": note} if note else {}),
                        "failure": None if ok else (failure + (f" ({st['why']})" if st.get("why") else "")),
                        **({} if ok else {"expected": st["expect"], "actual": {"status": r["status"], "body": _preview(r.get("body"), 400)}}),
                        "request": {"method": st["method"], "path": path, **({"body": body} if body else {}),
                                    **({"headers": hdrs} if hdrs else {})}})
    return results


def failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if r.get("ok") is False]


def failure_lines(results: list[dict[str, Any]], limit: int = 12) -> list[str]:
    out = []
    for r in failures(results)[:limit]:
        req = r.get("request") or {}
        out.append(f"- step '{r.get('step')}' [{r.get('kind')}] {req.get('method', '')} {req.get('path', r.get('route'))}"
                   f"{' body ' + json.dumps(req['body'], default=str)[:160] if req.get('body') else ''}: {r['failure']}"
                   f" | expected {json.dumps(r.get('expected'), default=str)[:200]}"
                   f" | actual status {r.get('status')}, response {str(r.get('body_preview'))[:200]}")
    return out


# ── project-level standing scenario: start run -> job finishes -> sync -> triage -> approve -> re-read the case ─────────

_BODY_VAR = re.compile(r"\b(\w+)\s*=\s*(?:\w+\.)?req\.json\(\)")
_LABEL_KEYS = {"label", "title", "name", "note", "reason", "comment", "description"}
_ACTOR_KEYS = {"actor", "by", "author", "who", "user", "decided_by"}
_DECISION_KEYS = {"decision", "action", "verdict", "outcome"}


def _noun(path: str) -> str:
    seg = next((p for p in path.strip("/").split("/") if p and not p.startswith("{")), "item")
    if seg.endswith("ies"):
        return seg[:-3] + "y"
    return seg.removesuffix("s")


def _fn_segments(source: str) -> dict[str, str]:
    """Source text per module-level function, plus (two levels of) the module-level helpers it calls, so a handler that starts
    its job through ``_start_job(...)`` still shows ``studio.jobs.start``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    own = {k: (ast.get_source_segment(source, n) or "") for k, n in fns.items()}

    def called(n: ast.AST) -> set[str]:
        return {c.func.id for c in ast.walk(n) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in fns}
    out: dict[str, str] = {}
    for k, n in fns.items():
        seen = {k}
        frontier = called(n) - seen
        text = own[k]
        for _ in range(2):
            nxt: set[str] = set()
            for h in sorted(frontier):
                if h not in seen:
                    seen.add(h)
                    text += "\n" + own[h]
                    nxt |= called(fns[h])
            frontier = nxt - seen
        out[k] = text
    return out


def body_keys(seg: str) -> list[str]:
    """Keys a handler reads from its JSON body (``b = req.json() ...; b.get("k")`` / ``b["k"]``)."""
    out: list[str] = []
    for v in _BODY_VAR.findall(seg):
        for k in re.findall(rf"\b{re.escape(v)}\.(?:get|pop)\(\s*[\"'](\w+)[\"']|\b{re.escape(v)}\[\s*[\"'](\w+)[\"']\s*\]", seg):
            key = k[0] or k[1]
            if key and key not in out:
                out.append(key)
    for v in _BODY_VAR.findall(seg):                       # helper(body, "started_by") / helper(body): a claimed-actor helper
        for k in re.findall(rf"\w+\(\s*{re.escape(v)}\s*,\s*[\"'](\w+)[\"']", seg):
            if k not in out:
                out.append(k)
        if re.search(rf"\b_?actor\(\s*{re.escape(v)}\s*\)", seg) and "actor" not in out:
            out.append("actor")
    if re.search(r"\b_?actor\(\s*req\.json\(\)", seg) and "actor" not in out:
        out.append("actor")
    for k in re.findall(r"req\.json\(\)\.get\(\s*[\"'](\w+)[\"']", seg):
        if k not in out:
            out.append(k)
    return out


def _table_for(stem: str, tables: list[str]) -> str | None:
    for cand in (stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else None, stem):
        if cand and cand in tables:
            return cand
    return next((t for t in tables if t.startswith(stem)), None)


def default_body(seg: str, tables: list[str]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for k in body_keys(seg):
        if k in _LABEL_KEYS:
            body[k] = "standing check"
        elif k in _ACTOR_KEYS or k.endswith("_by"):
            body[k] = "standing-check"
        elif k in _DECISION_KEYS:
            body[k] = "approve"
        elif k.endswith("_ids") and (t := _table_for(k[:-4], tables)):
            body[k] = [f"{{{t}_id}}"]
        elif k.endswith("_id") and k != "id" and (t := _table_for(k[:-3], tables)):
            body[k] = f"{{{t}_id}}"
    return body


def standing_roles(mods: list[tuple[str, str, list[dict[str, Any]]]]) -> dict[str, dict[str, Any]]:
    """Find the routes that play each part in a job-backed flow, from the code alone:
    ``start`` (jobs.start test-run), ``sync``, ``run_read``, ``triage`` (jobs.start triage), ``finding_read``, ``approve``,
    ``case_read``. Each: ``{method, path, seg, module}``."""
    cands: list[dict[str, Any]] = []
    for name, source, decls in mods:
        segs = _fn_segments(source)
        for d in decls:
            cands.append({"method": d["method"], "path": d["path"], "module": name, "fn": d["function"], "seg": segs.get(d["function"], "")})
    jobs_re = re.compile(r"\bstudio\.jobs\.start\(|\bjobs\.start\(")
    triage_re = re.compile(r"[\"']triage[\"']")

    def first(pred):
        return next((c for c in cands if pred(c)), None)
    roles: dict[str, dict[str, Any]] = {}
    tri = first(lambda c: c["method"] == "POST" and jobs_re.search(c["seg"]) and triage_re.search(c["seg"]))
    start = first(lambda c: c["method"] == "POST" and jobs_re.search(c["seg"]) and not triage_re.search(c["seg"])
                  and re.search(r"test-run|run", c["seg"] + c["path"]))
    if tri:
        roles["triage"] = tri
    if start:
        roles["start"] = start
        seg0 = start["path"].strip("/").split("/")[0]
        sync = first(lambda c: c["method"] == "POST" and re.search(r"sync", c["path"] + c["fn"]) and c["path"].strip("/").startswith(seg0)) \
            or first(lambda c: c["method"] == "GET" and re.search(r"status|sync", c["path"]) and c["path"].strip("/").startswith(seg0)
                     and "{" in c["path"])
        if sync:
            roles["sync"] = sync
        run_read = first(lambda c: c["method"] == "GET" and re.fullmatch(rf"/{re.escape(seg0)}/\{{\w+\}}", c["path"]))
        if run_read:
            roles["run_read"] = run_read
    if tri:
        base = tri["path"].rsplit("/", 1)[0]
        fr = first(lambda c: c["method"] == "GET" and c["path"] in (base + "/finding", base + "/findings")) \
            or first(lambda c: c["method"] == "GET" and re.fullmatch(r"/findings/\{\w+\}", c["path"]))
        if fr:
            roles["finding_read"] = fr
    appr = first(lambda c: c["method"] in ("POST", "PUT", "PATCH") and re.search(r"approve|accept|apply", c["path"] + c["fn"], re.IGNORECASE)) \
        or first(lambda c: c["method"] in ("POST", "PUT", "PATCH") and re.search(r"decision|decide", c["path"] + c["fn"], re.IGNORECASE))
    if appr:
        roles["approve"] = appr
    cr = first(lambda c: c["method"] == "GET" and re.fullmatch(r"/\w*case\w*/\{\w+\}", c["path"]))
    if cr:
        roles["case_read"] = cr
    return roles


STANDING_REQUIRED = ("start", "run_read", "triage", "approve", "case_read")


def standing_plan(roles: dict[str, dict[str, Any]], tables: list[str], overrides: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """The model-facing plan steps for the standing scenario, and the roles that could not be found (empty when complete).
    ``overrides``: ``{role: {call?, body?, expect?}}`` replaces a discovered route or body."""
    ov = overrides or {}

    def role(name: str) -> dict[str, Any] | None:
        o = ov.get(name) if isinstance(ov.get(name), dict) else {}
        r = roles.get(name)
        if o.get("call") and _CALL_RE.match(str(o["call"])):
            m = _CALL_RE.match(o["call"])
            r = {"method": m.group(1).upper(), "path": m.group(2), "seg": (r or {}).get("seg", ""), "module": (r or {}).get("module")}
        return r

    def call(name: str, r: dict[str, Any]) -> str:
        path = r["path"]
        noun = _noun(path)
        path = re.sub(r"\{id\}", "{" + noun + "_id}", path)
        return f"{r['method']} {path}"

    def body(name: str, r: dict[str, Any]) -> dict[str, Any] | None:
        o = ov.get(name) if isinstance(ov.get(name), dict) else {}
        if "body" in o:
            return o["body"]
        b = default_body(r.get("seg", ""), tables)
        return b or ({} if r["method"] in ("POST", "PUT", "PATCH") else None)

    found = {n: role(n) for n in (*STANDING_REQUIRED, "sync", "finding_read")}
    missing = [n for n in STANDING_REQUIRED if not found[n]]
    if missing:
        return [], missing
    ok2 = {"status": "2xx"}
    plan: list[dict[str, Any]] = []

    def add(sid: str, name: str, expect: dict[str, Any] | None = None, save: dict[str, str] | None = None, twice: bool = False) -> None:
        r = found[name]
        o = ov.get(name) if isinstance(ov.get(name), dict) else {}
        step: dict[str, Any] = {"id": f"standing:{sid}", "call": call(name, r), "expect": o.get("expect") or expect or ok2}
        b = body(name, r)
        if b is not None:
            step["body"] = b
        if save:
            step["save"] = save
        if twice:
            step["twice"] = True
        plan.append(step)
    add("start_run", "start", save={"run_id": "run_id|id|~run_id|~id"})
    if found["sync"]:
        add("sync_run", "sync", twice=found["sync"]["method"] == "POST" and "{" not in found["sync"]["path"])
    add("read_run", "run_read", save={"result_id": "~results.0.id|~result_id", "case_id": "~results.0.case_id|~case_id"})
    add("case_before", "case_read", save={"steps_before": "steps|~steps"})
    add("triage", "triage", save={"finding_id": "finding_id|id|~finding_id|~id"})
    if found["sync"]:
        add("sync_triage", "sync")
    if found["finding_read"]:
        add("read_finding", "finding_read")
    bodies = (ov.get("approve") or {}).get("bodies") if isinstance(ov.get("approve"), dict) else None
    if isinstance(bodies, list) and bodies:               # e.g. two different approvers for a risky case
        for i, b in enumerate(bodies, 1):
            r = found["approve"]
            plan.append({"id": f"standing:approve{'' if i == 1 else '_' + str(i)}", "call": call("approve", r), "body": b, "expect": ok2})
    else:
        add("approve", "approve")
    add("case_after", "case_read", expect={"status": 200, "not_equals": {"steps|~steps": "{steps_before}"}})
    return plan, []
