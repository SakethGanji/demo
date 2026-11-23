"""data: rows typed or pasted into the notebook (CSV is parsed at create time), or, with ``from_table``, the current
rows of a project data table (by id or name; what store cells write), read when the cell runs."""

from __future__ import annotations

from typing import Any

from . import CellOutcome


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ..service import normalize_data_spec
    from ..storage import preview

    if (inputs or {}).get("from_table"):
        return await _from_table(ctx, str(inputs["from_table"]))
    spec = normalize_data_spec(inputs or {})
    rows = list(spec.get("rows") or [])
    return CellOutcome(output=preview(rows, spec.get("columns") or None), full_rows=rows)


async def _from_table(ctx: Any, ref: str) -> CellOutcome:
    from sqlalchemy import select

    from ....db.models import DataTableModel, DataTableRowModel
    from ....schemas.notebook import MAX_DATA_ROWS
    from ...project_service import resolve_project
    from ..storage import columns_of, preview

    async with ctx.session() as s:
        project = await resolve_project(s, ctx.project_id)
        t = await s.get(DataTableModel, ref)
        if t is None or t.project_id != project.id:
            t = (await s.execute(select(DataTableModel).where(DataTableModel.project_id == project.id,
                                                              DataTableModel.name == ref))).scalars().first()
        if t is None:
            return CellOutcome.fail("table_not_found", f"there is no data table '{ref}' in this project")
        rows = [dict(d or {}) for d in (await s.execute(
            select(DataTableRowModel.data).where(DataTableRowModel.table_id == t.id)
            .order_by(DataTableRowModel.id).limit(MAX_DATA_ROWS))).scalars().all()]
        cols = [c.get("name") for c in (t.columns or []) if isinstance(c, dict) and c.get("name")] or columns_of(rows)
        return CellOutcome(output=preview(rows, cols, table_id=t.id, table=t.name), full_rows=rows)
