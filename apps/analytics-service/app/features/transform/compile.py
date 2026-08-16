"""Compile a transformation pipeline to one DuckDB statement (ROADMAP §19).

Each step becomes one CTE reading from the previous one::

    WITH src    AS (SELECT * FROM df),
         step_0 AS (SELECT ... FROM src),
         step_1 AS (SELECT ... FROM step_0)
    SELECT * FROM step_1

Alongside the SQL the compiler folds a **running schema**: the column list as
each step leaves it. Every column reference is resolved against that folded
schema, so a filter on a column an earlier step dropped fails at compile time
with ``unknown-column`` (listing what *is* available) rather than as a DuckDB
error at execution.

Safety invariant, same as :mod:`.expr`: identifiers go through
``quote_ident``, every user-supplied value becomes a ``?`` bind, and the only
tokens interpolated into SQL come from whitelists in this package. Binds are
appended in the order their placeholders appear in the final statement.
"""

from __future__ import annotations

import re
from typing import Callable

from app.api.errors import ProblemException
from app.shared.query.schemas import Sort
from app.shared.utils.sql import quote_ident

from . import steps as S
from .expr import (
    CAST_TYPES,
    UNKNOWN,
    _compile_condition,
    compile_expr,
    resolve_column,
)

# Emitter for one output column: appends its binds and returns its SQL.
Emitter = Callable[[], str]

_TRIM_FNS = {"both": "TRIM", "left": "LTRIM", "right": "RTRIM"}
_CASE_FNS = {"lower": "LOWER", "upper": "UPPER", "title": "INITCAP"}
_TEXT_PREFIXES = ("VARCHAR", "CHAR", "TEXT", "STRING")


def normalize_name(name: str) -> str:
    """Normalized form of a new column name (same rule as ingest)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "column"


def initial_columns(schema_json: list[dict]) -> list[dict]:
    """The running schema at the head of a pipeline, from a sheet's schema_json."""
    return [
        {
            "name": c["name"],
            "normalized_name": c.get("normalized_name") or c["name"],
            "dtype": c.get("dtype") or UNKNOWN,
            "position": i,
        }
        for i, c in enumerate(sorted(schema_json, key=lambda c: c.get("position", 0)))
    ]


def _renumber(cols: list[dict]) -> list[dict]:
    return [{**c, "position": i} for i, c in enumerate(cols)]


def _new_column(name: str, dtype: str) -> dict:
    return {"name": name, "normalized_name": normalize_name(name),
            "dtype": dtype, "position": -1}


def _is_text(dtype: str) -> bool:
    return dtype == UNKNOWN or dtype.upper().startswith(_TEXT_PREFIXES)


def _require_text(col: dict, what: str) -> None:
    if not _is_text(col.get("dtype") or UNKNOWN):
        raise ProblemException(
            400, f"Step '{what}' requires a text column; '{col['name']}' is {col['dtype']}",
            code="operator-type-mismatch", column=col["name"], dtype=col["dtype"])


def _resolve_many(names: list[str], cols: list[dict], *, what: str) -> list[dict]:
    """Resolve column references, rejecting a repeated reference."""
    rows = [resolve_column(n, cols) for n in names]
    seen: set[str] = set()
    for r in rows:
        if r["name"] in seen:
            raise ProblemException(
                400, f"Step '{what}' names column '{r['name']}' more than once",
                code="duplicate-column", column=r["name"])
        seen.add(r["name"])
    return rows


def _emit_select(cols: list[dict], relation: str, *,
                 replace: dict[str, Emitter] | None = None,
                 append: list[tuple[str, Emitter]] | None = None,
                 drop: set[str] | None = None) -> str:
    """Build ``SELECT <list> FROM relation``.

    Walks *cols* in order so that emitters — which append their binds when
    called — run in the same order their placeholders appear in the SQL, then
    emits appended columns last. *replace*/*drop* are keyed by physical name.
    """
    parts: list[str] = []
    for col in cols:
        if drop and col["name"] in drop:
            continue
        ref = quote_ident(col["name"])
        emitter = (replace or {}).get(col["name"])
        parts.append(f"{emitter()} AS {ref}" if emitter else ref)
    for out_name, emitter in append or []:
        parts.append(f"{emitter()} AS {quote_ident(out_name)}")
    return f"SELECT {', '.join(parts)} FROM {relation}"


def _order_terms(sorts: list[Sort], cols: list[dict], *, invert: bool = False) -> str:
    terms = []
    for s in sorts:
        col = resolve_column(s.column, cols)
        ascending = (s.direction == "asc") != invert
        terms.append(f"{quote_ident(col['name'])} {'ASC' if ascending else 'DESC'}")
    return ", ".join(terms)


def compile_step(step, in_cols: list[dict], binds: list,
                 relation: str) -> tuple[str, list[dict]]:
    """Compile one step over *relation*; returns (CTE body, folded columns).

    Pure — appends to *binds* and returns SQL, touching no connection.
    """
    # --- projection steps -----------------------------------------------------
    if isinstance(step, S.SelectStep):
        rows = _resolve_many(step.columns, in_cols, what="select")
        select_list = ", ".join(quote_ident(r["name"]) for r in rows)
        return f"SELECT {select_list} FROM {relation}", _renumber(rows)

    if isinstance(step, S.DropStep):
        rows = _resolve_many(step.columns, in_cols, what="drop")
        dropped = {r["name"] for r in rows}
        kept = [c for c in in_cols if c["name"] not in dropped]
        if not kept:
            raise ProblemException(
                400, "Step 'drop' would remove every column",
                code="empty-projection")
        return _emit_select(kept, relation), _renumber(kept)

    if isinstance(step, S.RenameStep):
        mapping: dict[str, str] = {}
        for old, new in step.renames.items():
            mapping[resolve_column(old, in_cols)["name"]] = new
        out = [
            {**c, "name": mapping[c["name"]],
             "normalized_name": normalize_name(mapping[c["name"]])}
            if c["name"] in mapping else c
            for c in in_cols
        ]
        names = [c["name"] for c in out]
        clashes = {n for n in names if names.count(n) > 1}
        if clashes:
            raise ProblemException(
                400, f"Step 'rename' produces duplicate column names: {sorted(clashes)}",
                code="duplicate-column", columns=sorted(clashes))
        select_list = ", ".join(
            f"{quote_ident(old)} AS {quote_ident(mapping[old])}" if old in mapping
            else quote_ident(old)
            for old in (c["name"] for c in in_cols)
        )
        return f"SELECT {select_list} FROM {relation}", _renumber(out)

    if isinstance(step, S.ReorderStep):
        front = _resolve_many(step.columns, in_cols, what="reorder")
        front_names = {r["name"] for r in front}
        out = [*front, *[c for c in in_cols if c["name"] not in front_names]]
        return _emit_select(out, relation), _renumber(out)

    # --- in-place value steps -------------------------------------------------
    if isinstance(step, S.CastStep):
        col = resolve_column(step.column, in_cols)
        target = CAST_TYPES[step.to]
        ref = quote_ident(col["name"])
        sql = _emit_select(in_cols, relation,
                           replace={col["name"]: lambda: f"CAST({ref} AS {target})"})
        return sql, [{**c, "dtype": target} if c["name"] == col["name"] else c
                     for c in in_cols]

    if isinstance(step, S.TrimStep):
        rows = _resolve_many(step.columns, in_cols, what="trim")
        for r in rows:
            _require_text(r, "trim")
        fn = _TRIM_FNS[step.mode]
        targets = {r["name"] for r in rows}
        replace = {
            name: (lambda ref=quote_ident(name): f"{fn}({ref})") for name in targets
        }
        return _emit_select(in_cols, relation, replace=replace), in_cols

    if isinstance(step, S.CaseNormalizeStep):
        rows = _resolve_many(step.columns, in_cols, what="case_normalize")
        for r in rows:
            _require_text(r, "case_normalize")
        fn = _CASE_FNS[step.mode]
        replace = {
            r["name"]: (lambda ref=quote_ident(r["name"]): f"{fn}({ref})") for r in rows
        }
        return _emit_select(in_cols, relation, replace=replace), in_cols

    if isinstance(step, S.ReplaceStep):
        col = resolve_column(step.column, in_cols)
        _require_text(col, "replace")
        ref = quote_ident(col["name"])

        def emit_replace() -> str:
            inner = ref
            if step.find is not None:
                if step.mode == "exact":
                    binds.extend([step.find, step.replace_with])
                    inner = f"CASE WHEN {ref} = ? THEN ? ELSE {ref} END"
                elif step.mode == "substring":
                    binds.extend([step.find, step.replace_with])
                    inner = f"REPLACE({ref}, ?, ?)"
                else:  # regex — the 'g' flag is a fixed token, the pattern is a bind
                    binds.extend([step.find, step.replace_with])
                    inner = f"REGEXP_REPLACE({ref}, ?, ?, 'g')"
            if step.nulls_to is not None:
                binds.append(step.nulls_to)
                inner = f"COALESCE({inner}, ?)"
            return inner

        sql = _emit_select(in_cols, relation, replace={col["name"]: emit_replace})
        return sql, in_cols

    if isinstance(step, S.ParseDatesStep):
        rows = _resolve_many(step.columns, in_cols, what="parse_dates")
        for r in rows:
            _require_text(r, "parse_dates")
        targets = {r["name"] for r in rows}

        def make_emitter(name: str) -> Emitter:
            def emit() -> str:
                binds.append(step.format)
                return f"STRPTIME({quote_ident(name)}, ?)"
            return emit

        replace = {name: make_emitter(name) for name in targets}
        sql = _emit_select(in_cols, relation, replace=replace)
        return sql, [{**c, "dtype": "TIMESTAMP"} if c["name"] in targets else c
                     for c in in_cols]

    if isinstance(step, S.SplitStep):
        col = resolve_column(step.column, in_cols)
        _require_text(col, "split")
        ref = quote_ident(col["name"])

        def emit_split() -> str:
            binds.extend([step.delimiter, step.index])
            return f"list_extract(STR_SPLIT({ref}, ?), ?)"

        return _emit_projection(in_cols, relation, step.into, emit_split,
                                "VARCHAR", binds)

    if isinstance(step, S.MergeStep):
        rows = _resolve_many(step.columns, in_cols, what="merge")
        refs = ", ".join(quote_ident(r["name"]) for r in rows)

        def emit_merge() -> str:
            binds.append(step.separator)
            return f"CONCAT_WS(?, {refs})"

        drop = {r["name"] for r in rows} if step.drop_sources else None
        if drop and step.into in drop:
            drop = drop - {step.into}
        return _emit_projection(in_cols, relation, step.into, emit_merge,
                                "VARCHAR", binds, drop=drop)

    if isinstance(step, S.ComputeStep):
        # Compile once up front to type check and reject unknown refs early;
        # the emitter recompiles in place so binds land in textual order.
        probe = compile_expr(step.expression, in_cols, [], source_relation=relation)

        def emit_compute() -> str:
            return compile_expr(step.expression, in_cols, binds,
                                source_relation=relation).sql

        return _emit_projection(in_cols, relation, step.into, emit_compute,
                                probe.dtype, binds)

    # --- row steps ------------------------------------------------------------
    if isinstance(step, S.FilterStep):
        clause = _compile_condition(step.where, in_cols, binds, relation)
        where = f" WHERE {clause}" if clause else ""
        return f"SELECT * FROM {relation}{where}", in_cols

    if isinstance(step, S.DeduplicateStep):
        subset = (_resolve_many(step.subset, in_cols, what="deduplicate")
                  if step.subset else in_cols)
        partition = ", ".join(quote_ident(r["name"]) for r in subset)
        if step.keep == "none":
            qualify = f"COUNT(*) OVER (PARTITION BY {partition}) = 1"
        else:
            order = _order_terms(step.order_by, in_cols, invert=step.keep == "last")
            window = f"PARTITION BY {partition}" + (f" ORDER BY {order}" if order else "")
            qualify = f"row_number() OVER ({window}) = 1"
        return f"SELECT * FROM {relation} QUALIFY {qualify}", in_cols

    if isinstance(step, S.SortStep):
        order = _order_terms(step.by, in_cols)
        return f"SELECT * FROM {relation} ORDER BY {order}", in_cols

    if isinstance(step, S.LimitStep):
        binds.extend([step.count, step.offset])
        return f"SELECT * FROM {relation} LIMIT ? OFFSET ?", in_cols

    # Unreachable: the discriminated union rejects unknown ``type`` on input.
    raise ProblemException(
        400, f"Unsupported step type: {getattr(step, 'type', type(step).__name__)}",
        code="invalid-step")


def _emit_projection(in_cols: list[dict], relation: str, into: str,
                     emitter: Emitter, dtype: str, binds: list,
                     *, drop: set[str] | None = None) -> tuple[str, list[dict]]:
    """Emit a step that writes one derived column, replacing it in place when
    *into* names an existing column and appending it otherwise."""
    existing = next((c for c in in_cols if c["name"] == into
                     or c.get("normalized_name") == into), None)
    if existing is not None and not (drop and existing["name"] in drop):
        sql = _emit_select(in_cols, relation,
                           replace={existing["name"]: emitter}, drop=drop)
        out = [{**c, "dtype": dtype} if c["name"] == existing["name"] else c
               for c in in_cols]
        out = [c for c in out if not drop or c["name"] not in drop]
        return sql, _renumber(out)
    sql = _emit_select(in_cols, relation, append=[(into, emitter)], drop=drop)
    kept = [c for c in in_cols if not drop or c["name"] not in drop]
    return sql, _renumber([*kept, _new_column(into, dtype)])


def compile_pipeline(steps: list, schema_json: list[dict], *,
                     source: str = "df",
                     sample_rows: int | None = None,
                     step_columns: list[list[dict]] | None = None,
                     ) -> tuple[str, list, list[dict]]:
    """Compile a whole pipeline. Returns (SQL, binds, output columns).

    *source* is the relation the data is already registered under (``df`` for
    ``load_data``). *sample_rows*, when set, makes the head of the chain a
    ``USING SAMPLE`` — the preview path, which never touches the full file.

    *step_columns*, when a list is passed, is filled with the folded column list
    as each step leaves it — the intermediate snapshots this loop computes and
    then throws away. Handed back as an out-parameter rather than a fourth
    return value so the many callers that only want the final schema are
    untouched.
    """
    if len(steps) > S.MAX_PIPELINE_STEPS:
        raise ProblemException(
            400, f"A pipeline may have at most {S.MAX_PIPELINE_STEPS} steps "
                 f"(got {len(steps)})",
            code="too-many-steps")

    binds: list = []
    cols = initial_columns(schema_json)
    src = f"SELECT * FROM {source}"
    if sample_rows is not None:
        src += f" USING SAMPLE {int(sample_rows)} ROWS"
    ctes: list[tuple[str, str]] = [("src", src)]

    relation = "src"
    for i, step in enumerate(steps):
        sql, cols = compile_step(step, cols, binds, relation)
        if step_columns is not None:
            step_columns.append([dict(c) for c in cols])
        relation = f"step_{i}"
        ctes.append((relation, sql))

    body = ",\n     ".join(f"{name} AS ({sql})" for name, sql in ctes)
    return f"WITH {body}\nSELECT * FROM {relation}", binds, cols
