"""Canonical value text (labeling plan §4.1) for label values and for the cells the row hashes cover."""

from __future__ import annotations

import datetime as _dt
import json
import math
import unicodedata
from decimal import Decimal
from typing import Any

MAX_TEXT = 10_000


class LabelError(Exception):
    """A typed refusal: status, code, message (+ fields). The routes turn it into an HTTPException."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _num(v: Any) -> str:
    if isinstance(v, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(v, str):
        v = v.strip()
        try:
            i = int(v)
            return str(i)
        except ValueError:
            v = float(v)
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, int):
        return str(v)
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("not a finite number")
    if f.is_integer() and abs(f) < 2 ** 53:
        return str(int(f))
    return repr(f)


def canon_label(value: Any, col: dict[str, Any] | None, *, column: str) -> tuple[str | None, int]:
    """(canonical text, is_null) for a value sent by a client, validated against the schema column (None: no schema
    yet — the value is canonicalised by its JSON type). LabelError 400 on anything invalid."""
    if value is None:
        if col is not None and col.get("allow_null") is False:
            raise LabelError(400, "null_not_allowed", f"{column}: an empty value is not allowed by the schema.")
        return None, 1
    t = (col or {}).get("type")
    try:
        if t == "class":
            if col.get("multi"):
                if not isinstance(value, list):
                    raise ValueError("a multi-label value is a list of classes")
                vals = sorted({nfc(str(x)) for x in value})
                bad = [x for x in vals if x not in col.get("classes", [])]
                if bad:
                    raise LabelError(400, "unknown_class", f"{column}: {bad[0]!r} is not one of the schema's classes.",
                                     classes=col.get("classes", []))
                return json.dumps(vals, ensure_ascii=False, separators=(",", ":")), 0
            s = nfc(str(value)) if not isinstance(value, (dict, list)) else None
            if s is None or s not in [str(c) for c in col.get("classes", [])]:
                raise LabelError(400, "unknown_class", f"{column}: {str(value)[:40]!r} is not one of the schema's classes.",
                                 classes=col.get("classes", []))
            return s, 0
        if t == "number":
            s = _num(value)
            for k, op in (("min", lambda a, b: a < b), ("max", lambda a, b: a > b)):
                if col.get(k) is not None and op(float(s), float(col[k])):
                    raise LabelError(400, "out_of_range", f"{column}: {s} is outside the schema's {k} {col[k]}.")
            return s, 0
        if isinstance(value, float) or isinstance(value, int) and not isinstance(value, bool):
            return _num(value), 0
        if isinstance(value, bool):
            return ("true" if value else "false"), 0
        if isinstance(value, (dict, list)):
            raise ValueError("a value is text, a number or a list of classes")
        s = nfc(str(value))
        if len(s) > MAX_TEXT or "\x00" in s:
            raise ValueError(f"text is at most {MAX_TEXT} characters, without NUL")
        return s, 0
    except LabelError:
        raise
    except (ValueError, TypeError, OverflowError) as e:
        raise LabelError(400, "bad_value", f"{column}: {e}.") from None


def canon_cell(v: Any) -> str | None:
    """A cell of a version as canonical text (None for null), the bytes the row hashes cover."""
    if v is None:
        return None
    try:
        import numpy as np

        if isinstance(v, np.generic):
            v = v.item()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, Decimal)):
        try:
            return _num(v)
        except ValueError:
            return str(v)
    if isinstance(v, _dt.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return v.strftime("%Y-%m-%dT%H:%M:%SZ") if not v.microsecond else v.isoformat() + "Z"
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return nfc(str(v))


def canon_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# -- typed label values (LB-FIX M1) ------------------------------------------------------------------------------------

TRUE_WORDS = {"true", "t", "yes", "y", "1"}
FALSE_WORDS = {"false", "f", "no", "n", "0"}


def base_kind(type_name: str | None) -> str:
    """bool · integer · number · text: the kind of a version column's type (a lake schema type name)."""
    t = (type_name or "").upper()
    if t in ("BOOLEAN", "BOOL"):
        return "bool"
    if t in ("BIGINT", "INTEGER", "INT", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT") \
            or t.startswith("INT") or t.startswith("UINT"):
        return "integer"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL") or t.startswith("NUMERIC") or t.startswith("FLOAT"):
        return "number"
    return "text"


def cast_to_kind(text: str, kind: str) -> str:
    """The canonical text of a label value as the base column's kind would hold it: what `canon_cell` gives for the
    same value written into that column. ValueError when the value cannot be one of that kind."""
    if kind == "bool":
        w = text.strip().lower()
        if w in TRUE_WORDS:
            return "true"
        if w in FALSE_WORDS:
            return "false"
        raise ValueError(f"{text[:40]!r} is not a boolean (true/false, yes/no, 1/0)")
    if kind == "integer":
        s = _num(text)
        if "." in s or "e" in s or "inf" in s or "nan" in s:
            raise ValueError(f"{text[:40]!r} is not an integer")
        return s
    if kind == "number":
        return _num(text)
    return text


def typed_equal(label: str | None, cell: Any) -> bool:
    """A resolved label's text equals a version cell, compared in the cell's type ("no" equals false on a BOOLEAN
    column; "3" equals 3.0 on a DOUBLE column)."""
    c = canon_cell(cell)
    if label is None or c is None:
        return label is None and c is None
    if c == label:
        return True
    try:
        import numpy as np

        if isinstance(cell, np.generic):
            cell = cell.item()
    except ImportError:  # pragma: no cover
        pass
    kind = "bool" if isinstance(cell, bool) else "integer" if isinstance(cell, int) else "number" if isinstance(cell, float) else None
    if kind is None:
        return False
    try:
        return cast_to_kind(label, kind) == c
    except (ValueError, TypeError, OverflowError):
        return False
