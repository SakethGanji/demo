"""Analyst review of flagged rows (F2): "confirm" (fraud) or "dismiss" (not fraud). The confirmations are labels of the
dataset's rows (a transaction is fraud whichever run flagged it), kept in `anomaly_labels.json` next to data.csv:

    {"rows": {"<row>": {"label": 1 | 0, "by": str, "at": iso, "run_id": str, "s": float | null, "note": str}}}

label 1 = confirmed fraud, 0 = dismissed. Every change also goes to the audit log (action `anomaly_review`).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

FILE = "anomaly_labels.json"
MIN_POSITIVES = 10  # confirmed frauds needed before "Train supervised" is offered
_lock = threading.Lock()


def load(dataset_dir: Path) -> dict[int, dict[str, Any]]:
    p = dataset_dir / FILE
    if not p.exists():
        return {}
    return {int(k): v for k, v in json.loads(p.read_text()).get("rows", {}).items()}


def update(dataset_dir: Path, items: list[dict[str, Any]], *, by: str, run_id: str, at: str,
           scores: dict[int, float]) -> dict[int, dict[str, Any]]:
    """items: [{row, label: 1 | 0 | None, note?}]; None removes the row's label. Returns all labels."""
    with _lock:
        cur = load(dataset_dir)
        for it in items:
            r = int(it["row"])
            if it.get("label") is None:
                cur.pop(r, None)
            else:
                cur[r] = {"label": int(it["label"]), "by": by, "at": at, "run_id": run_id,
                          "s": scores.get(r), "note": it.get("note") or ""}
        p = dataset_dir / FILE
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rows": {str(k): v for k, v in sorted(cur.items())}}, indent=0))
        tmp.replace(p)
        return cur


def summary(cur: dict[int, dict[str, Any]], min_positives: int = MIN_POSITIVES) -> dict[str, Any]:
    pos = sum(1 for v in cur.values() if v["label"] == 1)
    neg = sum(1 for v in cur.values() if v["label"] == 0)
    return {"confirmed": pos, "dismissed": neg, "reviewed": pos + neg, "min_positives": min_positives,
            "can_train": pos >= min_positives,
            "needed": max(0, min_positives - pos)}
