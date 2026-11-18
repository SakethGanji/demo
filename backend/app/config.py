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


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("ENSEMBLE_DATA_DIR", BACKEND_DIR / "data")).resolve()
    return Settings(data_dir=data_dir)
