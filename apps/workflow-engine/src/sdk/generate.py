"""Generate the ``studio`` Python package from the node registry (BACKEND-DESIGN §3.4, §4.1).

One function per function-exposed node: ``studio.nodes.http_request(method=..., url=...)``.
Function and parameter names are snake_case; they map back to the node's property names.
Output is deterministic and keyed by a hash of the registry, cached on disk for the sandbox.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import keyword
import sys
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..broker.policy import egress_kind, is_exposed, snake

STATIC_DIR = Path(__file__).parent / "static"
_PY_TYPES = {"string": "str", "number": "float", "boolean": "bool", "options": "str",
             "collection": "list | dict", "json": "Any", "fixedCollection": "dict"}
_JSON_TYPES = {"string": "string", "number": "number", "boolean": "boolean", "options": "string"}


def _py_name(name: str) -> str:
    n = snake(name).replace("-", "_").replace(" ", "_")
    if keyword.iskeyword(n) or n in ("items", "parameters"):
        n += "_"
    return n


def _json_schema(prop: Any) -> dict[str, Any]:
    t = prop.type
    if t in _JSON_TYPES:
        s: dict[str, Any] = {"type": _JSON_TYPES[t]}
    elif t == "collection":
        multi = (prop.type_options or {}).get("multipleValues")
        inner = {"type": "object", "properties": {p.name: _json_schema(p) for p in (prop.properties or [])}}
        s = {"type": "array", "items": inner} if multi else inner
    else:
        s = {}
    if prop.options and t == "options":
        s["enum"] = [o.value for o in prop.options]
    if prop.description:
        s["description"] = prop.description[:500]
    if prop.default not in (None, "", [], {}):
        s["default"] = prop.default
    return s


_ANN = {"string": "str", "number": "float", "boolean": "bool", "array": "list", "object": "dict"}


def _python_doc(fn: str, props: list[Any]) -> dict[str, Any]:
    """Review G-UI-5: the python-side view of a function — snake_case keyword params with type,
    default, required and enum — plus a display signature. Same names as the generated SDK."""
    params = []
    for p in props:
        schema = _json_schema(p)
        params.append({"name": _py_name(p.name), "property": p.name, "type": _ANN.get(schema.get("type"), "Any"),
                       "default": schema.get("default"), "required": bool(p.required),
                       "enum": schema.get("enum"), "description": schema.get("description")})
    parts = []
    for q in sorted(params, key=lambda q: not q["required"]):
        if q["required"]:
            parts.append(f"{q['name']}: {q['type']}")
        else:
            parts.append(f"{q['name']}: {q['type']} = {json.dumps(q['default']) if q['default'] is not None else 'None'}")
    sig = f"{fn}(*, {', '.join(parts + ['items: list[dict] | None = None'])}) -> list[dict]"
    return {"signature": sig, "params": params}


def _fmt_signature(display: str, fn: Any, drop_first: bool = False) -> tuple[str, list[dict[str, Any]]]:
    """Signature text from the real SDK source (annotations as written, string defaults double-quoted)."""
    import inspect

    sig = inspect.signature(fn)
    out, params, star = [], [], False
    items = list(sig.parameters.values())[1 if drop_first else 0:]
    for prm in items:
        if prm.kind is prm.VAR_KEYWORD or prm.kind is prm.VAR_POSITIONAL:
            continue
        if prm.kind is prm.KEYWORD_ONLY and not star:
            out.append("*")
            star = True
        ann = prm.annotation if isinstance(prm.annotation, str) else (
            "" if prm.annotation is prm.empty else inspect.formatannotation(prm.annotation))
        txt = f"{prm.name}: {ann}" if ann else prm.name
        default = None
        if prm.default is not prm.empty:
            default = prm.default
            txt += " = " + (json.dumps(prm.default) if isinstance(prm.default, str) else repr(prm.default))
        out.append(txt)
        params.append({"name": prm.name, "annotation": ann or None, "default": default,
                       "required": prm.default is prm.empty, "keyword_only": prm.kind is prm.KEYWORD_ONLY})
    return f"{display}({', '.join(out)})", params


@lru_cache
def reference() -> dict[str, Any]:
    """Review C14: the hand-written part of the SDK (api, Response, HTTPError, Request, secret, db),
    documented from the SDK source itself — one source for the studio's sdk reference and the LLM
    generation context. GET /api/sdk/reference."""
    import importlib

    api_mod = importlib.import_module("src.sdk.static.api")
    secret_mod = importlib.import_module("src.sdk.static.secret")
    db_mod = importlib.import_module("src.sdk.static.db")
    from ..services.code_module_service import DECORATOR_KEYWORDS, ROUTE_AUTHS

    entries: list[dict[str, Any]] = []

    def add(name: str, fn: Any, kind: str, doc: str, drop_first: bool = False, **extra: Any) -> None:
        sig, params = _fmt_signature(name, fn, drop_first)
        entries.append({"name": name, "kind": kind, "signature": sig, "params": params, "doc": doc, **extra})

    for m in ("get", "post", "put", "patch", "delete"):
        add(f"api.{m}", getattr(api_mod, m), "decorator",
            f"Declare a {m.upper()} route handled by the decorated function(req).",
            auth_values=sorted(ROUTE_AUTHS), keywords=sorted(DECORATOR_KEYWORDS))
    add("Response", api_mod.Response.__init__, "class", "Return a custom status/headers/body from a route handler.",
        drop_first=True)
    add("HTTPError", api_mod.HTTPError.__init__, "exception", "Raise to answer with an HTTP error status and body.",
        drop_first=True)
    entries.append({"name": "Request", "kind": "class", "signature": "Request",
                    "attributes": ["method", "path", "route", "params", "query", "headers", "body"],
                    "doc": "The `req` argument of a route handler; `req.json()` returns the body.", "params": []})
    add("studio.secret", secret_mod.secret, "function",
        "A reference to a project secret; its str() is a placeholder the broker resolves server-side.")
    add("studio.db.table", db_mod.table, "function", "A project data table.")
    for op in ("insert", "get", "list", "update", "delete", "upsert"):
        add(f"studio.db.table(name).{op}", getattr(db_mod.Table, op), "method", f"Table.{op}", drop_first=True)
    add("studio.nodes.call", lambda node, parameters=None, items=None: None, "function",
        "Call any function-exposed node by type (see GET /api/sdk/functions for named functions).")
    entries[-1]["signature"] = "studio.nodes.call(node: str, parameters: dict | None = None, items: list[dict] | None = None)"
    rules = {
        "secrets": ("Use studio.secret('K') inside node parameters; embedding it in an f-string (e.g. "
                    "f\"Bearer {studio.secret('K')}\") is fine; never transform it (.upper(), slicing, hashing, "
                    "encoding) — the broker only resolves the bare placeholder."),
        "decorators": (f"Decorator arguments must be string literals: the path positionally, then "
                       f"{', '.join(k + '=' for k in sorted(DECORATOR_KEYWORDS))} as keywords; auth is one of "
                       f"{', '.join(sorted(ROUTE_AUTHS))} (default none = public). Variables, f-strings and **kwargs are rejected."),
        "responses": "Return a dict/list (200 JSON), or Response(status=…, body=…, headers=…); raise HTTPError(status, body) for errors.",
    }
    return {"version": generate_python_sdk()["version"], "entries": entries, "rules": rules}


def catalogue() -> list[dict[str, Any]]:
    """The function catalogue: GET /api/sdk/functions, the AI agent tool and UI docs."""
    from ..engine.node_registry import node_registry

    out = []
    for t in sorted(node_registry.list()):
        if not is_exposed(t):
            continue
        node = node_registry.get(t)
        desc = node.node_description
        props = list(desc.properties) if desc else []
        out.append({
            "function": snake(t),
            "node_type": t,
            "description": (desc.description if desc else node.description) or "",
            "parameters": {
                "type": "object",
                "properties": {p.name: _json_schema(p) for p in props},
                "required": [p.name for p in props if p.required],
            },
            "python_names": {p.name: _py_name(p.name) for p in props},
            "python": _python_doc(snake(t), props),
            "returns": {"type": "array", "items": {"type": "object"}},
            "egress": egress_kind(t) != "none",
        })
    return out


def _render_nodes(cat: list[dict[str, Any]]) -> str:
    lines = [
        '"""Node functions — generated from the engine node registry. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "from ._transport import call as _call",
        "",
        "_UNSET: Any = object()",
        "",
        "",
        "def call(node: str, parameters: dict | None = None, items: list[dict] | None = None) -> list[dict]:",
        '    """Call any function-exposed node by its type name."""',
        "    return _call(node, parameters or {}, items)",
        "",
    ]
    names = []
    for f in cat:
        props = f["parameters"]["properties"]
        pyn = f["python_names"]
        args = []
        mapping = []
        for pname, schema in props.items():
            py = pyn[pname]
            ann = {"string": "str", "number": "float", "boolean": "bool", "array": "list", "object": "dict"}.get(schema.get("type"), "Any")
            args.append(f"{py}: {ann} = _UNSET")
            mapping.append(f"        {json.dumps(pname)}: {py},")
        sig = ", ".join(["*"] + args + ["items: list[dict] | None = None"]) if args else "*, items: list[dict] | None = None"
        doc = (f["description"] or f["node_type"]).replace('"""', "'")
        lines += [
            "",
            f"def {f['function']}({sig}) -> list[dict]:",
            f'    """{doc}',
            "",
            f"    Node type: {f['node_type']}. Returns the node's output items (json).",
            '    """',
            "    params = {",
            *mapping,
            "    }",
            f"    return _call({json.dumps(f['node_type'])}, {{k: v for k, v in params.items() if v is not _UNSET}}, items)",
            "",
        ]
        names.append(f["function"])
    lines += ["", f"__all__ = {json.dumps(['call'] + names)}", ""]
    return "\n".join(lines)


_INIT = '''"""studio — nodes as functions. Generated by the workflow engine (registry {version}).

    import studio
    rows = studio.nodes.http_request(method="GET", url="https://api.example.com/x",
                                     headers=[{{"name": "Authorization", "value": f"Bearer {{studio.secret('API_KEY')}}"}}])
"""

from . import nodes
from ._transport import BrokerError, configure
from .secret import SecretRef, secret

__version__ = "{version}"
__all__ = ["nodes", "secret", "SecretRef", "BrokerError", "configure"]
'''


def _optional_static() -> dict[str, str]:
    out = {}
    for name in ("db.py", "api.py"):
        p = STATIC_DIR / name
        if p.exists():
            out[name] = p.read_text()
    return out


@lru_cache
def generate_python_sdk() -> dict[str, Any]:
    """{"version": hash, "files": {"studio/__init__.py": source, ...}}"""
    cat = catalogue()
    static = {n: (STATIC_DIR / n).read_text() for n in ("_transport.py", "secret.py")}
    static.update(_optional_static())
    digest = hashlib.sha256(json.dumps([cat, sorted(static.items())], sort_keys=True, default=str).encode()).hexdigest()
    init = _INIT.format(version=digest)
    extra_imports = ""
    if "db.py" in static:
        extra_imports += "from . import db\n"
    if "api.py" in static:
        extra_imports += "from . import api\nfrom .api import HTTPError, Response\n"
    if extra_imports:
        init = init.replace("from . import nodes\n", "from . import nodes\n" + extra_imports)
        init = init.replace('__all__ = ["nodes",', '__all__ = ["nodes", "db", "api", "HTTPError", "Response",')
    files = {"studio/__init__.py": init, "studio/nodes.py": _render_nodes(cat)}
    for name, src in static.items():
        files[f"studio/{name}"] = src
    return {"version": digest, "files": files}


def sdk_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, src in sorted(generate_python_sdk()["files"].items()):
            z.writestr(path, src)
    return buf.getvalue()


@lru_cache
def sdk_dir() -> Path:
    """Write the generated package to <tmp>/studio-sdk/<version>/ (ro-bound into the sandbox)."""
    sdk = generate_python_sdk()
    root = Path(tempfile.gettempdir()) / "studio-sdk" / sdk["version"]
    for path, src in sdk["files"].items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.read_text() != src:
            p.write_text(src)
    runner_src = Path(__file__).resolve().parents[1] / "sandbox" / "runtime" / "runner.py"
    if runner_src.exists():
        (root / "runner.py").write_text(runner_src.read_text())
    return root


@lru_cache
def load_in_process():
    """Import the generated package inside the engine (legacy in-process Code node)."""
    root = sdk_dir()
    name = "_studio_sdk_" + generate_python_sdk()["version"]
    spec = importlib.util.spec_from_file_location(name, root / "studio" / "__init__.py",
                                                  submodule_search_locations=[str(root / "studio")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
