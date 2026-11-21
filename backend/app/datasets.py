"""Dataset storage: files under `data/datasets/<id>/`, records in SQLite.

    data/datasets/<id>/data.csv        the rows, exactly as profiled (row positions are the IDs used by splits)
    data/datasets/<id>/splits/<key>.json  holdout and folds per (target, strategy), drawn on first use (see splits.py)
    data/datasets/<id>/splits.json     legacy: the split drawn on upload for the profiler's target (pre-F4 runs use it)
    data/datasets/<id>/<sidecar>.json  samples only: ground truth (label_noise.json, signal.json)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import config
from . import frame_io as FIO
from .db import Database
from .profiling import profile_frame
from .samples import SAMPLES
from .samples_g3 import SAMPLES_G3  # G3 adds its sample here: samples.py stays unchanged (its fingerprint)
from .samples_g4 import SAMPLES_G4  # G4: loan applications with surname and zip (BISG)
from .samples_g5 import SAMPLES_G5  # G5: loans from customer types that behave differently
from .samples_g6 import SAMPLES_G6  # G6: accounts followed until they close or the data ends (censoring)
from .samples_g7 import SAMPLES_G7  # G7: applications with declines (outcomes only for the approved)
from .samples_g8 import SAMPLES_G8  # G8: complaints with overlapping topics (multi-label)
from . import splits as splits_mod
from .splits import RANDOM_STRATIFIED, load_splits, make_splits, save_splits, split_key, summarize

log = logging.getLogger("ensemble.datasets")


def decode_upload(content: bytes) -> tuple[str, str]:
    """A1 #15: the uploaded bytes as text and the encoding used. A BOM decides (UTF-8, UTF-16 LE/BE); UTF-16 without
    one is recognised by its NUL bytes; otherwise UTF-8, then cp1252 (Windows/bank exports), then latin-1."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if content.startswith(bom):
            try:
                return content[len(bom):].decode(enc), enc.upper() if enc != "utf-8" else enc
            except UnicodeDecodeError as e:
                raise DatasetError(f"The file starts with a {enc.upper()} marker but is not valid {enc.upper()} text.") from e
    head = content[:4096]
    if head.count(b"\x00") > len(head) // 4:
        enc = "utf-16-le" if head[1::2].count(b"\x00") > head[0::2].count(b"\x00") else "utf-16-be"
        try:
            return content.decode(enc), enc.upper()
        except UnicodeDecodeError as e:
            raise DatasetError("This looks like a binary file, not a CSV.") from e
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    ctrl = sum(1 for ch in text[:20000] if ord(ch) < 32 and ch not in "\t\r\n")
    if ctrl > max(2, len(text[:20000]) // 100):
        raise DatasetError("This looks like a binary file, not a CSV. Upload a .csv or .tsv text file.")
    return text, enc


def check_target(y: pd.Series, name: str) -> None:
    """A1 #15: refuse a target with nothing to learn, saying which case it is."""
    n = int(y.notna().sum())
    if n == 0:
        raise DatasetError(f"Column '{name}' is empty: every row is missing, so there is nothing to learn. "
                           "Pick the column that holds the outcome.")
    vals = y.dropna().unique()
    if len(vals) < 2:
        raise DatasetError(f"Column '{name}' has one value ({str(vals[0])!r}) in all {n:,} labelled rows: a model needs "
                           "at least two outcomes to learn from. Add rows with the other outcome.")


def sniff_sep(text: str) -> str:
    """The separator: the one of , ; tab | that splits the header and the first rows into the same, largest number
    of fields (outside quotes)."""
    lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    best, best_n = ",", 1
    for sep in (",", ";", "\t", "|"):
        try:
            counts = [len(r) for r in csv.reader(lines, delimiter=sep)]
        except csv.Error:
            continue
        if not counts:
            continue
        n = counts[0]
        consistent = sum(c == n for c in counts) >= 0.8 * len(counts)
        if n > best_n and consistent:
            best, best_n = sep, n
    return best


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


def read_upload(filename: str, content: bytes) -> tuple[pd.DataFrame, str, str]:
    """The uploaded bytes as a frame, the note on how it was read, and the file's stem (A1 #15 checks, in order)."""
    if not content.strip():
        raise DatasetError("The file is empty.")
    if not re.search(r"\.(csv|tsv|txt)$", filename or "", re.I):
        raise DatasetError(f"{filename or 'This file'} is not a CSV. Upload a .csv or .tsv file with a header row.")
    text, encoding = decode_upload(content)
    sep = "\t" if filename.lower().endswith(".tsv") else sniff_sep(text)
    header = next(csv.reader(io.StringIO(text), delimiter=sep), [])
    names = [h.strip() for h in header]
    dup = sorted({h for h in names if h and names.count(h) > 1})
    if dup:
        where = "; ".join(f"'{h}' is columns {', '.join(str(i + 1) for i, n in enumerate(names) if n == h)}"
                          for h in dup)
        raise DatasetError(f"The header repeats a column name ({where}). Rename or remove the duplicate and "
                           "upload again, so no column is silently renamed.")
    try:
        # ';'-separated files are the European export: decimal commas.
        df = pd.read_csv(io.StringIO(text), sep=sep, decimal="," if sep == ";" else ".")
    except pd.errors.EmptyDataError as e:
        raise DatasetError("The file has no header row.") from e
    except pd.errors.ParserError as e:
        m = re.search(r"Expected (\d+) fields in line (\d+), saw (\d+)", str(e))
        if m:
            raise DatasetError(f"Line {m[2]} has {m[3]} fields but the header has {m[1]}. Quote values that "
                               f"contain '{sep}' or fix that line, then upload again.") from e
        raise DatasetError(f"Could not read {filename} as CSV: {str(e).splitlines()[0]}") from e
    # DL4 (m-e): codes with leading zeros ('02139', '007') stay text: pandas would read them as numbers and drop the zeros
    try:
        raw = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=True)
        keep = FIO.leading_zero_columns(raw, df)
    except Exception:  # noqa: BLE001 - the typed read above succeeded; this is only a refinement
        keep = []
    for c in keep:
        df[c] = raw[c].where(raw[c].notna(), None).astype(object)
    df.attrs["kept_as_text"] = keep
    if df.shape[1] < 2:
        raise DatasetError("No separated columns found (tried comma, semicolon, tab, pipe). Give a CSV with a "
                           "header row and at least two columns.")
    if len(df) < 2:
        raise DatasetError("This file has fewer than two data rows.")
    stem = re.sub(r"\.(csv|tsv|txt)$", "", Path(filename).name, flags=re.I)
    note = "Uploaded file" + ("" if encoding in ("utf-8", "ascii") else f" (read as {encoding})") + \
        ("" if sep == "," else f" (separator {'tab' if sep == chr(9) else repr(sep)})")
    return df, note, stem


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Column names stripped and unique, numbers coerced (A1 #5), as every new dataset is written."""
    df.columns = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(df.columns)]
    if df.columns.duplicated().any():
        raise DatasetError("Column names must be unique.")
    from .profiling import coerce_numeric

    return coerce_numeric(df)[0]


#: DL3 (m3): background jobs that make versions with no person behind the request; recorded as "system:<op>"
SYSTEM_OPS = ("autopilot", "autopilot_synthetic", "sample_regenerated")
#: DL3 (m3): ops whose identical result returns the existing version instead of a duplicate
DEDUPE_OPS = ("shift_weights", "edit", "anomaly_review", "feature_lab")


class DatasetConflict(ValueError):
    """DL3: the request is valid but the data refuses it (the file on disk no longer matches its version): a 409."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatasetStore:
    def __init__(self, settings: config.Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._split_lock = threading.Lock()
        settings.datasets_dir.mkdir(parents=True, exist_ok=True)
        from .catalog.store import Catalog

        self.catalog = Catalog(db)  # P1: dataset versions and lineage edges

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
                self.record_split_file(dataset_id, path, "first seen (drawn before DL4)", only_new=True)
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
            self.record_split_file(dataset_id, path, "drawn")  # DL4 (M-d)
            return path, sp, True

    # -- DL4 (M-d): split files are hashed when drawn, like data.csv ------------------------------------------------

    def split_rel(self, dataset_id: str, path: Path | str) -> str:
        p = Path(path)
        return f"splits/{p.name}" if p.parent.name == "splits" else p.name

    def record_split_file(self, dataset_id: str, path: Path, basis: str = "drawn", only_new: bool = False) -> None:
        import zlib

        from .catalog.schema import file_sha256

        sha = file_sha256(path)
        if not sha:
            return
        rel = self.split_rel(dataset_id, path)
        with self.db.connect() as con:
            if only_new and con.execute("SELECT 1 FROM split_files WHERE dataset_id = ? AND file = ?", (dataset_id, rel)).fetchone():
                return
            con.execute("INSERT INTO split_files (dataset_id, file, sha256, recorded_at, basis, content) VALUES (?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT (dataset_id, file) DO UPDATE SET sha256 = excluded.sha256, recorded_at = excluded.recorded_at,"
                        " basis = excluded.basis, content = excluded.content",
                        (dataset_id, rel, sha, now(), basis, zlib.compress(path.read_bytes())))
            self._refresh_splits_sha(con, dataset_id)

    @staticmethod
    def _refresh_splits_sha(con: Any, dataset_id: str) -> str | None:
        import hashlib

        rows = con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ? ORDER BY file", (dataset_id,)).fetchall()
        combined = hashlib.sha256("\n".join(f"{r[0]} {r[1]}" for r in rows).encode()).hexdigest() if rows else None
        con.execute("UPDATE datasets SET splits_sha256 = ? WHERE id = ?", (combined, dataset_id))
        return combined

    def split_hashes(self, dataset_id: str) -> dict[str, str]:
        with self.db.connect() as con:
            return {r[0]: r[1] for r in con.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (dataset_id,))}

    def check_splits(self, dataset_id: str) -> list[dict[str, Any]]:
        """Each recorded split file: its stored sha256, the file's now, and ok / MISMATCH / file missing."""
        from .catalog.schema import file_sha256

        out = []
        for rel, stored in sorted(self.split_hashes(dataset_id).items()):
            now_sha = file_sha256(self.dir(dataset_id) / rel)
            out.append({"file": rel, "stored": stored, "now": now_sha,
                        "status": "ok" if now_sha == stored else ("file missing" if not now_sha else "MISMATCH")})
        return out

    def restore_split_files(self, dataset_id: str) -> list[str]:
        """Write back every split file whose bytes differ from the copy recorded when it was drawn (hash-checked)."""
        import hashlib
        import zlib

        restored = []
        with self.db.connect() as con:
            rows = con.execute("SELECT file, sha256, content FROM split_files WHERE dataset_id = ?", (dataset_id,)).fetchall()
        for rel, sha, blob in rows:
            p = self.dir(dataset_id) / rel
            if blob is None:
                continue
            raw = zlib.decompress(blob)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise DatasetError(f"The recorded copy of {rel} does not match its own hash: it can't be restored from it.")
            from .catalog.schema import file_sha256

            if file_sha256(p) != sha:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
                restored.append(rel)
        return restored

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
        check_target(df[target], target)
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
        return FIO.read(self.csv_path(dataset_id))  # DL4 (m-e): with the version's schema (text stays text)

    def schema(self, dataset_id: str, df: pd.DataFrame | None = None) -> dict[str, Any]:
        """DL4: the version's column schema (schema.json), or, for a version made before DL4, the types its file reads as."""
        return FIO.load(self.dir(dataset_id)) or FIO.schema_of(self.load_frame(dataset_id) if df is None else df)

    def _same_bytes(self, sha: str, root: str, schema: dict[str, Any]) -> str | None:
        """DL4 (m-e): an existing live version of this dataset with the same bytes AND the same column schema (a number cast
        to text writes the same bytes, but it is a different version)."""
        with self.db.connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM datasets WHERE sha256 = ? AND (id = ? OR root_id = ?) AND state IS NOT 'deleted'"
                                             " ORDER BY created_at", (sha, root, root))]
        want = FIO.columns(schema)
        for i in ids:
            try:
                if FIO.columns(self.schema(i)) == want:
                    return i
            except Exception:  # noqa: BLE001 - an unreadable file is not a duplicate
                continue
        return None

    # -- create -----------------------------------------------------------

    def _register(self, dataset_id: str, df: pd.DataFrame, *, name: str, filename: str, source: str, note: str,
                  size_bytes: int, target: str | None = None, kind: str | None = None, by: str | None = None) -> dict[str, Any]:
        try:
            profile = profile_frame(df, target)
        except ValueError as e:
            raise DatasetError(str(e)) from e
        tgt = profile["target"]
        y = df[tgt] if tgt else None
        try:
            splits = make_splits(y, len(df), config.SEED, config.HOLDOUT_SHARE, config.N_FOLDS)
        except ValueError as e:  # DL3 (m4): too few rows, in words
            raise DatasetError(str(e)) from e
        save_splits(self.legacy_splits_path(dataset_id), splits)
        for p in (self.dir(dataset_id) / "splits").glob("*.json"):  # a regenerated sample: its old splits are stale
            p.unlink()
        with self.db.connect() as con:
            con.execute("DELETE FROM split_files WHERE dataset_id = ?", (dataset_id,))
        self.record_split_file(dataset_id, self.legacy_splits_path(dataset_id), "drawn with the version")
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
        self.catalog.record_root_dataset(rec, self.csv_path(dataset_id), op=source, actor=by)
        with self.db.connect() as con:
            self._refresh_splits_sha(con, dataset_id)  # DL4 (M-d): stored with the version
        return rec

    def sidecar(self, dataset_id: str, name: str) -> dict[str, Any] | None:
        """A sample's ground-truth / spec file (forecast_spec.json, anomaly_truth.json, …), or None."""
        p = self.dir(dataset_id) / name
        return json.loads(p.read_text()) if p.exists() else None

    def ensure_samples(self) -> list[str]:
        """Generate the sample datasets if they are missing. Returns the IDs generated."""
        made = []
        for dataset_id, spec in {**SAMPLES, **SAMPLES_G3, **SAMPLES_G4, **SAMPLES_G5, **SAMPLES_G6, **SAMPLES_G7, **SAMPLES_G8}.items():
            # A sample made by an older generator is stale (new columns, new planted truth): regenerate it.
            # The fingerprint is the source of the module that defines the generator.
            fp = _generator_fingerprint(spec["generate"])
            stamp = self.dir(dataset_id) / ".generator"
            current = stamp.exists() and stamp.read_text().strip() == fp
            if self.db.get_dataset(dataset_id) and self.csv_path(dataset_id).exists():
                if current:
                    continue
                # DL3 (m7): a sample that runs trained on (or that has versions) is never rewritten in place: the new
                # generator's rows become a new version of it, with lineage and an audit row
                if self._sample_in_use(dataset_id):
                    try:
                        made += self._sample_as_version(dataset_id, spec, fp)
                    except Exception:  # noqa: BLE001 - never fail startup over a sample; the old one stays as it was
                        log.exception("sample %s: could not write the regenerated rows as a new version", dataset_id)
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

    def _sample_in_use(self, dataset_id: str) -> bool:
        with self.db.connect() as con:
            return bool(con.execute("SELECT 1 FROM lineage_edges WHERE src_kind = 'dataset' AND src_id = ? AND relation IN"
                                    " ('trained_on', 'derived_from') LIMIT 1", (dataset_id,)).fetchone())

    def _sample_as_version(self, dataset_id: str, spec: dict[str, Any], fp: str) -> list[str]:
        """DL3 (m7): the regenerated sample as a new version of the one in use (rows matched by key or exact hash)."""
        from . import dataset_versions as DV

        df, sidecar = spec["generate"](seed=config.SEED)
        df = pd.read_csv(io.StringIO(df.to_csv(index=False)))
        pdf = self.load_frame(dataset_id)
        match = DV.match_rows(pdf, df, None)
        checks = DV.contract_checks(pdf, df, (self.db.get_dataset(dataset_id) or {}).get("target"))
        rec = self.derive(dataset_id, df, None, "sample_regenerated",
                          [{"op": "rows_changed", "n": match["changed"]}, {"op": "rows_added", "n": match["added"]},
                           {"op": "rows_removed", "n": match["removed"]}],
                          name=spec["name"], note=f"The sample's generator changed ({fp}): its new rows, as a new version (the version "
                                                  "runs trained on is kept as it was)",
                          extra={"kind": "sample_regenerated", "generator": fp, "matched_by": match["matched_by"], "matched": match["matched"],
                                 "changed": match["changed"], "added": match["added"], "removed": match["removed"], "checks": checks},
                          by="system:sample generator", row_map=match["row_map"], new_holdout_share=config.HOLDOUT_SHARE,
                          reason="the sample generator changed", dedupe=True)
        if not rec.get("deduplicated"):
            (self.dir(rec["id"]) / spec["sidecar"]).write_text(json.dumps(sidecar, indent=1))
        (self.dir(dataset_id) / ".generator").write_text(fp)
        log.info("sample %s is in use: the regenerated rows are version %s", dataset_id, rec["id"])
        return [rec["id"]]

    def create_version(self, parent_id: str, df: pd.DataFrame, splits: dict[str, Any], *, name: str, note: str,
                       lineage: dict[str, Any], op: str | None = None, schema: dict[str, Any] | None = None,
                       target: str | None = None) -> dict[str, Any]:
        """A new dataset derived from `parent_id` (Autopilot: relabelled / dropped / synthetic rows). The splits are
        given, not drawn: the holdout is the parent's holdout rows that were kept, so scores stay comparable.
        `lineage` (parent, root, root row of every row, the changes) goes to lineage.json and a summary to the profile."""
        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        dataset_id = "ds_" + secrets.token_hex(4)
        d = self.dir(dataset_id)
        d.mkdir(parents=True, exist_ok=True)
        if schema is None:  # DL4: the parent's schema as the hint (an int column stays int, text stays text)
            schema = FIO.schema_of(df, FIO.columns(FIO.load(self.dir(parent_id))))
        self.csv_path(dataset_id).write_bytes(FIO.to_csv_bytes(df, schema))
        FIO.save(d, schema)
        df = FIO.read(self.csv_path(dataset_id), schema)
        tgt0 = target if target is not None else parent["target"]  # DL4 (M-c): an edit that names a new target sets it
        profile = profile_frame(df, tgt0 if tgt0 in df.columns else None)
        save_splits(self.legacy_splits_path(dataset_id), splits)
        self.record_split_file(dataset_id, self.legacy_splits_path(dataset_id), "drawn with the version")
        tgt = profile["target"]
        # The given split is stored under its own target (adopted from splits.json, rows unchanged).
        st = splits.get("target") if splits.get("target") in df.columns else tgt
        _, splits, _ = self.ensure_splits(dataset_id, st if st in df.columns else None)
        profile["target_source"] = "confirmed" if target is not None else parent["profile"].get("target_source", "given")
        profile["splits"] = summarize(splits, df[st] if st in df.columns else None)
        (d / "lineage.json").write_text(json.dumps(lineage))
        profile["lineage"] = {k: v for k, v in lineage.items() if k not in ("root_rows", "changes")}
        rec = {
            "id": dataset_id, "name": name, "filename": f"{dataset_id}.csv", "source": "upload", "note": note,
            "created_at": now(), "size_bytes": self.csv_path(dataset_id).stat().st_size, "rows": len(df), "target": tgt,
            "kind": profile["kind"], "profile": profile,
        }
        self.db.insert_dataset(rec)
        with self.db.connect() as con:
            self._refresh_splits_sha(con, dataset_id)  # DL4 (M-d): stored with the version
        rr = lineage.get("root_rows")
        self.catalog.record_version(parent_id, rec, self.csv_path(dataset_id), op=str(op or lineage.get("kind") or "derived"),
                                    actor=str(lineage.get("by") or lineage.get("created_by") or "system"),
                                    meta={k: lineage.get(k) for k in ("rows_before", "rows_after", "relabelled", "dropped", "synthetic", "reason", "duplicate_of",
                                                                       "ops_summary")
                                          if lineage.get(k) is not None} | {"n_changes": len(lineage.get("changes") or [])},
                                    paired=bool(rr) and len(rr) == len(df))
        return rec

    def derive(self, parent_id: str, df: pd.DataFrame, keep_idx: Any, op: str, changes: list[Any], *, name: str, note: str,
               splits: dict[str, Any] | None = None, extra: dict[str, Any] | None = None, by: str | None = None,
               new_rows: int = 0, row_map: Any = None, new_holdout_share: float = 0.0, reason: str | None = None,
               dedupe: bool | None = None, schema: dict[str, Any] | None = None, target: str | None = None) -> dict[str, Any]:
        """P1 step 2: the one way to make a dataset version. `df` is the new frame: the parent's rows `keep_idx` (positions
        in the parent, in order; a bool mask or ints; None = every row) followed by `new_rows` rows with no root row
        (synthetic: root -1-i). Writes data.csv, lineage.json {parent, root, root_rows, changes, op, …extra}, the version's
        columns and a derived_from edge. `splits` defaults to the parent's split mapped through keep_idx (the holdout is the
        parent's holdout rows that were kept, so scores stay paired).

        DL1: `row_map` (one entry per row of `df`: its position in the parent, or -1 for a new row) replaces keep_idx /
        new_rows when the rows are not "kept rows, then new ones" (an uploaded version: matched rows anywhere, in the
        file's own order). Matched rows keep their root row and their parent's holdout / fold; new rows get root ids
        below the parent's lowest, and a seeded `new_holdout_share` of them go to the holdout (0 = none, as for
        synthetic rows)."""
        import numpy as np

        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        plin = self.lineage(parent_id) or {}
        n_parent = int(parent["rows"])
        proots = plin.get("root_rows")
        proots = np.asarray(proots, dtype=int) if proots and len(proots) == n_parent else np.arange(n_parent)
        if row_map is None:
            if keep_idx is None:
                keep = np.arange(n_parent)
            else:
                keep = np.asarray(keep_idx)
                keep = np.flatnonzero(keep) if keep.dtype == bool else keep.astype(int)
            if len(keep) + new_rows != len(df):
                raise DatasetError(f"derive: {len(keep)} kept + {new_rows} new rows ≠ {len(df)} rows in the frame.")
            rmap = np.concatenate([keep, np.full(new_rows, -1, dtype=int)])
        else:
            rmap = np.asarray(row_map, dtype=int)
            if len(rmap) != len(df) or (rmap >= n_parent).any():
                raise DatasetError(f"derive: row_map has {len(rmap)} entries for {len(df)} rows, or points past the parent's {n_parent}.")
        new_pos = np.flatnonzero(rmap < 0)
        neg0 = min(0, int(proots.min()) if len(proots) else 0)  # new rows never reuse a root id the parent's new rows hold
        root_rows = [int(proots[p]) if p >= 0 else 0 for p in rmap]
        for i, j in enumerate(new_pos):
            root_rows[j] = neg0 - 1 - i
        if schema is None:  # DL4: the parent's schema as the hint (an int column stays int, text stays text)
            schema = FIO.schema_of(df, FIO.columns(self.schema(parent_id)))
        if splits is None:
            st = parent["target"] if parent["target"] in df.columns else None
            _, sp, _ = self.ensure_splits(parent_id, parent["target"])
            k = int(sp.get("n_folds") or config.N_FOLDS)
            ph = set(int(h) for h in sp["holdout"])
            rng = np.random.default_rng(config.SEED + len(df))
            new_hold = set(int(j) for j in new_pos[rng.random(len(new_pos)) < new_holdout_share]) if new_holdout_share else set()
            fold, hold = [], []
            for i, p in enumerate(rmap):
                if p >= 0:
                    fold.append(int(sp["fold"][p]))
                    if int(p) in ph:
                        hold.append(i)
                elif i in new_hold:
                    fold.append(-1)
                    hold.append(i)
                else:
                    fold.append(i % k)
            splits = {**sp, "holdout": hold, "fold": fold, **({} if st else {"target": None})}
        splits = {**splits, "derived_from": parent_id}
        lineage = {**(extra or {}), "parent": parent_id, "root": plin.get("root") or parent_id, "kind": (extra or {}).get("kind") or op,
                   "op": op, "rows_before": (extra or {}).get("rows_before", n_parent), "rows_after": int(len(df)),
                   "root_rows": root_rows, "changes": changes,
                   **({"parent_rows": [int(x) for x in rmap]} if row_map is not None else {})}
        from .catalog import governance as G

        by = by or G.current_actor()  # DL2: the UI's "Acting as" header when the path passes no name
        if not by:  # DL3 (m3): a version needs a name; only genuine background jobs are "system:<job>", labelled as such
            if op not in SYSTEM_OPS:
                raise DatasetError(f"Say who is making this {op} version: send by (or the X-Ensemble-Actor header); "
                                   "every version records a name.")
            by = f"system:{op}"
        reason = (reason or G.current_reason() or "").strip()[:500]  # DL2: an optional reason / change ticket
        lineage["by"] = by
        if reason:
            lineage["reason"] = reason
        # DL3 (m3): the same bytes as a version of this dataset that still exists: return it (dedupe), or flag it
        import hashlib

        sha = hashlib.sha256(FIO.to_csv_bytes(df, schema)).hexdigest()
        root = plin.get("root") or parent.get("root_id") or parent_id
        same = self._same_bytes(sha, root, schema)
        same = (same,) if same else None
        if same and (dedupe if dedupe is not None else op in DEDUPE_OPS):
            from .catalog.schema import audit

            with self.db.connect() as con:
                audit(con, by, "dataset_version_deduplicated", comment=reason,
                      details={"dataset_id": same[0], "root_id": root, "parent": parent_id, "op": op, "sha256": sha,
                               "note": "the same bytes as an existing version: no new version was made, the existing one is returned"})
            rec = dict(self.db.get_dataset(same[0]) or {})
            rec["deduplicated"] = {"existing": same[0], "sha256": sha}
            return rec
        if same and not any(w.get("code") == "identical_file" for w in (lineage.get("checks") or {}).get("warnings") or []):
            lineage["duplicate_of"] = same[0]
            lineage.setdefault("checks", {"warnings": []}).setdefault("warnings", []).append(
                {"code": "identical_file", "message": f"The same rows as {same[0]} (sha256 {sha[:12]}…): this version adds nothing but a pointer."})
        return self.create_version(parent_id, df, splits, name=name, note=note, lineage=lineage, op=op, schema=schema, target=target)

    def lineage(self, dataset_id: str) -> dict[str, Any] | None:
        p = self.dir(dataset_id) / "lineage.json"
        return json.loads(p.read_text()) if p.exists() else None

    def create_from_upload(self, filename: str, content: bytes, target: str | None = None, by: str | None = None) -> dict[str, Any]:
        df, note, stem = read_upload(filename, content)
        return self.create_from_frame(df, name=stem, filename=Path(filename).name, note=note, target=target, by=by)

    def upload_version(self, parent_id: str, filename: str, content: bytes, *, by: str, row_key: str | None = None,
                       preview: bool = False, reason: str | None = None) -> dict[str, Any]:
        """DL1: a new file as the next version of `parent_id`. Rows are matched to the parent by `row_key` (a column
        unique in both files) or, without one, by an exact hash of the row over the columns both files share with an
        unchanged type. Matched rows keep their original row and their holdout / fold, so runs on the two versions
        pair on those rows; new rows get a seeded share of the holdout. Contract checks warn, never block."""
        from . import dataset_versions as DV

        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        df, note, _ = read_upload(filename, content)
        kept = list(df.attrs.get("kept_as_text") or [])
        held = {c: df[c] for c in kept}
        df = _normalize_frame(df)
        for c, v in held.items():
            if c in df.columns:
                df[c] = v
        pdf = self.load_frame(parent_id)
        pschema = FIO.columns(self.schema(parent_id, pdf))
        # DL4 (m-e): a column the parent keeps as text stays text in the new file (its codes keep their zeros)
        schema = FIO.schema_of(df, pschema, {c: "text" for c in df.columns if pschema.get(c) == "text" or c in kept})
        df = FIO.roundtrip(df, schema)  # exactly what data.csv will read back as
        match = DV.match_rows(pdf, df, row_key)
        checks = DV.contract_checks(pdf, df, parent["target"])
        import hashlib

        sha = hashlib.sha256(FIO.to_csv_bytes(df, schema)).hexdigest()  # the bytes create_version will write
        same = self._same_bytes(sha, parent.get("root_id") or parent_id, schema)
        same = (same,) if same else None
        if same:
            checks["warnings"].append({"code": "identical_file", "message": f"This file has the same rows as {same[0]} (sha256 "
                                       f"{sha[:12]}…): the new version adds nothing but a pointer."})
        extra = {"kind": "upload_version", "filename": Path(filename).name, "matched_by": match["matched_by"],
                 "row_key": match.get("row_key"), "matched": match["matched"], "changed": match["changed"],
                 "added": match["added"], "removed": match["removed"], "checks": checks,
                 "columns_before": int(pdf.shape[1]), "columns_after": int(df.shape[1]),
                 **({"kept_as_text": kept} if kept else {})}
        if kept:
            checks.setdefault("notes", []).append(f"Kept as text (codes with leading zeros): {', '.join(kept)}.")
        changes = [{"op": "rows_changed", "n": match["changed"]}, {"op": "rows_added", "n": match["added"]},
                   {"op": "rows_removed", "n": match["removed"]}]
        changes = [c for c in changes if c["n"]] + [{"op": "column_added", "column": c} for c in checks["columns_added"]] + \
            [{"op": "column_removed", "column": c} for c in checks["columns_removed"]]
        n_ver = 1 + len(self.catalog.tree_ids(parent_id))
        if preview:  # DL2: what the upload would make, written nowhere
            return {"preview": True, "v": n_ver, "parent": parent_id, "filename": Path(filename).name, "rows": int(len(df)),
                    "n_cols": int(df.shape[1]), "sha256": sha, **{k: v for k, v in extra.items() if k != "kind"},
                    "would_be_default": not checks["warnings"],
                    "note": "nothing was written: send the file again without preview to create the version"}
        if reason:
            extra["reason"] = reason.strip()[:500]
        rec = self.derive(parent_id, df, None, "upload_version", changes, name=parent["name"],
                          note=f"{note} as v{n_ver} of {parent['name']}: {match['matched']:,} rows matched by "
                               f"{match['matched_by']}, {match['changed']:,} changed, {match['added']:,} new, {match['removed']:,} gone",
                          extra=extra, by=by, row_map=match["row_map"], new_holdout_share=config.HOLDOUT_SHARE, reason=reason,
                          schema=schema)
        rec["upload"] = {k: v for k, v in extra.items() if k != "kind"}
        return rec


    def edit_version(self, parent_id: str, ops: Any, *, by: str, reason: str, preview: bool = False,
                     set_target: Any = None) -> dict[str, Any]:
        """DL3: typed edits (dataset_edit.OPS) applied to `parent_id`, committed as a NEW version through derive(): edited
        rows keep their root row and holdout / fold, new rows get new root ids (and a seeded share of the holdout),
        deleted rows are gone. The version edited is never changed. `preview` writes nothing. Refused (409) when the
        parent's file on disk no longer matches its stored sha256 (an edit would carry an outside change forward).

        DL4: column types are kept (schema.json: an int stays int, text stays text; a cast changes the schema and makes a
        real version). An op that drops, renames or casts the target is refused unless `set_target` {column, reason}
        names the target of the new version explicitly (audited either way)."""
        import hashlib

        from . import dataset_edit as DE
        from . import dataset_versions as DV
        from .catalog.schema import audit, file_sha256

        parent = self.db.get_dataset(parent_id)
        if not parent:
            raise DatasetError(f"No dataset with id '{parent_id}'.")
        stored, on_disk = parent.get("sha256"), file_sha256(self.csv_path(parent_id))
        if stored and on_disk != stored:
            raise DatasetConflict(f"The data on disk differs from {parent_id} (stored sha256 {stored[:12]}…, file now "
                                  f"{(on_disk or 'missing')[:12]}…): it was changed outside the app, so it can't be edited. "
                                  "Restore the file, or create a new version from it (Data → the version → Create a new version "
                                  "from this file).")
        st_col, st_reason = None, ""
        if set_target is not None:
            if not isinstance(set_target, dict) or not isinstance(set_target.get("column"), str) or not set_target["column"].strip():
                raise DatasetError('set_target is {"column": <the new version\'s target>, "reason": "…"}.')
            st_col, st_reason = set_target["column"].strip(), str(set_target.get("reason") or "").strip()
            if len(st_reason) < 10:
                raise DatasetError("Changing the target needs its own reason (set_target.reason, at least 10 characters): "
                                   "models trained on the old target can't be compared with ones trained on the new.")
        pdf = self.load_frame(parent_id)
        pschema = FIO.columns(self.schema(parent_id, pdf))
        try:
            res = DE.apply_ops(pdf, ops, parent["target"])
        except DE.EditError as e:
            raise DatasetError(str(e)) from e
        old_t = parent["target"]
        if res["target_touched"] and st_col is None:  # DL4 (M-c): never re-target silently
            t = res["target_touched"]
            msg = (f"Operation {t['i']} {t['what']} the target '{old_t}'. Runs on this version would predict something else, so "
                   "this is refused. Leave the target alone, or say what the new version predicts: send "
                   '"set_target": {"column": "<column>", "reason": "<why the target changes>"} with the edit.')
            with self.db.connect() as con:
                audit(con, by, "dataset_edit_refused", comment=msg[:500],
                      details={"dataset_id": parent_id, "root_id": parent.get("root_id") or parent_id, "status": 400,
                               "target": old_t, "op": t})
            raise DatasetError(msg)
        df = res["frame"]
        if st_col is not None and st_col not in df.columns:
            raise DatasetError(f"set_target: no column {st_col!r} in the edited version (columns: {', '.join(map(str, df.columns[:12]))}).")
        if st_col is not None:
            check_target(df[st_col].dropna(), st_col)
        # the schema of the new version: the parent's, through renames and drops; casts set their column's kind
        inv = {}
        for old, new in res["renamed"].items():
            inv[new] = inv.pop(old, old)
        hint = {c: pschema.get(inv.get(c, c)) for c in df.columns if pschema.get(inv.get(c, c))}
        schema = FIO.schema_of(df, hint, res["casts"])
        df = FIO.roundtrip(df, schema)  # exactly what data.csv will read back as
        if df.shape[1] < 2 or len(df) < 2:
            raise DatasetError("A version needs at least two rows and two columns.")
        sha = hashlib.sha256(FIO.to_csv_bytes(df, schema)).hexdigest()
        rmap = np.asarray(res["row_map"], dtype=int)
        ib = np.flatnonzero(rmap >= 0)
        ia = rmap[ib]
        # DL4 (m-g): compare renamed columns under their old name, as the diff does
        changed = int(DV._changed(pdf, df.rename(columns=inv), ia, ib).sum())
        new_t = st_col if st_col is not None else res["target"]
        checks = DV.contract_checks(pdf, df.rename(columns=inv), old_t if st_col is None or st_col == old_t else None)
        for old, new in res["renamed"].items():  # a rename is not a loss: say so next to the warning it raises
            for w in checks["warnings"]:
                if w.get("column") == old and w["code"] == "column_removed":
                    w["message"] = f"'{old}' was renamed to '{new}': runs that used '{old}' can't be re-fitted on this version as they were."
        if res["renamed"]:  # contract_checks saw the renamed columns under their old names: report the rename itself
            for old, new in res["renamed"].items():
                if old in pdf.columns:
                    checks["warnings"].append({"code": "column_removed", "column": old,
                                               "message": f"'{old}' was renamed to '{new}': runs that used '{old}' can't be re-fitted on this version as they were."})
            checks["columns_removed"] = sorted(set(checks.get("columns_removed") or []) | {o for o in res["renamed"] if o in pdf.columns})
            checks["columns_added"] = sorted(set(checks.get("columns_added") or []) | {n for n in res["renamed"].values() if n in df.columns})
        if st_col is not None and st_col != old_t:
            checks["warnings"].append({"code": "target_changed", "column": st_col,
                                       "message": f"The target changes from '{old_t}' to '{st_col}' ({st_reason}): runs on this version "
                                                  "predict something else, and are not compared with runs on the old target."})
        kinds_changed = [{"column": c, "from": pschema.get(inv.get(c, c)), "to": k} for c, k in FIO.columns(schema).items()
                         if pschema.get(inv.get(c, c)) and pschema.get(inv.get(c, c)) != k]
        root = parent.get("root_id") or parent_id
        same = self._same_bytes(sha, root, schema)
        n_ver = 1 + len(self.catalog.tree_ids(parent_id))
        summary = {"matched": int(len(ib)), "changed": changed, "added": int((rmap < 0).sum()), "removed": int(len(pdf) - len(ib)),
                   "columns_before": int(pdf.shape[1]), "columns_after": int(df.shape[1]), "checks": checks,
                   "renamed": res["renamed"], "edit_log": res["log"], "ops_summary": [e["summary"] for e in res["log"]],
                   "n_ops": len(res["log"]), "schema": FIO.columns(schema), "types_changed": kinds_changed,
                   "target": {"before": old_t, "after": new_t, **({"set_by_edit": True, "reason": st_reason} if st_col is not None else {})}}
        if same:
            summary["duplicate_of"] = same
        if preview:
            cols = list(df.columns)
            return {"preview": True, "v": n_ver, "parent": parent_id, "rows": int(len(df)), "n_cols": int(df.shape[1]), "sha256": sha,
                    **summary, "would_be_default": not checks["warnings"] and not same,
                    "columns": cols, "sample": json.loads(df.head(5).to_json(orient="records")),
                    "note": "nothing was written: send the same operations without preview to create the version"}
        changes = [{"op": e["op"], "n": e.get("cells") or e.get("rows") or 0, **({"column": e["column"]} if e.get("column") else {})}
                   for e in res["log"]]
        extra = {"kind": "edit", "matched_by": "edit (rows keep their identity)", **summary}
        rec = self.derive(parent_id, df, None, "edit", changes, name=parent["name"],
                          note=f"Edited {parent_id} as v{n_ver}: " + "; ".join(summary["ops_summary"][:4])
                               + ("…" if len(summary["ops_summary"]) > 4 else ""),
                          extra=extra, by=by, row_map=res["row_map"], new_holdout_share=config.HOLDOUT_SHARE, reason=reason,
                          schema=schema, target=new_t if st_col is not None else None)
        if rec.get("deduplicated"):
            return {**rec, "created": False, "edit": {k: v for k, v in summary.items() if k != "edit_log"}}
        # the full list of operations as sent (every cell), beside lineage.json, whose log is capped
        (self.dir(rec["id"]) / "edit_ops.json").write_text(json.dumps({"parent": parent_id, "by": by, "reason": reason, "ops": ops,
                                                                        **({"set_target": set_target} if set_target else {})}, default=str))
        if st_col is not None:
            with self.db.connect() as con:
                audit(con, by, "dataset_target_changed", comment=st_reason[:500],
                      details={"dataset_id": rec["id"], "root_id": root, "parent": parent_id, "from": old_t, "to": st_col,
                               "edit_reason": reason})
        rec["edit"] = summary
        rec["created"] = True
        return rec

    def create_from_frame(self, df: pd.DataFrame, *, name: str, filename: str, note: str, target: str | None = None,
                          lineage: dict[str, Any] | None = None, by: str | None = None) -> dict[str, Any]:
        """A new dataset from a frame (an upload, or G9's SQL query). `lineage` (G9: the query, the redacted URL, the
        version) goes to lineage.json and the profile, like Autopilot's derived versions."""
        df.columns = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(df.columns)]
        if df.columns.duplicated().any():
            raise DatasetError("Column names must be unique.")
        if df.shape[1] < 2:
            raise DatasetError("A dataset needs at least two columns.")
        if len(df) < 2:
            raise DatasetError("A dataset needs at least two rows.")
        from .profiling import coerce_numeric

        kept = list(df.attrs.get("kept_as_text") or [])
        held = {c: df[c] for c in kept if c in df.columns}
        df, coerced = coerce_numeric(df)  # A1 #5: '  ' / 'n/a' / '$12,000' in a number column must not make it a category or an ID
        dataset_id = "ds_" + secrets.token_hex(4)
        d = self.dir(dataset_id)
        d.mkdir(parents=True, exist_ok=True)
        for c, v in held.items():  # DL4 (m-e): codes with leading zeros are not numbers
            df[c] = v
        coerced = [x for x in coerced if x.get("column") not in held]
        schema = FIO.schema_of(df, force={c: "text" for c in kept})  # DL4 (m-e): types travel with the version
        self.csv_path(dataset_id).write_bytes(FIO.to_csv_bytes(df, schema))
        FIO.save(d, schema)
        if coerced:
            (d / "coerced.json").write_text(json.dumps(coerced))
        df = FIO.read(self.csv_path(dataset_id), schema)  # profile exactly what the engine will read back
        try:
            rec = self._register(dataset_id, df, name=name, filename=filename, source="upload", note=note,
                                 size_bytes=self.csv_path(dataset_id).stat().st_size, target=target, by=by)
        except DatasetError:
            shutil.rmtree(d, ignore_errors=True)
            raise
        if kept:  # DL4 (m-e): say which columns were kept as text, and why
            rec["profile"]["kept_as_text"] = kept
            rec["profile"].setdefault("warnings", []).append(
                f"Kept as text (codes with leading zeros, e.g. ZIP or ID codes): {', '.join(kept)}. Cast to number in Data → Edit if they are amounts.")
            self.db.insert_dataset(rec)
        if lineage:
            lineage = {**lineage, "root": lineage.get("root") or dataset_id}
            (d / "lineage.json").write_text(json.dumps(lineage))
            rec["profile"]["lineage"] = lineage
            self.db.insert_dataset(rec)
            if lineage.get("parent") and self.db.get_dataset(lineage["parent"]):  # a SQL refresh: a version of new rows
                self.catalog.record_version(lineage["parent"], rec, self.csv_path(dataset_id), op=str(lineage.get("source") or "refresh"),
                                            meta={"version": lineage.get("version")}, paired=False)
        return rec
