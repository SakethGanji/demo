"""X2: compare two recordings call by call; keys named `storage` are ignored everywhere. Prints per call: same / differs
(with the first differing paths). usage: jdiff.py <before dir> <after dir> [--show N]"""
import json, sys
from pathlib import Path
A, B = Path(sys.argv[1]), Path(sys.argv[2])
show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 4


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if k != "storage"}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


def diff(a, b, p="", out=None):
    out = [] if out is None else out
    if type(a) != type(b):
        out.append((p, a, b))
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{p}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>")))
            else:
                diff(a[k], b[k], f"{p}.{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((f"{p}[len]", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{p}[{i}]", out)
    elif a != b:
        out.append((p, a, b))
    return out


same, differ = 0, {}
for f in sorted(A.glob("*.json")):
    g = B / f.name
    if not g.exists():
        differ[f.stem] = [("(missing after)", None, None)]
        continue
    d = diff(strip(json.loads(f.read_text())), strip(json.loads(g.read_text())))
    if d:
        differ[f.stem] = d
    else:
        same += 1
print(f"identical outside storage: {same} | differ: {len(differ)}")
for k, d in differ.items():
    print(f"  {k}: {len(d)} paths")
    for p, a, b in d[:show]:
        print(f"      {p}: {json.dumps(a, default=str)[:140]}  →  {json.dumps(b, default=str)[:140]}")
