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
        return self._op("list", filter=filter, limit=limit, sort=sort)

    def update(self, row_id: int, data: dict) -> dict:
        return self._op("update", rowId=row_id, data=data)[0]

    def delete(self, row_id: int) -> None:
        self._op("delete", rowId=row_id)

    def upsert(self, match: list[str], data: dict) -> dict:
        return self._op("upsert", matchColumns=match, data=data)[0]


def table(name: str) -> Table:
    return Table(name)
