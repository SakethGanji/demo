"""Create/load the ``shopdemo`` sample database on the engine's Postgres server (idempotent).

    from tests.fixtures.shopdemo import ensure_shopdemo
    url = ensure_shopdemo()          # postgresql://... for use as a project connection URL

Credentials come from the same WORKFLOW_DB_* settings the engine tests use; they are never printed.
Run directly (``python -m tests.fixtures.shopdemo``) to create it and print only the database name.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import psycopg2

SQL_FILE = Path(__file__).with_name("shopdemo.sql")
DB_NAME = "shopdemo"


def _cfg() -> dict[str, str]:
    return {
        "host": os.environ.get("WORKFLOW_DB_HOST", "localhost"),
        "port": os.environ.get("WORKFLOW_DB_PORT", "5433"),
        "user": os.environ.get("WORKFLOW_DB_USER", "workflow"),
        "password": os.environ.get("WORKFLOW_DB_PASSWORD", "workflow"),
    }


def shopdemo_url() -> str:
    c = _cfg()
    return f"postgresql://{quote(c['user'])}:{quote(c['password'])}@{c['host']}:{c['port']}/{DB_NAME}"


def ensure_shopdemo() -> str:
    """Create the database and load the data if it is not there yet; returns its URL."""
    c = _cfg()
    admin = psycopg2.connect(host=c["host"], port=int(c["port"]), user=c["user"], password=c["password"], dbname="postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{DB_NAME}"')
    finally:
        admin.close()
    conn = psycopg2.connect(host=c["host"], port=int(c["port"]), user=c["user"], password=c["password"], dbname=DB_NAME)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.orders') IS NOT NULL")
            if not cur.fetchone()[0]:
                cur.execute(SQL_FILE.read_text())
        conn.commit()
    finally:
        conn.close()
    return shopdemo_url()


if __name__ == "__main__":
    ensure_shopdemo()
    print(f"database {DB_NAME} ready")
