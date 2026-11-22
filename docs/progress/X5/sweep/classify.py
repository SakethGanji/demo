"""Bucket every recorded 5xx of a pass into the distinct causes (by route + input family). python classify.py <jsonl>..."""
import collections, json, re, sys

def cause(r):
    v, rt, m = r["variant"], r["route"], r["method"]
    if "sync-s3" in rt:
        return "S1 sync-s3 SystemExit (server exits)"
    if v.startswith("rawval:") and not v.endswith("7890") or (v.startswith("raw:") and ("NaN" in v or "1e400" in v)):
        return "G1 422 echo of NaN/Inf/1e400 (pydantic body)" if "max_experiments" not in v else "B9 review max_experiments=Infinity"
    if v.startswith("raw:") and ("[[[" in v or '{"a":{' in v):
        return "G2 422 echo of 1000-deep JSON (RecursionError)"
    if v.startswith("mp:") and m in ("PATCH", "PUT") or (v in ("mp:binary", "mp:latin1 csv") and not any(k in rt for k in ("anomaly", "/score"))):
        return "G3 422 echo of non-UTF-8 multipart on a JSON route"
    if "MultipartParse" in r.get("resp", "") or (rt == "/api/uploads" and "multipart" in v):
        return "B8 raw multipart w/o valid boundary to /uploads"
    if rt.endswith("/export"):
        return "B1 export: unknown/odd id (FileNotFound/ENAMETOOLONG/FrameError) or non-latin-1 name"
    if rt.endswith("/pii") or (rt.endswith("/rows/preview") and "table" in v):
        return "B2 table selector: unknown table / non-text (PageError, unhashable, SQLite overflow)"
    if rt.endswith("/splits") and "target" in v:
        return "B3 splits target non-text"
    if re.search(r"body:by|rawval:by|\"by\":1", v) or '"by":"Ada","by":1' in v:
        return "K1 non-text by (known)"
    if "seed" in rt:
        return "B4 lake/seed project_id non-text (SQLite overflow/binding/FK)"
    if "path:" in v and "AAAA" in v:
        return "B5 10 KB path id -> ENAMETOOLONG (file-backed stores)"
    if "notes" in rt or "cursor" in v or "events" in rt:
        return "B6 2**64 int into SQLite (note_id, audit cursor, events from)"
    if "authz/check" in rt or "changes" in rt:
        return "B7 non-text action/kind (unhashable)"
    if any(k in rt for k in ("anomaly", "/score")) and v.startswith("mp:"):
        return "B10 scoring upload of a non-CSV / malformed file"
    return f"Z other {m} {rt} {v[:40]}"

tot = collections.Counter(); routes = collections.defaultdict(set)
for f in sys.argv[1:]:
    for line in open(f):
        r = json.loads(line)
        if r.get("kind") != "5xx":
            continue
        c = cause(r); tot[c] += 1; routes[c].add(f"{r['method']} {r['route']}")
for c, n in sorted(tot.items()):
    print(f"{n:6d}  {c}  [{len(routes[c])} routes]" + (f": {sorted(routes[c])[:6]}" if len(routes[c]) < 7 else ""))
print(sum(tot.values()), "total")
