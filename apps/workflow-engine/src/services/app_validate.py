"""Static checks on generated app code that tsc cannot do: the data contract and invented display data.

* ``contract_issues``: every property the UI reads from an ``api.<op>(...)`` result must exist in that route's response
  signature (fields read from the handler's AST by ``project_knowledge``, or the route's response schema). Reads are followed
  one level into ``.map/.filter/.find`` callbacks. Unknown signatures are skipped, never guessed.
* ``literal_issues``: JSX text that looks like data (``Passed (320ms)``, ``92%``, ``TC-218``, ``run #1482``) and ``MOCK_*`` constants.

Both return short human-readable findings, fed back to the model as a bounded repair nudge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SYSTEM_FIELDS = frozenset({"id", "created_at", "updated_at"})
_BUILTIN_MEMBERS = frozenset({
    "length", "map", "filter", "find", "findIndex", "forEach", "some", "every", "slice", "join", "concat", "includes", "reduce", "sort",
    "indexOf", "push", "toString", "keys", "values", "entries", "flat", "flatMap", "at", "reverse", "splice", "then", "catch", "finally",
    "hasOwnProperty", "toFixed", "toLocaleString", "trim", "split", "startsWith", "endsWith", "toLowerCase", "toUpperCase", "replace",
})
_ITER = r"(?:map|filter|find|findIndex|forEach|some|every|flatMap)"


@dataclass
class OpSig:
    """What one operation is known to return. ``fields`` is the union of every key the handler's response can carry."""
    fields: set[str] = field(default_factory=set)
    known: bool = False
    path_params: bool | None = None   # None = unknown
    elements_known: bool = True       # False: only the response's top-level keys are known, not the keys of nested array elements


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)(?<![:'\"`\\])//.*$", "", src)


def schema_fields(schema: object, depth: int = 0) -> set[str]:
    """Property names anywhere in a JSON schema (nested objects and array items)."""
    out: set[str] = set()
    if depth > 6 or not isinstance(schema, dict):
        return out
    props = schema.get("properties")
    if isinstance(props, dict):
        for k, v in props.items():
            out.add(str(k))
            out |= schema_fields(v, depth + 1)
    if "items" in schema:
        out |= schema_fields(schema["items"], depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key) or []:
            out |= schema_fields(sub, depth + 1)
    return out


def _destructured(names: str) -> list[str]:
    out = []
    for part in names.split(","):
        part = part.strip()
        if not part or part.startswith("..."):
            continue
        out.append(re.split(r"[:=]", part, maxsplit=1)[0].strip())
    return [n for n in out if re.fullmatch(r"\w+", n)]


_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def _skip_string(src: str, i: int) -> int:
    """If ``src[i]`` opens a single-line '...' or "..." string, the index just past it; else ``i``."""
    q = src[i]
    j = i + 1
    while j < len(src) and src[j] not in (q, "\n"):
        j += 2 if src[j] == "\\" else 1
    return j + 1 if j < len(src) and src[j] == q else i


def _block_end(src: str, pos: int) -> int:
    """End of the innermost ``{ ... }`` block (or the file) that encloses ``pos``."""
    depth = 0
    i = pos
    n = len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            i = max(_skip_string(src, i), i + 1)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    return n


def _match_forward(src: str, i: int) -> int:
    """``src[i]`` is an opener; the index of its matching closer (or the end of the file)."""
    depth = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            i = max(_skip_string(src, i), i + 1)
            continue
        if c in _CLOSERS:
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _arrow_body(src: str, i: int) -> tuple[int, int]:
    """(start, end) of the body of an arrow function whose ``=>`` ends just before ``i``."""
    while i < len(src) and src[i].isspace():
        i += 1
    if i < len(src) and src[i] == "{":
        return i, _match_forward(src, i)
    depth = 0
    j = i
    while j < len(src):
        c = src[j]
        if c in "'\"":
            j = max(_skip_string(src, j), j + 1)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c in ",;" and depth == 0:
            break
        j += 1
    return i, j


def _param_names(text: str) -> set[str]:
    """Identifiers a parameter list introduces (plain, defaulted, typed or destructured)."""
    names: set[str] = set()
    depth = 0
    part = ""
    parts: list[str] = []
    for c in text:
        if c in "({[<":
            depth += 1
        elif c in ")}]>":
            depth -= 1
        if c == "," and depth <= 0:
            parts.append(part)
            part = ""
        else:
            part += c
    parts.append(part)
    for p in parts:
        p = p.strip()
        if p.startswith("{") or p.startswith("["):
            inner = re.split(r"[:=]\s*[A-Za-z_][\w<>\[\]| ]*$", p.strip("{}[] "))[0]
            names |= set(re.findall(r"\w+", re.sub(r"\w+\s*:", "", inner)))
            names |= set(re.findall(r"(?<=:)\s*(\w+)", inner))
        else:
            m = re.match(r"\.{0,3}\s*(\w+)", p)
            if m:
                names.add(m.group(1))
    return names


def _shadow_ranges(src: str, var: str, start: int, end: int, own: int) -> list[tuple[int, int]]:
    """Ranges inside [start, end) where ``var`` names something else: an arrow parameter or a nested declaration."""
    root = var.split(".")[0]
    out: list[tuple[int, int]] = []
    for m in re.finditer(r"=>", src[start:end]):
        arrow = start + m.start()
        k = arrow - 1
        while k >= start and src[k].isspace():
            k -= 1
        if k < start:
            continue
        if src[k] == ")":
            depth, j = 0, k
            while j >= start:
                if src[j] == ")":
                    depth += 1
                elif src[j] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            params = src[j + 1:k]
        else:
            w = re.search(r"([\w$]+)\s*$", src[start:k + 1])
            params = w.group(1) if w else ""
        if root in _param_names(params):
            out.append(_arrow_body(src, arrow + 2))
    for m in re.finditer(r"\b(?:const|let|var)\s+" + re.escape(root) + r"\b|\b(?:const|let|var)\s*\{[^}]*\b" + re.escape(root) + r"\b[^}]*\}", src[start:end]):
        at = start + m.start()
        if at != own:
            out.append((at, _block_end(src, at)))
    return out


@dataclass
class Binding:
    """``var`` holds the result of ``api.<op>`` inside ``src[start:end]`` (its lexical scope)."""
    var: str
    op: str
    start: int
    end: int
    own: int = -1


# ``api.op(`` with any normal formatting (``api\n  .op(``); not ``xapi.op(`` / ``.api.op(``. Group 1 is the operation.
_API = r"(?<![\w$.])api\s*\.\s*(\w+)\s*\("


def bindings(src: str) -> tuple[list[Binding], list[tuple[str, str]]]:
    """([scoped variable bindings], [(destructured field, operation)])."""
    found: list[Binding] = []
    destructured: list[tuple[str, str]] = []

    for m in re.finditer(r"(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>\[\]| ]+?)?\s*=\s*await\s+" + _API, src):
        found.append(Binding(m.group(1), m.group(2), m.end(), _block_end(src, m.end()), m.start()))
    for m in re.finditer(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*await\s+" + _API, src):
        destructured += [(n, m.group(2)) for n in _destructured(m.group(1))]
    for m in re.finditer(_API, src):
        window = src[m.end():m.end() + 240]
        t = re.match(r"[^;]{0,200}?\)\s*\.then\(\s*\(?\s*(\w+)([^=]{0,60}?)=>", window)
        if t and not re.search(r"(?<![\w$.])api\s*\.", window[:t.start(1)]):
            body = _arrow_body(src, m.end() + t.end())
            found.append(Binding(t.group(1), m.group(1), body[0], body[1]))
    for m in re.finditer(r"(?:const|let)\s+(\{[^}]*\}|\w+)\s*=\s*usePoll\(\s*(?:async\s*)?\(\s*\)\s*=>\s*(?:\{\s*return\s+)?(?:await\s+)?" + _API, src):
        target, op = m.group(1), m.group(2)
        end = _block_end(src, m.end())
        if target.startswith("{"):
            dm = re.search(r"\bdata\s*(?::\s*(\w+))?", target)
            if dm:
                found.append(Binding(dm.group(1) or "data", op, m.end(), end, m.start()))
        else:
            found.append(Binding(target + ".data", op, m.end(), end, m.start()))  # the hook result: its payload is `<var>.data`
    return found, destructured


def _member_reads(src: str, var: str, start: int = 0, end: int | None = None, skip: list[tuple[int, int]] | None = None) -> list[str]:
    """Fields read as ``var.field`` / ``var?.field`` inside ``src[start:end]``, outside the ``skip`` ranges."""
    pat = r"(?<![\w$.])" + re.escape(var) + r"\??\.(\w+)"
    out = []
    for m in re.finditer(pat, src[start:end if end is not None else len(src)]):
        at = start + m.start()
        if skip and any(a <= at < b for a, b in skip):
            continue
        out.append(m.group(1))
    return out


def _make_flagger(path: str, sigs: dict[str, OpSig], seen: set[tuple[str, str]], issues: list[str]):
    """A reporter for one file: ``flag(prefix, field, ops)`` records a finding when ``field`` is not a known response field of ``ops``."""
    def flag(what: str, field_: str, ops: set[str]) -> None:
        known = [sigs[o] for o in ops if o in sigs and sigs[o].known]
        if not known or len(known) != len(ops):
            return  # an unknown signature is never guessed at
        allowed = set(SYSTEM_FIELDS)
        for s in known:
            allowed |= s.fields
        if field_ in allowed or field_ in _BUILTIN_MEMBERS or (path, what + field_) in seen:
            return
        seen.add((path, what + field_))
        op_list = ", ".join(f"api.{o}" for o in sorted(ops))
        have = ", ".join(sorted(allowed - SYSTEM_FIELDS)[:14]) or "(none)"
        issues.append(f"{path}: reads `{what}{field_}` from {op_list}, but that route never returns `{field_}` (it returns: {have})")
    return flag


def ops_elements_known(ops: set[str], sigs: dict[str, OpSig]) -> bool:
    return all(o in sigs and sigs[o].elements_known for o in ops)


def contract_issues(files: list[dict[str, str]], sigs: dict[str, OpSig]) -> list[str]:
    """Findings such as ``views/Library.tsx: reads `res.items` from api.getCases (fields: rows, total)``.

    A variable only means "the result of api.op" inside the scope where it was bound (its block, or the ``.then`` callback),
    and not inside a nested function that re-uses the name as a parameter (``rows.map((r) => ...)``). A row variable over a nested
    array field (``data.requirements.map((r) => r.req_key)``) is only checked when the signature knows element keys.
    """
    issues: list[str] = []
    for f in files:
        path = f["path"]
        if path.startswith(("kit/", "src/kit/")) or path.endswith("api/client.ts") or not path.endswith((".tsx", ".ts", ".jsx")):
            continue
        src = strip_comments(f["content"])
        if "api." not in src:
            continue
        scoped, destructured = bindings(src)
        seen: set[tuple[str, str]] = set()
        flag = _make_flagger(path, sigs, seen, issues)

        for fld, op in destructured:
            flag("", fld, {op})
        # (variable, read position range) -> operations; two bindings of one name that overlap contribute both
        for b in scoped:
            skip = _shadow_ranges(src, b.var, b.start, b.end, b.own)
            ops = {x.op for x in scoped if x.var == b.var and x.start <= b.start < x.end} | {b.op}
            base = b.var
            direct = _member_reads(src, base, b.start, b.end, skip)
            for fld in direct:
                flag(f"{base}.", fld, ops)
            # callbacks over the result itself or over one of its fields: rows.map((r) => r.x) -- `r` lives only in the callback body.
            # Elements of a nested field are only checked when the signature knows element keys (response schema / table columns);
            # a signature of bare top-level keys says nothing about what data.requirements[] carries.
            eb = re.escape(base)
            chains = [(eb, True)] + [(eb + r"\??\." + x, ops_elements_known(ops, sigs)) for x in set(direct) if x not in _BUILTIN_MEMBERS]
            for chain, checked in chains:
                if not checked:
                    continue
                for cm in re.finditer(r"(?<![\w$.])" + chain + r"\??\." + _ITER + r"\(\s*\(?\s*(\w+)([^=]{0,60}?)=>", src[b.start:b.end]):
                    item = cm.group(1)
                    body_start, body_end = _arrow_body(src, b.start + cm.end())
                    for fld in _member_reads(src, item, body_start, body_end):
                        flag(f"{item}.", fld, ops)
    issues += call_shape_issues(files, sigs)
    return issues


_EMPTY_FIRST_ARG = re.compile(r"(?<![\w$.])api\s*\.\s*(\w+)\s*\(\s*(?:\{\s*\}|undefined|null)\s*,\s*[{\w]")


def call_shape_issues(files: list[dict[str, str]], sigs: dict[str, OpSig]) -> list[str]:
    """`api.op({}, {body})` on a route without path params: the client then sends the body as a query string (400s)."""
    out: list[str] = []
    for f in files:
        if f["path"].startswith(("kit/", "src/kit/")) or f["path"].endswith("api/client.ts") or not f["path"].endswith((".tsx", ".ts", ".jsx")):
            continue
        for m in _EMPTY_FIRST_ARG.finditer(strip_comments(f["content"])):
            sig = sigs.get(m.group(1))
            if sig is not None and sig.path_params is False:
                out.append(f"{f['path']}: `api.{m.group(1)}({{}}, …)` — this route has no path params, so the body is the FIRST argument: `api.{m.group(1)}({{…body}})`")
    return out


_JSX_TEXT = re.compile(r">([^<>{}\n;=]*[A-Za-z][^<>{}\n;=]*)<")
_DATAISH = re.compile(
    r"\(\s*\d+(?:\.\d+)?\s?(?:ms|s|sec|min)\s*\)"          # Passed (320ms)
    r"|\b\d+(?:\.\d+)?\s?%"                                 # 92%
    r"|\b(?:Passed|Failed|Blocked|Running)\s*[·:(-]\s*\d"   # Failed · step 5
    r"|\b[A-Z]{2,6}-\d{2,}\b"                               # TC-218
    r"|\b(?:run|job|case|build)\s*#\d+"                      # run #1482
    r"|\bLast (?:green|passed|run|failed)\b[^<]*[#\d]",
    re.IGNORECASE)
_MOCK_CONST = re.compile(r"\b(?:const|let)\s+((?:MOCK|SAMPLE|DEMO|FAKE|DUMMY|SEED|STUB)\w*|\w*(?:Mock|Sample|Demo|Fake|Dummy)\w*)\s*(?::[^=]+)?=\s*[\[{]")


def literal_issues(files: list[dict[str, str]]) -> list[str]:
    """JSX text that reads like data the UI invented, and mock-data constants."""
    issues: list[str] = []
    for f in files:
        path = f["path"]
        if path.startswith(("kit/", "src/kit/")) or path.endswith(("api/client.ts", "BRIEF.md")) or not path.endswith((".tsx", ".jsx")):
            continue
        src = strip_comments(f["content"])
        for m in _JSX_TEXT.finditer(src):
            text = m.group(1).strip()
            hit = _DATAISH.search(text)
            if hit and "&&" not in text:
                issues.append(f"{path}: literal data-like text \"{text[:70]}\" (`{hit.group(0).strip()}`) — render it from an API field or remove it")
        for m in _MOCK_CONST.finditer(src):
            issues.append(f"{path}: mock data constant `{m.group(1)}` — the UI must show API data, not stand-ins")
    return issues[:12]


# ── Delivery checks: is what the plan promised actually there, and does every call hit a real route? ─────────────

_CLIENT_OP = re.compile(r"(?m)^  (\w+): \(")
_API_CALL = re.compile(_API)


def _ui_files(files: list[dict[str, str]]) -> list[dict[str, str]]:
    return [f for f in files if f["path"].endswith((".tsx", ".ts", ".jsx"))
            and not f["path"].startswith(("kit/", "src/kit/")) and not f["path"].endswith("api/client.ts")]


def client_operations(files: list[dict[str, str]]) -> set[str] | None:
    """Operation names the generated ``api/client.ts`` exposes; None when the client is not in the file set."""
    for f in files:
        if f["path"].endswith("api/client.ts"):
            return set(_CLIENT_OP.findall(f["content"]))
    return None


def called_operations(files: list[dict[str, str]]) -> set[str]:
    out: set[str] = set()
    for f in _ui_files(files):
        out |= set(_API_CALL.findall(strip_comments(f["content"])))
    return out


def unknown_route_calls(files: list[dict[str, str]], known_ops: set[str] | None = None) -> list[str]:
    """``api.<op>(...)`` calls whose operation is not a route of the project: the route has to be declared first."""
    known = client_operations(files) if known_ops is None else known_ops
    if known is None:
        return []
    out: list[str] = []
    for f in _ui_files(files):
        seen: set[str] = set()
        for op in _API_CALL.findall(strip_comments(f["content"])):
            if op in known or op in seen:
                continue
            seen.add(op)
            out.append(f"{f['path']}: calls `api.{op}`, but this project has no such route — call `declare_endpoint` for it "
                       f"(method + path) so it exists and is attached, or remove the call")
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def screen_gaps(screens: list[dict[str, str]], files: list[dict[str, str]]) -> list[str]:
    """Planned screens with no file, component or on-screen heading to show for them (names, in plan order)."""
    ui = _ui_files(files)
    if len(screens) <= 1 and ui:
        return []  # a one-screen plan is App.tsx itself: there is no separate file to look for
    stems = {_norm(f["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]) for f in ui}
    comps = {_norm(m) for f in ui for m in re.findall(r"(?:function|const|class)\s+([A-Z]\w+)", f["content"])}
    bodies = [f["content"].lower() for f in ui if f["path"].rsplit("/", 1)[-1] not in ("App.tsx", "app.tsx")]
    missing = []
    for sc in screens:
        name = str(sc.get("name") or "").strip()
        n = _norm(name)
        if not n:
            continue
        def hit(cand: str) -> bool:
            return bool(cand) and (cand == n or (len(cand) >= 4 and (cand in n or n in cand)))
        if any(hit(c) for c in stems | comps) or any(name.lower() in b for b in bodies):
            continue
        missing.append(name)
    return missing


def unused_planned_routes(routes: list[dict[str, str]], files: list[dict[str, str]]) -> list[str]:
    """Planned operations (``METHOD /path``) that no file ever calls."""
    called = called_operations(files)
    return [f"{r.get('method')} {r.get('path')}" for r in routes if r.get("operation") and r["operation"] not in called]
