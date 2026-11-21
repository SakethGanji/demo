"""G2 · Credit-risk model report for a registered binary version (the numbers are in credit_report.py).

GET  /registry/versions/{vid}/credit-report                  the report (JSON); ?format=md|html&download=true exports it
POST /registry/versions/{vid}/credit-report/tape             upload a performance tape for the vintage curves (audited)

Zero-config: the date column, the id column and the tape's columns come from G0's column roles (`intake.detect`), each
shown with its evidence and overridable (`?date_column=`). Inputs are the version's holdout predictions and its scored
batches (F18), whose labels give the per-month rank ordering and whose stored per-feature PSI is the CSI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import config, frames
from ..authz import path, require  # P19
from ..http import version_or_404
from . import credit_report as C
from .scoring import read_batch

router = APIRouter(prefix="/api", tags=["credit-report"])
SAMPLE_TAPES = {"loan-stream": "samples_g2"}
OPEN = re.compile(r"(orig|open|book|appl|start|disburs|fund|issue)", re.I)
DEFAULT = re.compile(r"(default|bad|charge|write|dpd|delinq)", re.I)
UNTIL = re.compile(r"(observ|as_of|asof|until|snapshot|report)", re.I)


def _dir(request: Request) -> Path:
    return request.app.state.settings.data_dir / "registry" / "credit_report"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def _ctx(request: Request, vid: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    v = version_or_404(request, vid, "model version")
    if v["status"] != "ready":
        raise HTTPException(409, f"Version {v['model_name']} v{v['version']} is {v['status']}.")
    run = request.app.state.runs.get(v["run_id"]) or {}
    ver = (v.get("result") or {}).get("version") or {}
    if (run.get("config") or {}).get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or ver.get("problem_type") != "binary":
        raise HTTPException(400, "The credit report is for binary scoring versions (p̂ = P(bad | x)).")
    return v, run, ver


# --------------------------------------------------------------------------- detected inputs (G0 roles)


def _roles(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    from ..intake import detect

    by: dict[str, list[dict[str, Any]]] = {}
    for c in detect(df)["columns"]:
        for r in [c, *c.get("also", [])]:
            by.setdefault(r["role"], []).append({"column": c["name"], "confidence": r["confidence"], "evidence": r["evidence"]})
    return by


def _detected(request: Request, v: dict[str, Any], run: dict[str, Any], train: pd.DataFrame, date_column: str | None) -> dict[str, Any]:
    from .monitoring_api import get_config

    roles = _roles(train.drop(columns=[run["label"]], errors="ignore").head(5000))
    dates = roles.get("date") or []
    split_t = ((run["config"].get("split") or {}).get("params") or {}).get("time_column") or (run["config"].get("split") or {}).get("time_column")
    if date_column:
        if date_column not in train.columns:
            raise HTTPException(400, f"No column '{date_column}' in the training data.")
        d = {"column": date_column, "confidence": 1.0, "evidence": ["set by you"]}
    else:
        d = next((x for x in dates if x["column"] == split_t), None) or (dates[0] if dates else None)
    idc = get_config(request, v["model_name"]).get("id_column")
    ids = roles.get("id") or []
    i = next((x for x in ids if x["column"] == idc), None) or (ids[0] if ids else None)
    return {"date": d, "id": i, "date_candidates": [x["column"] for x in dates], "label": run["label"]}


# --------------------------------------------------------------------------- the populations


def _holdout(v: dict[str, Any], run: dict[str, Any], pos: str) -> pd.DataFrame:
    hp_path = Path(v["path"]).parent / "holdout_predictions.csv"
    if not hp_path.exists():
        hp_path = Path(run["config"]["run_dir"]) / "holdout_predictions.csv"
    if not hp_path.exists():
        raise HTTPException(404, "This version has no saved holdout predictions.")
    hp = frames.read_artifact(hp_path)
    return pd.DataFrame({"row": hp["row"].astype(int), "y": (hp["y_true"].astype(str) == pos).astype(int), "p": hp[f"p_{pos}"].astype(float)})


def _batches(request: Request, vid: str, pos: str) -> tuple[list[dict[str, Any]], pd.DataFrame, list[dict[str, Any]]]:
    """(monitoring points, labelled rows of every batch with their period, unlabelled batches)."""
    from .monitoring_api import monitoring_state

    st = monitoring_state(request, vid)
    rows, pending = [], []
    bdir = request.app.state.settings.data_dir / "registry" / "batches"
    for p in st["points"]:
        d = bdir / p["batch_id"]
        if not p.get("performance") or not (d / "labels.csv").exists():
            pending.append({"period": p["period"], "batch_id": p["batch_id"], "n": p["n_rows"], "filename": p["filename"]})
            continue
        lab = frames.read_artifact(d / "labels.csv")
        sc = frames.read_artifact(d / "scored.csv", usecols=[f"p_{pos}"])
        rows.append(pd.DataFrame({"period": p["period"] or p["batch_id"], "batch_id": p["batch_id"],
                                  "y": (lab["y"].astype(str) == pos).astype(int).to_numpy(),
                                  "p": sc[f"p_{pos}"].to_numpy()[lab["row"].to_numpy()]}))
    return st["points"], (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["period", "batch_id", "y", "p"])), pending


# --------------------------------------------------------------------------- the vintage tape


def _tape_path(request: Request, model: str) -> Path:
    return _dir(request) / _safe(model) / "tape.csv"


def _sample_tape(request: Request, dataset_id: str) -> pd.DataFrame:
    p = _dir(request) / "_samples" / f"{dataset_id}.csv"
    if not p.exists():
        from ..samples_g2 import performance_tape

        p.parent.mkdir(parents=True, exist_ok=True)
        performance_tape(config.SEED).to_csv(p, index=False)
    return frames.read_artifact(p, dtype=str, keep_default_na=False)


def _read_tape(content: bytes) -> pd.DataFrame:
    """§6.5 R-F: a user's performance tape (stored as uploaded), every cell as its text ('' for blank): the encoding and
    the separator detected as for any upload. Errors are a plain message, never the parser's text."""
    import io

    from ..datasets import DatasetError, decode_upload, sniff_sep

    if not content.strip():
        raise DatasetError("The file is empty.")
    text, _enc = decode_upload(content)
    try:
        return frames.read_artifact(io.StringIO(text), sep=sniff_sep(text), dtype=str, keep_default_na=False)
    except Exception as e:  # noqa: BLE001
        raise DatasetError("Not a readable CSV: give a performance tape with a header row.") from e


def map_tape(df: pd.DataFrame) -> dict[str, Any]:
    """Which columns of a tape are the id, the origination date, the default date and the as-of date, from G0's roles
    (date / id) and the column names; with the evidence."""
    roles = _roles(df)
    dates = [x["column"] for x in roles.get("date") or []]
    # The date role needs many distinct days, and a default-date column is mostly blank: also take any column whose
    # filled-in values parse as dates.
    for c in df.columns:
        v = df[c].astype(str).replace("", None).dropna().head(2000)
        if c not in dates and len(v) and pd.to_datetime(v, errors="coerce", format="mixed").notna().mean() >= 0.95 \
                and not pd.to_numeric(v, errors="coerce").notna().all():
            dates.append(c)
    dflt = next((c for c in dates if DEFAULT.search(c)), None)
    until = next((c for c in dates if UNTIL.search(c) and c != dflt), None)
    opened = next((c for c in dates if OPEN.search(c) and c not in (dflt, until)), None) or \
        next((c for c in dates if c not in (dflt, until)), None)
    ids = [x["column"] for x in (roles.get("id") or []) + (roles.get("entity") or [])]
    ev = {"open": f"dates{', name reads like origination' if opened and OPEN.search(opened) else ''}",
          "default": "name reads like a default date (blank = no default seen)", "until": "name reads like an as-of date",
          "id": "row ID role"}
    return {"open": opened, "default": dflt, "until": until, "id": ids[0] if ids else None,
            "evidence": {k: ev[k] for k, c in (("open", opened), ("default", dflt), ("until", until), ("id", ids[0] if ids else None)) if c}}


def _normalise(df: pd.DataFrame, m: dict[str, Any]) -> pd.DataFrame:
    def dt(c: str | None) -> pd.Series:
        return pd.to_datetime(df[c].replace("", None), errors="coerce") if c else pd.Series(pd.NaT, index=df.index)

    until = dt(m.get("until"))
    if until.isna().all():
        until = pd.Series(max(dt(m["open"]).max(), dt(m["default"]).max()), index=df.index)
    return pd.DataFrame({"id": df[m["id"]].astype(str) if m.get("id") else pd.Series(range(len(df))).astype(str),
                         "open": dt(m["open"]), "default": dt(m["default"]), "until": until})


def _vintage(request: Request, v: dict[str, Any], run: dict[str, Any], train: pd.DataFrame, det: dict[str, Any],
             batch_ids: list[str], cohort: str) -> dict[str, Any] | None:
    up = _tape_path(request, v["model_name"])
    if up.exists():
        meta = json.loads((up.parent / "tape.json").read_text())
        tape = _normalise(_read_tape(up.read_bytes()), meta["mapping"])
        src = {"kind": "uploaded", "file": meta.get("filename"), "by": meta.get("uploaded_by"), "at": meta.get("uploaded_at"),
               "mapping": meta["mapping"]}
    elif run.get("dataset_id") in SAMPLE_TAPES:
        raw = _sample_tape(request, run["dataset_id"])
        mp = map_tape(raw.head(3000))
        tape = _normalise(raw, mp)
        # only the loans this version has seen: its training data and the batches it scored
        seen: set[str] = set()
        idc = (det.get("id") or {}).get("column")
        if idc and idc in train.columns:
            seen |= set(train[idc].astype(str))
            bdir = request.app.state.settings.data_dir / "registry" / "batches"
            for b in batch_ids:
                try:
                    seen |= set(read_batch(bdir / b / "input.csv", run["config"], columns=[idc])[idc].astype(str))
                except (FileNotFoundError, ValueError, KeyError):
                    pass
            tape = tape[tape["id"].isin(seen)]
        src = {"kind": "sample", "file": f"{SAMPLE_TAPES[run['dataset_id']]}.performance_tape", "mapping": mp,
               "note": "the loan-stream sample's seeded performance tape: the month each defaulter reached 90 DPD, as of 2026-03-31",
               "filtered_to": "the loans in this version's training data and scored batches"}
    else:
        return None
    out = C.vintage(tape, cohort)
    out["source"] = src
    return out


# --------------------------------------------------------------------------- the report


def build(request: Request, vid: str, on: str = "holdout", date_column: str | None = None, cohort: str = "auto") -> dict[str, Any]:
    v, run, ver = _ctx(request, vid)
    pos = str(ver.get("positive_class"))
    t = ver.get("decision_threshold")
    t = float(t) if t is not None else None
    train = frames.read(frames.resolve(run["config"]))
    det = _detected(request, v, run, train, date_column)
    hold = _holdout(v, run, pos)
    points, lab, pending = _batches(request, vid, pos)

    periods: list[dict[str, Any]] = []
    dcol = (det.get("date") or {}).get("column")
    if dcol:
        when = pd.to_datetime(train[dcol], errors="coerce").loc[hold["row"].to_numpy()].dt.strftime("%Y-%m").to_numpy()  # P11: by __row_id
        for per in sorted(set(x for x in when if isinstance(x, str))):
            m = when == per
            periods.append({"period": per, "source": "holdout", **C.rank_stats(hold["y"].to_numpy()[m], hold["p"].to_numpy()[m])})
    for per, g in lab.groupby("period", sort=True):
        periods.append({"period": str(per), "source": "batch", "batches": sorted(g["batch_id"].unique().tolist()),
                        **C.rank_stats(g["y"].to_numpy(), g["p"].to_numpy())})
    overall = {"holdout": C.rank_stats(hold["y"].to_numpy(), hold["p"].to_numpy())}
    if len(lab):
        overall["batches"] = C.rank_stats(lab["y"].to_numpy(), lab["p"].to_numpy())

    pop = lab if on == "batches" and len(lab) else hold
    on_used = "batches" if pop is lab else "holdout"
    edges = C.band_edges(hold["p"].to_numpy())
    bands = C.band_table(pop["y"].to_numpy(), pop["p"].to_numpy(), edges, t)
    dec = C.deciles(pop["y"].to_numpy(), pop["p"].to_numpy())

    csi = C.csi_matrix(points)
    score_psi = [{"period": p["period"], "batch_id": p["batch_id"], "psi": p.get("prediction_psi"), "n": p.get("n_rows"),
                  "mean_p": p.get("mean_p")} for p in points]
    flag = next((f for f in csi["features"] if f["first_shift"]), None)
    bins = None
    if flag:
        b = next(p for p in points if p["period"] == flag["first_shift"])
        dp = request.app.state.settings.data_dir / "registry" / "batches" / b["batch_id"] / "drift.json"
        if dp.exists():
            fe = next((f for f in json.loads(dp.read_text()).get("features") or [] if f["feature"] == flag["feature"]), None)
            if fe and fe.get("bins"):
                bins = {"feature": flag["feature"], "period": flag["first_shift"], "csi": fe["psi"], "bins": fe["bins"]}

    vin = _vintage(request, v, run, train, det, [p["batch_id"] for p in points], cohort)
    gh = overall["holdout"]
    out = {"schema": "credit-report/1", "version_id": vid, "label": f"{v['model_name']} v{v['version']}", "model": v["model_name"],
           "stage": v["stage"], "run_id": v["run_id"], "positive_class": pos, "threshold": t, "served_model": ver.get("model"),
           "title": f"Model Performance Report · {v['model_name']} v{v['version']}", "detected": det, "on": on_used,
           "overall": overall, "periods": periods, "pending": pending, "bands": bands, "deciles": dec,
           "stability": {"score_psi": score_psi, "csi": csi, "first_flag": ({"feature": flag["feature"], "period": flag["first_shift"],
                                                                             "stays_flagged": flag["stays_flagged"]} if flag else None),
                         "flag_bins": bins, "reference": "CSI: the version's training rows; score PSI: its holdout p̂"},
           "vintage": vin, "formulas": C.FORMULAS,
           "headline": {"gini": gh.get("gini"), "gini_ci": gh.get("gini_ci"), "ks": gh.get("ks"), "bad_rate": gh.get("bad_rate"),
                        "months": sum(1 for p in periods if p["source"] == "batch"), "first_csi_flag": flag and f"{flag['feature']} from {flag['first_shift']}",
                        "cohorts": len((vin or {}).get("cohorts") or [])},
           "links": {"html": f"/api/registry/versions/{vid}/credit-report?format=html&download=true",
                     "md": f"/api/registry/versions/{vid}/credit-report?format=md&download=true"}}
    from ..engine.jsonsafe import jsonable

    out = jsonable(out)
    try:  # the model card reads the last headline (no recompute)
        (Path(v["path"]).parent / "credit_report.json").write_text(json.dumps({"headline": out["headline"], "on": on_used,
                                                                              "computed_at": pd.Timestamp.now(tz="UTC").isoformat()[:19]}))
    except OSError:
        pass
    return out


@router.get("/registry/versions/{vid}/credit-report")
async def credit_report(vid: str, request: Request, on: Literal["holdout", "batches"] = "holdout",
                        date_column: str | None = None, cohort: Literal["auto", "month", "quarter"] = "auto",
                        format: Literal["json", "md", "html"] = "json", download: bool = False):
    """G2: rank ordering (Gini with DeLong CI, KS with a bootstrap CI) overall and per month, score bands, deciles, score
    PSI, CSI per feature per month, vintage curves; exportable as Markdown or HTML."""
    rep = await run_in_threadpool(build, request, vid, on, date_column, cohort)
    fname = f"model-performance-report_{_safe(rep['model'])}_v{rep['label'].split(' v')[-1]}"
    if format == "md":
        return PlainTextResponse(C.to_markdown(rep), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}.md"'} if download else None)
    if format == "html":
        from .card import md_html

        return HTMLResponse(md_html(C.to_markdown(rep), rep["title"]),
                            headers={"Content-Disposition": f'attachment; filename="{fname}.html"'} if download else None)
    return rep


@router.post("/registry/versions/{vid}/credit-report/tape", dependencies=[require("model.use", path("vid", "model_version"))])
async def upload_tape(vid: str, request: Request, file: UploadFile = File(...), actor: str = Form("")) -> dict[str, Any]:
    """A performance tape for the vintage curves: one row per loan with an origination date and a default date (blank
    when it has not defaulted), optionally an id and an as-of date. Columns are found from G0's roles and names."""
    v, run, _ = _ctx(request, vid)
    who = (actor or "").strip()
    if not who:
        raise HTTPException(400, "actor is required (the upload is audited).")
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The tape is too large.")
    from ..datasets import DatasetError

    try:
        df = _read_tape(content)
    except DatasetError as e:
        raise HTTPException(400, str(e)) from e
    mp = await run_in_threadpool(map_tape, df)
    if not mp["open"] or not mp["default"]:
        raise HTTPException(400, f"The tape needs an origination date and a default date column (found: dates "
                                 f"{mp['open'] or '—'} / default {mp['default'] or '—'}; columns {', '.join(df.columns)}).")
    p = _tape_path(request, v["model_name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    meta = {"mapping": mp, "filename": file.filename, "uploaded_by": who, "uploaded_at": pd.Timestamp.now(tz="UTC").isoformat()[:19],
            "rows": int(len(df))}
    (p.parent / "tape.json").write_text(json.dumps(meta))
    request.app.state.registry.audit(actor=who, action="credit_tape_uploaded", version=v,
                                     comment=f"{len(df):,} loans from {file.filename}", details={"mapping": mp})
    vin = C.vintage(_normalise(df, mp))
    return {"tape": meta, "cohorts": len(vin.get("cohorts") or []), "loans": vin.get("loans")}


def card_section(v: dict[str, Any]) -> dict[str, Any]:
    """The model card's line: the last computed headline (no recompute) and the export links."""
    p = Path(v["path"]).parent / "credit_report.json"
    last = json.loads(p.read_text()) if p.exists() else None
    return {"last": last, "links": {"html": f"/api/registry/versions/{v['id']}/credit-report?format=html&download=true",
                                    "md": f"/api/registry/versions/{v['id']}/credit-report?format=md&download=true"}}



@router.delete("/registry/versions/{vid}/credit-report/tape", dependencies=[require("model.use", path("vid", "model_version"))])
def delete_tape(vid: str, request: Request, actor: str = "") -> dict[str, Any]:
    """Remove the uploaded tape: the vintage goes back to the detected source (the sample's tape, or none)."""
    v, _, _ = _ctx(request, vid)
    if not actor.strip():
        raise HTTPException(400, "actor is required (the removal is audited).")
    p = _tape_path(request, v["model_name"])
    if not p.exists():
        raise HTTPException(404, "No uploaded tape for this model.")
    p.unlink()
    (p.parent / "tape.json").unlink(missing_ok=True)
    request.app.state.registry.audit(actor=actor.strip(), action="credit_tape_removed", version=v, comment="uploaded tape removed")
    return {"removed": True}
