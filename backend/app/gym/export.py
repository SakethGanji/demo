"""`GET /api/runs/{id}/export.py`: a standalone script that reproduces a run with plain AutoGluon.

The script carries everything the run used: the data file (path and SHA-256), the exact holdout rows (a zlib +
base64 bitmask of data.csv positions, so no re-splitting can drift), the dropped columns and column types, the
resolved `TabularPredictor(...)` and `.fit(...)` arguments, the post-fit steps (pseudo-labelling, distillation),
and the decision threshold step. It ends by scoring the holdout and comparing its leaderboard with the run's,
model by model, and exits non-zero if they differ (`--no-check` skips that).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from . import pycode

HEADER = '''#!/usr/bin/env python
"""Reproduce ENSEMBLE run {run_id} ("{name}") with plain AutoGluon.

Generated {ts} by ENSEMBLE with AutoGluon {ag}. Nothing here needs ENSEMBLE running{text_note}.

    python {fname} [--data path/to/data.csv] [--out folder] [--no-check]

Steps: load the data (checked against its SHA-256), take out the exact holdout rows the run held out, drop rows
without a label and the dropped columns, cast the column types, fit TabularPredictor with the run's resolved
arguments, run the post-fit steps, set the decision threshold the same way, then score the holdout and compare
the leaderboard with the run's.
"""
'''

#: the script's body (helpers and main) is a template next to this file: its reads belong to the standalone script,
#: not to the app, so the app's one-reader rule (§6.5 grep gate, *.py) does not apply to it
BODY = Path(__file__).with_name("export_body.py.tmpl").read_text().split("\n", 2)[2]


def _pack_rows(rows: list[int], n: int) -> str:
    bits = np.zeros(n, dtype=np.uint8)
    bits[np.asarray(rows, dtype=int)] = 1
    return base64.b64encode(zlib.compress(np.packbits(bits).tobytes(), 9)).decode()


def _has_key(hp: Any, key: str) -> bool:
    """`key` in a hyperparameters dict, flat or keyed by stack level."""
    if not isinstance(hp, dict):
        return False
    return key in hp or any(isinstance(v, dict) and key in v for v in hp.values())


def _wrap(s: str, width: int = 100) -> str:
    return "(\n" + "\n".join(f'    "{s[i:i + width]}"' for i in range(0, len(s), width)) + "\n)"


def build_script(rec: dict[str, Any], *, backend_dir: Path, unlabeled_csv: str | None, ag_version: str | None) -> str:
    cfg = rec["config"]
    res = rec.get("result") or {}
    resolved = res.get("resolved") or {}
    if not resolved:
        raise ValueError("This run has no resolved fit call yet (it has not started fitting).")
    from .. import frames

    ref = frames.resolve(cfg)
    csv_path = str(frames.path_of(ref))
    raw = Path(csv_path).read_bytes()  # hashed, as the fit hashed it
    n_rows = frames.n_rows(ref)
    text_columns = [c.name for c in frames.columns(ref) if c.kind == "text"]
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    fk = {k: v for k, v in (resolved.get("fit_kwargs") or {}).items() if k not in ("callbacks", "unlabeled_data")}
    fm = fk.pop("feature_metadata", None)
    pk = {k: v for k, v in (resolved.get("predictor_kwargs") or {}).items() if v is not None}
    hp = fk.get("hyperparameters")
    needs_text = isinstance(hp, dict) and any(k in ("TFIDF_LR", "TFIDF_SVM") for k in hp)
    needs_mono = isinstance(hp, dict) and any(str(k).endswith("_MONO") for k in hp)  # F8: monotone subclasses
    needs_sc = _has_key(hp, "SCORECARD")  # F11: the scorecard challenger
    cm = resolved.get("cost_metric")
    cost = {k: cm[k] for k in ("kind", "c_fp", "c_fn", "g_tp", "classes", "matrix") if k in cm} if cm else None
    thr = res.get("threshold") or None
    thr_out = {"source": thr.get("source"), "metric": thr.get("metric"), "value": thr.get("value")} if thr else None
    fthr = res.get("fit_threshold", thr) or None
    fthr_out = {"source": fthr.get("source"), "metric": fthr.get("metric"), "value": fthr.get("value")} if fthr else None
    post = {p["call"]: p["kwargs"] for p in resolved.get("post_fit") or []}
    pl = cfg.get("pseudolabel")
    pseudo = {"method": pl.get("method", "fit_pseudolabel"), "max_iter": int(pl.get("max_iter", 3))} if pl else None
    distill = post.get("distill")
    lb = {r["model"]: [r.get("score_val"), r.get("score_test")] for r in res.get("leaderboard") or [] if not r.get("custom")}
    fname = f"export_{rec['id']}.py"
    head = HEADER.format(run_id=rec["id"], name=rec["name"].replace('"', "'"), ts=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                         ag=ag_version or "?", fname=fname,
                         text_note=" (its TF-IDF models need ENSEMBLE's `ensemble_models` package on the path)" if needs_text else
                         " (its monotone-constrained GBM_MONO / CAT_MONO / XGB_MONO models need ENSEMBLE's `ensemble_models` "
                         "package on the path)" if needs_mono else
                         " (its SCORECARD model needs ENSEMBLE's `ensemble_models` package on the path)" if needs_sc else "")
    lines = [
        head,
        "from __future__ import annotations\n",
        "import argparse\nimport base64\nimport hashlib\nimport json\nimport os\nimport sys\nimport zlib\nfrom pathlib import Path\n",
        "import numpy as np\nimport pandas as pd\nfrom autogluon.tabular import TabularPredictor\n",
        f"RUN_ID = {pycode.py(rec['id'])}",
        f"DATA_CSV = {pycode.py(csv_path)}",
        f"DATA_SHA256 = {pycode.py(hashlib.sha256(raw).hexdigest())}",
        f"N_ROWS = {n_rows}",
        f"LABEL = {pycode.py(cfg['label'])}",
        f"DROP_COLUMNS = {pycode.py(cfg.get('drop_columns') or [])}",
        f"COLUMN_TYPES = {pycode.py(cfg.get('column_types') or {})}",
        "# the version's text columns (schema.json): read as text, so codes with leading zeros keep them",
        f"TEXT_COLUMNS = {pycode.py(text_columns)}",
        f"# {len(splits['holdout'])} holdout rows (seed {splits.get('seed')}, "
        f"{'stratified' if splits.get('stratified') else 'random'}, share {splits.get('holdout_share')})",
        f"HOLDOUT_ROWS = {_wrap(_pack_rows(splits['holdout'], n_rows))}",
        "",
        f"PREDICTOR_KWARGS = {pycode.py(pk)}",
        f"FIT_KWARGS = {pycode.py(fk)}",
        f"FEATURE_METADATA = {pycode.py(fm)}",
        f"PSEUDOLABEL = {pycode.py(pseudo)}",
        f"UNLABELED_CSV = {pycode.py(unlabeled_csv)}",
        f"DISTILL = {pycode.py(distill)}",
        f"THRESHOLD = {pycode.py(thr_out)}",
        f"FIT_THRESHOLD = {pycode.py(fthr_out)}",
        "# F7: the business cost the run optimised (None: a standard metric). Multiclass matrices are in AutoGluon's",
        "# class order (the sorted labels): matrix[i][j] = cost of deciding class j when the truth is class i.",
        f"COST = {pycode.py(cost)}",
        f"NEEDS_ENSEMBLE_MODELS = {pycode.py(needs_text or needs_mono or needs_sc or cost is not None)}",
        f"ENSEMBLE_BACKEND = {pycode.py(str(backend_dir))}",
        "",
        "# The run's leaderboard: model → (validation score, holdout score), AutoGluon's higher-is-better scale.",
        f"RUN_LEADERBOARD = {pycode.py(lb)}",
    ]
    return "\n".join(lines) + BODY
