import json, collections, sys
for f in sys.argv[1:]:
    c = collections.Counter(); ex = {}
    for l in open(f):
        d = json.loads(l)
        if d.get("kind") in ("5xx", "hang"):
            k = (d["kind"], d["method"], d["route"], d.get("status"))
            c[k] += 1; ex.setdefault(k, d.get("variant"))
    print("==", f, sum(c.values()))
    for k, v in c.most_common(): print(f"  {v:5d} {k} e.g. {str(ex[k])[:70]}")
