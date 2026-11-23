"""Data tables: schema'd JSON rows per project (BACKEND-DESIGN §3.7, phase 7).

Filter grammar: ``{"col": v}`` equality; ``{"col": {"$eq|$ne|$gt|$gte|$lt|$lte": v, "$in": [...],
"$contains": "s", "$exists": bool}}``; top-level ``$and`` / ``$or`` lists. Column names are checked
against the table schema (injection guard); values are bound parameters.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Numeric, and_, case, cast, delete as sa_delete, func, not_, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DataTableModel, DataTableRowModel, ProjectModel
from ..db.schema_models import DataTableMigrationModel
from ..utils.ids import data_table_id, generate_id
from .project_service import ProjectError

COLUMN_TYPES = {"string", "number", "integer", "boolean", "datetime", "json"}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
SYSTEM_SORT = {"created_at", "id", "updated_at"}
MAX_BULK = 1000
REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$")
# column metadata beyond name/type/required/default; all optional and only stored when set
FLAG_KEYS = ("unique", "index", "deprecated")
META_KEYS = FLAG_KEYS + ("enum", "ref", "description")
# schema migrations: only these are applied; anything destructive is refused (a deliberate edit on the Data page instead)
ADDITIVE_OPS = ("add_column", "add_enum_values", "set_unique", "set_index", "set_ref", "set_description", "set_required",
                "set_default", "deprecate_column")


def _type_ok(t: str, v: Any) -> bool:
    if v is None:
        return True
    if t == "string":
        return isinstance(v, str)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    if t == "boolean":
        return isinstance(v, bool)
    if t == "datetime":
        if not isinstance(v, str):
            return False
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return True  # json


def iso_utc(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset. Columns are naive and written with ``datetime.now()`` (server local
    time), so a naive value is read as local time and converted; an aware value is just converted."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def table_to_dict(t: DataTableModel, row_count: int | None = None) -> dict[str, Any]:
    out = {"id": t.id, "project_id": t.project_id, "name": t.name, "description": t.description,
           "columns": t.columns, "created_at": iso_utc(t.created_at), "updated_at": iso_utc(t.updated_at)}
    if row_count is not None:
        out["row_count"] = row_count
    return out


def row_to_dict(r: DataTableRowModel) -> dict[str, Any]:
    return {"id": r.id, "data": r.data, "created_at": iso_utc(r.created_at), "updated_at": iso_utc(r.updated_at)}


class DataTableService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── tables ───────────────────────────────────────────────────────────

    @staticmethod
    def _validate_columns(columns: Any) -> list[dict[str, Any]]:
        if not isinstance(columns, list):
            raise ProjectError(422, "invalid_column", "columns must be a list")
        out, seen = [], set()
        for c in columns:
            if not isinstance(c, dict) or not NAME_RE.match(str(c.get("name", ""))) or c.get("type") not in COLUMN_TYPES:
                raise ProjectError(422, "invalid_column", f"invalid column {c!r}; types: {sorted(COLUMN_TYPES)}")
            if c["name"] in seen or c["name"] in SYSTEM_SORT:
                raise ProjectError(422, "invalid_column", f"duplicate or reserved column name '{c['name']}'")
            seen.add(c["name"])
            col = {"name": c["name"], "type": c["type"], "required": bool(c.get("required", False))}
            if "default" in c and c["default"] is not None:
                if not _type_ok(c["type"], c["default"]):
                    raise ProjectError(422, "invalid_column", f"default for '{c['name']}' is not a {c['type']}")
                col["default"] = c["default"]
            for k in FLAG_KEYS:
                if c.get(k):
                    col[k] = True
            if c.get("enum") is not None:
                en = c["enum"]
                if (not isinstance(en, list) or not en or len(set(map(repr, en))) != len(en)
                        or c["type"] in ("boolean", "json", "datetime")
                        or not all(_type_ok(c["type"], v) and v is not None for v in en)):
                    raise ProjectError(422, "invalid_column", f"enum for '{c['name']}' must be a non-empty list of distinct "
                                                              f"{c['type']} values")
                col["enum"] = en
                if "default" in col and col["default"] not in en:
                    raise ProjectError(422, "invalid_column", f"default for '{c['name']}' is not one of its enum values")
            if c.get("ref") is not None:
                if not isinstance(c["ref"], str) or not REF_RE.match(c["ref"]):
                    raise ProjectError(422, "invalid_column", f"ref for '{c['name']}' must look like 'table.column'")
                col["ref"] = c["ref"]
            if c.get("description"):
                col["description"] = str(c["description"])[:500]
            out.append(col)
        return out

    async def list_tables(self, project: ProjectModel) -> list[dict[str, Any]]:
        rows = (await self._s.execute(select(DataTableModel).where(DataTableModel.project_id == project.id)
                                      .order_by(DataTableModel.name))).scalars().all()
        counts = dict((await self._s.execute(
            select(DataTableRowModel.table_id, func.count()).where(DataTableRowModel.table_id.in_([t.id for t in rows]))
            .group_by(DataTableRowModel.table_id))).all()) if rows else {}
        return [table_to_dict(t, counts.get(t.id, 0)) for t in rows]

    async def get_table(self, project: ProjectModel, tid: str) -> DataTableModel:
        t = await self._s.get(DataTableModel, tid)
        if not t or t.project_id != project.id:
            raise ProjectError(404, "table_not_found", "Table not found")
        return t

    async def table_by_name(self, project_id: str, name: str) -> DataTableModel | None:
        return (await self._s.execute(select(DataTableModel).where(
            DataTableModel.project_id == project_id, DataTableModel.name == name))).scalars().first()

    async def row_count(self, tid: str) -> int:
        return (await self._s.execute(select(func.count()).select_from(DataTableRowModel)
                                      .where(DataTableRowModel.table_id == tid))).scalar_one()

    async def create_table(self, project: ProjectModel, body: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        name = body.get("name") or ""
        if not NAME_RE.match(name):
            raise ProjectError(422, "invalid_name", "table name must be an identifier")
        cols = self._validate_columns(body.get("columns") or [])
        if await self.table_by_name(project.id, name):
            raise ProjectError(409, "name_taken", f"table '{name}' already exists")
        now = datetime.now()
        t = DataTableModel(id=data_table_id(), team_id=project.team_id, project_id=project.id, name=name,
                           description=body.get("description"), columns=cols, created_by=actor, created_at=now, updated_at=now)
        self._s.add(t)
        await self._s.commit()
        await self._record(project, name, None, cols, "created")
        return table_to_dict(t, 0)

    async def _record(self, project: ProjectModel, name: str, before: Any, after: Any, summary: str) -> None:
        """The project file change log for backend/tables/<name>.json (never fails the change). Builder scratch tables (self-test,
        smoke) are not project files."""
        from . import project_files as pf
        from .backend_templates import selftest

        if selftest.SCRATCH_RE.search(name):
            return

        def text(cols: Any) -> str | None:
            return None if cols is None else json.dumps(list(cols), indent=2)

        await pf.record_changes(self._s, project.id, [{"path": pf.table_path(name), "before": text(before), "after": text(after),
                                                       "summary": summary}], commit=True)

    async def ensure_tables(self, project: ProjectModel, tables: Any, actor: str | None = None,
                            dry_run: bool = False, migrate: bool = False, plan_version: int | None = None) -> dict[str, Any]:
        """Create every declared-but-missing project table (``[{name, columns, description?}]``), idempotently.

        Existing tables are left untouched (never altered or emptied) unless ``migrate``: then the declared columns are diffed
        against the live ones and the additive changes are applied through :meth:`apply_migration`; destructive ones are
        reported in ``refused`` and never applied. Returns
        ``{created, existing, migrated: [{table, ops}], refused: [{table, op, reason}], errors: [{name, message}]}``;
        with ``dry_run`` nothing is written and ``created`` / ``migrated`` list what would be."""
        out: dict[str, Any] = {"created": [], "existing": [], "migrated": [], "refused": [], "errors": []}
        for t in tables if isinstance(tables, list) else []:
            name = t.get("name") if isinstance(t, dict) else None
            if not isinstance(name, str) or not NAME_RE.match(name):
                out["errors"].append({"name": name, "message": "table name must be an identifier"})
                continue
            if name in out["created"] or name in out["existing"]:
                continue
            live = await self.table_by_name(project.id, name)
            if live is not None:
                out["existing"].append(name)
                if migrate:
                    try:
                        diff = await self.diff_declared(live, t)
                        out["refused"] += [{"table": name, **r} for r in diff["refused"]]
                        if diff["ops"] and not diff["refused"]:
                            res = await self.apply_migration(project, name, diff["ops"], plan_version=plan_version,
                                                             actor=actor, dry_run=dry_run)
                            if res["applied"]:
                                out["migrated"].append({"table": name, "ops": res["applied"]})
                    except ProjectError as e:
                        out["errors"].append({"name": name, "message": e.message})
                continue
            try:
                if dry_run:
                    self._validate_columns(t.get("columns") or [])
                else:
                    await self.create_table(project, {"name": name, "columns": t.get("columns") or [],
                                                      "description": t.get("description")}, actor=actor)
            except ProjectError as e:
                out["errors"].append({"name": name, "message": e.message})
                continue
            out["created"].append(name)
        return out

    async def diff_declared(self, live: DataTableModel, declared: dict[str, Any]) -> dict[str, Any]:
        """``{ops, refused}`` to bring the live table to a declared definition. Live columns the declaration does not
        mention are kept as they are (a module declares what it uses, not the whole table), so a missing column is never a drop."""
        from .schema_migrations import diff_table

        cols = self._validate_columns(declared.get("columns") or [])
        mentioned = {c["name"] for c in cols}
        target = {**declared, "columns": cols + [dict(c) for c in live.columns or [] if c["name"] not in mentioned]}
        diff = diff_table(list(live.columns or []), target, await self.row_count(live.id))
        return {"ops": list(diff.get("ops") or []), "refused": [r if isinstance(r, dict) else {"reason": str(r)}
                                                                 for r in diff.get("refused") or []]}

    # ── schema migrations (additive only) ─────────────────────────────────

    @staticmethod
    def _op_col(op: dict[str, Any]) -> str | None:
        c = op.get("column", op.get("name"))
        if c is None and isinstance(op.get("columns"), list) and len(op["columns"]) == 1:
            c = op["columns"][0]      # set_index {columns: [name]}
        return c.get("name") if isinstance(c, dict) else c

    @staticmethod
    def _refuse(ops: list[dict[str, Any]], reason: str) -> ProjectError:
        return ProjectError(422, "schema_change_refused", reason, ops=ops)

    async def apply_migration(self, project: ProjectModel, table: str, ops: Any, *, plan_version: int | None = None,
                              actor: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        """Apply additive column changes to a table (by name or id) in one transaction under a row lock.

        Ops: ``add_column {column}`` (new defaults are backfilled into existing rows), ``add_enum_values {column, values}``,
        ``set_unique {column, value?}``, ``set_index|deprecate_column {column}``, ``set_ref {column, ref}``,
        ``set_description {column, description}`` (``column`` null: the table), ``set_required {column, value}`` (tightening
        needs a default to backfill, or no row missing the value), ``set_default {column, default}``. A composite ``set_index``
        is accepted and has no effect (indexes are metadata only).
        Anything else (drop/rename/type change/removing enum values, a required column without default on a table with rows)
        is refused with 422 ``schema_change_refused`` and nothing changes. ``set_unique`` over duplicate values is 409
        ``unique_conflict``. Applying ops that change nothing is a no-op; each change is recorded in ``data_table_migrations``.
        Returns ``{table, applied, noop, backfilled, dry_run, columns}``."""
        if not isinstance(ops, list) or not ops or not all(isinstance(o, dict) for o in ops):
            raise ProjectError(422, "invalid_ops", "ops must be a non-empty list of objects")
        bad = [o for o in ops if o.get("op") not in ADDITIVE_OPS]
        if bad:
            raise self._refuse(bad, "only additive changes can be migrated ("
                                    + ", ".join(ADDITIVE_OPS) + "); destructive changes are made by hand on the Data page")
        stmt = (select(DataTableModel).where(DataTableModel.project_id == project.id,
                                             (DataTableModel.name == table) | (DataTableModel.id == table))
                .with_for_update().execution_options(populate_existing=True))
        t = (await self._s.execute(stmt)).scalars().first()
        if t is None:
            raise ProjectError(404, "table_not_found", "Table not found")
        before = [dict(c) for c in t.columns or []]
        sp = await self._s.begin_nested()   # savepoint: a dry run or a refusal rolls back only this, never the caller's work
        try:
            res = await self._apply_locked(project, t, ops, plan_version, actor, dry_run)
        except BaseException:
            await sp.rollback()
            raise
        if dry_run or res["noop"]:
            await sp.rollback()
        else:
            await sp.commit()
            await self._s.commit()
            await self._record(project, t.name, before, res.get("columns", t.columns),
                               "migrated: " + ", ".join(str(o.get("op")) for o in res["applied"])[:300])
        return res

    async def _apply_locked(self, project: ProjectModel, t: DataTableModel, ops: list[dict[str, Any]],
                            plan_version: int | None, actor: str | None, dry_run: bool) -> dict[str, Any]:
        n_rows = await self.row_count(t.id)
        cols = [dict(c) for c in t.columns or []]
        by = {c["name"]: c for c in cols}
        applied: list[dict[str, Any]] = []
        backfill: dict[str, Any] = {}
        new_unique: list[str] = []
        widened: dict[str, list[Any]] = {}
        new_desc: Any = ...               # the table's own description (written after the dry-run check)
        tighten: list[str] = []           # became required and have a default: rows missing the value get it
        tighten_strict: list[str] = []    # became required with no default: refused if any row lacks the value
        for op in ops:
            kind, name = op["op"], self._op_col(op)
            if kind == "add_column":
                spec = op.get("column")
                if not isinstance(spec, dict):
                    raise ProjectError(422, "invalid_ops", "add_column needs a column object")
                (new,) = self._validate_columns([spec])
                cur = by.get(new["name"])
                if cur is not None:
                    if cur["type"] != new["type"]:
                        raise self._refuse([op], f"column '{new['name']}' exists as {cur['type']}; changing its type to "
                                                 f"{new['type']} is destructive")
                    continue  # already there: nothing to do
                if new["required"] and "default" not in new and n_rows:
                    raise self._refuse([op], f"'{new['name']}' is required with no default but the table has {n_rows} row(s); "
                                             f"add a default or make it optional")
                cols.append(new)
                by[new["name"]] = new
                if "default" in new:
                    backfill[new["name"]] = new["default"]
                if new.get("unique"):
                    new_unique.append(new["name"])
                applied.append(op)
                continue
            if kind == "set_description" and name is None:       # the table's own description
                d = str(op.get("description") or "")[:500]
                if (t.description or "") != d:
                    new_desc = d or None
                    applied.append(op)
                continue
            if kind == "set_index" and isinstance(op.get("columns"), list) and len(op["columns"]) > 1:
                continue                                          # composite index: metadata only, nothing to store per column
            if name not in by:
                raise ProjectError(422, "invalid_ops", f"{kind}: column '{name}' does not exist")
            c = by[name]
            if kind == "add_enum_values":
                vals = op.get("values") or op.get("enum") or op.get("add")
                if not isinstance(vals, list) or not vals:
                    raise ProjectError(422, "invalid_ops", "add_enum_values needs a non-empty 'values' list")
                merged = list(c.get("enum") or []) + [v for v in vals if v not in (c.get("enum") or [])]
                if merged == (c.get("enum") or []):
                    continue
                (chk,) = self._validate_columns([{**c, "enum": merged, "default": c.get("default")}])
                if not c.get("enum"):
                    widened[name] = merged  # a new constraint: existing values must already fit it
                c["enum"] = chk["enum"]
            elif kind == "set_unique":
                if op.get("value") is False:
                    if not c.get("unique"):
                        continue
                    c.pop("unique")
                elif c.get("unique"):
                    continue
                else:
                    c["unique"] = True
                    new_unique.append(name)
            elif kind == "set_required":
                want = bool(op.get("value", True))
                if want == bool(c.get("required")):
                    continue
                c["required"] = want
                if want:
                    if "default" in c:
                        backfill.setdefault(name, c["default"])
                        tighten.append(name)
                    else:
                        tighten_strict.append(name)
            elif kind == "set_default":
                d = op.get("default")
                if d is None:
                    if "default" not in c:
                        continue
                    c.pop("default")
                else:
                    if c.get("default") == d:
                        continue
                    (chk,) = self._validate_columns([{**c, "default": d}])
                    c["default"] = chk["default"]
            elif kind in ("set_index", "deprecate_column"):
                flag = "index" if kind == "set_index" else "deprecated"
                if c.get(flag):
                    continue
                c[flag] = True
            elif kind == "set_ref":
                ref = op.get("ref")
                if ref is None:
                    if "ref" not in c:
                        continue
                    c.pop("ref")
                elif not isinstance(ref, str) or not REF_RE.match(ref):
                    raise ProjectError(422, "invalid_ops", "set_ref needs ref like 'table.column'")
                elif c.get("ref") == ref:
                    continue
                else:
                    c["ref"] = ref
            else:  # set_description
                d = str(op.get("description") or "")[:500]
                if (c.get("description") or "") == d:
                    continue
                if d:
                    c["description"] = d
                else:
                    c.pop("description", None)
            applied.append(op)
        result: dict[str, Any] = {"table": t.name, "applied": applied, "noop": not applied, "backfilled": 0,
                                  "dry_run": dry_run, "columns": cols}
        if not applied:
            return result
        for name, merged in widened.items():
            bad_n = (await self._s.execute(text(
                "SELECT count(*) FROM data_table_rows WHERE table_id = :t AND data ? CAST(:c AS text) AND jsonb_typeof(data -> CAST(:c AS text)) <> 'null' "
                "AND NOT (jsonb_build_array(data -> CAST(:c AS text)) <@ CAST(:v AS jsonb))"),
                {"t": t.id, "c": name, "v": json.dumps(merged)})).scalar_one()
            if bad_n:
                raise ProjectError(409, "rows_incompatible", f"{bad_n} existing row(s) have a '{name}' outside the new enum",
                                   count=bad_n)
        for name in tighten_strict:
            lacking = (await self._s.execute(text(
                "SELECT count(*) FROM data_table_rows WHERE table_id = :t AND (NOT data ? CAST(:c AS text) "
                "OR jsonb_typeof(data -> CAST(:c AS text)) = 'null')"), {"t": t.id, "c": name})).scalar_one()
            if lacking:
                raise self._refuse([o for o in ops if self._op_col(o) == name and o.get("op") == "set_required"],
                                   f"'{name}' cannot become required: {lacking} row(s) have no value and there is no default")
        for name, default in backfill.items():
            r = await self._s.execute(text(
                "UPDATE data_table_rows SET data = data || jsonb_build_object(CAST(:c AS text), CAST(:d AS jsonb)) "
                "WHERE table_id = :t AND (NOT data ? CAST(:c AS text) OR jsonb_typeof(data -> CAST(:c AS text)) = 'null')"),
                {"t": t.id, "c": name, "d": json.dumps(default)})
            result["backfilled"] += r.rowcount or 0
        for name in new_unique:
            dup = (await self._s.execute(text(
                "SELECT data -> CAST(:c AS text) AS v, array_agg(id ORDER BY id) AS ids FROM data_table_rows "
                "WHERE table_id = :t AND data ? CAST(:c AS text) AND jsonb_typeof(data -> CAST(:c AS text)) <> 'null' "
                "GROUP BY 1 HAVING count(*) > 1 ORDER BY min(id) LIMIT 5"), {"t": t.id, "c": name})).all()
            if dup:
                raise ProjectError(409, "unique_conflict", f"cannot make '{name}' unique: {len(dup)} value(s) already repeat",
                                   column=name, samples=[{"value": d.v, "row_ids": list(d.ids)[:10]} for d in dup])
        if dry_run:
            return result
        t.columns = self._validate_columns(cols)
        if new_desc is not ...:
            t.description = new_desc
        result["columns"] = t.columns
        t.updated_at = datetime.now()
        h = hashlib.sha256(json.dumps(applied, sort_keys=True, default=str).encode()).hexdigest()
        rec = (await self._s.execute(select(DataTableMigrationModel).where(
            DataTableMigrationModel.table_id == t.id, DataTableMigrationModel.op_hash == h))).scalars().first()
        if rec is not None:  # the same ops again (the table drifted back in between): refresh, never duplicate
            rec.applied_at, rec.applied_by = datetime.now(), actor
            if plan_version is not None:
                rec.plan_version = plan_version
        else:
            self._s.add(DataTableMigrationModel(id=generate_id("dtm"), project_id=project.id, table_id=t.id,
                                                plan_version=plan_version, ops=applied, op_hash=h, applied_by=actor))
        await self._s.flush()
        return result

    async def migrations(self, project: ProjectModel, table: str) -> list[dict[str, Any]]:
        t = (await self._s.execute(select(DataTableModel).where(
            DataTableModel.project_id == project.id, (DataTableModel.name == table) | (DataTableModel.id == table)))).scalars().first()
        if t is None:
            raise ProjectError(404, "table_not_found", "Table not found")
        rows = (await self._s.execute(select(DataTableMigrationModel).where(DataTableMigrationModel.table_id == t.id)
                                      .order_by(DataTableMigrationModel.applied_at))).scalars().all()
        return [{"id": m.id, "plan_version": m.plan_version, "ops": m.ops, "applied_by": m.applied_by,
                 "applied_at": iso_utc(m.applied_at)} for m in rows]

    async def update_table(self, project: ProjectModel, tid: str, body: dict[str, Any]) -> dict[str, Any]:
        t = await self.get_table(project, tid)
        if body.get("name") and body["name"] != t.name:
            if not NAME_RE.match(body["name"]):
                raise ProjectError(422, "invalid_name", "table name must be an identifier")
            if await self.table_by_name(project.id, body["name"]):
                raise ProjectError(409, "name_taken", f"table '{body['name']}' already exists")
            t.name = body["name"]
        if "description" in body:
            t.description = body["description"]
        if body.get("columns") is not None:
            cols = self._validate_columns(body["columns"])
            old = {c["name"]: c for c in t.columns or []}
            for raw, c in zip(body["columns"], cols):  # a client that does not know a metadata key must not erase it
                for k in META_KEYS:
                    if k not in raw and k in old.get(c["name"], {}):
                        c[k] = old[c["name"]][k]
            bad = 0
            rows = (await self._s.execute(select(DataTableRowModel.data).where(DataTableRowModel.table_id == t.id))).scalars().all()
            newly_unique = [c["name"] for c in cols if c.get("unique") and not old.get(c["name"], {}).get("unique")]
            seen_vals: dict[str, list[Any]] = {n: [] for n in newly_unique}
            for data in rows:
                dup = False
                for n in newly_unique:
                    v = data.get(n)
                    if v is not None:
                        dup = dup or v in seen_vals[n]
                        seen_vals[n].append(v)
                if dup or any((c["required"] and data.get(c["name"]) is None and "default" not in c) or
                              not _type_ok(c["type"], data.get(c["name"])) or
                              (c.get("enum") and data.get(c["name"]) is not None and data[c["name"]] not in c["enum"])
                              for c in cols):
                    bad += 1
            if bad:
                raise ProjectError(409, "rows_incompatible", f"{bad} existing row(s) do not fit the new columns", count=bad)
            t.columns = cols
        t.updated_at = datetime.now()
        await self._s.commit()
        return table_to_dict(t, await self.row_count(t.id))

    async def delete_table(self, project: ProjectModel, tid: str) -> None:
        t = await self.get_table(project, tid)
        await self._s.execute(sa_delete(DataTableRowModel).where(DataTableRowModel.table_id == t.id))
        await self._s.delete(t)
        await self._s.commit()

    # ── rows ─────────────────────────────────────────────────────────────

    @staticmethod
    def _check_row(t: DataTableModel, data: Any, idx: int, partial: bool = False) -> tuple[dict, list[dict]]:
        errors: list[dict[str, Any]] = []
        if not isinstance(data, dict):
            return {}, [{"row": idx, "column": None, "error": "row data must be an object"}]
        cols = {c["name"]: c for c in t.columns}
        out = dict(data)
        for k in data:
            if k not in cols:
                errors.append({"row": idx, "column": k, "error": "unknown column"})
        for name, c in cols.items():
            if name not in out and not partial:
                if "default" in c:
                    out[name] = c["default"]
                elif c["required"]:
                    errors.append({"row": idx, "column": name, "error": "is required"})
                    continue
            if name in out:
                if out[name] is None and c["required"]:
                    errors.append({"row": idx, "column": name, "error": "is required"})
                elif not _type_ok(c["type"], out[name]):
                    errors.append({"row": idx, "column": name, "error": f"expected {c['type']}"})
                elif c.get("enum") and out[name] is not None and out[name] not in c["enum"]:
                    errors.append({"row": idx, "column": name, "error": f"must be one of {c['enum']}"})
        return out, errors

    async def _check_unique(self, t: DataTableModel, rows: list[dict[str, Any]], exclude_id: int | None = None) -> None:
        """409 ``unique_violation {column, value}`` when a row repeats a value of a ``unique`` column (null never collides).
        Runs under a per-table advisory lock held until the caller's commit, so two writers cannot both pass the check."""
        ucols = [c["name"] for c in t.columns or [] if c.get("unique")]
        if not ucols:
            return
        sp = await self._s.begin_nested()   # a savepoint, so a refusal leaves the caller's session and objects untouched
        try:
            await self._unique_checks(t, rows, ucols, exclude_id)
        except BaseException:
            await sp.rollback()
            raise
        await sp.commit()

    async def _unique_checks(self, t: DataTableModel, rows: list[dict[str, Any]], ucols: list[str], exclude_id: int | None) -> None:
        await self._s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"unique:{t.id}"})
        for name in ucols:
            seen: list[Any] = []
            for d in rows:
                v = d.get(name)
                if v is None:
                    continue
                if v in seen:
                    raise ProjectError(409, "unique_violation", f"'{name}' must be unique: {v!r} is repeated in this request",
                                       column=name, value=v)
                seen.append(v)
                q = select(DataTableRowModel.id).where(DataTableRowModel.table_id == t.id,
                                                       DataTableRowModel.data.op("@>")(cast({name: v}, JSONB))).limit(1)
                if exclude_id is not None:
                    q = q.where(DataTableRowModel.id != exclude_id)
                if (await self._s.execute(q)).first() is not None:
                    raise ProjectError(409, "unique_violation", f"'{name}' must be unique: {v!r} already exists",
                                       column=name, value=v)

    async def insert_rows(self, t: DataTableModel, rows: list[Any]) -> list[DataTableRowModel]:
        if len(rows) > MAX_BULK:
            raise ProjectError(422, "too_many_rows", f"at most {MAX_BULK} rows per request")
        clean, errors = [], []
        for i, d in enumerate(rows):
            c, e = self._check_row(t, d, i)
            clean.append(c)
            errors.extend(e)
        if errors:
            raise ProjectError(422, "row_invalid", "row validation failed", errors=errors)
        await self._check_unique(t, clean)
        now = datetime.now()
        models = [DataTableRowModel(table_id=t.id, data=c, created_at=now, updated_at=now) for c in clean]
        self._s.add_all(models)
        await self._s.commit()
        return models

    async def get_row(self, t: DataTableModel, row_id: int) -> DataTableRowModel:
        r = await self._s.get(DataTableRowModel, row_id)
        if not r or r.table_id != t.id:
            raise ProjectError(404, "row_not_found", "Row not found")
        return r

    async def update_row(self, t: DataTableModel, row_id: int, data: Any, merge: bool) -> DataTableRowModel:
        r = await self.get_row(t, row_id)
        new = {**r.data, **(data or {})} if merge and isinstance(data, dict) else data
        clean, errors = self._check_row(t, new, 0)
        if errors:
            raise ProjectError(422, "row_invalid", "row validation failed", errors=errors)
        await self._check_unique(t, [clean], exclude_id=r.id)
        r.data = clean
        r.updated_at = datetime.now()
        await self._s.commit()
        return r

    async def delete_row(self, t: DataTableModel, row_id: int) -> None:
        r = await self.get_row(t, row_id)
        await self._s.delete(r)
        await self._s.commit()

    def _compile(self, t: DataTableModel, flt: Any):
        cols = {c["name"]: c for c in t.columns}
        data = DataTableRowModel.data

        def colref(name: str):
            if name not in cols and name not in ("id",):
                raise ProjectError(422, "unknown_column", f"unknown column '{name}'", column=name)
            return name

        def num(name: str):
            return case((func.jsonb_typeof(data[name]) == "number", cast(data[name].astext, Numeric)), else_=None)

        def cond(name: str, op: str, v: Any):
            if name == "id":
                idc = DataTableRowModel.id
                ops = {"$eq": lambda: idc == v, "$ne": lambda: idc != v, "$gt": lambda: idc > v, "$gte": lambda: idc >= v,
                       "$lt": lambda: idc < v, "$lte": lambda: idc <= v,
                       "$in": lambda: idc.in_(v if isinstance(v, list) else [v])}
                if op not in ops:
                    raise ProjectError(422, "invalid_filter", f"operator {op} not supported for id")
                return ops[op]()
            if op == "$eq":
                return data.op("@>")(cast({name: v}, JSONB))
            if op == "$ne":
                return not_(data.op("@>")(cast({name: v}, JSONB)))
            if op == "$in":
                if not isinstance(v, list):
                    raise ProjectError(422, "invalid_filter", "$in needs a list")
                return or_(*[data.op("@>")(cast({name: x}, JSONB)) for x in v]) if v else False
            if op == "$contains":
                s = str(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                return data[name].astext.ilike(f"%{s}%", escape="\\")
            if op == "$exists":
                return data.has_key(name) if v else not_(data.has_key(name))
            if op in ("$gt", "$gte", "$lt", "$lte"):
                lhs = num(name) if isinstance(v, (int, float)) and not isinstance(v, bool) else data[name].astext
                return {"$gt": lhs > v, "$gte": lhs >= v, "$lt": lhs < v, "$lte": lhs <= v}[op]
            raise ProjectError(422, "invalid_filter", f"unknown operator {op}")

        def walk(f: Any):
            if not isinstance(f, dict):
                raise ProjectError(422, "invalid_filter", "filter must be an object")
            parts = []
            for k, v in f.items():
                if k in ("$and", "$or"):
                    if not isinstance(v, list):
                        raise ProjectError(422, "invalid_filter", f"{k} needs a list")
                    subs = [walk(x) for x in v]
                    parts.append(and_(*subs) if k == "$and" else or_(*subs))
                elif isinstance(v, dict) and v and all(str(x).startswith("$") for x in v):
                    colref(k)
                    parts.extend(cond(k, op, x) for op, x in v.items())
                else:
                    colref(k)
                    parts.append(cond(k, "$eq", v))
            return and_(*parts) if parts else True

        return walk(flt) if flt else None

    async def list_rows(self, t: DataTableModel, *, filter: Any = None, sort: str | None = None, limit: int = 50,
                        cursor: str | None = None, count: bool = False) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 1000))
        stmt = select(DataTableRowModel).where(DataTableRowModel.table_id == t.id)
        where = self._compile(t, filter)
        if where is not None:
            stmt = stmt.where(where)
        sort = sort or "-created_at"
        desc = sort.startswith("-")
        key = sort.lstrip("-+")
        cols = {c["name"]: c for c in t.columns}
        if key in SYSTEM_SORT:
            col = getattr(DataTableRowModel, key)
        elif key in cols:
            col = DataTableRowModel.data[key].astext if cols[key]["type"] not in ("number", "integer") else \
                cast(DataTableRowModel.data[key].astext, Numeric)
        else:
            raise ProjectError(422, "unknown_column", f"cannot sort by '{key}'", column=key)
        stmt = stmt.order_by(col.desc() if desc else col.asc(), DataTableRowModel.id.desc() if desc else DataTableRowModel.id.asc())
        offset = 0
        if cursor:
            try:
                offset = int(base64.urlsafe_b64decode(cursor.encode()).decode().split(":", 1)[1])
            except Exception:
                raise ProjectError(422, "invalid_cursor", "invalid cursor")
        rows = (await self._s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        nxt = None
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = base64.urlsafe_b64encode(f"o:{offset + limit}".encode()).decode()
        out: dict[str, Any] = {"rows": [row_to_dict(r) for r in rows], "next_cursor": nxt}
        if count:
            cstmt = select(func.count()).select_from(DataTableRowModel).where(DataTableRowModel.table_id == t.id)
            if where is not None:
                cstmt = cstmt.where(where)
            out["count"] = (await self._s.execute(cstmt)).scalar_one()
        return out
