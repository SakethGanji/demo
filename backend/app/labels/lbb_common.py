"""LB-B shared helpers: ids, time, canonical JSON, the caller, row reads (masked) and the one seam to the label core
(LB-A): `append()` and the layer/schema lookups. Everything LB-B writes into `label_events` goes through `append()`."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from . import lbb_schema


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(8)}"


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha(x: Any) -> str:
    return hashlib.sha256((x if isinstance(x, str) else canon(x)).encode()).hexdigest()


def loads(s: Any, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


_DB: Any = None


def db():
    """One Database per process (constructing one runs the schema migration: never per call)."""
    global _DB
    if _DB is None:
        try:
            from ..main import app

            d = getattr(app.state, "db", None)
        except Exception:  # noqa: BLE001
            d = None
        if d is None:
            from .. import frames

            d = frames._db()
        lbb_schema.ensure(d)
        _DB = d
    return _DB


def rows(con: Any, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    cur = con.execute(sql, tuple(params))
    names = [c[0] for c in cur.description] if cur.description else []
    return [dict(zip(names, r)) for r in cur.fetchall()]


def one(con: Any, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    r = rows(con, sql, params)
    return r[0] if r else None


# -- who is calling ---------------------------------------------------------------------------------------------------

def caller(request: Any, required: bool = True) -> str:
    """The principal id behind the request (authz identity), else the typed actor header; never a body field."""
    try:
        from ..authz import deps

        p = deps.principal(request)
        if p is not None:
            return p.id
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        pass
    from ..catalog import governance as G

    a = G.current_actor()
    if a:
        return resolve_principal(a)
    if required:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Say who you are (sign in or X-Ensemble-Actor)."})
    return "system"


def resolve_principal(name_or_id: str) -> str:
    """A typed name or a principal id → the principal id (upserted by identity key, as the typed-name provider does)."""
    from ..catalog.teams_api import ensure_principal

    s = str(name_or_id or "").strip()[:80]
    with db().connect() as con:
        r = one(con, "SELECT id FROM principals WHERE id = ?", (s,))
        if r:
            return r["id"]
        r = ensure_principal(con, s)
    if not r:
        raise HTTPException(400, f"'{s}' is not a usable principal name.")
    return r["id"]


def team_role(con: Any, principal_id: str, team_id: str | None) -> str | None:
    if not team_id:
        return None
    try:
        from ..authz import policy

        return policy.role_of(con, principal_id, team_id)
    except Exception:  # noqa: BLE001
        return None


def team_policy(con: Any, team_id: str | None) -> dict[str, Any]:
    if not team_id:
        return {}
    r = one(con, "SELECT policy FROM teams WHERE id = ?", (team_id,))
    return loads(r["policy"] if r else None, {}) or {}


def audit(con: Any, who: str, action: str, details: dict[str, Any], **cols: Any) -> None:
    from ..catalog.schema import audit as _audit

    _audit(con, who, action, details, **cols)


# -- the layer (read-only on the core tables) -------------------------------------------------------------------------

def layer(con: Any, layer_id: str) -> dict[str, Any]:
    r = one(con, "SELECT * FROM label_layers WHERE id = ?", (layer_id,))
    if not r:
        raise HTTPException(404, f"No label layer '{layer_id}'.")
    return r


def schema(con: Any, layer_id: str, version: int | None = None) -> dict[str, Any] | None:
    q = "SELECT * FROM label_schemas WHERE layer_id = ?" + (" AND version = ?" if version else "") + " ORDER BY version DESC LIMIT 1"
    r = one(con, q, (layer_id, version) if version else (layer_id,))
    if r:
        r["spec_obj"] = loads(r.get("spec"), {}) or {}
    return r


def classes_of(spec: dict[str, Any], column: str) -> list[str] | None:
    """The class list of `column` in a schema spec (§4.1), tolerant of the two shapes in the plan."""
    cols = spec.get("columns")
    if isinstance(cols, dict):
        c = cols.get(column) or {}
        return [str(x) for x in (c.get("classes") or [])] or None
    if isinstance(cols, list):
        for c in cols:
            if c.get("column", c.get("name")) == column:
                return [str(x) for x in (c.get("classes") or [])] or None
    if spec.get("classes"):
        return [str(x) for x in spec["classes"]]
    return None


def layer_team(con: Any, lay: dict[str, Any]) -> str | None:
    return lay.get("team_id")


def append(layer_id: str, events: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    """Write events through the label core (LB-A `labels.append_events`). Each event: {row_id, column, kind, value,
    is_null?, source, task_id?, assignment_id?, refers_to?, confidence?, seen_version_id, provenance?, id?, created_at?}.
    Returns [{id, seq}] in order."""
    from . import core_bridge

    return core_bridge.append(layer_id, events, actor)


def mask_for(version_id: str, table: str | None) -> Any:
    from ..lake import mask as M

    return M.for_version(version_id, table)


def read_rows(version_id: str, table: str | None, row_ids: list[int] | None, columns: list[str] | None, *,
              mask: Any = "masked", limit: int | None = None) -> list[dict[str, Any]]:
    """Rows of a version by `__row_id` (masked for display unless `mask` is an unmasked Mask). Columns not in the
    table are skipped."""
    from .. import frames
    from ..lake import duck
    from ..lake import pages as PG

    src = frames.scan(version_id, table, mask_for=mask if mask != "masked" else mask_for(version_id, table))
    names = list(src.names)
    cols = [c for c in (columns if columns is not None else names) if c in names]
    sel = ", ".join([PG.q(PG.ROW_ID)] + [PG.q(c) for c in cols])
    where = ""
    if row_ids is not None:
        if not row_ids:
            return []
        where = f" WHERE {PG.q(PG.ROW_ID)} IN ({', '.join(str(int(r)) for r in row_ids)})"
    lim = f" LIMIT {int(limit)}" if limit else ""
    df = duck.interactive().query(f"SELECT {sel} FROM {src.rel}{where} ORDER BY {PG.q(PG.ROW_ID)}{lim}", fetch="df", bind=src.bind)
    out = []
    for rec in df.to_dict("records"):
        out.append({k: (None if _isna(v) else (v.item() if hasattr(v, "item") else v)) for k, v in rec.items()})
    return out


def _isna(v: Any) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(v)) if not isinstance(v, (list, dict)) else False
    except (TypeError, ValueError):
        return False


def column_kinds(version_id: str, table: str | None) -> dict[str, str]:
    from ..lake import pages as PG

    src = PG.source(version_id, table)
    return {c: PG.kind(t) for c, t in src.columns}


def version_table(con: Any, version_id: str, lay: dict[str, Any] | None = None) -> str | None:
    if lay and lay.get("table_name"):
        return lay["table_name"]
    r = one(con, "SELECT primary_table FROM datasets WHERE id = ?", (version_id,))
    return r["primary_table"] if r else None


def check_version_of_layer(con: Any, lay: dict[str, Any], version_id: str) -> None:
    r = one(con, "SELECT COALESCE(root_id, id) AS root FROM datasets WHERE id = ?", (version_id,))
    if not r:
        raise HTTPException(404, f"No dataset version '{version_id}'.")
    if r["root"] != lay["root_id"]:
        raise HTTPException(400, f"Version '{version_id}' is not a version of the layer's root '{lay['root_id']}'.")


def split(seed: str, n: int = 1) -> float:
    """A deterministic draw in [0, 1) from a string (splitmix-like via sha256)."""
    return int(hashlib.sha256(seed.encode()).hexdigest()[:13], 16) / float(1 << 52)
