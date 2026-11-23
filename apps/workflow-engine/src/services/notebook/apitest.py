"""API testing for the http cell: build the request (params, auth, form body) and check the response (sentence
assertions with the actual value, extraction into variables, a small JSON-schema check).

Pure stdlib on purpose: the notebook's http executor imports it, and Keep embeds this very source in the kept
workflow's Code nodes, so a kept test suite judges a response exactly as the notebook did.

Secrets never enter a request built here as values: an auth secret is written as the engine expression
``{{ $vars.KEY }}`` and the engine's HttpRequest node fills it in (and redacts it from what it records).
"""

from __future__ import annotations

import json as _ajson
import re as _are
from urllib.parse import quote as _aquote

API_IDENT = _are.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
ASSERT_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "contains", "not_contains", "exists", "not_exists", "matches",
              "in", "has_item")
_OP_WORD = {"eq": "equals", "ne": "is not", "lt": "is under", "le": "is at most", "gt": "is over", "ge": "is at least",
            "contains": "contains", "not_contains": "does not contain", "exists": "exists",
            "not_exists": "does not exist", "matches": "matches", "in": "is one of", "has_item": "has an item with"}


class ApiTestError(Exception):
    pass


# ── request ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def secret_expr(key):
    if not isinstance(key, str) or not API_IDENT.match(key):
        raise ApiTestError("a secret is named by its key (letters, digits, _), got %r" % (key,))
    return "{{ $vars.%s }}" % key


def _q(v):
    s = v if isinstance(v, str) else _ajson.dumps(v, default=str)
    return s if "{{" in s else _aquote(s, safe="")


def with_params(url, params):
    """``url`` plus ``params`` as a query string (values URL-encoded; an engine expression is left as written)."""
    url = url or ""
    pairs = [(k, v) for k, v in (params or {}).items() if v is not None]
    if not pairs:
        return url
    qs = "&".join("%s=%s" % (_aquote(str(k), safe=""), _q(v)) for k, v in pairs)
    return url + ("&" if "?" in url else "?") + qs


def auth_parts(spec):
    """``(headers, query)`` the auth adds: lists of ``(name, value)``; values are engine expressions for the secret.
    A legacy ``credential_slot`` is a Bearer token."""
    auth = spec.get("auth") or None
    if not auth and spec.get("credential_slot"):
        auth = {"type": "bearer", "secret": spec["credential_slot"]}
    if not auth or (auth.get("type") or "none") == "none":
        return [], []
    t = auth.get("type")
    sec = secret_expr(auth.get("secret"))
    if t == "bearer":
        return [("Authorization", "Bearer " + sec)], []
    if t == "basic":
        user = str(auth.get("username") or "")
        if "{{" in user or "}}" in user or '"' in user or "\\" in user:
            raise ApiTestError("a Basic auth username can't contain {{, }}, quotes or backslashes")
        return [("Authorization", 'Basic {{ base64("%s:" + $vars.%s) }}' % (user, auth.get("secret")))], []
    if t == "api_key":
        name = str(auth.get("name") or "X-API-Key")
        if not _are.match(r"^[A-Za-z0-9_.\-]{1,100}$", name):
            raise ApiTestError("an API key name is letters, digits, '-', '_' or '.'")
        if (auth.get("add_to") or "header") == "query":
            return [], [(name, sec)]
        return [(name, sec)], []
    raise ApiTestError("unknown auth type %r" % (t,))


def build_request(spec):
    """``{method, url, headers: [{name, value}], body}`` from a resolved http spec (references already filled in)."""
    method = str(spec.get("method") or "GET").upper()
    a_headers, a_query = auth_parts(spec)
    url = with_params(spec.get("url") or "", spec.get("params") or {})
    if a_query:
        url += ("&" if "?" in url else "?") + "&".join("%s=%s" % (k, v) for k, v in a_query)
    headers = [{"name": k, "value": str(v)} for k, v in (spec.get("headers") or {}).items()]
    names = {h["name"].lower() for h in headers}
    body = spec.get("body")
    out_body = None
    if body is not None and method in ("POST", "PUT", "PATCH"):
        if (spec.get("body_type") or "json") == "form":
            if isinstance(body, dict):
                out_body = "&".join("%s=%s" % (_aquote(str(k), safe=""), _q(v)) for k, v in body.items())
            else:
                out_body = str(body)
            if "content-type" not in names:
                headers.append({"name": "Content-Type", "value": "application/x-www-form-urlencoded"})
        else:
            out_body = body if isinstance(body, str) else _ajson.dumps(body, default=str)
    for k, v in a_headers:
        headers = [h for h in headers if h["name"].lower() != k.lower()] + [{"name": k, "value": v}]
    return {"method": method, "url": url, "headers": headers, "body": out_body}


# ── response ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def json_path(body, path):
    """``(found, value)`` for ``$.a.b[0].c`` (``$`` alone is the whole body)."""
    p = str(path or "").strip()
    if p.startswith("$"):
        p = p[1:]
    cur = body
    for seg in _are.findall(r"[^.\[\]]+|\[-?\d+\]", p):
        if seg.startswith("["):
            i = int(seg[1:-1])
            if not isinstance(cur, list) or not -len(cur) <= i < len(cur):
                return False, None
            cur = cur[i]
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.lstrip("-").isdigit() and -len(cur) <= int(seg) < len(cur):
            cur = cur[int(seg)]
        elif isinstance(cur, list) and seg in ("length", "count"):
            cur = len(cur)
        else:
            return False, None
    return True, cur


def header_value(headers, name):
    want = str(name or "").lower()
    for k, v in (headers or {}).items():
        if str(k).lower() == want:
            return True, v
    return False, None


_TYPES = {"string": (str,), "number": (int, float), "integer": (int,), "boolean": (bool,), "object": (dict,),
          "array": (list,), "null": (type(None),)}


def _is_type(v, t):
    if t in ("number", "integer") and isinstance(v, bool):
        return False
    if t == "integer" and isinstance(v, float):
        return v.is_integer()
    return isinstance(v, _TYPES.get(t, (object,)))


def schema_problems(value, schema, at="$", out=None, limit=20):
    """A JSON-schema subset: type, required, properties, additionalProperties: false, items, enum, minimum, maximum,
    minLength, maxLength, minItems, maxItems. Returns the problems (``$.items[2].id: expected integer, got string``)."""
    out = [] if out is None else out
    if not isinstance(schema, dict) or len(out) >= limit:
        return out
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in ts):
            got = "null" if value is None else ("boolean" if isinstance(value, bool) else
                                                {str: "string", int: "integer", float: "number", dict: "object",
                                                 list: "array"}.get(type(value), type(value).__name__))
            out.append("%s: expected %s, got %s" % (at, " or ".join(ts), got))
            return out
    if "enum" in schema and value not in (schema.get("enum") or []):
        out.append("%s: %r is not one of %r" % (at, value, schema.get("enum")))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema["minimum"]:
            out.append("%s: %r is under the minimum %r" % (at, value, schema["minimum"]))
        if schema.get("maximum") is not None and value > schema["maximum"]:
            out.append("%s: %r is over the maximum %r" % (at, value, schema["maximum"]))
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < schema["minLength"]:
            out.append("%s: shorter than %s characters" % (at, schema["minLength"]))
        if schema.get("maxLength") is not None and len(value) > schema["maxLength"]:
            out.append("%s: longer than %s characters" % (at, schema["maxLength"]))
    if isinstance(value, dict):
        for k in schema.get("required") or []:
            if k not in value:
                out.append("%s: %s is required" % (at, k))
        props = schema.get("properties") or {}
        for k, sub in props.items():
            if k in value:
                schema_problems(value[k], sub, "%s.%s" % (at, k), out, limit)
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    out.append("%s: %s is not allowed" % (at, k))
    if isinstance(value, list):
        if schema.get("minItems") is not None and len(value) < schema["minItems"]:
            out.append("%s: fewer than %s items" % (at, schema["minItems"]))
        if schema.get("maxItems") is not None and len(value) > schema["maxItems"]:
            out.append("%s: more than %s items" % (at, schema["maxItems"]))
        if isinstance(schema.get("items"), dict):
            for i, x in enumerate(value[:200]):
                schema_problems(x, schema["items"], "%s[%d]" % (at, i), out, limit)
    return out[:limit]


def _short(v, n=120):
    s = v if isinstance(v, str) else _ajson.dumps(v, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def assertion_label(a):
    """The sentence a user reads: "Status is 200", "Responds in under 500 ms", "$.id exists"."""
    kind, op, val = a.get("kind"), a.get("op") or "eq", a.get("value")
    if kind == "status":
        return ("Status is one of %s" % ", ".join(str(x) for x in val)) if op == "in" else \
            ("Status is %s" % val if op == "eq" else "Status %s %s" % (_OP_WORD.get(op, op), val))
    if kind == "time":
        return "Responds in under %s ms" % val if op in ("lt", "le") else "Response time %s %s ms" % (
            _OP_WORD.get(op, op), val)
    if kind == "header":
        n = a.get("name")
        if op == "exists":
            return "Header %s is present" % n
        if op == "not_exists":
            return "Header %s is absent" % n
        return "Header %s %s %s" % (n, _OP_WORD.get(op, op), _short(val, 60))
    if kind == "schema":
        return "Body matches the schema"
    path = a.get("path") or "$"
    if op in ("exists", "not_exists"):
        return "%s %s" % (path, _OP_WORD[op])
    return "%s %s %s" % (path, _OP_WORD.get(op, op), _short(val, 60))


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _compare(op, got, want):
    if op == "eq":
        return got == want or (_num(got) is not None and _num(got) == _num(want) and not isinstance(got, str)
                               and not isinstance(want, str))
    if op == "ne":
        return got != want
    if op in ("lt", "le", "gt", "ge"):
        a, b = _num(got), _num(want)
        if a is None or b is None:
            return False
        return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    if op in ("contains", "not_contains"):
        if isinstance(got, list):
            hit = want in got
        elif isinstance(got, dict):
            hit = want in got
        else:
            hit = got is not None and str(want) in str(got)
        return hit if op == "contains" else not hit
    if op == "matches":
        try:
            return got is not None and _are.search(str(want), str(got)) is not None
        except _are.error:
            return False
    if op == "in":
        return isinstance(want, list) and got in want
    if op == "has_item":
        # a list with an item whose fields equal every field of ``want`` ({"id": 5}); a plain ``want`` is an item
        if not isinstance(got, list):
            return False
        if isinstance(want, dict):
            return any(isinstance(x, dict) and all(k in x and _compare("eq", x[k], v) for k, v in want.items())
                       for x in got)
        return any(_compare("eq", x, want) for x in got)
    return False


def check(a, status, headers, body, ms):
    """One assertion -> ``{label, kind, passed, actual, expected, message?}``."""
    kind, op, want = a.get("kind"), a.get("op") or "eq", a.get("value")
    res = {"label": assertion_label(a), "kind": kind, "expected": want}
    if kind == "status":
        got = status
        ok = _compare(op, got, want)
    elif kind == "time":
        got = ms
        if ms is None:  # a kept workflow's Code node does not see the request's timing
            res.update(passed=True, actual=None, message="not measured here")
            return res
        ok = _compare(op if op in ("lt", "le", "gt", "ge") else "lt", got, want)
    elif kind == "header":
        found, got = header_value(headers, a.get("name"))
        ok = found if op == "exists" else (not found) if op == "not_exists" else (found and _compare(op, got, want))
        if not found and op not in ("exists", "not_exists"):
            res["message"] = "the response has no %s header" % a.get("name")
    elif kind == "schema":
        probs = schema_problems(body, a.get("json_schema") or {})
        got = "matches" if not probs else probs
        ok = not probs
        res["expected"] = "the schema"
        if probs:
            res["message"] = "; ".join(probs[:5])
    else:  # json_path
        found, got = json_path(body, a.get("path") or "$")
        ok = found if op == "exists" else (not found) if op == "not_exists" else (found and _compare(op, got, want))
        if not found and op not in ("exists", "not_exists"):
            res["message"] = "%s is not in the response" % (a.get("path") or "$")
    res["passed"] = bool(ok)
    res["actual"] = got if not isinstance(got, (dict, list)) or len(_ajson.dumps(got, default=str)) <= 2000 \
        else _short(got, 2000)
    return res


def check_all(spec, status, headers, body, ms):
    """``(results, problems)``. ``assertions`` and the legacy ``expect`` both count; with neither, a status of 400 or
    more is the one failure (``the server answered 404``)."""
    results = [check(a, status, headers, body, ms) for a in (spec.get("assertions") or [])]
    exp = spec.get("expect") or {}
    if exp.get("status") is not None:
        results.append(check({"kind": "status", "op": "eq", "value": exp["status"]}, status, headers, body, ms))
    for p, want in (exp.get("json_path_equals") or {}).items():
        results.append(check({"kind": "json_path", "path": p, "op": "eq", "value": want}, status, headers, body, ms))
    problems = []
    for r in results:
        if not r["passed"]:
            problems.append("%s: %s" % (r["label"], r.get("message") or "got %s" % _short(r["actual"], 200)))
    if not results and isinstance(status, int) and status >= 400:
        problems.append("the server answered %s" % status)
    return results, problems


def extract(rules, status, headers, body):
    """``(vars, problems)`` for ``extract: [{name, source: json_path|header|status, path?}]``."""
    got, problems = {}, []
    for x in rules or []:
        name, src = x.get("name"), x.get("source") or "json_path"
        if src == "status":
            got[name] = status
            continue
        if src == "header":
            found, v = header_value(headers, x.get("path"))
        else:
            found, v = json_path(body, x.get("path") or "$")
        if found:
            got[name] = v
        else:
            problems.append("could not save %s: %s is not in the response" % (name, x.get("path")))
    return got, problems
