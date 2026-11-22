import sys; sys.path.insert(0, "."); from x3 import *
VM = json.load(open("Vc.json"))["Vc"]
br = b"branch,city\nB1,X\nB2,Y\nB3,Z\n"
for plan in [{"table": "branches"}, {"tables": {"branches": {"action": "new"}}}, {"table": "branches", "tables": {"branches": {"action": "new"}}}, None]:
    r = chain([("branches.csv", br)], parent=VM, plan=plan)
    print(plan, "→", r["stage"], r.get("check_status"), r.get("commit_status"), str(r.get("check"))[:200] if r.get("check_status") != 200 else [(t["name"], t["action"]) for t in r["check"]["tables"]])
    if r.get("commit_status") in (200, 201): s, d = version(r["commit"]["version_id"]); print("   tables", [(t["name"], t["change"]) for t in d["tables"]])
