"""DataTable node — read/write the project's data tables (phase 7, BACKEND-DESIGN §3.7).

Also the function behind ``studio.db.table(name)`` (via the broker). Tables are resolved by
name inside the run's project (``context.project_id``).
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from ..base import BaseNode, NodeInputDefinition, NodeOutputDefinition, NodeProperty, NodePropertyOption, NodeTypeDescription

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult

OPS = ["insert", "get", "list", "update", "delete", "upsert"]


class DataTableNode(BaseNode):
    node_description = NodeTypeDescription(
        name="DataTable",
        display_name="Data Table",
        description="Insert, read, update or delete rows in a project data table",
        icon="fa:table",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[NodeOutputDefinition(name="main", display_name="Rows")],
        properties=[
            NodeProperty(display_name="Table", name="table", type="string", default="", required=True),
            NodeProperty(display_name="Operation", name="operation", type="options", default="list",
                         options=[NodePropertyOption(name=o, value=o) for o in OPS]),
            NodeProperty(display_name="Data", name="data", type="json", default=None,
                         description="Row data (insert/update/upsert). Insert without data inserts each input item."),
            NodeProperty(display_name="Row ID", name="rowId", type="string", default=None),
            NodeProperty(display_name="Filter", name="filter", type="json", default=None,
                         description='e.g. {"status": "open", "total": {"$gt": 5}}'),
            NodeProperty(display_name="Limit", name="limit", type="number", default=50),
            NodeProperty(display_name="Sort", name="sort", type="string", default="-created_at"),
            NodeProperty(display_name="Match Columns", name="matchColumns", type="json", default=None,
                         description="Columns identifying the row for upsert, e.g. [\"email\"]"),
        ],
    )

    @property
    def type(self) -> str:
        return "DataTable"

    @property
    def description(self) -> str:
        return "Read/write project data tables"

    async def execute(self, context: ExecutionContext, node_definition: NodeDefinition,
                      input_data: list[NodeData]) -> NodeExecutionResult:
        from ...db.session import async_session_factory
        from ...engine.expression_engine import ExpressionEngine, expression_engine
        from ...engine.types import NodeData
        from ...services.data_table_service import DataTableService
        from ...services.project_service import ProjectError

        items = input_data or [NodeData(json={})]
        expr = ExpressionEngine.create_context(items, context.node_states, context.execution_id, 0)
        p = {k: expression_engine.resolve(v, expr) for k, v in node_definition.parameters.items()}
        for k in ("data", "filter", "matchColumns"):
            if isinstance(p.get(k), str) and p[k].strip():
                try:
                    p[k] = json.loads(p[k])
                except ValueError:
                    pass
        op = p.get("operation") or "list"
        if op not in OPS:
            raise ValueError(f"unknown operation '{op}'")
        project_id = context.project_id or "proj_default"

        def flat(r) -> dict[str, Any]:
            return {"id": r.id, **(r.data or {})}

        try:
            async with async_session_factory() as session:
                svc = DataTableService(session)
                t = await svc.table_by_name(project_id, str(p.get("table") or ""))
                if t is None:  # review B1: a distinct code so callers can offer "Create table"
                    raise ProjectError(404, "table_not_found", f"table '{p.get('table')}' not found in this project",
                                       table=str(p.get("table") or ""))
                if op == "insert":
                    rows = p.get("data")
                    rows = rows if isinstance(rows, list) else ([rows] if isinstance(rows, dict) else [i.json for i in items])
                    out = [flat(r) for r in await svc.insert_rows(t, rows)]
                elif op == "get":
                    out = [flat(await svc.get_row(t, int(p.get("rowId"))))]
                elif op == "list":
                    res = await svc.list_rows(t, filter=p.get("filter"), sort=p.get("sort"), limit=int(p.get("limit") or 50))
                    out = [{"id": r["id"], **r["data"]} for r in res["rows"]]
                elif op == "update":
                    out = [flat(await svc.update_row(t, int(p.get("rowId")), p.get("data") or {}, merge=True))]
                elif op == "delete":
                    rid = int(p.get("rowId"))
                    await svc.delete_row(t, rid)
                    out = [{"id": rid, "deleted": True}]
                else:  # upsert
                    data = p.get("data") or {}
                    match = p.get("matchColumns") or []
                    flt = {c: data.get(c) for c in match}
                    found = (await svc.list_rows(t, filter=flt, limit=1))["rows"] if flt else []
                    if found:
                        out = [flat(await svc.update_row(t, found[0]["id"], data, merge=True))]
                    else:
                        out = [flat(r) for r in await svc.insert_rows(t, [data])]
        except ProjectError as e:
            err = RuntimeError(e.message)
            err.code, err.details = e.code, {k: v for k, v in e.extra.items()}  # type: ignore[attr-defined]
            raise err
        return self.output([NodeData(json=o) for o in out])
