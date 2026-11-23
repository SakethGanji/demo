"""Persistence of AI builder proposals and their audit trail (Stage 6, DESIGN-STAGE6-AI-BUILDER section 2).

Raw SQL over ``ai_proposals`` / ``ai_proposal_events``, like the human-task repository: every state change is one
conditional ``UPDATE ... WHERE status = ANY(:from)``, so Postgres row locks (not application locks) decide races
(two Keeps, a Keep against a Discard, a build against the stale reaper).

* ``transition`` returns the row when this caller won the change and ``None`` when someone else got there first.
* The event table is append-only: there is deliberately no update or delete method for it.
* Methods never commit: the caller owns the transaction (the sandbox needs claim + save + event in one commit).
  ``commit=True`` is available on the small single-statement helpers for callers without their own transaction.

Rows come back as plain dicts (JSONB decoded, ``cost_usd`` as float, timestamps as aware ``datetime``).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATUSES = ("spec_draft", "spec_approved", "building", "ready", "failed", "kept", "undone", "discarded", "answered", "clarify")
#: ``clarify`` (capability graph): Understand found open required questions, so no spec has been drafted yet.
KINDS = ("build", "modify", "node_assist", "chat")
_JSON_COLS = ("spec", "base_definition", "proposed_definition", "ops_log", "diff", "report", "test_change_requests", "understanding")
#: columns ``insert`` accepts (everything else has a default)
_INSERT_COLS = (
    "id", "project_id", "team_id", "workflow_id", "parent_id", "kind", "status", "actor", "request", "request_sha256",
    "spec", "spec_approved_by", "spec_approved_at", "spec_fingerprint", "base_definition", "base_draft_hash",
    "proposed_definition", "ops_log", "diff", "report", "test_change_requests", "answer", "model", "provider", "region",
    "llm_calls", "input_tokens", "output_tokens", "cost_usd", "duration_ms", "error_code", "error_message",
    "understanding", "app_build_id", "part_key",
)
#: columns ``transition`` / ``update_fields`` may set; ``id``, ``project_id``, ``created_at`` and the actor never change
_SETTABLE = frozenset(_INSERT_COLS) - {"id", "project_id", "actor", "request", "request_sha256"} | {
    "kept_by", "kept_at", "kept_draft_hash", "accepted_failing",
}


def new_proposal_id() -> str:
    return "p_" + uuid.uuid4().hex[:12]


class UnknownColumn(ValueError):
    pass


def _row(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    for k in _JSON_COLS:
        if isinstance(out.get(k), (str, bytes)):  # driver gave raw JSON text
            out[k] = json.loads(out[k])
    if out.get("cost_usd") is not None:
        out["cost_usd"] = float(out["cost_usd"])
    return out


def encode_cursor(created_at: datetime, proposal_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{proposal_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts, _, pid = raw.partition("|")
        if not pid:
            return None
        dt = datetime.fromisoformat(ts)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)), pid
    except Exception:
        return None


def _bind(col: str, value: Any) -> Any:
    return json.dumps(value, default=str) if col in _JSON_COLS and value is not None else value


def _placeholder(col: str) -> str:
    return f"CAST(:{col} AS jsonb)" if col in _JSON_COLS else f":{col}"


class AIProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── proposals ─────────────────────────────────────────────────────────────

    async def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert a proposal and return it. ``id`` is generated when missing; unknown keys raise."""
        row = dict(row)
        row.setdefault("id", new_proposal_id())
        bad = set(row) - set(_INSERT_COLS)
        if bad:
            raise UnknownColumn(f"cannot insert {sorted(bad)}")
        for required in ("project_id", "kind", "status", "actor", "request", "request_sha256", "model", "provider"):
            if row.get(required) is None:
                raise ValueError(f"{required} is required")
        cols = list(row)
        res = await self._s.execute(
            text(f"INSERT INTO ai_proposals ({', '.join(cols)}) VALUES ({', '.join(_placeholder(c) for c in cols)}) RETURNING *"),
            {c: _bind(c, row[c]) for c in cols},
        )
        return _row(res.mappings().one())

    async def get(self, proposal_id: str, project_id: str | None = None, *, for_update: bool = False) -> dict[str, Any] | None:
        """The proposal, or None. With ``project_id`` a proposal of another project is None too (404 hides existence)."""
        sql = "SELECT * FROM ai_proposals WHERE id = :id"
        params: dict[str, Any] = {"id": proposal_id}
        if project_id is not None:
            sql += " AND project_id = :pid"
            params["pid"] = project_id
        if for_update:
            sql += " FOR UPDATE"
        res = await self._s.execute(text(sql), params)
        m = res.mappings().first()
        return _row(m) if m else None

    async def list(
        self, project_id: str, *, workflow_id: str | None = None, status: str | None = None, kind: str | None = None,
        limit: int = 50, cursor: str | None = None, app_build_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Newest first; ``(rows, next_cursor)``. The listing leaves out the heavy JSON columns (use ``get`` for those)."""
        limit = max(1, min(int(limit), 200))
        where = ["project_id = :pid"]
        params: dict[str, Any] = {"pid": project_id, "lim": limit + 1}
        if workflow_id is not None:
            where.append("workflow_id = :wid")
            params["wid"] = workflow_id
        if status is not None:
            where.append("status = :status")
            params["status"] = status
        if kind is not None:
            where.append("kind = :kind")
            params["kind"] = kind
        if app_build_id is not None:
            where.append("app_build_id = :abid")
            params["abid"] = app_build_id
        if cursor:
            decoded = decode_cursor(cursor)
            if decoded:
                where.append("(created_at, id) < (:c_at, :c_id)")
                params["c_at"], params["c_id"] = decoded
        cols = (
            "id, project_id, team_id, workflow_id, parent_id, kind, status, actor, request_sha256, model, provider, region, "
            "llm_calls, input_tokens, output_tokens, cost_usd, duration_ms, error_code, error_message, kept_by, kept_at, "
            "accepted_failing, app_build_id, part_key, created_at, updated_at, (report->>'passed') AS passed_text"
        )
        res = await self._s.execute(
            text(f"SELECT {cols} FROM ai_proposals WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT :lim"),
            params,
        )
        rows = [_row(m) for m in res.mappings().all()]
        nxt = None
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
        for r in rows:
            t = r.pop("passed_text", None)
            r["passed"] = None if t is None else t == "true"
        return rows, nxt

    async def transition(
        self, proposal_id: str, from_statuses: Iterable[str], to_status: str, fields: dict[str, Any] | None = None,
        *, touch: bool = True,
    ) -> dict[str, Any] | None:
        """``UPDATE ... SET status = :to WHERE id = :id AND status = ANY(:from)``; the row when this call won, else None.

        Of two concurrent callers with the same ``from`` set exactly one gets a row.
        """
        if to_status not in STATUSES:
            raise ValueError(f"unknown status {to_status!r}")
        return await self._update(proposal_id, list(from_statuses), {**(fields or {}), "status": to_status}, touch)

    async def update_fields(
        self, proposal_id: str, fields: dict[str, Any], *, only_in: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """Set columns without changing the status; ``only_in`` guards on the current status. None when the guard fails."""
        return await self._update(proposal_id, list(only_in) if only_in is not None else None, dict(fields), True)

    async def _update(self, proposal_id: str, from_statuses: list[str] | None, fields: dict[str, Any], touch: bool) -> dict[str, Any] | None:
        bad = set(fields) - _SETTABLE - {"status"}
        if bad:
            raise UnknownColumn(f"cannot set {sorted(bad)}")
        sets = [f"{c} = {_placeholder(c)}" for c in fields]
        if touch:
            sets.append("updated_at = now()")
        params: dict[str, Any] = {c: _bind(c, v) for c, v in fields.items()}
        params["id"] = proposal_id
        sql = f"UPDATE ai_proposals SET {', '.join(sets)} WHERE id = :id"
        if from_statuses is not None:
            sql += " AND status = ANY(:from_statuses)"
            params["from_statuses"] = from_statuses
        res = await self._s.execute(text(sql + " RETURNING *"), params)
        m = res.mappings().first()
        return _row(m) if m else None

    async def add_usage(
        self, proposal_id: str, *, llm_calls: int = 0, input_tokens: int = 0, output_tokens: int = 0, duration_ms: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        """Atomic increments (never read-modify-write), so parallel loops of one proposal cannot lose counts."""
        await self._s.execute(
            text("""UPDATE ai_proposals SET llm_calls = llm_calls + :c, input_tokens = input_tokens + :i,
                    output_tokens = output_tokens + :o, duration_ms = duration_ms + :d,
                    cost_usd = CASE WHEN CAST(:cost AS numeric) IS NULL THEN cost_usd
                                    ELSE COALESCE(cost_usd, 0) + CAST(:cost AS numeric) END,
                    updated_at = now() WHERE id = :id"""),
            {"id": proposal_id, "c": llm_calls, "i": input_tokens, "o": output_tokens, "d": duration_ms, "cost": cost_usd},
        )

    async def append_ops_log(self, proposal_id: str, entries: list[dict[str, Any]]) -> None:
        """Append to ``ops_log`` atomically (JSONB concatenation)."""
        if not entries:
            return
        await self._s.execute(
            text("UPDATE ai_proposals SET ops_log = ops_log || CAST(:e AS jsonb), updated_at = now() WHERE id = :id"),
            {"id": proposal_id, "e": json.dumps(entries, default=str)},
        )

    async def fail_stale_building(self, older_than_s: float, *, code: str = "deadline", message: str | None = None) -> list[str]:
        """The stale reaper: ``building`` rows untouched for ``older_than_s`` seconds become ``failed``. Returns their ids."""
        res = await self._s.execute(
            text("""UPDATE ai_proposals SET status = 'failed', error_code = :code, error_message = :msg, updated_at = now()
                    WHERE status = 'building' AND updated_at < now() - make_interval(secs => :secs) RETURNING id"""),
            {"secs": float(older_than_s), "code": code, "msg": message or "The build did not finish before its deadline."},
        )
        return [r[0] for r in res.all()]

    # ── events (append-only) ──────────────────────────────────────────────────

    async def add_event(
        self, proposal_id: str, event: str, payload: dict[str, Any] | None = None, actor: str | None = None,
    ) -> int:
        """Append one event and return its ``seq`` (1, 2, 3 ... per proposal).

        The seq is taken in the INSERT itself and the unique ``(proposal_id, seq)`` arbitrates concurrent writers:
        the loser's ``ON CONFLICT DO NOTHING`` returns no row and it retries with the new max. No gaps are created
        by the retry, and no lock is held between statements.
        """
        params = {"p": proposal_id, "e": event, "a": actor, "pl": json.dumps(payload or {}, default=str)}
        for _ in range(20):
            res = await self._s.execute(
                text("""INSERT INTO ai_proposal_events (proposal_id, seq, actor, event, payload)
                        SELECT :p, COALESCE(MAX(seq), 0) + 1, :a, :e, CAST(:pl AS jsonb)
                        FROM ai_proposal_events WHERE proposal_id = :p
                        ON CONFLICT (proposal_id, seq) DO NOTHING RETURNING seq"""),
                params,
            )
            row = res.first()
            if row is not None:
                return int(row[0])
        raise RuntimeError(f"could not allocate an event seq for {proposal_id}")

    async def list_events(self, proposal_id: str, after: int = 0, limit: int = 500) -> tuple[list[dict[str, Any]], int]:
        """``(events with seq > after, last_seq)``; ``last_seq`` is the newest seq overall (0 when none)."""
        res = await self._s.execute(
            text("""SELECT seq, at, actor, event, payload FROM ai_proposal_events
                    WHERE proposal_id = :p AND seq > :after ORDER BY seq LIMIT :lim"""),
            {"p": proposal_id, "after": int(after), "lim": max(1, min(int(limit), 1000))},
        )
        items = []
        for m in res.mappings().all():
            d = dict(m)
            if isinstance(d["payload"], (str, bytes)):
                d["payload"] = json.loads(d["payload"])
            items.append(d)
        last = (await self._s.execute(
            text("SELECT COALESCE(MAX(seq), 0) FROM ai_proposal_events WHERE proposal_id = :p"), {"p": proposal_id}
        )).scalar()
        return items, int(last or 0)
