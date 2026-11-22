"""X4: error echoes and odd corners of masking. A tagged column's value must never come back in a 4xx body (§6.3). As Mo
(Credit Risk member: no unmasked reads) and Vic (viewer).

    python echo.py <base> <state.json>"""
from x4lib import B, R, c, csv, get, h, load, post, show

st = load()
v1 = st["v1"]
print(f"# mode {st['mode']}")

print("\n# 1. a delta whose key is a tagged column (email), with duplicate keys")
dup = [dict(r) for r in R[:5]] + [dict(R[0], income=1)]
u = c.post(B + "/uploads", params={"filename": "loans.csv", "parent_version_id": v1},
           files={"file": ("loans.csv", csv(dup), "text/csv")}, headers=h("Mo")).json().get("upload_id")
post("Mo", f"/uploads/{u}", None)
c.patch(f"{B}/uploads/{u}", json={"tables": {"loans": {"action": "delta", "key": ["email"]}}, "by": "Mo"}, headers=h("Mo"))
show("1a Mo POST /uploads/{u}/check (delta keyed on email, one email twice)", post("Mo", f"/uploads/{u}/check"), None, cut=500)
show("1b Mo POST /uploads/{u}/commit", post("Mo", f"/uploads/{u}/commit", {"reason": "delta", "by": "Mo"}), None, cut=500)

print("\n# 2. a delta keyed on ssn with an op column holding an SSN")
d2 = [dict(R[1], op="U"), dict(R[2], op=str(R[3]["ssn"]))]
u = c.post(B + "/uploads", params={"filename": "loans.csv", "parent_version_id": v1},
           files={"file": ("loans.csv", csv(d2), "text/csv")}, headers=h("Mo")).json().get("upload_id")
c.patch(f"{B}/uploads/{u}", json={"tables": {"loans": {"action": "delta", "key": ["ssn"], "op_column": "op"}}, "by": "Mo"}, headers=h("Mo"))
show("2a Mo POST /uploads/{u}/check", post("Mo", f"/uploads/{u}/check"), None, cut=500)

print("\n# 3. rows filter on a tagged column (Vic)")
show("3a Vic GET /datasets/v1/rows?filter={ssn eq <an SSN>}", get("Vic", f"/datasets/{v1}/rows", params={
    "by": "Vic", "filter": '{"column": "ssn", "cmp": "eq", "value": %d}' % R[7]["ssn"]}), ["rows", "matching", "detail"], cut=300)
show("3b Vic GET /datasets/v1/rows/count?filter={applicant_name eq 'Person0007 Example'}", get("Vic", f"/datasets/{v1}/rows/count", params={
    "by": "Vic", "filter": '{"column": "applicant_name", "cmp": "eq", "value": "Person0007 Example"}'}), None, cut=300)
show("3c Vic GET /datasets/v1/rows/count?filter={ssn gt 500000000}", get("Vic", f"/datasets/{v1}/rows/count", params={
    "by": "Vic", "filter": '{"column": "ssn", "cmp": "gt", "value": 500000000}'}), None, cut=300)

print("\n# 4. Mo edit preview: replace_values on ssn / cast applicant_name")
show("4a Mo POST edit preview {replace_values ssn find <SSN>}", post("Mo", f"/datasets/{v1}/edit", {
    "ops": [{"op": "replace_values", "column": "ssn", "find": R[9]["ssn"], "replace": 1, "match": "exact"}], "preview": True, "by": "Mo"}),
    None, cut=600)
show("4b Mo POST edit preview {cast applicant_name → number}", post("Mo", f"/datasets/{v1}/edit", {
    "ops": [{"op": "cast_column", "column": "applicant_name", "to": "number"}], "preview": True, "by": "Mo"}), None, cut=600)
show("4c Mo POST edit preview {filter_rows dob eq '1957-08-08'} ", post("Mo", f"/datasets/{v1}/edit", {
    "ops": [{"op": "filter_rows", "where": {"column": "dob", "cmp": "eq", "value": R[7]["dob"]}}], "preview": True, "by": "Mo"}),
    None, cut=600)

print("\n# 5. diff between v1 and an edited version (Vic)")
e = post("Mo", f"/datasets/{v1}/edit", {"ops": [{"op": "set_cells", "cells": [{"row_id": 3, "column": "income", "value": 1}]}],
                                       "reason": "fix one income", "by": "Mo"}).json()
v2 = e.get("id") or e.get("version_id")
show("5a Vic GET /datasets/{v1}/diff/{v2}", get("Vic", f"/datasets/{v1}/diff/{v2}", params={"by": "Vic"}), None, cut=400)
