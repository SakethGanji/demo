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


#: Predictor retention pins (P1 step 7). P24 / §11 item 3: retention is manual and has NO default period; a team's
#: periods live in its policy (`retention.runs.slim_days / evict_days`, §6.4) or are sent with the request, and a person
#: starts every action (catalog/retention.py). Only the pin rules stay here.
RETENTION_POLICY = {
    "status": "manual (no timer, no default period): §11 item 3",
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

#: P10b: 'local' (LocalBlobStore under Settings.lake_dir; dev) or 's3' (S3BlobStore on S3 / MinIO; the shared
#: deployment's default, §11 item 1). ENSEMBLE_BLOB_STORE (P02's ENSEMBLE_LAKE_STORE is still read). `s3://bucket/prefix`
#: is accepted too and means 's3' with that bucket and prefix.
def lake_store_kind() -> str:
    v = (os.environ.get("ENSEMBLE_BLOB_STORE") or os.environ.get("ENSEMBLE_LAKE_STORE") or "local").strip()
    return "s3" if v.lower().startswith("s3") else v.lower()


def s3_config() -> dict:
    """P10b: the S3BlobStore settings. Credentials are NOT settings: boto3's default chain reads them (AWS_ACCESS_KEY_ID /
    AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, AWS_PROFILE, an instance role or IRSA). Nothing here is a secret.

    ENSEMBLE_S3_ENDPOINT    http://minio:9000 for MinIO; empty for AWS
    ENSEMBLE_S3_BUCKET      the bucket (or ENSEMBLE_BLOB_STORE=s3://bucket/prefix)
    ENSEMBLE_S3_PREFIX      a key prefix inside the bucket ('' by default)
    ENSEMBLE_S3_REGION      us-east-1 by default
    ENSEMBLE_S3_URL_STYLE   path (MinIO) | virtual
    ENSEMBLE_S3_READ        cache (DuckDB reads the verified local cache; the default) | httpfs (DuckDB reads s3://)
    ENSEMBLE_S3_LOCK_DAYS   Object Lock GOVERNANCE retention per blob when the bucket has Object Lock (30; 0 = none).
                            NEEDS MODEL-RISK APPROVAL: retention is bank policy. Purge (P24) bypasses governance.
    ENSEMBLE_S3_CONDITIONAL auto | on | off (off = HEAD then PUT, for a server without If-None-Match)
    ENSEMBLE_S3_MULTIPART_MB a blob above this goes up as a multipart upload (4,096)
    ENSEMBLE_S3_CREATE_BUCKET 1 = create a missing bucket (with Object Lock); dev only
    ENSEMBLE_CACHE_DIR      the read cache (data_dir/cache by default)
    ENSEMBLE_CACHE_MAX_GB   its cap (20), LRU
    ENSEMBLE_CACHE_VERIFY   stat (default) | always | fill
    ENSEMBLE_CACHE_PIN_S    an entry used this recently is never evicted (300)
    """
    url = (os.environ.get("ENSEMBLE_BLOB_STORE") or os.environ.get("ENSEMBLE_LAKE_STORE") or "").strip()
    bucket, prefix = os.environ.get("ENSEMBLE_S3_BUCKET", ""), os.environ.get("ENSEMBLE_S3_PREFIX", "")
    if url.lower().startswith("s3://"):
        b, _, p = url[5:].partition("/")
        bucket, prefix = b or bucket, p or prefix
    read = (os.environ.get("ENSEMBLE_S3_READ") or "cache").strip().lower()
    return {"endpoint": os.environ.get("ENSEMBLE_S3_ENDPOINT") or None, "bucket": bucket, "prefix": prefix.strip("/"),
            "region": os.environ.get("ENSEMBLE_S3_REGION") or "us-east-1",
            "url_style": (os.environ.get("ENSEMBLE_S3_URL_STYLE") or "path").strip().lower(),
            "read": read if read in ("cache", "httpfs") else "cache",
            "lock_days": _env_num("ENSEMBLE_S3_LOCK_DAYS", 30.0, 0),
            "conditional": (os.environ.get("ENSEMBLE_S3_CONDITIONAL") or "auto").strip().lower(),
            "multipart_mb": int(_env_num("ENSEMBLE_S3_MULTIPART_MB", 4096, 5)),
            "create_bucket": os.environ.get("ENSEMBLE_S3_CREATE_BUCKET", "") in ("1", "true", "yes"),
            "cache_max_gb": _env_num("ENSEMBLE_CACHE_MAX_GB", 20.0, 0.001),
            "cache_verify": (os.environ.get("ENSEMBLE_CACHE_VERIFY") or "stat").strip().lower(),
            "cache_pin_s": _env_num("ENSEMBLE_CACHE_PIN_S", 300.0, 0)}


def cache_dir(settings: "Settings") -> Path:
    v = os.environ.get("ENSEMBLE_CACHE_DIR")
    return Path(v).resolve() if v else settings.data_dir / "cache"


def duck_extension_dir() -> str | None:
    """P10b: where DuckDB finds its extensions (httpfs) offline. Bundle them there in the image; nothing is downloaded."""
    return os.environ.get("ENSEMBLE_DUCK_EXTENSION_DIR") or None


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
FRAME_LOAD_PEAK = 2.5  # P11 measured: frames.read peaks at 2.49-2.55 x the frame (R12's 1.6 was pd.read_parquet alone)
#: §5.9 step 3: the holdout is never sampled; it is scored in batches of this many rows.
HOLDOUT_BATCH_ROWS = int(_env_num("ENSEMBLE_HOLDOUT_BATCH_ROWS", 500_000, 1000))
#: §5.9 step 2: a class with at most this many training rows is kept whole by the stratified sample.
SAMPLE_MIN_CLASS_ROWS = 1000

#: P11 calibration: peak RSS of a whole run in the worker (python -m app.engine.worker and its children: read, AutoGluon
#: 1.6.3 fit, holdout scoring, ladder, importance) against the estimated training frame, by preset. Measured on 27-column
#: tables (5 text-like) at 1M and 5M rows, 4 vCPU (docs/progress/P11.md, docs/plans/B-scale-performance.md "P11"):
#:   medium_quality  1M: estimate 539 MB → peak 3,436 MB (6.37 x);  5M: 2,697 MB → 8,669 MB (3.21 x)
#: so peak ≈ base_mb + per_frame_byte × estimate (a fixed ~2.1 GB of libraries and models plus 2.43 x the frame).
#: A run's default budget is capped so that this peak stays under FIT_RAM_SHARE of the worker's RAM; a preset without a
#: calibration is not capped (only the 10 % share applies).
FIT_PEAK: dict[str, dict[str, float]] = {
    "medium_quality": {"base_mb": 2127.0, "per_frame_byte": 2.43, "measured_1m_x": 6.37, "measured_5m_x": 3.21},
}
FIT_RAM_SHARE = 0.8


#: F-X1 C1: the widest file an upload accepts (400 too_many_columns above it). Bank tables of 1-2k columns are real.
def max_columns() -> int:
    return int(_env_num("ENSEMBLE_MAX_COLUMNS", 2000, 2))


#: F-X1 C2: the Data Doctor's report (and Feature Lab) read a version of at most this many rows whole; a larger one is
#: read as a recorded splitmix64 sample of about this many rows, and Doctor fixes run as SQL (no full pandas read).
def doctor_rows() -> int:
    return int(_env_num("ENSEMBLE_DOCTOR_ROWS", 200_000, 1000))


#: F-X1 C2: DuckDB's memory limit for writing a Doctor / Feature Lab version as SQL (it runs in the API process).
def doctor_write_mb() -> int:
    return int(_env_num("ENSEMBLE_DOCTOR_WRITE_MB", 1024, 256))
