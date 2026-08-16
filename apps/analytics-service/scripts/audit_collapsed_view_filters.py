"""Report saved views whose stored filter tree contains an empty filter group.

READ-ONLY. This script issues SELECTs and nothing else. It must stay that way —
see "Why this cannot repair anything" below.

Background
----------
``FilterGroup.conditions`` used to be typed ``list[Filter | FilterGroup]``.
Every ``FilterGroup`` field has a default, so any condition that failed to
validate as a ``Filter`` — a typo'd operator, a bad value arity — fell through
the union and matched ``FilterGroup`` instead, which pydantic then built with
``logic="and", conditions=[]``. The caller's ``column`` / ``op`` / ``value``
were discarded, the compiled WHERE clause lost that predicate, and the response
still said success.

``app.features.explorer.service.create_view`` persists
``query.model_dump()`` — the *parsed* spec — so a view saved while that bug was
live was written to ``dataset_views.query`` with the collapse already baked in.
``app.shared.query.schemas`` now rejects those conditions instead of collapsing
them, which stops new corruption but does nothing for rows already stored: they
keep returning unfiltered rows on every run, quietly.

Why this cannot repair anything
-------------------------------
The original operator is unrecoverable. All that survives in the JSON is
``{"logic": "and", "conditions": []}`` — the column, the operator and the value
were dropped before the row was written, and nothing else in the schema records
them. There is no audit copy of the pre-parse request body. So the only correct
action is to report and let a human decide, in conversation with the view's
owner, what the filter was meant to be.

The two cases are NOT reliably distinguishable
----------------------------------------------
An empty ``FilterGroup`` is legal and still means "no filtering" — call paths
build one deliberately, and a UI that always emits a group object emits exactly
this shape when the user has set no filter. A collapsed condition produces a
byte-identical node. Every "likely" verdict below is a *heuristic*, and the two
that follow are the only real signal:

* ``logic`` is not ``"and"``. A collapse always yields the default ``"and"``,
  because the malformed dict carried no ``logic`` key. An empty group with
  ``"or"`` was therefore authored on purpose — reported as DELIBERATE.
* The empty group is *nested inside another group's* ``conditions``. Authoring
  a no-op group as a sibling of real predicates is unusual; a collapse lands
  there by construction. Reported as LIKELY-COLLAPSED.

An empty group at the root of ``filters`` is genuinely ambiguous — it is both
the natural "saved with no filter" shape and what a single malformed top-level
condition collapses to. Reported as AMBIGUOUS. Do not treat any verdict as
proof; open the view and ask its owner.

    venv/bin/python -m scripts.audit_collapsed_view_filters [--json]
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory, dispose_engine

# Verdicts, most-actionable first.
LIKELY_COLLAPSED = "LIKELY-COLLAPSED"
AMBIGUOUS = "AMBIGUOUS"
DELIBERATE = "DELIBERATE"

_VERDICT_ORDER = {LIKELY_COLLAPSED: 0, AMBIGUOUS: 1, DELIBERATE: 2}

_QUERY = """
    SELECT v.id::text            AS view_id,
           v.name                AS view_name,
           v.dataset_id::text    AS dataset_id,
           d.name                AS dataset_name,
           v.created_by::text    AS owner_id,
           u.email               AS owner_email,
           v.created_at::text    AS created_at,
           v.query               AS query
      FROM dataset_views v
      JOIN datasets d ON d.id = v.dataset_id
      LEFT JOIN users u ON u.id = v.created_by
     ORDER BY v.created_at
"""


def _is_group(node: Any) -> bool:
    """A stored condition node is a group when it carries group shape.

    Mirrors ``app.shared.query.schemas._condition_kind`` on *dumped* JSON: a
    node with ``conditions`` (or with neither filter nor group keys, i.e. the
    ``{}`` no-op) is a group. Deliberately tolerant — this reads historical
    rows that predate the current model and must not raise on them.
    """
    if not isinstance(node, dict):
        return False
    if "conditions" in node or "logic" in node:
        return True
    return not ({"column", "op", "value", "case_sensitive"} & set(node))


def _empty_group_findings(node: Any, path: str, *, nested: bool) -> list[dict]:
    """Walk one filter tree, collecting every empty group with its path.

    *path* is a JSONPath-ish pointer into ``dataset_views.query`` so a human
    can find the node without guessing. *nested* says whether this node sits
    inside another group's ``conditions`` list, which is the discriminator the
    module docstring describes.
    """
    if not _is_group(node):
        return []

    conditions = node.get("conditions") or []
    if not isinstance(conditions, list):
        conditions = []

    findings: list[dict] = []
    if not conditions:
        logic = node.get("logic", "and")
        if logic != "and":
            verdict = DELIBERATE
            why = f"logic={logic!r}; a collapse always leaves the default 'and'"
        elif nested:
            verdict = LIKELY_COLLAPSED
            why = "empty group sitting inside another group's conditions list"
        else:
            verdict = AMBIGUOUS
            why = "empty group at the root of `filters` — also the shape of a view saved with no filter"
        findings.append({"path": path, "verdict": verdict, "why": why,
                         "node": node})
        return findings

    for i, child in enumerate(conditions):
        findings.extend(
            _empty_group_findings(child, f"{path}.conditions[{i}]", nested=True))
    return findings


def scan_query(query: Any) -> list[dict]:
    """Findings for one ``dataset_views.query`` value (a dumped ``QuerySpec``)."""
    if not isinstance(query, dict):
        return []
    filters = query.get("filters")
    if filters is None:
        return []  # no filter saved at all — nothing was ever there to lose
    return _empty_group_findings(filters, "query.filters", nested=False)


def _render(rows: list[dict]) -> None:
    total_views = 0
    counts = dict.fromkeys(_VERDICT_ORDER, 0)
    for row in rows:
        findings = row["findings"]
        total_views += 1
        print(f"\nview  {row['view_id']}  {row['view_name']!r}")
        print(f"  dataset    {row['dataset_name']} ({row['dataset_id']})")
        print(f"  owner      {row['owner_email'] or '(unknown)'} ({row['owner_id']})")
        print(f"  created_at {row['created_at']}")
        for f in findings:
            counts[f["verdict"]] += 1
            print(f"  {f['verdict']:<16} {f['path']}")
            print(f"                   {f['why']}")
            print(f"                   node: {json.dumps(f['node'], sort_keys=True)}")

    print(f"\n{total_views} view(s) with at least one empty filter group.")
    for verdict in sorted(counts, key=lambda v: _VERDICT_ORDER[v]):
        print(f"  {verdict:<16} {counts[verdict]}")
    if total_views:
        print(
            "\nNothing was changed. The original operator is unrecoverable, so a "
            "collapsed group cannot be repaired automatically — confirm each "
            "LIKELY-COLLAPSED and AMBIGUOUS finding with the view's owner and have "
            "them re-save the filter."
        )


async def collect() -> list[dict]:
    """Every view with at least one empty filter group. SELECT only."""
    async with async_session_factory() as s:
        rows = (await s.execute(text(_QUERY))).mappings().all()
    out = []
    for row in rows:
        findings = scan_query(row["query"])
        if findings:
            out.append({**dict(row), "findings": findings})
    # Most-actionable first: the views a human should look at before the ones
    # that are probably fine.
    out.sort(key=lambda r: (min(_VERDICT_ORDER[f["verdict"]] for f in r["findings"]),
                            r["created_at"]))
    return out


async def _main() -> int:
    try:
        rows = await collect()
    finally:
        await dispose_engine()
    if "--json" in sys.argv:
        print(json.dumps(
            [{k: v for k, v in r.items() if k != "query"} for r in rows],
            indent=2, sort_keys=True, default=str))
        return 0
    if not rows:
        print("No saved view stores an empty filter group. Nothing to review.")
        return 0
    _render(rows)
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
