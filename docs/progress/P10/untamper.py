"""P10: undo the outside change to churn v1's data.csv the way an admin restores it from a backup (the original bytes);
checked against the hash stored when the version was made. usage: untamper.py <state.json> <data dir>"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

st = json.loads(Path(sys.argv[1]).read_text())
data = Path(sys.argv[2])
p = data / "datasets" / st["C1"] / "data.csv"
lines = p.read_text().splitlines(keepends=True)
cells = lines[5].rstrip("\n").split(",")
cells[2] = str(round(float(cells[2]) - 1, 2))
lines[5] = ",".join(cells) + "\n"
raw = "".join(lines).encode()
stored = sqlite3.connect(data / "ensemble.sqlite3").execute("SELECT legacy_sha256 FROM datasets WHERE id = ?", (st["C1"],)).fetchone()[0]
ok = hashlib.sha256(raw).hexdigest() == stored
print(" restored", p.relative_to(data), "| matches the stored hash:", ok)
if ok:
    p.write_bytes(raw)
