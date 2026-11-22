"""X2: the CSV files the generator uploads (seeded; written as raw text so the edge cases are exact bytes).
Edge cases: leading-zero ids and ZIPs, int-like floats ("3.0"), dates, unicode (accents, CJK, emoji, a comma inside
quotes), empty strings vs NA ("" quoted, an empty cell, the literal NA), ints with missing cells, booleans with missing
cells, a big file with a column pandas reads as mixed int/str objects (low_memory chunks)."""
import csv
import io
import sys
from pathlib import Path

import numpy as np

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
NAMES = ["José", "Zoë", "李雷", "Ærøskøbing", "O'Brien", "Smith, Jr.", "🙂 happy", "Müller", "naïve café", "Ольга"]


def write(fn, header, rows, quote_empty_cols=()):
    b = io.StringIO()
    b.write(",".join(header) + "\n")
    for r in rows:
        cells = []
        for h, v in zip(header, r):
            if v is None:
                cells.append("")              # an empty cell (NA)
            elif v == "" and h in quote_empty_cols:
                cells.append('""')            # a quoted empty string
            else:
                s = str(v)
                cells.append('"' + s.replace('"', '""') + '"' if any(ch in s for ch in ',"\n') else s)
        b.write(",".join(cells) + "\n")
    (out / fn).write_text(b.getvalue(), encoding="utf-8")


def loans(n, start, seed):
    r = np.random.default_rng(seed)
    rows = []
    for i in range(start, start + n):
        inc = round(float(r.normal(52000, 15000)), 2)
        rate = f"{int(r.integers(2, 9))}.0" if r.random() < 0.8 else f"{r.integers(2, 9)}.5"   # int-like floats
        late = None if r.random() < 0.08 else int(r.poisson(1.2))
        note = [None, "", "NA", "n/a", "ok", "late payer"][int(r.integers(0, 6))]
        flag = None if r.random() < 0.05 else bool(r.random() < 0.6)
        d = f"2024-{int(r.integers(1, 13)):02d}-{int(r.integers(1, 29)):02d}"
        z = 0.00004 * (inc - 52000) - 0.5 * (late or 0) + (0.3 if flag else 0)
        y = int(z + r.normal(0, 1) > 0)
        rows.append([f"L{i:05d}", f"{int(r.integers(500, 99000)):05d}", f"{int(r.integers(1, 10**6)):08d}", inc if r.random() > 0.04 else None,
                     rate, late, NAMES[int(r.integers(0, len(NAMES)))], note, flag, d, int(r.integers(21, 75)), y])
    return rows


H = ["loan_id", "zip", "acct_no", "income", "rate", "n_late", "name", "note", "flag", "opened", "age", "y"]
L = loans(600, 1, 1)
write("loans.csv", H, L, quote_empty_cols=("note",))
rng = np.random.default_rng(3)
L3 = [r for i, r in enumerate(L) if not 100 <= i < 115]
for r in L3[:12]:
    r[3] = (r[3] or 0) + 1000
L3 = L3 + loans(25, 601, 7)
L3 = [L3[i] for i in rng.permutation(len(L3))]           # shuffled: the row map is not increasing
write("loans_v3.csv", H, L3, quote_empty_cols=("note",))

r = np.random.default_rng(20)
write("churn.csv", ["cust", "tenure", "spend", "plan", "churn"],
      [[f"C{i:04d}", int(r.integers(1, 60)), round(float(r.gamma(2, 50)), 2), ["basic", "plus", "pro"][int(r.integers(0, 3))],
        int(r.random() < 0.25)] for i in range(400)])

r = np.random.default_rng(30)
HA = ["acct", "zip", "balance", "tenure", "active", "region", "since", "owner", "memo", "y"]
A = []
for i in range(1, 801):
    A.append([f"{i:08d}", f"{int(r.integers(500, 99000)):05d}", None if r.random() < 0.06 else round(float(r.lognormal(8, 1)), 2),
              None if r.random() < 0.05 else int(r.integers(0, 120)), None if r.random() < 0.03 else bool(r.random() < 0.8),
              ["north", "south", "east", "west"][int(r.integers(0, 4))], f"20{int(r.integers(10, 24)):02d}-0{int(r.integers(1, 10))}-15",
              NAMES[int(r.integers(0, len(NAMES)))], [None, "", "NA", "-", "vip"][int(r.integers(0, 5))], int(r.random() < 0.3)])
write("accounts.csv", HA, A, quote_empty_cols=("memo",))
A2 = [list(x) for x in A[50:]]
for x in A2[:10]:
    x[5] = "central"
write("accounts_next.csv", HA, A2, quote_empty_cols=("memo",))

r = np.random.default_rng(40)
write("scratch.csv", ["k", "x1", "x2", "y"], [[f"K{i}", round(float(r.normal()), 4), int(r.integers(0, 9)), int(r.random() < 0.4)] for i in range(300)])

# big: 300k rows; 'code' is digits in the first chunk and letters later → pandas (low_memory) reads mixed int/str objects
r = np.random.default_rng(50)
n = 300_000
codes = [str(int(v)) for v in r.integers(100, 999, n)]
for i in range(250_000, n, 7):
    codes[i] = "X" + codes[i]
write("big.csv", ["id", "code", "v", "y"], [[i, codes[i], round(float(x), 3), int(x > 0)] for i, x in enumerate(r.normal(size=n))])
print("files in", out)

# fl: y is a ratio of two columns (trees approximate it badly), so Feature Lab keeps ratio(a, b)
r = np.random.default_rng(60)
n = 1500
a = r.lognormal(3, 1, n); b = r.lognormal(3, 1, n)
y = ((a / b) * np.exp(r.normal(0, 0.15, n)) > 1).astype(int)
write("fl.csv", ["rid", "a", "b", "noise", "y"], [[f"R{i:06d}", round(float(a[i]), 3), round(float(b[i]), 3), round(float(r.normal()), 3), int(y[i])] for i in range(n)])
# fl2: the ratio helps but alone stays under the leak bar (AUC < 0.9)
r = np.random.default_rng(61)
n = 2000
a = r.lognormal(3, 1, n); b = r.lognormal(3, 1, n); cc = r.normal(size=n)
y = (np.log(a / b) + 0.9 * cc + r.normal(0, 0.6, n) > 0).astype(int)
write("fl2.csv", ["rid", "a", "b", "c", "y"], [[f"R{i:06d}", round(float(a[i]), 3), round(float(b[i]), 3), round(float(cc[i]), 3), int(y[i])] for i in range(n)])
