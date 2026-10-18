"""Stage 6 spec handling (DESIGN-STAGE6-AI-BUILDER §3): parse the model's spec reply, validate it, trace rules to tests.

Pure and deterministic (no I/O, no LLM).

* ``parse_spec(reply)``      the model's JSON (§5.2 spec schema: tests as ``[{ref, rules, test_json}]``) -> ``(spec, issues)`` in the §3.1 shape.
                             THIS is where the server maps each ``ref`` to a fresh ``t_`` + 8 hex id, forces ``status: draft``, ``locked: false``,
                             ``source: "ai"``, and builds ``rule_tests`` (``{rule_id: [test_id]}``) from each entry's ``rules``.
* ``virtual_definition``     one node per step (of that step's type, no parameters, no connections): what tests are validated against.
* ``validate_spec``          SPEC001-SPEC008 -> ``[SpecIssue{path, code, message, level}]`` (``level``: ``error`` blocks approval, ``warning`` does not).
* ``approve_spec_tests``     the approval rewrite: every test ``approved`` + ``locked``.
* ``spec_coverage``          how a built definition covers the spec (the report's ``spec_coverage``).

SPEC001  step names are unique and 1-60 characters.
SPEC002  every step's node_type is in the node catalog.
SPEC003  api_ref exists in the project's operations (and only an HttpRequest step has one).
SPEC004  every rule has a test in rule_tests, and rule_tests / test rules name only rules and tests that exist.
SPEC005  every test passes 3B ``validate_test`` against the virtual definition (mocks / assertions name step names).
SPEC006  exactly one trigger step and its type matches ``trigger.kind``.
SPEC007  effectful steps are mocked in each test that surely reaches them (warning when it is not sure).
SPEC008  (warning) trigger fields agree with the keys of ``input.items[0]`` in each workflow test.
SPEC009  (warning, new workflows only) too few tests for the spec's paths: at least 3, and one per outcome of each decision step
         (``required_test_count``). A warning, so it never blocks approval, but ``draft_spec`` sends it to the spec repair round.
SPEC010  (warning, only with an ``understanding``) a confirmed concept has no step of a node type that realises it (its signature).
SPEC011  (warning, only with an ``understanding``) a test obligation of a confirmed concept has no test: an Approval needs a test for each
         port it wires (approved, rejected), and an Approval that can time out (Escalation, or an SLA on an Approval) needs one whose mock
         takes ``timed_out``. Both are warnings: the graph never blocks spec approval (decision D3 is about questions only).
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..engine.effects import effect_of
from ..engine.graph import TRIGGER_NODE_TYPES
from .ai_grounding import TRIGGER_TYPE_BY_KIND

SpecIssue = dict[str, Any]

SPEC_VERSION = 1
MAX_STEP_NAME = 60
TRIGGER_KINDS = tuple(TRIGGER_TYPE_BY_KIND)
INTERFACE_KINDS = ("form", "chat")
#: steps after which a later step may or may not run (a test cannot be sure to reach what follows)
_GATE_TYPES = frozenset({"If", "Switch", "Approval", "Filter", "Loop", "StopAndError", "Merge", "Wait", "Poll"})
_APPROVAL_STATE = ("status", "locked", "approved_by", "approved_at")


def issue(path: str, code: str, message: str, level: str = "error") -> SpecIssue:
    return {"path": path, "code": code, "message": message, "level": level}


def errors_of(issues: Iterable[SpecIssue]) -> list[SpecIssue]:
    return [i for i in issues if i.get("level", "error") == "error"]


def new_test_id(taken: set[str] | None = None) -> str:
    taken = taken if taken is not None else set()
    while True:
        tid = "t_" + uuid.uuid4().hex[:8]
        if tid not in taken:
            taken.add(tid)
            return tid


# ── parse ───────────────────────────────────────────────────────────────────────────────────


def _str(v: Any, default: str = "") -> str:
    return v.strip() if isinstance(v, str) else default


def _norm_trigger(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, Mapping) else {}
    fields: list[dict[str, Any]] = []
    for f in raw.get("fields") or []:
        if isinstance(f, Mapping) and _str(f.get("name")):
            item = {"name": _str(f["name"]), "type": _str(f.get("type"), "string") or "string", "required": bool(f.get("required", False))}
            if _str(f.get("label")):
                item["label"] = _str(f["label"])
            fields.append(item)
    return {"kind": _str(raw.get("kind")).lower(), "fields": fields}


def _norm_steps(raw: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, Mapping):
            continue
        step: dict[str, Any] = {"name": _str(s.get("name")), "node_type": _str(s.get("node_type")), "purpose": _str(s.get("purpose")),
                                "rules": [str(r) for r in s.get("rules") or [] if isinstance(r, (str, int))]}
        if s.get("api_ref") not in (None, ""):
            step["api_ref"] = _str(s.get("api_ref")) if isinstance(s.get("api_ref"), str) else s.get("api_ref")
        steps.append(step)
    return steps


def _norm_rules(raw: Any) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    taken: set[str] = set()
    for r in raw if isinstance(raw, list) else []:
        text = _str(r.get("text")) if isinstance(r, Mapping) else (_str(r) if isinstance(r, str) else "")
        if not text:
            continue
        rid = _str(r.get("id")) if isinstance(r, Mapping) else ""
        if not rid or rid in taken:
            i = len(rules) + 1
            while f"r{i}" in taken:
                i += 1
            rid = f"r{i}"
        taken.add(rid)
        rules.append({"id": rid, "text": text})
    return rules


def _norm_interface(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, Mapping) or not raw:
        return None
    kind = _str(raw.get("kind")).lower()
    if kind not in INTERFACE_KINDS:
        return None
    return {"kind": kind, "slug": _str(raw.get("slug")), "title": _str(raw.get("title"))}


def virtual_definition(spec: Mapping[str, Any], base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The definition a spec's tests are validated against: ``base``'s nodes (modify mode), then one node per step of that step's type with
    empty parameters (a step whose name already exists in ``base`` keeps the base node). No connections, no tests."""
    nodes: list[dict[str, Any]] = [copy.deepcopy(n) for n in (base or {}).get("nodes") or [] if isinstance(n, Mapping)]
    have = {n.get("name") for n in nodes}
    for step in spec.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        name = step.get("name")
        if isinstance(name, str) and name and name not in have:
            nodes.append({"name": name, "type": step.get("node_type") or "Set", "parameters": {}, "position": {"x": 0, "y": 0}})
            have.add(name)
    return {"name": spec.get("title") or "Draft", "nodes": nodes, "connections": [], "tests": [], "test_defaults": {}}


def _make_test(raw: Mapping[str, Any], taken: set[str]) -> dict[str, Any]:
    """A model test -> a stored draft test with a fresh id. Raises ``PydanticValidationError`` for a structurally invalid test."""
    from ..schemas.workflow import normalize_tests

    t = copy.deepcopy(dict(raw))
    for k in _APPROVAL_STATE:
        t.pop(k, None)
    t.update({"id": new_test_id(taken), "status": "draft", "locked": False, "source": "ai"})
    return normalize_tests([t])[0]


def parse_spec(reply: Mapping[str, Any], *, taken_test_ids: set[str] | None = None,
               base_definition: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[SpecIssue]]:
    """The model's spec JSON -> ``(spec, issues)``.

    ``reply["tests"]`` is ``[{ref, rules, test_json}]`` (``test_json`` a JSON string; an object is accepted too). Each is parsed, given a
    fresh id (``taken_test_ids`` are avoided; the model's own ids and approval fields are discarded), made a draft with ``source: "ai"``,
    and linked into ``rule_tests`` by the rule ids in its ``rules``. A test that is not JSON or not a TestCase is DROPPED with a SPEC005
    issue at ``tests[<ref>]`` (its rules then fail SPEC004, which is what makes the repair round add a replacement). A test that parses
    but names a missing step is KEPT; ``validate_spec`` reports it (SPEC005) so the repair round can fix it.

    Never raises for odd input."""
    reply = reply if isinstance(reply, Mapping) else {}
    taken = set(taken_test_ids or ())
    issues: list[SpecIssue] = []
    rules = _norm_rules(reply.get("rules"))
    spec: dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "title": _str(reply.get("title")) or "Untitled workflow",
        "summary": _str(reply.get("summary")),
        "trigger": _norm_trigger(reply.get("trigger")),
        "rules": rules,
        "steps": _norm_steps(reply.get("steps")),
        "interface": _norm_interface(reply.get("interface")),
        "tests": [],
        "rule_tests": {},
        "assumptions": [str(a) for a in reply.get("assumptions") or [] if isinstance(a, str) and a.strip()],
        "questions": [str(q) for q in reply.get("questions") or [] if isinstance(q, str) and q.strip()],
    }
    rule_tests: dict[str, list[str]] = {}
    raw_tests = reply.get("tests")
    for i, entry in enumerate(raw_tests if isinstance(raw_tests, list) else []):
        ref = _str(entry.get("ref")) if isinstance(entry, Mapping) else ""
        label = ref or str(i + 1)
        path = f"tests[{label}]"
        if not isinstance(entry, Mapping):
            issues.append(issue(path, "SPEC005", "A test must be an object {ref, rules, test_json}."))
            continue
        body = entry.get("test_json")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError as e:
                issues.append(issue(path, "SPEC005", f"test_json is not valid JSON ({e.args[0] if e.args else 'parse error'}); the test was dropped."))
                continue
        if not isinstance(body, Mapping):
            issues.append(issue(path, "SPEC005", "test_json must be one TestCase object; the test was dropped."))
            continue
        try:
            test = _make_test(body, taken)
        except PydanticValidationError as e:
            err = e.errors()[0]
            issues.append(issue(path, "SPEC005", f"Invalid test: {err['msg']} ({'.'.join(str(p) for p in err['loc'])}); the test was dropped."))
            continue
        spec["tests"].append(test)
        rids = entry.get("rules")
        for rid in [rids] if isinstance(rids, str) else (rids if isinstance(rids, list) else []):
            rid = str(rid).strip()
            if rid:
                rule_tests.setdefault(rid, [])
                if test["id"] not in rule_tests[rid]:
                    rule_tests[rid].append(test["id"])
    spec["rule_tests"] = rule_tests
    normalize_agent_mocks(spec)
    drop_logic_step_mocks(spec)
    return spec, issues


# Pure routing / shaping steps: a test must run them for real, since they ARE the logic under test. A mock on one hides that logic and
# replaces a pass-through item with an invented one (live eval: a mocked Respond broke its own pass-through contract in every test).
LOGIC_STEP_TYPES = frozenset({"If", "Switch", "Set", "Respond", "Merge", "Filter", "Sort", "NoOp", "StopAndError"})


def drop_logic_step_mocks(spec: dict[str, Any]) -> None:
    """In place: remove mocks an AI-written spec test puts on a pure logic step (``LOGIC_STEP_TYPES``). Like ``normalize_agent_mocks``,
    only AI-authored specs go through here (``parse_spec``); a mock a person wrote or edited is never rewritten."""
    logic = {str(s.get("name")) for s in spec.get("steps") or [] if isinstance(s, Mapping) and s.get("node_type") in LOGIC_STEP_TYPES}
    if not logic:
        return
    for t in spec.get("tests") or []:
        mocks = t.get("mocks") if isinstance(t, dict) else None
        if isinstance(mocks, dict):
            for name in [n for n in mocks if n in logic]:
                del mocks[name]


def normalize_agent_mocks(spec: dict[str, Any]) -> None:
    """In place: an AIAgent step's mock item that lacks ``response`` is a flat-written structured answer, so it becomes
    ``{response: "", structured: <item>}``.

    Unambiguous because a real AIAgent item ALWAYS has ``response`` (the runtime output is ``{response, structured, toolCalls, iterations}``),
    so an item without it can never be what the step emits; and downstream steps read ``$json.structured.<field>``. Only AI-authored specs go
    through here (``parse_spec``); a mock a person wrote or edited is never rewritten."""
    agents = {str(s.get("name")) for s in spec.get("steps") or [] if isinstance(s, Mapping) and s.get("node_type") == "AIAgent"}
    if not agents:
        return
    for t in spec.get("tests") or []:
        mocks = t.get("mocks") if isinstance(t, dict) else None
        for name, mock in (mocks or {}).items():
            if name in agents and isinstance(mock, dict) and isinstance(mock.get("items"), list):
                mock["items"] = [dict(i) if "response" in i or "structured" in i else {"response": "", "structured": dict(i)}
                                 for i in mock["items"] if isinstance(i, dict)]


def tests_by_ref(spec: Mapping[str, Any]) -> dict[str, str]:
    """``{"T1": test_id, ...}`` in spec order: the refs ``ai_builder_prompts.spec_to_model_output`` shows the model."""
    return {f"T{i}": t["id"] for i, t in enumerate(spec.get("tests") or [], 1) if isinstance(t, Mapping) and t.get("id")}


# ── validate ────────────────────────────────────────────────────────────────────────────────


def _catalog_types(catalog: Any) -> set[str]:
    if isinstance(catalog, Mapping):
        return set(catalog)
    return {str(e["type"]) if isinstance(e, Mapping) else str(e) for e in catalog or [] if (isinstance(e, Mapping) and e.get("type")) or isinstance(e, str)}


def _api_refs(api_ops: Any) -> set[str]:
    if isinstance(api_ops, Mapping):
        return set(api_ops)
    return {str(o["api_ref"]) if isinstance(o, Mapping) else str(o) for o in api_ops or [] if (isinstance(o, Mapping) and o.get("api_ref")) or isinstance(o, str)}


def validate_spec(spec: Mapping[str, Any], catalog: Any, api_ops: Any, *, base_definition: Mapping[str, Any] | None = None,
                  existing_test_ids: Iterable[str] = (), mode: str = "new", understanding: Mapping[str, Any] | None = None) -> list[SpecIssue]:
    """SPEC001-SPEC008 for ``spec`` (§3.1 shape). Never raises.

    ``mode`` is ``new`` (default) or ``modify``. A change spec lists only the new or changed steps, so in ``modify`` mode, when
    ``base_definition`` already has a trigger node, the spec need not contain a trigger step (SPEC006 still fires for two trigger steps, and for
    a trigger step whose type does not match ``trigger.kind``).

    ``catalog`` is the node catalog (list of entries, or any collection of type names); ``api_ops`` the grounding entries
    (``api_operations``) or a collection of ``api_ref`` strings. ``base_definition`` and ``existing_test_ids`` are for modify mode: nodes
    and locked tests that already exist (a rule may be proven by a locked test, a test may name an existing node)."""
    from .test_ai_service import validate_test

    issues: list[SpecIssue] = []
    types = _catalog_types(catalog)
    refs = _api_refs(api_ops)
    steps = [s for s in spec.get("steps") or [] if isinstance(s, Mapping)]
    tests = [t for t in spec.get("tests") or [] if isinstance(t, Mapping)]
    rules = [r for r in spec.get("rules") or [] if isinstance(r, Mapping)]
    rule_ids = [str(r.get("id")) for r in rules]
    test_ids = {str(t.get("id")) for t in tests} | {str(i) for i in existing_test_ids}

    # SPEC001
    seen: dict[str, int] = {}
    for i, s in enumerate(steps):
        name = s.get("name")
        path = f"steps[{i}].name"
        if not isinstance(name, str) or not name.strip():
            issues.append(issue(path, "SPEC001", "A step needs a name."))
        elif len(name.strip()) > MAX_STEP_NAME:
            issues.append(issue(path, "SPEC001", f"Step name '{name[:20]}...' is longer than {MAX_STEP_NAME} characters."))
        elif name in seen:
            issues.append(issue(path, "SPEC001", f"Step name '{name}' is used by steps[{seen[name]}] too; names must be unique."))
        else:
            seen[name] = i
    if not steps:
        issues.append(issue("steps", "SPEC001", "The spec has no steps."))

    # SPEC002 / SPEC003
    for i, s in enumerate(steps):
        ntype = s.get("node_type")
        if not isinstance(ntype, str) or ntype not in types:
            issues.append(issue(f"steps[{i}].node_type", "SPEC002", f"Node type '{ntype}' is not in the catalog."))
        ref = s.get("api_ref")
        if ref not in (None, ""):
            if ntype != "HttpRequest":
                issues.append(issue(f"steps[{i}].api_ref", "SPEC003", f"Only an HttpRequest step takes an api_ref (this step is '{ntype}')."))
            elif not isinstance(ref, str) or ref not in refs:
                issues.append(issue(f"steps[{i}].api_ref", "SPEC003", f"api_ref '{ref}' is not in api_operations."))
        elif ntype == "HttpRequest" and refs:
            issues.append(issue(f"steps[{i}].api_ref", "SPEC003", "This HttpRequest step has no api_ref; use an operation from api_operations if one fits.", "warning"))

    # SPEC004
    rule_tests = spec.get("rule_tests") if isinstance(spec.get("rule_tests"), Mapping) else {}
    for i, rid in enumerate(rule_ids):
        if not (rule_tests.get(rid) or []):
            issues.append(issue(f"rules[{i}]", "SPEC004", f"Rule {rid} has no test; add a test that proves it."))
    for rid, tids in rule_tests.items():
        if rid not in rule_ids:
            issues.append(issue(f"rule_tests.{rid}", "SPEC004", f"rule_tests names rule '{rid}', which does not exist."))
        for tid in tids or []:
            if tid not in test_ids:
                issues.append(issue(f"rule_tests.{rid}", "SPEC004", f"rule_tests names test '{tid}', which does not exist."))
    for i, s in enumerate(steps):
        for rid in s.get("rules") or []:
            if rid not in rule_ids:
                issues.append(issue(f"steps[{i}].rules", "SPEC004", f"Step '{s.get('name')}' names rule '{rid}', which does not exist.", "warning"))
    if not rules:
        issues.append(issue("rules", "SPEC004", "The spec has no rules."))

    # SPEC006
    triggers = [(i, s) for i, s in enumerate(steps) if s.get("node_type") in TRIGGER_NODE_TYPES]
    kind = (spec.get("trigger") or {}).get("kind") if isinstance(spec.get("trigger"), Mapping) else None
    if kind not in TRIGGER_TYPE_BY_KIND:
        issues.append(issue("trigger.kind", "SPEC006", f"trigger.kind '{kind}' must be one of {', '.join(TRIGGER_KINDS)}."))
    base_has_trigger = any(isinstance(n, Mapping) and n.get("type") in TRIGGER_NODE_TYPES for n in (base_definition or {}).get("nodes") or [])
    if mode == "modify" and base_has_trigger and not triggers:
        pass  # the workflow already has its trigger; a change spec need not restate it
    elif len(triggers) != 1:
        issues.append(issue("steps", "SPEC006", f"The spec needs exactly one trigger step (found {len(triggers)})."))
    elif kind in TRIGGER_TYPE_BY_KIND and triggers[0][1].get("node_type") != TRIGGER_TYPE_BY_KIND[kind]:
        issues.append(issue(f"steps[{triggers[0][0]}].node_type", "SPEC006",
                            f"trigger.kind '{kind}' needs a {TRIGGER_TYPE_BY_KIND[kind]} step, but the trigger step is {triggers[0][1].get('node_type')}."))

    # SPEC005 / SPEC007 / SPEC008
    definition = virtual_definition(spec, base_definition)
    step_types = {str(s.get("name")): str(s.get("node_type")) for s in steps}
    node_types = {str(n.get("name")): str(n.get("type")) for n in definition["nodes"]}
    step_order = [str(s.get("name")) for s in steps]
    fields = {str(f["name"]): bool(f.get("required")) for f in (spec.get("trigger") or {}).get("fields") or [] if isinstance(f, Mapping) and f.get("name")} \
        if isinstance(spec.get("trigger"), Mapping) else {}
    for i, t in enumerate(tests):
        tpath = f"tests[{i}]"
        try:
            bad = validate_test(dict(t), definition)
        except Exception as e:  # noqa: BLE001 - a malformed test is an issue, never a crash
            bad = f"the test could not be read ({type(e).__name__})"
        if bad:
            issues.append(issue(tpath, "SPEC005", f"Test '{t.get('name')}': {bad}."))
        _spec007(issues, tpath, t, step_order, step_types, node_types)
        _failure_mock_issues(issues, tpath, t)
        _spec008(issues, tpath, t, spec, fields)
    if mode != "modify":
        need = required_test_count(steps, len([r for r in spec.get("rules") or [] if isinstance(r, Mapping)]))
        if len(tests) < need:
            issues.append(issue("tests", "SPEC009",
                                f"The spec has {len(tests)} test(s) but needs at least {need}: one per rule, one per outcome of every decision step "
                                f"(each Approval outcome, each If / Switch branch) and a test on each side of every numeric threshold. Add the missing tests.",
                                "warning"))
    if understanding:
        issues += graph_issues(spec, understanding, base_definition)
    return issues


def graph_issues(spec: Mapping[str, Any], understanding: Mapping[str, Any], base_definition: Mapping[str, Any] | None = None) -> list[SpecIssue]:
    """SPEC010 and SPEC011 (see the module docstring). Pure; never raises; warnings only."""
    from . import kg_understand as ku

    issues: list[SpecIssue] = []
    confirmed = [c for c in understanding.get("concepts") or [] if c.get("confirmed")]
    if not confirmed:
        return issues
    concepts = ku.seed_vocabulary()[0]
    steps = [s for s in spec.get("steps") or [] if isinstance(s, Mapping)]
    tests = [t for t in spec.get("tests") or [] if isinstance(t, Mapping)]
    types = {str(s.get("node_type")) for s in steps} | {str(n.get("type")) for n in (base_definition or {}).get("nodes") or [] if isinstance(n, Mapping)}
    # SPEC010
    for c in confirmed:
        sigs = [str(sig.get("node_type")) for sig in (concepts.get(c["key"]).signatures if c["key"] in concepts else []) if sig.get("node_type")]
        if sigs and not (set(sigs) & types):
            issues.append(issue("steps", "SPEC010", f"The request involves {c['label']}, but no step uses a matching node type ({' or '.join(dict.fromkeys(sigs))}).", "warning"))
    # SPEC011: Approval ports
    keys = {c["key"] for c in confirmed}
    approvals = {str(s.get("name")) for s in steps if s.get("node_type") == "Approval"}
    if approvals and keys & {"concept:approval", "concept:four_eyes_approval", "concept:escalation", "concept:refund"}:
        ports: set[str] = set()
        for t in tests:
            for node, mock in (t.get("mocks") or {}).items():
                if node in approvals and isinstance(mock, Mapping) and mock.get("port"):
                    ports.add(str(mock["port"]))
            for a in t.get("expect") or []:
                if isinstance(a, Mapping) and a.get("node") in approvals and a.get("port") in ("approved", "rejected", "timed_out"):
                    ports.add(str(a["port"]))
        for port in ("approved", "rejected"):
            if port not in ports:
                issues.append(issue("tests", "SPEC011", f"Approval has no test whose mock takes the '{port}' port; add one that proves what happens then.", "warning"))
        times_out = "concept:escalation" in keys or (keys & {"concept:approval", "concept:four_eyes_approval"} and ku.slot_value(understanding, "sla.due_in") is not None)
        if times_out and "timed_out" not in ports:
            issues.append(issue("tests", "SPEC011", "The request has a time limit on an approval, but no test mocks the Approval with the 'timed_out' port; add one "
                                                    "that proves what happens when nobody decides in time.", "warning"))
    return issues


def _failure_mock_issues(issues: list[SpecIssue], tpath: str, test: Mapping[str, Any]) -> None:
    """SPEC005 for a test that expects a step to FAIL while mocking it with a reply. An ``http`` mock with a 4xx/5xx status is a reply, not
    a failure: HttpRequest emits {statusCode, body} and the run goes on, so ``status: error`` can never hold (live eval: three error-path
    tests were written this way and could never pass). A failure is mocked with ``{"error": {...}}`` or ``{"timeout": true}``."""
    mocks = test.get("mocks") if isinstance(test.get("mocks"), Mapping) else {}
    for a in test.get("expect") or []:
        if not (isinstance(a, Mapping) and a.get("op") == "status" and a.get("value") == "error" and isinstance(a.get("node"), str)):
            continue
        mock = mocks.get(a["node"])
        if isinstance(mock, Mapping) and ("http" in mock or "items" in mock) and "error" not in mock and not mock.get("timeout"):
            issues.append(issue(tpath, "SPEC005",
                                f"Test '{test.get('name')}' expects step '{a['node']}' to fail but mocks it with a reply. An http mock with an error status "
                                f"is a normal reply (the step emits statusCode and body and the run continues). To make the step fail, mock it with "
                                f"{{\"error\": {{\"message\": \"...\", \"code\": \"...\"}}}} or {{\"timeout\": true}}."))


#: outcomes a decision step adds (a Switch's real case count is not in the spec, so 3 is assumed)
_BRANCHES = {"If": 2, "Approval": 2, "Switch": 3}
MIN_SPEC_TESTS = 3
MAX_REQUIRED_TESTS = 8


def required_test_count(steps: Iterable[Mapping[str, Any]], rule_count: int = 0) -> int:
    """The fewest tests a spec's paths need: a floor of 3, or one per distinct path through the decision steps (1 + sum of extra branches),
    plus one boundary test when an If / Switch is there (a threshold is judged from both sides), or one per rule when there are more rules
    (live eval: 4 rules, one If, 3 tests left a listed input value unproven; the SPEC009 message already asks for one per rule)."""
    steps = [s for s in steps if isinstance(s, Mapping)]
    paths = 1 + sum(_BRANCHES.get(str(s.get("node_type")), 1) - 1 for s in steps)
    boundary = 1 if any(s.get("node_type") in ("If", "Switch") for s in steps) else 0
    return max(MIN_SPEC_TESTS, min(max(paths + boundary, rule_count), MAX_REQUIRED_TESTS))


def _not_run_nodes(test: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for a in test.get("expect") or []:
        if not isinstance(a, Mapping):
            continue
        if a.get("op") == "not_exists" or (a.get("subject") == "node" and a.get("op") == "status" and a.get("value") in ("not_run", "no_output")):
            if isinstance(a.get("node"), str):
                out.add(a["node"])
    return out


def _spec007(issues: list[SpecIssue], tpath: str, test: Mapping[str, Any], step_order: list[str], step_types: dict[str, str],
             node_types: dict[str, str]) -> None:
    target = test.get("target") or {}
    if target.get("kind", "workflow") != "workflow":
        return
    mocks = set((test.get("mocks") or {}).keys())
    skipped = _not_run_nodes(test)
    asserted = {str(a["node"]) for a in test.get("expect") or [] if isinstance(a, Mapping) and isinstance(a.get("node"), str)}
    touched = mocks | asserted  # a gate the test mocks or asserts on is one whose outcome the test controls
    gated = False        # a gate precedes this step: the test may not reach it
    ruled_out = False    # ... and the test itself decides that gate
    for name in step_order:
        ntype = step_types.get(name, node_types.get(name, ""))
        # §3.2: a gate does not excuse a step the test itself asserts on (it must run for the assertion to hold), so
        # an asserted, unmocked effectful step is flagged even behind a gate the test controls.
        if effect_of(ntype) != "none" and name not in mocks and name not in skipped and (not ruled_out or name in asserted):
            if gated:
                issues.append(issue(f"{tpath}.mocks", "SPEC007",
                                    f"Test '{test.get('name')}' does not mock '{name}' ({ntype}); if the test reaches it, the run fails with TEST_UNMOCKED_EFFECT.", "warning"))
            else:
                issues.append(issue(f"{tpath}.mocks", "SPEC007",
                                    f"Test '{test.get('name')}' must mock '{name}' ({ntype}): it runs on every path and talks to the outside."))
        if ntype in _GATE_TYPES:
            gated = True
            if name in touched:
                ruled_out = True


def _spec008(issues: list[SpecIssue], tpath: str, test: Mapping[str, Any], spec: Mapping[str, Any], fields: dict[str, bool]) -> None:
    kind = (spec.get("trigger") or {}).get("kind") if isinstance(spec.get("trigger"), Mapping) else None
    if kind not in ("form", "execute") or not fields:
        return
    if (test.get("target") or {}).get("kind", "workflow") != "workflow":
        return
    items = (test.get("input") or {}).get("items") or []
    if not items or not isinstance(items[0], Mapping):
        return
    keys = set(items[0])
    missing = sorted(f for f, req in fields.items() if req and f not in keys)
    extra = sorted(k for k in keys if k not in fields and not str(k).startswith("_"))
    if missing:
        issues.append(issue(f"{tpath}.input", "SPEC008", f"Test '{test.get('name')}' input lacks required trigger field(s): {', '.join(missing)}.", "warning"))
    if extra:
        issues.append(issue(f"{tpath}.input", "SPEC008", f"Test '{test.get('name')}' input has key(s) that are not trigger fields: {', '.join(extra)}.", "warning"))


# ── approval and coverage ───────────────────────────────────────────────────────────────────


def approve_spec_tests(spec: Mapping[str, Any], actor: str, at: str | None = None) -> dict[str, Any]:
    """A copy of ``spec`` with every test ``status: "approved"``, ``locked: true`` (``source`` stays ``ai``), approver recorded."""
    out = copy.deepcopy(dict(spec))
    for t in out.get("tests") or []:
        t.update({"status": "approved", "locked": True, "approved_by": actor})
        if at:
            t["approved_at"] = at
    return out


def spec_coverage(defn: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, list]:
    """How a built definition covers the spec: ``{steps_missing, steps_wrong_type, rules_untested}``."""
    nodes = {str(n.get("name")): n for n in (defn or {}).get("nodes") or [] if isinstance(n, Mapping)}
    missing: list[str] = []
    wrong: list[dict[str, str]] = []
    for s in spec.get("steps") or []:
        if not isinstance(s, Mapping):
            continue
        n = nodes.get(str(s.get("name")))
        if n is None:
            missing.append(str(s.get("name")))
        elif s.get("node_type") and n.get("type") != s.get("node_type"):
            wrong.append({"step": str(s.get("name")), "expected": str(s.get("node_type")), "actual": str(n.get("type"))})
    rule_tests = spec.get("rule_tests") if isinstance(spec.get("rule_tests"), Mapping) else {}
    untested = [str(r.get("id")) for r in spec.get("rules") or [] if isinstance(r, Mapping) and not (rule_tests.get(r.get("id")) or [])]
    return {"steps_missing": missing, "steps_wrong_type": wrong, "rules_untested": untested}
