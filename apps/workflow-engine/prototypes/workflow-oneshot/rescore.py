"""Re-score saved workflows without calling the model: python rescore.py --crazy DIR [case_id ...]"""
import json, sys
from pathlib import Path
import wf_build as B

args = sys.argv[1:]
d_dir = Path([a for a in args if not a.startswith("--")][0])
ids = [a for a in args if not a.startswith("--")][1:]
for p in sorted(B.EVAL.glob("*.json")):
    case = json.loads(p.read_text())
    f = d_dir / f'{case["id"]}.json'
    if (ids and case["id"] not in ids) or not f.exists():
        continue
    d = json.loads(f.read_text())
    apis = B.load_apis(case["apis"])
    own = B.check(d, apis, case)
    s = B.score(case, d, apis, case)
    print(f'{case["id"]}: own-test fails {len(own)}  struct {[x["name"] for x in s["struct"]]}  hidden {s["hidden"]}')
    for h in s["hidden_fail"]:
        print("   ", h["name"], "--", h["reason"][:300])
    for o in own[:3]:
        print("    own:", str(o)[:300])
