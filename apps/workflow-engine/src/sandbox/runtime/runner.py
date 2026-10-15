"""Runs INSIDE the sandbox (bwrap). Reads /run/job/in/input.json, writes /run/job/out/out.json.

kinds:
  route — call ``module:function(req)``; return value / studio.Response / studio.HTTPError → HTTP result
  step  — call ``module:function(items)`` → items
  code  — legacy Code-node body (same globals as nodes/data/code.py), full builtins, isolated
"""

import asyncio
import json
import sys
import traceback

IN, OUT = "/run/job/in", "/run/job/out"
sys.path[:0] = [IN + "/modules", "/opt/studio"]
sys.path.append("/opt/site")


def _write(obj):
    with open(OUT + "/out.json", "w") as f:
        json.dump(obj, f, default=str)


def _await(v):
    if asyncio.iscoroutine(v):
        return asyncio.run(v)
    return v


def _load(entry):
    import importlib
    mod_name, fn_name = entry.split(":", 1)
    return getattr(importlib.import_module(mod_name), fn_name)


def _run_code(payload):
    import base64
    import io
    import math
    import random
    import re
    from datetime import datetime, timedelta

    import studio

    items = payload.get("items") or []
    logs = []

    def log(*args):
        if len(logs) < 100:
            logs.append([str(a) if not isinstance(a, (int, float, bool, type(None))) else a for a in args])
        print("[Code Node]", *args)

    g = {
        "items": items, "json_data": items[0]["json"] if items else {}, "input_data": items,
        "node_data": payload.get("node_data") or {}, "execution": payload.get("execution") or {},
        "get_item": lambda i: items[i] if 0 <= i < len(items) else None,
        "new_item": lambda d: {"json": d}, "log": log,
        "json": json, "math": math, "re": re, "random": random, "base64": base64, "io": io,
        "datetime": datetime, "timedelta": timedelta, "studio": studio,
    }
    g.update(payload.get("globals") or {})
    code = payload.get("code") or "return items"
    if "pd." in code or "pandas" in code:
        try:
            import pandas as pd
            g["pd"] = g["pandas"] = pd
        except ImportError:
            pass
    if "\\n" in code:
        code = code.replace("\\n", "\n").replace("\\t", "\t")
    body = "\n".join(("    " + line) if line.strip() else "" for line in code.split("\n"))
    src = "def __user_code__():\n" + (body if body.strip() else "    return items") + "\n"
    try:
        compiled = compile(src, "<code>", "exec")
    except SyntaxError as e:
        # The user's code is wrapped in `def __user_code__():` (one extra line) — report the user's line.
        line = max(1, (e.lineno or 2) - 1)
        err = SyntaxError(f"Line {line}: {e.msg}")
        err.user_line = line
        raise err from None
    exec(compiled, g)
    return {"result": g["__user_code__"](), "logs": logs}


def main():
    cfg = json.load(open(IN + "/input.json"))
    import studio
    from studio import _transport

    if cfg.get("token"):
        _transport.configure(_transport.UnixSocketTransport("/run/broker/broker.sock", cfg["token"]))
    kind, payload = cfg["kind"], cfg.get("payload") or {}
    try:
        if kind == "route":
            from studio.api import HTTPError, Request, Response
            fn = _load(cfg["entry"])
            try:
                rv = _await(fn(Request(payload.get("req") or {})))
            except HTTPError as e:
                _write({"ok": True, "result": {"status": e.status, "headers": e.headers, "body": e.body}})
                return
            if isinstance(rv, Response):
                _write({"ok": True, "result": {"status": rv.status, "headers": rv.headers, "body": rv.body}})
            else:
                _write({"ok": True, "result": {"status": 200, "headers": {}, "body": rv}})
        elif kind == "step":
            fn = _load(cfg["entry"])
            _write({"ok": True, "result": _await(fn(payload.get("items") or []))})
        elif kind == "code":
            out = _run_code(payload)
            _write({"ok": True, "result": out["result"], "logs": out["logs"]})
        else:
            _write({"ok": False, "error": {"type": "ValueError", "message": f"unknown kind {kind}"}})
    except _transport.BrokerError as e:
        _write({"ok": False, "error": {"type": "BrokerError", "code": e.code, "message": str(e), "details": e.details,
                                       "traceback": traceback.format_exc()[-4000:]}})
    except BaseException as e:  # noqa: BLE001 - report everything back to the engine
        tb = traceback.format_exc()
        line = getattr(e, "user_line", None)
        if line is None and kind == "code":
            import re
            frames = re.findall(r'File "<code>", line (\d+)', tb)
            line = max(1, int(frames[-1]) - 1) if frames else None
        _write({"ok": False, "error": {"type": type(e).__name__, "message": str(e),
                                       "traceback": tb[-4000:], "line": line}})


if __name__ == "__main__":
    main()
