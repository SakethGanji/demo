"""P20 verification chain: manual API calls chained as the UI makes them, default mode (audit). Usage:
python chain.py [base]   (API on :8000, ENSEMBLE_ADMINS=Ada, fresh data dir).

Every response is scanned for the raw values of the PII columns (`leaks`): the count printed after each call is the
number of raw ssn / email / name / zip / ref_code / dob values found in its body (0 = nothing leaked)."""
import io
import json
import re
import sys
import time
import zipfile

import httpx

B = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
c = httpx.Client(timeout=600)
OUT: dict = {}


def h(who):
    return {"X-Ensemble-Actor": who} if who else {}


# -- the data: 600 applicants with direct identifiers, quasi-identifiers and ordinary features -----------------------
N = 600
rows = []
for i in range(N):
    rows.append({"applicant_name": f"Person{i:04d} Example", "ssn": 100000007 + i * 7919, "email": f"user{i:04d}@example.com",
                 "zip": f"{(i * 37) % 90000:05d}", "dob": f"{1950 + i % 50}-{1 + i % 12:02d}-{1 + i % 28:02d}",
                 "ref_code": f"{100 + i % 800:03d}-{10 + i % 89:02d}-{1000 + i:04d}", "income": 30000 + (i * 37) % 50000,
                 "age": 20 + i % 50, "segment": ["retail", "sme", "corporate"][i % 3],
                 "y": int((30000 + (i * 37) % 50000) < 52000 and (20 + i % 50) < 50 or i % 7 == 0)})
cols = list(rows[0])
CSV = ",".join(cols) + "\n" + "".join(",".join(str(r[c]) for c in cols) + "\n" for r in rows)
RAW = {c: {str(r[c]) for r in rows} for c in ("applicant_name", "ssn", "email", "zip", "ref_code", "dob")}
RAW["ssn"] |= {f"{r['ssn']:,}" for r in rows}  # as reports format numbers (slice levels: "ssn < 101,403,254")
_rx = re.compile("|".join(sorted((re.escape(v) for vs in RAW.values() for v in vs), key=len, reverse=True)))


def leaks(text: str) -> int:
    """Raw PII values in a body (a zip / ssn inside a longer number does not count)."""
    return sum(1 for m in _rx.finditer(text) if not (m.start() and text[m.start() - 1].isdigit())
               and not (m.end() < len(text) and text[m.end()].isdigit()))


def show(n, r, keys=None, cut=700):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:200]
    body = r.text if r.headers.get("content-type", "").startswith(("application/json", "text")) else ""
    shown = {k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j
    hdr = f" | X-Ensemble-Authz: {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    print(f"## {n} → HTTP {r.status_code}{hdr} | raw PII values in the body: {leaks(body) if body else 'n/a'}")
    print("   ", json.dumps(shown, default=str, ensure_ascii=False)[:cut])
    return j


# 1. the team: Ada (admin) makes it, Ana owner, Ben viewer; Ana's project
show("1 Ada POST /teams {Credit Risk}", c.post(f"{B}/teams", json={"name": "Credit Risk", "by": "Ada"}, headers=h("Ada")), ["id", "slug"])
show("1b Ada adds Ana (owner)", c.post(f"{B}/teams/credit-risk/members", json={"name": "Ana", "role": "owner", "by": "Ada"}, headers=h("Ada")), ["slug"])
show("1c Ana adds Ben (viewer)", c.post(f"{B}/teams/credit-risk/members", json={"name": "Ben", "role": "viewer", "by": "Ana"}, headers=h("Ana")), ["slug"])
p = show("1d Ana POST /projects", c.post(f"{B}/projects", json={"name": "PD applicants", "team_id": "credit-risk", "by": "Ana"}, headers=h("Ana")), ["id", "team_id"])

# 2. the upload: stage → check (PII suggestions) → commit
up = show("2 Ana POST /uploads?project_id=…&filename=applicants.csv (raw body)",
          c.post(f"{B}/uploads", params={"project_id": p["id"], "filename": "applicants.csv", "by": "Ana", "target": "y", "name": "applicants"},
                 content=CSV.encode(), headers={**h("Ana"), "Content-Type": "text/csv"}), ["upload_id", "status"])
up["id"] = up["upload_id"]
chk = c.post(f"{B}/uploads/{up['id']}/check", headers=h("Ana")).json()
print("## 2b POST /uploads/{u}/check → pii per table:")
for s in chk["tables"][0].get("pii") or []:
    print("    ", s)
cm = show("2c POST /uploads/{u}/commit", c.post(f"{B}/uploads/{up['id']}/commit", json={"by": "Ana"}, headers=h("Ana")), ["version_id", "id", "created"])
ds = cm.get("version_id") or cm.get("id")
print("    dataset", ds)

# 3. the tags: suggested, masked until confirmed
t = show("3 Ben GET /datasets/{id}/pii", c.get(f"{B}/datasets/{ds}/pii", headers=h("Ben")), ["masked_columns", "pending_review"])
for x in t["tags"]:
    print("    ", {k: x[k] for k in ("column", "tag", "mask", "source", "set_by")})

# 4. Ben's rows: masked
r = show("4 Ben GET /datasets/{id}/rows?limit=3", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 3}, headers=h("Ben")), ["masked_columns", "masks", "unmasked"])
for x in r["rows"]:
    print("    ", {k: x[k] for k in ("_row_id", "applicant_name", "ssn", "email", "zip", "dob", "ref_code", "income")})
g = show("4b Ben GET /datasets/{id}/grid?limit=2", c.get(f"{B}/datasets/{ds}/grid", params={"limit": 2}, headers=h("Ben")), ["masked_columns"])
print("     columns:", [(x["name"], x["type"], x.get("mask")) for x in g["columns"]])
show("4c Ben GET …/rows?q=user0007 (a masked column is not searched)", c.get(f"{B}/datasets/{ds}/rows", params={"q": "user0007"}, headers=h("Ben")),
     ["searched_columns", "rows"])
show("4d Ben GET …/rows?value=<a raw ssn>&column=ssn (lookup on the masked view)",
     c.get(f"{B}/datasets/{ds}/rows", params={"value": str(rows[5]["ssn"]), "column": "ssn"}, headers=h("Ben")), ["n", "rows"])
show("4e Ben POST …/rows/preview {filter: zip eq <raw zip>} (compares masked values)",
     c.post(f"{B}/datasets/{ds}/rows/preview", json={"filter": {"column": "zip", "cmp": "eq", "value": rows[5]["zip"]}, "by": "Ben"}, headers=h("Ben")),
     ["matching", "masked_columns"])

# 5. unmasked reads: a reason is required, the decision is data.read_unmasked, the read is audited
show("5 Ana GET …/rows?unmasked=1 (no reason)", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 2, "unmasked": 1}, headers=h("Ana")))
r = show("5b Ana GET …/rows?unmasked=1&reason=QA&limit=2", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 2, "unmasked": 1, "reason": "QA"}, headers=h("Ana")),
         ["masked_columns", "unmasked"])
for x in r["rows"]:
    print("    ", {k: x[k] for k in ("_row_id", "applicant_name", "ssn", "email", "zip")})
show("5c Ana GET …/rows?unmasked=1&reason=QA&value=<raw ssn>&column=ssn", c.get(f"{B}/datasets/{ds}/rows",
     params={"value": str(rows[5]["ssn"]), "column": "ssn", "unmasked": 1, "reason": "QA"}, headers=h("Ana")), ["n", "unmasked"])
r = show("5d Ben GET …/rows?unmasked=1&reason=QA (viewer: not allowed)", c.get(f"{B}/datasets/{ds}/rows", params={"limit": 1, "unmasked": 1, "reason": "QA"}, headers=h("Ben")),
         ["masked_columns", "unmasked", "unmasked_refused"])
print("     ssn shown:", r["rows"][0]["ssn"])
a = c.get(f"{B}/teams/credit-risk/audit", params={"action": "dataset_rows_read"}, headers=h("Ana")).json()
print(f"## 5e Ana GET /teams/credit-risk/audit?action=dataset_rows_read → {len(a['entries'])} rows")
for e in a["entries"]:
    d = e["details"] if isinstance(e["details"], dict) else json.loads(e["details"])
    print("    ", e["id"], e["actor"], e["principal_id"], e["auth"], e["request_id"], {k: d.get(k) for k in ("read", "reason", "columns", "action")},
          e["row_hash"][:12])
a = c.get(f"{B}/teams/credit-risk/audit", params={"action": "authz_would_deny"}, headers=h("Ana")).json()
print("## 5f …?action=authz_would_deny →", [(e["actor"], (e["decision"] or {}).get("action") if isinstance(e["decision"], dict)
                                            else json.loads(e["decision"] or "{}").get("action")) for e in a["entries"]])

# 6. stats rendering
st = c.get(f"{B}/datasets/{ds}/stats", params={"wait": 30}, headers=h("Ben"))
j = show("6 Ben GET …/stats?wait=30", st, ["status", "masked_columns"])
for col in j["columns"]:
    if col["name"] in ("ssn", "zip", "email", "dob", "income"):
        print("    ", col["name"], {k: col.get(k) for k in ("masked", "mask", "min", "max", "mean", "histogram", "distinct")},
              "top:", (col.get("top") or [])[:3])
dd = c.get(f"{B}/datasets/{ds}", headers=h("Ben")).json()
print("## 6b GET /datasets/{id} profile → raw PII values:", leaks(json.dumps(dd)), "|",
      [(x["name"], x.get("example"), x.get("masked")) for x in dd["profile"]["columns"] if x["name"] in ("ssn", "email", "zip", "dob", "income")])

# 7. export: masked by default; unmasked with data.export_unmasked + a reason
for who, params, n in (("Ana", {}, "7 Ana export (masked by default)"), ("Ana", {"unmasked": 1, "reason": "regulator file"}, "7b Ana export unmasked"),
                       ("Ben", {"unmasked": 1, "reason": "QA"}, "7c Ben export unmasked (viewer)")):
    r = c.get(f"{B}/datasets/{ds}/export", params={"format": "zip", "by": who, **params}, headers=h(who))
    z = zipfile.ZipFile(io.BytesIO(r.content))
    data = z.read("data.csv").decode()
    info = json.loads(z.read("export.json"))
    sch = json.loads(z.read("schema.json"))
    import hashlib
    print(f"## {n} → HTTP {r.status_code}" + (f" | X-Ensemble-Authz: {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else "")
          + f" | X-Ensemble-Masked: {r.headers.get('x-ensemble-masked')} | files {z.namelist()}")
    print("     data.csv line 2:", data.splitlines()[1])
    print("     raw PII values in data.csv:", leaks(data), "| sha256(data.csv) == export.json csv_sha256:",
          hashlib.sha256(data.encode()).hexdigest() == info["csv_sha256"], "| manifest.json table sha256 == export.json table_sha256_stored:",
          json.loads(z.read("manifest.json"))["data"]["tables"][0]["table_sha256"] == info["table_sha256_stored"] if "manifest.json" in z.namelist() else None)
    print("     schema.json masked:", sch.get("masked_columns"), "| export.json masked:", (info.get("masked") or {}).get("masked_columns"),
          "| unmasked:", info.get("unmasked"))

# 8. edit → v2, then the diff: samples, changed cells, the edit log masked
e = show("8 Ana POST …/edit (set ssn of row 3, delete row 7, add a row)", c.post(f"{B}/datasets/{ds}/edit", json={"ops": [
    {"op": "set_cells", "cells": [{"row_id": 3, "column": "ssn", "value": 999888777}, {"row_id": 4, "column": "income", "value": 41000}]},
    {"op": "delete_rows", "row_ids": [7]},
    {"op": "add_rows", "rows": [{**{k: v for k, v in rows[11].items()}, "ssn": 111222333, "email": "new.person@example.com", "applicant_name": "New Person"}]}],
    "reason": "correct an applicant's SSN", "by": "Ana"}, headers=h("Ana")), ["id", "created"], cut=400)
v2 = e["id"]
RAW["ssn"] |= {"999888777", "111222333", "999,888,777", "111,222,333"}
RAW["email"].add("new.person@example.com")
RAW["applicant_name"].add("New Person")
_rx = re.compile("|".join(sorted((re.escape(v) for vs in RAW.values() for v in vs), key=len, reverse=True)))
print("     edit block raw PII values:", leaks(json.dumps(e.get("edit"))))
d = show("8b Ben GET /datasets/diff?a=v1&b=v2", c.get(f"{B}/datasets/diff", params={"a": ds, "b": v2}, headers=h("Ben")), ["method", "masked_columns"], cut=300)
print("     rows:", {k: d["rows"][k] for k in ("changed", "added", "removed", "changed_by_column")})
print("     changed_examples:", d["rows"]["changed_examples"])
print("     samples.added:", [{k: x.get(k) for k in ("_row_id", "applicant_name", "ssn", "email", "zip")} for x in d["samples"]["added"]])
print("     samples.removed:", [{k: x.get(k) for k in ("_row_id", "applicant_name", "ssn", "email", "zip")} for x in d["samples"]["removed"]])
print("     edit log cells:", [x for op in (d.get("edit") or {}).get("ops") or [] for x in (op.get("examples") or op.get("cells_examples") or [])][:3])
print("     columns zip / ssn:", [{k: x.get(k) for k in ("column", "masked", "top_a", "mean_a")} for x in d["columns"] if x["column"] in ("zip", "ssn")])
show("8c Ben GET /datasets/diff?a=v1&b=v2&key=zip (key_invalid: the repeated keys masked)",
     c.get(f"{B}/datasets/diff", params={"a": ds, "b": v2, "key": "zip"}, headers=h("Ben")))
d = show("8d Ana GET /datasets/diff …&unmasked=1&reason=QA", c.get(f"{B}/datasets/diff", params={"a": ds, "b": v2, "unmasked": 1, "reason": "QA"},
                                                                  headers=h("Ana")), ["masked_columns"], cut=200)
print("     changed_examples:", d["rows"]["changed_examples"])
show("8e Ben GET /datasets/{v2}/history", c.get(f"{B}/datasets/{v2}/history", headers=h("Ben")), ["root_id"], cut=100)

# 9. errors never echo a tagged value
show("9 Ana POST …/edit set_cells ssn = '987-65-4321' (BIGINT column)", c.post(f"{B}/datasets/{v2}/edit", json={"ops": [
    {"op": "set_cells", "cells": [{"row_id": 3, "column": "ssn", "value": "987-65-4321"}]}], "reason": "typo fix", "by": "Ana"}, headers=h("Ana")))
show("9b the same on an untagged column (income = 'abc-12-xyz'): the value is shown", c.post(f"{B}/datasets/{v2}/edit", json={"ops": [
    {"op": "set_cells", "cells": [{"row_id": 3, "column": "income", "value": "abc-12-xyz"}]}], "reason": "typo fix", "by": "Ana"}, headers=h("Ana")))
show("9c Ana POST …/edit filter_rows ssn gt 1e30 (out of range)", c.post(f"{B}/datasets/{v2}/edit", json={"ops": [
    {"op": "filter_rows", "where": {"column": "ssn", "cmp": "gt", "value": 1e30}}], "reason": "typo fix", "by": "Ana"}, headers=h("Ana")))

# 10. a maintainer reviews the tags
show("10 Ben PUT …/pii {confirm suggested} (viewer)", c.put(f"{B}/datasets/{ds}/pii", json={"confirm": "suggested", "by": "Ben"}, headers=h("Ben")),
     ["changed"], cut=200)
t = show("10b Ana PUT …/pii {zip: pii_quasi hash, segment: none}", c.put(f"{B}/datasets/{ds}/pii", json={"columns": [
    {"column": "zip", "tag": "pii_quasi", "mask": "hash"}, {"column": "segment", "tag": "none"}], "by": "Ana"}, headers=h("Ana")), ["changed"], cut=500)
t = show("10c Ana GET /datasets/{v2}/pii (tags belong to the root: v2 has them)", c.get(f"{B}/datasets/{v2}/pii", headers=h("Ana")),
         ["masked_columns", "pending_review"])
r = c.get(f"{B}/datasets/{v2}/rows", params={"limit": 3}, headers=h("Ben")).json()
print("## 10d Ben GET /datasets/{v2}/rows → zip hashed:", [x["zip"] for x in r["rows"]], "| raw zips", [rows[i]["zip"] for i in range(3)])

OUT.update(ds=ds, v2=v2)
json.dump(OUT, open(__import__("os").environ.get("P20_IDS", "p20_ids.json"), "w"))
print("ids", OUT)
