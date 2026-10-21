"""Static checks the builder runs on generated code, beyond syntax: things the platform would silently accept but that are
wrong or unsafe. Each returns ``[{code, line, message}]`` in the shape ``problem_line`` prints."""

from __future__ import annotations

import ast
import re
from typing import Any

from ...sdk.static.jobs import JOB_FIELDS, STEP_ALIASES, TARGET_ROLES, TEMPLATE_FIELDS, UI_STEPS

_ROLE_KEY = re.compile(r"role|admin|permission|privilege|is_?lead|scope", re.IGNORECASE)
_CRED_KEY = re.compile(r"^(password|passwd|pwd|credentials?|api_?key|secret|client_secret|auth_?token)$", re.IGNORECASE)


def _chain(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _dict_of(fn: ast.AST, arg: ast.AST) -> ast.Dict | None:
    """The dict literal an argument stands for: itself, or the literal last assigned to that name in the function."""
    if isinstance(arg, ast.Dict):
        return arg
    if isinstance(arg, ast.Name):
        found = None
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets) \
                    and isinstance(n.value, ast.Dict):
                found = n.value
        return found
    return None


def _is_secret_ref(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _chain(node.func) in ("studio.secret", "secret")


# ── provenance: where a credentials value comes from ──────────────────────────
# "secret" = derived from studio.secret(...) (and nothing from the request); "literal" = constants only; "bad" = anything
# that may carry request data (the handler's parameters, req.*, unresolvable names).

_MAX_DEPTH = 6


def _fn_params(fn: ast.AST) -> set[str]:
    a = getattr(fn, "args", None)
    if a is None:
        return set()
    names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    names += [x.arg for x in (a.vararg, a.kwarg) if x is not None]
    return set(names)


def _combine(kinds: list[str]) -> str:
    if "bad" in kinds:
        return "bad"
    return "secret" if "secret" in kinds else "literal"


def _module_fn(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next((f for f in tree.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == name), None)


def _assignments(fn: ast.AST, name: str) -> list[ast.AST]:
    """Every value assigned to ``name`` in the function: ``x = v``, ``x: T = v``, ``x |= v`` and ``for x in v``."""
    out: list[ast.AST] = []
    for n in ast.walk(fn):
        if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)) \
                or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name and n.value is not None) \
                or (isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == name):
            out.append(n.value)
        elif isinstance(n, (ast.For, ast.comprehension)) and isinstance(n.target, ast.Name) and n.target.id == name:
            out.append(n.iter)
    return out


def _provenance(node: ast.AST, fn: ast.AST, tree: ast.Module, depth: int = 0) -> str:
    if depth > _MAX_DEPTH:
        return "bad"
    if isinstance(node, ast.Constant):
        return "literal"
    if _is_secret_ref(node):
        return "secret"
    if isinstance(node, ast.Name):
        if node.id in _fn_params(fn):
            return "bad"
        vals = _assignments(fn, node.id)
        if not vals:
            mod = [a.value for a in tree.body if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in a.targets)]
            vals = mod
        if not vals:
            return "bad"
        return _combine([_provenance(v, fn, tree, depth + 1) for v in vals])
    if isinstance(node, ast.Call):
        callee = node.func
        if isinstance(callee, ast.Name) and (helper := _module_fn(tree, callee.id)) is not None and helper is not fn:
            rets = [r.value for r in ast.walk(helper) if isinstance(r, ast.Return) and r.value is not None]
            return _combine([_provenance(r, helper, tree, depth + 1) for r in rets]) if rets else "bad"
        parts = [callee.value] if isinstance(callee, ast.Attribute) else []
        parts += list(node.args) + [k.value for k in node.keywords]
        return _combine([_provenance(p, fn, tree, depth + 1) for p in parts])
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        root = node
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        return _provenance(root, fn, tree, depth + 1) if not isinstance(root, ast.Name) or root.id not in _fn_params(fn) else "bad"
    if isinstance(node, ast.JoinedStr):
        return _combine([_provenance(v.value, fn, tree, depth + 1) for v in node.values if isinstance(v, ast.FormattedValue)] or ["literal"])
    if isinstance(node, ast.BinOp):
        return _combine([_provenance(node.left, fn, tree, depth + 1), _provenance(node.right, fn, tree, depth + 1)])
    if isinstance(node, ast.BoolOp):
        return _combine([_provenance(v, fn, tree, depth + 1) for v in node.values])
    if isinstance(node, ast.IfExp):
        return _combine([_provenance(node.body, fn, tree, depth + 1), _provenance(node.orelse, fn, tree, depth + 1)])
    if isinstance(node, (ast.Tuple, ast.List)):
        return _combine([_provenance(e, fn, tree, depth + 1) for e in node.elts] or ["literal"])
    return "bad"


def _dict_values(node: ast.AST, fn: ast.AST, tree: ast.Module, depth: int = 0) -> list[ast.AST] | None:
    """The value expressions that end up in a credentials dict, or None when the dict cannot be resolved statically."""
    if depth > _MAX_DEPTH:
        return None
    if isinstance(node, ast.Dict):
        vals: list[ast.AST] = []
        for k, v in zip(node.keys, node.values):
            if k is None:                                   # {**other}
                sub = _dict_values(v, fn, tree, depth + 1)
                if sub is None:
                    return None
                vals += sub
            else:
                vals.append(v)
        return vals
    if isinstance(node, ast.DictComp):
        return [node.value]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        vals = [k.value for k in node.keywords if k.arg is not None]
        for a in node.args:
            sub = _dict_values(a, fn, tree, depth + 1)
            if sub is None:
                return None
            vals += sub
        for k in node.keywords:
            if k.arg is None:
                sub = _dict_values(k.value, fn, tree, depth + 1)
                if sub is None:
                    return None
                vals += sub
        return vals
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (helper := _module_fn(tree, node.func.id)) is not None:
        vals = []
        for r in [r.value for r in ast.walk(helper) if isinstance(r, ast.Return) and r.value is not None]:
            sub = _dict_values(r, helper, tree, depth + 1)
            if sub is None:
                return None
            vals += sub
        return vals
    if isinstance(node, ast.Name):
        if node.id in _fn_params(fn):
            return None
        vals = []
        found = False
        for n in ast.walk(fn):
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == node.id and n.value is not None:
                        found = True
                        sub = _dict_values(n.value, fn, tree, depth + 1)
                        if sub is None:
                            return None
                        vals += sub
                    elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == node.id and n.value is not None:
                        vals.append(n.value)                # d["k"] = v
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == node.id:
                sub = _dict_values(n.value, fn, tree, depth + 1)
                if sub is None:
                    return None
                vals += sub
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == node.id and n.func.attr in ("update", "setdefault"):
                if n.func.attr == "setdefault":
                    vals += n.args[1:2]
                    continue
                for a in n.args:
                    sub = _dict_values(a, fn, tree, depth + 1)
                    if sub is None:
                        return None
                    vals += sub
                vals += [k.value for k in n.keywords if k.arg is not None]
        return vals if found else None
    return None


def job_start_problems(source: str) -> list[dict[str, Any]]:
    """``studio.jobs.start({...})``: fields the Jobs service ignores, and credentials that are not secret references."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict[str, Any]] = []
    for fn in _functions(tree):
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _chain(n.func) in ("studio.jobs.start", "jobs.start")]:
            body = _dict_of(fn, call.args[0]) if call.args else None
            if body is None:
                continue
            keys = {k.value: v for k, v in zip(body.keys, body.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            typ = keys.get("type")
            known = JOB_FIELDS.get(typ.value) if isinstance(typ, ast.Constant) and isinstance(typ.value, str) else None
            if known is None and not any(True for _ in keys.get("type", [])) and "type" not in keys:
                out.append({"code": "job_body", "line": call.lineno,
                            "message": "studio.jobs.start body has no 'type' (test-run, triage or explore)"})
                continue
            if known is not None:
                extra = sorted(k for k in keys if k not in known)
                if extra:
                    out.append({"code": "unknown_job_field", "line": call.lineno,
                                "message": (f"the Jobs service ignores {', '.join(extra)} for type {typ.value}; allowed: "
                                            f"{', '.join(sorted(known))}")})
            cred = keys.get("credentials")
            if cred is not None:
                vals = _dict_values(cred, fn, tree)
                if vals is None:
                    out.append({"code": "insecure_credentials", "line": call.lineno,
                                "message": ("credentials must be a dict whose values derive from studio.secret('NAME') "
                                            "(a literal dict, or one built with .update()/item assignment from secret references)")})
                elif any(_provenance(v, fn, tree) != "secret" for v in vals):
                    out.append({"code": "insecure_credentials", "line": call.lineno,
                                "message": ("credentials values must derive from studio.secret('NAME') references resolved on the "
                                            "server, never request data or literals")})
    return out


# ── Jobs wiring against the Jobs service contract (apps/jobs-service/README.md, sdk/static/jobs.py) ──────────────────
# JOB_FIELDS (the SDK) says which top-level fields the service reads; what it REQUIRES per type, which keys policy takes,
# which fields an API case request and its ``expect`` read, and that ``{{key}}`` / ``{{totp:key}}`` in a step resolve
# against ``credentials`` keys and ``save`` variables is the same contract. Findings name the call, the field and the fix.

JOB_REQUIRED: dict[str, tuple[str, ...]] = {"test-run": ("target", "cases"), "triage": ("job_id", "case_id"),
                                            "explore": ("target", "goal")}
JOB_POLICY_KEYS = frozenset({"forbidden_names", "forbidden_requests"})
JOB_REQUEST_KEYS = frozenset({"name", "method", "path", "url", "headers", "body", "expect", "save"})
JOB_EXPECT_KEYS = frozenset({"status", "json_path_equals", "schema"})
_TEMPLATE = re.compile(r"\{\{\s*(totp:)?([A-Za-z_][\w.-]*)\s*\}\}")


def _str_keys(d: ast.Dict) -> dict[str, ast.AST]:
    return {k.value: v for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _mutations(fn: ast.AST, name: str) -> tuple[set[str], bool]:
    """Keys added to the dict ``name`` after its literal (``d["k"] = v``, ``d.update({...})``), and whether anything
    unresolvable also changes it (a computed key, ``update(other)``, ``**spread``), in which case the key set is unknown."""
    keys: set[str] = set()
    unknown = False
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == name:
                    if isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                        keys.add(t.slice.value)
                    else:
                        unknown = True
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == name and n.func.attr in ("update", "setdefault"):
            if n.func.attr == "setdefault" and n.args and isinstance(n.args[0], ast.Constant):
                keys.add(str(n.args[0].value))
            elif n.args and isinstance(n.args[0], ast.Dict) and None not in n.args[0].keys:
                keys |= set(_str_keys(n.args[0]))
            else:
                unknown = True
    return keys, unknown


def _known_keys(fn: ast.AST, node: ast.AST) -> set[str] | None:
    """All keys of the dict an expression stands for, or None when they cannot be known statically."""
    d = _dict_of(fn, node)
    if d is None or None in d.keys:
        return None
    keys = set(_str_keys(d))
    if isinstance(node, ast.Name):
        more, unknown = _mutations(fn, node.id)
        if unknown:
            return None
        keys |= more
    return keys


def _prose_strings(tree: ast.AST) -> set[int]:
    """ids of string constants that are prose, not values: docstrings and other bare string statements."""
    return {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}


def _item_values(fn: ast.AST, name: str, key: str) -> list[ast.AST]:
    """Every value put under ``key`` of the dict ``name`` after its literal: ``name[key] = v`` and ``name.update({key: v})``."""
    out: list[ast.AST] = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == name \
                        and isinstance(t.slice, ast.Constant) and t.slice.value == key:
                    out.append(n.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == name and n.args and isinstance(n.args[0], ast.Dict):
            if key in _str_keys(n.args[0]):
                out.append(_str_keys(n.args[0])[key])
    return out


def _credential_keys(fn: ast.AST, body: ast.AST, fields: dict[str, ast.AST]) -> set[str] | None:
    """The keys of the start body's ``credentials``: from its literal and from items assigned to the body afterwards
    (``body["credentials"] = {...}`` in each branch). None when they cannot be known statically."""
    vals = [fields["credentials"]] if "credentials" in fields else []
    if isinstance(body, ast.Name):
        vals += _item_values(fn, body.id, "credentials")
    keys: set[str] = set()
    for v in vals:
        k = _known_keys(fn, v)
        if k is None:
            return None
        keys |= k
    return keys


def job_contract_problems(source: str) -> list[dict[str, Any]]:
    """``studio.jobs.start({...})`` checked against what the Jobs service requires and reads: required fields per type,
    ``target.base_url``, an explicit ``policy`` of the shape the service reads, API case request/``expect`` fields it ignores
    (e.g. ``max_ms``), and ``{{credential}}`` / ``{{totp:credential}}`` references with no matching ``credentials`` key."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict[str, Any]] = []
    templates: dict[str, list[tuple[bool, int]]] = {}
    prose = _prose_strings(tree)            # a docstring that mentions {{user}} is not a step that uses it
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in prose:
            for m in _TEMPLATE.finditer(n.value):
                templates.setdefault(m.group(2), []).append((bool(m.group(1)), n.lineno))
    for fn in _functions(tree):
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _chain(n.func) in ("studio.jobs.start", "jobs.start")]:
            if not call.args:
                continue
            body = call.args[0]
            keys = _known_keys(fn, body)
            lit = _dict_of(fn, body)
            if keys is None or lit is None:
                continue
            fields = _str_keys(lit)
            typ = fields.get("type")
            kind = typ.value if isinstance(typ, ast.Constant) and isinstance(typ.value, str) else None
            if kind not in JOB_FIELDS:
                continue
            ln = call.lineno
            for req in JOB_REQUIRED.get(kind, ()):
                if req not in keys:
                    out.append({"code": "job_missing_field", "line": ln,
                                "message": f"studio.jobs.start type '{kind}' needs '{req}' (the Jobs service answers 400 without it)"})
            tgt = fields.get("target")
            tkeys = _known_keys(fn, tgt) if tgt is not None else None
            if tkeys is not None and "base_url" not in tkeys:
                out.append({"code": "job_missing_field", "line": ln,
                            "message": "studio.jobs.start target needs 'base_url' (target: {base_url, allowed_hosts?})"})
            if kind in ("test-run", "explore"):
                if "policy" not in keys:
                    out.append({"code": "job_no_policy", "line": ln,
                                "message": ("no 'policy' in this studio.jobs.start body: the run has NO safety rails. Say what must "
                                            "never be done on the target: policy={\"forbidden_names\": [element names not to "
                                            "click], \"forbidden_requests\": [\"METHOD /path*\"]} (an explicit empty policy is "
                                            "allowed when nothing is dangerous)")})
                else:
                    pk = _known_keys(fn, fields["policy"])
                    if pk is not None and (bad := sorted(pk - JOB_POLICY_KEYS)):
                        out.append({"code": "job_policy_shape", "line": ln,
                                    "message": (f"policy field(s) {', '.join(bad)} are ignored by the Jobs service; it reads "
                                                f"{', '.join(sorted(JOB_POLICY_KEYS))}")})
            if kind == "test-run":
                ckeys = _credential_keys(fn, body, fields)
                saved: set[str] = set()
                for dn in ast.walk(fn):
                    if isinstance(dn, ast.Dict):
                        sv = _str_keys(dn).get("save")
                        if isinstance(sv, ast.Dict):
                            saved |= set(_str_keys(sv))
                        elif sv is not None:
                            saved.add("*")                      # computed save map: any variable may exist
                if ckeys is not None and "*" not in saved:
                    for name, uses in sorted(templates.items()):
                        if name in ckeys or name in saved:
                            continue
                        ttl = any(t for t, _ in uses)
                        out.append({"code": "job_credential_missing", "line": ln,
                                    "message": (f"steps use {{{{{'totp:' if ttl else ''}{name}}}}} (line {uses[0][1]}) but this "
                                                f"studio.jobs.start has no credentials key '{name}'"
                                                + (" (a TOTP step needs the base32 secret as credentials['" + name + "'] = "
                                                   "studio.secret('...'))" if ttl else "")
                                                + "; the run fails at that step")})
                for dn in ast.walk(fn):
                    if not isinstance(dn, ast.Dict):
                        continue
                    f2 = _str_keys(dn)
                    if isinstance(f2.get("expect"), ast.Dict) and ("method" in f2 or "path" in f2):
                        extra = sorted(set(_str_keys(f2["expect"])) - JOB_EXPECT_KEYS)
                        if extra:
                            out.append({"code": "job_ignored_field", "line": dn.lineno,
                                        "message": (f"API case 'expect' field(s) {', '.join(extra)} are ignored by the Jobs service "
                                                    f"(it reads {', '.join(sorted(JOB_EXPECT_KEYS))}); assert it yourself")})
                        extra = sorted(set(f2) - JOB_REQUEST_KEYS)
                        if extra:
                            out.append({"code": "job_ignored_field", "line": dn.lineno,
                                        "message": f"API case request field(s) {', '.join(extra)} are ignored by the Jobs service"})
    seen: set[tuple[str, int, str]] = set()
    uniq = []
    for p in out:
        k = (p["code"], p["line"], p["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def identity_problems(source: str) -> list[dict[str, Any]]:
    """A role or permission read from a request header (forgeable by anyone who can reach the route) and credentials read
    from the request body in a route that calls out."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict[str, Any]] = []
    for fn in _functions(tree):
        calls_out = any(isinstance(c, ast.Call) and (_chain(c.func).startswith(("studio.jobs.", "studio.nodes."))
                                                     or _chain(c.func).endswith("jobs.start"))
                        for c in ast.walk(fn))         # calls, not a docstring that names them
        for n in ast.walk(fn):
            key = None
            if isinstance(n, ast.Subscript) and not isinstance(n.ctx, ast.Load):
                continue                        # body["credentials"] = ... writes the key; it reads nothing from the request
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                base, key = _chain(n.value), n.slice.value
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "pop") \
                    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                base, key = _chain(n.func.value), n.args[0].value
            if key is None:
                continue
            holder = n.value if isinstance(n, ast.Subscript) else n.func.value
            if isinstance(holder, ast.Name) and not base.endswith("headers"):
                vals = _dict_values(holder, fn, tree)
                if vals and all(_provenance(v, fn, tree) == "secret" for v in vals):
                    continue                    # a dict the handler built from studio.secret() references, not the request
            if base.endswith("headers") and _ROLE_KEY.search(key):
                out.append({"code": "spoofable_identity", "line": n.lineno,
                            "message": (f"'{key}' is read from a request header; anyone can forge it. Do not grant rights from a "
                                        "header: use the engine-verified `req.caller` (`req.caller[\"id\"]`, `[\"roles\"]`) and check it against "
                                        "an approvers table; never a body `actor`)")})
            elif calls_out and _CRED_KEY.match(key) and not base.endswith("headers"):
                out.append({"code": "browser_credentials", "line": n.lineno,
                            "message": (f"'{key}' is read from the request and this route calls out; credentials must be project "
                                        "secrets (studio.secret('NAME')), never supplied by the browser")})
    seen: set[tuple[str, int]] = set()
    uniq = []
    for p in out:
        k = (p["code"], p["line"])
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def job_persistence_problems(source: str) -> list[dict[str, Any]]:
    """A module that starts jobs must be able to persist their results with nobody polling: a route a schedule can call."""
    if not re.search(r"\bstudio\.jobs\.start\(|\bjobs\.start\(", source):
        return []
    has = re.search(r"^@(?:studio\.|api\.)?schedule\(", source, re.M) or re.search(r"@api\.\w+\(\s*[\"'][^\"']*(sync[-_]pending|pending[-_]sync|sync[-_]all)", source) or \
        re.search(r"def \w*(sync_pending|sync_all|finish_pending)\w*\(", source)
    if has:
        return []
    return [{"code": "no_background_sync", "line": None,
             "message": ("jobs are started but nothing persists their results unless a browser polls: add a POST route "
                         "(e.g. /runs/sync-pending, function sync_pending_runs) that syncs every unfinished run idempotently, "
                         "or a @schedule function that does the same (see get_backend_template job-run-poll-sync)")}]


def _step_target_problem(node: ast.AST, where: str) -> str | None:
    if isinstance(node, (ast.JoinedStr, ast.Constant)):
        return (f"{where} must be an object, never a string: {{\"role\": \"button\", \"name\": \"Sign in\"}} | "
                "{\"label\": ...} | {\"text\": ...}")
    if not isinstance(node, ast.Dict) or None in node.keys:
        return None
    keys = _str_keys(node)
    role = keys.get("role")
    if isinstance(role, ast.Constant) and isinstance(role.value, str) and role.value not in TARGET_ROLES:
        return f"{where} has unsupported role '{role.value}' (one of {', '.join(sorted(TARGET_ROLES))})"
    if not {"role", "label", "text"} & set(keys):
        return f"{where} needs role, label or text"
    if "within" in keys:
        return _step_target_problem(keys["within"], f"{where}.within")
    return None


def job_step_problems(source: str) -> list[dict[str, Any]]:
    """UI step literals (``{"action": ..., ...}``) checked against the Jobs service's step contract (``UI_STEPS`` in the
    SDK): an unknown action, a missing field, a target given as a string or without role/label/text, a role the service
    does not take, an empty value, a bad count or waitFor state, and ``{{key}}`` outside fill.value / otp.value (sent as
    typed). A dict counts as a step when its literal action is a step action, or it has a ``target``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict[str, Any]] = []
    for d in ast.walk(tree):
        if not isinstance(d, ast.Dict):
            continue
        f = _str_keys(d)
        a = f.get("action")
        if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
            continue
        action = STEP_ALIASES.get(a.value, a.value)
        if action not in UI_STEPS and action != "done" and "target" not in f:
            continue                                  # {"action": "create"} in an audit row is not a step

        def bad(msg: str) -> None:
            out.append({"code": "job_step", "line": d.lineno, "message": f"UI step '{a.value}': {msg}"})

        if action == "done":
            bad("'done' is not a runnable step; end the steps list instead")
            continue
        if action not in UI_STEPS:
            bad(f"unknown UI step action '{a.value}'; the Jobs service runs: {', '.join(UI_STEPS)}")
            continue
        req, _opt = UI_STEPS[action]
        if None not in d.keys:                        # a **spread may supply the rest
            for k in req:
                if k not in f and not (action == "expectUrl" and k == "url" and ({"contains", "pattern"} & set(f))):
                    bad(f"needs '{k}' ({action} takes {{{', '.join(list(req) + [o + '?' for o in _opt])}}})")
        for k in ("target", "within"):
            if k in f and (msg := _step_target_problem(f[k], k)):
                bad(msg)
        for k, v in f.items():
            if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
                continue
            if v.value == "" and k in ("url", "option", "key", "file", "text", "contains", "pattern") \
                    or (v.value == "" and action == "otp" and k == "value"):
                bad(f"'{k}' must not be empty")
            if _TEMPLATE.search(v.value) and (action, k) not in TEMPLATE_FIELDS:
                bad(f"'{k}' has {v.value!r}, but {{{{key}}}} / {{{{totp:key}}}} are replaced only in fill.value and otp.value; "
                    "anywhere else they are sent as typed")
        cnt = f.get("count")
        if action == "expectCount" and isinstance(cnt, (ast.Constant, ast.UnaryOp)):
            try:
                val = ast.literal_eval(cnt)
            except ValueError:
                val = 0                               # -n: not a literal, judged at run time
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                bad(f"count must be an integer >= 0 (got {val!r})")
        st = f.get("state")
        if action == "waitFor" and isinstance(st, ast.Constant) and st.value not in ("visible", "hidden"):
            bad(f"state must be 'visible' or 'hidden' (got {st.value!r})")
    return out


def all_problems(source: str) -> list[dict[str, Any]]:
    return (job_start_problems(source) + identity_problems(source) + job_persistence_problems(source)
            + job_step_problems(source))
