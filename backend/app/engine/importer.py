"""Import an existing AutoGluon TabularPredictor as a run: a zipped predictor folder (upload) or a folder on the
server. It is checked in a worker (it loads, its label and features are in the chosen dataset), copied into a new run
folder, then scored on the dataset's holdout by the same worker code as a trained run (leaderboard, holdout metrics,
threshold record, importance, MLflow). Explain, the registry, the model card, Try It and batch scoring then work on it.
"""

from __future__ import annotations

import json
import secrets
import shutil
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .. import config
from .store import now
from ..http import dataset_or_404

router = APIRouter(prefix="/api")

MAX_UNZIPPED = 4 * 1024 ** 3


def find_root(d: Path) -> Path | None:
    """The folder holding predictor.pkl: d itself or the shallowest folder below it."""
    hits = sorted(d.rglob("predictor.pkl"), key=lambda p: len(p.parts))
    return hits[0].parent if hits else None


def safe_unzip(zpath: Path, dest: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        total = 0
        root = dest.resolve()
        for info in z.infolist():
            total += info.file_size
            if total > MAX_UNZIPPED:
                raise HTTPException(413, "The archive unpacks to more than 4 GB.")
            target = (dest / info.filename).resolve()
            if root not in target.parents and target != root:
                raise HTTPException(400, f"Unsafe path in the archive: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def inspect_predictor(path: str) -> dict[str, Any]:
    """(In a worker.) Load the predictor and read what the run needs."""
    from autogluon.tabular import TabularPredictor

    import ensemble_models  # noqa: F401 — imported predictors may use the TF-IDF models

    p = TabularPredictor.load(path, require_version_match=False, require_py_version_match=False, verbosity=0)
    lb = p.leaderboard(silent=True)
    info = p.info()
    return {"label": p.label, "problem_type": p.problem_type, "eval_metric": p.eval_metric.name,
            "features": list(p.original_features), "model_best": p.model_best, "n_models": len(p.model_names()),
            "autogluon_version": info.get("version"), "classes": [str(c) for c in (p.class_labels or [])] or None,
            "models": lb[["model", "score_val", "stack_level"]].astype({"score_val": float}).to_dict(orient="records")}


@router.post("/runs/import", status_code=201)
async def import_run(request: Request, dataset_id: str = Form(...), file: UploadFile | None = File(None),
                     path: str | None = Form(None), name: str | None = Form(None),
                     created_by: str = Form("")) -> dict[str, Any]:
    from .api import run_out

    app = request.app
    ds = dataset_or_404(request, dataset_id)
    if (file is None) == (not path):
        raise HTTPException(400, "Give either a zipped predictor folder (file) or a server path (path), not both.")
    run_id = "run_" + secrets.token_hex(4)
    settings = app.state.settings
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True)
    dest = run_dir / "predictor"
    try:
        if file is not None:
            content = await file.read(config.MAX_UPLOAD_BYTES + 1)
            if len(content) > config.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"The archive is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
            zp = run_dir / "upload.zip"
            zp.write_bytes(content)
            if not zipfile.is_zipfile(zp):
                raise HTTPException(400, "The file is not a .zip archive of a predictor folder.")
            tmp = run_dir / "unzipped"
            safe_unzip(zp, tmp)
            zp.unlink()
            root = find_root(tmp)
            if root is None:
                raise HTTPException(400, "No predictor.pkl in the archive: zip the folder TabularPredictor saved to.")
            shutil.move(str(root), str(dest))
            shutil.rmtree(tmp, ignore_errors=True)
            source_kind, source = "upload", file.filename or "predictor.zip"
        else:
            src = Path(path).expanduser()
            if not src.is_dir():
                raise HTTPException(400, f"{path} is not a folder on the server.")
            root = find_root(src)
            if root is None:
                raise HTTPException(400, f"No predictor.pkl under {path}.")
            shutil.copytree(root, dest)
            source_kind, source = "server path", str(root)
        if (dest / "metadata.json").exists():
            meta = json.loads((dest / "metadata.json").read_text())
            if meta.get("is_timeseries") or "timeseries" in str(meta.get("version", "")):
                raise HTTPException(400, "This is a TimeSeriesPredictor; import supports TabularPredictor.")
        try:
            info = await app.state.heavy.run(inspect_predictor, str(dest))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Could not load the predictor: {type(e).__name__}: {e}") from e
        cols = {c["name"] for c in ds["profile"]["columns"]}
        if info["label"] not in cols:
            raise HTTPException(400, f"The predictor predicts '{info['label']}', which is not a column of {ds['name']}.")
        missing = [f for f in info["features"] if f not in cols]
        if missing:
            raise HTTPException(400, f"{ds['name']} lacks the predictor's feature(s): {', '.join(missing[:10])}.")
    except HTTPException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    label = info["label"]
    from .api import split_record
    split = split_record(app.state.datasets, dataset_id, label)
    drop = [c for c in cols if c not in info["features"] and c != label]
    imp = {"source_kind": source_kind, "source": source, "created_by": created_by, **{k: info[k] for k in
           ("autogluon_version", "n_models", "model_best", "problem_type", "eval_metric")}}
    cfg = {"dataset_id": dataset_id, "label": label, "problem_type": info["problem_type"], "eval_metric": info["eval_metric"],
           "presets": None, "time_limit": None, "fit_kwargs": {}, "predictor_kwargs": {}, "custom_text_models": False,
           "feature_importance": True, "column_types": {}, "calibrate_threshold": None, "drop_columns": drop,
           "text_columns": [], "task": info["problem_type"], "import": imp, "source": "import",
           "run_id": run_id, "db_path": str(settings.db_path), "csv_path": str(app.state.datasets.csv_path(dataset_id)),
           "splits_path": split["path"], "split": split, "run_dir": str(run_dir),
           "predictor_path": str(dest)}
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
    store = app.state.runs
    store.create({"id": run_id, "name": name or f"Imported · {source.split('/')[-1]} · {label}", "dataset_id": dataset_id,
                  "label": label, "created_at": now(), "config": cfg})
    store.emit(run_id, "run_queued", kind="import", dataset_id=dataset_id, label=label, **imp)
    app.state.runner.submit(run_id)
    return {**run_out(store.get(run_id), store), "inspected": info}
