"""P21 (C §4.5, plan §2.1 `data_contracts`): data contracts per dataset.

A contract belongs to the dataset (the root) and is versioned: every PUT or freeze writes version n+1 and deactivates
the others (audited `contract_set`). The spec names tables, columns and relations:

    {"tables": {"applications": {"required": true, "key": ["app_id"], "rows": {"min": 100},
                                 "columns": {"app_id": {"type": "string", "nullable": false, "unique": true},
                                             "income": {"type": "number", "max_null_share": 0.02, "range": [0, 1e7]},
                                             "y": {"type": "int", "values": [0, 1]}},
                                 "allow_new_columns": "warn", "on_removed_column": "block", "on_type_change": "block"}},
     "relations": [{"from": "bureau.app_id", "to": "applications.app_id", "min_coverage": 0.95}],
     "severity_default": "warn"}

Each rule is `warn` or `block` (a column rule may carry its own `severity`; else the table's `on_*`, else
`severity_default`). Rules run in DuckDB on the tables as the version holds them: at the upload check step (a `block`
fails the check, so the commit answers 409 `check_blocked`), on a version (`POST …/contract/check`, which stores
`datasets.contract_result`), and on a certification request and decision. Results carry counts, never values (a
column may be PII-tagged).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, require
from . import governance as G
from .schema import audit, now

router = APIRouter(prefix="/api")

SEVERITIES = ("warn", "block")
TYPES = ("int", "number", "string", "bool", "date", "timestamp")
ROW_ID = "__row_id"
MAX_SPEC = 200_000


from ..lake.writer import LakeError


class ContractError(LakeError):
    """A LakeError, so the upload routes (`_lthread`) answer it as {code, message} too."""

    def http(self) -> HTTPException:
        return HTTPException(self.status, {"code": self.code, "message": self.message, **self.extra})


# --------------------------------------------------------------------------- spec


def kind_of(duck_type: str) -> str:
    t = str(duck_type).upper()
    if any(x in t for x in ("INT",)) and "INTERVAL" not in t:
        return "int"
    if any(x in t for x in ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC")):
        return "number"
    if t.startswith("BOOL"):
        return "bool"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t == "DATE":
        return "date"
    return "string"


def _compatible(want: str, have: str) -> bool:
    return want == have or (want == "number" and have == "int")


def _sev(v: Any, where: str) -> str:
    if v not in SEVERITIES:
        raise ContractError(400, "contract_invalid", f"{where}: severity is 'warn' or 'block', not {v!r}.")
    return v


def validate(spec: Any) -> dict[str, Any]:
    """The spec, checked (400 contract_invalid naming the place)."""
    if not isinstance(spec, dict) or not isinstance(spec.get("tables"), dict) or not spec["tables"]:
        raise ContractError(400, "contract_invalid", "A contract is {tables: {<table>: {columns: {...}}}, relations?, severity_default?}.")
    if len(json.dumps(spec, default=str)) > MAX_SPEC:
        raise ContractError(400, "contract_invalid", "The contract is too large (200 kB at most).")
    _sev(spec.get("severity_default", "warn"), "severity_default")
    for tn, t in spec["tables"].items():
        if not isinstance(t, dict):
            raise ContractError(400, "contract_invalid", f"tables.{tn} must be an object.")
        for k in ("on_removed_column", "on_type_change", "on_missing_table"):
            if k in t:
                _sev(t[k], f"tables.{tn}.{k}")
        if "allow_new_columns" in t and t["allow_new_columns"] not in ("allow", "warn", "block"):
            raise ContractError(400, "contract_invalid", f"tables.{tn}.allow_new_columns is allow, warn or block.")
        if "key" in t and not (isinstance(t["key"], list) and t["key"] and all(isinstance(c, str) for c in t["key"])):
            raise ContractError(400, "contract_invalid", f"tables.{tn}.key is a list of column names.")
        rows = t.get("rows")
        if rows is not None and not (isinstance(rows, dict) and all(k in ("min", "max", "severity") for k in rows)):
            raise ContractError(400, "contract_invalid", f"tables.{tn}.rows is {{min?, max?, severity?}}.")
        cols = t.get("columns") or {}
        if not isinstance(cols, dict):
            raise ContractError(400, "contract_invalid", f"tables.{tn}.columns must be an object.")
        for cn, c in cols.items():
            w = f"tables.{tn}.columns.{cn}"
            if not isinstance(c, dict):
                raise ContractError(400, "contract_invalid", f"{w} must be an object.")
            if "type" in c and c["type"] not in TYPES:
                raise ContractError(400, "contract_invalid", f"{w}.type is one of {', '.join(TYPES)}.")
            if "severity" in c:
                _sev(c["severity"], w)
            if "max_null_share" in c and not (isinstance(c["max_null_share"], (int, float)) and 0 <= c["max_null_share"] <= 1):
                raise ContractError(400, "contract_invalid", f"{w}.max_null_share is a share between 0 and 1.")
            if "range" in c and not (isinstance(c["range"], list) and len(c["range"]) == 2
                                     and all(x is None or isinstance(x, (int, float)) for x in c["range"])):
                raise ContractError(400, "contract_invalid", f"{w}.range is [low, high] (numbers or null).")
            if "values" in c and not (isinstance(c["values"], list) and len(c["values"]) <= 1000):
                raise ContractError(400, "contract_invalid", f"{w}.values is a list (1,000 at most).")
    for i, r in enumerate(spec.get("relations") or []):
        if not (isinstance(r, dict) and isinstance(r.get("from"), str) and isinstance(r.get("to"), str)
                and "." in r["from"] and "." in r["to"]):
            raise ContractError(400, "contract_invalid", f"relations[{i}] is {{from: 'table.col', to: 'table.col', min_coverage?}}.")
        if "severity" in r:
            _sev(r["severity"], f"relations[{i}]")
    return spec


def root_of(con: Any, dataset_id: str) -> str:
    r = con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if not r:
        raise ContractError(404, "not_found", f"No dataset '{dataset_id}'.")
    return r[0]


def active(con: Any, root: str) -> dict[str, Any] | None:
    r = con.execute("SELECT * FROM data_contracts WHERE root_id = ? AND active = 1 ORDER BY version DESC LIMIT 1", (root,)).fetchone()
    return _out(dict(r)) if r else None


def _out(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "spec": json.loads(r["spec"]) if isinstance(r.get("spec"), str) else r.get("spec"), "active": bool(r.get("active"))}


def put(db: Any, dataset_id: str, spec: Any, by: str, reason: str = "", source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write contract version n+1 for the dataset (the root of `dataset_id`) and make it the active one. Audited."""
    spec = validate(spec)
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        root = root_of(con, dataset_id)
        prev = active(con, root)
        n = int(con.execute("SELECT COALESCE(MAX(version), 0) FROM data_contracts WHERE root_id = ?", (root,)).fetchone()[0]) + 1
        cid = "dc_" + uuid.uuid4().hex[:10]
        con.execute("UPDATE data_contracts SET active = 0 WHERE root_id = ? AND active = 1", (root,))
        con.execute("INSERT INTO data_contracts (id, root_id, version, spec, created_by, created_at, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (cid, root, n, json.dumps(spec, sort_keys=True, default=str), by, now()))
        audit(con, by, "contract_set", {"dataset_id": dataset_id, "root_id": root, "contract_id": cid, "contract_version": n,
                                        "previous": {"id": prev["id"], "version": prev["version"]} if prev else None,
                                        "reason": reason, **(source or {})},
              comment=reason, object_kind="dataset", object_id=root)
        return active(con, root)  # type: ignore[return-value]


# --------------------------------------------------------------------------- relations of a version / of an upload


def version_rels(db: Any, version_id: str) -> dict[str, str]:
    """{table: DuckDB relation SQL} of a committed Parquet version."""
    from .. import frames

    with db.connect() as con:
        v = con.execute("SELECT storage FROM datasets WHERE id = ?", (version_id,)).fetchone()
        if not v:
            raise ContractError(404, "not_found", f"No dataset version '{version_id}'.")
        if v[0] != "parquet":
            raise ContractError(409, "not_parquet", f"{version_id} is a CSV-era version: contracts run on Parquet versions "
                                                    "(migrate it first, P10).")
        names = [r[0] for r in con.execute("SELECT table_name FROM version_tables WHERE version_id = ? ORDER BY ordinal", (version_id,))]
    return {n: f"(SELECT * EXCLUDE ({ROW_ID}) FROM {frames.lake_sql(frames.lake_table(frames.Table(version_id, n)))})" for n in names}


def upload_rels(db: Any, store: Any, up: str, out: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
    """(root, {table: relation}) of the version an upload would make, or (None, {}) for a new dataset / a blocked check."""
    from .. import frames
    from ..lake import upload as U

    with db.connect() as con:
        u = con.execute("SELECT parent_version_id, root_id FROM uploads WHERE id = ?", (up,)).fetchone()
    pvid = (u["parent_version_id"] if u else None) or (out.get("next_version") or {}).get("parent_version_id")
    if not pvid:
        return None, {}
    with db.connect() as con:
        root = root_of(con, pvid)
        ptabs = [(r[0], r[1]) for r in con.execute("SELECT table_name, ordinal FROM version_tables WHERE version_id = ? ORDER BY ordinal",
                                                   (pvid,))]
        prim = con.execute("SELECT primary_table FROM datasets WHERE id = ?", (pvid,)).fetchone()[0]
    tmp = store.tmp_dir(up)
    rels: dict[str, str] = {}
    if out.get("kind") == "multi":
        for t in out.get("tables") or []:
            if t.get("typed"):
                rels[t["name"]] = U._rel_sql(db, tmp, pvid, t, t["name"])
        for n in out.get("carried") or []:
            rels[n] = U._rel_sql(db, tmp, pvid, None, n)
        return root, rels
    if not out.get("typed"):
        return root, {}
    plan_table = ((out.get("next_version") or {}).get("table")) or None
    with db.connect() as con:
        pl = con.execute("SELECT plan_json FROM uploads WHERE id = ?", (up,)).fetchone()
    try:
        plan_table = (json.loads(pl[0]) if pl and pl[0] else {}).get("table") or plan_table
    except ValueError:
        pass
    target = plan_table if plan_table in [n for n, _ in ptabs] else prim
    for n, _ in ptabs:
        if n == target:
            rels[n] = f"(SELECT * FROM read_parquet('{str(tmp / out['typed']).replace(chr(39), chr(39) * 2)}'))"
        else:
            rels[n] = f"(SELECT * EXCLUDE ({ROW_ID}) FROM {frames.lake_sql(frames.lake_table(frames.Table(pvid, n)))})"
    return root, rels


# --------------------------------------------------------------------------- evaluation


def _q(c: str) -> str:
    return '"' + str(c).replace('"', '""') + '"'


def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def evaluate(contract: dict[str, Any], rels: dict[str, str]) -> dict[str, Any]:
    """Run every rule of the contract on the tables (DuckDB). {status: pass|warn|block, n_blocks, n_warnings, rules[]}."""
    from ..lake import duck

    spec = contract["spec"]
    dflt = spec.get("severity_default", "warn")
    rules: list[dict[str, Any]] = []

    def rule(name: str, table: str, sev: str, ok: bool, message: str, column: str | None = None, **nums: Any) -> None:
        rules.append({"rule": name, "table": table, **({"column": column} if column else {}), "severity": sev,
                      "ok": bool(ok), "message": message, **nums})

    with duck.job() as con:
        schemas: dict[str, dict[str, str]] = {}
        for tn, rel in rels.items():
            schemas[tn] = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall() if r[0] != ROW_ID}
        for tn, t in spec["tables"].items():
            if tn not in rels:
                sev = t.get("on_missing_table", "block") if t.get("required", True) else "warn"
                rule("table_present", tn, sev, False, f"Table '{tn}' is not in this version.")
                continue
            rel, have = rels[tn], schemas[tn]
            cols = t.get("columns") or {}
            n = int(con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0])
            rule("table_present", tn, t.get("on_missing_table", "block"), True, f"Table '{tn}' is present ({n:,} rows).", n_rows=n)
            rows = t.get("rows") or {}
            if rows.get("min") is not None or rows.get("max") is not None:
                lo, hi = rows.get("min"), rows.get("max")
                ok = (lo is None or n >= lo) and (hi is None or n <= hi)
                rule("rows", tn, rows.get("severity", dflt), ok, f"{n:,} rows" + ("" if ok else f", outside [{lo}, {hi}]") + ".",
                     n_rows=n)
            removed = [c for c in cols if c not in have]
            for c in removed:
                rule("column_present", tn, cols[c].get("severity", t.get("on_removed_column", dflt)), False,
                     f"Column '{c}' was removed.", column=c)
            new = [c for c in have if c not in cols]
            how = t.get("allow_new_columns", "warn")
            if new and how != "allow":
                rule("new_columns", tn, "block" if how == "block" else "warn", False,
                     f"{len(new)} column(s) not in the contract: {', '.join(new[:20])}.", columns=new[:50])
            for c, cs in cols.items():
                if c not in have:
                    continue
                sev = cs.get("severity", dflt)
                k = kind_of(have[c])
                if cs.get("type"):
                    ok = _compatible(cs["type"], k)
                    rule("type", tn, cs.get("severity", t.get("on_type_change", dflt)), ok,
                         f"'{c}' is {k}" + ("" if ok else f", the contract says {cs['type']}") + ".", column=c,
                         expected=cs["type"], actual=k)
                qc = _q(c)
                agg = [f"COUNT({qc})"]
                if cs.get("unique"):
                    agg.append(f"COUNT(DISTINCT {qc})")
                rng = cs.get("range")
                if rng and k in ("int", "number"):
                    conds = ([f"{qc} < {_lit(rng[0])}"] if rng[0] is not None else []) + ([f"{qc} > {_lit(rng[1])}"] if rng[1] is not None else [])
                    agg.append(f"COUNT(*) FILTER (WHERE {' OR '.join(conds)})" if conds else "0")
                vals = cs.get("values")
                if vals is not None:  # compared as text: 0 / 1, true / false, codes
                    allowed = ", ".join(_lit(str(v).lower() if isinstance(v, bool) else str(v)) for v in vals) or "NULL"
                    agg.append(f"COUNT(*) FILTER (WHERE {qc} IS NOT NULL AND CAST({qc} AS VARCHAR) NOT IN ({allowed}))")
                res = list(con.execute(f"SELECT {', '.join(agg)} FROM {rel}").fetchone())
                non_null = int(res.pop(0))
                nulls = n - non_null
                if cs.get("nullable") is False:
                    rule("not_null", tn, sev, nulls == 0, f"'{c}' has {nulls:,} missing value(s)." if nulls else f"'{c}' has no missing value.",
                         column=c, n_null=nulls)
                if cs.get("max_null_share") is not None:
                    share = nulls / n if n else 0.0
                    ok = share <= float(cs["max_null_share"])
                    rule("max_null_share", tn, sev, ok, f"'{c}' is {share:.2%} missing (at most {float(cs['max_null_share']):.2%}).",
                         column=c, null_share=round(share, 6))
                if cs.get("unique"):
                    dist = int(res.pop(0))
                    rule("unique", tn, sev, dist == non_null, f"'{c}' has {non_null - dist:,} repeated value(s)." if dist != non_null
                         else f"'{c}' is unique.", column=c, n_repeated=non_null - dist)
                if rng and k in ("int", "number"):
                    bad = int(res.pop(0))
                    rule("range", tn, sev, bad == 0, f"{bad:,} value(s) of '{c}' are outside the range." if bad else
                         f"'{c}' is within the range.", column=c, n_outside=bad)
                if vals is not None:
                    bad = int(res.pop(0))
                    rule("values", tn, sev, bad == 0, f"{bad:,} value(s) of '{c}' are not in the allowed list." if bad else
                         f"'{c}' takes only the allowed values.", column=c, n_outside=bad)
            key = t.get("key")
            if key:
                miss = [c for c in key if c not in have]
                if miss:
                    rule("key", tn, t.get("on_removed_column", dflt), False, f"Key column(s) missing: {', '.join(miss)}.", columns=key)
                else:
                    kc = ", ".join(_q(c) for c in key)
                    dup = int(con.execute(f"SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) AS n FROM {rel} GROUP BY {kc}"
                                          " HAVING COUNT(*) > 1)").fetchone()[0])
                    knull = int(con.execute(f"SELECT COUNT(*) FROM {rel} WHERE " + " OR ".join(f"{_q(c)} IS NULL" for c in key))
                                .fetchone()[0])
                    rule("key", tn, t.get("key_severity", "block"), dup == 0 and knull == 0,
                         f"Key ({', '.join(key)}): {dup:,} duplicate(s), {knull:,} missing." if dup or knull else
                         f"Key ({', '.join(key)}) is unique and complete.", columns=key, n_duplicates=dup, n_null=knull)
        for r in spec.get("relations") or []:
            (ft, fc), (tt, tc) = r["from"].split(".", 1), r["to"].split(".", 1)
            sev = r.get("severity", dflt)
            name = f"{r['from']} → {r['to']}"
            if ft not in rels or tt not in rels or fc not in schemas[ft] or tc not in schemas[tt]:
                rule("relation", ft, sev, False, f"{name}: a table or column is missing.", relation=name)
                continue
            tot, hit = con.execute(f"SELECT COUNT(*), COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM {rels[tt]} b WHERE b.{_q(tc)} ="
                                   f" a.{_q(fc)})) FROM {rels[ft]} a WHERE a.{_q(fc)} IS NOT NULL").fetchone()
            cov = hit / tot if tot else 1.0
            mc = float(r.get("min_coverage", 1.0))
            rule("relation", ft, sev, cov >= mc, f"{name}: coverage {cov:.2%} (at least {mc:.2%}).", relation=name,
                 coverage=round(cov, 6))
    failed = [r for r in rules if not r["ok"]]
    blocks = [r for r in failed if r["severity"] == "block"]
    return {"contract_id": contract["id"], "contract_version": contract["version"], "checked_at": now(),
            "status": "block" if blocks else "warn" if failed else "pass", "n_rules": len(rules),
            "n_blocks": len(blocks), "n_warnings": len(failed) - len(blocks), "rules": rules}


def evaluate_version(db: Any, version_id: str, contract: dict[str, Any] | None = None, store_result: bool = True) -> dict[str, Any] | None:
    """The active contract (or `contract`) on a committed version; stored on datasets.contract_result. None: no contract."""
    with db.connect() as con:
        root = root_of(con, version_id)
        c = contract or active(con, root)
    if c is None:
        return None
    res = evaluate(c, version_rels(db, version_id))
    if store_result:
        with db.connect() as con:
            con.execute("UPDATE datasets SET contract_result = ? WHERE id = ?", (json.dumps(res, default=str), version_id))
    return res


def check_upload(db: Any, store: Any, up: str, out: dict[str, Any]) -> dict[str, Any]:
    """The upload check step (C §2.4): the dataset's active contract on the version the upload would make. A failed
    `block` rule is added to `blocks` (code contract_block), a failed `warn` rule to `warnings`; the check is stored
    again with them, so the commit answers 409 check_blocked. `contract` on the check names the contract version."""
    if out.get("blocks") and not out.get("tables"):
        return out
    try:
        root, rels = upload_rels(db, store, up, out)
    except ContractError:
        return out
    if root is None:
        out["contract"] = None
        return _store_check(db, up, out)
    with db.connect() as con:
        c = active(con, root)
    if c is None:
        out["contract"] = None
        return _store_check(db, up, out)
    if not rels:
        out["contract"] = {"contract_id": c["id"], "contract_version": c["version"], "status": "not_evaluated",
                           "message": "the file is blocked: the contract runs once the blocks are fixed"}
        return _store_check(db, up, out)
    res = evaluate(c, rels)
    out["contract"] = res
    for r in res["rules"]:
        if r["ok"]:
            continue
        item = {"code": "contract_block" if r["severity"] == "block" else "contract_warning", "table": r["table"],
                **({"column": r["column"]} if r.get("column") else {}), "rule": r["rule"],
                "message": f"Contract v{c['version']}: {r['message']}"}
        (out.setdefault("blocks", []) if r["severity"] == "block" else out.setdefault("warnings", [])).append(item)
    for t in out.get("tables") or []:
        mine = [r for r in res["rules"] if r["table"] == t.get("name") and not r["ok"]]
        t["contract"] = "block" if any(r["severity"] == "block" for r in mine) else "warn" if mine else "pass"
    return _store_check(db, up, out)


def _store_check(db: Any, up: str, out: dict[str, Any]) -> dict[str, Any]:
    import hashlib

    body = {k: v for k, v in out.items() if k not in ("seconds", "check_sha256")}
    for k in ("contract",):  # checked_at changes on every check: not part of the identity
        if isinstance(body.get(k), dict):
            body[k] = {a: b for a, b in body[k].items() if a != "checked_at"}
    out["check_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    with db.connect() as con:
        con.execute("UPDATE uploads SET check_json = ?, check_sha256 = ? WHERE id = ? AND status = 'checked'",
                    (json.dumps(out, default=str), out["check_sha256"], up))
    return out


def commit_guard(db: Any, up: str) -> None:
    """Before an upload commits: the contract it was checked against must still be the active one (a contract set after
    the check makes the check stale: 409 check_stale, check again)."""
    with db.connect() as con:
        u = con.execute("SELECT status, check_json, parent_version_id FROM uploads WHERE id = ?", (up,)).fetchone()
        if not u or u["status"] != "checked" or not u["check_json"] or not u["parent_version_id"]:
            return
        try:
            chk = json.loads(u["check_json"])
        except ValueError:
            return
        r = con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (u["parent_version_id"],)).fetchone()
        c = active(con, r[0]) if r else None
    had = (chk.get("contract") or {}).get("contract_id") if isinstance(chk.get("contract"), dict) else None
    if c is not None and c["id"] != had:
        raise ContractError(409, "check_stale", f"The dataset's contract changed (now v{c['version']}) after this upload was "
                                                "checked; check again.")


def record_commit(db: Any, up: str, version_id: str) -> None:
    """The committed version keeps the contract result its check computed (datasets.contract_result)."""
    with db.connect() as con:
        u = con.execute("SELECT check_json FROM uploads WHERE id = ?", (up,)).fetchone()
        try:
            res = (json.loads(u[0]) if u and u[0] else {}).get("contract")
        except ValueError:
            res = None
        if isinstance(res, dict) and res.get("rules") is not None:
            con.execute("UPDATE datasets SET contract_result = ? WHERE id = ? AND contract_result IS NULL",
                        (json.dumps(res, default=str), version_id))


# --------------------------------------------------------------------------- freeze


def freeze(db: Any, version_id: str, opts: dict[str, Any]) -> dict[str, Any]:
    """A contract that says "the dataset looks like this version": every table required, its columns with their kinds
    (and nullable: false where the version has no missing value), its valid keys; removed columns and type changes
    block, new columns warn (each overridable in `opts`)."""
    from ..lake import duck

    rels = version_rels(db, version_id)
    with db.connect() as con:
        keys: dict[str, list[str]] = {}
        for k in con.execute("SELECT table_name, columns_json FROM table_keys WHERE version_id = ? AND valid = 1 AND kind = 'primary'",
                             (version_id,)):
            keys.setdefault(k[0], json.loads(k[1]))
        rels_decl = [dict(r) for r in con.execute("SELECT * FROM table_relations WHERE version_id = ?", (version_id,))]
    tables: dict[str, Any] = {}
    with duck.job() as con:
        for tn, rel in rels.items():
            desc = [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall() if r[0] != ROW_ID]
            agg = ", ".join(f"COUNT({_q(c)})" for c, _ in desc)
            row = con.execute(f"SELECT COUNT(*){', ' + agg if agg else ''} FROM {rel}").fetchone()
            n = int(row[0])
            cols = {}
            for (c, t), nn in zip(desc, row[1:]):
                cols[c] = {"type": kind_of(t), **({"nullable": False} if int(nn) == n and n else {})}
            tables[tn] = {"required": True, "columns": cols, **({"key": keys[tn]} if tn in keys else {}),
                          "allow_new_columns": opts.get("allow_new_columns", "warn"),
                          "on_removed_column": opts.get("on_removed_column", "block"),
                          "on_type_change": opts.get("on_type_change", "block")}
    relations = []
    for r in rels_decl:
        fc, tc = json.loads(r["from_cols_json"]), json.loads(r["to_cols_json"])
        if len(fc) == 1 and len(tc) == 1:
            relations.append({"from": f"{r['from_table']}.{fc[0]}", "to": f"{r['to_table']}.{tc[0]}", "min_coverage": 1.0})
    return {"tables": tables, "relations": relations, "severity_default": opts.get("severity_default", "warn"),
            "frozen_from": version_id}


# --------------------------------------------------------------------------- routes


def _by(body: dict[str, Any]) -> str:
    from .api import _who

    return _who((body.get("by") or G.current_actor() or "").strip())


async def _run(fn, *a):
    try:
        return await run_in_threadpool(fn, *a)
    except ContractError as e:
        raise e.http() from e


@router.get("/datasets/{dataset_id}/contract", dependencies=[require("data.read_meta", path("dataset_id", "dataset"))])
async def get_contract(dataset_id: str, request: Request) -> dict[str, Any]:
    """P21: the dataset's active contract and every earlier version (newest first)."""
    db = request.app.state.db

    def go() -> dict[str, Any]:
        with db.connect() as con:
            root = root_of(con, dataset_id)
            hist = [_out(dict(r)) for r in con.execute("SELECT * FROM data_contracts WHERE root_id = ? ORDER BY version DESC", (root,))]
        return {"root_id": root, "active": next((h for h in hist if h["active"]), None),
                "versions": [{k: h[k] for k in ("id", "version", "created_by", "created_at", "active")} for h in hist]}

    return await _run(go)


@router.put("/datasets/{dataset_id}/contract", dependencies=[require("data.contract", path("dataset_id", "dataset"))])
async def put_contract(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """P21: {spec, reason?, by?}: a new contract version (the previous one stays, inactive). Audited contract_set."""
    body = body if isinstance(body, dict) else {}
    return await _run(put, request.app.state.db, dataset_id, body.get("spec"), _by(body), str(body.get("reason") or "")[:500])


@router.post("/datasets/{dataset_id}/contract/from-version/{version_id}", status_code=201,
             dependencies=[require("data.contract", path("dataset_id", "dataset"))])
async def contract_from_version(dataset_id: str, version_id: str, request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """P21 (C §4.5 "freeze what this looks like"): {on_removed_column?, on_type_change?, allow_new_columns?,
    severity_default?, reason?, by?}. The new active contract, and the version's own result against it."""
    body = body if isinstance(body, dict) else {}
    db = request.app.state.db

    def go() -> dict[str, Any]:
        with db.connect() as con:
            if root_of(con, version_id) != root_of(con, dataset_id):
                raise ContractError(404, "not_found", f"{version_id} is not a version of {dataset_id}.")
        spec = freeze(db, version_id, body)
        c = put(db, dataset_id, spec, _by(body), str(body.get("reason") or f"frozen from {version_id}")[:500],
                source={"frozen_from": version_id})
        return {**c, "result": evaluate_version(db, version_id, c)}

    return await _run(go)


@router.post("/datasets/{dataset_id}/contract/check", dependencies=[require("data.read_meta", path("dataset_id", "dataset"))])
async def check_version(dataset_id: str, request: Request) -> dict[str, Any]:
    """P21: the active contract on this version, now; stored on the version (contract_result)."""
    db = request.app.state.db

    def go() -> dict[str, Any]:
        res = evaluate_version(db, dataset_id)
        if res is None:
            raise ContractError(404, "no_contract", "This dataset has no contract: freeze one from a version "
                                                    "(POST …/contract/from-version/{version}) or PUT one.")
        return {"version_id": dataset_id, **res}

    return await _run(go)
