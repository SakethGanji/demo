#!/usr/bin/env python3
"""D1: download the four public datasets of the round-4 plan into backend/data/realdata/ (not committed) and write
each as a plain comma-separated CSV ready to upload.

    backend/.venv/bin/python -I scripts/realdata_d1.py [--upload http://127.0.0.1:8000]

UCI's host has an expired certificate, so the plan's mirrors are used. With --upload, each CSV is uploaded to the API
(target named) and its dataset id printed, so the Data Doctor can be read on it.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent / "backend" / "data" / "realdata"
SOURCES = {
    "taiwan": ("https://huggingface.co/datasets/scikit-learn/credit-card-clients/resolve/main/UCI_Credit_Card.csv", "taiwan.csv"),
    "bank": ("https://raw.githubusercontent.com/selva86/datasets/master/bank-full.csv", "bank.csv"),
    "german": ("https://api.openml.org/data/v1/download/31", "german.arff"),
    "banking77": ("https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/train.csv", "banking77.csv"),
}
# name → (csv written, target)
OUT = {"taiwan": ("taiwan_default.csv", "default.payment.next.month"), "bank": ("bank_marketing.csv", "y"),
       "german": ("german_credit.csv", "class"), "banking77": ("banking77.csv", "category")}


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - fixed public URLs
        dest.write_bytes(r.read())


def read_arff(path: Path) -> pd.DataFrame:
    """A small ARFF reader (nominal attributes kept as strings): enough for OpenML's German credit."""
    names, rows, data = [], [], False
    for line in path.read_text(errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        if data:
            rows.append(s)
        elif s.lower().startswith("@attribute"):
            names.append(s.split(None, 2)[1].strip("'\""))
        elif s.lower().startswith("@data"):
            data = True
    df = pd.read_csv(io.StringIO("\n".join(rows)), header=None, names=names, quotechar="'", skipinitialspace=True)
    return df


def prepare() -> dict[str, Path]:
    ROOT.mkdir(parents=True, exist_ok=True)
    raw = ROOT / "raw"
    raw.mkdir(exist_ok=True)
    out = {}
    for name, (url, fn) in SOURCES.items():
        fetch(url, raw / fn)
        dest = ROOT / OUT[name][0]
        if not dest.exists():
            if name == "bank":
                df = pd.read_csv(raw / fn, sep=";")
            elif name == "german":
                df = read_arff(raw / fn)
            else:
                df = pd.read_csv(raw / fn)
            df.to_csv(dest, index=False)
        out[name] = dest
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", default=None, help="API base URL to upload the CSVs to")
    args = ap.parse_args()
    files = prepare()
    ids = {}
    for name, path in files.items():
        df = pd.read_csv(path)
        print(f"{name:10s} {path.name:22s} {len(df):>6,} rows × {df.shape[1]} columns · y = {OUT[name][1]}")
        if args.upload:
            import httpx

            r = httpx.post(f"{args.upload.rstrip('/')}/api/datasets", files={"file": (path.name, path.read_bytes(), "text/csv")},
                           data={"target": OUT[name][1]}, timeout=300)
            r.raise_for_status()
            ids[name] = r.json()["id"]
            print(f"  uploaded → {ids[name]}")
    if ids:
        (ROOT / "uploaded.json").write_text(json.dumps(ids, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
