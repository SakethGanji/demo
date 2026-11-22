"""X5 (lane x4): after a purge, scan a whole work dir (data dir + the API's HOME + import dir + anything else the API
wrote there) for the 600 loans SSNs, as decimal text and int64/float64 bytes (F-X4c purge.py's matchers).

    python scanwork.py <work dir> [<extra dir> …]"""
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "X4"))
from x4lib import R  # noqa: E402

SSN = [r["ssn"] for r in R]
SSN_SET = {str(s).encode() for s in SSN} | {f"{s:,}".encode() for s in SSN}
_I, _F = np.array(SSN, dtype="<i8"), np.array(SSN, dtype="<f8")


def hits(b):
    t = {m for m in re.findall(rb"(?<!\d)(?:\d{9}|\d{3},\d{3},\d{3})(?!\d)", b) if m in SSN_SET}
    out = set()
    for off in range(8):
        n = (len(b) - off) // 8
        if n > 0:
            a = np.frombuffer(b, dtype="<i8", count=n, offset=off)
            out |= set(a[np.isin(a, _I)].tolist())
            f = np.frombuffer(b, dtype="<f8", count=n, offset=off)
            out |= {int(x) for x in f[np.isin(f, _F)].tolist()}
    return len(t), len(out)


n = 0
for d in sys.argv[1:]:
    for root, _, files in os.walk(d):
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):
                continue
            try:
                b = open(p, "rb").read()
            except OSError:
                continue
            n += 1
            t, bn = hits(b)
            if t or bn:
                print(f"  HIT {p}: text {t}, binary {bn}")
print(f"scanned {n} files under {sys.argv[1:]}")
