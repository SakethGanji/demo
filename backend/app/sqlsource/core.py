"""G9: load a dataset from a SQL query.

A connection is a SQLAlchemy-style URL. For the POC that is a SQLite or DuckDB file on the server (under the data
directory, or a directory listed in ENSEMBLE_SQL_ROOTS) or a Postgres URL. Credentials come with each request and are
never stored: lineage keeps the URL with its password masked, and a refresh of a password-protected source asks again.

Read-only, three ways:
  1. the statement: one SELECT (or WITH … SELECT); no second statement and no write, DDL or admin keyword outside
     string literals and comments; F-X4b: no server-side file, large-object, dblink or SQL-in-a-string function
     (pg_read_file, lo_import, query_to_xml, …: `pg_*` / `lo_*` calls are refused), no U& identifiers, no file as a table;
  2. the connection: SQLite opens the file with mode=ro, DuckDB with read_only=True and no external access, Postgres
     in a READ ONLY transaction with a statement timeout;
  3. a row limit: at most `limit` rows are fetched (fetchmany(limit + 1) says whether there were more).

F-X4b (X4 C4): the source never reaches the platform's own databases or the API host's identity:
  - files only under ENSEMBLE_SQL_IMPORT_DIRS (alias ENSEMBLE_SQL_ROOTS; a dir that is, holds or sits in the data dir is
    ignored), plus the bundled demo file; never the platform database, the MLflow store or anything under the data dir;
  - Postgres only with an explicit TCP host, user and non-empty password; never the platform's database server
    (ENSEMBLE_DB_URL / ENSEMBLE_DB_OWNER_URL: the same address and port; a local server = every local address); no
    loopback / link-local / unspecified address unless ENSEMBLE_SQL_ALLOW_LOOPBACK=1; only ENSEMBLE_SQL_ALLOWED_HOSTS
    when that is set. The connection is opened with psycopg on the address checked (hostaddr, no second DNS lookup),
    passfile=/dev/null, no client certificate, no GSS, and require_auth = a password method, so the API process's peer
    / trust / .pgpass / PGPASSWORD / Kerberos identity is never inherited.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import sqlite3
import threading
import uuid
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
    """The URL or query can't be used; the message is shown to the user. `code` names the refusal for the audit."""

    def __init__(self, message: str, code: str = "sql_error") -> None:
        super().__init__(message)
        self.code = code


#: F-X4b: function calls refused anywhere in a query (server files, large objects, other servers, SQL in a string,
#: settings); any `pg_*` / `lo_*` function is refused too
DENY_FUNCS = ("dblink", "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema", "cursor_to_xml",
              "cursor_to_xmlschema", "table_to_xml", "table_to_xml_and_xmlschema", "schema_to_xml", "database_to_xml",
              "ts_stat", "set_config", "current_setting", "load_extension", "readfile", "writefile", "read_csv",
              "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text", "read_blob", "glob", "sniff_csv",
              "fts_main", "edit", "fsdir", "sqlar_uncompress", "lo")
DENY_PREFIX = ("pg_", "lo_", "dblink_", "read_")


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
        raise SqlError(f"The query contains {', '.join(w.upper() for w in bad)}: only read-only SELECTs are allowed.",
                       "sql_write_keyword")
    # F-X4b (X4 C4): server-side functions. Quoted identifiers count as names here ("pg_read_file"(…) is a call).
    if re.search(r"\bu&\s*[\"']", bare, re.I):
        raise SqlError("Unicode-escaped identifiers or strings (U&\"…\") are not allowed in a SQL source query.", "sql_function")
    unq = re.sub(r'"([^"]*)"|`([^`]*)`|\[([^\]]*)\]', lambda m: (m.group(1) or m.group(2) or m.group(3) or "").replace(" ", "_"), bare)
    calls = {m.group(1).lower() for m in re.finditer(r"([A-Za-z_][\w$]*)\s*\(", unq)}
    calls |= {m.group(1).lower() for m in re.finditer(r"\.\s*([A-Za-z_][\w$]*)\s*\(", unq)}
    bad = sorted(c for c in calls if c in DENY_FUNCS or c.startswith(DENY_PREFIX))
    if bad:
        raise SqlError(f"The query calls {', '.join(bad)}: server-side file, large-object, settings and cross-database "
                       "functions are not allowed in a SQL source.", "sql_function")
    if re.search(r"\b(from|join)\s+''", bare, re.I):
        raise SqlError("A file path cannot be read as a table; query the database's tables.", "sql_function")
    return q


def first_table(sql: str) -> str | None:
    m = re.search(r"\bfrom\s+([A-Za-z_][\w.]*)", _strip(sql), re.I)
    return m.group(1).split(".")[-1] if m else None


# -- the connection --------------------------------------------------------------------------------------------------

def _under(p: Path, r: Path) -> bool:
    return p == r or r in p.parents


def _roots(data_dir: Path) -> list[Path]:
    """F-X4b: the import dirs a SQLite / DuckDB file may be read from. A dir that is the data dir, sits inside it or
    holds it is ignored (the platform's own files live there)."""
    dd = data_dir.resolve()
    raw = os.environ.get("ENSEMBLE_SQL_IMPORT_DIRS") or os.environ.get("ENSEMBLE_SQL_ROOTS") or ""
    out = []
    for x in raw.split(os.pathsep):
        if not x.strip():
            continue
        r = Path(x.strip()).resolve()
        if _under(r, dd) or _under(dd, r):
            continue
        out.append(r)
    return out


def demo_path(data_dir: Path) -> Path:
    return (data_dir / "demo" / "bank.sqlite").resolve()


def _platform_files(data_dir: Path) -> list[Path]:
    out = [(data_dir / "ensemble.sqlite3").resolve(), (data_dir / "mlflow" / "mlflow.db").resolve()]
    for env in ("ENSEMBLE_DB_URL", "ENSEMBLE_DB_OWNER_URL", "MLFLOW_TRACKING_URI"):
        v = (os.environ.get(env) or "").strip()
        if v.startswith("sqlite:///"):
            out.append(Path(v[len("sqlite:///"):]).resolve())
    return out


def _local_addrs() -> set[ipaddress._BaseAddress]:
    out: set[Any] = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    try:
                        out.add(ipaddress.ip_address(a.address.split("%")[0]))
                    except ValueError:
                        pass
    except Exception:  # noqa: BLE001 - psutil missing: the hostname's addresses
        pass
    try:
        for ai in socket.getaddrinfo(socket.gethostname(), None):
            out.add(ipaddress.ip_address(ai[4][0].split("%")[0]))
    except OSError:
        pass
    return out


def _resolve(host: str) -> list[Any]:
    try:
        return sorted({ipaddress.ip_address(ai[4][0].split("%")[0]) for ai in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)},
                      key=str)
    except (OSError, ValueError) as e:
        raise SqlError(f"Could not resolve host {host!r}.", "sql_host_unresolved") from e


def _platform_servers() -> list[tuple[set[Any], int, str]]:
    """(addresses, port, which) of every platform database server (the app's catalog, its owner URL, MLflow)."""
    from sqlalchemy.engine import make_url

    out = []
    for env in ("ENSEMBLE_DB_URL", "ENSEMBLE_DB_OWNER_URL", "MLFLOW_TRACKING_URI"):
        v = (os.environ.get(env) or "").strip()
        if not v.startswith(("postgres", "postgresql")):
            continue
        try:
            u = make_url(v)
        except Exception:  # noqa: BLE001
            continue
        host = u.host or str(u.query.get("host") or "")
        if isinstance(host, (list, tuple)):
            host = host[0] if host else ""
        qp = u.query.get("port")
        qp = qp[0] if isinstance(qp, (list, tuple)) and qp else qp
        try:
            port = int(u.port or qp or 5432)
        except ValueError:
            port = 5432
        if not host or host.startswith("/"):
            addrs = _local_addrs()  # a socket: the server is on this machine, so every local address reaches it
        else:
            try:
                addrs = set(_resolve(host))
            except SqlError:
                addrs = set()
            if any(a.is_loopback for a in addrs) or addrs & _local_addrs():
                addrs |= _local_addrs()
        out.append((addrs, port, env))
    return out


def _allowed_hosts() -> set[str]:
    return {h.strip().lower() for h in (os.environ.get("ENSEMBLE_SQL_ALLOWED_HOSTS") or "").split(",") if h.strip()}


def parse_url(url: str, data_dir: Path, password: str | None = None) -> dict[str, Any]:
    """{dialect, path | conninfo, redacted}. F-X4b (X4 C4): files only from the import dirs (or the demo file); Postgres
    only to an explicit, checked TCP address with its own user and password; never a platform database."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    url = (url or "").strip()
    if not url:
        raise SqlError("Give a connection URL, e.g. sqlite:///demo/bank.sqlite.")
    try:
        u = make_url(url)
    except ArgumentError as e:
        raise SqlError(f"Not a database URL: {url!r}. Use sqlite:///file.sqlite, duckdb:///file.duckdb or "
                       "postgresql://user:password@host:port/db.") from e
    backend = u.get_backend_name()
    if password:
        u = u.set(password=password)
    redacted = u.render_as_string(hide_password=True)
    if backend in ("sqlite", "duckdb"):
        if not u.database or u.database == ":memory:":
            raise SqlError("Give the database file, e.g. sqlite:///demo/bank.sqlite.")
        if u.query:
            raise SqlError("File URLs take no options.", "sql_path_refused")
        dd = data_dir.resolve()
        roots = _roots(data_dir)
        p = Path(u.database)
        demo = demo_path(data_dir)
        if not p.is_absolute():  # relative: the demo file, else the first import dir
            p = (dd / p) if (dd / p).resolve() == demo else ((roots[0] / p) if roots else (dd / p))
        p = p.resolve()
        if p != demo:
            if any(p == f for f in _platform_files(data_dir)):
                raise SqlError("That is one of the workbench's own databases, not a data source.", "sql_platform_db")
            if _under(p, dd):
                raise SqlError("Files under the workbench's data directory are not data sources (only the bundled demo "
                               "database is).", "sql_platform_db")
            if not any(_under(p, r) for r in roots):
                raise SqlError("Database files are read only from the import directories (ENSEMBLE_SQL_IMPORT_DIRS"
                               + (f": {', '.join(map(str, roots))}" if roots else ", not set on this server") + f"); {p} is outside them.",
                               "sql_path_refused")
        if not p.is_file():
            raise SqlError(f"No database file at {p}.")
        return {"dialect": backend, "path": p, "redacted": redacted, "has_password": False}
    if backend == "postgresql":
        return _pg_target(u, redacted)
    raise SqlError(f"'{backend}' is not supported here: use SQLite, DuckDB or Postgres.")


def _pg_target(u: Any, redacted: str) -> dict[str, Any]:
    extra = sorted(k for k in u.query if k not in ("sslmode",))
    if extra:
        raise SqlError(f"Connection options {', '.join(extra)} are not allowed: give host, port, user, password and "
                       "database in the URL (sslmode may be set).", "sql_pg_option")
    host = (u.host or "").strip()
    if not host or host.startswith("/"):
        raise SqlError("Give the database server's host name or address (postgresql://user:password@host:5432/db): "
                       "local sockets are not allowed.", "sql_pg_no_host")
    if not (u.username or "").strip():
        raise SqlError("Give the user to connect as (postgresql://user:password@host/db).", "sql_pg_no_credentials")
    if not (u.password or ""):
        raise SqlError("Give the password for this source (the URL's password, or `password`): the server never connects "
                       "with its own identity.", "sql_pg_no_credentials")
    if not (u.database or "").strip():
        raise SqlError("Give the database name (postgresql://user:password@host/db).", "sql_pg_no_database")
    allowed = _allowed_hosts()
    if allowed and host.lower() not in allowed:
        raise SqlError(f"Host {host} is not in ENSEMBLE_SQL_ALLOWED_HOSTS.", "sql_host_refused")
    port = int(u.port or 5432)
    addrs = _resolve(host)
    if not addrs:
        raise SqlError(f"Could not resolve host {host!r}.", "sql_host_unresolved")
    for addrs_p, port_p, which in _platform_servers():
        if port == port_p and set(addrs) & addrs_p:
            raise SqlError("That is the workbench's own database server, not a data source.", "sql_platform_db")
    loop_ok = os.environ.get("ENSEMBLE_SQL_ALLOW_LOOPBACK") == "1"
    for a in addrs:
        if (a.is_loopback or a.is_unspecified or a.is_link_local or a.is_multicast) and not loop_ok:
            raise SqlError(f"{host} resolves to {a}: local, link-local and unspecified addresses are not allowed "
                           "(ENSEMBLE_SQL_ALLOW_LOOPBACK=1 allows loopback for local development).", "sql_host_refused")
    conninfo = {"host": host, "hostaddr": str(addrs[0]), "port": port, "dbname": u.database, "user": u.username,
                "password": u.password, "sslmode": str(u.query.get("sslmode") or "prefer")}
    return {"dialect": "postgresql", "conninfo": conninfo, "redacted": redacted, "has_password": True}


def _pg_connect(ci: dict[str, Any]) -> Any:
    """psycopg on the checked address, with nothing taken from the API process's environment that could authenticate:
    no .pgpass (passfile=/dev/null), no client certificate, no GSS / Kerberos, and only a password method accepted."""
    try:
        import psycopg
    except ImportError as e:
        raise SqlError("Postgres needs a driver on the server (pip install psycopg[binary]).") from e
    nocert = "/nonexistent/ensemble-sql-source-no-client-cert"
    kw = dict(ci, passfile="/dev/null", sslcert=nocert, sslkey=nocert, gssencmode="disable", connect_timeout=10,
              application_name="ensemble-sql-source", options=f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_S * 1000}")
    if psycopg.pq.version() >= 160000:
        kw["require_auth"] = "scram-sha-256,md5,password"
    try:
        return psycopg.connect(**kw, autocommit=False)
    except Exception as e:  # noqa: BLE001
        raise SqlError(f"Could not connect: {str(e).splitlines()[0] if str(e) else type(e).__name__}") from e


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
        # F-X4b: no file / network reads from inside a query (read_csv('/etc/…'), FROM 'file.csv', httpfs)
        con = duckdb.connect(str(src["path"]), read_only=True, config={"enable_external_access": False,
                                                                       "lock_configuration": True})
        try:
            yield con.cursor()
        finally:
            con.close()
    else:
        raw = _pg_connect(src["conninfo"])
        try:
            cur = raw.cursor()
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cur.execute(f"SET statement_timeout = {TIMEOUT_S * 1000}")
            yield cur
        finally:
            raw.rollback()
            raw.close()


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
#: U3: concurrent first calls (two GET /sql/demo at once) built the file through one shared tmp path → 500
_DEMO_LOCK = threading.Lock()
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
    # one builder per process (the lock; re-checked inside), and a private tmp file per call so builders in other
    # processes never write into each other's file; os.replace makes the finished file appear at once
    with _DEMO_LOCK:
        if path.exists() and mark.exists() and mark.read_text() == stamp:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            cd = frames.read_version("credit-default")
            with sqlite3.connect(tmp) as con:
                cd.drop(columns=["defaulted"]).to_sql("applications", con, index=False)
                cd[["applicant_id", "defaulted"]].to_sql("outcomes", con, index=False)
                frames.read_version("loan-stream").to_sql("loans", con, index=False)
                frames.read_version("account-churn").to_sql("accounts", con, index=False)
                con.execute("CREATE INDEX ix_outcomes ON outcomes(applicant_id)")
            con.close()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        mtmp = mark.with_name(f".{mark.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        mtmp.write_text(stamp)
        os.replace(mtmp, mark)
    return path
