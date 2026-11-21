"""X2: outside changes made with file (and DB) access before the migration, on a copy of the populated data dir.
  a  data        churn v1 (C1, has a run): one cell of data.csv
  b  split       scratch v1 (T1, DL4-recorded): one holdout row moved to fold 0 in its per-target split JSON
  c  split + DB  accounts v3 (A3, no runs): both split JSONs changed AND split_files.sha256 updated to match (content not)
  d  split + DB  accounts v2 (A2, runs rA2/rA4 recorded the hash): same as c
  e  pre-DL4     loans v1 (L1, rL1 recorded no split hash, no split_files row): one holdout row moved to fold 0
  f  data + DB   loans relabel v5 (LR, no runs): one cell of data.csv AND datasets.sha256/legacy_sha256 updated
usage: tamper.py <state.json> <data dir> [which ...]"""
import hashlib, json, sqlite3, sys
from pathlib import Path

st = json.loads(Path(sys.argv[1]).read_text()); D = Path(sys.argv[2]); which = sys.argv[3:] or list("abcdef")
ds = D / "datasets"; con = sqlite3.connect(D / "ensemble.sqlite3")
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def cell(vid, row, col, f):
    p = ds / vid / "data.csv"; lines = p.read_text().splitlines(keepends=True)
    cells = lines[row + 1].rstrip("\n").split(","); cells[col] = f(cells[col]); lines[row + 1] = ",".join(cells) + "\n"
    p.write_text("".join(lines)); return p


def move_holdout(p):
    j = json.loads(p.read_text()); h = j["holdout"].pop(0); j["fold"][h] = 0; p.write_text(json.dumps(j)); return h


if "a" in which:
    p = cell(st["C1"], 4, 2, lambda v: str(round(float(v) + 1, 2))); print(" a data:", p.relative_to(D))
if "b" in which:
    p = sorted((ds / st["T1"] / "splits").glob("*.json"))[0]; print(" b split:", p.relative_to(D), "holdout row", move_holdout(p), "→ fold 0")
for k, v in (("c", "A3"), ("d", "A2")):
    if k in which:
        for p in [*(ds / st[v] / "splits").glob("*.json"), ds / st[v] / "splits.json"]:
            h = move_holdout(p); rel = f"splits/{p.name}" if p.parent.name == "splits" else p.name
            n = con.execute("UPDATE split_files SET sha256 = ? WHERE dataset_id = ? AND file = ?", (sha(p), st[v], rel)).rowcount
            print(f" {k} split+DB: {p.relative_to(D)} holdout row {h} → fold 0; split_files rows updated {n}")
if "e" in which:
    p = sorted((ds / st["L1"] / "splits").glob("*.json"))[0]; print(" e pre-DL4 split:", p.relative_to(D), "holdout row", move_holdout(p), "→ fold 0")
if "f" in which:
    p = cell(st["LR"], 7, 3, lambda v: str(round(float(v or 0) + 5, 2)))
    n = con.execute("UPDATE datasets SET sha256 = ?, legacy_sha256 = ? WHERE id = ?", (sha(p), sha(p), st["LR"])).rowcount
    print(" f data+DB:", p.relative_to(D), "datasets rows updated", n)
con.commit()
