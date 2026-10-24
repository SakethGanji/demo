"""Sets up the isolated :8020 engine for live runs of the 5 AI-built bank workflows.

Project, variables (env dev), API specs pointing at the mock bank on :9100, three real Postgres connections
(ops_db made writable by SQL: the API has no way to), and the workflows exactly as the AI wrote them except:
spec ids remapped to the imported specs, and Wait steps shortened from hours/days to 1 second.
Writes live/setup.json.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
WF = HERE.parent / os.environ.get("WF_DIR", "runs/crazy-live")
APIS = HERE.parent / "crazy" / "apis"
# Same variables the workflows were built against (crazy_cases.VARS; importing it would rewrite the suite).
VARS = {k: {"value": v} for k, v in {
    "UNDERWRITER_EMAIL": "underwriters@bank.test", "HEAD_OF_LENDING_EMAIL": "head-lending@bank.test",
    "CREDIT_COMMITTEE_EMAIL": "committee@bank.test", "PAYMENTS_OPS_EMAIL": "payops@bank.test", "PAYMENTS_LEAD_EMAIL": "paylead@bank.test",
    "FRAUD_TEAM_EMAIL": "fraud@bank.test", "MLRO_EMAIL": "mlro@bank.test", "TREASURY_EMAIL": "treasury@bank.test",
    "KYC_TEAM_EMAIL": "kyc@bank.test", "RM_EMAIL": "rm@bank.test", "CISO_EMAIL": "ciso@bank.test",
    "COLLECTIONS_OFFICER_EMAIL": "collections@bank.test", "RISK_MANAGER_EMAIL": "risk@bank.test"}.items()}
VARS["BANK_API_KEY"] = {"value": "sk-bank-31337", "secret": True}

E = "http://127.0.0.1:8020/api"
PG = "postgresql://bank:bankdemo@127.0.0.1:5440/{}"
NAMES = ["mortgage_offer", "wire_release", "kyc_document_chase", "access_recertification", "hardship_restructure"]


def call(method, path, body=None):
    req = urllib.request.Request(E + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code} {e.read().decode()[:400]}")


def psql(db, sql):
    subprocess.run(["psql", "-h", "127.0.0.1", "-p", "5440", "-U", "bank", "-d", db, "-qtAc", sql],
                   env={"PGPASSWORD": "bankdemo", "PATH": "/usr/bin:/bin"}, check=True)


proj = call("POST", "/projects", {"name": "Bank live run", "slug": "bank-live"})
pid = proj["id"]

for k, v in VARS.items():
    call("POST", "/variables", {"key": k, "value": v["value"], "type": "secret" if v.get("secret") else "string",
                                "environment": "dev"})

specs = {}
for y in sorted(APIS.glob("*.yaml")):
    text = re.sub(r"(servers:\s*\n-\s*url:\s*)\S+", r"\g<1>http://127.0.0.1:9100", y.read_text())
    specs[y.stem] = call("POST", "/api-specs", {"project_id": pid, "name": y.stem, "spec_text": text})["id"]

conns = {db: call("POST", f"/projects/{pid}/connections", {"name": db, "kind": "postgres", "url": PG.format(db)})["key"]
         for db in ["core_banking", "iam_db", "ops_db"]}
# The API creates every connection read-only; the workflows write ops_db, so flip that one row.
psql("workflows_live", f"""UPDATE "workflow-app".project_connections SET readonly=false WHERE project_id='{pid}' AND key='{conns["ops_db"]}'""")

wfs = {}
for name in NAMES:
    w = json.loads((WF / f"{name}.json").read_text())
    for n in w["nodes"]:
        p = n.get("parameters") or {}
        if isinstance(p.get("api_ref"), dict):
            p["api_ref"]["spec_id"] = specs[p["api_ref"]["spec_id"]]
        if n["type"] == "Wait":
            p.update(unit="seconds", duration=1)
    body = {"name": w["name"], "nodes": w["nodes"], "connections": w["connections"]}
    wfs[name] = call("POST", f"/projects/{pid}/workflows", body)["id"]

out = {"project": pid, "specs": specs, "connections": conns, "workflows": wfs}
(HERE / "setup.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
