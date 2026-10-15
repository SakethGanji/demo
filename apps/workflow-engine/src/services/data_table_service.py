"""Data tables: schema'd JSON rows per project (BACKEND-DESIGN §3.7, phase 7).

Filter grammar: ``{"col": v}`` equality; ``{"col": {"$eq|$ne|$gt|$gte|$lt|$lte": v, "$in": [...],
"$contains": "s", "$exists": bool}}``; top-level ``$and`` / ``$or`` lists. Column names are checked
against the table schema (injection guard); values are bound parameters.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Any

from sqlalchemy import Numeric, and_, case, cast, delete as sa_delete, func, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DataTableModel, DataTableRowModel, ProjectModel
from ..utils.ids import data_table_id
from .project_service import ProjectError

COLUMN_TYPES = {"string", "number", "integer", "boolean", "datetime", "json"}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
SYSTEM_SORT = {"created_at", "id", "updated_at"}
MAX_BULK = 1000


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


def table_to_dict(t: DataTableModel, row_count: int | None = None) -> dict[str, Any]:
    out = {"id": t.id, "project_id": t.project_id, "name": t.name, "description": t.description,
           "columns": t.columns, "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()}
    if row_count is not None:
        out["row_count"] = row_count
    return out


def row_to_dict(r: DataTableRowModel) -> dict[str, Any]:
    return {"id": r.id, "data": r.data, "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()}


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
        return table_to_dict(t, 0)

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
            bad = 0
            rows = (await self._s.execute(select(DataTableRowModel.data).where(DataTableRowModel.table_id == t.id))).scalars().all()
            for data in rows:
                if any((c["required"] and data.get(c["name"]) is None and "default" not in c) or
                       not _type_ok(c["type"], data.get(c["name"])) for c in cols):
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
        return out, errors

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
