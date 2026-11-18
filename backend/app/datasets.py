"""Dataset storage: files under `data/datasets/<id>/`, records in SQLite.

    data/datasets/<id>/data.csv        the rows, exactly as profiled (row positions are the IDs used by splits)
    data/datasets/<id>/splits.json     fixed holdout and folds (see splits.py)
    data/datasets/<id>/<sidecar>.json  samples only: ground truth (label_noise.json, signal.json)
"""

from __future__ import annotations

import io
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import config
from .db import Database
from .profiling import profile_frame
from .samples import SAMPLES
from .splits import make_splits, save_splits, summarize

log = logging.getLogger("ensemble.datasets")


def _generator_fingerprint(fn) -> str:
    """sha256 of the source file that defines a sample generator; changes when the generator code changes."""
    import hashlib
    import inspect
    try:
        src = inspect.getsource(inspect.getmodule(fn))
    except (OSError, TypeError):
        src = getattr(fn, "__qualname__", repr(fn))
    return hashlib.sha256(src.encode()).hexdigest()[:16]


class DatasetError(ValueError):
    """The file can't be used as a dataset; the message is shown to the user."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatasetStore:
    def __init__(self, settings: config.Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        settings.datasets_dir.mkdir(parents=True, exist_ok=True)

    def dir(self, dataset_id: str) -> Path:
        return self.settings.datasets_dir / dataset_id

    def csv_path(self, dataset_id: str) -> Path:
        return self.dir(dataset_id) / "data.csv"

    def splits_path(self, dataset_id: str) -> Path:
        return self.dir(dataset_id) / "splits.json"

    def load_frame(self, dataset_id: str) -> pd.DataFrame:
        return pd.read_csv(self.csv_path(dataset_id))

    # -- create -----------------------------------------------------------

    def _register(self, dataset_id: str, df: pd.DataFrame, *, name: str, filename: str, source: str, note: str,
                  size_bytes: int, target: str | None = None, kind: str | None = None) -> dict[str, Any]:
        try:
            profile = profile_frame(df, target)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        tgt = profile["target"]
        y = df[tgt] if tgt else None
        splits = make_splits(y, len(df), config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS)
        save_splits(self.splits_path(dataset_id), splits)
        profile["splits"] = summarize(splits, y)
        rec = {
            "id": dataset_id, "name": name, "filename": filename, "source": source, "note": note,
            "created_at": now(), "size_bytes": size_bytes, "rows": len(df), "target": tgt,
            "kind": kind or profile["kind"], "profile": profile,
        }
        self.db.insert_dataset(rec)
        return rec

    def sidecar(self, dataset_id: str, name: str) -> dict[str, Any] | None:
        """A sample's ground-truth / spec file (forecast_spec.json, anomaly_truth.json, …), or None."""
        p = self.dir(dataset_id) / name
        return json.loads(p.read_text()) if p.exists() else None

    def ensure_samples(self) -> list[str]:
        """Generate the sample datasets if they are missing. Returns the IDs generated."""
        made = []
        for dataset_id, spec in SAMPLES.items():
            # A sample made by an older generator is stale (new columns, new planted truth): regenerate it.
            # The fingerprint is the source of the module that defines the generator.
            fp = _generator_fingerprint(spec["generate"])
            stamp = self.dir(dataset_id) / ".generator"
            current = stamp.exists() and stamp.read_text().strip() == fp
            if self.db.get_dataset(dataset_id) and self.csv_path(dataset_id).exists():
                if current:
                    continue
                # Stale, or made before stamps existed: regenerate (seeded, so an unchanged generator gives the same rows).
                log.info("sample %s was made by an older generator: regenerating", dataset_id)
            log.info("generating sample dataset %s", dataset_id)
            df, sidecar = spec["generate"](seed=config.SEED)
            d = self.dir(dataset_id)
            d.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.csv_path(dataset_id), index=False)
            (d / spec["sidecar"]).write_text(json.dumps(sidecar, indent=1))
            # Profile what was written, so row positions and dtypes match what the engine will read.
            written = pd.read_csv(self.csv_path(dataset_id))
            self._register(dataset_id, written, name=spec["name"], filename=spec["filename"], source="sample",
                           note=spec["note"], size_bytes=self.csv_path(dataset_id).stat().st_size,
                           target=sidecar["label_column"], kind=spec.get("kind"))
            stamp.write_text(fp)
            made.append(dataset_id)
        return made

    def create_version(self, parent_id: str, df: pd.DataFrame, splits: dict[str, Any], *, name: str, note: str,
                       lineage: dict[str, Any]) -> dict[str, Any]:
        """A new dataset derived from `parent_id` (Autopilot: relabelled / dropped / synthetic rows). The splits are
        given, not drawn: the holdout is the parent's holdout rows that were kept, so scores stay comparable.
        `lineage` (parent, root, root row of every row, the changes) goes to lineage.json and a summary to the profile."""
        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        dataset_id = "ds_" + secrets.token_hex(4)
        d = self.dir(dataset_id)
        d.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.csv_path(dataset_id), index=False)
        df = pd.read_csv(self.csv_path(dataset_id))
        profile = profile_frame(df, parent["target"])
        save_splits(self.splits_path(dataset_id), splits)
        tgt = profile["target"]
        profile["splits"] = summarize(splits, df[tgt] if tgt else None)
        (d / "lineage.json").write_text(json.dumps(lineage))
        profile["lineage"] = {k: v for k, v in lineage.items() if k not in ("root_rows", "changes")}
        rec = {
            "id": dataset_id, "name": name, "filename": f"{dataset_id}.csv", "source": "upload", "note": note,
            "created_at": now(), "size_bytes": self.csv_path(dataset_id).stat().st_size, "rows": len(df), "target": tgt,
            "kind": profile["kind"], "profile": profile,
        }
        self.db.insert_dataset(rec)
        return rec

    def lineage(self, dataset_id: str) -> dict[str, Any] | None:
        p = self.dir(dataset_id) / "lineage.json"
        return json.loads(p.read_text()) if p.exists() else None

    def create_from_upload(self, filename: str, content: bytes, target: str | None = None) -> dict[str, Any]:
        if not content.strip():
            raise DatasetError("The file is empty.")
        if not re.search(r"\.(csv|tsv|txt)$", filename or "", re.I):
            raise DatasetError(f"{filename or 'This file'} is not a CSV. Upload a .csv or .tsv file with a header row.")
        sep = "\t" if filename.lower().endswith(".tsv") else ","
        try:
            df = pd.read_csv(io.BytesIO(content), sep=sep)
        except (pd.errors.ParserError, UnicodeDecodeError, pd.errors.EmptyDataError) as e:
            raise DatasetError(f"Could not read {filename} as CSV: {str(e).splitlines()[0]}") from e
        if df.shape[1] < 2:
            raise DatasetError("No comma-separated columns found. Give a CSV with a header row and at least two columns.")
        if len(df) < 2:
            raise DatasetError("This file has fewer than two data rows.")
        df.columns = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(df.columns)]
        if df.columns.duplicated().any():
            raise DatasetError("Column names must be unique.")

        dataset_id = "ds_" + secrets.token_hex(4)
        d = self.dir(dataset_id)
        d.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.csv_path(dataset_id), index=False)
        df = pd.read_csv(self.csv_path(dataset_id))  # profile exactly what the engine will read back
        stem = re.sub(r"\.(csv|tsv|txt)$", "", Path(filename).name, flags=re.I)
        try:
            return self._register(dataset_id, df, name=stem, filename=Path(filename).name, source="upload",
                                  note="Uploaded file", size_bytes=len(content), target=target)
        except DatasetError:
            for p in d.iterdir():
                p.unlink()
            d.rmdir()
            raise
