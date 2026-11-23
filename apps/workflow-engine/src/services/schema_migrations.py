"""Schema evolution for data tables: what it takes to move a table from its current columns to a planned definition.

Pure functions, no database. A data table is JSONB rows plus a ``columns`` metadata list, so every additive change is a
metadata update (plus a default backfill); nothing here needs DDL. :func:`diff_table` splits a change into ``ops`` that are
safe to apply to a table with rows and ``refused`` ops that would lose or reinterpret data. :func:`plan_migrations` runs it
over two plan bodies. The apply side (row lock, backfill, duplicate check, the migrations ledger) lives with the table
service.

Ops (additive): ``create_table``, ``add_column``, ``add_enum_values``, ``set_unique``, ``set_index``, ``set_ref``,
``set_description``, ``set_required`` (relaxing, or tightening when a default or an empty table makes it safe),
``set_default`` and ``deprecate_column`` (a flag; a column is never dropped).
Refused (destructive): ``drop_column``, ``rename_column`` (a drop plus an add of the same type), ``type_change``,
``remove_enum_values``, ``required_without_default_on_nonempty`` and ``drop_table``.
"""

from __future__ import annotations

from typing import Any

SYSTEM_COLUMNS = frozenset({"id", "created_at", "updated_at"})
ADDITIVE_OPS = frozenset({"create_table", "add_column", "add_enum_values", "set_unique", "set_index", "set_ref",
                          "set_description", "set_required", "set_default", "deprecate_column"})
REFUSED_OPS = frozenset({"drop_column", "rename_column", "type_change", "remove_enum_values",
                         "required_without_default_on_nonempty", "drop_table"})
COLUMN_KEYS = ("name", "type", "required", "default", "unique", "index", "enum", "ref", "description", "deprecated")


def column(c: dict[str, Any]) -> dict[str, Any]:
    """A column as the table metadata keeps it: name, type, required, then only the optional keys that are set."""
    out: dict[str, Any] = {"name": str(c.get("name") or ""), "type": str(c.get("type") or "string"),
                           "required": bool(c.get("required"))}
    if c.get("default") is not None:
        out["default"] = c["default"]
    for k in ("unique", "index", "deprecated"):
        if c.get(k):
            out[k] = True
    if isinstance(c.get("enum"), list):
        out["enum"] = [str(v) for v in c["enum"]]
    for k in ("ref", "description"):
        if c.get(k):
            out[k] = str(c[k])
    return out


def _cols(cols: Any) -> dict[str, dict[str, Any]]:
    return {str(c.get("name")): column(c) for c in cols or [] if isinstance(c, dict) and c.get("name")
            and str(c.get("name")) not in SYSTEM_COLUMNS}


def is_additive(ops: list[dict[str, Any]]) -> bool:
    return all(o.get("op") in ADDITIVE_OPS for o in ops)


def _refuse(op: str, reason: str, **kw: Any) -> dict[str, Any]:
    return {"op": op, **kw, "reason": reason}


def diff_table(current_cols: list[dict[str, Any]] | None, planned_table: dict[str, Any], row_count: int = 0, *,
               current_meta: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """``{"ops": [...], "refused": [...]}`` to move a table whose columns are ``current_cols`` (``None``: the table does not
    exist yet) to ``planned_table`` (``{name, columns, indexes?, description?}``). ``row_count`` decides whether a required
    column without a default can be added: only to an empty table. Table-level metadata (composite ``indexes`` and the
    table ``description``) is compared only when ``current_meta`` (``{indexes, description}``) is given; column-level
    changes always are."""
    planned = _cols(planned_table.get("columns"))
    if current_cols is None:
        return {"ops": [{"op": "create_table", "columns": list(planned.values()),
                         **({"description": str(planned_table["description"])} if planned_table.get("description") else {})}],
                "refused": []}
    cur = _cols(current_cols)
    has_rows = (row_count or 0) > 0
    ops: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    added = [n for n in planned if n not in cur]
    dropped = [n for n in cur if n not in planned]
    renames: list[tuple[str, str]] = []
    for d in dropped:   # a column gone and one of the same type new: most likely a rename, which loses the old values
        a = next((a for a in added if planned[a]["type"] == cur[d]["type"] and a not in {x for _, x in renames}), None)
        if a is not None:
            renames.append((d, a))
    renamed_from, renamed_to = {d for d, _ in renames}, {a for _, a in renames}
    for d, a in renames:
        refused.append(_refuse("rename_column", f"renaming {d} to {a} would orphan the values stored under {d}; add {a} "
                               f"and deprecate {d} instead", **{"from": d, "to": a}))
    for d in dropped:
        if d not in renamed_from:
            refused.append(_refuse("drop_column", f"dropping {d} loses its values; mark it deprecated instead", column=d))

    for n in added:
        if n in renamed_to:
            continue
        c = planned[n]
        if c["required"] and "default" not in c and has_rows:
            refused.append(_refuse("required_without_default_on_nonempty",
                                   f"{n} is required but has no default, and the table already has rows", column=n))
        else:
            ops.append({"op": "add_column", "column": c})

    for n, p in planned.items():
        c = cur.get(n)
        if c is None:
            continue
        if p["type"] != c["type"]:
            refused.append(_refuse("type_change", f"{n} would change from {c['type']} to {p['type']}; existing values "
                                   "would no longer match", column=n, **{"from": c["type"], "to": p["type"]}))
            continue
        if "default" in p and p.get("default") != c.get("default"):
            ops.append({"op": "set_default", "column": n, "default": p["default"]})
        if p["required"] != c["required"]:
            if p["required"] and has_rows and "default" not in p:
                refused.append(_refuse("required_without_default_on_nonempty",
                                       f"{n} would become required without a default, and the table already has rows", column=n))
            else:
                ops.append({"op": "set_required", "column": n, "value": p["required"]})
        pe, ce = p.get("enum"), c.get("enum")
        if pe is not None and ce is not None:
            more = [v for v in pe if v not in ce]
            gone = [v for v in ce if v not in pe]
            if more:
                ops.append({"op": "add_enum_values", "column": n, "values": more})
            if gone:
                refused.append(_refuse("remove_enum_values", f"rows may still hold {', '.join(gone)} in {n}",
                                       column=n, values=gone))
        elif pe is not None and ce is None:
            if has_rows:
                refused.append(_refuse("remove_enum_values", f"restricting {n} to {', '.join(pe)} may reject values rows "
                                       "already hold", column=n, values=[]))
            else:
                ops.append({"op": "add_enum_values", "column": n, "values": pe})
        if bool(p.get("unique")) != bool(c.get("unique")):
            ops.append({"op": "set_unique", "column": n, "value": bool(p.get("unique"))})
        if p.get("ref") != c.get("ref"):
            ops.append({"op": "set_ref", "column": n, "ref": p.get("ref")})
        if p.get("description") and p.get("description") != c.get("description"):
            ops.append({"op": "set_description", "column": n, "description": p["description"]})
        if p.get("index") and not c.get("index"):
            ops.append({"op": "set_index", "columns": [n]})
        if p.get("deprecated") and not c.get("deprecated"):
            ops.append({"op": "deprecate_column", "column": n})

    if current_meta is not None:
        have = [[str(x) for x in i] if isinstance(i, list) else [str(i)] for i in current_meta.get("indexes") or []]
        for idx in planned_table.get("indexes") or []:
            cols = [str(x) for x in idx] if isinstance(idx, list) else [str(idx)]
            if cols and cols not in have:
                ops.append({"op": "set_index", "columns": cols})
        if planned_table.get("description") and planned_table.get("description") != current_meta.get("description"):
            ops.append({"op": "set_description", "column": None, "description": str(planned_table["description"])})
    return {"ops": ops, "refused": refused}


def _tables(plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    data = (plan or {}).get("data") if isinstance((plan or {}).get("data"), dict) else {}
    return {str(t.get("name")): t for t in data.get("tables") or [] if isinstance(t, dict) and t.get("name")}


def plan_migrations(prev_plan: dict[str, Any] | None, new_plan: dict[str, Any],
                    row_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """``[{table, ops, refused}]`` for every table whose definition differs between two plan bodies (``prev_plan=None``:
    nothing approved yet, so every table is created). Row counts are unknown between plans, so a table is assumed to
    hold rows unless ``row_counts`` says otherwise."""
    prev, new = _tables(prev_plan), _tables(new_plan)
    out: list[dict[str, Any]] = []
    for name, t in new.items():
        p = prev.get(name)
        rows = (row_counts or {}).get(name, 1)
        d = diff_table(p.get("columns") if p is not None else None, t, rows,
                       current_meta={"indexes": p.get("indexes") or [], "description": p.get("description")} if p else None)
        if d["ops"] or d["refused"]:
            out.append({"table": name, "ops": d["ops"], "refused": d["refused"]})
    for name in prev:
        if name not in new:
            out.append({"table": name, "ops": [], "refused": [_refuse("drop_table", f"dropping {name} loses its rows; "
                                                                      "keep it and stop writing to it instead")]})
    return out
