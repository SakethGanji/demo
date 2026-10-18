"""Variations / fuzzing (Stage 3B, CONTRACTS-STAGE3B §3). Nothing here saves anything.

``fuzz_workflow`` builds many odd inputs from a base input, runs each once in test mode (mocks from
``test_defaults``, ``persist=False``, frozen clock and seed) through the Stage 3A runner, and reports what broke:

* ``node_error``: a node ended with status ``error``;
* ``contract``: a node received or emitted data that breaks its contract;
* ``crash``: the run itself raised (or timed out).

Findings are deduplicated by ``(kind, node, message without digits)``; the smallest input is kept and ``count``
says how many inputs hit it. Every finding carries a ready draft ``TestCase`` (never saved) that fails until the
bug is fixed. The generators are deterministic for a given ``seed``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import re
from time import perf_counter
from typing import Any, Awaitable, Callable

from pydantic import ValidationError as PydanticValidationError

from ..core.exceptions import ValidationError, WorkflowNotFoundError
from ..engine import shapes as sh
from ..engine.data_categories import ABSENT, is_absent, values_for
from ..engine.test_recipes import stable_id
from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition
from ..schemas.workflow import normalize_tests
from .check_lookups import build_lookups
from .mock_gen import example_from_shape
from .partial_run import target_run_options
from .test_runner import _run_one, _setup
from .test_suggest import AuthoringError
from .workflow_checker import check_workflow

DEFAULT_COUNT = 60
MAX_COUNT = 200
TIME_BUDGET_S = 20.0
_MAX_DEPTH = 3
_EDGE_CATEGORIES = ("boundary", "missing", "wrong_type", "unusual")
_ODD_VALUES: list[tuple[Any, str]] = [
    (0, "boundary"), (-1, "boundary"), ("", "boundary"), ("x" * 300, "unusual"),
    ("héllo 日本語 😀", "unusual"), (None, "missing"), ([], "unusual"), ({}, "unusual"),
]

Path = tuple[str, ...]
Proposer = Callable[..., Awaitable[list[list[dict[str, Any]]]]]


def _label(path: Path) -> str:
    return ".".join(path)


def _canon(items: list[Any]) -> str:
    return json.dumps(items, sort_keys=True, default=repr, separators=(",", ":"))


def _walk(value: Any, path: Path = ()) -> list[Path]:
    """Every dict key path of ``value`` (objects descend, arrays and scalars are leaves)."""
    out: list[Path] = []
    if isinstance(value, dict) and len(path) < _MAX_DEPTH:
        for key, child in value.items():
            p = (*path, str(key))
            out.append(p)
            out.extend(_walk(child, p))
    return out


def _get(item: Any, path: Path) -> Any:
    cur = item
    for key in path:
        cur = cur[key]
    return cur


def _apply(base: dict[str, Any], changes: list[tuple[Path, Any]]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for path, value in changes:
        parent = _get(out, path[:-1])
        if is_absent(value):
            parent.pop(path[-1], None)
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return out


def _options_for(base: dict[str, Any], shape: Any, path: Path) -> list[tuple[Any, str]]:
    """Every value worth trying for one field: ``(value, category)``; the field's own current value is left out."""
    current = _get(base, path)
    field_shape = sh.lookup(shape, path) if shape is not None else None
    if field_shape is None or sh.is_unknown(field_shape):
        field_shape = sh.from_value(current)
    vals = values_for(field_shape, path[-1])
    out: list[tuple[Any, str]] = []
    seen: list[Any] = []
    for cat in _EDGE_CATEGORIES:
        for v in vals.get(cat, []):
            out.append((v, cat))
    out.extend(_ODD_VALUES)
    unique: list[tuple[Any, str]] = []
    for v, cat in out:
        if not is_absent(v) and type(v) is type(current) and v == current:
            continue
        if any(type(v) is type(s) and v == s for s in seen):
            continue
        seen.append(v)
        unique.append((v, cat))
    return unique


def _base_input(
    definition: dict[str, Any], target: dict[str, Any], shapes: dict[str, Any], start: str,
) -> tuple[dict[str, Any], Any]:
    """``(base item, shape the fields are read from)``: the first matching test's first input item, else an example."""
    node_shape = shapes.get(start) or {}
    if target.get("kind") == "node":
        shape = sh.input_shape(node_shape.get("inputs") or {})
    else:
        shape = (node_shape.get("outputs") or {}).get("main")
    # a replay test's input is real production data: it never seeds fuzzing (and so never reaches the AI model)
    usable = [t for t in normalize_tests(definition.get("tests")) if t.get("source") != "replay"]
    for t in usable:
        items = (t.get("input") or {}).get("items") or []
        if items and isinstance(items[0], dict) and (t.get("target") or {}).get("kind") == target.get("kind") \
                and (t.get("target") or {}).get("node") == target.get("node"):
            return copy.deepcopy(items[0]), shape
    for t in usable:
        items = (t.get("input") or {}).get("items") or []
        if items and isinstance(items[0], dict) and target.get("kind") == "workflow":
            return copy.deepcopy(items[0]), shape
    example = example_from_shape(shape)
    return (copy.deepcopy(example) if isinstance(example, dict) else {}), shape


class _Candidate:
    __slots__ = ("items", "changes", "gen", "category", "ai")

    def __init__(self, items: list[Any], changes: list[tuple[Path, Any]], gen: str, category: str, ai: bool = False) -> None:
        self.items, self.changes, self.gen, self.category, self.ai = items, changes, gen, category, ai

    @property
    def field(self) -> str | None:
        return ", ".join(_label(p) for p, _ in self.changes) or None


def _generate(
    base: dict[str, Any], shape: Any, count: int, seed: int, ai_items: list[list[Any]],
) -> list[_Candidate]:
    """The deterministic input pool for ``seed``: baseline, AI inputs, one-field changes, then seeded 2-3 field combos."""
    rng = random.Random(seed)
    seen: set[str] = set()
    pool: list[_Candidate] = []

    def add(c: _Candidate) -> bool:
        key = _canon(c.items)
        if key in seen:
            return False
        seen.add(key)
        pool.append(c)
        return True

    add(_Candidate([copy.deepcopy(base)], [], "baseline", "normal"))
    for items in ai_items[: max(count - 1, 0)]:
        add(_Candidate(copy.deepcopy(items), [], "ai", "unusual", ai=True))

    paths = _walk(base)
    options = {p: _options_for(base, shape, p) for p in paths}
    removals: list[_Candidate] = []
    others: list[_Candidate] = []
    for p in paths:
        removals.append(_Candidate([_apply(base, [(p, ABSENT)])], [(p, ABSENT)], "missing", "missing"))
    for p in paths:
        for v, cat in options[p]:
            if is_absent(v):
                continue
            others.append(_Candidate([_apply(base, [(p, v)])], [(p, v)], "edge", cat))

    budget = count - len(pool)
    if budget <= 0:
        return pool[:count]
    reserve = budget // 4 if len(removals) + len(others) > budget else 0
    room = budget - reserve
    singles = removals[:room]
    rest = room - len(singles)
    if rest > 0:
        if len(others) > rest:
            picked = sorted(rng.sample(range(len(others)), rest))
            singles += [others[i] for i in picked]
        else:
            singles += others
    for c in singles:
        add(c)

    if len(paths) >= 2:
        attempts = 0
        while len(pool) < count and attempts < count * 20:
            attempts += 1
            k = min(rng.choice((2, 3)), len(paths))
            chosen = rng.sample(paths, k)
            if any(a != b and a == b[: len(a)] for a in chosen for b in chosen):
                continue  # one field is inside the other
            changes: list[tuple[Path, Any]] = []
            first_cat = "unusual"
            for i, p in enumerate(chosen):
                opts = [(ABSENT, "missing"), *options[p]]
                v, cat = rng.choice(opts)
                if i == 0:
                    first_cat = cat
                changes.append((p, v))
            add(_Candidate([_apply(base, changes)], changes, "random", first_cat))
    return pool[:count]


_DIGITS = re.compile(r"\d+")


def _message_key(message: str) -> str:
    return _DIGITS.sub("", message or "")


def _failures_of(result: dict[str, Any]) -> list[tuple[str, str | None, dict[str, Any]]]:
    """``[(kind, node, error)]`` of one run: node errors, contract breaks and a broken run."""
    out: list[tuple[str, str | None, dict[str, Any]]] = []
    for f in result.get("failures") or []:
        kind = f.get("kind")
        if kind == "node_error":
            out.append(("node_error", f.get("node"), {"message": f.get("message") or ""}))
        elif kind == "contract":
            err: dict[str, Any] = {"message": f.get("message") or ""}
            if f.get("path"):
                err["path"] = f["path"]
            out.append(("contract", f.get("node"), err))
        elif kind == "setup" and str(f.get("message", "")).startswith("The test could not run"):
            out.append(("crash", None, {"message": f["message"]}))
    return out


def _size(c: _Candidate) -> tuple[int, int]:
    return (len(c.changes), len(_canon(c.items)))


def _draft_test(
    finding: dict[str, Any], cand: _Candidate, target: dict[str, Any], start: str, taken_ids: set[str],
) -> dict[str, Any]:
    node = finding["node"] or (target.get("node") if target.get("kind") == "node" else start)
    field = finding["field"]
    what = f"{field} {_describe(cand)}" if field else "input"
    salt = 0
    while True:
        tid = stable_id("fuzz", finding["kind"], finding["node"], _message_key(finding["error"]["message"]), cand.items, salt)
        if tid not in taken_ids:
            break
        salt += 1
    taken_ids.add(tid)
    test: dict[str, Any] = {
        "id": tid,
        "name": f"Fuzz: {node} survives {what}"[:120],
        "description": f"Found by fuzzing: {finding['error']['message']}"[:500],
        "target": {"kind": "node", "node": target["node"]} if target.get("kind") == "node" else {"kind": "workflow"},
        "input": {"items": copy.deepcopy(cand.items)},
        "mocks": {},
        "expect": [{"node": node, "op": "status", "value": "success"}],
        "category": cand.category if cand.category in ("boundary", "missing", "wrong_type", "unusual") else "unusual",
        "covers": [node],
        "status": "draft", "locked": False, "source": "ai" if cand.ai else "fuzz",
    }
    if field:
        test["field"] = field
    return test


def _describe(cand: _Candidate) -> str:
    if not cand.changes:
        return ""
    parts = []
    for _, v in cand.changes:
        parts.append("missing" if is_absent(v) else f"= {json.dumps(v, ensure_ascii=False, default=repr)[:30]}")
    return " and ".join(parts)


async def fuzz_workflow(
    repo: Any, workflow_id: str, *, definition: dict[str, Any] | None = None, target: dict[str, Any] | None = None,
    count: int = DEFAULT_COUNT, seed: int = 1, ai: bool = False, propose: Proposer | None = None,
    time_budget_s: float = TIME_BUDGET_S,
) -> dict[str, Any]:
    """Fuzz the stored draft (or the unsaved ``definition``); returns ``{runs, seed, findings, inputs_tried, ai_inputs}``."""
    stored = await repo.get(workflow_id)
    if not stored:
        raise WorkflowNotFoundError(workflow_id)
    definition = copy.deepcopy(definition) if definition is not None else workflow_to_definition(stored.workflow)
    count = max(1, min(int(count), MAX_COUNT))
    target = target or {"kind": "workflow"}
    if target.get("kind") not in ("workflow", "node"):
        raise AuthoringError(422, "target must be {kind: workflow} or {kind: node, node}")
    workflow = definition_to_workflow(definition, stored.name, stored.id, stored.workflow.description)
    try:
        opts = target_run_options(workflow, target, [])
    except ValidationError as e:
        raise AuthoringError(422, e.message)
    start = opts["start_node_name"]
    try:
        existing = {t["id"] for t in normalize_tests(definition.get("tests"))}
    except PydanticValidationError as e:
        raise AuthoringError(422, f"Invalid tests: {e.errors()[0]['msg']}")

    lookups = await build_lookups(repo._session, stored.project_id, None, team_id=getattr(stored, "team_id", None) or "default",
                                  workflow_id=stored.id, definition=definition)
    shapes = check_workflow(definition, lookups=lookups).get("shapes_by_node", {})
    base, shape = _base_input(definition, target, shapes, start)

    ai_items: list[list[Any]] = []
    if ai and propose is not None:
        proposed = await propose(definition, shapes, [copy.deepcopy(base)], n=10)
        ai_items = [list(v) for v in proposed if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)][:10]

    pool = _generate(base, shape, count, seed, ai_items)
    ai_used = sum(1 for c in pool if c.ai)

    setup = await _setup(repo, stored, definition, workflow)
    defaults = {**(definition.get("test_defaults") or {})}
    defaults.setdefault("seed", 1)
    runs = 0
    found: dict[tuple, dict[str, Any]] = {}
    best: dict[tuple, _Candidate] = {}
    started = perf_counter()
    for cand in pool:
        remaining = time_budget_s - (perf_counter() - started)
        if remaining <= 0:
            break
        test = {
            "id": "t_00000000", "name": "fuzz", "target": target, "input": {"items": copy.deepcopy(cand.items)},
            "mocks": {}, "expect": [], "now": defaults.get("now"), "seed": defaults["seed"],
        }
        timed_out = False
        try:  # the cap also bounds one hanging run, not only the gap between runs
            result = await asyncio.wait_for(
                _run_one(repo, stored, workflow, definition, test, defaults, setup, False), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            timed_out = True
            result = {"failures": [{"kind": "setup", "node": None,
                                    "message": "The test could not run: the input took too long (time budget reached)"}]}
        runs += 1
        for kind, node, error in _failures_of(result):
            key = (kind, node, _message_key(error["message"]))
            if key not in found:
                found[key] = {"kind": kind, "node": node, "error": error, "count": 0}
                best[key] = cand
            found[key]["count"] += 1
            if _size(cand) < _size(best[key]):
                best[key] = cand
                found[key]["error"] = error
        if timed_out:
            break

    findings: list[dict[str, Any]] = []
    for key, f in found.items():
        cand = best[key]
        finding = {
            "id": "", "kind": f["kind"], "node": f["node"], "field": cand.field,
            "input": {"items": copy.deepcopy(cand.items)}, "error": f["error"], "count": f["count"],
        }
        finding["test"] = _draft_test(finding, cand, target, start, existing)
        finding["id"] = finding["test"]["id"]
        findings.append(finding)
    return {"runs": runs, "seed": seed, "findings": findings, "inputs_tried": len(pool), "ai_inputs": ai_used}
