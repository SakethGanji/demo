"""Value generators per data category (Stage 3A, CONTRACTS-STAGE3A §7).

``values_for(shape, field="value") -> {Category: [value]}`` gives, for one field shape, concrete values for
each data category the grid knows about:

* ``normal``: a typical value that respects the enum / format / limits;
* ``boundary``: the edges of the limits (``minimum``/``maximum``/``minLength``/``maxLength``), just outside
  them, and ``0`` for numbers;
* ``missing``: ``ABSENT`` (the field isn't there at all), ``None`` and ``""``;
* ``wrong_type``: values of another type;
* ``unusual``: very large numbers, long strings, unicode, emoji, special characters, duplicate-ish lists.

Pure and deterministic: the same shape always gives the same lists. ``ABSENT`` is a marker, not JSON: a
caller that builds an item leaves the key out when it sees it (``is_absent``).
"""

from __future__ import annotations

from typing import Any

from ..services.mock_gen import example_from_shape
from .shapes import Shape

CATEGORIES = ("normal", "boundary", "missing", "wrong_type", "unusual")


class _Absent:
    """Marker for "the field is not in the item"."""

    _instance: "_Absent | None" = None

    def __new__(cls) -> "_Absent":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "ABSENT"

    def __deepcopy__(self, memo: dict) -> "_Absent":
        return self

    def __copy__(self) -> "_Absent":
        return self


ABSENT = _Absent()


def is_absent(value: Any) -> bool:
    return value is ABSENT


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dedupe(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if not any(type(v) is type(o) and v == o for o in out):
            out.append(v)
    return out


def _round(v: float) -> float:
    return round(v, 10)


def _numeric(shape: Shape, integer: bool) -> dict[str, list[Any]]:
    lo, hi = shape.get("minimum"), shape.get("maximum")
    lo = lo if _num(lo) else None
    hi = hi if _num(hi) else None
    step: Any = 1 if integer else 0.01
    cast = (lambda v: int(v)) if integer else _round
    normal = example_from_shape(shape)
    if not _num(normal):
        normal = 1
    boundary: list[Any] = []
    if lo is not None:
        boundary += [cast(lo), cast(lo - step)]
    if hi is not None:
        boundary += [cast(hi), cast(hi + step)]
    if lo is None or lo <= 0 <= (hi if hi is not None else 0):
        boundary.append(0 if integer else 0.0)
    if lo is None and hi is None:
        boundary += [-1 if integer else -0.01, 1 if integer else 0.01]
    big = 2 ** 53 if integer else 1e15
    unusual = [big, -big, 10 ** 12 if integer else 1.0000000001]
    if hi is not None:
        unusual = [x for x in unusual if x != hi] + [cast(hi * 1000 if hi else big)]
    wrong = ["abc", "12", True, [], {}]
    if integer:
        wrong.insert(0, 1.5)
    return {
        "normal": [normal], "boundary": _dedupe(boundary), "missing": [ABSENT, None, ""],
        "wrong_type": wrong, "unusual": _dedupe(unusual),
    }


def _string(shape: Shape, field: str) -> dict[str, list[Any]]:
    lo, hi = shape.get("minLength"), shape.get("maxLength")
    lo = lo if isinstance(lo, int) and not isinstance(lo, bool) and lo >= 0 else None
    hi = hi if isinstance(hi, int) and not isinstance(hi, bool) and hi >= 0 else None
    enum = shape.get("enum") if isinstance(shape.get("enum"), list) else None
    normal = example_from_shape(shape, 1, field)
    if not isinstance(normal, str):
        normal = f"{field}_example"
    boundary: list[Any] = []
    if enum:
        boundary += [enum[0], enum[-1]]
    if lo is not None:
        boundary.append("x" * lo)
        if lo > 0:
            boundary.append("x" * (lo - 1))
    if hi is not None:
        boundary += ["x" * hi, "x" * (hi + 1)]
    if not boundary:
        boundary.append("a")
    unusual: list[Any] = ["héllo wörld 日本語", "emoji 😀🚀", "<script>alert(1)</script> ' \" ; -- \\ %s {{ }}", "  padded  ", "line1\nline2\ttab"]
    longest = hi + 1 if hi is not None else 1000
    unusual.append("a" * min(longest, 5000))
    if enum:
        unusual.append(str(enum[0]).upper() if str(enum[0]).upper() != enum[0] else str(enum[0]) + " ")
    return {
        "normal": [normal], "boundary": _dedupe(boundary), "missing": [ABSENT, None, ""],
        "wrong_type": [12345, True, ["x"], {"x": 1}], "unusual": _dedupe(unusual),
    }


def _boolean() -> dict[str, list[Any]]:
    return {
        "normal": [True, False], "boundary": [], "missing": [ABSENT, None, ""],
        "wrong_type": ["true", 1, "yes", []], "unusual": ["TRUE", 0.5],
    }


def _array(shape: Shape, field: str) -> dict[str, list[Any]]:
    item = example_from_shape(shape.get("items"), 1, field) if isinstance(shape.get("items"), dict) else "item"
    return {
        "normal": [[item]], "boundary": [[], [item]], "missing": [ABSENT, None, ""],
        "wrong_type": ["text", 5, {}, True], "unusual": [[item, item], [item] * 1000, [None]],
    }


def _object(shape: Shape, field: str) -> dict[str, list[Any]]:
    obj = example_from_shape(shape, 1, field)
    if not isinstance(obj, dict):
        obj = {}
    extra = {**obj, "unexpected_key": "extra", "unicodé": "日本語"}
    return {
        "normal": [obj], "boundary": [{}], "missing": [ABSENT, None, ""],
        "wrong_type": ["text", 5, [], True], "unusual": [extra, {"nested": {"a": {"b": {"c": [1, 2, 3]}}}}],
    }


def values_for(shape: Shape | None, field: str = "value") -> dict[str, list[Any]]:
    """Values per category for one field ``shape`` (unknown shapes get string-ish defaults)."""
    t = shape.get("type") if isinstance(shape, dict) else "unknown"
    s: Shape = shape if isinstance(shape, dict) else {"type": "unknown"}
    if t == "integer":
        return _numeric(s, True)
    if t == "number":
        return _numeric(s, False)
    if t == "string":
        return _string(s, field)
    if t == "boolean":
        return _boolean()
    if t == "array":
        return _array(s, field)
    if t == "object":
        return _object(s, field)
    out = _string({}, field)  # unknown / null: no assumption about the type, so no "wrong" type either
    out["wrong_type"] = []
    return out
