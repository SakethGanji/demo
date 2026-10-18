"""AI drafts tests for grid gaps, and AI input variations (Stage 3B, CONTRACTS-STAGE3B §2 and §3).

``draft_tests(repo, workflow_id, definition=None, cells=None, max_tests=8) -> {drafts, rejected, cells, model}``

The model is never trusted. Every test it returns is rebuilt server-side: a fresh ``t_`` id, ``status: draft``,
``locked: false``, ``source: "ai"``, no ``approved_*``; then validated (``normalize_tests``, node names, assertion
ops, mock formats). Invalid ones go back to the model once with the reasons. Each accepted draft is dry-run once
(``persist=False``) and the result attached as ``ai_run``. Nothing is saved and no existing test is ever touched.

``propose_variations(definition, shapes, base_items, n=10) -> list[list[dict]]`` asks the model for odd but plausible
input item lists; the fuzz service (owner B) adds them to its pool.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..engine import test_ai
from ..engine.test_ai import TestAIError, ask_json
from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition
from ..schemas.workflow import TEST_CATEGORIES, new_test_id, normalize_tests
from . import test_runner as tr
from .node_catalog import build_node_catalog
from .test_suggest import AuthoringError

logger = logging.getLogger(__name__)

ASSERTION_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "contains", "matches", "exists", "not_exists",
                 "count", "type", "one_of", "range", "status", "shape")
_STATUS_VALUES = ("success", "error", "no_output", "not_run")
_TYPE_VALUES = ("string", "integer", "number", "boolean", "object", "array", "null")
_MOCK_KEYS = ("items", "http", "error", "timeout", "rules", "approval")
_MAX_CELLS = 20
_MAX_PROMPT_SHAPES_CHARS = 60000

GRAMMAR: dict[str, Any] = {
    "answer_format": {"tests": ["<TestCase>", "..."]},
    "TestCase": {
        "name": "string, 1..120 chars, says what is checked",
        "description": "optional string",
        "target": {"workflow": {"kind": "workflow"}, "node": {"kind": "node", "node": "<node name>"},
                   "range": {"kind": "range", "from": "<node name>", "to": "<node name>"}},
        "input": {"items": ["<json object>"]},
        "input_meaning": "workflow target: the items the trigger emits (use the trigger's output shape from shapes_by_node). "
                         "node target: the items arriving at that node (use that node's input shape).",
        "mocks": "{<node name>: Mock} answers for outside steps (nodes whose effect is network or database), "
                 "overriding test_defaults.mocks for this test only. An outside step with no mock fails the test.",
        "expect": ["<Assertion>"],
        "category": list(TEST_CATEGORIES),
        "field": "optional: the input field the category is about, e.g. amount",
        "covers": "optional: node names this test counts for in the grid; default: the target node, or the nodes in expect",
    },
    "Mock": {
        "items": {"items": [{"any": "json"}], "port": "optional output port, default main"},
        "approval": {"approval": {"decision": "approved | rejected | timed_out", "comment": "optional", "decided_by": "optional"},
                     "port": "optional, default = the decision",
                     "note": "Approval nodes only. No items: the run emits each real incoming item plus `approval` on that port, as a real decision does"},
        "http": {"http": {"status": 200, "body": {"any": "json"}, "headers": {"k": "v"}}, "note": "HttpRequest nodes only"},
        "error": {"error": {"message": "text", "code": "optional"}},
        "timeout": {"timeout": True},
        "rules": {"rules": [{"when": {"path": "amount", "op": "gt", "value": 500}, "then": "<Mock>"}], "default": "<Mock>"},
    },
    "Assertion": {
        "shape": {"node": "<node name>", "port": "optional, default main", "item": "0 (default) | all | any",
                  "path": "dotted with [i], e.g. body.items[0].id; empty means the whole item",
                  "op": list(ASSERTION_OPS), "value": "depends on op", "subject": "output (default) | request | node"},
        "op_values": {
            "status": list(_STATUS_VALUES), "type": list(_TYPE_VALUES), "count": "integer: items on the port (path ignored)",
            "range": {"min": 0, "max": 10}, "one_of": ["list"], "matches": "regex (re.search)",
            "exists / not_exists": "no value", "eq neq gt gte lt lte contains": "json value",
            "shape": "optional Shape; omit to use the node's contract",
        },
        "subjects": {"output": "the items a node emitted", "request": "the HTTP request an HttpRequest node sent (path like body.order_id, params.x)",
                     "node": "only with op status"},
        "ports": "If has ports true and false; Switch has its case ports; every other node uses main",
    },
    "worked_example": {
        "tests": [{
            "name": "Large refund goes to the risk check",
            "description": "An order over the limit is sent to the risk API and the node answers success.",
            "target": {"kind": "workflow"},
            "input": {"items": [{"body": {"order_id": "o-1", "amount": 900, "reason": "late"}}]},
            "mocks": {"Risk API": {"http": {"status": 200, "body": {"decision": "review", "score": 0.7}}}},
            "expect": [
                {"node": "Amount over 500", "port": "true", "op": "count", "value": 1},
                {"node": "Risk API", "op": "status", "value": "success"},
                {"node": "Risk API", "subject": "request", "path": "body.order_id", "op": "eq", "value": "o-1"},
            ],
            "category": "normal", "covers": ["Amount over 500", "Risk API"],
        }, {
            "name": "Risk API is down",
            "target": {"kind": "node", "node": "Risk API"},
            "input": {"items": [{"order_id": "o-2", "amount": 700}]},
            "mocks": {"Risk API": {"error": {"message": "503 Service Unavailable"}}},
            "expect": [{"node": "Risk API", "op": "status", "value": "error"}],
            "category": "ext_error", "covers": ["Risk API"],
        }],
    },
}

_DRAFT_SYSTEM = """TEST_AI:DRAFT_TESTS
You write automated test cases for a workflow made of nodes. The user message is JSON data: the workflow
definition, the data shapes at every node (shapes_by_node), the node catalog entries for the node types used,
test_defaults.mocks, the existing tests (summary only), the grid cells that need a test (target_cells), and the
exact grammar (grammar) with a worked example.

Rules:
- Answer with ONLY one JSON object {"tests": [ ... ]}. No prose, no markdown fences.
- Write one test for every target cell: its category is the cell's category, and the cell's node is the target
  node (target {"kind":"node","node":...}) or is named in expect/covers. You may add a second test for a cell
  only if it checks something different. Never more than max_tests tests.
- Use ONLY node names that exist in the definition, exactly spelled. Use ONLY the assertion ops and mock formats in the grammar.
- Do not set id, status, locked, source or approved_by: the server does that. Never copy or edit an existing test.
- Input items must look like the shapes in shapes_by_node (field names and types), apart from the field a
  missing / wrong_type / boundary / unusual test deliberately breaks.
- Mocks: every outside step (effect network or database) that the test reaches needs a mock, either from
  test_defaults.mocks (already shared by all tests, so do not repeat it) or one you write for this test.
  ext_success / ext_error / ext_timeout / ext_empty tests override the mock of the outside step they are about:
  success = a good reply, error = {"error": {"message": ..., "code": ...}}, timeout = {"timeout": true}, empty = no items or an empty body.
  An http mock with a 4xx/5xx status is NOT a failure: an HttpRequest step emits {statusCode, body} and the run goes on, so never
  assert status error for it; use it only to test a workflow that itself checks statusCode.
- When a test makes a node fail on purpose, also assert {"node": X, "op": "status", "value": "error"}, otherwise
  the node error counts as an unexpected failure.
- Prefer assertions you can work out exactly from the input and the node parameters: status, count, exists,
  type, and eq on values copied or computed from the input. Never guess values you cannot derive.
- Think about what each node really does with the input before you write the expectation (read its parameters)."""

_VARIATIONS_SYSTEM = """TEST_AI:VARIATIONS
You invent unusual-but-plausible inputs to find bugs in a workflow. The user message is JSON data: the workflow
definition, the data shapes at every node (shapes_by_node), and base_items, an input that works today.

Rules:
- Answer with ONLY one JSON object {"variations": [{"items": [ {<json object>} ]}, ...]}. No prose, no markdown fences.
- Each variation is a list of items shaped like base_items (same field names), changed in a way real systems
  produce: missing or null fields, wrong types (number sent as a string), empty strings, very long text,
  unicode and emoji, negative or huge numbers, zero, float versus integer, extra unexpected fields, empty lists,
  nested values where a scalar is expected, whitespace-only strings, boundary values from the conditions in the nodes.
- Vary one or two things per variation so a failure points at its cause. Return exactly `count` variations (or fewer if you run out of ideas).
- Never include secrets or real personal data."""


# ── prompt building ──────────────────────────────────────────────────────────────────────────


def _compact_params(params: Any) -> list[dict[str, Any]]:
    out = []
    for p in params or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        item: dict[str, Any] = {"name": p["name"], "type": p.get("type")}
        if p.get("required"):
            item["required"] = True
        opts = [o.get("value") for o in p.get("options") or [] if isinstance(o, dict)]
        if opts:
            item["options"] = opts[:12]
        if p.get("description"):
            item["description"] = str(p["description"])[:100]
        out.append(item)
    return out


def catalog_for(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Catalog entries (compact) for the node types present in ``definition``."""
    present = {n.get("type") for n in definition.get("nodes") or [] if isinstance(n, dict)}
    return [
        {"type": e["type"], "effect": e["effect"], "parameters": _compact_params(e.get("parameters")),
         "categories": e["recipe"]["categories"], "checks": e["recipe"]["checks"]}
        for e in build_node_catalog() if e["type"] in present
    ]


def _shapes_for_prompt(shapes: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(shapes, default=str)) <= _MAX_PROMPT_SHAPES_CHARS:
        return shapes
    return {n: {"outputs": (s or {}).get("outputs", {})} for n, s in shapes.items()}  # inputs repeat upstream outputs


def _existing_summary(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": t["id"], "name": t["name"], "category": t.get("category"), "target": t.get("target"),
             "covers": tr._covers(t)} for t in tests]


# ── redaction: nothing that can hold a credential value goes to the model ───────────────────

REDACTED = "***"
_SECRET_KEY_RE = re.compile(r"(authorization|api[-_]?key|apikey|token|secret|password|passwd|cookie|credential|bearer)", re.I)
_SECRET_REF_RE = re.compile(r"\$secrets?\b|studio\.secret|\$credentials?\b", re.I)
_SECRET_QUERY_RE = re.compile(
    r"([?&;](?:[\w.-]*(?:authorization|api[-_]?key|apikey|token|secret|password|passwd|cookie|sig|signature)[\w.-]*)=)[^&#\s\"']*",
    re.I)
_BEARER_RE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}", re.I)


def redact_for_prompt(value: Any, secret_values: set[str] | None = None) -> Any:
    """A copy of ``value`` that is safe to send to an external model.

    Dropped or blanked: credential references (``_credentials``, ``{{$secrets...}}``), values under auth-like keys
    (authorization, api-key, x-api-key, token, secret, password, cookie, ...), credential-like URL query values,
    ``Bearer``/``Basic`` tokens, and any known secret value (``secret_values``) anywhere in a string.
    """
    values = sorted((v for v in (secret_values or set()) if isinstance(v, str) and len(v) >= 4), key=len, reverse=True)

    def _str(x: str) -> str:
        if _SECRET_REF_RE.search(x):
            return REDACTED
        x = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", x)
        x = _SECRET_QUERY_RE.sub(lambda m: m.group(1) + REDACTED, x)
        for v in values:
            if v in x:
                x = x.replace(v, REDACTED)
        return x

    def _walk(x: Any) -> Any:
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k == "_credentials":
                    continue
                if isinstance(k, str) and _SECRET_KEY_RE.search(k) and v not in (None, "", False):
                    out[k] = REDACTED
                else:
                    out[k] = _walk(v)
            nm = x.get("name", x.get("key"))
            if isinstance(nm, str) and _SECRET_KEY_RE.search(nm) and "value" in x:
                out["value"] = REDACTED
            return out
        if isinstance(x, list):
            return [_walk(i) for i in x]
        if isinstance(x, str):
            return _str(x)
        return x

    return _walk(copy.deepcopy(value))


def _definition_for_prompt(definition: dict[str, Any], secret_values: set[str] | None = None) -> dict[str, Any]:
    nodes = [{k: redact_for_prompt(n[k], secret_values) if k == "parameters" else n[k]
              for k in ("name", "type", "parameters", "on_error", "disabled") if n.get(k) not in (None, False, {})}
             for n in definition.get("nodes") or [] if isinstance(n, dict)]
    conns = [{k: c.get(k) for k in ("source_node", "source_output", "target_node")} for c in definition.get("connections") or []]
    return {"name": definition.get("name"), "description": definition.get("description"), "nodes": nodes, "connections": conns}


def build_draft_prompt(
    definition: dict[str, Any], shapes: dict[str, Any], existing: list[dict[str, Any]], cells: list[dict[str, str]], max_tests: int,
    secret_values: set[str] | None = None,
) -> str:
    """The user message of ``TEST_AI:DRAFT_TESTS``: JSON data, no prose."""
    return json.dumps({
        "definition": _definition_for_prompt(definition, secret_values),
        "shapes_by_node": _shapes_for_prompt(shapes),
        "node_catalog": catalog_for(definition),
        "test_defaults": {"mocks": redact_for_prompt((definition.get("test_defaults") or {}).get("mocks") or {}, secret_values)},
        "existing_tests": _existing_summary(existing),
        "target_cells": cells,
        "max_tests": max_tests,
        "grammar": GRAMMAR,
    }, default=str)


# ── sanitising ───────────────────────────────────────────────────────────────────────────────


def _check_mock(node: str, mock: Any, is_http: bool, is_approval: bool = False) -> str | None:
    if not isinstance(mock, dict) or not any(k in mock for k in _MOCK_KEYS):
        return f"mock for '{node}' must have one of items, http, error, timeout, rules (Approval: approval)"
    if "approval" in mock and "items" not in mock:
        if not is_approval:
            return f"mock for '{node}': a decision-only `approval` mock is for Approval nodes only"
        a = mock["approval"]
        if not isinstance(a, dict) or a.get("decision") not in ("approved", "rejected", "timed_out"):
            return f"mock for '{node}': approval.decision must be approved, rejected or timed_out"
    if "http" in mock:
        h = mock["http"]
        if not is_http:
            return f"mock for '{node}': http mocks are for HttpRequest nodes only"
        if not isinstance(h, dict) or not isinstance(h.get("status"), int) or isinstance(h.get("status"), bool):
            return f"mock for '{node}': http.status must be an integer"
    if "items" in mock and not (isinstance(mock["items"], list) and all(isinstance(i, dict) for i in mock["items"])):
        return f"mock for '{node}': items must be a list of objects"
    if "error" in mock and not (isinstance(mock["error"], dict) and isinstance(mock["error"].get("message"), str)):
        return f"mock for '{node}': error needs a message"
    return None


def _check_assertion(a: Any, names: set[str]) -> str | None:
    if not isinstance(a, dict):
        return "an assertion is not an object"
    node = a.get("node")
    if node not in names:
        return f"assertion names node '{node}', which doesn't exist"
    op = a.get("op")
    if op not in ASSERTION_OPS:
        return f"unknown assertion op '{op}' (allowed: {', '.join(ASSERTION_OPS)})"
    if a.get("subject", "output") not in ("output", "request", "node"):
        return f"unknown assertion subject '{a.get('subject')}'"
    v = a.get("value")
    if op == "status" and v not in _STATUS_VALUES:
        return f"status value must be one of {', '.join(_STATUS_VALUES)}"
    if op == "type" and v not in _TYPE_VALUES:
        return f"type value must be one of {', '.join(_TYPE_VALUES)}"
    if op == "count" and (not isinstance(v, int) or isinstance(v, bool)):
        return "count value must be an integer"
    if op == "one_of" and not isinstance(v, list):
        return "one_of value must be a list"
    if op == "range" and not (isinstance(v, dict) and ("min" in v or "max" in v)):
        return "range value must be {min?, max?}"
    return None


def inline_child_key(key: Any, inline: set[str]) -> bool:
    """True for ``"<node>/<child node>"`` where ``<node>`` runs its sub-workflow inline in this test (section 6.3)."""
    return isinstance(key, str) and any(key.startswith(n + "/") and len(key) > len(n) + 1 for n in inline)


def validate_test(test: dict[str, Any], definition: dict[str, Any]) -> str | None:
    """A reason the (already normalised) test is unusable for this workflow, or None."""
    nodes = {n["name"]: n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")}
    names = set(nodes)
    inline = set((test.get("subworkflows") or {}).keys())
    for n in sorted(inline):
        if n not in names:
            return f"subworkflows names node '{n}', which doesn't exist"
        if nodes[n].get("type") != "ExecuteWorkflow":
            return f"subworkflows names node '{n}', which is not an Execute Workflow node"
    target = test.get("target") or {}
    for key in ("node", "from", "to"):
        if key in target and target[key] not in names:
            return f"target names node '{target[key]}', which doesn't exist"
    for node, mock in (test.get("mocks") or {}).items():
        if node not in names and inline_child_key(node, inline):
            bad = _check_mock(node, mock, True)  # the child node's type is not known here (http allowed); its run reports a bad mock
            if bad:
                return bad
            continue
        if node not in names:
            return f"mock names node '{node}', which doesn't exist"
        bad = _check_mock(node, mock, nodes[node].get("type") == "HttpRequest", nodes[node].get("type") == "Approval")
        if bad:
            return bad
    for c in test.get("covers") or []:
        if c not in names:
            return f"covers names node '{c}', which doesn't exist"
    if not test.get("expect"):
        return "the test has no assertions"
    for a in test["expect"]:
        bad = _check_assertion(a, names | {a["node"]} if isinstance(a, dict) and inline_child_key(a.get("node"), inline) else names)
        if bad:
            return bad
    if not all(isinstance(i, dict) for i in (test.get("input") or {}).get("items", [])):
        return "input.items must be a list of objects"
    return None


def sanitize_tests(
    raw_tests: Any, definition: dict[str, Any], taken_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(accepted, rejected)``. Accepted tests are rebuilt: new id, draft, unlocked, ``source: "ai"``, no approvals.

    ``taken_ids`` is updated with the new ids. A model-sent id (even one that exists) is always replaced.
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not isinstance(raw_tests, list):
        return [], [{"reason": "'tests' is not a list"}]
    for raw in raw_tests:
        if not isinstance(raw, dict):
            rejected.append({"reason": "a test is not an object", "test": raw})
            continue
        t = copy.deepcopy(raw)
        for k in ("approved_by", "approved_at"):
            t.pop(k, None)
        new_id = new_test_id()
        while new_id in taken_ids:
            new_id = new_test_id()
        t.update({"id": new_id, "status": "draft", "locked": False, "source": "ai"})
        try:
            norm = normalize_tests([t])[0]
        except PydanticValidationError as e:
            err = e.errors()[0]
            where = ".".join(str(p) for p in err["loc"])
            rejected.append({"reason": f"invalid test: {err['msg']} ({where})", "test": raw})
            continue
        bad = validate_test(norm, definition)
        if bad:
            rejected.append({"reason": bad, "test": raw})
            continue
        taken_ids.add(new_id)
        accepted.append(norm)
    return accepted, rejected


# ── cells ────────────────────────────────────────────────────────────────────────────────────


def _check_cells(cells: list[dict[str, Any]], definition: dict[str, Any]) -> list[dict[str, str]]:
    names = {n.get("name") for n in definition.get("nodes") or [] if isinstance(n, dict)}
    out = []
    for c in cells:
        if not isinstance(c, dict) or c.get("node") not in names:
            raise AuthoringError(422, f"Cell names node '{c.get('node') if isinstance(c, dict) else c}', which doesn't exist")
        if c.get("category") not in TEST_CATEGORIES:
            raise AuthoringError(422, f"Unknown category '{c.get('category')}'")
        out.append({"node": c["node"], "category": c["category"]})
    return out


# ── drafting ─────────────────────────────────────────────────────────────────────────────────

_RANK = {"passed": 0, "failed": 1, "error": 2}


async def _load(repo: Any, workflow_id: str, override: dict[str, Any] | None) -> tuple[Any, dict[str, Any], dict[str, Any], set[str]]:
    from ..core.exceptions import WorkflowNotFoundError
    from .check_lookups import build_lookups
    from .workflow_checker import check_workflow

    stored = await repo.get(workflow_id)
    if not stored:
        raise WorkflowNotFoundError(workflow_id)
    definition = copy.deepcopy(override) if override is not None else workflow_to_definition(stored.workflow)
    lookups = await build_lookups(repo._session, stored.project_id, None, team_id=getattr(stored, "team_id", None) or "default",
                                  workflow_id=stored.id, definition=definition)
    shapes = check_workflow(definition, lookups=lookups).get("shapes_by_node", {})
    stored_ids = {t["id"] for t in normalize_tests(workflow_to_definition(stored.workflow).get("tests"))}
    return stored, definition, shapes, stored_ids


async def _gap_cells(repo: Any, stored: Any, definition: dict[str, Any], limit: int) -> list[dict[str, str]]:
    from .scorecard import _gaps

    report = await tr.run_tests(repo, stored, definition, persist=False)
    return [{"node": g["node"], "category": g["category"]} for g in _gaps(report)][:limit]


async def _dry_run(repo: Any, stored: Any, definition: dict[str, Any], drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each draft run once with ``persist=False``; returns the drafts with ``ai_run`` attached, passed first."""
    if not drafts:
        return []
    workflow = definition_to_workflow(definition, stored.name, stored.id, stored.workflow.description)
    defaults = definition.get("test_defaults") or {}
    out = []
    try:
        setup = await tr._setup(repo, stored, definition, workflow)
    except Exception as e:
        logger.warning("test-ai dry run could not start: %s", type(e).__name__)
        return [{**d, "ai_run": {"status": "error", "failures": [{"kind": "setup", "node": None, "message": f"The test could not run: {e}"}]}}
                for d in drafts]
    for d in drafts:
        try:
            res = await tr._run_one(repo, stored, workflow, definition, d, defaults, setup, False)
            ai_run = {"status": res["status"], "failures": res["failures"]}
        except Exception as e:  # one broken draft must not lose the others
            ai_run = {"status": "error", "failures": [{"kind": "setup", "node": None, "message": f"The test could not run: {e}"}]}
        out.append({**d, "ai_run": ai_run})
    return sorted(out, key=lambda d: _RANK.get(d["ai_run"]["status"], 3))  # sorted() is stable


async def draft_tests(
    repo: Any, workflow_id: str, definition: dict[str, Any] | None = None, cells: list[dict[str, Any]] | None = None,
    max_tests: int = 8,
) -> dict[str, Any]:
    """Ask the model for tests for ``cells`` (default: the grid gaps), sanitise, repair once, dry-run. Saves nothing."""
    max_tests = max(1, min(int(max_tests or 8), _MAX_CELLS))
    stored, definition, shapes, stored_ids = await _load(repo, workflow_id, definition)
    stored_tests = normalize_tests(definition.get("tests"))
    if cells is None:
        target_cells = await _gap_cells(repo, stored, definition, max_tests)
    else:
        target_cells = _check_cells(cells, definition)[:max_tests]
    result: dict[str, Any] = {"drafts": [], "rejected": [], "cells": target_cells, "model": test_ai.TEST_AI_MODEL}
    if not target_cells:
        return result

    taken = {t["id"] for t in stored_tests} | stored_ids  # ids in the unsaved definition and in storage
    try:  # known secret values are scrubbed from the prompt on top of the key/pattern redaction
        from .variable_loader import load_run_secrets
        from .project_service import effective_environment, resolve_project

        project = await resolve_project(repo._session, stored.project_id)
        known_secrets = set(await load_run_secrets(
            environment=effective_environment(project, "default"), project_id=stored.project_id))
    except Exception:
        known_secrets = set()
    user = build_draft_prompt(definition, shapes, stored_tests, target_cells, max_tests, known_secrets)
    reply = await ask_json("draft_tests", _DRAFT_SYSTEM, user)
    accepted, rejected = sanitize_tests(reply.get("tests"), definition, taken)
    overflow = max(0, len(accepted) - max_tests)
    if overflow:
        rejected.extend({"reason": f"more than max_tests ({max_tests}) tests"} for _ in range(overflow))
        accepted = accepted[:max_tests]

    if rejected and any("test" in r for r in rejected):  # one repair round with the reasons
        retry = [r for r in rejected if "test" in r]
        try:
            fix = await ask_json("draft_tests", _DRAFT_SYSTEM, json.dumps({
                **json.loads(user), "repair": {
                    "instruction": "These tests were rejected. Return ONLY corrected versions of them as {\"tests\": [...]}.",
                    "rejected": retry,
                },
            }, default=str))
        except TestAIError as e:
            logger.info("test-ai repair round failed: %s", e.code)
        else:
            fixed_raw = fix.get("tests") if isinstance(fix.get("tests"), list) else []
            fixed, still_bad = sanitize_tests(fixed_raw, definition, taken)
            room = max(0, max_tests - len(accepted))
            accepted.extend(fixed[:room])
            rejected = [r for r in rejected if "test" not in r] + still_bad
            rejected.extend({"reason": f"{r['reason']} (not fixed in the repair round)", "test": r["test"]}
                            for r in retry[len(fixed_raw):])
    result["drafts"] = await _dry_run(repo, stored, definition, accepted)
    result["rejected"] = rejected
    return result


# ── variations ───────────────────────────────────────────────────────────────────────────────


def _as_items(entry: Any) -> list[dict[str, Any]] | None:
    if isinstance(entry, dict) and isinstance(entry.get("items"), list):
        entry = entry["items"]
    if isinstance(entry, dict):
        return [entry]
    if isinstance(entry, list) and entry and all(isinstance(i, dict) for i in entry):
        return entry
    return None


async def propose_variations(
    definition: dict[str, Any], shapes: dict[str, Any], base_items: list[dict[str, Any]], n: int = 10,
) -> list[list[dict[str, Any]]]:
    """Up to ``n`` input item lists the model thinks could break the workflow. Raises ``TestAIError`` when the model can't be used."""
    n = max(1, min(int(n or 10), 50))
    user = json.dumps({
        "definition": _definition_for_prompt(definition), "shapes_by_node": _shapes_for_prompt(shapes or {}),
        "base_items": redact_for_prompt(base_items), "count": n,
    }, default=str)
    reply = await ask_json("variations", _VARIATIONS_SYSTEM, user)
    entries = reply.get("variations")
    if entries is None:
        entries = reply.get("inputs") if reply.get("inputs") is not None else reply.get("tests")
    out: list[list[dict[str, Any]]] = []
    for e in entries if isinstance(entries, list) else []:
        items = _as_items(e)
        if items is not None:
            out.append(copy.deepcopy(items))
        if len(out) >= n:
            break
    return out
