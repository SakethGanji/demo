"""n8n workflow import (Stage 2 contract section 4).

Pure function ``import_n8n(n8n_json, name=None) -> (workflow_dict, report)``. It does no I/O
and never saves. The workflow dict is ``WorkflowCreateRequest``-shaped; the report lists what
was mapped, what could not be, which expressions were kept verbatim, and which Code nodes were
imported disabled.

Deviations from the literal contract text (all deliberate, all reported to the lead):
  * ``.length`` becomes ``length(x)`` (our engine has no ``len``; ``length`` is its function).
  * ``.toLowerCase()`` and friends become engine *functions* (``lower(x)``), not ``.lower()``,
    because the expression engine rewrites ``$json.a.b.lower`` into a ``get()`` chain and would
    break a method call on a ``$json``/``$node`` path.
  * n8n Aggregate (collect items into a list) maps to ``ItemLists`` (``summarize`` /
    ``aggregate``); our ``Aggregate`` node is a pandas-style group-by and not equivalent.
  * Extras beyond the contract: n8n splitOut/limit/sort/removeDuplicates map to ItemLists, and
    ``report["notes"]`` carries human-readable caveats (credentials dropped, defaults, etc.).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Expression translation
# ---------------------------------------------------------------------------

_SEG_RE = re.compile(r"\{\{(.*?)\}\}", re.S)

_ALLOWED_DOLLAR = {"json", "input", "env", "vars", "execution", "itemIndex"}

_STR_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')

_IDX = r"(?:\[\s*\w+\s*\])"
_CHAIN = r"(?:\$[A-Za-z_]\w*|[A-Za-z_]\w*)(?:\s*\.\s*[A-Za-z_]\w*|" + _IDX + r")*"
_CALLRES = r"(?:length|lower|upper|trim|str)\((?:[^()]|\([^()]*\))*\)"
_OPERAND = rf"(?<![\w$.\]\)])(?:{_CHAIN}|{_CALLRES})"
_ARG = rf"(?:{_CHAIN}|\d+)"

_METHOD_FUNCS = {
    "toLowerCase": "lower",
    "toUpperCase": "upper",
    "trim": "trim",
    "toString": "str",
}
_METHOD_ARG_FUNCS = {
    "startsWith": "startswith",
    "endsWith": "endswith",
    "includes": "includes",
    "split": "split",
    "join": "join",
}
_GLOBAL_FUNCS = [
    (r"JSON\.stringify\(", "json_stringify("),
    (r"JSON\.parse\(", "json_parse("),
    (r"Math\.(round|floor|ceil|abs|min|max)\(", r"\1("),
    (r"parseInt\(", "int("),
    (r"parseFloat\(", "float("),
    (r"Number\(", "float("),
    (r"String\(", "str("),
    (r"Boolean\(", "bool("),
]

_LEFTOVER = [
    re.compile(p)
    for p in (
        r"=>",
        r"\?",
        r";",
        r"`",
        r"\bfunction\b",
        r"\bnew\b",
        r"\b(?:let|const|var|return|typeof|instanceof|await)\b",
        r"\.\s*[A-Za-z_]\w*\s*\(",
        r"\.\s*length\b",
        r"\$(?![A-Za-z_])",
        r"\b(?:DateTime|Object|Array|JSON|Math|Date|Buffer|process)\b",
        r"\{\s*[\w\"']+\s*:",
        r"\.\.\.",
    )
]


def _node_ref_normalise(s: str, name_map: dict[str, str]) -> str:
    def nm(n: str) -> str:
        return name_map.get(n, n)

    # $('X').item.json / .first().json / .last().json  ->  $node["X"].json
    s = re.sub(
        r"""\$\(\s*(["'])((?:(?!\1).)+)\1\s*\)\s*\.\s*(?:item|first\(\s*\)|last\(\s*\))\s*\.\s*json\b""",
        lambda m: f'$node["{nm(m.group(2))}"].json',
        s,
    )
    # $node["X"].json / $node['X'].data  (normalise quotes and map renamed nodes)
    s = re.sub(
        r"""\$node\[\s*(["'])((?:(?!\1).)+)\1\s*\]\s*\.\s*(?:json|data)\b""",
        lambda m: f'$node["{nm(m.group(2))}"].json',
        s,
    )
    # $node.X.json
    s = re.sub(r"\$node\.([A-Za-z_]\w*)\.(?:json|data)\b", lambda m: f'$node["{nm(m.group(1))}"].json', s)
    # $input.item.json / $input.first().json -> $json
    s = re.sub(r"\$input\s*\.\s*(?:item|first\(\s*\)|last\(\s*\))\s*\.\s*json\b", "$json", s)
    return s


def translate_expression(inner: str, name_map: dict[str, str] | None = None) -> str | None:
    """Translate one JS expression (the inside of ``{{ }}``). ``None`` means untranslatable."""
    name_map = name_map or {}
    s = inner.strip()
    if not s or "`" in s:
        return None
    s = _node_ref_normalise(s, name_map)

    # Mask node refs and string literals so regexes can't touch their insides.
    nodes: list[str] = []

    def mask_node(m: re.Match[str]) -> str:
        nodes.append(m.group(0))
        return f"__N{len(nodes) - 1}__"

    s = re.sub(r'\$node\["[^"]*"\]', mask_node, s)

    strs: list[str] = []

    def mask_str(m: re.Match[str]) -> str:
        strs.append(m.group(0))
        return f"__S{len(strs) - 1}__"

    s = _STR_RE.sub(mask_str, s)

    # Method and global rewrites to engine functions.
    for pat, rep in _GLOBAL_FUNCS:
        s = re.sub(rf"(?<![\w$.]){pat}", rep, s)
    for _ in range(3):
        before = s
        s = re.sub(rf"({_OPERAND})\s*\.\s*length\b(?!\s*\()", r"length(\1)", s)
        for js, fn in _METHOD_FUNCS.items():
            s = re.sub(rf"({_OPERAND})\s*\.\s*{js}\(\s*\)", rf"{fn}(\1)", s)
        for js, fn in _METHOD_ARG_FUNCS.items():
            s = re.sub(rf"({_OPERAND})\s*\.\s*{js}\(\s*({_ARG})?\s*\)", lambda m, fn=fn: f"{fn}({m.group(1)}" + (f", {m.group(2)}" if m.group(2) else "") + ")", s)
        if s == before:
            break

    # Operators.
    s = s.replace("?.", ".")
    s = s.replace("!==", "!=").replace("===", "==")
    s = s.replace("&&", " and ").replace("||", " or ")
    s = re.sub(r"!(?!=)", " not ", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip()

    for rx in _LEFTOVER:
        if rx.search(s):
            return None
    for m in re.finditer(r"\$(\w+)", s):
        if m.group(1) not in _ALLOWED_DOLLAR:
            return None

    s = re.sub(r"__S(\d+)__", lambda m: strs[int(m.group(1))], s)
    s = re.sub(r"__N(\d+)__", lambda m: nodes[int(m.group(1))], s)
    return s


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

_TRIGGERS = {"Start", "Webhook", "Cron", "ExecuteWorkflowTrigger"}

_NO_REMAP = object()


class _Unmapped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _short(t: str) -> str:
    return (t or "").split(".")[-1]


def _clean_name(raw: Any, fallback: str) -> str:
    s = str(raw if raw is not None else "")
    s = re.sub(r"[\"'\\\r\n\t]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return (s or fallback)[:200]


def _ver(node: dict) -> float:
    try:
        return float(node.get("typeVersion", 1))
    except (TypeError, ValueError):
        return 1.0


def _first(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, ""):
            return v
    return None


def _rl(v: Any) -> Any:
    """Unwrap an n8n resource locator ``{__rl, mode, value}``."""
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


_OP_ALIASES = {
    "equal": "equals", "notEqual": "notEquals", "larger": "gt", "largerEqual": "gte",
    "smaller": "lt", "smallerEqual": "lte", "isEmpty": "empty", "isNotEmpty": "notEmpty",
    "afterOrEquals": "gte", "beforeOrEquals": "lte", "after": "gt", "before": "lt",
}

_PY_OPS: dict[str, str] = {
    "equals": "{a} == {b}", "notEquals": "{a} != {b}", "gt": "{a} > {b}", "gte": "{a} >= {b}",
    "lt": "{a} < {b}", "lte": "{a} <= {b}",
    "exists": "{a} is not None", "notExists": "{a} is None",
    "empty": "is_empty({a})", "notEmpty": "not is_empty({a})",
    "true": "{a} == True", "false": "{a} == False",
    "startsWith": "startswith({a}, {b})", "notStartsWith": "not startswith({a}, {b})",
    "endsWith": "endswith({a}, {b})", "notEndsWith": "not endswith({a}, {b})",
    "lengthEquals": "length({a}) == {b}", "lengthNotEquals": "length({a}) != {b}",
    "lengthGt": "length({a}) > {b}", "lengthGte": "length({a}) >= {b}",
    "lengthLt": "length({a}) < {b}", "lengthLte": "length({a}) <= {b}",
}

_NATIVE_STR = {
    "equals": "equals", "notEquals": "notEquals", "contains": "contains", "notContains": "notContains",
    "startsWith": "startsWith", "endsWith": "endsWith", "empty": "isEmpty", "notEmpty": "isNotEmpty",
    "regex": "regex",
}
_NATIVE_NUM = {"gt": "gt", "gte": "gte", "lt": "lt", "lte": "lte"}


class _Importer:
    def __init__(self, n8n: dict[str, Any]):
        self.n8n = n8n
        self.name_map: dict[str, str] = {}
        self.mapped: list[dict] = []
        self.unmapped: list[dict] = []
        self.untranslated: list[dict] = []
        self.disabled_code: list[str] = []
        self.notes: list[dict] = []
        self.cur = ""  # current node's n8n name (for reporting)

    # -- reporting ------------------------------------------------------------------------
    def note(self, msg: str) -> None:
        self.notes.append({"node": self.name_map.get(self.cur, self.cur), "message": msg})

    def _untranslated(self, param: str, expr: str) -> None:
        self.untranslated.append({"node": self.name_map.get(self.cur, self.cur), "param": param, "expression": expr})

    # -- expression / value translation ----------------------------------------------------
    def tpl(self, body: str, param: str) -> str:
        """Translate a template string (the part after the leading ``=``)."""
        def rep(m: re.Match[str]) -> str:
            t = translate_expression(m.group(1), self.name_map)
            if t is None:
                self._untranslated(param, m.group(0))
                return m.group(0)
            return "{{ " + t + " }}"
        return _SEG_RE.sub(rep, body)

    def lit(self, s: str) -> str:
        """An n8n literal (no leading ``=``) is plain text, but our engine evaluates any ``{{ }}`` in a string.

        The expression engine has no escape for a literal ``{{`` (and adding one would touch three resolve paths),
        so the braces are separated (``{{`` -> ``{ {``) and a note is recorded.
        """
        if "{{" not in s:
            return s
        self.note("literal text containing '{{' was changed to '{ {' so it is not evaluated as an expression")
        return s.replace("{{", "{ {")

    def val(self, v: Any, param: str) -> Any:
        """Translate any n8n parameter value; ``=``-prefixed strings are expressions."""
        if isinstance(v, str):
            return self.tpl(v[1:], param) if v.startswith("=") else self.lit(v)
        if isinstance(v, list):
            return [self.val(x, f"{param}[{i}]") for i, x in enumerate(v)]
        if isinstance(v, dict):
            return {k: self.val(x, f"{param}.{k}") for k, x in v.items()}
        return v

    def operand(self, v: Any, typ: str, param: str) -> str | None:
        """A Python-expression source for a condition operand (None if untranslatable)."""
        if isinstance(v, bool):
            return "True" if v else "False"
        if isinstance(v, (int, float)):
            return repr(v)
        if v is None:
            return "None"
        s = str(v)
        if not s.startswith("="):
            if typ == "number":
                try:
                    return repr(float(s)) if "." in s else repr(int(s))
                except ValueError:
                    pass
            if typ == "boolean" and s.lower() in ("true", "false"):
                return "True" if s.lower() == "true" else "False"
            return json.dumps(self.lit(s), ensure_ascii=False)
        body = s[1:]
        parts: list[str] = []
        pos = 0
        for m in _SEG_RE.finditer(body):
            if m.start() > pos:
                parts.append(json.dumps(body[pos:m.start()], ensure_ascii=False))
            t = translate_expression(m.group(1), self.name_map)
            if t is None:
                self._untranslated(param, m.group(0))
                return None
            parts.append(f"({t})" if (m.start() > 0 or m.end() < len(body)) else t)
            pos = m.end()
        if pos < len(body):
            parts.append(json.dumps(body[pos:], ensure_ascii=False))
        if not parts:
            return '""'
        if len(parts) == 1:
            return parts[0]
        return " + ".join(p if p.startswith('"') else f"str({p})" for p in parts)

    def cond_expr(self, typ: str, op: str, left: Any, right: Any, param: str) -> str | None:
        op = _OP_ALIASES.get(op, op)
        a = self.operand(left, typ, param)
        b = self.operand(right, typ, param) if right is not None else "None"
        if a is None or b is None:
            return None
        if op in ("contains", "notContains"):
            tpl = "{b} in {a}" if typ == "array" else "{b} in str({a})"
            if op == "notContains":
                tpl = tpl.replace(" in ", " not in ", 1)
        elif op in ("regex", "notRegex"):
            self._untranslated(param, f"regex condition ({op}) on {left!r}")
            return None
        elif op in _PY_OPS:
            tpl = _PY_OPS[op]
        else:
            self._untranslated(param, f"unsupported operator {typ}.{op} on {left!r}")
            return None
        return "(" + tpl.format(a=a, b=b) + ")"

    def combine(self, exprs: list[str | None], combinator: str) -> str:
        use = [e if e is not None else "True" for e in exprs]
        if not use:
            return "{{ True }}"
        joiner = " or " if str(combinator).lower() in ("or", "any") else " and "
        return "{{ " + joiner.join(use) + " }}"

    # -- condition extraction --------------------------------------------------------------
    def conditions_v2(self, cd: dict | None) -> tuple[str, list[tuple[str, str, Any, Any]]]:
        cd = cd or {}
        out = []
        for c in cd.get("conditions") or []:
            op = c.get("operator") or {}
            out.append((op.get("type", "string"), op.get("operation", "equals"), c.get("leftValue"), c.get("rightValue")))
        return cd.get("combinator", "and"), out

    def conditions_v1(self, cd: dict | None, combine_op: str) -> tuple[str, list[tuple[str, str, Any, Any]]]:
        out = []
        for typ in ("string", "number", "boolean", "dateTime"):
            for c in (cd or {}).get(typ) or []:
                out.append((typ, c.get("operation", "equal"), c.get("value1"), c.get("value2")))
        return ("or" if combine_op == "any" else "and"), out

    def conds_of(self, p: dict, v: float) -> tuple[str, list[tuple[str, str, Any, Any]]]:
        conds = p.get("conditions")
        if isinstance(conds, dict) and isinstance(conds.get("conditions"), list):
            return self.conditions_v2(conds)
        return self.conditions_v1(conds if isinstance(conds, dict) else None, p.get("combineOperation", "all"))

    def condition_string(self, p: dict, v: float, param: str = "condition") -> str:
        comb, conds = self.conds_of(p, v)
        exprs = [self.cond_expr(t, o, l, r, f"{param}[{i}]") for i, (t, o, l, r) in enumerate(conds)]
        return self.combine(exprs, comb)

    def native_rule(self, typ: str, op: str, left: Any, right: Any, param: str) -> dict | None:
        """Switch rule in our native {field, operation, value} form when it translates cleanly."""
        op = _OP_ALIASES.get(op, op)
        if not (isinstance(left, str) and left.startswith("=") and len(_SEG_RE.findall(left[1:])) == 1
                and _SEG_RE.sub("", left[1:]).strip() == ""):
            return None
        native = None
        if typ == "string" and op in _NATIVE_STR:
            native = _NATIVE_STR[op]
        elif typ == "number" and op in _NATIVE_NUM:
            native = _NATIVE_NUM[op]
        elif typ == "boolean" and op in ("true", "false"):
            native = "isTrue" if op == "true" else "isFalse"
        if native is None:
            return None
        rule = {"field": self.val(left, f"{param}.field"), "operation": native}
        if native not in ("isEmpty", "isNotEmpty", "isTrue", "isFalse"):
            rv = self.val(right, f"{param}.value")
            rule["value"] = rv if isinstance(rv, str) else ("" if rv is None else str(rv))
        return rule

    def switch_rule(self, comb: str, conds: list[tuple[str, str, Any, Any]], idx: int, param: str) -> dict:
        if len(conds) == 1:
            nr = self.native_rule(*conds[0], param=param)
            if nr:
                return {"output": idx, **nr}
        exprs = [self.cond_expr(t, o, l, r, f"{param}[{i}]") for i, (t, o, l, r) in enumerate(conds)]
        return {"output": idx, "field": self.combine(exprs, comb), "operation": "isTrue"}

    # -- node handlers ---------------------------------------------------------------------
    def h_start(self, n: dict, p: dict) -> tuple[str, dict]:
        return "Start", {}

    def h_webhook(self, n: dict, p: dict) -> tuple[str, dict]:
        method = p.get("httpMethod", "GET")
        if isinstance(method, list):
            method = method[0] if method else "GET"
        method = str(method).upper()
        if method not in ("POST", "GET", "PUT", "PATCH", "DELETE"):
            self.note(f"webhook method {method} is not supported; using POST")
            method = "POST"
        mode = p.get("responseMode", "onReceived")
        resp = "lastNode" if mode in ("lastNode", "responseNode") else "onReceived"
        return "Webhook", {"method": method, "path": str(self.val(p.get("path", ""), "path")), "responseMode": resp}

    def h_schedule(self, n: dict, p: dict) -> tuple[str, dict]:
        rules = (p.get("rule") or {}).get("interval") or [{}]
        r = rules[0] if rules else {}
        if len(rules) > 1:
            self.note("multiple schedule rules; only the first was imported")
        f = r.get("field", "days")
        if f == "cronExpression":
            return "Cron", {"mode": "cron", "cronExpression": str(r.get("expression", "0 * * * *"))}
        mult = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
        if f in mult:
            count = r.get(f"{f}Interval", 1) or 1
            return "Cron", {"mode": "interval", "interval": int(count) * mult[f]}
        if f == "months":
            dom = r.get("triggerAtDayOfMonth", 1)
            return "Cron", {"mode": "cron", "cronExpression": f"{r.get('triggerAtMinute', 0)} {r.get('triggerAtHour', 0)} {dom} */{r.get('monthsInterval', 1) or 1} *"}
        raise _Unmapped(f"unsupported schedule interval field {f!r}")

    def h_cron(self, n: dict, p: dict) -> tuple[str, dict]:
        items = (p.get("triggerTimes") or {}).get("item") or [{"mode": "everyDay"}]
        if len(items) > 1:
            self.note("multiple cron trigger times; only the first was imported")
        it = items[0]
        mode = it.get("mode", "everyDay")
        minute, hour = it.get("minute", 0), it.get("hour", 0)
        if mode == "everyMinute":
            return "Cron", {"mode": "cron", "cronExpression": "* * * * *"}
        if mode == "everyHour":
            return "Cron", {"mode": "cron", "cronExpression": f"{minute} * * * *"}
        if mode == "everyDay":
            return "Cron", {"mode": "cron", "cronExpression": f"{minute} {hour} * * *"}
        if mode == "everyWeek":
            return "Cron", {"mode": "cron", "cronExpression": f"{minute} {hour} * * {it.get('weekday', 1)}"}
        if mode == "everyMonth":
            return "Cron", {"mode": "cron", "cronExpression": f"{minute} {hour} {it.get('dayOfMonth', 1)} * *"}
        if mode == "everyX":
            mult = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
            return "Cron", {"mode": "interval", "interval": int(it.get("value", 1)) * mult.get(it.get("unit", "minutes"), 60)}
        if mode == "custom":
            return "Cron", {"mode": "cron", "cronExpression": str(it.get("cronExpression", "0 * * * *"))}
        raise _Unmapped(f"unsupported cron mode {mode!r}")

    def h_http(self, n: dict, p: dict) -> tuple[str, dict]:
        v = _ver(n)
        method = str(p.get("method") or p.get("requestMethod") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            self.note(f"HTTP method {method} is not supported; using GET")
            method = "GET"
        url = self.val(p.get("url", ""), "url")

        def kv(entries: Any, key: str) -> list[dict]:
            out = []
            for e in entries or []:
                if isinstance(e, dict) and e.get("name"):
                    out.append({"name": self.val(e["name"], f"{key}.name"), "value": e.get("value", "")})
            return out

        headers: Any = []
        query: list[dict] = []
        body: Any = ""
        if v < 3:
            if p.get("jsonParameters"):
                hj = p.get("headerParametersJson")
                if hj:
                    headers = self._json_headers(hj)
                qj = p.get("queryParametersJson")
                if qj:
                    self.note("queryParametersJson was not imported (JSON query object)")
                bj = p.get("bodyParametersJson")
                body = self.val(bj, "body") if bj else ""
            else:
                headers = kv((p.get("headerParametersUi") or {}).get("parameter"), "headers")
                query = kv((p.get("queryParametersUi") or {}).get("parameter"), "query")
                bp = kv((p.get("bodyParametersUi") or {}).get("parameter"), "body")
                if bp:
                    body = json.dumps({e["name"]: self.val(e["value"], f"body.{e['name']}") for e in bp}, ensure_ascii=False)
        else:
            if p.get("sendHeaders"):
                if p.get("specifyHeaders") == "json":
                    headers = self._json_headers(p.get("jsonHeaders"))
                else:
                    headers = kv((p.get("headerParameters") or {}).get("parameters"), "headers")
            if p.get("sendQuery"):
                if p.get("specifyQuery") == "json":
                    self.note("JSON-specified query parameters were not imported")
                else:
                    query = kv((p.get("queryParameters") or {}).get("parameters"), "query")
            if p.get("sendBody"):
                ctype = p.get("contentType", "json")
                if ctype == "raw":
                    body = self.val(p.get("body", ""), "body")
                    rct = p.get("rawContentType")
                    if rct:
                        headers = (headers if isinstance(headers, list) else []) + [{"name": "Content-Type", "value": rct}]
                elif ctype == "json" and p.get("specifyBody", "keypair") == "json":
                    jb = p.get("jsonBody", "")
                    body = self.val(jb, "jsonBody") if isinstance(jb, str) else json.dumps(jb, ensure_ascii=False)
                elif ctype in ("json", "form-urlencoded", "multipart-form-data"):
                    bp = kv((p.get("bodyParameters") or {}).get("parameters"), "body")
                    body = json.dumps({e["name"]: self.val(e["value"], f"bodyParameters.{e['name']}") for e in bp}, ensure_ascii=False) if bp else ""
                    if ctype != "json":
                        self.note(f"body contentType {ctype} is sent as JSON by our HttpRequest node")
                else:
                    self.note(f"body contentType {ctype} is not supported; body dropped")
        if query:
            qs = "&".join(f"{e['name']}={self.val(e['value'], 'query.' + str(e['name']))}" for e in query)
            sep = "&" if isinstance(url, str) and "?" in url else "?"
            url = f"{url}{sep}{qs}"
        if isinstance(headers, list):
            headers = [{"name": h["name"], "value": self.val(h.get("value", ""), f"headers.{h['name']}")} for h in headers]
        fmt = (((p.get("options") or {}).get("response") or {}).get("response") or {}).get("responseFormat")
        rtype = {"text": "text", "file": "binary"}.get(fmt, "json")
        if p.get("authentication") not in (None, "none"):
            self.note("authentication/credentials are not imported; set a secret or header manually")
        return "HttpRequest", {"method": method, "url": url, "headers": headers, "body": body, "responseType": rtype}

    def _json_headers(self, raw: Any) -> Any:
        if isinstance(raw, dict):
            return {self.val(k, "headers.name"): self.val(v, f"headers.{k}") for k, v in raw.items()}
        if isinstance(raw, str) and not raw.startswith("="):
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    return {self.lit(k): self.val(v, f"headers.{k}") for k, v in d.items()}
            except ValueError:
                pass
        if raw:
            self._untranslated("headers", str(raw))
        return []

    def h_set(self, n: dict, p: dict) -> tuple[str, dict]:
        v = _ver(n)
        fields: list[dict] = []
        out: dict[str, Any] = {}
        if "values" in p or v < 3:
            vals = p.get("values") or {}
            for typ in ("string", "number", "boolean"):
                for e in vals.get(typ) or []:
                    if e.get("name"):
                        fields.append({"name": e["name"], "value": self.val(e.get("value"), f"values.{typ}.{e['name']}")})
            out["keepOnlySet"] = bool(p.get("keepOnlySet", False))
        else:
            if p.get("mode") == "raw":
                raw = p.get("jsonOutput", "{}")
                out.update({"mode": "json", "jsonData": self.val(raw, "jsonOutput") if isinstance(raw, str) else json.dumps(raw)})
                out["keepOnlySet"] = not bool(p.get("includeOtherFields", False))
                return "Set", out
            for e in (p.get("assignments") or {}).get("assignments") or []:
                if e.get("name"):
                    fields.append({"name": e["name"], "value": self.val(e.get("value"), f"assignments.{e['name']}")})
            out["keepOnlySet"] = not bool(p.get("includeOtherFields", False))
        out.update({"mode": "manual", "fields": fields})
        return "Set", out

    def h_noop(self, n: dict, p: dict) -> tuple[str, dict]:
        return "Set", {"mode": "manual", "fields": [], "keepOnlySet": False}

    def h_if(self, n: dict, p: dict) -> tuple[str, dict]:
        return "If", {"condition": self.condition_string(p, _ver(n))}

    def h_filter(self, n: dict, p: dict) -> tuple[str, dict]:
        return "Filter", {"mode": "expression", "condition": self.condition_string(p, _ver(n))}

    def h_switch(self, n: dict, p: dict) -> tuple[str, dict]:
        v = _ver(n)
        mode = p.get("mode", "rules")
        if mode == "expression":
            n_out = int(p.get("numberOutputs", 4) or 4)
            return "Switch", {"mode": "expression", "numberOfOutputs": n_out, "outputIndex": self.val(p.get("output", 0), "output")}
        rules: list[dict] = []
        if v >= 3 and isinstance((p.get("rules") or {}).get("values"), list):
            for i, r in enumerate(p["rules"]["values"]):
                comb, conds = self.conditions_v2(r.get("conditions"))
                rules.append(self.switch_rule(comb, conds, i, f"rules[{i}]"))
            n_out = max(len(rules), 1)
        else:
            dtype = p.get("dataType", "number")
            left = p.get("value1")
            raw = (p.get("rules") or {}).get("rules") or []
            for i, r in enumerate(raw):
                idx = int(r.get("output", i) or 0)
                rules.append(self.switch_rule("and", [(dtype, r.get("operation", "equal"), left, r.get("value2"))], idx, f"rules[{i}]"))
            n_out = max([r["output"] for r in rules] + [0]) + 1
        return "Switch", {"mode": "rules", "numberOfOutputs": min(n_out, 15), "rules": rules}

    def h_merge(self, n: dict, p: dict) -> tuple[str, dict]:
        v = _ver(n)
        mode = p.get("mode", "append")
        out: dict[str, Any] = {"mode": "append"}
        if mode == "append":
            pass
        elif mode == "wait":
            out["mode"] = "waitForAll"
        elif mode in ("mergeByIndex", "combine") and p.get("combineBy", "combineByPosition") == "combineByPosition":
            out["mode"] = "combinePairs"
        elif mode in ("mergeByKey", "keepKeyMatches", "combine") and (mode != "combine" or p.get("combineBy") == "combineByFields"):
            out["mode"] = "keepMatches"
            f1 = _first(p.get("propertyName1"), (p.get("fieldsToMatchString") or "").split(",")[0].strip())
            vals = (p.get("fieldsToMatch") or {}).get("values") or []
            if vals:
                f1 = _first(vals[0].get("field1"), f1)
            if f1:
                out["matchField"] = f1
        else:
            self.note(f"merge mode {mode!r} is not supported; imported as append")
        return "Merge", out

    def h_loop(self, n: dict, p: dict) -> tuple[str, dict]:
        v = _ver(n)
        bs = p.get("batchSize", 1 if v >= 3 else 10)
        bs = self.val(bs, "batchSize")
        return "Loop", {"batchSize": bs if isinstance(bs, (int, str)) else 1, "maxIterations": 0}

    def h_wait(self, n: dict, p: dict) -> tuple[str, dict]:
        resume = p.get("resume", "timeInterval")
        if resume != "timeInterval":
            raise _Unmapped(f"Wait resume mode {resume!r} is not supported (only a fixed time interval)")
        unit = p.get("unit", "hours")
        amount = p.get("amount", 1)
        if "unit" not in p:
            self.note("Wait has no explicit unit; n8n's default (hours) was used")
        if unit == "days":
            unit, amount = "hours", (amount * 24 if isinstance(amount, (int, float)) else amount)
        if unit not in ("seconds", "minutes", "hours"):
            unit = "hours"
        return "Wait", {"unit": unit, "duration": self.val(amount, "amount")}

    _EXEC_SOURCES = {
        "parameter": "an inline workflow JSON",
        "url": "a workflow loaded from a URL",
        "localFile": "a workflow loaded from a local file",
    }
    _TRIGGER_FIELD_TYPES = ("string", "number", "boolean", "array", "object")

    def h_exec(self, n: dict, p: dict) -> tuple[str, dict]:
        """n8n Execute Workflow -> ExecuteWorkflow v2 (DESIGN-STAGE8-REUSE 2.5).

        n8n's child ids (and inline / URL / file children) don't exist here, so the node is always imported *unbound*
        (``workflowId: ""``) with a note saying what it pointed at; the checker then reports the missing workflow and the
        owner picks the child. An id is never carried over, so it can't silently bind to the wrong workflow.
        """
        source = p.get("source") or "database"
        target = None
        if source == "database":
            raw = p.get("workflowId")
            ref = _rl(raw)
            label = raw.get("cachedResultName") if isinstance(raw, dict) else None
            if isinstance(ref, str) and ref.strip():
                if ref.strip().startswith("="):
                    target = f"a computed workflow id ({ref.strip()[:80]})"
                else:
                    target = f"n8n workflow {ref.strip()!r}" + (f" ({label})" if label else "")
            elif label:
                target = f"n8n workflow {label!r}"
        elif source in self._EXEC_SOURCES:
            target = self._EXEC_SOURCES[source]
        else:
            target = f"an unsupported source {source!r}"
        self.note(
            f"Execute Workflow called {target or 'a workflow that is not named in the export'}, which is not available here; "
            "the node was imported without a child. Pick the sub-workflow it should call."
        )

        out: dict[str, Any] = {"workflowId": "", "version": "latest"}
        wi = p.get("workflowInputs")
        mapping_mode = wi.get("mappingMode") if isinstance(wi, dict) else None
        value = wi.get("value") if isinstance(wi, dict) else None
        if isinstance(wi, dict) and mapping_mode != "autoMapInputData" and (mapping_mode == "defineBelow" or isinstance(value, dict)):
            inputs: dict[str, Any] = {}
            for k, v in (value if isinstance(value, dict) else {}).items():
                if v is None or not str(k).strip():
                    continue
                inputs[str(k)] = self.val(v, f"workflowInputs.value.{k}")
            out["inputMode"] = "map"
            out["inputs"] = inputs
            if wi.get("attemptToConvertTypes"):
                self.note("Execute Workflow 'attempt to convert types' is not supported; mapped values are passed as written")
            if wi.get("convertFieldsToString"):
                self.note("Execute Workflow 'convert all fields to string' is not supported; mapped values keep their type")
        else:
            out["inputMode"] = "passThrough"
            if mapping_mode not in (None, "autoMapInputData", "defineBelow"):
                self.note(f"Execute Workflow input mapping mode {mapping_mode!r} is not supported; all incoming data is passed through")

        mode = p.get("mode")
        if mode == "each":
            out["mode"] = "each"
        elif mode not in (None, "", "once"):
            self.note(f"Execute Workflow mode {mode!r} is not supported; it runs once with all items")

        opts = p.get("options") if isinstance(p.get("options"), dict) else {}
        for key, ov in opts.items():
            if key == "waitForSubWorkflow":
                if ov is False or (isinstance(ov, str) and ov.strip().lower() == "false"):
                    self.note("Execute Workflow 'wait for sub-workflow' was off (fire and forget); that is not supported, "
                              "so the node waits for the child to finish")
            else:
                self.note(f"Execute Workflow option {key!r} is not supported and was ignored")
        return "ExecuteWorkflow", out

    def h_exec_trigger(self, n: dict, p: dict) -> tuple[str, dict]:
        """n8n Execute Workflow Trigger -> ExecuteWorkflowTrigger (``fields`` or ``passthrough``)."""
        src = p.get("inputSource")
        if src is None:
            # typeVersion 1 had no input source and accepted anything; 1.1+ defaults to declared fields.
            src = "passthrough" if _ver(n) < 1.1 else "workflowInputs"
        if src == "passthrough":
            return "ExecuteWorkflowTrigger", {"inputSource": "passthrough"}

        fields: list[dict[str, Any]] = []
        out: dict[str, Any] = {}
        seen: set[str] = set()

        def add(name: Any, typ: Any) -> None:
            nm = str(name if name is not None else "").strip()
            if not nm:
                return
            if nm in seen:
                self.note(f"Execute Workflow Trigger input {nm!r} is declared twice; the first declaration was kept")
                return
            seen.add(nm)
            if typ in self._TRIGGER_FIELD_TYPES:
                fields.append({"name": nm, "type": typ, "required": False})
            else:
                what = "a null example value" if typ is None else f"type {typ!r}"
                self.note(f"Execute Workflow Trigger input {nm!r} has {what}, which can't be declared here; "
                          "it is still accepted but not type-checked")

        if src == "workflowInputs":
            wi = p.get("workflowInputs")
            values = wi.get("values") if isinstance(wi, dict) else None
            for f in values if isinstance(values, list) else []:
                if isinstance(f, dict):
                    add(f.get("name"), f.get("type", "string"))
        elif src == "jsonExample":
            raw = p.get("jsonExample")
            ex: Any = None
            if isinstance(raw, str):
                try:
                    ex = json.loads(raw[1:] if raw.startswith("=") and not raw.startswith("={{") else raw)
                except ValueError:
                    ex = None
            elif isinstance(raw, dict):
                ex = raw
            if isinstance(ex, dict):
                for k, v in ex.items():
                    typ = ("boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else
                           "array" if isinstance(v, list) else "object" if isinstance(v, dict) else
                           "string" if isinstance(v, str) else None)
                    add(k, typ)
                out["defaultInput"] = json.dumps(ex)
            else:
                self.note("Execute Workflow Trigger JSON example is not a valid JSON object; inputs are not declared")
        else:
            self.note(f"Execute Workflow Trigger input source {src!r} is not supported; it accepts all input")

        if fields:
            return "ExecuteWorkflowTrigger", {"inputSource": "fields", "fields": fields, **out}
        return "ExecuteWorkflowTrigger", {"inputSource": "passthrough", **out}

    def h_respond(self, n: dict, p: dict) -> tuple[str, dict]:
        opts = p.get("options") or {}
        rw = p.get("respondWith", "firstIncomingItem")
        out: dict[str, Any] = {"statusCode": opts.get("responseCode", 200), "contentType": "application/json"}
        if rw == "firstIncomingItem":
            out["body"] = "{{ $json }}"
        elif rw == "allIncomingItems":
            out["body"] = "{{ $input }}"
        elif rw == "json":
            rb = p.get("responseBody", "{}")
            out["body"] = self.val(rb, "responseBody") if isinstance(rb, str) else json.dumps(rb)
        elif rw == "text":
            out["body"] = self.val(p.get("responseBody", ""), "responseBody")
            out["contentType"] = "text/plain"
        elif rw == "noData":
            out["body"] = ""
        else:
            self.note(f"respondWith {rw!r} is not supported; responding with the first item")
            out["body"] = "{{ $json }}"
        entries = ((opts.get("responseHeaders") or {}).get("entries")) or []
        out["headers"] = [{"name": e["name"], "value": self.val(e.get("value", ""), f"responseHeaders.{e['name']}")} for e in entries if e.get("name")]
        return "Respond", out

    def h_stop(self, n: dict, p: dict) -> tuple[str, dict]:
        msg = p.get("errorMessage", "Workflow stopped")
        if p.get("errorType") == "errorObject":
            self.note("errorObject is imported as a plain message")
            msg = p.get("errorObject", msg)
        return "StopAndError", {"errorType": "error", "message": self.val(msg, "errorMessage") if isinstance(msg, str) else json.dumps(msg)}

    def h_code(self, n: dict, p: dict) -> tuple[str, dict]:
        src = _first(p.get("jsCode"), p.get("functionCode"), p.get("pythonCode"), p.get("code")) or ""
        lang = "python" if p.get("language") == "python" else "javascript"
        out = {"code": src, "n8n_language": lang}
        if p.get("mode"):
            out["n8n_mode"] = p["mode"]
        return "Code", out

    def h_postgres(self, n: dict, p: dict) -> tuple[str, dict]:
        op = p.get("operation", "insert")  # n8n default
        base: dict[str, Any] = {"connectionString": "{{ $vars.POSTGRES_URL }}", "operation": "query"}
        self.note("credentials are not imported; the connection string reads $vars.POSTGRES_URL")
        if op == "executeQuery":
            q = self.val(p.get("query", ""), "query")
            qr = (p.get("options") or {}).get("queryReplacement")
            base["query"] = q
            if qr:
                qrt = self.val(qr, "options.queryReplacement")
                if isinstance(qrt, str) and not qrt.lstrip().startswith("{{"):
                    qrt = json.dumps([x.strip() for x in qrt.split(",")])
                elif isinstance(qrt, str):
                    qrt = qrt
                base["queryParameters"] = qrt
            return "Postgres", base
        if op == "select":
            table, schema = _rl(p.get("table")), _rl(p.get("schema")) or "public"
            ident = re.compile(r"^[A-Za-z_][\w$]*$")
            if isinstance(table, str) and ident.match(table) and ident.match(str(schema)):
                sql = f'SELECT * FROM "{schema}"."{table}"'
                if not p.get("returnAll", False) and p.get("limit"):
                    sql += f" LIMIT {int(p['limit'])}"
                if p.get("where") or p.get("options", {}).get("where"):
                    self.note("select WHERE conditions were not imported")
                base["query"] = sql
                return "Postgres", base
        raise _Unmapped(f"Postgres operation {op!r} is not supported by the importer (only executeQuery and simple select)")

    def h_mongo(self, n: dict, p: dict) -> tuple[str, dict]:
        op = p.get("operation", "find")
        base: dict[str, Any] = {
            "connectionString": "{{ $vars.MONGO_URL }}", "database": "{{ $vars.MONGO_DB }}",
            "collection": self.val(p.get("collection", ""), "collection"),
        }
        self.note("credentials are not imported; connection reads $vars.MONGO_URL and $vars.MONGO_DB")
        q = self.val(p.get("query", "{}"), "query") or "{}"
        if op == "find":
            base.update({"operation": "find", "filter": q, "limit": p.get("limit", 0) or 0})
            if p.get("options", {}).get("sort"):
                base["sort"] = self.val(p["options"]["sort"], "options.sort")
            return "MongoDB", base
        if op == "aggregate":
            base.update({"operation": "aggregate", "pipeline": q})
            return "MongoDB", base
        if op == "delete":
            base.update({"operation": "deleteMany", "filter": q})
            return "MongoDB", base
        if op == "insert":
            base.update({"operation": "insertMany", "dataSource": "inputItems"})
            return "MongoDB", base
        raise _Unmapped(f"MongoDB operation {op!r} is not supported by the importer")

    def h_aggregate(self, n: dict, p: dict) -> tuple[str, dict]:
        mode = p.get("aggregate", "aggregateIndividualFields")
        if mode == "aggregateAllItemData":
            return "ItemLists", {"operation": "summarize", "summarizeField": p.get("destinationFieldName", "data"), "includeCount": False}
        fields = (p.get("fieldsToAggregate") or {}).get("fieldToAggregate") or []
        aggs = []
        for f in fields:
            name = f.get("fieldToAggregate")
            if name:
                aggs.append({"field": name, "aggOperation": "collect", "outputField": f.get("renameField") and f.get("outputFieldName") or name})
        return "ItemLists", {"operation": "aggregate", "groupBy": "", "aggregations": aggs}

    def h_itemlists(self, n: dict, p: dict) -> tuple[str, dict]:
        op = p.get("operation", "aggregateItems")
        if op == "sortItems":
            fields = (p.get("sortFieldsUi") or {}).get("sortField") or []
            f = fields[0] if fields else {}
            if len(fields) > 1:
                self.note("only the first sort field was imported")
            return "ItemLists", {"operation": "sort", "sortBy": f.get("fieldName", ""), "order": "descending" if f.get("order") == "descending" else "ascending", "sortType": "auto"}
        if op == "limit":
            return "ItemLists", {"operation": "limit", "maxItems": p.get("maxItems", 1), "offset": 0}
        if op == "removeDuplicates":
            cf = p.get("fieldsToCompare", "")
            return "ItemLists", {"operation": "removeDuplicates", "compareField": str(cf).split(",")[0].strip(), "keep": "first"}
        if op == "splitOutItems":
            return "ItemLists", {"operation": "splitOut", "arrayField": p.get("fieldToSplitOut", ""), "includeOther": p.get("include", "noOtherFields") != "noOtherFields"}
        if op == "aggregateItems":
            fields = (p.get("fieldsToAggregate") or {}).get("fieldToAggregate") or []
            return "ItemLists", {"operation": "aggregate", "groupBy": "", "aggregations": [{"field": f.get("fieldToAggregate", ""), "aggOperation": "collect", "outputField": f.get("fieldToAggregate", "")} for f in fields]}
        raise _Unmapped(f"Item Lists operation {op!r} is not supported")

    def h_split_out(self, n: dict, p: dict) -> tuple[str, dict]:
        return "ItemLists", {"operation": "splitOut", "arrayField": str(self.val(p.get("fieldToSplitOut", ""), "fieldToSplitOut")), "includeOther": p.get("include", "noOtherFields") != "noOtherFields"}

    def h_limit(self, n: dict, p: dict) -> tuple[str, dict]:
        return "ItemLists", {"operation": "limit", "maxItems": self.val(p.get("maxItems", 1), "maxItems"), "offset": 0}

    def h_sort(self, n: dict, p: dict) -> tuple[str, dict]:
        fields = (p.get("sortFieldsUi") or {}).get("sortField") or []
        f = fields[0] if fields else {}
        return "ItemLists", {"operation": "sort", "sortBy": f.get("fieldName", ""), "order": "descending" if f.get("order") == "descending" else "ascending", "sortType": "auto"}

    def h_remove_dupes(self, n: dict, p: dict) -> tuple[str, dict]:
        cf = (p.get("fieldsToCompare") or "") if p.get("compare") == "selectedFields" else ""
        return "ItemLists", {"operation": "removeDuplicates", "compareField": str(cf).split(",")[0].strip(), "keep": "first"}

    HANDLERS: dict[str, str] = {
        "manualTrigger": "h_start", "start": "h_start", "webhook": "h_webhook",
        "scheduleTrigger": "h_schedule", "cron": "h_cron", "httpRequest": "h_http", "set": "h_set",
        "noOp": "h_noop", "if": "h_if", "filter": "h_filter", "switch": "h_switch", "merge": "h_merge",
        "splitInBatches": "h_loop", "wait": "h_wait", "executeWorkflow": "h_exec",
        "executeWorkflowTrigger": "h_exec_trigger", "respondToWebhook": "h_respond",
        "postgres": "h_postgres", "mongoDb": "h_mongo", "aggregate": "h_aggregate",
        "itemLists": "h_itemlists", "stopAndError": "h_stop", "code": "h_code", "function": "h_code",
        "functionItem": "h_code", "splitOut": "h_split_out", "limit": "h_limit", "sort": "h_sort",
        "removeDuplicates": "h_remove_dupes",
    }
    CODE_TYPES = {"code", "function", "functionItem"}

    # -- main ------------------------------------------------------------------------------
    def run(self, name: str | None) -> tuple[dict, dict]:
        raw_nodes = [n for n in self.n8n["nodes"] if isinstance(n, dict) and _short(n.get("type", "")) != "stickyNote"]
        if not raw_nodes:
            raise ValueError("n8n workflow has no importable nodes (only sticky notes or empty)")

        # Unique, engine-valid names (renames are applied to $node refs and connections).
        used: set[str] = set()
        for i, n in enumerate(raw_nodes):
            orig = n.get("name")
            base = _clean_name(orig, _short(n.get("type", "")) or f"node{i + 1}")
            cand, k = base, 2
            while cand.lower() in used:
                cand = f"{base} {k}"
                k += 1
            used.add(cand.lower())
            self.name_map[str(orig)] = cand

        info: dict[str, dict] = {}  # our node name -> routing info
        nodes_out: list[dict] = []
        for n in raw_nodes:
            self.cur = str(n.get("name"))
            our_name = self.name_map[self.cur]
            full_type = n.get("type", "")
            short = _short(full_type)
            params = n.get("parameters") if isinstance(n.get("parameters"), dict) else {}
            disabled = bool(n.get("disabled", False))
            node_type: str
            out_params: dict[str, Any]
            kind = "default"
            try:
                handler_name = self.HANDLERS.get(short)
                if handler_name is None or full_type.startswith("@n8n/"):
                    raise _Unmapped(f"n8n node type {short or full_type!r} has no equivalent in this engine")
                node_type, out_params = getattr(self, handler_name)(n, params)
                if short in self.CODE_TYPES:
                    disabled = True
                    self.disabled_code.append(our_name)
                self.mapped.append({"n8n_name": self.cur, "n8n_type": full_type, "node_type": node_type})
                kind = {"If": "if", "Switch": "switch", "Loop": "loop"}.get(node_type, "default")
            except _Unmapped as e:
                self.unmapped.append({"n8n_name": self.cur, "n8n_type": full_type, "reason": e.reason})
                node_type = "Set"
                out_params = {
                    "mode": "manual", "fields": [], "keepOnlySet": False,
                    "n8n_type": full_type, "n8n_type_version": n.get("typeVersion"),
                    "n8n_parameters": params,
                }
                disabled = True
                kind = "placeholder"
            ninfo = {"kind": kind, "version": _ver(n), "normal": 1, "error": False}
            if kind == "if":
                ninfo["normal"] = 2
            elif kind == "switch":
                no = out_params.get("numberOfOutputs", 2)
                ninfo["normal"] = no
                fb = (params.get("options") or {}).get("fallbackOutput", params.get("fallbackOutput"))
                ninfo["fallback_idx"] = no if fb == "extra" else (int(fb) if isinstance(fb, int) and not isinstance(fb, bool) else None)
                if ninfo["fallback_idx"] is not None:
                    ninfo["normal"] = max(no, ninfo["fallback_idx"] + 1)
            elif kind == "loop":
                ninfo["normal"] = 2 if _ver(n) >= 2 else 1
            on_error = None
            oe = n.get("onError")
            if oe == "continueRegularOutput" or n.get("continueOnFail"):
                on_error = "continue"
            elif oe == "continueErrorOutput":
                on_error = "error_output"
                ninfo["error"] = True
            info[our_name] = ninfo
            node: dict[str, Any] = {
                "name": our_name, "type": node_type, "parameters": out_params, "disabled": disabled,
            }
            pos = n.get("position")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                try:
                    node["position"] = {"x": float(pos[0]), "y": float(pos[1])}
                except (TypeError, ValueError):
                    pass
            if on_error:
                node["on_error"] = on_error
            if kind == "placeholder" or short in self.CODE_TYPES:
                # Disabled stand-ins would otherwise be treated as passthrough by the checker, which flags every
                # field the real node would have produced. Their output is genuinely unknown.
                node["contract"] = {
                    "outputs": {"main": {"type": "unknown", "x-source": "unknown"}},
                    "source": "import_placeholder",
                    "approved_by": "n8n-import",
                }
            if n.get("retryOnFail"):
                node["retry_on_fail"] = max(0, int(n.get("maxTries", 3) or 3) - 1)
                node["retry_delay"] = int(n.get("waitBetweenTries", 1000) or 1000)
            if n.get("credentials") and kind not in ("placeholder",) and short not in ("postgres", "mongoDb"):
                self.note("credentials are not imported")
            nodes_out.append(node)

        edges = self._edges(info)

        # Ensure the workflow can start: add a Start node when no trigger was mapped.
        if not any(nd["type"] in _TRIGGERS and not nd["disabled"] for nd in nodes_out):
            has_in = {e["target_node"] for e in edges}
            roots = [nd for nd in nodes_out if nd["name"] not in has_in]
            start_name = "Start"
            k = 2
            while start_name.lower() in {nd["name"].lower() for nd in nodes_out}:
                start_name = f"Start {k}"
                k += 1
            xs = [nd["position"]["x"] for nd in roots if nd.get("position")]
            ys = [nd["position"]["y"] for nd in roots if nd.get("position")]
            start = {"name": start_name, "type": "Start", "parameters": {}, "disabled": False}
            if xs:
                start["position"] = {"x": min(xs) - 220, "y": ys[0]}
            nodes_out.insert(0, start)
            for r in roots:
                edges.append({"source_node": start_name, "target_node": r["name"], "source_output": "main", "target_input": "main"})
            self.notes.append({"node": start_name, "message": "no supported trigger was found; a Start node was added"})

        wf_name = _clean_name(name or self.n8n.get("name"), "Imported n8n workflow")[:255]
        workflow = {
            "name": wf_name,
            "nodes": nodes_out,
            "connections": edges,
            "description": "Imported from n8n",
            "settings": {},
        }
        report = {
            "mapped": self.mapped,
            "unmapped_nodes": self.unmapped,
            "untranslated_expressions": self.untranslated,
            "disabled_code_nodes": self.disabled_code,
            "findings": [],
            "notes": self.notes,
        }
        return workflow, report

    def _port(self, ninfo: dict, i: int) -> str:
        kind = ninfo["kind"]
        if ninfo["error"] and i == ninfo["normal"]:
            return "error"
        if kind == "if":
            return "true" if i == 0 else "false"
        if kind == "switch":
            fb = ninfo.get("fallback_idx")
            if fb is not None and i == fb:
                return "fallback"
            return f"output{min(i, 14)}"
        if kind == "loop":
            if ninfo["version"] >= 2:
                return "done" if i == 0 else "loop"
            return "loop"
        return "main"

    def _edges(self, info: dict[str, dict]) -> list[dict]:
        edges: list[dict] = []
        seen: set[tuple] = set()
        conns = self.n8n.get("connections") or {}
        for src_raw, outs in conns.items():
            src = self.name_map.get(str(src_raw))
            if src is None or not isinstance(outs, dict):
                continue
            for ctype, branches in outs.items():
                if ctype != "main":
                    continue
                for i, branch in enumerate(branches or []):
                    for t in branch or []:
                        tgt = self.name_map.get(str(t.get("node")))
                        if tgt is None:
                            continue
                        port = self._port(info[src], i)
                        key = (src, port, tgt)
                        if key in seen:
                            continue
                        seen.add(key)
                        edges.append({"source_node": src, "target_node": tgt, "source_output": port, "target_input": "main"})
        return edges


def _unwrap(n8n_json: Any) -> dict:
    if isinstance(n8n_json, str):
        try:
            n8n_json = json.loads(n8n_json)
        except ValueError as e:
            raise ValueError(f"n8n payload is not valid JSON: {e}") from e
    for _ in range(3):  # the templates API wraps the export as {workflow: {workflow: {...}}}
        if isinstance(n8n_json, dict) and "nodes" not in n8n_json and isinstance(n8n_json.get("workflow"), dict):
            n8n_json = n8n_json["workflow"]
    if not isinstance(n8n_json, dict):
        raise ValueError("Not an n8n workflow: expected a JSON object with 'nodes' and 'connections'")
    nodes = n8n_json.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Not an n8n workflow: missing a non-empty 'nodes' list")
    if not all(isinstance(n, dict) and isinstance(n.get("type"), str) for n in nodes):
        raise ValueError("Not an n8n workflow: every node needs a string 'type' (for example 'n8n-nodes-base.set')")
    conns = n8n_json.get("connections", {})
    if not isinstance(conns, dict):
        raise ValueError("Not an n8n workflow: 'connections' must be an object")
    n8n_json = dict(n8n_json)
    n8n_json["connections"] = conns
    return n8n_json


def _run_checker(workflow: dict, project_id: str | None = None) -> list[dict]:
    """Findings are filled in by the route (``routes/n8n_import.py``), which has the DB session the
    checker's lookups need; the pure importer leaves them empty."""
    return []


def import_n8n(n8n_json: dict, name: str | None = None, project_id: str | None = None) -> tuple[dict, dict]:
    """Convert an n8n workflow export into a WorkflowCreateRequest-shaped dict plus a report.

    Raises ``ValueError`` when the input is not an n8n workflow.
    """
    n8n = _unwrap(n8n_json)
    workflow, report = _Importer(n8n).run(name)
    if project_id:
        workflow["project_id"] = project_id
    # --- CHECKER CALL SITE (single, marked): the checker agent wires this -----------------
    findings = _run_checker(workflow, project_id)
    report["findings"] = findings
    return workflow, report
