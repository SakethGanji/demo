"""Replay of the DLA4 #1 value cases against POST /api/datasets/{id}/edit. Usage: python dla4_replay.py <dataset_id>"""
import json, sys, urllib.request

DS = sys.argv[1] if len(sys.argv) > 1 else "credit-default"
URL = f"http://localhost:8000/api/datasets/{DS}/edit"
BIG = [1e30, 1e308, 2**63, 2**64]
NEG = [-1e30, -1e308, -(2**63) - 1, -(2**64)]
STR = ["1e30", "1e308", str(2**63), str(2**64)]
LEAK = ("pandas", "numpy", "Traceback", "safely", "dtype", "Int64", "int64", "float64", "Error:", "Exception", "astype")

def edit(ops, preview=False):
    body = {"ops": ops, "reason": "DLA4 replay", "by": "Ana", "preview": preview}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Ensemble-Actor": "Ana"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

cases = []
for v in BIG + NEG:
    cases.append((f"set_cells credit_score={v!r}", [{"op": "set_cells", "cells": [{"row": 0, "column": "credit_score", "value": v}]}], False))
for v in STR:
    cases.append((f"set_cells age={v!r} (text)", [{"op": "set_cells", "cells": [{"row": 1, "column": "age", "value": v}]}], False))
for v in BIG:
    cases.append((f"set_cells credit_score={v!r} preview", [{"op": "set_cells", "cells": [{"row": 2, "column": "credit_score", "value": v}]}], True))
for v in BIG:
    cases.append((f"add_rows credit_score={v!r}", [{"op": "add_rows", "rows": [{"credit_score": v, "age": 40}]}], False))
for v in [1.797e308, -1.797e308, "1.797e308", 1.7976931348623157e308]:
    cases.append((f"add_column value={v!r}", [{"op": "add_column", "name": "big", "value": v}], False))
cases.append(("add_column value=1.797e308 preview", [{"op": "add_column", "name": "big", "value": 1.797e308}], True))
for f in ["credit_score * 1e308", "exp(credit_score)", "income_usd * 1e306 * 1e3"]:
    cases.append((f"add_column formula {f}", [{"op": "add_column", "name": "big", "formula": f}], False))
extra = []
for v in BIG:
    extra.append((f"fill_missing credit_score constant {v!r}", [{"op": "fill_missing", "column": "credit_score", "strategy": "constant", "value": v}], False))
for v in BIG:
    extra.append((f"replace_values credit_score 724 -> {v!r}", [{"op": "replace_values", "column": "credit_score", "find": 724, "replace": v}], False))

def run(title, cs):
    print(f"## {title} ({len(cs)})")
    bad = 0
    for name, ops, pv in cs:
        st, body = edit(ops, pv)
        try:
            d = json.loads(body).get("detail")
        except Exception:
            d = body[:120]
        code = d.get("code") if isinstance(d, dict) else None
        leak = [w for w in LEAK if w in body]
        ok = st == 400 and code and not leak
        bad += not ok
        msg = d.get("message") if isinstance(d, dict) else d
        print(f"{'ok ' if ok else 'BAD'} {st} {code or '-':20} {name:48} {'LEAK ' + ','.join(leak) if leak else ''} | {str(msg)[:150]}")
    print(f"=> {len(cs) - bad}/{len(cs)} as required\n")
    return bad

b = run("DLA4 #1 cases", cases) + run("fill_missing / replace_values with the same values", extra)
sys.exit(1 if b else 0)
