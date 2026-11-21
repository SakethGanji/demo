"""P10: two outside changes, made with file access (not through the app), before the migration:
  data   one value in data.csv of churn v1 (its stored hash no longer matches)
  split  one holdout row moved to fold 0 in the recorded split JSON of scratch v1
usage: tamper.py <state.json> <data dir>"""
import json
import sys
from pathlib import Path

st = json.loads(Path(sys.argv[1]).read_text())
d = Path(sys.argv[2]) / "datasets"
p = d / st["C1"] / "data.csv"
lines = p.read_text().splitlines(keepends=True)
cells = lines[5].rstrip("\n").split(",")
cells[2] = str(round(float(cells[2]) + 1, 2))  # spend of row 4
lines[5] = ",".join(cells) + "\n"
p.write_text("".join(lines))
print(" data:", p.relative_to(d.parent), "row 4 spend changed")
sp = sorted((d / st["T1"] / "splits").glob("*.json"))[0]
j = json.loads(sp.read_text())
h = j["holdout"].pop(0)
j["fold"][h] = 0
sp.write_text(json.dumps(j))
print(" split:", sp.relative_to(d.parent), f"holdout row {h} moved to fold 0")
