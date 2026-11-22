"""P21: the dataset-delete decide path refuses the requester and lookalikes (a fresh dataset nothing depends on).
python delete_check.py <base>"""
import sys

import httpx

B = sys.argv[1] + "/api"
c = httpx.Client(timeout=120)
ANA = ["Ana", "A.n.a", "Anà", "Аna", "ANA", "A n a", "An​a", "Ａna"]


def h(w):
    return {"X-Ensemble-Actor": w} if w.isascii() else {}


csv = b"k,x,y\n" + b"".join(f"{i},{i * 3 % 17},{i % 2}\n".encode() for i in range(200))
u = c.post(B + "/uploads", params={"filename": "tmp.csv"}, files={"file": ("tmp.csv", csv, "text/csv")}, headers=h("Ana")).json()["upload_id"]
c.post(f"{B}/uploads/{u}/check", headers=h("Ana"))
v = c.post(f"{B}/uploads/{u}/commit", json={"by": "Ana", "reason": "scratch"}, headers=h("Ana")).json()["version_id"]
r = c.post(f"{B}/datasets/{v}/delete", json={"by": "Ana", "reason": "P21 four eyes"}, headers=h("Ana"))
print("request delete →", r.status_code, r.json().get("id"), r.json().get("status"))
rid = r.json()["id"]
print("spoofs:", " ".join(f"{s!r}→{c.post(f'{B}/dataset-requests/{rid}/decide', json={'approve': True, 'by': s}, headers=h(s)).status_code}" for s in ANA))
print("last:", c.post(f"{B}/dataset-requests/{rid}/decide", json={"approve": True, "by": "A.n.a"}, headers=h("A.n.a")).json()["detail"][:160])
r = c.post(f"{B}/dataset-requests/{rid}/decide", json={"approve": True, "by": "Ben", "comment": "ok"}, headers=h("Ben"))
print("Ben approves →", r.status_code, r.json().get("status"), r.json().get("decided_by"))
