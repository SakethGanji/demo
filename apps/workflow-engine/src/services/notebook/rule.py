"""Rule cells: deterministic checks over row sets, pure Python (no AI, no I/O).

``op``:
- ``filter``: the rows of ``left`` that match every ``where`` condition.
- ``count``: how many rows of ``left`` match (``where`` may be empty).
- ``assert``: every row of ``left`` must match every ``where`` condition; the violating rows make it fail.
- ``compare``: join ``left`` and ``right`` on ``key``; every ``fields`` pair that differs (beyond ``tolerance``) is a
  mismatch row ``{key, field, left, right}``; rows only on one side are mismatches too (``field: "(missing)"``).

Also used by guards (``guard_holds``).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

_DUR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|"
                     r"d|day|days|w|week|weeks)?\s*$", re.I | re.ASCII)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class RuleError(Exception):
    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


def duration_seconds(v: Any) -> float:
    """``"1 day"``, ``"2 days"``, ``"3h"``, ``"90 min"``, or a number of seconds."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = _DUR_RE.match(str(v or ""))
    if not m:
        raise RuleError(f"'{v}' is not a duration (try '1 day', '3 hours', '30 min')")
    unit = (m.group(2) or "s").lower()
    unit = "m" if unit.startswith("mi") or unit == "m" else unit[0]
    return float(m.group(1)) * _UNIT[unit]


def _as_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    elif isinstance(v, str) and v:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=None)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _cmp(a: Any, b: Any) -> tuple[Any, Any]:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na, nb
    da, db = _as_dt(a), _as_dt(b)
    if da is not None and db is not None and (da.tzinfo is None) == (db.tzinfo is None):
        return da, db
    return ("" if a is None else str(a)), ("" if b is None else str(b))


def compare(op: str, a: Any, b: Any, tolerance: float | None = None) -> bool:
    if op == "approx":
        na, nb = _num(a), _num(b)
        if na is None or nb is None:
            return a == b
        return abs(na - nb) <= (tolerance or 0)
    if op in ("eq", "ne"):
        na, nb = _num(a), _num(b)
        same = (abs(na - nb) <= (tolerance or 0)) if (na is not None and nb is not None) else (a == b or (
            a is not None and b is not None and str(a) == str(b)))
        return same if op == "eq" else not same
    if a is None or b is None:
        return False
    x, y = _cmp(a, b)
    try:
        return {"lt": x < y, "le": x <= y, "gt": x > y, "ge": x >= y}[op]
    except TypeError:
        return False


def _get(row: Any, field: str) -> Any:
    cur = row
    for part in str(field).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def matches(row: Any, cond: dict[str, Any], now: datetime | None = None) -> bool:
    field, op, value = cond.get("field"), cond.get("op"), cond.get("value")
    v = _get(row, field)
    if op == "is_null":
        return v is None
    if op == "not_null":
        return v is not None
    if op == "in":
        return v in (value or []) or str(v) in [str(x) for x in (value or [])]
    if op == "not_in":
        return not (v in (value or []) or str(v) in [str(x) for x in (value or [])])
    if op == "contains":
        if isinstance(v, list):
            return value in v
        return v is not None and str(value).lower() in str(v).lower()
    if op == "older_than":
        dt = _as_dt(v)
        if dt is None:
            return False
        ref = now or (datetime.now(timezone.utc) if dt.tzinfo else datetime.now())
        return (ref - dt) > timedelta(seconds=duration_seconds(value))
    return compare(op, v, value)


def _rows(v: Any, side: str) -> list[dict[str, Any]]:
    if v is None:
        return []
    if isinstance(v, dict) and isinstance(v.get("rows"), list):
        v = v["rows"]
    if not isinstance(v, list):
        raise RuleError(f"'{side}' must be a list of rows (e.g. {{{{cells.c2.rows}}}})")
    return [r if isinstance(r, dict) else {"value": r} for r in v]


def _keys(key: Any) -> list[str]:
    if key is None:
        return []
    return [key] if isinstance(key, str) else [str(k) for k in key]


def evaluate(spec: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Run a resolved rule spec. Returns ``{rows, columns, passed, summary, ...}``; ``passed`` False means the rule
    failed (only ``assert`` fails; ``compare`` reports mismatches as rows and passes when there are none)."""
    op = spec.get("op")
    left = _rows(spec.get("left"), "left")
    where = spec.get("where") or []
    if op == "filter":
        out = [r for r in left if all(matches(r, c, now) for c in where)]
        return {"rows": out, "passed": True, "summary": f"{len(out)} of {len(left)} rows match"}
    if op == "count":
        n = sum(1 for r in left if all(matches(r, c, now) for c in where))
        return {"rows": [], "count": n, "passed": True, "summary": f"{n} of {len(left)} rows match"}
    if op == "assert":
        bad = [r for r in left if not all(matches(r, c, now) for c in where)]
        return {"rows": bad, "passed": not bad,
                "summary": "all rows pass" if not bad else f"{len(bad)} of {len(left)} rows break the rule"}
    if op == "compare":
        right = _rows(spec.get("right"), "right")
        keys = _keys(spec.get("key"))
        if not keys:
            raise RuleError("compare needs a key (the column that pairs left and right rows)")
        fields = spec.get("fields") or []

        def k(r: dict[str, Any]) -> tuple:
            return tuple(str(_get(r, x)) for x in keys)

        rmap: dict[tuple, dict[str, Any]] = {}
        for r in right:
            rmap.setdefault(k(r), r)
        lkeys = set()
        mism: list[dict[str, Any]] = []
        matched = 0
        for lr in left:
            kk = k(lr)
            lkeys.add(kk)
            keyval = dict(zip(keys, [_get(lr, x) for x in keys]))
            rr = rmap.get(kk)
            if rr is None:
                mism.append({**keyval, "field": "(missing)", "left": "present", "right": None})
                continue
            bad = False
            for f in fields:
                a, b = _get(lr, f.get("left")), _get(rr, f.get("right"))
                if not compare(f.get("op") or "eq", a, b, f.get("tolerance")):
                    bad = True
                    mism.append({**keyval, "field": f.get("left"), "left": a, "right": b})
            if not bad:
                matched += 1
        for kk, rr in rmap.items():
            if kk not in lkeys:
                mism.append({**dict(zip(keys, [_get(rr, x) for x in keys])), "field": "(missing)", "left": None,
                             "right": "present"})
        return {"rows": mism, "passed": True, "matched": matched, "mismatches": len(mism),
                "columns": [*keys, "field", "left", "right"],
                "summary": "no mismatches" if not mism else f"{len(mism)} mismatch{'es' if len(mism) != 1 else ''}"}
    raise RuleError(f"unknown rule op {op!r}")


def guard_holds(left: Any, op: str, right: Any) -> bool:
    """A cell guard: ``nonempty`` / ``empty`` (lists, dicts, strings, numbers: 0 is empty) or a comparison."""
    if op in ("nonempty", "empty"):
        if isinstance(left, dict) and "rows" in left and isinstance(left["rows"], list):
            left = left["rows"]
        if isinstance(left, (list, dict, str)):
            empty = len(left) == 0
        elif isinstance(left, (int, float)) and not isinstance(left, bool):
            empty = left == 0
        else:
            empty = left is None or left is False
        return empty if op == "empty" else not empty
    return compare(op, left, right)
