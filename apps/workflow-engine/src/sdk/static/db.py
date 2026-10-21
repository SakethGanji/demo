"""``studio.db`` — project data tables through the broker (DataTable node)."""

from __future__ import annotations

from typing import Any

from ._transport import BrokerError
from ._transport import call as _call


class Table:
    """Rows come back as ``{id, created_at, updated_at, **columns}``. ``id``, ``created_at`` and ``updated_at``
    are managed by the table: read them, never write them."""

    def __init__(self, name: str) -> None:
        self.name = name

    def _op(self, operation: str, **params: Any) -> list[dict]:
        return _call("DataTable", {"table": self.name, "operation": operation,
                                   **{k: v for k, v in params.items() if v is not None}}, [{}])

    def insert(self, data: dict | list[dict]) -> dict | list[dict]:
        rows = self._op("insert", data=data)
        return rows if isinstance(data, list) else rows[0]

    def get(self, row_id: int) -> dict | None:
        """The row, or ``None`` when there is no row with that id."""
        try:
            return self._op("get", rowId=row_id)[0]
        except BrokerError as e:
            if e.code == "row_not_found":
                return None
            raise

    def list(self, filter: dict | None = None, limit: int = 50, sort: str | None = None) -> list[dict]:
        """Up to ``limit`` rows (at most 1000). filter: ``{"col": value}`` (equals), or per column
        ``{"$eq"|"$ne"|"$gt"|"$gte"|"$lt"|"$lte"|"$in"|"$contains"|"$exists": ...}``, combined with
        ``{"$and": [...]}`` / ``{"$or": [...]}``. sort: a column, ``-column`` for descending (default ``-created_at``)."""
        return self._op("list", filter=filter, limit=limit, sort=sort)

    def all(self, filter: dict | None = None, max_rows: int = 10000) -> list[dict]:
        """Every matching row (oldest first), fetched 1000 at a time by id; stops at ``max_rows``."""
        out: list[dict] = []
        last = 0
        while len(out) < max_rows:
            page_filter = {"id": {"$gt": last}}
            if filter:
                page_filter = {"$and": [filter, page_filter]}
            rows = self._op("list", filter=page_filter, limit=min(1000, max_rows - len(out)), sort="id")
            out += rows
            if len(rows) < 1000:
                break
            last = rows[-1]["id"]
        return out

    def count(self, filter: dict | None = None, max_rows: int = 10000) -> int:
        """How many rows match (counted up to ``max_rows``)."""
        return len(self.all(filter, max_rows=max_rows))

    def page(self, filter: dict | None = None, sort: str | None = None, limit: int = 50, offset: int = 0,
             max_rows: int = 10000) -> dict:
        """One page for a paginated list route: ``{"rows", "total", "limit", "offset"}``. ``sort`` may name any column
        (``-col`` = descending, default ``-created_at``); rows missing the column sort last."""
        rows = self.all(filter, max_rows=max_rows)
        key = (sort or "-created_at").lstrip("+-")
        desc = (sort or "-created_at").startswith("-")
        present = [r for r in rows if r.get(key) is not None]
        missing = [r for r in rows if r.get(key) is None]
        try:
            present.sort(key=lambda r: (r[key], r["id"]), reverse=desc)
        except TypeError:
            present.sort(key=lambda r: (str(r[key]), r["id"]), reverse=desc)
        rows = present + missing
        limit = max(1, min(int(limit or 50), 1000))
        offset = max(0, int(offset or 0))
        return {"rows": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    def update(self, row_id: int, data: dict) -> dict:
        return self._op("update", rowId=row_id, data=data)[0]

    def delete(self, row_id: int) -> None:
        self._op("delete", rowId=row_id)

    def upsert(self, match: list[str], data: dict) -> dict:
        return self._op("upsert", matchColumns=match, data=data)[0]


def table(name: str) -> Table:
    return Table(name)
