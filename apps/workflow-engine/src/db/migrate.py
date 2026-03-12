"""
Imperative SQL migration runner.

Features:
  - Sequential, timestamped migrations (YYYYMMDDHHMMSS_description.sql)
  - Transactional execution (rollback on failure)
  - Advisory locks (prevents concurrent runs across pods)
  - Checksum verification (detects tampered migrations)
  - Rollback support via `-- migrate:down` sections

Commands:
  python -m src.db.migrate                           # apply pending migrations (default)
  python -m src.db.migrate apply                     # same as above
  python -m src.db.migrate status                    # show applied/pending migrations
  python -m src.db.migrate dry-run                   # show what would be applied without running
  python -m src.db.migrate rollback                  # rollback the last applied migration
  python -m src.db.migrate rollback 20260307191500   # rollback to a specific version
  python -m src.db.migrate reset                     # drop schema and re-apply all from scratch
  python -m src.db.migrate new "create_users"        # create a new migration file

Connection overrides (forremote access):
  python -m src.db.migrate apply --admin-user postgres --admin-password secret
  python -m src.db.migrate apply --host db.prod --port 5432 --dbname workflows --admin-user postgres --admin-password secret
  python -m src.db.migrate status --host db.prod --dbname workflows --admin-user postgres --admin-password secret
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

from ..core.config import settings
from ..core.secrets import resolve_database_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_ID = 839201  # project-specific advisory lock ID
DB_SCHEMA = '"workflow-app"'

# Populated by argparse in admin mode; None otherwise.
_cli_args: argparse.Namespace | None = None


def _get_conn():
    """Get a migration connection. Uses CLI overrides if provided, else app config."""
    args = _cli_args
    if args and (args.admin_user or args.host or args.port or args.dbname):
        # Build connection from CLI args with app config as defaults
        host = args.host or settings.db_host
        port = args.port or settings.db_port
        user = args.admin_user or settings.db_user
        password = args.admin_password or settings.db_password or ""
        dbname = args.dbname or settings.db_name
        conn = psycopg2.connect(
            host=host, port=int(port), user=user, password=password, dbname=dbname,
            options=f"-c search_path={DB_SCHEMA}",
        )
    else:
        url = resolve_database_url(settings)
        if not url:
            print("ERROR: No database URL configured.")
            print("  Set WORKFLOW_DATABASE_URL or individual WORKFLOW_POSTGRESQL_* vars.")
            sys.exit(1)
        sync_url = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = psycopg2.connect(sync_url, options=f"-c search_path={DB_SCHEMA}")
    conn.autocommit = False
    return conn


def _ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                filename   TEXT NOT NULL,
                checksum   TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT now()
            );
        """)
    conn.commit()


def _get_applied(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, filename, checksum FROM schema_migrations ORDER BY version;")
        return {
            row[0]: {"filename": row[1], "checksum": row[2]}
            for row in cur.fetchall()
        }


def _get_migration_files() -> list[str]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))


def _parse_migration(path: str) -> tuple[str, str | None]:
    """Parse migration file into (up_sql, down_sql)."""
    with open(path) as f:
        content = f.read()

    if "-- migrate:down" in content:
        parts = content.split("-- migrate:down", 1)
        return parts[0].strip(), parts[1].strip()
    return content.strip(), None


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def _version_from_filename(filename: str) -> str:
    return filename.split("_", 1)[0]


# -- Commands ----------------------------------------------------------------


def cmd_apply():
    conn = _get_conn()
    _ensure_table(conn)

    with conn.cursor() as cur:
        cur.execute(f"SELECT pg_advisory_lock({LOCK_ID});")

    try:
        applied = _get_applied(conn)
        files = _get_migration_files()
        pending = 0

        for filename in files:
            version = _version_from_filename(filename)
            path = MIGRATIONS_DIR / filename
            up_sql, _ = _parse_migration(str(path))
            cs = _checksum(up_sql)

            if version in applied:
                if applied[version]["checksum"] != cs:
                    print(f"ERROR: Checksum mismatch for {filename}")
                    print(f"  Expected: {applied[version]['checksum']}")
                    print(f"  Got:      {cs}")
                    print("  Migration was modified after being applied. This is not allowed.")
                    sys.exit(1)
                continue

            # Apply migration
            with conn.cursor() as cur:
                try:
                    cur.execute(up_sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                        (version, filename, cs),
                    )
                    conn.commit()
                    print(f"  Applied: {filename}")
                    pending += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  FAILED:  {filename}")
                    print(f"           {e}")
                    sys.exit(1)

        if pending == 0:
            print("  No pending migrations.")
        else:
            print(f"  {pending} migration(s) applied.")

    finally:
        with conn.cursor() as cur:
            cur.execute(f"SELECT pg_advisory_unlock({LOCK_ID});")
        conn.close()


def cmd_status():
    conn = _get_conn()
    _ensure_table(conn)
    applied = _get_applied(conn)
    files = _get_migration_files()
    conn.close()

    print(f"\n  {'Status':<10} {'Version':<16} {'Description'}")
    print(f"  {'------':<10} {'-------':<16} {'-----------'}")

    for filename in files:
        version = _version_from_filename(filename)
        desc = filename.split("_", 1)[1].replace(".sql", "") if "_" in filename else filename
        status = "applied" if version in applied else "pending"
        print(f"  {status:<10} {version:<16} {desc}")

    print()


def cmd_rollback(target_version: str | None = None):
    conn = _get_conn()
    _ensure_table(conn)
    applied = _get_applied(conn)

    if not applied:
        print("  Nothing to rollback.")
        conn.close()
        return

    with conn.cursor() as cur:
        cur.execute(f"SELECT pg_advisory_lock({LOCK_ID});")

    try:
        if target_version:
            versions_to_rollback = [v for v in sorted(applied.keys(), reverse=True) if v >= target_version]
        else:
            versions_to_rollback = [sorted(applied.keys())[-1]]

        for version in versions_to_rollback:
            info = applied[version]
            path = MIGRATIONS_DIR / info["filename"]

            if not path.exists():
                print(f"  ERROR: Migration file not found: {info['filename']}")
                sys.exit(1)

            _, down_sql = _parse_migration(str(path))
            if not down_sql:
                print(f"  ERROR: No rollback section in {info['filename']}")
                print("         Add a `-- migrate:down` section to enable rollback.")
                sys.exit(1)

            with conn.cursor() as cur:
                try:
                    cur.execute(down_sql)
                    cur.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))
                    conn.commit()
                    print(f"  Rolled back: {info['filename']}")
                except Exception as e:
                    conn.rollback()
                    print(f"  FAILED rollback: {info['filename']}")
                    print(f"                   {e}")
                    sys.exit(1)

    finally:
        with conn.cursor() as cur:
            cur.execute(f"SELECT pg_advisory_unlock({LOCK_ID});")
        conn.close()


def cmd_dry_run():
    conn = _get_conn()
    _ensure_table(conn)
    applied = _get_applied(conn)
    files = _get_migration_files()
    conn.close()

    pending = []
    for filename in files:
        version = _version_from_filename(filename)
        if version not in applied:
            pending.append(filename)

    if not pending:
        print("\n  No pending migrations.\n")
        return

    print(f"\n  {len(pending)} migration(s) would be applied:\n")
    for filename in pending:
        print(f"    - {filename}")
    print()


def cmd_reset():
    """Drop all tables and re-apply migrations from scratch."""
    conn = _get_conn()

    with conn.cursor() as cur:
        cur.execute(f"SELECT pg_advisory_lock({LOCK_ID});")

    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {DB_SCHEMA} CASCADE; CREATE SCHEMA {DB_SCHEMA};")
            conn.commit()
            print("  Dropped all tables.")

        _ensure_table(conn)

        applied = _get_applied(conn)
        files = _get_migration_files()
        count = 0

        for filename in files:
            version = _version_from_filename(filename)
            path = MIGRATIONS_DIR / filename
            up_sql, _ = _parse_migration(str(path))
            cs = _checksum(up_sql)

            with conn.cursor() as cur:
                try:
                    cur.execute(up_sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                        (version, filename, cs),
                    )
                    conn.commit()
                    print(f"  Applied: {filename}")
                    count += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  FAILED:  {filename}")
                    print(f"           {e}")
                    sys.exit(1)

        print(f"  Reset complete. {count} migration(s) applied.")

    finally:
        with conn.cursor() as cur:
            cur.execute(f"SELECT pg_advisory_unlock({LOCK_ID});")
        conn.close()


def cmd_new(description: str):
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    slug = description.lower().replace(" ", "_").replace("-", "_")
    filename = f"{timestamp}_{slug}.sql"
    path = MIGRATIONS_DIR / filename

    path.write_text(f"""-- {timestamp}_{slug}

-- migrate:up


-- migrate:down

""")
    print(f"  Created: {path}")


# -- Main -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.db.migrate",
        description="SQL migration runner",
    )
    sub = parser.add_subparsers(dest="command")

    # Commands
    sub.add_parser("apply", help="Apply pending migrations (default)")
    sub.add_parser("status", help="Show applied/pending migrations")
    sub.add_parser("dry-run", help="Show what would be applied")
    sub.add_parser("reset", help="Drop schema and re-apply from scratch")

    rb = sub.add_parser("rollback", help="Rollback last (or specific) migration")
    rb.add_argument("target", nargs="?", default=None, help="Version to rollback to")

    nw = sub.add_parser("new", help="Create a new migration file")
    nw.add_argument("description", nargs="+", help="Migration description")

    # Connection overrides
    parser.add_argument("--host", default=None, help=f"DB host (default: {settings.db_host})")
    parser.add_argument("--port", default=None, help=f"DB port (default: {settings.db_port})")
    parser.add_argument("--dbname", default=None, help=f"DB name (default: {settings.db_name})")
    parser.add_argument("--admin-user", default=None, help="DB user override (default: app user)")
    parser.add_argument("--admin-password", default=None, help="DB password override")

    return parser


def main():
    global _cli_args

    parser = _build_parser()
    args = parser.parse_args()
    _cli_args = args

    command = args.command or "apply"

    if command == "apply":
        print("\n  Running migrations...")
        cmd_apply()
    elif command == "status":
        cmd_status()
    elif command == "rollback":
        cmd_rollback(args.target)
    elif command == "reset":
        print("\n  Resetting database...")
        cmd_reset()
    elif command == "dry-run":
        cmd_dry_run()
    elif command == "new":
        cmd_new(" ".join(args.description))


if __name__ == "__main__":
    main()
