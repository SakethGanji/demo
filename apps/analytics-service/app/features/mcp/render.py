"""Compact text rendering.

Every tool returns text rather than JSON. For the same content a pipe-delimited
table costs roughly half the tokens of pretty-printed JSON, and the point of
this tool surface is to spend as few tokens as possible describing data.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

MAX_CELL = 80


def scalar(value: Any, limit: int | None = MAX_CELL) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.6g}"
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("|", "/")
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def fields(pairs: Iterable[tuple[str, Any]]) -> str:
    """One `key: value` per line, skipping empties.

    Not truncated: these carry prose like health summaries, where the tail is
    usually the actionable part.
    """
    lines = [f"{k}: {scalar(v, limit=None)}" for k, v in pairs if v not in (None, "", [], {})]
    return "\n".join(lines)


def table(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str] | None = None,
    *,
    full: Sequence[str] = (),
) -> str:
    """Pipe-delimited table with a header row.

    Cells are capped at :data:`MAX_CELL` characters, which is right for data
    rows — a sampled value read at 80 characters is still recognisable. It is
    wrong for stored prose and for closed sets: a column description cut at 79
    characters loses the qualification at its end, and an ``allowed_values``
    list cut mid-enum reads as a complete enumeration, which is how a model
    comes to filter on a vocabulary that is missing its last few members. Name
    those columns in ``full`` and they are rendered whole.
    """
    if not rows:
        return "(no rows)"
    if columns is None:
        seen: dict[str, None] = {}
        for row in rows:
            for key in row:
                seen[key] = None
        columns = list(seen)
    uncapped = set(full)
    out = [" | ".join(columns)]
    out += [
        " | ".join(
            scalar(row.get(col), limit=None if col in uncapped else MAX_CELL)
            for col in columns
        )
        for row in rows
    ]
    return "\n".join(out)


def bullets(items: Iterable[str]) -> str:
    listed = [f"- {item}" for item in items]
    return "\n".join(listed) if listed else "(none)"


def section(title: str, body: str) -> str:
    return f"## {title}\n{body}" if body else ""


def join(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part and part.strip())


def count_note(shown: int, total: Any, *, noun: str = "rows") -> str:
    if total is None:
        return f"{shown} {noun} shown."
    if isinstance(total, int) and total > shown:
        return f"{shown} of {total} {noun} shown."
    return f"{shown} {noun} shown."
