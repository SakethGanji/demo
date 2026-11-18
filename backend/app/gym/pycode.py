"""JSON-like values as Python source: the resolved fit call shown in the Gym and written into exported scripts."""

from __future__ import annotations

import json
from typing import Any


def py(v: Any, indent: int = 0, width: int = 88) -> str:
    """A Python literal for a JSON-like value; short containers stay on one line, long ones break per item."""
    flat = _flat(v)
    if len(flat) + indent <= width or not isinstance(v, (dict, list, tuple)) or not v:
        return flat
    pad = " " * (indent + 4)
    if isinstance(v, dict):
        items = [f"{pad}{json.dumps(str(k), ensure_ascii=False)}: {py(x, indent + 4, width)}," for k, x in v.items()]
        return "{\n" + "\n".join(items) + "\n" + " " * indent + "}"
    items = [f"{pad}{py(x, indent + 4, width)}," for x in v]
    o, c = ("(", ")") if isinstance(v, tuple) else ("[", "]")
    return o + "\n" + "\n".join(items) + "\n" + " " * indent + c


def _flat(v: Any) -> str:
    if v is None:
        return "None"
    if v is True:
        return "True"
    if v is False:
        return "False"
    if isinstance(v, float):
        if v != v:
            return "float('nan')"
        return repr(v)
    if isinstance(v, int):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(str(k), ensure_ascii=False)}: {_flat(x)}" for k, x in v.items()) + "}"
    if isinstance(v, tuple):
        return "(" + ", ".join(_flat(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    if isinstance(v, list):
        return "[" + ", ".join(_flat(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def call(fn: str, args: list[str], kwargs: dict[str, Any], indent: int = 0) -> str:
    """`fn(arg, k=v, …)`, one argument per line when it does not fit on one."""
    parts = list(args) + [f"{k}={py(v, indent + 4)}" for k, v in kwargs.items()]
    one = f"{fn}({', '.join(parts)})"
    if len(one) + indent <= 88 and "\n" not in one:
        return one
    pad = " " * (indent + 4)
    return f"{fn}(\n" + "\n".join(f"{pad}{p}," for p in parts) + "\n" + " " * indent + ")"
