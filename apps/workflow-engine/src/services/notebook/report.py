"""The notebook report: deterministic markdown over each cell's latest result (no AI).

Pure standard-library code with no package imports, so Keep can embed this file's source in a kept ``report`` workflow
and the scheduled report reads exactly like ``GET /notebooks/{id}/report``.

``render(title, cells, run_n=None, generated_at=None)``; each cell is
``{name, kind, title, status, output, error, markdown?, children?}`` (``status`` None = not run).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

MAX_TABLE_ROWS = 10
MAX_TABLE_COLS = 8
_STATUS = {
    None: "not run", "success": "done", "error": "failed", "skipped_ask": "needs AI (skipped)",
    "guard_false": "skipped (condition false)", "kept_earlier": "kept the earlier result",
    "needs_approval": "waiting for approval", "waiting": "waiting", "applied": "applied", "discarded": "discarded",
    "cancelled": "cancelled",
}


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= 60 else s[:57] + "..."


def _table(rows: list[Any], columns: list[str] | None) -> list[str]:
    rows = [r if isinstance(r, dict) else {"value": r} for r in rows]
    cols = list(columns or [])
    if not cols:
        for r in rows[:50]:
            for k in r:
                if k not in cols:
                    cols.append(k)
    cols = cols[:MAX_TABLE_COLS]
    if not cols:
        return []
    out = ["| " + " | ".join(_cell_text(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:MAX_TABLE_ROWS]:
        out.append("| " + " | ".join(_cell_text(r.get(c)) for c in cols) + " |")
    return out


def _body(c: dict[str, Any]) -> list[str]:
    kind, out, err = c.get("kind"), c.get("output") or {}, c.get("error") or {}
    lines: list[str] = []
    if kind == "note":
        md = c.get("markdown") or ""
        return [md] if md else []
    lines.append(f"Status: {_STATUS.get(c.get('status'), str(c.get('status')))}")
    if err:
        lines.append(f"Error `{err.get('code')}`: {err.get('message')}")
    if not isinstance(out, dict):
        return lines
    if out.get("summary"):
        lines.append(f"Result: {out['summary']}")
    if kind == "for_each" and ("ok" in out or "failed" in out):
        lines.append(f"Rows: {out.get('ok', 0)} ok, {out.get('failed', 0)} failed")
    if kind == "http" and out.get("status") is not None:
        lines.append(f"HTTP {out.get('status')}")
    if kind == "browser" and out.get("step_count") is not None:
        lines.append(f"Steps: {out.get('passed', 0)} of {out.get('step_count')} passed")
    if kind == "http" and isinstance(out.get("assertions"), list) and out["assertions"]:
        a = out["assertions"]
        lines.append(f"Checks: {sum(1 for x in a if x.get('passed'))} of {len(a)} passed")
        lines.extend(f"- {'pass' if x.get('passed') else 'FAIL'}: {x.get('label')}"
                     + ("" if x.get("passed") else f" (got {x.get('actual')!r})") for x in a)
    rows = out.get("rows")
    if isinstance(rows, list):
        n = out.get("row_count", len(rows))
        if kind not in ("for_each", "browser"):
            lines.append(f"{n} row{'s' if n != 1 else ''}" + (" (capped)" if out.get("truncated") else ""))
        if rows and kind not in ("for_each", "browser"):
            lines.append("")
            lines.extend(_table(rows, out.get("columns")))
            if n > MAX_TABLE_ROWS:
                lines.append(f"\n_First {MAX_TABLE_ROWS} of {n} rows._")
    elif "count" in out and kind == "rule":
        lines.append(f"Count: {out['count']}")
    return lines


def render(title: str, cells: list[dict[str, Any]], run_n: int | None = None,
           generated_at: str | None = None) -> str:
    when = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    head = [f"# {title}", "", f"_Report generated {when}" + (f" from run {run_n}" if run_n else "") + "._", ""]
    ran = [c for c in cells if c.get("kind") != "note" and c.get("status") is not None]
    failed = [c for c in ran if c.get("status") == "error"]
    if cells:
        head.append(f"{len(ran)} of {len([c for c in cells if c.get('kind') != 'note'])} cells have a result"
                    + (f"; {len(failed)} failed ({', '.join(c['name'] for c in failed)})." if failed else "."))
        head.append("")
    parts = head
    for c in cells:
        label = c.get("title") or c.get("kind")
        parts.append(f"## {c.get('name')} · {label}" if c.get("kind") != "note" else f"## {c.get('title') or 'Note'}")
        parts.extend(_body(c))
        for ch in c.get("children") or []:
            parts.append(f"- {ch.get('name')} · {ch.get('title') or ch.get('kind')}: "
                         f"{_STATUS.get(ch.get('status'), str(ch.get('status')))}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def cells_for_report(cells: list[Any], latest: dict[str, Any]) -> list[dict[str, Any]]:
    """Cell models + ``{cell_id: result}`` → the ``render`` input (top level, children nested)."""
    top = sorted([c for c in cells if c.parent_cell_id is None], key=lambda c: c.position)

    def one(c: Any) -> dict[str, Any]:
        r = latest.get(c.id)
        return {"name": c.name, "kind": c.kind, "title": c.title, "markdown": (c.spec or {}).get("markdown"),
                "status": getattr(r, "status", None), "output": getattr(r, "output", None),
                "error": getattr(r, "error", None)}

    out = []
    for c in top:
        d = one(c)
        d["children"] = [one(x) for x in sorted([x for x in cells if x.parent_cell_id == c.id], key=lambda x: x.position)]
        out.append(d)
    return out


__all__ = ["cells_for_report", "render"]
