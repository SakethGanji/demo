"""Reset the workbench: delete every run, registered model, batch, autopilot, MLflow record and uploaded dataset, then
regenerate the sample datasets exactly (they are seeded). `make reset` calls POST /api/admin/reset when the API is
running (so its in-memory state is cleared too), else `python -m app.reset` offline."""

from __future__ import annotations

import shutil
from typing import Any

from . import config
from .db import Database

TABLES = ("run_events", "runs", "batch_jobs", "audit_log", "model_versions", "registered_models", "datasets")
FOLDERS = ("runs", "registry", "mlflow", "autopilot", "datasets")


def reset(settings: config.Settings) -> dict[str, Any]:
    from .datasets import DatasetStore

    db = Database(settings.db_path)
    counts = {}
    with db.connect() as con:
        for t in TABLES:
            counts[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            con.execute(f"DELETE FROM {t}")
    for f in FOLDERS:
        shutil.rmtree(settings.data_dir / f, ignore_errors=True)
    store = DatasetStore(settings, db)
    made = store.ensure_samples()
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return {"deleted": counts, "samples": made, "data_dir": str(settings.data_dir)}


if __name__ == "__main__":
    import json

    out = reset(config.load_settings())
    print(json.dumps(out, indent=1))
