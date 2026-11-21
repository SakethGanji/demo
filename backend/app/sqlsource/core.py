"""G9: load a dataset from a SQL query.

A connection is a SQLAlchemy-style URL. For the POC that is a SQLite or DuckDB file on the server (under the data
directory, or a directory listed in ENSEMBLE_SQL_ROOTS) or a Postgres URL. Credentials come with each request and are
never stored: lineage keeps the URL with its password masked, and a refresh of a password-protected source asks again.

Read-only, three ways:
  1. the statement: one SELECT (or WITH … SELECT); no second statement and no write, DDL or admin keyword outside
     string literals and comments;
  2. the connection: SQLite opens the file with mode=ro, DuckDB with read_only=True, Postgres in a READ ONLY
     transaction with a statement timeout;
  3. a row limit: at most `limit` rows are fetched (fetchmany(limit + 1) says whether there were more).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from .. import frames

DEFAULT_LIMIT = 100_000
MAX_LIMIT = 1_000_000
PREVIEW_ROWS = 20
TIMEOUT_S = 60
DEMO_URL = "sqlite:///demo/bank.sqlite"  # relative file paths are read from the data directory

WRITE_WORDS = ("insert", "update", "delete", "merge", "upsert", "drop", "alter", "create", "truncate",
               "grant", "revoke", "attach", "detach", "pragma", "vacuum", "copy", "call", "exec", "execute", "install",
               "export", "set", "begin", "commit", "rollback", "reindex", "into")


class SqlError(ValueError):
    """The URL or query can't be used; the message is shown to the user."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- the statement ---------------------------------------------------------------------------------------------------

def _strip(sql: str) -> str:
    """The query without comments and string literals (so keywords inside them don't count)."""
    s = re.sub(r"--[^\n]*", " ", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"'(?:[^']|'')*'", "''", s)
    return s


def check_query(sql: str) -> str:
    """The query, trimmed, if it is one read-only SELECT; else SqlError saying why."""
    q = (sql or "").strip().rstrip(";").strip()
    if not q:
        raise SqlError("Write a SELECT query.")
    bare = _strip(q)
    if ";" in bare:
        raise SqlError("One statement only: remove the ';' between statements.")
    if not re.match(r"^\s*(select|with)\b", bare, re.I):
        raise SqlError("Only SELECT queries (or WITH … SELECT) can load data; the source is read-only.")
    # Quoted identifiers may contain anything: drop them before looking for keywords.
    words = set(re.findall(r"[a-z_]+", re.sub(r'"[^"]*"|`[^`]*`|\[[^\]]*\]', " ", bare).lower()))
    bad = sorted(words & set(WRITE_WORDS))
    if bad:
        raise SqlError(f"The query contains {', '.join(w.upper() for w in bad)}: only read-only SELECTs are allowed.")
    return q


def first_table(sql: str) -> str | None:
    m = re.search(r"\bfrom\s+([A-Za-z_][\w.]*)", _strip(sql), re.I)
    return m.group(1).split(".")[-1] if m else None


# -- the connection --------------------------------------------------------------------------------------------------

def _roots(data_dir: Path) -> list[Path]:
    extra = [Path(p).resolve() for p in os.environ.get("ENSEMBLE_SQL_ROOTS", "").split(os.pathsep) if p.strip()]
    return [data_dir.resolve(), *extra]


def parse_url(url: str, data_dir: Path, password: str | None = None) -> dict[str, Any]:
    """{dialect, path | url, redacted}. Files must exist under an allowed root; the workbench's own database is refused."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    url = (url or "").strip()
    if not url:
        raise SqlError("Give a connection URL, e.g. sqlite:///demo/bank.sqlite.")
    try:
        u = make_url(url)
    except ArgumentError as e:
        raise SqlError(f"Not a database URL: {url!r}. Use sqlite:///file.sqlite, duckdb:///file.duckdb or "
                       "postgresql://user@host/db.") from e
    backend = u.get_backend_name()
    if password:
        u = u.set(password=password)
    redacted = u.render_as_string(hide_password=True)
    if backend in ("sqlite", "duckdb"):
        if not u.database or u.database == ":memory:":
            raise SqlError("Give the database file, e.g. sqlite:///demo/bank.sqlite.")
        roots = _roots(data_dir)
        p = Path(u.database)
        p = (p if p.is_absolute() else roots[0] / p).resolve()
        if not any(p == r or r in p.parents for r in roots):
            raise SqlError(f"Database files are read from the data directory ({roots[0]}) or ENSEMBLE_SQL_ROOTS; "
                           f"{p} is outside them.")
        if p.name == "ensemble.sqlite3":
            raise SqlError("That is the workbench's own database, not a data source.")
        if not p.is_file():
            raise SqlError(f"No database file at {p}.")
        return {"dialect": backend, "path": p, "redacted": redacted, "has_password": False}
    if backend == "postgresql":
        return {"dialect": backend, "url": u, "redacted": redacted, "has_password": u.password is not None}
    raise SqlError(f"'{backend}' is not supported here: use SQLite, DuckDB or Postgres.")


@contextmanager
def _cursor(src: dict[str, Any]) -> Iterator[Any]:
    """A DB-API cursor on a read-only connection."""
    d = src["dialect"]
    if d == "sqlite":
        con = sqlite3.connect(f"file:{src['path']}?mode=ro", uri=True, timeout=5, check_same_thread=False)
        t0 = time.monotonic()
        con.set_progress_handler(lambda: int(time.monotonic() - t0 > TIMEOUT_S), 100_000)
        try:
            yield con.cursor()
        finally:
            con.close()
    elif d == "duckdb":
        try:
            import duckdb
        except ImportError as e:
            raise SqlError("DuckDB files need the duckdb package on the server (pip install duckdb).") from e
        con = duckdb.connect(str(src["path"]), read_only=True)
        try:
            yield con.cursor()
        finally:
            con.close()
    else:
        from sqlalchemy import create_engine
        from sqlalchemy.exc import NoSuchModuleError

        try:
            eng = create_engine(src["url"], connect_args={"connect_timeout": 10}, pool_pre_ping=False)
        except (NoSuchModuleError, ModuleNotFoundError) as e:
            raise SqlError("Postgres needs a driver on the server (pip install psycopg[binary]).") from e
        try:
            raw = eng.raw_connection()
        except ModuleNotFoundError as e:
            raise SqlError("Postgres needs a driver on the server (pip install psycopg[binary]).") from e
        except Exception as e:  # noqa: BLE001
            raise SqlError(f"Could not connect: {str(e).splitlines()[0]}") from e
        try:
            cur = raw.cursor()
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cur.execute(f"SET statement_timeout = {TIMEOUT_S * 1000}")
            yield cur
        finally:
            raw.rollback()
            raw.close()
            eng.dispose()


def _fetch(src: dict[str, Any], sql: str, limit: int) -> tuple[pd.DataFrame, bool]:
    with _cursor(src) as cur:
        try:
            cur.execute(sql)
            cols = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(limit + 1)
        except SqlError:
            raise
        except Exception as e:  # noqa: BLE001  (each driver has its own error classes)
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            if "interrupted" in msg.lower():
                msg = f"the query ran longer than {TIMEOUT_S} s"
            raise SqlError(f"The database refused the query: {msg}") from e
    if not cols:
        raise SqlError("The query returned no columns.")
    truncated = len(rows) > limit
    return pd.DataFrame(rows[:limit], columns=cols), truncated


def run_query(url: str, query: str, data_dir: Path, *, limit: int = DEFAULT_LIMIT, password: str | None = None
              ) -> dict[str, Any]:
    """Run the read-only query: {frame, truncated, seconds, src, query}."""
    q = check_query(query)
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    src = parse_url(url, data_dir, password)
    t0 = time.perf_counter()
    df, truncated = _fetch(src, q, limit)
    return {"frame": df, "truncated": truncated, "seconds": time.perf_counter() - t0, "src": src, "query": q,
            "limit": limit}


def tables(url: str, data_dir: Path, password: str | None = None) -> list[dict[str, Any]]:
    """The tables and views with their columns (name, type) and row counts (files only), for the schema list."""
    src = parse_url(url, data_dir, password)
    d = src["dialect"]
    out: list[dict[str, Any]] = []
    with _cursor(src) as cur:
        if d == "sqlite":
            cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
            for name, kind in cur.fetchall():
                cur.execute(f'PRAGMA table_info("{name}")')
                cols = [{"name": r[1], "type": r[2] or ""} for r in cur.fetchall()]
                cur.execute(f'SELECT COUNT(*) FROM "{name}"')
                out.append({"name": name, "kind": kind, "columns": cols, "rows": int(cur.fetchone()[0])})
        else:
            cur.execute("SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name, ordinal_position")
            by: dict[str, dict[str, Any]] = {}
            for schema, tname, col, typ in cur.fetchall()[:5000]:
                key = tname if schema in ("public", "main") else f"{schema}.{tname}"
                by.setdefault(key, {"name": key, "kind": "table", "columns": [], "rows": None})["columns"].append(
                    {"name": col, "type": str(typ)})
            out = list(by.values())
    return out


# -- lineage ---------------------------------------------------------------------------------------------------------

def frame_sha(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()[:16]


def row_diff(old: pd.DataFrame, new: pd.DataFrame) -> dict[str, int]:
    """Rows added, removed and kept between two versions, matching whole rows (as multisets)."""
    if list(old.columns) != list(new.columns):
        return {"added": len(new), "removed": len(old), "kept": 0, "columns_changed": 1}
    ho = pd.util.hash_pandas_object(old.astype(str), index=False).value_counts()
    hn = pd.util.hash_pandas_object(new.astype(str), index=False).value_counts()
    kept = int(pd.concat([ho, hn], axis=1).fillna(0).min(axis=1).sum())
    return {"added": len(new) - kept, "removed": len(old) - kept, "kept": kept, "columns_changed": 0}


def lineage_record(res: dict[str, Any], df: pd.DataFrame, *, version: int, root: str | None, parent: str | None,
                   base_name: str, diff: dict[str, int] | None = None) -> dict[str, Any]:
    src = res["src"]
    return {"source": "sql", "dialect": src["dialect"], "url": src["redacted"], "needs_password": src["has_password"],
            "query": res["query"], "query_sha": hashlib.sha256(res["query"].encode()).hexdigest()[:16],
            "limit": res["limit"], "truncated": res["truncated"], "rows": len(df), "columns": list(df.columns),
            "fetched_at": now(), "seconds": round(res["seconds"], 3), "data_sha": frame_sha(df),
            "version": version, "root": root, "parent": parent, "base_name": base_name, "diff": diff}


# -- the demo database -----------------------------------------------------------------------------------------------

DEMO_VERSION = "1"
DEMO_QUERY = """SELECT a.*, o.defaulted
FROM applications a
JOIN outcomes o ON o.applicant_id = a.applicant_id
WHERE a.application_date >= '2022-01-01'"""


def ensure_demo_db(data_dir: Path, store: Any) -> Path:
    """data/demo/bank.sqlite, built from the bundled samples (rebuilt when they are regenerated):
    applications + outcomes (credit-default, split in two so the demo query is a join), loans (loan-stream) and
    accounts (account-churn)."""
    path = data_dir / "demo" / "bank.sqlite"
    # P10: stamped by each sample's content identity (a Parquet table's table_sha256, or a CSV-era file's hash)
    try:
        ids = [frames.data_identity(k) for k in ("credit-default", "loan-stream", "account-churn")]
    except Exception:  # noqa: BLE001 - a sample is missing: no demo database
        return path
    stamp = DEMO_VERSION + ":" + ",".join(ids)
    mark = path.with_suffix(".stamp")
    if path.exists() and mark.exists() and mark.read_text() == stamp:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    cd = frames.read_version("credit-default")
    with sqlite3.connect(tmp) as con:
        cd.drop(columns=["defaulted"]).to_sql("applications", con, index=False)
        cd[["applicant_id", "defaulted"]].to_sql("outcomes", con, index=False)
        frames.read_version("loan-stream").to_sql("loans", con, index=False)
        frames.read_version("account-churn").to_sql("accounts", con, index=False)
        con.execute("CREATE INDEX ix_outcomes ON outcomes(applicant_id)")
    tmp.replace(path)
    mark.write_text(stamp)
    return path
