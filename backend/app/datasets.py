"""Dataset storage: files under `data/datasets/<id>/`, records in SQLite.

    data/datasets/<id>/data.csv        the rows, exactly as profiled (row positions are the IDs used by splits)
    data/datasets/<id>/splits/<key>.json  holdout and folds per (target, strategy), drawn on first use (see splits.py)
    data/datasets/<id>/splits.json     legacy: the split drawn on upload for the profiler's target (pre-F4 runs use it)
    data/datasets/<id>/<sidecar>.json  samples only: ground truth (label_noise.json, signal.json)
"""

from __future__ import annotations

import io
import json
import logging
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import config
from .db import Database
from .profiling import profile_frame
from .samples import SAMPLES
from .samples_g3 import SAMPLES_G3  # G3 adds its sample here: samples.py stays unchanged (its fingerprint)
from .samples_g4 import SAMPLES_G4  # G4: loan applications with surname and zip (BISG)
from . import splits as splits_mod
from .splits import RANDOM_STRATIFIED, load_splits, make_splits, save_splits, split_key, summarize

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
        self._split_lock = threading.Lock()
        settings.datasets_dir.mkdir(parents=True, exist_ok=True)

    def dir(self, dataset_id: str) -> Path:
        return self.settings.datasets_dir / dataset_id

    def csv_path(self, dataset_id: str) -> Path:
        return self.dir(dataset_id) / "data.csv"

    def legacy_splits_path(self, dataset_id: str) -> Path:
        return self.dir(dataset_id) / "splits.json"

    def split_path(self, dataset_id: str, target: str | None, strategy: str = RANDOM_STRATIFIED,
                   params: dict[str, Any] | None = None) -> Path:
        return self.dir(dataset_id) / "splits" / f"{split_key(target, strategy, params)}.json"

    def splits_path(self, dataset_id: str, target: str | None = None) -> Path:
        """The split file for `target` (default: the dataset's current target), drawn if it doesn't exist yet."""
        if target is None:
            rec = self.db.get_dataset(dataset_id)
            target = rec["target"] if rec else None
        return self.ensure_splits(dataset_id, target)[0]

    def ensure_splits(self, dataset_id: str, target: str | None, strategy: str = RANDOM_STRATIFIED,
                      params: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any], bool]:
        """(path, splits, created) of the (target, strategy, parameters) split. An existing split is never redrawn. A
        new one is drawn from data.csv with the fixed seed, so the same file and target always give the same holdout,
        whichever target the profiler guessed. The legacy splits.json is adopted when it was drawn for this target (so
        the samples, Autopilot's derived datasets and pre-F4 runs keep their exact holdout). F6: group / time /
        time-group splits take `params` (group_column, time_column, cutoff, tuning_share); bad ones raise
        DatasetError."""
        try:
            params = splits_mod.normalize_params(strategy, params)
            path = self.split_path(dataset_id, target, strategy, params)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        with self._split_lock:
            if path.exists():
                return path, load_splits(path), False
            legacy = self.legacy_splits_path(dataset_id)
            sp = load_splits(legacy) if legacy.exists() and strategy == RANDOM_STRATIFIED else None
            if sp is not None and sp.get("target") == target and sp.get("strategy", RANDOM_STRATIFIED) == strategy:
                sp = {**sp, "strategy": strategy, "key": split_key(target, strategy), "adopted_from": "splits.json"}
            else:
                df = self.load_frame(dataset_id)
                if target is not None and target not in df.columns:
                    raise DatasetError(f"Column '{target}' is not in the dataset.")
                y = df[target] if target is not None else None
                try:
                    sp = make_splits(y, len(df), config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS, strategy,
                                     frame=df if strategy != RANDOM_STRATIFIED else None, params=params)
                except ValueError as e:
                    raise DatasetError(str(e)) from e
            sp["created_at"] = sp.get("created_at") or now()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            save_splits(tmp, sp)
            tmp.replace(path)
            return path, sp, True

    def list_splits(self, dataset_id: str) -> list[dict[str, Any]]:
        """Summaries of every split stored for the dataset (one per target and strategy used so far)."""
        d = self.dir(dataset_id) / "splits"
        out = []
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            sp = load_splits(p)
            out.append({**{k: v for k, v in summarize(sp, None).items() if k != "diagram"},
                        "created_at": sp.get("created_at"), "adopted_from": sp.get("adopted_from")})
        return out

    def split_options(self, dataset_id: str, target: str | None) -> dict[str, Any]:
        """F6: the strategies this dataset supports and why. Date columns (≥ 95 % of values read as dates, at least
        10 distinct) and entity columns (ID-like name or a high-cardinality category whose values repeat: several
        rows per customer) are offered for time and group splits; a random split on data with either gets a
        warning, because it lets the same customer, or the future, into training."""
        dates, groups = splits_mod.date_and_group_columns(self.load_frame(dataset_id), target)
        warnings = []
        if dates:
            warnings.append(f"'{dates[0]['column']}' is a date: a random split trains on rows from after the holdout's "
                            "dates, so its score does not show how the model does on the future. Use an out-of-time split.")
        if groups:
            g = groups[0]
            warnings.append(f"'{g['column']}' has {g['groups']:,} values with {g['rows_per_group']:.2f} rows each on average: "
                            "a random split puts rows of the same entity in training and holdout. Use a group split.")
        suggested = RANDOM_STRATIFIED
        if dates and groups:
            suggested = splits_mod.TIME_GROUP
        elif dates:
            suggested = splits_mod.TIME
        elif groups:
            suggested = splits_mod.GROUP
        return {"strategies": list(splits_mod.STRATEGIES), "date_columns": dates, "group_columns": groups,
                "warnings": warnings, "suggested": suggested, "holdout_share": config.HOLDOUT_SHARE,
                "tuning_share": splits_mod.DEFAULT_TUNING_SHARE,
                "suggested_params": {"group_column": groups[0]["column"] if groups else None,
                                     "time_column": dates[0]["column"] if dates else None, "cutoff": None,
                                     "tuning_share": splits_mod.DEFAULT_TUNING_SHARE}}

    def set_target(self, dataset_id: str, target: str) -> dict[str, Any]:
        """Confirm the column to predict: re-profile for it (classes, task, conflicts), draw or reuse its split, and
        record that the user confirmed it. Other targets' splits are untouched."""
        rec = self.db.get_dataset(dataset_id)
        if not rec:
            raise DatasetError(f"No dataset with id '{dataset_id}'.")
        df = self.load_frame(dataset_id)
        if target not in df.columns:
            raise DatasetError(f"Column '{target}' is not in the dataset.")
        if df[target].nunique(dropna=True) < 2:
            raise DatasetError(f"Column '{target}' has fewer than two distinct values; there is nothing to learn.")
        old = rec["profile"]
        try:
            profile = profile_frame(df, target)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        for k, v in old.items():  # keep what was added after profiling (lineage) and the sample's kind
            if k not in profile:
                profile[k] = v
        profile["kind"] = old.get("kind", profile["kind"])
        profile["suggested_target"] = old.get("suggested_target", profile["suggested_target"])
        _, sp, _ = self.ensure_splits(dataset_id, target)
        profile["splits"] = summarize(sp, df[target])
        profile["target_source"] = "confirmed"
        profile["target_history"] = [*old.get("target_history", []), {"target": target, "at": now()}]
        rec = {**rec, "target": target, "profile": profile}
        self.db.insert_dataset(rec)
        return rec

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
        save_splits(self.legacy_splits_path(dataset_id), splits)
        for p in (self.dir(dataset_id) / "splits").glob("*.json"):  # a regenerated sample: its old splits are stale
            p.unlink()
        _, splits, _ = self.ensure_splits(dataset_id, tgt)
        profile["splits"] = summarize(splits, y)
        # "given": named on upload (or a sample's label); "guessed": the profiler's suggestion, to be confirmed.
        profile["target_source"] = "given" if target else "guessed"
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
        for dataset_id, spec in {**SAMPLES, **SAMPLES_G3, **SAMPLES_G4}.items():
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
        save_splits(self.legacy_splits_path(dataset_id), splits)
        tgt = profile["target"]
        # The given split is stored under its own target (adopted from splits.json, rows unchanged).
        st = splits.get("target") if splits.get("target") in df.columns else tgt
        _, splits, _ = self.ensure_splits(dataset_id, st)
        profile["target_source"] = parent["profile"].get("target_source", "given")
        profile["splits"] = summarize(splits, df[st] if st else None)
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
