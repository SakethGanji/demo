"""Test mode of the runner (Stage 3A, CONTRACTS-STAGE3A §1-§3).

Pure helpers plus the mock machinery the runner calls:

* ``TestRunOptions``: what ``WorkflowRunner.run(test_options=...)`` takes.
* The frozen clock and seeded RNG, exposed through context variables so the expression helpers and the
  AI tools (which have no access to the ExecutionContext) read the same values as the nodes.
* Mock resolution (``plan_mock``): items / http / error / timeout / smart ``rules`` mocks, with
  ``{{ }}`` expressions resolved per input item.
* ``rename_node_in_tests`` and ``locked_test_ids``: the pure parts of the test-data rules on save.
"""

from __future__ import annotations

import copy
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from .types import NodeData

DEFAULT_NOW = "2026-01-01T09:00:00+00:00"
DEFAULT_SEED = 1

#: The frozen "now" of the current test run (None outside test mode).
frozen_now_var: ContextVar[datetime | None] = ContextVar("frozen_now", default=None)
#: The seeded ``random.Random`` of the current test run (None outside test mode).
test_rng_var: ContextVar[Any] = ContextVar("test_rng", default=None)


def current_time(tz: timezone | None = None) -> datetime:
    """``datetime.now(tz)``, or the frozen clock inside a test run."""
    frozen = frozen_now_var.get()
    if frozen is None:
        return datetime.now(tz)
    return frozen.astimezone(tz) if tz is not None else frozen


def current_rng() -> Any:
    """The test run's seeded RNG, or the ``random`` module itself outside test mode."""
    rng = test_rng_var.get()
    if rng is None:
        import random

        return random
    return rng


def parse_now(value: str | None) -> datetime:
    """An ISO timestamp from a test / test_defaults (``Z`` allowed); default ``DEFAULT_NOW``."""
    raw = value or DEFAULT_NOW
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class TestRunOptions:
    """Options of one test run. ``mocks`` are the test's own, ``default_mocks`` the workflow's ``test_defaults``."""

    __test__ = False  # not a pytest class

    mocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: parse_now(None))
    seed: int = DEFAULT_SEED
    persist: bool = True
    default_mocks: dict[str, dict[str, Any]] = field(default_factory=dict)


class MockedNodeError(Exception):
    """The error a ``{error: {message, code}}`` mock raises."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class UnmockedEffectError(Exception):
    """A node with an outside effect had no mock. Bypasses retries and on_error; stops the run."""

    code = "TEST_UNMOCKED_EFFECT"

    def __init__(self, node: str, effect: str) -> None:
        super().__init__(f'"{node}" calls an outside system ({effect}) and has no mock in this test')
        self.details = {"node": node, "effect": effect, "fixes": ["generate_from_spec", "use_last_real_reply"]}


class BadMockError(Exception):
    """A mock that can't apply to the node it is set on (e.g. an http mock on a Set node)."""

    code = "TEST_BAD_MOCK"

    def __init__(self, node: str, message: str) -> None:
        super().__init__(message)
        self.details = {"node": node}


class TestNotFoundError(Exception):
    """An approve / unlock / run named a test id the workflow doesn't have (404)."""

    __test__ = False

    def __init__(self, test_id: str) -> None:
        super().__init__(f"Test not found: {test_id}")
        self.message = f"Test not found: {test_id}"
        self.test_id = test_id


class LockedTestsError(Exception):
    """A save touched locked tests (409 on PUT)."""

    def __init__(self, test_ids: list[str]) -> None:
        super().__init__("Locked tests can't be changed")
        self.message = "Locked tests can't be changed"
        self.test_ids = test_ids


# ── mocks ────────────────────────────────────────────────────────────────────────────────────

MockKind = Literal["items", "http", "error", "timeout", "rules", "unknown"]


def mock_kind(mock: Any) -> MockKind:
    if not isinstance(mock, dict):
        return "unknown"
    for kind in ("rules", "items", "http", "error", "timeout"):
        if kind in mock and not (kind == "timeout" and not mock[kind]):
            return kind  # type: ignore[return-value]
    if "default" in mock:
        return "rules"
    return "unknown"


def has_expression(value: Any) -> bool:
    """True when any string inside ``value`` holds ``{{ }}``."""
    if isinstance(value, str):
        return "{{" in value
    if isinstance(value, dict):
        return any(has_expression(v) for v in value.values())
    if isinstance(value, list):
        return any(has_expression(v) for v in value)
    return False


def _rule_matches(when: dict[str, Any], item_json: dict[str, Any]) -> bool:
    from .assertions import compare_value, resolve_path

    found, actual = resolve_path(item_json, str(when.get("path") or ""))
    passed, _ = compare_value(str(when.get("op") or "eq"), found, actual, when.get("value"))
    return passed


def pick_leaf(mock: Any, item_json: dict[str, Any]) -> dict[str, Any] | None:
    """The concrete (non-rules) mock that applies to one input item, or None when nothing matches."""
    depth = 0
    while mock_kind(mock) == "rules":
        depth += 1
        if depth > 10:
            return None
        nxt = None
        for rule in mock.get("rules") or []:
            if isinstance(rule, dict) and _rule_matches(rule.get("when") or {}, item_json):
                nxt = rule.get("then")
                break
        if nxt is None:
            nxt = mock.get("default")
        if nxt is None:
            return None
        mock = nxt
    return mock if mock_kind(mock) != "unknown" else None


def _resolve(value: Any, context: Any, input_items: list[NodeData], idx: int) -> Any:
    """Resolve ``{{ }}`` in a mock value against one input item, like node parameters."""
    if not has_expression(value):
        return copy.deepcopy(value)
    from .expression_engine import ExpressionEngine, expression_engine

    ectx = ExpressionEngine.create_context(
        input_items, context.node_states, context.execution_id, idx, variables=context.variables
    )
    return expression_engine.resolve(copy.deepcopy(value), ectx)


@dataclass
class MockPlan:
    """What a mock does for one job: emit ``outputs`` or raise ``error``; an ``http`` plan feeds the HttpRequest node."""

    kind: Literal["items", "http", "raise"]
    outputs: dict[str, list[NodeData]] = field(default_factory=dict)
    error: Exception | None = None
    leaves: list[dict[str, Any]] = field(default_factory=list)  # http: one concrete mock per input item


def _raise_of(leaf: dict[str, Any], node: str, context: Any, items: list[NodeData], idx: int) -> Exception:
    if leaf.get("timeout"):
        return TimeoutError(f"TEST_MOCK_TIMEOUT: {node}")
    err = leaf.get("error") or {}
    return MockedNodeError(str(_resolve(err.get("message", "Mocked error"), context, items, idx)), err.get("code"))


def plan_mock(mock: Any, node: str, node_type: str, input_items: list[NodeData], context: Any) -> MockPlan | None:
    """Turn a mock into a plan for this job; None when it doesn't apply (rules without a match: try the next level)."""
    kind = mock_kind(mock)
    if kind == "unknown":
        return None
    items = input_items or [NodeData(json={})]
    if kind == "items":
        port = mock.get("port") or "main"
        raw = mock.get("items") or []
        if not has_expression(raw):
            return MockPlan("items", {port: [NodeData(json=copy.deepcopy(i)) for i in raw]})
        out: list[NodeData] = []
        for idx in range(len(items)):
            out.extend(NodeData(json=_resolve(i, context, items, idx)) for i in raw)
        return MockPlan("items", {port: out})

    leaves: list[dict[str, Any]] = []
    for item in items:
        leaf = pick_leaf(mock, item.json if isinstance(item.json, dict) else {})
        if leaf is None:
            return None
        leaves.append(leaf)

    # a rules leaf behaves like the same mock at top level: all-`items` leaves are emitted directly, even on HttpRequest
    if node_type == "HttpRequest" and not all(mock_kind(leaf) == "items" for leaf in leaves):
        return MockPlan("http", leaves=leaves)
    outputs: dict[str, list[NodeData]] = {}
    for idx, leaf in enumerate(leaves):
        lk = mock_kind(leaf)
        if lk in ("error", "timeout"):
            return MockPlan("raise", error=_raise_of(leaf, node, context, items, idx))
        if lk == "http":
            return MockPlan("raise", error=BadMockError(node, "http mocks only work on HttpRequest nodes"))
        port = leaf.get("port") or "main"
        raw = leaf.get("items") or []
        # a plain `items` leaf of a rules mock still answers once per input item (that is what a rule is for)
        outputs.setdefault(port, []).extend(NodeData(json=_resolve(i, context, items, idx)) for i in raw)
    return MockPlan("items", outputs)


def http_mock_client(plan: MockPlan, node: str, input_items: list[NodeData], context: Any) -> httpx.AsyncClient:
    """An httpx client whose transport records each request and answers from the plan (HttpRequest only)."""
    items = input_items or [NodeData(json={})]
    counter = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        idx = counter["n"]
        counter["n"] += 1
        raw = request.content
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode("utf-8", "replace")
        context.test_requests.setdefault(node, []).append(
            {"method": request.method, "url": str(request.url), "headers": dict(request.headers), "body": body}
        )
        leaf = plan.leaves[min(idx, len(plan.leaves) - 1)]
        lk = mock_kind(leaf)
        i = min(idx, len(items) - 1)
        if lk in ("error", "timeout"):
            raise _raise_of(leaf, node, context, items, i)
        if lk == "items":
            first = (leaf.get("items") or [{}])[0]
            return httpx.Response(200, json=_resolve(first, context, items, i))
        spec = leaf.get("http") or {}
        status = int(spec.get("status", 200))
        headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
        payload = _resolve(spec.get("body"), context, items, i)
        if payload is None:
            return httpx.Response(status, headers=headers)
        if isinstance(payload, str):
            return httpx.Response(status, content=payload.encode(), headers=headers)
        return httpx.Response(status, json=payload, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def refusing_client() -> httpx.AsyncClient:
    """The shared client of a test run: any request that reaches it is a bug in the test setup, never real traffic."""

    async def refuse(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"Network access is disabled in test mode ({request.method} {request.url})")

    return httpx.AsyncClient(transport=httpx.MockTransport(refuse))


# ── renames and locks ────────────────────────────────────────────────────────────────────────


def rename_node_in_tests(tests: list[dict[str, Any]], old: str, new: str) -> list[dict[str, Any]]:
    """A copy of ``tests`` with node ``old`` renamed to ``new`` in targets, mocks, expectations and covers."""
    out = copy.deepcopy(tests or [])
    if old == new:
        return out
    for t in out:
        target = t.get("target")
        if isinstance(target, dict):
            for key in ("node", "from", "to"):
                if target.get(key) == old:
                    target[key] = new
        if isinstance(t.get("mocks"), dict):
            t["mocks"] = {(new if k == old else k): v for k, v in t["mocks"].items()}
        for a in t.get("expect") or []:
            if isinstance(a, dict) and a.get("node") == old:
                a["node"] = new
        if isinstance(t.get("covers"), list):
            t["covers"] = [new if c == old else c for c in t["covers"]]
    return out


def rename_node_in_test_defaults(defaults: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    """A copy of ``test_defaults`` with the default mock of ``old`` moved to ``new``."""
    out = copy.deepcopy(defaults or {})
    if old != new and isinstance(out.get("mocks"), dict):
        out["mocks"] = {(new if k == old else k): v for k, v in out["mocks"].items()}
    return out


_APPROVAL_FIELDS = ("status", "locked", "approved_by", "approved_at")


def locked_test_ids(stored: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[str]:
    """Ids of tests a save may not touch: locked tests that changed or vanished, and any test whose
    status/locked/approved_* differ from what is stored (a new test must come in as a plain draft).

    Both lists must already be normalised the same way (``TestCaseSchema.model_dump``); ``stored`` must
    already have the ``node_renames`` applied.
    """
    old = {t["id"]: t for t in stored}
    new = {t["id"]: t for t in incoming}
    bad: list[str] = []
    for tid, t in old.items():
        if t.get("locked") and (tid not in new or new[tid] != t):
            bad.append(tid)
    for tid, t in new.items():
        before = old.get(tid, {"status": "draft", "locked": False})
        if any(t.get(f) != before.get(f) for f in _APPROVAL_FIELDS) and tid not in bad:
            bad.append(tid)
    return bad
