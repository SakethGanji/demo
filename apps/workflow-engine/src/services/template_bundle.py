"""Template bundles (``wf-template/1``): strict parsing, lints, tokens and the shipped catalog.

DESIGN-STAGE8-REUSE section 8.1 / 8.2. Pure: no database. Every problem is a ``BundleIssue`` with a stable code, the
path it was found at and a message a template author can act on:

    TPL_SCHEMA            the bundle does not fit the format (the message names the field)
    TPL_FORMAT            unknown ``format``
    TPL_SLUG              ``slug`` is not a slug, or does not match the directory
    TPL_VERSION           ``version`` is not semver
    TPL_ENGINE_STAGE      the bundle needs a newer engine (``min_engine_stage``)
    TPL_DUPLICATE_KEY     two workflows / tables / APIs share a key
    TPL_ENTRY_COUNT       more than one ``role: entry`` workflow (a library template has none; an ``app`` bundle may have several)
    TPL_DEFINITION        a workflow definition is not a valid definition (unknown key or node type, bad connection...)
    TPL_SUB_TRIGGER       a sub-workflow needs an ExecuteWorkflowTrigger with declared ``fields``
    TPL_PUBLISH_FLAG      entry workflows stay drafts; sub-workflows must be published at install
    TPL_APPROVAL_IN_SUB   an Approval inside a sub-workflow or anything it calls (WF025)
    TPL_CALL_TARGET       an ExecuteWorkflow call to a workflow that is not a sub-workflow
    TPL_WORKFLOW_CYCLE    the workflows of the bundle call each other in a cycle
    TPL_TOKEN_UNRESOLVED  a ``@tpl:`` token names a key the bundle does not declare
    TPL_TOKEN_MISUSE      a ``@tpl:`` token in a place the installer does not replace
    TPL_SECRET_VALUE      something that looks like a key / token / card number / IBAN
    TPL_FAKE_DATA         an email outside ``@example.com``
    TPL_CREDENTIAL        a credential reference with a value
    TPL_HOST_UNDECLARED   a host used by an HTTP step that ``requires.egress_hosts`` does not list
    TPL_HOST_NOT_EXAMPLE  a host that is not a ``*.example`` host
    TPL_TEST_COUNT        fewer tests than ``ci.min_tests_per_workflow``
    TPL_TEST_NO_FAILURE   no test exercises a failure or rejection path
    TPL_TEST_NOT_APPROVED a test that is not ``status: approved``
    TPL_TEST_ID           a test without an id (or two with the same id)
    TPL_TEST_INVALID      a test that does not fit the test schema
    TPL_TEST_UNKNOWN_NODE a test mock or assertion that names a node the workflow does not have
    TPL_API_FILE          an API file that is missing, unreadable or not an OpenAPI document
    TPL_SEED_INVALID      a seed row that does not fit its table
    TPL_VAR_UNDECLARED    ``$vars.NAME`` used but not declared under ``requires``
    TPL_REQUIRES          ``requires`` names something wrong (unknown template, version, used_by target)
    TPL_REQUIRES_CYCLE    ``requires.templates`` forms a cycle
    TPL_FEW_SHOT          ``few_shot`` is malformed
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from pydantic import ValidationError as PydanticValidationError

from ..schemas.template import BUNDLE_FORMAT, SEMVER_RE, SLUG_RE, TemplateBundle

logger = logging.getLogger(__name__)

ENGINE_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ENGINE_DIR / "templates"
TEMPLATES_DIR_ENV = "WORKFLOW_TEMPLATES_DIR"

#: test categories that count as "a failure or rejection path"
FAILURE_CATEGORIES = frozenset({"missing", "wrong_type", "unusual", "ext_error", "ext_timeout", "ext_empty"})
DEFINITION_KEYS = frozenset({"nodes", "connections", "settings", "tests", "test_defaults", "interface"})
NODE_KEYS = frozenset({
    "name", "type", "label", "parameters", "position", "pinned_data", "pinnedData", "retry_on_fail", "retry_delay",
    "continue_on_fail", "disabled", "on_error", "contract",
})
CREDENTIAL_KEYS = frozenset({"credential", "credentialId", "credential_id", "credentials"})
VARS_RE = re.compile(r"\$vars\.([A-Za-z_][A-Za-z0-9_]*)")
TOKEN_FULL_RE = re.compile(r"^@tpl:(workflow|table|api)/([A-Za-z0-9_:.-]+)$")
TOKEN_ANY = "@tpl:"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
FAKE_PAN_PREFIX = "400000"
ENGINE_STAGE = 8  # the stage of this engine; a bundle may ask for at most this

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("API key (sk-)", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("live payment-provider key", re.compile(r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{8,}")),
    ("SendGrid key", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    ("password in a URL", re.compile(r"://[^/\s:@]+:[^/\s:@{]{3,}@")),
]
_PAN_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{2,4}){3,8}\b")   # run on upper-cased text: a lower-case IBAN is still an IBAN
_BASIC_RE = re.compile(r"(?i)\bbasic\s+([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])")
#: domains that can never be a real mailbox (RFC 2606 / 6761)
_RESERVED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
_RESERVED_EMAIL_TLDS = frozenset({"example", "test", "invalid", "localhost"})
#: a JSON number is only treated as a card number from this many digits (shorter ones are timestamps and ids)
_MIN_NUMERIC_PAN_DIGITS = 15


# ── issues ───────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BundleIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}

    def __str__(self) -> str:
        return f"{self.code} at {self.path or '<bundle>'}: {self.message}"


class TemplateBundleError(ValueError):
    """A bundle (or the whole catalog) failed validation; ``issues`` has every problem found."""

    def __init__(self, issues: list[BundleIssue], where: str = "") -> None:
        self.issues = list(issues)
        head = "; ".join(str(i) for i in self.issues[:3])
        more = f" (+{len(self.issues) - 3} more)" if len(self.issues) > 3 else ""
        super().__init__(f"{where + ': ' if where else ''}{head}{more}")


# ── small pure helpers ───────────────────────────────────────────────────────────────────────


def iter_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string value in a JSON-like structure with its path (dict keys are not yielded)."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f"{path}[{i}]")


def iter_scalars(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """What the secret / real-data lint reads: every string, every number (as its digits) and every dict key, with its path.

    A card number written as a JSON number, or a secret used as a key, is as readable by a user as one in a string.
    """
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, int):
        yield path, str(obj)
    elif isinstance(obj, float):
        if obj == obj and abs(obj) != float("inf") and obj == int(obj):
            yield path, str(int(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else str(k)
            if isinstance(k, str):
                yield f"{sub}<key>", k
            yield from iter_scalars(v, sub)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from iter_scalars(v, f"{path}[{i}]")


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def secret_findings(text: str) -> list[str]:
    """What in ``text`` looks like a real secret or real personal data (labels, no values)."""
    found: list[str] = []
    for label, rx in _SECRET_PATTERNS:
        if rx.search(text):
            found.append(label)
    for m in _BASIC_RE.finditer(text):
        if _is_basic_credential(m.group(1)):
            found.append("HTTP Basic credentials")
            break
    numeric = text.strip().isdigit()
    for m in _PAN_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if (_MIN_NUMERIC_PAN_DIGITS if numeric else 13) <= len(digits) <= 19 and luhn_ok(digits) and not digits.startswith(FAKE_PAN_PREFIX):
            found.append("card number (only 4000 00.. test numbers are allowed)")
            break
    for m in _IBAN_RE.finditer(text.upper()):
        groups = m.group(0).split(" ")
        # upper-casing can pull a following word ("... 32 PLEASE") into the match: try shorter tails too
        if any(iban_ok("".join(groups[:n])) for n in range(len(groups), 0, -1)):
            found.append("IBAN with a valid checksum")
            break
    return found


def _is_basic_credential(b64: str) -> bool:
    """``Basic <b64>`` is a credential only when the base64 decodes to ``user:password`` (so the word 'basic' in prose is not)."""
    import base64
    import binascii

    try:
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    user, sep, pw = raw.partition(":")
    return bool(sep and user and pw and raw.isprintable())


def _reserved_email(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return domain in _RESERVED_EMAIL_DOMAINS or domain.rsplit(".", 1)[-1] in _RESERVED_EMAIL_TLDS


def real_emails(text: str) -> list[str]:
    """Addresses outside the reserved example domains (example.com/.org/.net and the .example/.test/.invalid TLDs)."""
    return [m.group(0) for m in EMAIL_RE.finditer(text) if not _reserved_email(m.group(0))]


def host_of(url: str) -> str | None:
    """The host of a literal URL; None for an expression-led or relative one."""
    if not isinstance(url, str) or url.lstrip().startswith(("{{", "=")):
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def host_listed(host: str, patterns: list[str]) -> bool:
    from ..broker.policy import host_matches

    return host_matches(host, patterns)


def parse_semver(v: str) -> tuple[int, int, int] | None:
    if not isinstance(v, str) or not SEMVER_RE.match(v):
        return None
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def satisfies(version: str, wanted: str | None) -> bool:
    """``*`` / empty (anything), ``1.2.3`` (exact), ``^1`` or ``^1.2.3`` (same major, at least that)."""
    have = parse_semver(version)
    if wanted in (None, "", "*") or have is None:
        return wanted in (None, "", "*")
    if wanted.startswith("^"):
        parts = wanted[1:].split(".")
        if not parts or not all(p.isdigit() for p in parts) or len(parts) > 3:
            return False
        want = tuple(int(p) for p in parts) + (0,) * (3 - len(parts))
        return have[0] == want[0] and have >= want  # type: ignore[operator]
    exact = parse_semver(wanted)
    return exact is not None and exact == have


def bundle_hash(raw: dict[str, Any], api_texts: dict[str, str] | None = None) -> str:
    """sha256 of the canonical JSON of the bundle plus its API files."""
    blob = json.dumps({"bundle": raw, "apis": dict(sorted((api_texts or {}).items()))}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── tokens ───────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenUse:
    path: str  # path inside the definition
    kind: str  # workflow | table | api
    key: str   # "key" or "slug:key" (workflow only)


def _site_paths(definition: dict[str, Any]) -> Iterator[tuple[str, str, Callable[[Any], None], Any]]:
    """The places the installer replaces tokens: ``(path, kind, setter, current value)``.

    ExecuteWorkflow / WorkflowTool ``workflowId`` (workflow), DataTable ``table`` (table), HttpRequest ``api_ref.spec_id``
    (api) and ``settings.error_workflow_id`` (workflow)."""
    for i, n in enumerate(definition.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        params = n.get("parameters")
        if not isinstance(params, dict):
            continue
        t = n.get("type")
        base = f"nodes[{i}].parameters"
        if t in ("ExecuteWorkflow", "WorkflowTool") and "workflowId" in params:
            yield f"{base}.workflowId", "workflow", (lambda v, p=params: p.__setitem__("workflowId", v)), params["workflowId"]
        elif t == "DataTable" and "table" in params:
            yield f"{base}.table", "table", (lambda v, p=params: p.__setitem__("table", v)), params["table"]
        elif t == "HttpRequest" and isinstance(params.get("api_ref"), dict) and "spec_id" in params["api_ref"]:
            ref = params["api_ref"]
            yield f"{base}.api_ref.spec_id", "api", (lambda v, r=ref: r.__setitem__("spec_id", v)), ref["spec_id"]
    settings = definition.get("settings")
    if isinstance(settings, dict) and "error_workflow_id" in settings:
        yield "settings.error_workflow_id", "workflow", (lambda v, s=settings: s.__setitem__("error_workflow_id", v)), settings["error_workflow_id"]


def token_uses(definition: dict[str, Any]) -> list[TokenUse]:
    """Every well-formed token at a replaceable place."""
    out: list[TokenUse] = []
    for path, kind, _setter, value in _site_paths(definition):
        m = TOKEN_FULL_RE.match(value) if isinstance(value, str) else None
        if m and m.group(1) == kind:
            out.append(TokenUse(path, kind, m.group(2)))
    return out


def stray_tokens(definition: dict[str, Any]) -> list[tuple[str, str]]:
    """``(path, string)`` of every string that contains ``@tpl:`` but is not a well-formed token at a replaceable place."""
    good = {u.path for u in token_uses(definition)}
    return [(p, s) for p, s in iter_strings(definition) if TOKEN_ANY in s and p not in good]


def replace_tokens(definition: dict[str, Any], resolve: Callable[[str, str], str]) -> dict[str, Any]:
    """A deep copy of ``definition`` with every token replaced by ``resolve(kind, key)``.

    Raises ``TemplateBundleError`` (TPL_TOKEN_MISUSE) if any ``@tpl:`` is left afterwards: nothing leaves the installer
    with a token still in it."""
    out = copy.deepcopy(definition)
    for path, kind, setter, value in _site_paths(out):
        m = TOKEN_FULL_RE.match(value) if isinstance(value, str) else None
        if m and m.group(1) == kind:
            setter(resolve(kind, m.group(2)))
    left = stray_tokens(out)
    if left:
        raise TemplateBundleError([BundleIssue("TPL_TOKEN_MISUSE", p, f"'{s}' was not replaced") for p, s in left])
    return out


# ── definition checks ────────────────────────────────────────────────────────────────────────


def _registry():
    from ..engine.node_registry import node_registry, register_all_nodes

    if not node_registry.has("Set"):
        register_all_nodes()
    return node_registry


def check_definition(definition: Any, where: str) -> list[BundleIssue]:
    """The definition-level rules: known keys, known node types, unique names, connections between real nodes, valid
    settings / test_defaults / interface."""
    from ..schemas.interface import parse_interface
    from ..schemas.workflow import NodeDefinitionSchema, TestDefaultsSchema

    issues: list[BundleIssue] = []

    def bad(path: str, msg: str) -> None:
        issues.append(BundleIssue("TPL_DEFINITION", f"{where}.{path}" if path else where, msg))

    if not isinstance(definition, dict):
        return [BundleIssue("TPL_DEFINITION", where, "the definition must be an object")]
    for k in definition:
        if k not in DEFINITION_KEYS:
            bad(k, f"unknown definition key '{k}' (allowed: {', '.join(sorted(DEFINITION_KEYS))})")
    nodes = definition.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        bad("nodes", "a workflow needs at least one node")
        return issues
    registry = _registry()
    names: list[str] = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            bad(f"nodes[{i}]", "a node must be an object")
            continue
        for k in n:
            if k not in NODE_KEYS:
                bad(f"nodes[{i}].{k}", f"unknown node key '{k}'")
        try:
            NodeDefinitionSchema.model_validate({k: v for k, v in n.items() if k in NODE_KEYS})
        except PydanticValidationError as e:
            err = e.errors()[0]
            bad(f"nodes[{i}]", f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
            continue
        if not registry.has(n.get("type")):
            bad(f"nodes[{i}].type", f"unknown node type '{n.get('type')}'")
        names.append(n["name"])
    dupes = sorted({x for x in names if names.count(x) > 1})
    if dupes:
        bad("nodes", f"node names must be unique: {', '.join(dupes)}")
    known = set(names)
    for i, c in enumerate(definition.get("connections") or []):
        if not isinstance(c, dict) or "source_node" not in c or "target_node" not in c:
            bad(f"connections[{i}]", "a connection needs source_node and target_node")
            continue
        for end in ("source_node", "target_node"):
            if c[end] not in known:
                bad(f"connections[{i}].{end}", f"'{c[end]}' is not a node of this workflow")
    if not isinstance(definition.get("settings", {}), dict):
        bad("settings", "settings must be an object")
    try:
        TestDefaultsSchema.model_validate(definition.get("test_defaults") or {})
    except PydanticValidationError as e:
        bad("test_defaults", e.errors()[0]["msg"])
    if definition.get("interface") is not None:
        _clean, errs = parse_interface(definition["interface"])
        for e in errs:
            bad(f"interface.{e['field']}" if e["field"] else "interface", e["message"])
    return issues


def _calls_of(definition: dict[str, Any]) -> list[TokenUse]:
    return [u for u in token_uses(definition) if u.kind == "workflow"]


def _node_names(definition: dict[str, Any]) -> set[str]:
    return {n.get("name") for n in definition.get("nodes") or [] if isinstance(n, dict)}


def _check_tests(wf_key: str, definition: dict[str, Any], min_tests: int, *, require_failure: bool = True) -> list[BundleIssue]:
    from ..schemas.workflow import TestCaseSchema

    issues: list[BundleIssue] = []
    where = f"workflows.{wf_key}.definition.tests"
    raw_tests = definition.get("tests") or []
    if not isinstance(raw_tests, list):
        return [BundleIssue("TPL_TEST_INVALID", where, "tests must be a list")]
    if len(raw_tests) < min_tests:
        issues.append(BundleIssue("TPL_TEST_COUNT", where,
                                  f"{len(raw_tests)} test(s); a template workflow needs at least {min_tests}"))
    names = _node_names(definition)
    seen_ids: set[str] = set()
    failure_path = False
    for i, t in enumerate(raw_tests):
        at = f"{where}[{i}]"
        if not isinstance(t, dict):
            issues.append(BundleIssue("TPL_TEST_INVALID", at, "a test must be an object"))
            continue
        tid = t.get("id")
        if not tid:
            issues.append(BundleIssue("TPL_TEST_ID", at, "a bundle test needs an explicit id (t_ + 8 hex) so installs are stable"))
        elif tid in seen_ids:
            issues.append(BundleIssue("TPL_TEST_ID", at, f"duplicate test id {tid}"))
        seen_ids.add(tid or "")
        if t.get("status") != "approved":
            issues.append(BundleIssue("TPL_TEST_NOT_APPROVED", at,
                                      f"'{t.get('name', tid)}' is '{t.get('status', 'draft')}'; bundle tests ship approved"))
        try:
            case = TestCaseSchema.model_validate(t)
        except PydanticValidationError as e:
            err = e.errors()[0]
            issues.append(BundleIssue("TPL_TEST_INVALID", at, f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"))
            continue
        if case.category in FAILURE_CATEGORIES:
            failure_path = True
        refs: list[tuple[str, str]] = [(f"{at}.mocks.{k}", k) for k in (case.mocks or {})]
        refs += [(f"{at}.expect[{j}].node", str(a.get("node"))) for j, a in enumerate(case.expect) if a.get("node")]
        tgt = case.target
        refs += [(f"{at}.target.{k}", tgt[k]) for k in ("node", "from", "to") if tgt.get(k)]
        for p, ref in refs:
            if ref and not ref.startswith("$") and ref not in names and ref.rstrip("?") not in names:   # "Is it large?" is a real node name
                issues.append(BundleIssue("TPL_TEST_UNKNOWN_NODE", p, f"'{ref}' is not a node of this workflow"))
    if raw_tests and not failure_path and require_failure:
        issues.append(BundleIssue("TPL_TEST_NO_FAILURE", where,
                                  "no test exercises a failure or rejection path (category one of "
                                  + ", ".join(sorted(FAILURE_CATEGORIES)) + ")"))
    return issues


def _approval_closure(workflows: dict[str, dict[str, Any]], key: str, seen: set[str] | None = None) -> bool:
    """Does workflow ``key`` or anything it calls inside the bundle contain an Approval?"""
    seen = seen if seen is not None else set()
    if key in seen or key not in workflows:
        return False
    seen.add(key)
    d = workflows[key]
    if any(isinstance(n, dict) and n.get("type") == "Approval" for n in d.get("nodes") or []):
        return True
    return any(_approval_closure(workflows, u.key, seen) for u in _calls_of(d) if ":" not in u.key)


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if state.get(m) is None:
                c = visit(m)
                if c:
                    return c
        stack.pop()
        state[n] = 2
        return None

    for n in graph:
        if state.get(n) is None:
            c = visit(n)
            if c:
                return c
    return None


def _spec_for_api(api: Any, api_texts: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """The normalised ``{base_url, operations}`` of a bundle API, or ``(None, problem)``."""
    from .openapi_import import SpecError, import_openapi

    if api.spec is not None:
        if not isinstance(api.spec.get("operations"), list):
            return None, "an inline spec needs an 'operations' list"
        return {"base_url": api.spec.get("base_url") or "", "operations": api.spec["operations"]}, None
    if not api.file:
        return None, "give either 'file' or 'spec'"
    text = api_texts.get(api.key)
    if text is None:
        return None, f"file '{api.file}' was not found next to template.json"
    try:
        imported = import_openapi(text)
    except SpecError as e:
        return None, f"file '{api.file}' is not a usable OpenAPI document: {e}"
    except Exception as e:  # a parser error must be a finding, never a crash
        return None, f"file '{api.file}' could not be read: {e}"
    return {"base_url": imported["base_url"], "operations": imported["operations"], "title": imported.get("title")}, None


def normalized_apis(bundle: TemplateBundle, api_texts: dict[str, str]) -> dict[str, dict[str, Any]]:
    """key -> ``{base_url, operations}`` for every API that parses (the installer registers exactly this)."""
    out: dict[str, dict[str, Any]] = {}
    for a in bundle.apis:
        spec, _problem = _spec_for_api(a, api_texts)
        if spec is not None:
            out[a.key] = spec
    return out


# ── the lint ─────────────────────────────────────────────────────────────────────────────────


PROFILES = ("catalog", "generated")


def lint_bundle(
    bundle: TemplateBundle, api_texts: dict[str, str] | None = None, *, expected_slug: str | None = None,
    catalog: dict[str, "LoadedTemplate"] | None = None, profile: str = "catalog",
) -> list[BundleIssue]:
    """Every rule of section 8.2 on a parsed bundle. Returns all issues (empty = a valid bundle).

    ``profile="catalog"`` (the default, what shipped templates are held to) is unchanged. ``profile="generated"`` is for a bundle the AI
    app composer compiled from a person's own project: the authoring rules for *shipped* templates do not apply (``TPL_HOST_NOT_EXAMPLE``,
    ``TPL_FAKE_DATA``, ``TPL_TEST_NO_FAILURE``, and ``api_ref.spec_id`` may name the project's own API spec), while every secret, token,
    test-approval, sub-workflow, approval and cycle rule stays."""
    generated = profile == "generated"
    if profile not in PROFILES:
        raise ValueError(f"unknown lint profile {profile!r}")
    api_texts = api_texts or {}
    issues: list[BundleIssue] = []
    add = issues.append

    # 1. header
    if bundle.format != BUNDLE_FORMAT:
        add(BundleIssue("TPL_FORMAT", "format", f"unknown format '{bundle.format}'; this engine reads '{BUNDLE_FORMAT}'"))
    if not SLUG_RE.match(bundle.slug):
        add(BundleIssue("TPL_SLUG", "slug", "use lowercase letters, digits and single dashes"))
    if expected_slug is not None and bundle.slug != expected_slug:
        add(BundleIssue("TPL_SLUG", "slug", f"slug '{bundle.slug}' does not match its directory '{expected_slug}'"))
    if parse_semver(bundle.version) is None:
        add(BundleIssue("TPL_VERSION", "version", f"'{bundle.version}' is not a semantic version (1.2.3)"))

    if bundle.min_engine_stage > ENGINE_STAGE:
        add(BundleIssue("TPL_ENGINE_STAGE", "min_engine_stage",
                        f"needs engine stage {bundle.min_engine_stage}; this engine is stage {ENGINE_STAGE}"))

    # 2. keys
    for label, items in (("workflows", bundle.workflows), ("tables", bundle.tables), ("apis", bundle.apis)):
        seen: set[str] = set()
        for it in items:
            if it.key in seen:
                add(BundleIssue("TPL_DUPLICATE_KEY", f"{label}.{it.key}", f"the key '{it.key}' is used twice"))
            seen.add(it.key)
    names_seen: set[str] = set()
    for w in bundle.workflows:
        if w.name in names_seen:
            add(BundleIssue("TPL_DUPLICATE_KEY", f"workflows.{w.key}.name", f"two workflows are named '{w.name}'"))
        names_seen.add(w.name)

    wf_keys = {w.key for w in bundle.workflows}
    table_keys = {t.key for t in bundle.tables}
    api_keys = {a.key for a in bundle.apis}
    required_slugs = {r.slug for r in bundle.requires.templates}
    roles = {w.key: w.role for w in bundle.workflows}

    # 3. entry count
    # (a library template such as customer-notify ships only sub-workflows, so it has no entry at all)
    entries = [w.key for w in bundle.workflows if w.role == "entry"]
    if len(entries) > 1 and bundle.app is None:
        add(BundleIssue("TPL_ENTRY_COUNT", "workflows",
                        f"at most one workflow can have role 'entry'; found {len(entries)} ({', '.join(entries)})"))

    defs: dict[str, dict[str, Any]] = {w.key: w.definition for w in bundle.workflows if isinstance(w.definition, dict)}
    graph: dict[str, list[str]] = {}

    # 4. per workflow
    for w in bundle.workflows:
        where = f"workflows.{w.key}.definition"
        d_issues = check_definition(w.definition, where)
        issues.extend(d_issues)
        if not isinstance(w.definition, dict):
            continue
        d = w.definition

        if w.role == "entry" and w.publish:
            add(BundleIssue("TPL_PUBLISH_FLAG", f"workflows.{w.key}.publish",
                            "entry workflows stay drafts (their triggers must not switch on at install); set publish to false"))
        if w.role == "subworkflow" and not w.publish:
            add(BundleIssue("TPL_PUBLISH_FLAG", f"workflows.{w.key}.publish",
                            "sub-workflows are published at install so that 'latest' pins resolve; set publish to true"))

        if w.role == "subworkflow":
            trig = [n for n in d.get("nodes") or [] if isinstance(n, dict) and n.get("type") == "ExecuteWorkflowTrigger"]
            ok = False
            for n in trig:
                p = n.get("parameters") or {}
                if p.get("inputSource", "fields") == "fields" and isinstance(p.get("fields"), list) and p["fields"]:
                    ok = True
            if not ok:
                add(BundleIssue("TPL_SUB_TRIGGER", f"workflows.{w.key}.definition.nodes",
                                "a sub-workflow needs an ExecuteWorkflowTrigger with inputSource 'fields' and at least one declared field"))

        # tokens
        for u in token_uses(d):
            if u.kind == "workflow":
                if ":" in u.key:
                    slug, _, k = u.key.partition(":")
                    if slug not in required_slugs:
                        add(BundleIssue("TPL_TOKEN_UNRESOLVED", f"{where}.{u.path}",
                                        f"'@tpl:workflow/{u.key}' names template '{slug}', which is not in requires.templates"))
                    elif catalog is not None:
                        dep = catalog.get(slug)
                        if dep is None or not any(x.key == k and x.role == "subworkflow" for x in dep.bundle.workflows):
                            add(BundleIssue("TPL_TOKEN_UNRESOLVED", f"{where}.{u.path}",
                                            f"template '{slug}' has no sub-workflow with key '{k}'"))
                elif u.key not in wf_keys:
                    add(BundleIssue("TPL_TOKEN_UNRESOLVED", f"{where}.{u.path}", f"no workflow with key '{u.key}' in this bundle"))
                else:
                    if roles.get(u.key) != "subworkflow":
                        add(BundleIssue("TPL_CALL_TARGET", f"{where}.{u.path}",
                                        f"'{u.key}' is not a sub-workflow; only sub-workflows can be called"))
                    graph.setdefault(w.key, []).append(u.key)
            elif u.kind == "table" and u.key not in table_keys:
                add(BundleIssue("TPL_TOKEN_UNRESOLVED", f"{where}.{u.path}", f"no table with key '{u.key}' in this bundle"))
            elif u.kind == "api" and u.key not in api_keys:
                add(BundleIssue("TPL_TOKEN_UNRESOLVED", f"{where}.{u.path}", f"no API with key '{u.key}' in this bundle"))
        for p, s in stray_tokens(d):
            add(BundleIssue("TPL_TOKEN_MISUSE", f"{where}.{p}",
                            f"'{s}' is not at a place the installer replaces (ExecuteWorkflow.workflowId, DataTable.table, "
                            f"HttpRequest.api_ref.spec_id, settings.error_workflow_id) or is not a well-formed token"))

        # tests
        issues.extend(_check_tests(w.key, d, bundle.ci.min_tests_per_workflow, require_failure=not generated))

        # credentials
        for i, n in enumerate(d.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            for k, v in (n.get("parameters") or {}).items():
                if k in CREDENTIAL_KEYS and v not in (None, "", {}, []) and not (isinstance(v, str) and TOKEN_FULL_RE.match(v)):
                    add(BundleIssue("TPL_CREDENTIAL", f"{where}.nodes[{i}].parameters.{k}",
                                    "a credential reference cannot ship in a template; leave it empty and list the secret under requires"))

    # 5. approvals never in a sub-workflow (WF025)
    for w in bundle.workflows:
        if w.role == "subworkflow" and w.key in defs and _approval_closure(defs, w.key):
            add(BundleIssue("TPL_APPROVAL_IN_SUB", f"workflows.{w.key}",
                            f"'{w.name}' (or a sub-workflow it calls) contains an Approval; approvals belong in the top-level workflow (WF025)"))

    # 6. cycles
    cyc = _find_cycle(graph)
    if cyc:
        add(BundleIssue("TPL_WORKFLOW_CYCLE", "workflows", "workflows call each other in a cycle: " + " -> ".join(cyc)))

    # 7. secrets and real data (everything a user could read: workflows, seeds, few_shot, the bundled API files),
    #    strings, numbers and keys alike
    scan: list[tuple[str, str]] = []
    for w in bundle.workflows:
        scan += [(f"workflows.{w.key}.definition.{p}", s) for p, s in iter_scalars(w.definition)]
    for t in bundle.tables:
        scan += [(f"tables.{t.key}.seed_rows.{p}", s) for p, s in iter_scalars(t.seed_rows)]
    if bundle.few_shot is not None:
        scan += [(f"few_shot.{p}", s) for p, s in iter_scalars(bundle.few_shot.model_dump())]
    for key, text in sorted(api_texts.items()):
        scan += [(f"apis.{key}.line{n}", line) for n, line in enumerate(text.splitlines(), 1)]
    for p, s in scan:
        for label in secret_findings(s):
            add(BundleIssue("TPL_SECRET_VALUE", p, f"looks like a real {label}; templates carry names, never values"))
        for e in ([] if generated else real_emails(s)):
            add(BundleIssue("TPL_FAKE_DATA", p, f"'{e}' is not an @example.com address; use reserved fake data"))

    # 8. tables
    for t in bundle.tables:
        cols = {c.name: c for c in t.columns}
        if len(cols) != len(t.columns):
            add(BundleIssue("TPL_SEED_INVALID", f"tables.{t.key}.columns", "duplicate column names"))
        from .data_table_service import _type_ok

        for i, row in enumerate(t.seed_rows):
            at = f"tables.{t.key}.seed_rows[{i}]"
            for k in row:
                if k not in cols:
                    add(BundleIssue("TPL_SEED_INVALID", f"{at}.{k}", f"unknown column '{k}'"))
            for name, c in cols.items():
                v = row.get(name, c.default)
                if v is None and c.required:
                    add(BundleIssue("TPL_SEED_INVALID", f"{at}.{name}", "is required"))
                elif v is not None and not _type_ok(c.type, v):
                    add(BundleIssue("TPL_SEED_INVALID", f"{at}.{name}", f"expected {c.type}"))

    # 9. APIs and hosts
    allowed_hosts = list(bundle.requires.egress_hosts)
    for h in allowed_hosts:
        if not generated and not h.lower().rstrip("/").endswith(".example"):
            add(BundleIssue("TPL_HOST_NOT_EXAMPLE", "requires.egress_hosts", f"'{h}' is not a *.example host"))
    specs: dict[str, dict[str, Any]] = {}
    for a in bundle.apis:
        spec, problem = _spec_for_api(a, api_texts)
        if spec is None:
            add(BundleIssue("TPL_API_FILE", f"apis.{a.key}", problem or "unusable API"))
            continue
        specs[a.key] = spec
        host = host_of(spec.get("base_url") or "")
        if host is None:
            add(BundleIssue("TPL_API_FILE", f"apis.{a.key}", "the API has no base URL (servers[0].url)"))
        else:
            _check_host(issues, host, allowed_hosts, f"apis.{a.key}", example=not generated)
    for w in bundle.workflows:
        for i, n in enumerate((w.definition.get("nodes") if isinstance(w.definition, dict) else None) or []):
            if not isinstance(n, dict) or n.get("type") != "HttpRequest":
                continue
            params = n.get("parameters") or {}
            at = f"workflows.{w.key}.definition.nodes[{i}]"
            ref = params.get("api_ref")
            if isinstance(ref, dict) and ref.get("spec_id"):
                m = TOKEN_FULL_RE.match(ref["spec_id"]) if isinstance(ref["spec_id"], str) else None
                if m and m.group(1) == "api" and m.group(2) in specs:
                    ops = {o.get("operation_id") for o in specs[m.group(2)]["operations"]}
                    if ref.get("operation_id") not in ops:
                        add(BundleIssue("TPL_API_FILE", f"{at}.parameters.api_ref.operation_id",
                                        f"API '{m.group(2)}' has no operation '{ref.get('operation_id')}'"))
                elif not m and not generated:   # a generated bundle may name the project's own API spec by id
                    add(BundleIssue("TPL_TOKEN_MISUSE", f"{at}.parameters.api_ref.spec_id",
                                    "api_ref.spec_id must be an @tpl:api/<key> token in a template"))
            else:
                host = host_of(params.get("url") or "")
                if host is not None:
                    _check_host(issues, host, allowed_hosts, f"{at}.parameters.url", example=not generated)

    # 10. variables / secrets
    declared = {s.name for s in bundle.requires.secrets} | {v.name for v in bundle.requires.variables}
    used: dict[str, str] = {}
    for w in bundle.workflows:
        for p, s in iter_strings(w.definition):
            for m in VARS_RE.finditer(s):
                used.setdefault(m.group(1), f"workflows.{w.key}.definition.{p}")
    for name, at in sorted(used.items()):
        if name not in declared:
            add(BundleIssue("TPL_VAR_UNDECLARED", at, f"$vars.{name} is used but not listed under requires.secrets / requires.variables"))
    for s in bundle.requires.secrets:
        for ub in s.used_by:
            wk, _, node = ub.partition("/")
            if wk not in defs or node not in _node_names(defs[wk]):
                add(BundleIssue("TPL_REQUIRES", f"requires.secrets.{s.name}.used_by", f"'{ub}' is not a '<workflow key>/<node name>' of this bundle"))
    for r in bundle.requires.templates:
        if r.slug == bundle.slug:
            add(BundleIssue("TPL_REQUIRES_CYCLE", "requires.templates", f"'{r.slug}' requires itself"))
        elif catalog is not None:
            dep = catalog.get(r.slug)
            if dep is None:
                add(BundleIssue("TPL_REQUIRES", "requires.templates", f"required template '{r.slug}' is not in the catalog"))
            elif not satisfies(dep.bundle.version, r.version):
                add(BundleIssue("TPL_REQUIRES", "requires.templates",
                                f"'{r.slug}' {dep.bundle.version} does not satisfy '{r.version}'"))

    # 11. few shot
    if bundle.few_shot is not None and not isinstance(bundle.few_shot.spec.get("steps", []), list):
        add(BundleIssue("TPL_FEW_SHOT", "few_shot.spec.steps", "steps must be a list"))

    return issues


def _check_host(issues: list[BundleIssue], host: str, allowed: list[str], at: str, *, example: bool = True) -> None:
    if example and not host.endswith(".example"):
        issues.append(BundleIssue("TPL_HOST_NOT_EXAMPLE", at, f"'{host}' is not a *.example host"))
    if not host_listed(host, allowed):
        issues.append(BundleIssue("TPL_HOST_UNDECLARED", at, f"'{host}' is not listed in requires.egress_hosts"))


# ── parse + validate ─────────────────────────────────────────────────────────────────────────


def parse_bundle(raw: Any) -> TemplateBundle:
    """Strict structural parse. Raises ``TemplateBundleError`` (TPL_SCHEMA) naming every offending field."""
    if not isinstance(raw, dict):
        raise TemplateBundleError([BundleIssue("TPL_SCHEMA", "", "the bundle must be a JSON object")])
    try:
        return TemplateBundle.model_validate(raw)
    except PydanticValidationError as e:
        issues = []
        for err in e.errors():
            path = ".".join(str(p) for p in err["loc"])
            code = "TPL_FORMAT" if path == "format" else "TPL_SCHEMA"
            issues.append(BundleIssue(code, path, err["msg"]))
        raise TemplateBundleError(issues) from None


def validate_bundle(
    raw: Any, api_texts: dict[str, str] | None = None, *, expected_slug: str | None = None,
    catalog: dict[str, "LoadedTemplate"] | None = None, profile: str = "catalog",
) -> tuple[TemplateBundle, list[BundleIssue]]:
    """Parse and lint. Raises on a structural failure; otherwise returns the bundle and every lint issue."""
    if isinstance(raw, dict) and raw.get("format") != BUNDLE_FORMAT:
        raise TemplateBundleError([BundleIssue("TPL_FORMAT", "format",
                                               f"unknown format {raw.get('format')!r}; this engine reads '{BUNDLE_FORMAT}'")])
    bundle = parse_bundle(raw)
    return bundle, lint_bundle(bundle, api_texts, expected_slug=expected_slug, catalog=catalog, profile=profile)


# ── the catalog ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoadedTemplate:
    bundle: TemplateBundle
    raw: dict[str, Any]
    api_texts: dict[str, str]
    readme: str
    hash: str
    path: Path | None = None
    warnings: tuple[BundleIssue, ...] = field(default_factory=tuple)


def load_template_dir(path: Path, catalog: dict[str, LoadedTemplate] | None = None) -> LoadedTemplate:
    """Read ``<path>/template.json`` (+ its API files and README) and validate it. Raises ``TemplateBundleError``."""
    file = path / "template.json"
    if not file.is_file():
        raise TemplateBundleError([BundleIssue("TPL_SCHEMA", "", f"{file.name} not found")], where=path.name)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TemplateBundleError([BundleIssue("TPL_SCHEMA", "", f"template.json is not valid JSON: {e}")], where=path.name) from None
    api_texts: dict[str, str] = {}
    for a in (raw.get("apis") or []) if isinstance(raw, dict) else []:
        if isinstance(a, dict) and a.get("file") and a.get("key"):
            target = (path / a["file"]).resolve()
            if path.resolve() in target.parents and target.is_file():
                api_texts[a["key"]] = target.read_text(encoding="utf-8")
    bundle, issues = validate_bundle(raw, api_texts, expected_slug=path.name, catalog=catalog)
    if issues:
        raise TemplateBundleError(issues, where=path.name)
    readme = ""
    rf = path / "README.md"
    if rf.is_file():
        readme = rf.read_text(encoding="utf-8")
    return LoadedTemplate(bundle=bundle, raw=raw, api_texts=api_texts, readme=readme, hash=bundle_hash(raw, api_texts), path=path)


def templates_dir() -> Path:
    override = os.environ.get(TEMPLATES_DIR_ENV)
    return Path(override) if override else TEMPLATES_DIR


def strict_catalog() -> bool:
    """A bad bundle fails loudly in dev and test; production skips it with an error log."""
    return (os.environ.get("WORKFLOW_ENV") or "").lower() not in ("production", "prod")


def load_catalog(directory: Path | None = None, strict: bool | None = None) -> dict[str, LoadedTemplate]:
    """Load, validate and cross-check every ``<dir>/<slug>/template.json``. ``{slug: LoadedTemplate}`` sorted by slug."""
    directory = directory or templates_dir()
    strict = strict_catalog() if strict is None else strict
    problems: list[BundleIssue] = []
    loaded: dict[str, LoadedTemplate] = {}
    if directory.is_dir():
        for sub in sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            try:
                loaded[sub.name] = load_template_dir(sub)
            except TemplateBundleError as e:
                problems += [BundleIssue(i.code, f"{sub.name}:{i.path}" if i.path else sub.name, i.message) for i in e.issues]
    # cross-template: requires exist / satisfied / acyclic, and cross-template tokens point at real sub-workflows
    for slug, lt in list(loaded.items()):
        extra = [i for i in lint_bundle(lt.bundle, lt.api_texts, expected_slug=slug, catalog=loaded)]
        if extra:
            problems += [BundleIssue(i.code, f"{slug}:{i.path}" if i.path else slug, i.message) for i in extra]
            loaded.pop(slug, None)
    cyc = _find_cycle({s: [r.slug for r in lt.bundle.requires.templates if r.slug in loaded] for s, lt in loaded.items()})
    if cyc:
        problems.append(BundleIssue("TPL_REQUIRES_CYCLE", cyc[0], "requires.templates cycle: " + " -> ".join(cyc)))
        for s in cyc:
            loaded.pop(s, None)
    if problems:
        if strict:
            raise TemplateBundleError(problems, where="template catalog")
        for p in problems:
            logger.error("template catalog: skipped a bad bundle: %s", p)
    return dict(sorted(loaded.items()))


@lru_cache(maxsize=4)
def _cached_catalog(directory: str, strict: bool) -> dict[str, LoadedTemplate]:
    return load_catalog(Path(directory), strict)


def get_catalog() -> dict[str, LoadedTemplate]:
    """The shipped catalog (loaded once per process and directory)."""
    return _cached_catalog(str(templates_dir()), strict_catalog())


def clear_catalog_cache() -> None:
    _cached_catalog.cache_clear()
