"""API drift alerts and stale tests (Stage 3B, CONTRACTS-STAGE3B §4).

After every non-test execution, ``check_execution`` compares what each outside step (effect ``network`` or
``database``) actually returned with what the workflow expects of it, and records an ``api_drift`` row when they
no longer match. The expected shape is, in order:

1. **spec**: the catalog response for the received ``statusCode`` (HttpRequest with ``api_ref``);
2. **contract**: the node's declared or approved contract (``contract.outputs.main``);
3. **mock**: the shape inferred from the node's mock in ``test_defaults.mocks`` (``http.body`` or ``items``).

Diffs are ``status`` (reply code not documented), ``missing`` (a required field is gone), ``type`` (wrong type or
a broken keyword) and, for basis ``mock`` only, ``extra`` (a field the mock doesn't have: the mock is out of date).

Drift never changes a run: the caller guards every call. Stale tests are derived here and never written into
the workflow definition (locked tests must stay untouched).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import ApiDriftModel, ExecutionModel, NodeOutputModel
from ..engine import shapes as sh
from ..engine.effects import effect_of
from ..engine.test_mode import has_expression, mock_kind

logger = logging.getLogger(__name__)

_WATCHED_EFFECTS = ("network", "database")
_MAX_ITEMS = 50
_MAX_DIFFS = 40


class DriftNotFoundError(Exception):
    def __init__(self, drift_id: str) -> None:
        super().__init__(f"Drift alert not found: {drift_id}")
        self.message = f"Drift alert not found: {drift_id}"


# ── views ────────────────────────────────────────────────────────────────────────────────────


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def summarize(diffs: list[dict[str, Any]]) -> str:
    """A readable one-liner: ``body.decision missing; body.score: expected number, got string``."""
    parts: list[str] = []
    for d in diffs:
        path = d.get("path") or "reply"
        kind = d.get("kind")
        if kind == "missing":
            parts.append(f"{path} missing")
        elif kind == "extra":
            parts.append(f"unexpected {path}")
        elif kind == "status":
            exp = d.get("expected")
            exp_txt = ", ".join(str(x) for x in exp) if isinstance(exp, list) else str(exp)
            parts.append(f"status {d.get('actual')} (expected {exp_txt})")
        else:
            parts.append(f"{path}: expected {d.get('expected')}, got {d.get('actual')}")
    text = "; ".join(parts[:6])
    return text + (f"; and {len(parts) - 6} more" if len(parts) > 6 else "")


def drift_json(row: ApiDriftModel) -> dict[str, Any]:
    return {
        "id": row.id, "project_id": row.project_id, "workflow_id": row.workflow_id, "node_name": row.node_name,
        "api_ref": row.api_ref, "basis": row.basis, "status_code": row.status_code, "diffs": list(row.diffs or []),
        "fingerprint": row.fingerprint, "first_seen": _iso(row.first_seen), "last_seen": _iso(row.last_seen),
        "count": row.count, "last_execution_id": row.last_execution_id, "status": row.status,
        "resolved_by": row.resolved_by, "resolved_at": _iso(row.resolved_at),
        "title": f"{row.node_name} reply changed", "summary": summarize(list(row.diffs or [])),
    }


# ── expected shapes and comparison ───────────────────────────────────────────────────────────


def _fingerprint(diffs: list[dict[str, Any]]) -> str:
    keys = sorted(f"{d.get('path')}:{d.get('kind')}" for d in diffs)
    return hashlib.sha1("|".join(keys).encode()).hexdigest()


def _prune_expressions(value: Any) -> Any:
    """A copy of a mock value without anything that holds ``{{ }}`` (its type is only known at run time)."""
    if isinstance(value, dict):
        return {k: _prune_expressions(v) for k, v in value.items() if not has_expression(v) or isinstance(v, (dict, list))}
    if isinstance(value, list):
        return [_prune_expressions(v) for v in value if not (isinstance(v, str) and has_expression(v))]
    return value


def _first_leaf(mock: Any, depth: int = 0) -> dict[str, Any] | None:
    """The first concrete mock of a (possibly smart) mock."""
    if depth > 10 or not isinstance(mock, dict):
        return None
    if mock_kind(mock) != "rules":
        return mock if mock_kind(mock) in ("items", "http") else None
    candidates = [r.get("then") for r in mock.get("rules") or [] if isinstance(r, dict)] + [mock.get("default")]
    for c in candidates:
        leaf = _first_leaf(c, depth + 1)
        if leaf is not None:
            return leaf
    return None


def _violation_diffs(shape: Any, value: Any, prefix: str, basis: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for v in sh.validate(shape, value, prefix):
        if v["expected"] == "required" and v["actual"] == "missing":
            kind = "missing"
        elif str(v["message"]).startswith("expected "):
            if basis == "mock" and v["expected"] == "integer" and v["actual"] == "number":
                continue  # a mock's 1 doesn't promise a whole number
            kind = "type"
        elif basis == "mock":
            continue  # enums and ranges of a sample are illustrations, not promises
        else:
            kind = "type"
        out.append({"path": v["path"], "kind": kind, "expected": v["expected"], "actual": v["actual"]})
    return out


def _extras(shape: Any, value: Any, path: str, out: list[dict[str, Any]], depth: int = 0) -> None:
    """Fields the actual value has but the (mock) shape doesn't know."""
    if depth > 10 or not isinstance(shape, dict):
        return
    if shape.get("type") == "object" and isinstance(shape.get("properties"), dict) and isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else str(k)
            if k not in shape["properties"]:
                out.append({"path": p, "kind": "extra", "expected": "absent", "actual": sh._type_name(v)})
            else:
                _extras(shape["properties"][k], v, p, out, depth + 1)
    elif shape.get("type") == "array" and isinstance(shape.get("items"), dict) and isinstance(value, list):
        for v in value[:3]:
            _extras(shape["items"], v, f"{path}[]", out, depth + 1)


def _spec_response(op: dict[str, Any], code: int) -> tuple[bool, dict[str, Any] | None]:
    """(documented?, response) of an operation for a status code: exact, then ``2XX``, then ``default``."""
    responses = op.get("responses") or {}
    for key in (str(code), f"{str(code)[0]}XX", f"{str(code)[0]}xx", "default"):
        if key in responses:
            return True, responses[key]
    return (not responses), None


def _compare_item(
    node: dict[str, Any], item: Any, op: dict[str, Any] | None, test_defaults: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]] | None:
    """(basis, diffs) for one emitted item, or None when nothing is expected of it / it matches."""
    if not isinstance(item, dict) or "_errorNode" in item:
        return None
    is_http = isinstance(item.get("statusCode"), int) and "body" in item
    code = item["statusCode"] if is_http else None

    # 1. the API catalog
    if op is not None and is_http:
        documented, resp = _spec_response(op, code)
        if not documented:
            docs = sorted(int(k) for k in (op.get("responses") or {}) if str(k).isdigit())
            return "spec", [{"path": "statusCode", "kind": "status", "expected": docs, "actual": code}]
        shape = (resp or {}).get("shape")
        if isinstance(shape, dict) and not sh.is_unknown(shape):
            diffs = _violation_diffs(shape, item.get("body"), "body", "spec")
            return ("spec", diffs) if diffs else None

    # 2. a declared or approved contract
    override = node.get("contract")
    main = ((override or {}).get("outputs") or {}).get("main") if isinstance(override, dict) else None
    if isinstance(main, dict) and not sh.is_unknown(main):
        diffs = _violation_diffs(main, item, "", "contract")
        return ("contract", diffs) if diffs else None

    # 3. the node's default mock
    leaf = _first_leaf((test_defaults.get("mocks") or {}).get(node.get("name")))
    if leaf is None:
        return None
    if mock_kind(leaf) == "http":
        spec = leaf.get("http") or {}
        if is_http and int(spec.get("status", 200)) != code:
            return "mock", [{"path": "statusCode", "kind": "status", "expected": [int(spec.get("status", 200))], "actual": code}]
        body = _prune_expressions(spec.get("body"))
        if not is_http or body is None or not isinstance(body, (dict, list)):
            return None
        expected, actual, prefix = sh.from_sample([body]), item.get("body"), "body"
    else:
        raw = [_prune_expressions(i) for i in leaf.get("items") or [] if isinstance(i, dict)]
        if not raw:
            return None
        expected, actual, prefix = sh.from_sample(raw[:1]), item, ""
    if sh.is_unknown(expected):
        return None
    diffs = _violation_diffs(expected, actual, prefix, "mock")
    extras: list[dict[str, Any]] = []
    _extras(expected, actual, prefix, extras)
    diffs += extras
    return ("mock", diffs) if diffs else None


# ── detection ────────────────────────────────────────────────────────────────────────────────


async def check_outputs(
    session: AsyncSession, *, project_id: str, workflow_id: str, definition: dict[str, Any],
    outputs: dict[str, list[Any]], execution_id: str | None, only_nodes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Compare ``outputs`` (``{node: [item json]}``) with what the workflow expects; upsert and return the drift rows touched."""
    from .api_catalog import project_operations

    nodes = {n["name"]: n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")}
    test_defaults = definition.get("test_defaults") or {}
    ops: dict[str, dict[str, dict[str, Any]]] | None = None
    touched: list[dict[str, Any]] = []
    for name, items in outputs.items():
        node = nodes.get(name)
        if node is None or node.get("disabled") or node.get("pinned_data") or not items:
            continue
        if only_nodes is not None and name not in only_nodes:
            continue
        if effect_of(str(node.get("type"))) not in _WATCHED_EFFECTS:
            continue
        api_ref = (node.get("parameters") or {}).get("api_ref") if node.get("type") == "HttpRequest" else None
        op = None
        if isinstance(api_ref, dict) and api_ref.get("spec_id"):
            if ops is None:
                ops = await project_operations(session, project_id)
            op = (ops.get(api_ref["spec_id"]) or {}).get(api_ref.get("operation_id"))
        by_basis: dict[str, dict[str, Any]] = {}
        for item in items[:_MAX_ITEMS]:
            found = _compare_item(node, item, op, test_defaults)
            if found is None:
                continue
            basis, diffs = found
            entry = by_basis.setdefault(basis, {"diffs": {}, "code": None})
            if entry["code"] is None and isinstance(item, dict):
                entry["code"] = item.get("statusCode")
            for d in diffs:
                entry["diffs"].setdefault((d["path"], d["kind"]), d)
        for basis, entry in by_basis.items():
            diffs = list(entry["diffs"].values())[:_MAX_DIFFS]
            row = await _upsert(session, project_id, workflow_id, name, api_ref if isinstance(api_ref, dict) else None,
                                basis, entry["code"], diffs, execution_id)
            touched.append(drift_json(row))
    return touched


async def _upsert(
    session: AsyncSession, project_id: str, workflow_id: str, node_name: str, api_ref: dict[str, Any] | None,
    basis: str, status_code: int | None, diffs: list[dict[str, Any]], execution_id: str | None,
) -> ApiDriftModel:
    fp = _fingerprint(diffs)
    for attempt in (0, 1):
        stmt = select(ApiDriftModel).where(
            ApiDriftModel.workflow_id == workflow_id, ApiDriftModel.node_name == node_name, ApiDriftModel.fingerprint == fp)
        row = (await session.execute(stmt)).scalar_one_or_none()
        now = datetime.now()
        if row is not None:
            values: dict[str, Any] = {
                "count": ApiDriftModel.count + 1,  # evaluated by the database: parallel runs never lose a bump
                "last_seen": now, "diffs": diffs, "basis": basis, "status_code": status_code, "api_ref": api_ref,
                "last_execution_id": execution_id or row.last_execution_id,
            }
            if row.status == "resolved":
                values.update(status="open", resolved_by=None, resolved_at=None)
            await session.execute(update(ApiDriftModel).where(ApiDriftModel.id == row.id).values(**values))
            await session.commit()
            await session.refresh(row)
            return row
        row = ApiDriftModel(
            id="drift_" + uuid.uuid4().hex, project_id=project_id, workflow_id=workflow_id, node_name=node_name,
            api_ref=api_ref, basis=basis, status_code=status_code, diffs=diffs, fingerprint=fp, first_seen=now,
            last_seen=now, count=1, last_execution_id=execution_id, status="open",
        )
        session.add(row)
        try:
            await session.commit()
            return row
        except IntegrityError:  # a parallel run inserted the same fingerprint first: bump it instead
            await session.rollback()
            if attempt:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover


async def check_execution(session: AsyncSession, execution_id: str) -> list[dict[str, Any]]:
    """Drift check of one finished execution (any mode except ``test``). Returns the drift rows it opened or bumped."""
    from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition

    execution = await session.get(ExecutionModel, execution_id)
    if execution is None or execution.mode == "test":
        return []
    stored = await WorkflowRepository(session).get(execution.workflow_id)
    if stored is None:
        return []
    definition = workflow_to_definition(stored.workflow)
    watched = {n["name"] for n in definition.get("nodes") or []
               if isinstance(n, dict) and effect_of(str(n.get("type"))) in _WATCHED_EFFECTS}
    if not watched:
        return []
    rows = (await session.execute(select(NodeOutputModel).where(
        NodeOutputModel.execution_id == execution_id, NodeOutputModel.node_name.in_(watched),
        NodeOutputModel.status == "success"))).scalars().all()
    outputs = {r.node_name: [o.get("json") for o in (r.output or []) if isinstance(o, dict)] for r in rows}
    return await check_outputs(
        session, project_id=execution.project_id or stored.project_id, workflow_id=execution.workflow_id,
        definition=definition, outputs=outputs, execution_id=execution_id)


# ── queries ──────────────────────────────────────────────────────────────────────────────────


async def list_for_workflow(session: AsyncSession, workflow_id: str, status: str = "open") -> list[ApiDriftModel]:
    stmt = select(ApiDriftModel).where(ApiDriftModel.workflow_id == workflow_id)
    if status != "all":
        stmt = stmt.where(ApiDriftModel.status == "open")
    stmt = stmt.order_by(ApiDriftModel.last_seen.desc(), ApiDriftModel.id)
    return list((await session.execute(stmt)).scalars().all())


async def list_for_project(session: AsyncSession, project_id: str) -> list[ApiDriftModel]:
    stmt = select(ApiDriftModel).where(ApiDriftModel.project_id == project_id, ApiDriftModel.status == "open")
    return list((await session.execute(stmt.order_by(ApiDriftModel.last_seen.desc(), ApiDriftModel.id))).scalars().all())


async def open_nodes(session: AsyncSession, workflow_id: str) -> set[str]:
    """Names of the nodes of a workflow that have an open drift alert."""
    stmt = select(ApiDriftModel.node_name).where(ApiDriftModel.workflow_id == workflow_id, ApiDriftModel.status == "open")
    return set((await session.execute(stmt)).scalars().all())


async def resolve(session: AsyncSession, workflow_id: str, drift_id: str, by: str) -> ApiDriftModel:
    row = await session.get(ApiDriftModel, drift_id)
    if row is None or row.workflow_id != workflow_id:
        raise DriftNotFoundError(drift_id)
    row.status, row.resolved_by, row.resolved_at = "resolved", by, datetime.now()
    await session.commit()
    return row


# ── stale tests ──────────────────────────────────────────────────────────────────────────────


def stale_test_ids(definition: dict[str, Any], drifted_nodes: set[str]) -> list[str]:
    """Ids of the tests whose mocks (their own or the workflow's defaults) cover a node with an open drift alert."""
    if not drifted_nodes:
        return []
    default_mocks = set(((definition.get("test_defaults") or {}).get("mocks") or {}).keys())
    out: list[str] = []
    for t in definition.get("tests") or []:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        mocked = default_mocks | set((t.get("mocks") or {}).keys())
        if mocked & drifted_nodes:
            out.append(t["id"])
    return out


async def stale_ids_for(session: AsyncSession, workflow_id: str, definition: dict[str, Any]) -> list[str]:
    return stale_test_ids(definition, await open_nodes(session, workflow_id))


async def guarded_check_execution(session: AsyncSession, execution_id: str) -> None:
    """``check_execution`` that can never raise (the run's outcome must not depend on drift detection)."""
    try:
        await check_execution(session, execution_id)
    except Exception:
        logger.warning("drift check failed for execution %s", execution_id, exc_info=True)
        try:
            await session.rollback()
        except Exception:
            pass
