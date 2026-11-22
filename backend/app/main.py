"""The FastAPI app. Run with: uvicorn app.main:app --app-dir backend --port 8000"""

from __future__ import annotations

import asyncio

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from . import __version__, config, frames
from .datasets import DatasetError, DatasetStore
from .db import Database
from .autopilot import api as autopilot_api
from .autopilot.engine import Manager as AutopilotManager
from .engine import api as engine_api
from .engine import importer as import_api
from .forecast import api as forecast_api
from .anomaly import api as anomaly_api
from .gym import api as gym_api
from .vela import api as vela_api
from .agent import api as agent_api
from .agent.loop import AgentManager
from . import llm, llm_api
from . import data_doctor_api, featurelab_api
from .engine.runner import JobRunner
from .engine import active_learning_api, annotations_api, learning_curve_api, shift_api, slices_api
from .engine.pool import HeavyPool
from .registry import api as registry_api
from .registry import changes_api, monitoring_api
from .registry import credit_report_api
from .registry.store import RegistryStore
from .engine.serving import PredictorCache
from .engine.store import RunStore
from .catalog import api as catalog_api
from .catalog import datasets_api as catalog_datasets_api
from .catalog import teams_api
from .catalog.retention import EvictedError
from .authz import api as authz_api
from .authz import body, path, query, require, with_team

log = logging.getLogger("ensemble.main")
from .http import dataset_or_404
from .samples import SAMPLES
from .samples_g3 import SAMPLES_G3
from .samples_g4 import SAMPLES_G4
from .samples_g5 import SAMPLES_G5
from .samples_g6 import SAMPLES_G6
from .samples_g7 import SAMPLES_G7
from .samples_g8 import SAMPLES_G8
from .segmented import api as segmented_api
from .survival import api as survival_api
from .multilabel import api as multilabel_api
from .sqlsource import api as sql_api
from .reject import api as reject_api
from .el import api as el_api
from .fairlending import api as fairlending_api
from .splits import summarize as summarize_split
from .schemas import Dataset, DatasetList, Health

# The background-job modules: shut down with the API, block an admin reset while busy (409 message), and own a router.
JOB_MODULES = (
    (learning_curve_api, "A learning curve is running; cancel it first."),
    (active_learning_api, "A labelling queue is working; cancel it first."),
    (annotations_api, "Annotator class probabilities are being computed; wait for them."),
    (shift_api, "A shift check is running; cancel it first."),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _summary(rec: dict[str, Any]) -> dict[str, Any]:
    p = rec["profile"]
    return {
        "id": rec["id"], "name": rec["name"], "filename": rec["filename"], "source": rec["source"],
        "sample": rec["source"] == "sample", "note": rec["note"], "created_at": rec["created_at"],
        "size_bytes": rec["size_bytes"], "rows": rec["rows"], "n_cols": p["n_cols"], "kind": rec["kind"],
        "target": rec["target"], "n_classes": p["n_classes"],
        "flows": {"timeseries": ["forecast"], "transactions": ["anomaly"]}.get(rec["kind"], ["tabular", "anomaly"]),
        # DL1: where it sits in its dataset (root, parent, op) and whether it is archived / deleted
        "root_id": rec.get("root_id") or rec["id"], "parent_id": rec.get("parent_id"), "op": rec.get("op"), "state": rec.get("state"),
    }


def _detail(rec: dict[str, Any]) -> dict[str, Any]:
    """A dataset with its stored profile. P20: the profile's values of PII-tagged columns (example, top values, range,
    numeric stats) are masked (`profiling.mask_profile`), wherever a detail is returned (GET, upload, edit, patch)."""
    from . import profiling
    from .lake import mask as M

    out = _detail_raw(rec)
    m = M.masked_view(rec["id"]) if rec.get("id") else None
    if m is not None and m.cols:
        out["profile"] = profiling.mask_profile(out.get("profile"), {c: m.fn(c) for c in m.cols})
    return out


def _detail_raw(rec: dict[str, Any]) -> dict[str, Any]:
    return {**_summary(rec), "profile": rec["profile"]}


def catalog_backfill(db: Database, data_dir) -> dict[str, Any]:
    from .catalog.schema import backfill, backfill_fp2, backfill_p01

    with db.connect() as con:
        out = backfill(con, data_dir)
    if not out.get("skipped"):
        log.info("P1 catalog backfill: %s", out)
    with db.connect() as con:
        out2 = backfill_fp2(con, data_dir)  # F-P2: phantom projects, root ops, predictor 'none', review edges
    if not out2.get("skipped"):
        log.info("F-P2 catalog backfill: %s", out2)
    with db.connect() as con:
        out3 = backfill_p01(con)  # P01: tm_default, principals, dataset_roots, version numbers, team ids; then audit triggers
    if not out3.get("skipped"):
        log.info("P01 catalog backfill: %s", out3)
    return {**out, "fp2": out2, "p01": out3}


async def _verify_timer(app: FastAPI) -> None:
    """DL2: re-hash every dataset version every ENSEMBLE_VERIFY_EVERY_HOURS hours (off unless set); a mismatch is
    audited as dataset_integrity_failed and then blocks training on that version."""
    from .catalog import dsets

    while True:
        await asyncio.sleep(3600 * (config.verify_every_hours() or 24))
        try:
            out = await run_in_threadpool(dsets.verify_all, app, "scheduled verify")
            log.info("scheduled verify: %s versions, %s failed", out["checked"], len(out["failed"]))
        except Exception:  # noqa: BLE001
            log.exception("scheduled verify failed")


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        store = DatasetStore(settings, db)
        await run_in_threadpool(store.ensure_samples)  # first start: generate the samples
        await run_in_threadpool(catalog_backfill, db, settings.data_dir)  # P1: once per database (idempotent)
        app.state.settings, app.state.db, app.state.datasets = settings, db, store
        app.state.catalog = store.catalog  # P1: projects, edges, tags and notes, champions
        llm.configure(settings.data_dir)  # F5: runtime timeouts / model / switches from data/llm_settings.json
        llm.warm()
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        app.state.runs = RunStore(db)
        app.state.runner = JobRunner(settings, app.state.runs)
        app.state.runner.start()  # marks runs interrupted by a previous stop as failed
        app.state.predictors = PredictorCache()
        app.state.registry = RegistryStore(db)
        app.state.registry.recover()  # builds and batches cut off by a previous stop are marked failed
        app.state.jobs = {}
        app.state.heavy = HeavyPool(db=db)  # P22: version builds and batch scores go on the durable queue
        app.state.heavy.start()  # SHAP, importance, predictions run here
        app.state.autopilot = AutopilotManager(app, settings.data_dir / "autopilot")
        app.state.autopilot.resume_all()  # autopilots cut off by a restart continue from their saved state
        app.state.agent = AgentManager(app, settings.data_dir / "agent")  # G10: the Vela agent's conversations
        from .vela.api import warm_doctor
        from .vela import review as review_mod
        from .engine.jobs import dev_reload
        from .engine.workerd import Worker, embedded_slots

        loop = asyncio.get_running_loop()
        verify_task = asyncio.ensure_future(_verify_timer(app)) if config.verify_every_hours() else None
        app.state.runner.on_success = lambda rid: loop.call_soon_threadsafe(lambda: asyncio.ensure_future(warm_doctor(app, rid)))
        for mod in (learning_curve_api, active_learning_api, review_mod, annotations_api, shift_api):  # F-JOBS: +2
            mod.bind(db)
        # P22: the API's own worker: agent turns (`api`), the janitor, the success/heavy pollers, and with
        # ENSEMBLE_WORKERD=embedded (default) train/duck/light slots too; `python -m app.engine.workerd` adds more.
        app.state.worker = Worker(app.state, embedded_slots(), role="api", embedded=True, loop=loop)
        app.state.worker.app = app
        app.state.worker.hooks += [app.state.runner.poll_finished, app.state.heavy.poll_durable]
        app.state.worker.start()
        yield
        if verify_task:
            verify_task.cancel()
        app.state.agent.shutting_down = True
        app.state.worker.stop(requeue=True, kill=not dev_reload())  # held jobs go back on the queue
        app.state.agent.shutdown()
        app.state.runner.shutdown()
        for mod, _ in JOB_MODULES:
            mod.shutdown()
        app.state.heavy.shutdown()

    from .lake.values import SafeJSONResponse, ValueRefused

    # P03 (§4.4): every JSON response through one encoder (non-finite → null + "non_finite": true, allow_nan=False)
    app = FastAPI(title="ENSEMBLE demo API", version=__version__, lifespan=lifespan, default_response_class=SafeJSONResponse)
    from .lake import api as lake_api

    lake_api.install(app, settings)  # P02: the lake self-check (refuses to start on a hash mismatch), the store, /lake routes

    @app.exception_handler(ValueRefused)
    async def value_refused(request: Request, exc: ValueRefused):
        """P03 (§4.4, DLA4 #1): a value that does not fit its column → 400 {detail: {code, message, op_index, column,
        type, min, max}}. Never engine text."""
        return SafeJSONResponse({"detail": exc.payload}, status_code=exc.status)

    @app.middleware("http")
    async def acting_as(request: Request, call_next):
        """DL2: the UI's "Acting as" name (X-Ensemble-Actor) and an optional reason / change ticket (X-Ensemble-Reason)
        reach the audit log of paths that take no `by` (reads, in-app versions)."""
        from .catalog import governance as G

        t1 = G.ACTOR.set(request.headers.get("x-ensemble-actor"))
        t2 = G.REASON.set(request.headers.get("x-ensemble-reason"))
        try:
            return await call_next(request)
        finally:
            G.ACTOR.reset(t1)
            G.REASON.reset(t2)

    def store(request: Request) -> DatasetStore:
        return request.app.state.datasets

    @app.get("/api/health", response_model=Health)
    def health(request: Request) -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "datasets": request.app.state.db.count_datasets(),
                "data_dir": str(settings.data_dir), "runs": request.app.state.runs.count(),
                "engine": engine_api.engine_info()}

    @app.get("/api/datasets", response_model=DatasetList)
    def list_datasets(request: Request, all: bool = False, limit: int | None = None, after: str | None = None,
                      project_id: str | None = None, root_id: str | None = None) -> dict[str, Any]:
        """Samples first, in a fixed order; then uploads, oldest first. DL1: archived and deleted ones are hidden
        (all=true shows them). P08: `limit` (≤ 500) pages the list by keyset (`after` = the `next_after` of the last
        page) from light columns (no profile JSON is parsed); without it the whole list comes back as before.
        `project_id` / `root_id` narrow it."""
        order = [*SAMPLES, *SAMPLES_G3, *SAMPLES_G4, *SAMPLES_G5, *SAMPLES_G6, *SAMPLES_G7, *SAMPLES_G8]
        if limit is not None:
            page = request.app.state.db.list_datasets_page(limit=max(1, min(int(limit), 500)), after=after, include_hidden=all,
                                                           pinned=order, project_id=project_id, root_id=root_id)
            return with_team(request, {"datasets": [_summary(r) for r in page["rows"]], "next_after": page["next_after"],
                                       "total": page["total"]}, "datasets")
        recs = sorted([r for r in request.app.state.db.list_datasets(light=True) if all or not r.get("state")],
                      key=lambda r: (0, order.index(r["id"]), "") if r["id"] in order else (1, 0, r["created_at"]))
        return with_team(request, {"datasets": [_summary(r) for r in recs]}, "datasets")

    @app.get("/api/datasets/diff", dependencies=[require("data.preview", query("a", "dataset"), also=[("data.preview", query("b", "dataset"))])])
    async def datasets_diff(request: Request, a: str = "", b: str = "", key: str = "", by: str = "", wait: float = 0) -> dict[str, Any]:
        """P08: GET /datasets/diff?a=&b=[&key=col,col] — the two-pass diff of version a → b (see GET …/{a}/diff/{b})."""
        from .catalog.datasets_api import diff_q

        return await diff_q(request, a, b, by, key, wait)

    @app.get("/api/datasets/{dataset_id}", response_model=Dataset,
             dependencies=[require("data.read_meta", path("dataset_id", "dataset"))])  # P21: a shared version's scope
    def get_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
        rec = dataset_or_404(request, dataset_id)
        return _detail(rec)  # P20: a PII column's example / top values / range are shown masked

    @app.post("/api/datasets", response_model=Dataset, status_code=201,
               dependencies=[require("data.upload", body("project_id", "project"))])
    async def upload_dataset(request: Request, file: UploadFile = File(...), target: str | None = Form(None),
                             project_id: str | None = Form(None), by: str | None = Form(None)) -> dict[str, Any]:
        """A CSV upload. F-P2: `project_id` puts it in an existing project (default: a new project of its own).
        P05: a wrapper of /uploads → check → commit: the file streams into staging and becomes a Parquet dataset."""
        from .lake.writer import LakeError

        if project_id and not request.app.state.catalog.project(project_id):
            raise HTTPException(404, f"No project '{project_id}'.")
        try:
            rec = await run_in_threadpool(store(request).create_from_upload, file.filename or "", file.file, target or None,
                                           (by or "").strip()[:80] or None, project_id or None, request.app.state.lake, file.size)
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        except LakeError as e:
            raise HTTPException(e.status, e.message) from e
        return _detail(request.app.state.db.get_dataset(rec["id"]) or rec)  # DL1: with root, op, created_by

    @app.patch("/api/datasets/{dataset_id}", response_model=Dataset, dependencies=[require("run.train", path("dataset_id", "dataset"))])
    def confirm_target(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Confirm the column to predict (F4). Body {"target": column}. Re-profiles for that target and draws (or
        reuses) its own holdout and folds, keyed by the target; the profile's `splits` describe that split."""
        target = (body or {}).get("target")
        if not isinstance(target, str) or not target:
            raise HTTPException(400, 'Send {"target": "<column to predict>"}.')
        dataset_or_404(request, dataset_id)
        try:
            rec = store(request).set_target(dataset_id, target)
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        return _detail(rec)

    @app.get("/api/datasets/{dataset_id}/splits")
    def dataset_splits(dataset_id: str, request: Request) -> dict[str, Any]:
        """Every split stored for the dataset, one per (target, strategy) used so far, and the current target's key."""
        rec = dataset_or_404(request, dataset_id)
        cur = rec["profile"].get("splits") or {}
        return {"dataset_id": dataset_id, "target": rec["target"], "current": cur.get("key"),
                "splits": store(request).list_splits(dataset_id)}

    @app.get("/api/datasets/{dataset_id}/split-options")
    def split_options(dataset_id: str, request: Request, target: str | None = None) -> dict[str, Any]:
        """F6: the split strategies (random, group, time, time + group), the date and entity columns found, the
        suggested strategy, and warnings for a random split on data with a date or entity column."""
        rec = dataset_or_404(request, dataset_id)
        return {"dataset_id": dataset_id, "target": target or rec["target"],
                **store(request).split_options(dataset_id, target or rec["target"])}

    @app.get("/api/datasets/{dataset_id}/intake")
    def dataset_intake(dataset_id: str, request: Request) -> dict[str, Any]:
        """G0 Smart Intake: each column's role (target, date, entity, money, ...) with a confidence and its evidence,
        and what can be built from the table, best first, each with a reason and the pre-fill for its flow."""
        return _intake(request, dataset_id, None)

    @app.post("/api/datasets/{dataset_id}/intake", dependencies=[require("data.read_meta", path("dataset_id", "dataset"))])
    def dataset_intake_edit(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """The same with the user's role edits, body {"roles": {"column": "role"}}: the suggestions follow them."""
        roles = (body or {}).get("roles") or {}
        if not isinstance(roles, dict):
            raise HTTPException(400, 'Send {"roles": {"<column>": "<role>"}}.')
        return _intake(request, dataset_id, roles)

    def _intake(request: Request, dataset_id: str, roles: dict[str, str] | None) -> dict[str, Any]:
        from .intake import intake

        rec = dataset_or_404(request, dataset_id)
        # A2 M9: a confirmed target (Data Report's y, the run's label) is the target here too, unless the user's role
        # edits name another; Smart Intake's own guess is only for an unconfirmed table
        tgt = rec.get("target")
        if tgt and (rec.get("profile") or {}).get("target_source") != "guessed" and not any(v == "target" for v in (roles or {}).values()):
            roles = {**(roles or {}), tgt: "target"}
        try:
            from .lake import stats as lake_stats  # P08: above 200k rows, a deterministic sample (never a full read)

            df, sampled = lake_stats.sample_frame(dataset_id)
            if tgt not in df.columns:
                roles = {k: v for k, v in (roles or {}).items() if k in df.columns} or None
            out = {"dataset_id": dataset_id, **intake(df, roles, versions=_binary_versions(request, dataset_id))}
            if sampled:
                out["sampled"] = sampled
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # D1: the Data Doctor's stored answers (not known at decision time, fairness only) join every pre-filled drop list.
        drops = data_doctor_api.run_drops(store(request), dataset_id, list(df.columns))
        if drops and drops["columns"]:
            for s in out["suggestions"]:
                p = s["prefill"]
                if s["route"] == "quick-train":
                    tgt = p.get("target")
                    p["drop"] = list(dict.fromkeys([*(p.get("drop") or []), *[c for c in drops["columns"] if c != tgt]]))
            out["decision_time_drops"] = drops
        return out

    def _binary_versions(request: Request, dataset_id: str) -> list[dict[str, Any]]:
        """Ready binary registry versions trained on this dataset, newest first (G1's recourse suggestion opens one)."""
        reg = request.app.state.registry
        out = []
        for m in reg.list_models():
            if m.get("dataset_id") != dataset_id:
                continue
            for v in reg.list_versions(m["name"]):
                ver = (v.get("result") or {}).get("version") or {}
                if v["status"] == "ready" and ver.get("problem_type") == "binary":
                    out.append({"id": v["id"], "model_name": v["model_name"], "version": v["version"], "label": m.get("label"),
                                "stage": v.get("stage"), "created_at": v.get("created_at")})
        return sorted(out, key=lambda v: v["created_at"] or "", reverse=True)

    @app.get("/api/datasets/{dataset_id}/rows", dependencies=[require("data.preview", path("dataset_id", "dataset"))])
    def dataset_rows(dataset_id: str, request: Request, value: str | None = None, column: str | None = None, limit: int | None = None,
                     by: str = "", after: int | None = None, offset: int | None = None, q: str = "", filter: str | None = None,
                     columns: str | None = None, sort: str | None = None, desc: bool = False, table: str | None = None,
                     unmasked: str = "", reason: str = "") -> dict[str, Any]:
        """P08 (R5): a page of rows in `__row_id` order, by keyset: `after` = the last `_row_id` seen (exclusive),
        `limit` ≤ 500 (50). `q` keeps rows where any column contains it (case-insensitive; the first `limit` hits; the
        count is GET …/rows/count). `filter` is the edit filter AST as JSON. `offset` jumps to a row position;
        `sort` (+ `desc`) orders by a column, refused above 1M rows ("filter first"). `columns` (comma list) narrows.
        G10: with `value`, the rows where `column` equals `value` as text (any text column when none is named), for
        looking up one applicant or account by its id. DL3 (m2): needs a name (by or the X-Ensemble-Actor header).
        P20: PII-tagged columns are masked (`masked_columns`); `unmasked=1&reason=…` shows them in clear to a principal
        with data.read_unmasked, and is audited as dataset_rows_read."""
        from . import frames
        from .catalog import governance as G
        from .catalog.datasets_api import _mask_err, _src_where
        from .lake import mask as M
        from .lake import pages as PG

        who = G.reader(by)
        dataset_or_404(request, dataset_id)
        try:
            m = M.for_request(request, dataset_id, table, unmasked=unmasked, reason=reason, by=who,
                              read="row_lookup" if value is not None else "rows_page")
            if value is not None:
                src = frames.scan(dataset_id, table, mask_for=m)
                hit = PG.lookup(src, value, column, limit or 3)
                G.read_audit(request.app.state.db, "row_lookup", dataset_id, {"column": hit["column"], "n": hit["n"]}, by=who)
                return m.scrub({"dataset_id": dataset_id, "column": hit["column"], "value": value, "n": hit["n"], "rows": hit["rows"],
                                **m.info()})  # P20: the looked-up value of a masked column is not echoed
            src, where, searched = _src_where(dataset_id, table, q, filter, m)
            cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
            pg = PG.page(src, after=after, offset=offset, limit=limit or 50, where=where, columns=cols, sort=sort, desc=desc,
                         positions=False)
        except PG.PageError as e:
            raise HTTPException(e.status, e.payload()) from e
        except M.MaskError as e:
            raise _mask_err(e) from e
        G.read_audit(request.app.state.db, "rows_page", dataset_id, {"after": after, "q": q[:80] or None,
                                                                    "filter": bool(filter), "n": len(pg["rows"])}, by=who)
        return {"dataset_id": dataset_id, "table": src.table, "total": src.n_rows, "after": after, "limit": limit or 50, "q": q,
                **({"searched_columns": searched} if q.strip() else {}), **pg, **m.info()}

    @app.post("/api/datasets/{dataset_id}/splits", dependencies=[require("run.train", path("dataset_id", "dataset"))])
    def draw_split(dataset_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """F6: draw (or reuse) the split for {target, strategy, group_column?, time_column?, cutoff?, tuning_share?}
        and describe it: rows, groups and dates per role (train, tuning, holdout, excluded), shared groups, and rows
        per period for time splits. Runs that name the same split (POST /api/runs, `split_spec`) reuse it."""
        from .splits import RANDOM_STRATIFIED

        rec = dataset_or_404(request, dataset_id)
        body = body or {}
        target = body.get("target") or rec["target"]
        strategy = body.get("strategy") or RANDOM_STRATIFIED
        try:
            _, sp, created = store(request).ensure_splits(dataset_id, target, strategy, body)
            y = frames.read_version(dataset_id, [target])[target] if target else None  # P08: the target column only
        except DatasetError as e:
            raise HTTPException(400, str(e)) from e
        return {"dataset_id": dataset_id, "drawn_now": created, **summarize_split(sp, y)}

    @app.post("/api/admin/reset-requests", status_code=201, dependencies=[require("admin.reset")])
    def reset_request(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """DL3 (C1): {by, reason}: ask for a reset while models or champions are live. Someone else approves
        (POST /api/admin/reset-requests/{id}/decide); then an admin runs POST /api/admin/reset with its request_id."""
        import secrets as _s

        from . import reset as reset_mod
        from .catalog import governance as G
        from .catalog.api import _text, _who
        from .catalog.schema import audit, now as _now

        body = body if isinstance(body, dict) else {}
        who = _who(body.get("by"))
        reason = _text(body, "reason", 10, 500, True)
        if not reset_mod.demo_mode():
            G.refused(request.app.state.db, who, "admin_reset_request", 403, "reset is disabled (ENSEMBLE_DEMO_MODE is off)")
            raise HTTPException(403, "Reset is disabled: on only when the API runs with ENSEMBLE_DEMO_MODE=1.")
        rid = "arq_" + _s.token_hex(4)
        with request.app.state.db.connect() as con:
            if con.execute("SELECT 1 FROM admin_requests WHERE action = 'reset' AND status IN ('pending', 'approved')").fetchone():
                raise HTTPException(409, "A reset request is already pending or approved and unused.")
            con.execute("INSERT INTO admin_requests (id, action, status, requested_by, requested_at, reason) VALUES (?, 'reset', 'pending', ?, ?, ?)",
                        (rid, who, _now(), reason))
            audit(con, who, "admin_reset_requested", comment=reason, details={"request_id": rid, "live": reset_mod.live_governance(con) + reset_mod.user_data(con)})
            return dict(con.execute("SELECT * FROM admin_requests WHERE id = ?", (rid,)).fetchone())

    @app.post("/api/admin/reset-requests/{rid}/decide", dependencies=[require("admin.reset")])
    def reset_decide(rid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """{by, approve, comment}: four eyes. The requester may withdraw (approve=false) but never approve, admin or not."""
        from .catalog import governance as G
        from .catalog.api import _who
        from .catalog.schema import audit, now as _now

        body = body if isinstance(body, dict) else {}
        who = _who(body.get("by"))
        approve = body.get("approve")
        if not isinstance(approve, bool):
            raise HTTPException(400, 'Send {"approve": true} or {"approve": false}.')
        db = request.app.state.db
        with db.connect() as con:
            r = con.execute("SELECT * FROM admin_requests WHERE id = ?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, f"No reset request '{rid}'.")
        if r["status"] != "pending":
            raise HTTPException(409, f"This request is {r['status']}.")
        from .authz.policy import four_eyes  # C §1.5 (P21): every decide path goes through one check

        mine = four_eyes("admin.reset", dict(r), who) is not None
        if approve and mine:
            msg = f"Four eyes: {r['requested_by']} asked for this reset, so someone else must approve it (admins included)."
            G.refused(db, who, "admin_reset_decide", 403, msg, {"request_id": rid})
            raise HTTPException(403, msg)
        status = "approved" if approve else ("withdrawn" if mine else "rejected")
        comment = str(body.get("comment") or "")[:500]
        with db.connect() as con:
            con.execute("UPDATE admin_requests SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                        (status, who, _now(), comment, rid))
            audit(con, who, f"admin_reset_{status}", comment=comment, details={"request_id": rid, "requested_by": r["requested_by"]})
            return dict(con.execute("SELECT * FROM admin_requests WHERE id = ?", (rid,)).fetchone())

    @app.get("/api/admin/reset-requests")
    def reset_requests(request: Request) -> dict[str, Any]:
        from . import reset as reset_mod

        with request.app.state.db.connect() as con:
            return {"demo_mode": reset_mod.demo_mode(), "live": reset_mod.live_governance(con) + reset_mod.user_data(con),
                    "requests": [dict(r) for r in con.execute("SELECT * FROM admin_requests ORDER BY requested_at DESC LIMIT 20")]}

    @app.post("/api/admin/reset", dependencies=[require("admin.reset")])
    def admin_reset(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Delete every run, registry entry, batch, autopilot and upload; regenerate the samples.
        Body {"confirm": "reset", "by": <admin>, "request_id"?, "reason"?}. DL3 (C1): only with ENSEMBLE_DEMO_MODE=1, only by
        an admin, and while models or champions are live only with an approved four-eyes request. The audit log is kept
        and gets a row for the reset (who, counts). Refused while a run is queued or running."""
        from . import reset as reset_mod
        from .catalog import governance as G

        body = body if isinstance(body, dict) else {}
        if body.get("confirm") != "reset":
            raise HTTPException(400, 'Send {"confirm": "reset", "by": "<admin>"} to delete all runs, models and uploads.')
        who = str(body.get("by") or "").strip()[:80]
        try:
            reset_mod.check(request.app.state.db, who, body.get("request_id"))
        except reset_mod.ResetRefused as e:
            G.refused(request.app.state.db, who or G.current_actor("unknown"), "admin_reset", e.status, e.payload, {"via": "api"})
            raise HTTPException(e.status, e.payload) from e
        st = request.app.state
        if st.runs.list_active():
            raise HTTPException(409, "A run is queued or running; cancel it first.")
        for mod, busy in JOB_MODULES:
            if mod.active():
                raise HTTPException(409, busy)
        if any(getattr(a, "is_alive", lambda: False)() for a in getattr(st.autopilot, "threads", {}).values()):
            raise HTTPException(409, "An autopilot is running; cancel it first.")
        if any(not t.done() for t in st.agent.tasks.values()):
            raise HTTPException(409, "The Vela agent is working on a conversation; stop it first.")
        st.predictors = PredictorCache()
        registry_api.clear_serving_caches()
        try:
            out = reset_mod.reset(settings, by=who, request_id=body.get("request_id"), reason=str(body.get("reason") or ""), via="api")
        except reset_mod.ResetRefused as e:
            raise HTTPException(e.status, e.payload) from e
        st.autopilot.store.root.mkdir(parents=True, exist_ok=True)
        st.autopilot.threads.clear()
        st.agent.sessions.clear()
        st.agent.root.mkdir(parents=True, exist_ok=True)
        return out

    app.include_router(import_api.router)
    app.include_router(engine_api.router)
    app.include_router(catalog_datasets_api.router)  # DL1: dataset versions, diff, usage, default, card, archive, delete
    app.include_router(catalog_api.router)  # P1: projects, lineage, compare, annotations, champions, retention
    app.middleware("http")(catalog_api.evicted_guard)
    app.add_exception_handler(EvictedError, catalog_api.evicted_handler)
    app.include_router(teams_api.router)  # P18: /me, teams, members, principals
    from .catalog import certify as certify_api, contracts as contracts_api, shares as shares_api

    app.include_router(contracts_api.router)  # P21: contracts, certifications, shares
    app.include_router(certify_api.router)
    app.include_router(shares_api.router)
    from .jobs import api as jobs_api

    app.include_router(jobs_api.router)  # P22: GET /jobs, /jobs/{id}, /workers
    app.middleware("http")(teams_api.see_actor)  # P18: a principal on first sight of an X-Ensemble-Actor name
    for mod, _ in JOB_MODULES:
        app.include_router(mod.router)
    app.include_router(slices_api.router)
    app.include_router(forecast_api.router)
    app.include_router(anomaly_api.router)
    app.include_router(registry_api.router)
    app.include_router(monitoring_api.router)
    app.include_router(changes_api.router)
    app.include_router(credit_report_api.router)
    app.include_router(el_api.router)
    app.include_router(fairlending_api.router)
    app.include_router(segmented_api.router)
    app.include_router(survival_api.router)
    app.include_router(multilabel_api.router)
    app.include_router(sql_api.router)
    app.include_router(reject_api.router)
    app.include_router(gym_api.router)
    app.include_router(vela_api.router)
    app.include_router(autopilot_api.router)
    app.include_router(llm_api.router)
    app.include_router(agent_api.router)
    app.include_router(data_doctor_api.router)  # D1
    app.include_router(featurelab_api.router)  # D2
    authz_api.install(app)  # P19: /authz, team audit + verify, the middleware (outermost), the coverage gate
    return app


app = create_app()
