"""Row hashes (labeling plan §4.2): keyed HMAC-SHA256 over canonical bytes, computed by the server from the version the
event names, never sent by a client."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Iterable

from .values import canon_cell, canon_json


def _b(v: Any) -> bytes:
    c = canon_cell(v)
    return b"\x00" if c is None else c.encode("utf-8")


def row_hash(salt: str, cols: Iterable[str], row: dict[str, Any]) -> str:
    h = hmac.new(bytes.fromhex(salt), b"ensemble.rowhash/1\n", hashlib.sha256)
    for c in sorted(cols):
        h.update(c.encode("utf-8") + b"\x1f" + _b(row.get(c)) + b"\x1e")
    return h.hexdigest()


def cols_sha(cols: Iterable[str]) -> str:
    return hashlib.sha256(canon_json(sorted(cols)).encode()).hexdigest()


def cell_hash(salt: str, v: Any) -> str:
    return hmac.new(bytes.fromhex(salt), b"ensemble.cell/1\n" + _b(v), hashlib.sha256).hexdigest()


def hashes(salt: str, row: dict[str, Any] | None, column: str, ctx: list[str] | None) -> dict[str, str | None]:
    """The four hashes of an event on `column` of `row` (None row: the row is absent from the version)."""
    if row is None:
        return {"row_hash_ctx": None, "row_hash_all": None, "ctx_cols_sha256": None, "base_value_hash": None}
    others = [c for c in row if c != column and c != "__row_id"]
    ctx_cols = [c for c in (ctx if ctx is not None else others) if c != column and c != "__row_id"]
    return {"row_hash_ctx": row_hash(salt, ctx_cols, row), "row_hash_all": row_hash(salt, others, row),
            "ctx_cols_sha256": cols_sha(ctx_cols), "base_value_hash": cell_hash(salt, row.get(column)),
            "_ctx": sorted(ctx_cols)}
