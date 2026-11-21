"""P10: diff the before/after JSON files: every path that differs, except those under a key named `storage`.
usage: jdiff.py <before dir> <after dir>"""
import json
import sys
from pathlib import Path


def walk(a, b, path, out):
    if path and path[-1] == "storage":
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k == "storage":
                continue
            if k not in a or k not in b:
                out.append(f"{'/'.join(map(str, path + [k]))}: only {'after' if k not in a else 'before'}")
            else:
                walk(a[k], b[k], path + [k], out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{'/'.join(map(str, path))}: {len(a)} items before, {len(b)} after")
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, path + [i], out)
    elif a != b:
        out.append(f"{'/'.join(map(str, path))}: {str(a)[:90]!r} → {str(b)[:90]!r}")


bdir, adir = Path(sys.argv[1]), Path(sys.argv[2])
total = 0
for f in sorted(bdir.glob("*.json")):
    g = adir / f.name
    if not g.exists():
        print(f"{f.name}: missing after")
        continue
    out = []
    walk(json.loads(f.read_text()), json.loads(g.read_text()), [], out)
    total += len(out)
    print(f"{f.name:34s} {'identical (except storage)' if not out else str(len(out)) + ' differences'}")
    for o in out[:12]:
        print("    ", o)
print("differences outside `storage`:", total)
