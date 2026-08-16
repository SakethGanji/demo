"""Persistence for dataset relationships (ROADMAP §22)."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

# Sheet keys are joined from the logical sheets, so a caller always sees the
# CURRENT names of both endpoints however many renames they have been through.
_COLS = """r.id::text, r.dataset_id::text,
           r.from_logical_sheet_id::text, r.from_column,
           fs.current_sheet_key AS from_sheet,
           r.to_dataset_id::text, r.to_logical_sheet_id::text, r.to_column,
           ts.current_sheet_key AS to_sheet,
           r.status, r.method, r.evidence, r.confidence, r.algorithm_version,
           r.created_by::text, r.reviewed_by::text,
           r.created_at::text AS created_at, r.updated_at::text AS updated_at"""
_FROM = """dataset_relationships r
           JOIN dataset_sheets fs ON fs.id = r.from_logical_sheet_id
           JOIN dataset_sheets ts ON ts.id = r.to_logical_sheet_id"""


async def upsert_relationship(
    *, dataset_id: str, from_logical_sheet_id: str, from_column: str,
    to_dataset_id: str, to_logical_sheet_id: str, to_column: str,
    method: str, evidence: dict, confidence: float | None,
    created_by: str | None, algorithm_version: int = 1,
) -> dict:
    """Insert or refresh a suggestion for a directed (sheet, column) pair.

    Idempotent by design — discovery can be re-run at will. A human verdict is
    never clobbered: an existing ``confirmed`` or ``rejected`` row keeps its
    status (and a rejected edge keeps its evidence, so re-running discovery
    cannot quietly resurrect something a steward turned down).

    Provenance is ranked, not last-writer-wins. The conflict key is the
    directed (sheet, column) pair with no ``method`` component, so a declared
    edge — seeded from a ``foreign_key`` rule, or typed in by hand — and a
    discovered one are the SAME row whenever discovery picks that orientation.
    Discovery runs after seeding in every realistic order of operations, so
    plain ``EXCLUDED.*`` meant an ``fk_rule`` edge silently became
    ``statistical`` with ``confidence`` dropped from 1.0 to a score and
    ``evidence.rule_id``/``rule_name`` — the reviewer's only link back to the
    rule that declared it — deleted. A measurement never outranks a
    declaration, so a ``statistical`` write leaves ``method``, ``confidence``
    and the declared evidence of an ``fk_rule``/``manual`` row alone and hangs
    its own signals under ``evidence.statistical`` instead, where they
    corroborate the rule rather than replace it. A ``manual`` or ``fk_rule``
    write still wins outright: those come from a person.
    """
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO dataset_relationships
                    (dataset_id, from_logical_sheet_id, from_column,
                     to_dataset_id, to_logical_sheet_id, to_column,
                     method, evidence, confidence, algorithm_version, created_by)
                VALUES (:did, :fsid, :fcol, :tdid, :tsid, :tcol, :method,
                        CAST(:evidence AS jsonb), :confidence, :alg, :uid)
                ON CONFLICT (from_logical_sheet_id, from_column,
                             to_logical_sheet_id, to_column)
                DO UPDATE SET
                    evidence = CASE
                        WHEN dataset_relationships.status = 'rejected'
                            THEN dataset_relationships.evidence
                        WHEN EXCLUDED.method = 'statistical'
                             AND dataset_relationships.method IN ('fk_rule', 'manual')
                            THEN dataset_relationships.evidence
                                 || jsonb_build_object('statistical',
                                                       EXCLUDED.evidence)
                        -- A person re-declaring an already-REVIEWED edge must not
                        -- overwrite its provenance: ``method`` is frozen below
                        -- once status <> 'suggested', so leaving evidence/
                        -- confidence writable let a manual re-declare keep a
                        -- (say) fk_rule badge while destroying its rule_id, or
                        -- keep a statistical badge while fabricating a 1.0. The
                        -- three fields must move together or they contradict.
                        WHEN EXCLUDED.method IN ('fk_rule', 'manual')
                             AND dataset_relationships.status <> 'suggested'
                            THEN dataset_relationships.evidence
                        ELSE EXCLUDED.evidence END,
                    confidence = CASE
                        WHEN dataset_relationships.status = 'rejected'
                             OR (EXCLUDED.method = 'statistical'
                                 AND dataset_relationships.method
                                     IN ('fk_rule', 'manual'))
                             OR (EXCLUDED.method IN ('fk_rule', 'manual')
                                 AND dataset_relationships.status <> 'suggested')
                            THEN dataset_relationships.confidence
                        ELSE EXCLUDED.confidence END,
                    method = CASE
                        WHEN dataset_relationships.status <> 'suggested'
                             OR (EXCLUDED.method = 'statistical'
                                 AND dataset_relationships.method
                                     IN ('fk_rule', 'manual'))
                            THEN dataset_relationships.method
                        ELSE EXCLUDED.method END,
                    updated_at = now()
                RETURNING id::text, (xmax = 0) AS inserted
            """),
            {"did": dataset_id, "fsid": from_logical_sheet_id, "fcol": from_column,
             "tdid": to_dataset_id, "tsid": to_logical_sheet_id, "tcol": to_column,
             "method": method, "evidence": json.dumps(evidence),
             "confidence": confidence, "alg": algorithm_version, "uid": created_by},
        )).mappings().one()
        await s.commit()
    rel = await get_relationship(row["id"])
    if rel is not None:
        # (xmax = 0) is true only for a fresh INSERT; an ON CONFLICT UPDATE sets
        # xmax. Lets callers count newly-created edges vs refreshed ones (a
        # re-seed that changed nothing should report created: 0, not len(rules)).
        rel["inserted"] = bool(row["inserted"])
    return rel


async def get_relationship(relationship_id: str) -> dict | None:
    if not is_uuid(relationship_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_COLS} FROM {_FROM} WHERE r.id = :id"),
            {"id": relationship_id},
        )).mappings().first()
        return dict(row) if row else None


async def list_relationships(dataset_id: str, *, status: str | None,
                             limit: int, offset: int,
                             visible_team_ids: list[str] | None = None,
                             ) -> tuple[list[dict], int]:
    """Edges owned by a dataset, newest-confidence first.

    ``visible_team_ids`` (None = no restriction, i.e. a superuser) drops
    cross-dataset edges whose TARGET lives in a team the caller cannot read:
    the row exposes the target's dataset id and current sheet key, which is
    enough to learn that another team's dataset exists. It is applied to the
    count as well as the page, so ``total`` never describes rows the caller is
    not being shown.
    """
    where = "r.dataset_id = :did" + (" AND r.status = :status" if status else "")
    params: dict = {"did": dataset_id}
    if status:
        params["status"] = status
    if visible_team_ids is not None:
        where += (" AND (r.to_dataset_id = r.dataset_id"
                  "      OR EXISTS (SELECT 1 FROM datasets d"
                  "                 WHERE d.id = r.to_dataset_id"
                  "                   AND d.team_id::text = ANY(:tids)))")
        params["tids"] = [str(t) for t in visible_team_ids]
    async with async_session_factory() as s:
        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM dataset_relationships r WHERE {where}"),
            params)).scalar()
        rows = (await s.execute(
            text(f"SELECT {_COLS} FROM {_FROM} WHERE {where} "
                 f"ORDER BY r.confidence DESC NULLS LAST, r.created_at "
                 f"LIMIT :limit OFFSET :offset"),
            {**params, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def set_status(relationship_id: str, status: str,
                     reviewed_by: str | None) -> dict | None:
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                UPDATE dataset_relationships
                SET status = :status, reviewed_by = :uid, updated_at = now()
                WHERE id = :id RETURNING id::text
            """),
            {"id": relationship_id, "status": status, "uid": reviewed_by},
        )).mappings().first()
        await s.commit()
    return await get_relationship(relationship_id) if row else None


async def delete_relationship(dataset_id: str, relationship_id: str) -> bool:
    if not is_uuid(relationship_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM dataset_relationships "
                 "WHERE id = :id AND dataset_id = :did"),
            {"id": relationship_id, "did": dataset_id})
        await s.commit()
        return result.rowcount > 0


async def find_confirmed_between(from_logical_sheet_id: str,
                                 to_logical_sheet_id: str) -> list[dict]:
    """Confirmed edges linking two logical sheets, in either direction.

    Used by §24 to resolve coordinated-sampling keys from a relationship
    instead of explicit columns.
    """
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"""
                SELECT {_COLS} FROM {_FROM}
                WHERE r.status = 'confirmed'
                  AND ((r.from_logical_sheet_id = :a AND r.to_logical_sheet_id = :b)
                    OR (r.from_logical_sheet_id = :b AND r.to_logical_sheet_id = :a))
                ORDER BY r.created_at
            """),
            {"a": from_logical_sheet_id, "b": to_logical_sheet_id},
        )).mappings().all()
        return [dict(r) for r in rows]
