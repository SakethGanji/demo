"""P03 (plan §4.4, DLA4 #1): bounded value validation. A value going into a typed column is checked against that
column's type **before** anything is computed or written, so an out-of-range value is a 400 with a typed code, never a
500 from a cast deep in pandas / DuckDB / the CSV writer.

| type              | accepts                                                     | refuses                                     |
|-------------------|-------------------------------------------------------------|---------------------------------------------|
| BIGINT / INTEGER  | int, or a float with is_integer(), in [-2^63, 2^63-1]        | 1e30, 1e308, 2**63, 2**64, 1.5, bools,      |
|                   | (INTEGER: [-2^31, 2^31-1])                                   | NaN / ±inf                                  |
| DOUBLE            | finite floats, |x| <= 1.7976931348623157e308                | NaN, ±inf                                   |
| VARCHAR           | strings <= 32 KiB                                            | longer strings                              |
| DATE / TIMESTAMP  | ISO-8601                                                     | anything else                               |
| BOOLEAN           | true / false (and the words / 0 / 1)                         | anything else                               |

`json_safe` / `SafeJSONResponse` are the response side of the same rule: every JSON response is encoded with
`allow_nan=False`; a non-finite float becomes null and the response says `"non_finite": true`.

Codes: `value_out_of_range` and `value_invalid` ({op_index, column, type, min, max}), `non_finite_result`
({count, examples}: a formula or a column whose values or statistics overflow), `edit_invalid` ({op_index, column}:
an engine error inside an edit; the engine's text is logged, never returned).

On the CSV store the column types are the frame_io kinds: integer → BIGINT, number → DOUBLE, text → VARCHAR,
bool → BOOLEAN (`type_of_kind`)."""

from __future__ import annotations

import math
import sys
from datetime import date, datetime
from typing import Any

import numpy as np

INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1
INT32_MIN, INT32_MAX = -(2 ** 31), 2 ** 31 - 1
DOUBLE_MAX = sys.float_info.max  # 1.7976931348623157e308
VARCHAR_MAX_BYTES = 32 * 1024
TYPES = ("BIGINT", "INTEGER", "DOUBLE", "VARCHAR", "DATE", "TIMESTAMP", "BOOLEAN")
KIND_TYPES = {"integer": "BIGINT", "number": "DOUBLE", "text": "VARCHAR", "bool": "BOOLEAN"}
TRUE_WORDS = ("true", "1", "1.0", "yes")
FALSE_WORDS = ("false", "0", "0.0", "no")


class ValueRefused(ValueError):
    """A 400 with a typed body: {code, message, ...fields}. Never carries engine text."""

    status = 400

    def __init__(self, code: str, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.code = code
        self.payload = {"code": code, "message": message, **{k: v for k, v in fields.items() if v is not None}}


def type_of_kind(kind: str | None) -> str | None:
    return KIND_TYPES.get(kind or "")


def _short(v: Any) -> str:
    s = repr(v)
    return s if len(s) <= 40 else s[:37] + "…"


def _where(op_index: int | None, column: str | None) -> str:
    parts = []
    if op_index is not None:
        parts.append(f"Operation {op_index}")
    if column is not None:
        parts.append(f"'{column}'")
    return ", ".join(parts) + ": " if parts else ""


def _bounds(type_: str) -> tuple[int, int] | tuple[float, float] | tuple[None, None]:
    if type_ == "BIGINT":
        return INT64_MIN, INT64_MAX
    if type_ == "INTEGER":
        return INT32_MIN, INT32_MAX
    if type_ == "DOUBLE":
        return -DOUBLE_MAX, DOUBLE_MAX
    return None, None


def _refuse(code: str, what: str, value: Any, type_: str, op_index: int | None, column: str | None) -> ValueRefused:
    lo, hi = _bounds(type_)
    holds = f"whole numbers from {lo} to {hi}" if type_ in ("BIGINT", "INTEGER") else \
        "finite numbers" if type_ == "DOUBLE" else "values of its type"
    return ValueRefused(code, f"{_where(op_index, column)}{_short(value)} {what}; a {type_} column holds {holds}.",
                        op_index=op_index, column=column, type=type_,
                        min=None if lo is None else str(lo), max=None if hi is None else str(hi))


def _number(value: Any) -> int | float:
    """A JSON / text value as an exact int when it is written as one, else a float. Raises ValueError / TypeError."""
    if isinstance(value, (bool, np.bool_)):
        raise TypeError("bool")
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value)
    if isinstance(value, str):
        t = value.strip().replace(",", "").replace("_", "")
        if not t:
            raise ValueError("empty")
        try:
            return int(t, 10)
        except ValueError:
            return float(t)
    raise TypeError(type(value).__name__)


def check(value: Any, type_: str, *, op_index: int | None = None, column: str | None = None) -> Any:
    """The value as the column will hold it (int for BIGINT/INTEGER, float for DOUBLE, …), or ValueRefused.
    None / '' are a missing cell (allowed for every type)."""
    if type_ not in TYPES:
        raise ValueError(f"unknown type {type_!r}")
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if type_ in ("BIGINT", "INTEGER", "DOUBLE"):
        if isinstance(value, (dict, list)):
            raise _refuse("value_invalid", "is not one number", value, type_, op_index, column)
        try:
            v = _number(value)
        except OverflowError:
            raise _refuse("value_out_of_range", "is too large", value, type_, op_index, column) from None
        except (TypeError, ValueError):
            raise _refuse("value_invalid", "is not a number", value, type_, op_index, column) from None
        if isinstance(v, float) and not math.isfinite(v):
            raise _refuse("value_out_of_range" if math.isinf(v) else "value_invalid",
                          "is not a finite number", value, type_, op_index, column)
        lo, hi = _bounds(type_)
        if type_ == "DOUBLE":
            f = float(v) if not isinstance(v, int) or abs(v) <= DOUBLE_MAX else math.inf
            if not math.isfinite(f) or abs(f) > DOUBLE_MAX:
                raise _refuse("value_out_of_range", "is out of range", value, type_, op_index, column)
            return f
        if isinstance(v, float):
            if not v.is_integer():
                raise _refuse("value_invalid", "is not a whole number", value, type_, op_index, column)
            if not (lo <= v <= hi) or v >= 2.0 ** 63:  # float(2**63-1) rounds up to 2**63: out of int64
                raise _refuse("value_out_of_range", "is out of range", value, type_, op_index, column)
            v = int(v)
        if not lo <= v <= hi:
            raise _refuse("value_out_of_range", "is out of range", value, type_, op_index, column)
        return v
    if type_ == "VARCHAR":
        if isinstance(value, (dict, list)):
            raise ValueRefused("value_invalid", f"{_where(op_index, column)}a cell holds one value, not a {type(value).__name__}.",
                               op_index=op_index, column=column, type=type_)
        s = value if isinstance(value, str) else str(value)
        if len(s.encode("utf-8", "replace")) > VARCHAR_MAX_BYTES:
            raise ValueRefused("value_out_of_range", f"{_where(op_index, column)}the text is longer than {VARCHAR_MAX_BYTES // 1024} KiB.",
                               op_index=op_index, column=column, type=type_, max=str(VARCHAR_MAX_BYTES))
        return s
    if type_ == "BOOLEAN":
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        t = str(value).strip().lower()
        if t in TRUE_WORDS:
            return True
        if t in FALSE_WORDS:
            return False
        raise ValueRefused("value_invalid", f"{_where(op_index, column)}{_short(value)} is not true or false.",
                           op_index=op_index, column=column, type=type_)
    # DATE / TIMESTAMP
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    try:
        d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueRefused("value_invalid", f"{_where(op_index, column)}{_short(value)} is not an ISO-8601 "
                                            f"{'date' if type_ == 'DATE' else 'timestamp'}.",
                           op_index=op_index, column=column, type=type_) from None
    return d.date().isoformat() if type_ == "DATE" else d.isoformat()


def check_finite(values: Any, *, op_index: int | None = None, column: str | None = None, what: str = "the result") -> None:
    """`non_finite_result` when any value is ±inf / NaN-from-overflow (a formula's result, a column after an edit).
    Missing cells (NaN that the caller treats as missing) must be removed by the caller first."""
    a = np.asarray(values, dtype=float)
    bad = ~np.isfinite(a)
    if bad.any():
        idx = np.flatnonzero(bad)
        raise ValueRefused("non_finite_result", f"{_where(op_index, column)}{what} is not finite in {int(bad.sum())} row(s) "
                                                "(a number overflowed): use smaller values or another formula.",
                           op_index=op_index, column=column, count=int(bad.sum()),
                           examples=[{"row": int(i), "value": "inf" if a[i] > 0 else "-inf" if a[i] < 0 else "nan"} for i in idx[:5]])


def check_column(values: Any, *, op_index: int | None = None, column: str | None = None) -> None:
    """A number column as a whole: every value finite, and its sum and mean (what the profile, the preview and every model
    compute). 1.797e308 is a valid DOUBLE, but two of them overflow a sum, so a column
    of them is refused here with `value_out_of_range`, before anything is written."""
    a = np.asarray(values, dtype=float)
    a = a[~np.isnan(a)]
    if not a.size:
        return
    check_finite(a, op_index=op_index, column=column, what="the column")
    with np.errstate(over="ignore", invalid="ignore"):
        s = float(np.sum(a))
        m = float(np.mean(a))
    if not (math.isfinite(s) and math.isfinite(m)):
        big = np.argsort(-np.abs(a))[:5]
        raise ValueRefused("value_out_of_range", f"{_where(op_index, column)}the column's values add up to more than a number "
                                                 f"can hold (±{DOUBLE_MAX:.4g}), so its sum and mean overflow; use smaller values.",
                           op_index=op_index, column=column, type="DOUBLE", max=repr(DOUBLE_MAX),
                           examples=[{"value": repr(float(a[i]))} for i in big])


# --------------------------------------------------------------------------- JSON responses


def json_safe(obj: Any) -> tuple[Any, bool]:
    """(obj with every NaN / ±inf float replaced by None, whether any was). numpy scalars become Python numbers."""
    found = False

    def walk(v: Any) -> Any:
        nonlocal found
        if isinstance(v, bool) or v is None or isinstance(v, (str, int)):
            return v
        if isinstance(v, (float, np.floating)):
            f = float(v)
            if math.isfinite(f):
                return f
            found = True
            return None
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if isinstance(v, np.ndarray):
            return [walk(x) for x in v.tolist()]
        return v

    out = walk(obj)
    return out, found


def dumps(obj: Any) -> bytes:
    """The one JSON encoder for responses: non-finite → null (+ "non_finite": true on a top-level object),
    `allow_nan=False` so an Infinity token can never be sent."""
    import json

    out, found = json_safe(obj)
    if found and isinstance(out, dict) and "non_finite" not in out:
        out = {**out, "non_finite": True}
    return json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")


try:
    from fastapi.responses import JSONResponse as _JSONResponse

    class SafeJSONResponse(_JSONResponse):
        """FastAPI's JSONResponse through `dumps` (the app's default response class)."""

        def render(self, content: Any) -> bytes:
            return dumps(content)
except ImportError:  # pragma: no cover - the worker process imports this module without the web stack
    SafeJSONResponse = None  # type: ignore[assignment,misc]
