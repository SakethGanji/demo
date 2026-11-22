"""F-JOBS: the long version writes as queued jobs with progress.

Above `progress.threshold_rows()` rows (ENSEMBLE_JOB_ROWS, default 200,000) these routes answer **202** with a job id
instead of holding the request for the whole write (≈ 38 s for a 10M-row edit, 28–103 s for a Data Doctor apply):

| route | job kind | handler |
|---|---|---|
| POST /api/datasets/{id}/edit (not preview) | `edit` | `run_edit` |
| POST /api/datasets/{id}/data-doctor/apply | `doctor_apply` | `run_doctor_apply` |

(Feature Lab already runs as a `featurelab` job (P22); its SQL write reports progress and commits by job id too.)

The client polls `GET /api/jobs/{id}`: `progress` {pct, phase} while it runs; `result` = the body the synchronous route
would have returned (plus `status_code`) when it succeeds; `result.refused` {status, detail} when the route would have
answered 4xx. At or below the threshold the routes answer synchronously, as before.

**Idempotent by job id.** The VersionWriter records `datasets.job_id` in the commit transaction and a re-leased job
(lease expired, worker killed) gets its own version back instead of a second one (`deduped = "job"`); the write path
then finishes the post-commit steps (all upserts). A job whose version is already finished (its lineage edge exists)
returns at once without writing again.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

from . import progress as JP
from . import queue as Q


class JobRefused(Exception):
    """A 4xx the synchronous route would have answered; the job fails with result.refused = {status, detail}."""

    def __init__(self, status: int, detail: Any) -> None:
        msg = detail.get("message") if isinstance(detail, dict) else detail
        super().__init__(str(msg))
        self.job_refusal = {"status": int(status), "detail": detail, "message": str(msg)[:500]}


def team_of(db: Any, dataset_id: str) -> str | None:
    with db.connect() as con:
        r = con.execute("SELECT team_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return r[0] if r else None


def rows_of(db: Any, dataset_id: str) -> int:
    rec = db.get_dataset(dataset_id) or {}
    try:
        return int(rec.get("rows") or 0)
    except (TypeError, ValueError):
        return 0


def accepted(job: dict[str, Any], rows: int, what: str) -> dict[str, Any]:
    return {"async": True, "job_id": job["id"], "job": {k: job.get(k) for k in ("id", "kind", "status", "created_at")},
            "status": "queued", "poll": f"/api/jobs/{job['id']}", "rows": rows, "threshold_rows": JP.threshold_rows(),
            "note": f"{rows:,} rows is above {JP.threshold_rows():,}: the {what} runs as a job. Poll the job for its "
                    f"progress; its result is the new version."}


def enqueue(db: Any, kind: str, dataset_id: str, payload: dict[str, Any], by: str | None) -> dict[str, Any]:
    from ..catalog import governance as G

    return Q.enqueue(db, kind, {"dataset_id": dataset_id, "by": by, "reason_header": G.current_reason(), **payload},
                     resource="duck", by=by or G.current_actor() or "system", team_id=team_of(db, dataset_id),
                     ref_kind="dataset", ref_id=dataset_id)


@contextmanager
def _acting(pl: dict[str, Any]) -> Iterator[None]:
    """The request's X-Ensemble-Actor / X-Ensemble-Reason, as the route saw them, for code that reads them."""
    from ..catalog import governance as G

    t1 = G.ACTOR.set(pl.get("by"))
    t2 = G.REASON.set(pl.get("reason_header"))
    try:
        yield
    finally:
        G.ACTOR.reset(t1)
        G.REASON.reset(t2)


def _finished_version(db: Any, job_id: str, parent: str) -> str | None:
    """The version this job already committed AND finished (its derived_from edge is written last), else None."""
    with db.connect() as con:
        r = con.execute("SELECT id FROM datasets WHERE job_id = ?", (job_id,)).fetchone()
        if not r:
            return None
        e = con.execute("SELECT 1 FROM lineage_edges WHERE src_kind = 'dataset' AND src_id = ? AND dst_kind = 'dataset'"
                        " AND dst_id = ? AND relation = 'derived_from'", (parent, r[0])).fetchone()
    return r[0] if e else None


def _refusal(e: Exception, scrub: Any = None) -> JobRefused | None:
    from fastapi import HTTPException

    from ..catalog import dsets as D
    from ..datasets import DatasetConflict, DatasetError
    from ..lake.values import ValueRefused

    if isinstance(e, ValueRefused):
        st, det = e.status, dict(e.payload)
    elif isinstance(e, HTTPException) and 400 <= e.status_code < 500:
        st, det = e.status_code, e.detail
    elif isinstance(e, D.DsError):
        st, det = e.status, e.payload
    elif isinstance(e, DatasetConflict):
        st, det = 409, str(e)
    elif isinstance(e, DatasetError):
        st, det = 400, str(e)
    else:
        return None
    return JobRefused(st, scrub(det) if scrub else det)


# ------------------------------------------------------------------------------------------------------------ edit


def run_edit(ctx) -> dict[str, Any]:
    """Worker side of a queued edit: edit_version with this job's id, then the default settled once."""
    from ..catalog import dsets as D
    from ..catalog import governance as G
    from ..lake import mask as M
    from ..main import _detail

    pl, st = ctx.payload, ctx.state
    db, store, jid = st.db, st.datasets, ctx.job["id"]
    dataset_id = pl["dataset_id"]
    app = SimpleNamespace(state=st)
    with _acting(pl):
        m = M.masked_view(dataset_id)
        sent = M.tagged_values(m, pl.get("ops")) if m is not None else {}

        def scrub(detail: Any) -> Any:
            return M.scrub_error(detail, m, sent) if sent else detail

        done = _finished_version(db, jid, dataset_id)
        if done:  # an earlier attempt finished everything but closing the job
            rec = db.get_dataset(done) or {}
            lin = (rec.get("profile") or {}).get("lineage") or {}
            JP.mark(1.0, "done")
            return {"status_code": 201, **_detail(rec), "created": True, "resumed": True,
                    "edit": {k: lin.get(k) for k in ("ops_summary", "checks", "target", "matched", "changed", "added", "removed")},
                    "default": _default_after(app, done, pl, [], rerun=True)}
        try:
            out = store.edit_version(dataset_id, pl.get("ops"), by=pl["by"], reason=pl["reason"], set_target=pl.get("set_target"),
                                     acknowledge=pl.get("acknowledge"))
        except Exception as e:  # noqa: BLE001
            r = _refusal(e, scrub)
            if r is None:
                raise
            if r.job_refusal["status"] == 409:
                G.refused(db, pl["by"], "dataset_edit", 409, r.job_refusal["detail"], {"dataset_id": dataset_id})
            raise r from None
        if not out.get("created"):
            JP.mark(1.0, "done")
            return json.loads(json.dumps({"status_code": 200, **_detail(out), "created": False, "duplicate_of": out["id"],
                                          "edit": out.get("edit"),
                                          "note": "the result has the same bytes as this existing version: nothing new was made"},
                                         default=str))
        warnings = out["edit"]["checks"]["warnings"]
        dflt = _default_after(app, out["id"], pl, warnings, rerun=False)
        edit_out = dict(out["edit"])
        JP.mark(1.0, "done")
        return json.loads(json.dumps({"status_code": 201, **_detail(db.get_dataset(out["id"]) or out), "created": True,
                                      "edit": m.scrub(edit_out) if m is not None else edit_out, "default": dflt}, default=str))


def _default_after(app: Any, vid: str, pl: dict[str, Any], warnings: list[dict[str, Any]], rerun: bool) -> dict[str, Any]:
    """settle_default once: a re-run that finds the version already the default does not write (or audit) it again."""
    from ..catalog import dsets as D

    if D.current_default(app, vid) == vid:
        return {"is_default": True, "why": "no contract warnings: it is now the default"}
    if rerun and not warnings:
        warnings = list(((app.state.db.get_dataset(vid) or {}).get("profile") or {}).get("lineage", {}).get("checks", {}).get("warnings") or [])
    return D.settle_default(app, vid, pl.get("before"), warnings, pl["by"], pl.get("make_default") or "auto")


# ------------------------------------------------------------------------------------------------------------ doctor


def run_doctor_apply(ctx) -> dict[str, Any]:
    """Worker side of a queued Data Doctor apply: the route's own _apply, with this job's id on the commit."""
    from .. import data_doctor_api as DDA
    from ..main import _detail

    pl, st = ctx.payload, ctx.state
    db, jid = st.db, ctx.job["id"]
    dataset_id = pl["dataset_id"]
    req = SimpleNamespace(app=SimpleNamespace(state=st))
    with _acting(pl):
        done = _finished_version(db, jid, dataset_id)
        if done:
            rec = db.get_dataset(done) or {}
            JP.mark(1.0, "done")
            return {"status_code": 201, "dataset": _detail(rec), "parent": dataset_id, "created": True, "resumed": True}
        try:
            out = DDA._canonical_400(DDA._apply, req, dataset_id, pl.get("transforms"), pl.get("by"), pl.get("acknowledge"))
        except Exception as e:  # noqa: BLE001
            r = _refusal(e)
            if r is None:
                raise
            raise r from None
    JP.mark(1.0, "done")
    return json.loads(json.dumps({"status_code": 200 if out.get("deduplicated") else 201, **out}, default=str))
