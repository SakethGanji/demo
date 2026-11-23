"""``studio.connection("key")`` — read-only SQL against a project's external database, through the broker.

The database URL stays a server-side secret. Every query runs in a READ ONLY transaction with a ~10 s timeout and a row cap.
"""

from __future__ import annotations

from typing import Any

from ._transport import call as _call


class Connection:
    def __init__(self, key: str) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("connection key must be a non-empty string")
        self.key = key

    def query(self, sql: str, params: list | None = None, limit: int = 100) -> list[dict]:
        """Rows (dicts) of one SELECT/WITH statement. Parameters are positional ``%s`` placeholders filled from ``params``
        (a literal ``%`` is written ``%%`` when params are given); never build SQL by string concatenation. At most
        ``limit`` rows (default 100, max 5000). Anything that is not a SELECT is refused."""
        items = _call("Postgres", {"connection": self.key, "operation": "query", "query": sql,
                                   "queryParameters": list(params) if params else [], "limit": limit}, [{}])
        return list((items[0] if items else {}).get("rows") or [])

    def schema(self) -> list[dict]:
        """The connection's tables and columns as rows ``{schema, table, column, type, nullable}`` (system schemas skipped)."""
        return self.query(
            "SELECT table_schema AS schema, table_name AS \"table\", column_name AS \"column\", data_type AS type, "
            "(is_nullable = 'YES') AS nullable FROM information_schema.columns "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND table_schema NOT LIKE 'pg\\_%%' "
            "ORDER BY table_schema, table_name, ordinal_position", [], 5000)


def connection(key: str) -> Connection:
    """A project database connection by key (as listed in the project's Connections)."""
    return Connection(key)
