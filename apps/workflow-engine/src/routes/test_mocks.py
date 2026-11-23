"""Generate a mock for one node (Stage 3A, CONTRACTS-STAGE3A §6 "Mocks for one node"). Never saves."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..core.dependencies import get_workflow_service
from ..core.exceptions import WorkflowNotFoundError
from ..db.models import ExecutionModel, NodeOutputModel
from ..engine import shapes as sh
from ..services.api_catalog import detail_of, find_operation
from ..services.mock_gen import _body, example_from_shape, first_success
from ..repositories.api_spec_repository import ApiSpecRepository
from ..services.workflow_service import WorkflowService

router = APIRouter(prefix="/workflows")

PASTE_HINT = "paste an example"


class MockGenerateRequest(BaseModel):
    kind: Literal["from_spec", "from_contract", "last_real"]
    status: int | None = None


class MockGenerateResponse(BaseModel):
    mock: dict[str, Any]
    source: str


def _conflict(message: str, hint: str | None = None) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": message, **({"hint": hint} if hint else {})})


@router.post("/{workflow_id}/nodes/{node_name}/mock/generate", response_model=MockGenerateResponse)
async def generate_mock(
    workflow_id: str, node_name: str, body: MockGenerateRequest, service: WorkflowService = Depends(get_workflow_service),
) -> MockGenerateResponse:
    stored = await service._workflow_repo.get(workflow_id)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    node = next((n for n in stored.workflow.nodes if n.name == node_name), None)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_name}' not found in workflow")
    session = service._workflow_repo._session
    if body.kind == "from_spec":
        mock, source = await _from_spec(session, node, body.status)
    elif body.kind == "last_real":
        mock, source = await _last_real(session, workflow_id, node)
    else:
        try:
            result = await service.check(workflow_id, None, None)
        except WorkflowNotFoundError:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
        mock, source = await _from_contract(session, stored, node, result)
    return MockGenerateResponse(mock=mock, source=source)


# ── from_spec ────────────────────────────────────────────────────────────────────────────────


async def _from_spec(session, node, status: int | None) -> tuple[dict[str, Any], str]:
    ref = (node.parameters or {}).get("api_ref") if node.type == "HttpRequest" else None
    if not (isinstance(ref, dict) and ref.get("spec_id")):
        raise HTTPException(status_code=422, detail=f"Node '{node.name}' has no API endpoint (api_ref) to mock from")
    row = await ApiSpecRepository(session).get(ref["spec_id"])
    op = find_operation(detail_of(row), str(ref.get("operation_id") or "")) if row else None
    if op is None:
        raise _conflict(f"The API endpoint of '{node.name}' isn't in the API catalog")
    if status is not None:
        resp = (op.get("responses") or {}).get(str(status))
        if resp is None:
            raise _conflict(f"The endpoint documents no {status} response", "pick one of: " + ", ".join(op.get("responses") or []))
        code = status
    else:
        ok = first_success(op)
        if ok is None:
            raise _conflict("The endpoint documents no 2xx response", "pass a status")
        code, resp = ok
    http: dict[str, Any] = {"status": code}
    data = _body(resp, 1)
    if data is not None:
        http["body"] = data
    return {"http": http}, "spec"


# ── from_contract ────────────────────────────────────────────────────────────────────────────

_TABLE_RE = re.compile(r"""\b(?:from|into|update|join)\s+((?:"[^"]+"|\w+)(?:\.(?:"[^"]+"|\w+))?)""", re.IGNORECASE)
_NUMERIC = {"smallint", "integer", "bigint", "smallserial", "serial", "bigserial"}
_FLOATS = {"numeric", "decimal", "real", "double precision", "money"}


async def _from_contract(session, stored, node, result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    shape = ((result.get("shapes_by_node") or {}).get(node.name) or {}).get("outputs", {}).get("main")
    if not sh.is_unknown(shape):
        if node.type == "HttpRequest" and isinstance(shape.get("properties"), dict) and "body" in shape["properties"]:
            http: dict[str, Any] = {"status": 200}
            data = example_from_shape(shape["properties"]["body"], 1, "body")
            if data is not None:
                http["body"] = data
            return {"http": http}, "contract"
        return {"items": [example_from_shape(shape, 1)]}, "contract"
    if node.type == "Postgres":
        shape = await _postgres_shape(session, stored, node)
        return {"items": [example_from_shape(shape, 1)]}, "database"
    raise _conflict(f"'{node.name}' has no known output type yet", PASTE_HINT)


async def _postgres_shape(session, stored, node) -> dict[str, Any]:
    params = node.parameters or {}
    conn = params.get("connectionString")
    if not isinstance(conn, str) or not conn.strip() or "{{" in conn:
        raise _conflict("Can't read the table: the connection string isn't a plain value", PASTE_HINT)
    sql = " ".join(str(params.get(k) or "") for k in ("query", "table"))
    m = _TABLE_RE.search(sql) or re.match(r"^\s*((?:\w+\.)?\w+)\s*$", str(params.get("table") or ""))
    if not m:
        raise _conflict("The query doesn't name a table", PASTE_HINT)
    parts = [p.strip('"') for p in m.group(1).split(".")]
    table, schema = parts[-1], (parts[-2] if len(parts) > 1 else None)
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        from ..broker.policy import host_matches
        from ..services.project_service import resolve_project

        host = conninfo_to_dict(conn).get("host") or ""
        project = await resolve_project(session, stored.project_id)
        allow = list(((getattr(project, "settings", None) or {}).get("egress_allowlist")) or [])
        if not host or (allow and not host_matches(host, allow)):
            raise ValueError("host not allowed")
        async with await psycopg.AsyncConnection.connect(conn, connect_timeout=5) as c:
            async with c.cursor() as cur:
                await cur.execute(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = %s AND (%s::text IS NULL OR table_schema = %s) "
                    "AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY ordinal_position",
                    (table, schema, schema),
                )
                cols = await cur.fetchall()
    except Exception:  # unreachable, bad credentials, ... : never leak the connection string
        raise _conflict("Couldn't read the table definition from the database", PASTE_HINT) from None
    if not cols:
        raise _conflict(f"Table '{table}' wasn't found in the database", PASTE_HINT)
    props: dict[str, Any] = {}
    for name, dtype, nullable in cols:
        d = str(dtype).lower()
        if d in _NUMERIC:
            props[name] = sh.scalar("integer", "declared")
        elif d in _FLOATS:
            props[name] = sh.scalar("number", "declared")
        elif d == "boolean":
            props[name] = sh.scalar("boolean", "declared")
        elif d.startswith("timestamp"):
            props[name] = sh.scalar("string", "declared", format="date-time")
        elif d == "date":
            props[name] = sh.scalar("string", "declared", format="date")
        elif d == "uuid":
            props[name] = sh.scalar("string", "declared", format="uuid")
        elif d in ("json", "jsonb"):
            props[name] = sh.unknown()
        else:
            props[name] = sh.scalar("string", "declared")
    return sh.object_of(props, [], "declared")


# ── last_real ────────────────────────────────────────────────────────────────────────────────


async def _last_real(session, workflow_id: str, node) -> tuple[dict[str, Any], str]:
    stmt = (
        select(NodeOutputModel)
        .join(ExecutionModel, ExecutionModel.id == NodeOutputModel.execution_id)
        .where(
            ExecutionModel.workflow_id == workflow_id,
            ExecutionModel.status == "success",
            ExecutionModel.mode != "test",
            NodeOutputModel.node_name == node.name,
            NodeOutputModel.status == "success",
        )
        .order_by(ExecutionModel.start_time.desc(), NodeOutputModel.run_index.desc())
    )
    for row in (await session.execute(stmt)).scalars():
        items = [i.get("json", {}) for i in (row.output if isinstance(row.output, list) else []) if isinstance(i, dict)]
        if not items:
            continue
        if node.type == "HttpRequest":
            first = items[0]
            if not isinstance(first, dict) or "statusCode" not in first:
                continue
            http: dict[str, Any] = {"status": int(first["statusCode"])}
            if first.get("body") not in (None, {}):
                http["body"] = first["body"]
            if isinstance(first.get("headers"), dict) and first["headers"]:
                http["headers"] = {str(k): str(v) for k, v in first["headers"].items()}
            return {"http": http}, "last_real"
        return {"items": items}, "last_real"
    raise _conflict(f"'{node.name}' has no successful real run to copy", "run the workflow once, or generate from the spec")
