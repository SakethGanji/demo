"""P23: copy an existing SQLite metadata DB into Postgres, verify it, and resume after a crash.

    ENSEMBLE_DB_URL=postgresql://… python -m app.lake.migrate_pg --sqlite <data_dir>/ensemble.sqlite3 [--batch 2000]
    … --verify-only          re-run only the verification
    … --dry-run              list the tables, columns and row counts; write nothing

The URL comes from the environment only (ENSEMBLE_DB_OWNER_URL when set, else ENSEMBLE_DB_URL); it is never stored or
printed with its password. Run it with the API and workers stopped. The lake (Parquet blobs under data_dir/lake) does not
move: point the API at the same ENSEMBLE_DATA_DIR with ENSEMBLE_DB_URL set.

1. **Schema.** The target gets the §2.2 schema (catalog/pg_schema.migrate; grants too when ENSEMBLE_DB_APP_ROLE is set).
   Every source table must exist in the target with every source column; otherwise nothing is copied.
2. **Copy, verbatim.** Tables in foreign-key order, rows in SQLite rowid order (a self-reference, splits.carried_from,
   points at an earlier row), in batches. Each batch and its progress row (`pg_copy_progress`: table, last rowid, rows)
   commit together, so a crash resumes from the last committed batch. Values go across as stored: audit_log's
   details / decision TEXT, prev_hash, row_hash and the catalog_meta rows (audit_head:*, pii_salt:*, steps, verify-all
   passes) byte for byte. catalog_meta and teams are upserts (the source wins over the rows the schema step made,
   e.g. tm_default); everything else is a plain INSERT (a duplicate key fails loudly). Identity sequences (audit_log,
   lineage_edges, annotations) are moved past the copied ids.
3. **Verify.** Per table: the row count, and a content hash: sha256 over the sorted sha256 of each row's canonical JSON
   (columns by name; ints, floats (repr), text, bytes as hex, NULL). Order-free, so collation differences cannot
   matter. `ok` only when every table matches. Then each team's audit chain is re-walked on Postgres
   (schema.verify_audit_chain): the copy must not break a link.

A refused start: the target already has datasets and no progress rows from this source (it is not empty)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROGRESS_DDL = """CREATE TABLE IF NOT EXISTS pg_copy_progress (
  table_name TEXT PRIMARY KEY, source TEXT NOT NULL, last_rowid BIGINT NOT NULL DEFAULT 0, rows_copied BIGINT NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT)"""
#: the source wins on a key the schema step already wrote
UPSERT = {"catalog_meta": ("key",), "teams": ("id",)}
#: written by the target only, never compared
TARGET_ONLY_META = ("pg_schema",)
SKIP_TABLES = ("pg_copy_progress",)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _url() -> str:
    from .. import db_pg
    from ..catalog import pg_schema

    url = pg_schema.owner_url() or db_pg.env_url()
    if not db_pg.is_pg_url(url):
        raise SystemExit("Set ENSEMBLE_DB_URL (or ENSEMBLE_DB_OWNER_URL) to the target postgresql:// URL.")
    return url


def source_tables(src: sqlite3.Connection) -> list[str]:
    return [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def fk_order(pg, tables: list[str]) -> list[str]:
    """Parents before children (pg_constraint); a table's reference to itself is ignored."""
    deps = {t: set() for t in tables}
    for child, parent in pg.execute(
            "SELECT c.relname, p.relname FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_class p ON p.oid = k.confrelid"
            " WHERE k.contype = 'f'").fetchall():
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    out: list[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not d - set(out))
        if not ready:
            raise SystemExit(f"foreign-key cycle among {sorted(deps)}")
        for t in ready:
            out.append(t)
            deps.pop(t)
    return out


def pg_columns(pg, table: str) -> dict[str, str]:
    return {r[0]: r[1] for r in pg.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema()"
                                            " AND table_name = ? ORDER BY ordinal_position", (table,)).fetchall()}


def _coerce(v: Any, pgtype: str) -> Any:
    """A value as the target column stores it (SQLite's dynamic typing can put an int in a TEXT column)."""
    if v is None:
        return None
    if pgtype == "text" and not isinstance(v, str):
        return repr(v) if isinstance(v, float) else (v.decode() if isinstance(v, bytes) else str(v))
    if pgtype == "bytea" and isinstance(v, str):
        return v.encode()
    if pgtype == "double precision" and isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    return v


def _canon(v: Any) -> Any:
    if isinstance(v, memoryview):
        v = bytes(v)
    if isinstance(v, bytes):
        return {"b": v.hex()}
    if isinstance(v, float):
        return {"f": repr(v)}
    if isinstance(v, bool):
        return int(v)
    return v


def table_digest(rows, cols: list[str]) -> tuple[int, str]:
    hs = []
    for r in rows:
        body = json.dumps([_canon(x) for x in r], ensure_ascii=False, separators=(",", ":"))
        hs.append(hashlib.sha256(body.encode()).hexdigest())
    hs.sort()
    h = hashlib.sha256()
    for x in hs:
        h.update(x.encode())
    return len(hs), h.hexdigest()


def plan(src: sqlite3.Connection, pg) -> dict[str, Any]:
    tables = [t for t in source_tables(src) if t not in SKIP_TABLES]
    have = {r[0] for r in pg.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()").fetchall()}
    problems, cols = [], {}
    for t in tables:
        sc = [r[1] for r in src.execute(f'PRAGMA table_info("{t}")')]
        if t not in have:
            problems.append(f"table {t} is not in the Postgres schema")
            continue
        pc = pg_columns(pg, t)
        missing = [c for c in sc if c not in pc]
        if missing:
            problems.append(f"{t}: columns {missing} are not in the Postgres schema")
        cols[t] = {c: pc.get(c) for c in sc}
    order = fk_order(pg, [t for t in tables if t in cols])
    counts = {t: int(src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in order}
    return {"tables": order, "columns": cols, "counts": counts, "problems": problems}


def copy(src: sqlite3.Connection, pg, source_id: str, batch: int, log=print) -> dict[str, Any]:
    p = plan(src, pg)
    if p["problems"]:
        raise SystemExit("Refused: " + "; ".join(p["problems"]))
    pg.execute(PROGRESS_DDL)
    prog = {r[0]: r for r in pg.execute("SELECT table_name, source, last_rowid, rows_copied, done FROM pg_copy_progress").fetchall()}
    if any(r[1] != source_id for r in prog.values()):
        raise SystemExit(f"Refused: the target has a copy in progress from another source ({next(iter(prog.values()))[1]}).")
    if not prog and pg.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]:
        raise SystemExit("Refused: the target already has datasets and no copy in progress (copy into an empty database).")
    pg.commit()
    out: dict[str, Any] = {}
    for t in p["tables"]:
        cols = list(p["columns"][t])
        types = p["columns"][t]
        st = prog.get(t)
        if st and st[4]:
            out[t] = {"rows": st[3], "resumed": "done before"}
            continue
        last, n = (st[2], st[3]) if st else (0, 0)
        if not st:
            pg.execute("INSERT INTO pg_copy_progress (table_name, source, started_at) VALUES (?, ?, ?)", (t, source_id, now()))
            pg.commit()
        resumed = bool(st)
        ins = f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        if t in UPSERT:
            key = UPSERT[t]
            ins += f" ON CONFLICT ({', '.join(key)}) DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key)
        sel = f'SELECT rowid, {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}" WHERE rowid > ? ORDER BY rowid LIMIT ?'
        t0 = time.time()
        while True:
            rows = src.execute(sel, (last, batch)).fetchall()
            if not rows:
                break
            data = [[_coerce(v, types[c]) for v, c in zip(r[1:], cols)] for r in rows]
            pg.executemany(ins, data)
            last, n = rows[-1][0], n + len(rows)
            pg.execute("UPDATE pg_copy_progress SET last_rowid = ?, rows_copied = ? WHERE table_name = ?", (last, n, t))
            pg.commit()  # the batch and its progress row together: a crash resumes after the last committed batch
        pg.execute("UPDATE pg_copy_progress SET done = 1, finished_at = ? WHERE table_name = ?", (now(), t))
        pg.commit()
        out[t] = {"rows": n, "seconds": round(time.time() - t0, 2), **({"resumed": f"after rowid {st[2]}"} if resumed else {})}
        log(f"  copied {t}: {n} rows{' (resumed)' if resumed else ''}")
    # identity columns: the next id follows the copied ones
    for t, c in pg.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
                           " AND is_identity = 'YES'").fetchall():
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{t}', '{c}'), GREATEST((SELECT COALESCE(MAX({c}), 0) FROM {t}), 1),"
                   f" (SELECT COUNT(*) > 0 FROM {t}))")
    pg.commit()
    return out


def verify(src: sqlite3.Connection, pg) -> dict[str, Any]:
    from ..catalog.schema import verify_audit_chain

    p = plan(src, pg)
    tables, bad = {}, []
    for t in p["tables"]:
        cols = list(p["columns"][t])
        types = p["columns"][t]
        qs = ", ".join(f'"{c}"' for c in cols)
        where = f" WHERE key NOT IN ({', '.join(repr(k) for k in TARGET_ONLY_META)})" if t == "catalog_meta" else ""
        a = table_digest(([_coerce(v, types[c]) for v, c in zip(r, cols)] for r in src.execute(f'SELECT {qs} FROM "{t}"{where}')), cols)
        b = table_digest(pg.execute(f"SELECT {qs} FROM {t}{where}").fetchall(), cols)
        ok = a == b
        tables[t] = {"rows_sqlite": a[0], "rows_pg": b[0], "sha256": b[1][:16], "ok": ok}
        if not ok:
            tables[t]["sha256_sqlite"] = a[1][:16]
            bad.append(t)
    chains = {}
    for (team,) in pg.execute("SELECT DISTINCT team_id FROM audit_log WHERE team_id IS NOT NULL ORDER BY team_id").fetchall():
        v = verify_audit_chain(pg, team)
        chains[team] = {"ok": v["ok"], "rows": v["rows"], **({"first_break": v.get("first_break")} if not v["ok"] else {})}
    return {"ok": not bad and all(c["ok"] for c in chains.values()), "tables_checked": len(tables), "mismatched": bad,
            "rows": sum(x["rows_pg"] for x in tables.values()), "tables": tables, "audit_chains": chains}


def main(argv: list[str] | None = None) -> int:
    from .. import db_pg
    from ..catalog import pg_schema

    ap = argparse.ArgumentParser(description="Copy a SQLite metadata DB into Postgres (P23), verify, resume.")
    ap.add_argument("--sqlite", required=True, help="path to ensemble.sqlite3")
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", help="write the result here")
    a = ap.parse_args(argv)
    path = Path(a.sqlite).resolve()
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    url = _url()
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    print(f"source {path}\ntarget {db_pg.redact(url)}")
    import os

    role = (os.environ.get("ENSEMBLE_DB_APP_ROLE") or "").strip() or None
    with db_pg.direct(url) as pg:
        if a.dry_run:
            if not pg.execute("SELECT to_regclass('catalog_meta') IS NOT NULL").fetchone()[0]:
                ts = [t for t in source_tables(src) if t not in SKIP_TABLES]
                print(json.dumps({"target": "empty (the copy builds the schema first)",
                                  "counts": {t: int(src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in ts}}, indent=1))
                return 0
            p = plan(src, pg)
            print(json.dumps({k: p[k] for k in ("tables", "counts", "problems")}, indent=1))
            return 0 if not p["problems"] else 1
        res: dict[str, Any] = {"source": str(path), "at": now()}
        if not a.verify_only:
            res["schema"] = pg_schema.migrate(pg, app_role=role)
            pg.commit()
            t0 = time.time()
            res["copy"] = copy(src, pg, str(path), a.batch)
            res["copy_seconds"] = round(time.time() - t0, 1)
            # again, now that the copied catalog_meta says the P01 backfill ran: the audit triggers go on
            res["schema_after"] = pg_schema.migrate(pg, app_role=role, force=True)
            pg.commit()
        res["verify"] = verify(src, pg)
    v = res["verify"]
    print(f"verify: ok={v['ok']} tables={v['tables_checked']} rows={v['rows']} mismatched={v['mismatched']}")
    for t, c in v["audit_chains"].items():
        print(f"  audit chain {t}: ok={c['ok']} rows={c['rows']}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
    return 0 if v["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
