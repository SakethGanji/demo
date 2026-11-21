"""Settings read from the environment, with defaults for local use."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: One seed for everything that should be reproducible (samples, holdout, folds).
SEED = 42
HOLDOUT_SHARE = 0.2
N_FOLDS = 5
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ensemble.sqlite3"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def lake_dir(self) -> Path:
        """P02: the LocalBlobStore root (blobs/, tmp/, exports/). ENSEMBLE_LAKE_DIR overrides it."""
        v = os.environ.get("ENSEMBLE_LAKE_DIR")
        return Path(v).resolve() if v else self.data_dir / "lake"

    @property
    def duck_spill_dir(self) -> Path:
        """P02: DuckDB spill files (put it on its own volume in a shared deployment). ENSEMBLE_DUCK_SPILL_DIR overrides it."""
        v = os.environ.get("ENSEMBLE_DUCK_SPILL_DIR")
        return Path(v).resolve() if v else self.data_dir / "tmp" / "duck"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("ENSEMBLE_DATA_DIR", BACKEND_DIR / "data")).resolve()
    return Settings(data_dir=data_dir)


#: P1 step 7: predictor retention. NEEDS MODEL-RISK APPROVAL before it runs on a timer: retention periods are bank
#: policy. Until then the job runs only when an admin calls POST /api/retention/run (every action is audited), and
#: GET /api/retention/plan is the dry run. Days are counted from the run's finish.
RETENTION_POLICY = {
    "status": "needs model-risk approval",
    "slim_after_days": 7,      # present → slimmed: delete_models(models_to_keep='best') + save_space()
    "evict_after_days": 30,    # → evicted: the predictor folder is deleted; config, result, holdout predictions,
                               #   leaderboard, journal and hashes are kept
    "failed_evict_after_days": 3,
    "top_k_pinned": 3,         # the best runs per project and dataset are never evicted
    "keep_tag": "keep",
}


def admins() -> set[str]:
    """Names allowed to run retention (no sign-in: names, as four-eyes uses them). ENSEMBLE_ADMINS, comma-separated."""
    from .catalog.governance import admins as _admins  # identity keys: letters and digits, accents and lookalikes folded

    return _admins()


#: DL2: data behind a retired model may be deleted only this many days after the retirement (four eyes still apply).
#: NEEDS MODEL-RISK APPROVAL: the period is bank record-retention policy. ENSEMBLE_RETIRED_DATA_RETENTION_DAYS overrides
#: it; the default is 7 years.
def retired_data_retention_days() -> float:
    v = os.environ.get("ENSEMBLE_RETIRED_DATA_RETENTION_DAYS")
    try:
        return float(v) if v not in (None, "") else 2557.0
    except ValueError:
        return 2557.0


def verify_every_hours() -> float | None:
    """DL2: re-hash every active dataset version on a timer (ENSEMBLE_VERIFY_EVERY_HOURS). No default: off unless set."""
    try:
        v = float(os.environ.get("ENSEMBLE_VERIFY_EVERY_HOURS", "") or 0)
    except ValueError:
        return None
    return v if v > 0 else None


def _env_num(name: str, default: float, lo: float | None = None) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return default if lo is not None and v < lo else v


# == P02: the lake (docs/plans/data-platform-final.md §3, §5.2, §5.10; B §5.1) ========================================

#: 'local' (LocalBlobStore under Settings.lake_dir) is the only store until P10b adds 's3' (MinIO).
def lake_store_kind() -> str:
    return (os.environ.get("ENSEMBLE_LAKE_STORE") or "local").strip().lower()


#: DuckDB limits (B §5.1). Interactive = one process-wide connection shared by API requests; job = one per job.
def duck_interactive_mb() -> int:
    return int(_env_num("ENSEMBLE_DUCK_INTERACTIVE_MB", 512, 64))


def duck_interactive_threads() -> int:
    return int(_env_num("ENSEMBLE_DUCK_INTERACTIVE_THREADS", 2, 1))


def duck_interactive_slots() -> int:
    """How many interactive queries may run at once (the semaphore)."""
    return int(_env_num("ENSEMBLE_DUCK_INTERACTIVE_SLOTS", 8, 1))


def duck_interactive_timeout_s() -> float:
    """An interactive query is interrupted after this many seconds."""
    return _env_num("ENSEMBLE_DUCK_INTERACTIVE_TIMEOUT_S", 10.0, 0.1)


def duck_job_mb() -> int:
    return int(_env_num("ENSEMBLE_DUCK_JOB_MB", 2048, 64))


def duck_job_threads() -> int:
    return int(_env_num("ENSEMBLE_DUCK_JOB_THREADS", max(1, (os.cpu_count() or 2) // 2), 1))


def duck_spill_gb() -> float:
    """max_temp_directory_size per connection: a query that would spill more fails instead of filling the disk."""
    return _env_num("ENSEMBLE_DUCK_SPILL_GB", 20.0, 0.1)


#: §3.1 / A §2.5: table parts. Row groups of 122 880 rows (30 720 above 200 columns); at most 8M rows per part.
PART_MAX_ROWS = int(_env_num("ENSEMBLE_LAKE_PART_MAX_ROWS", 8_000_000, 1000))
ROW_GROUP_ROWS, ROW_GROUP_ROWS_WIDE, WIDE_COLS = 122_880, 30_720, 200
PARQUET_COMPRESSION, PARQUET_LEVEL = "zstd", 3

#: §5.10 GC (dry-run only until P24): an unreferenced blob is a candidate after these grace periods.
GC_PENDING_GRACE_HOURS = 24.0
GC_LIVE_GRACE_DAYS = 7.0
GC_ORPHAN_GRACE_HOURS = 24.0



# == P11: the training frame and sampling (docs/plans/data-platform-final.md §5.9, R12, R13) ==========================

def train_frame_share() -> float:
    """The training frame's budget as a share of the worker's RAM (§11: 10 %). ENSEMBLE_TRAIN_FRAME_SHARE overrides it."""
    return min(1.0, _env_num("ENSEMBLE_TRAIN_FRAME_SHARE", 0.10, 0.001))


def worker_ram_mb() -> int | None:
    """The worker's RAM when it is not the host's (a container limit the cgroup does not show). ENSEMBLE_WORKER_RAM_MB."""
    v = _env_num("ENSEMBLE_WORKER_RAM_MB", 0, 0)
    return int(v) if v > 0 else None


#: R12's per-column estimator (bytes per cell of the engine_v1 pandas frame): 8 B for a number / bool cell; an object
#: string cell costs a pointer plus the str object (≈ 50 B + its length). The load peaks at ≈ 1.6 × the frame.
FRAME_NUM_BYTES = 8
FRAME_STR_BASE = 58
FRAME_LOAD_PEAK = 1.6
#: §5.9 step 3: the holdout is never sampled; it is scored in batches of this many rows.
HOLDOUT_BATCH_ROWS = int(_env_num("ENSEMBLE_HOLDOUT_BATCH_ROWS", 500_000, 1000))
#: §5.9 step 2: a class with at most this many training rows is kept whole by the stratified sample.
SAMPLE_MIN_CLASS_ROWS = 1000

#: P11 calibration: peak RSS of a whole AutoGluon 1.6.3 fit (the worker process and its children) per byte of the
#: estimated training frame, by preset; measured at 1M and 5M rows (docs/plans/B-scale-performance.md, "P11"). A run's
#: frame budget is also capped so that estimate × factor stays under FIT_RAM_SHARE of the worker's RAM.
FIT_PEAK_PER_FRAME_BYTE: dict[str, float] = {}
FIT_RAM_SHARE = 0.8


#: F-X1 C1: the widest file an upload accepts (400 too_many_columns above it). Bank tables of 1-2k columns are real.
def max_columns() -> int:
    return int(_env_num("ENSEMBLE_MAX_COLUMNS", 2000, 2))
