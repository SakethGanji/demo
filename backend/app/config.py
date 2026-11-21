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
