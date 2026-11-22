"""X5: Feature Lab SQL write at 10M (F-JOBS §4 recipe): edit job adds scaled2, then FL with a ratio candidate, apply."""
import sys,os,time,json;sys.path.insert(0,os.path.dirname(__file__))
from lib import *
PID=int(sys.argv[1]); V=sys.argv[2]
with Peak(PID,"edit add_column (job)"):
    st,r=c("POST",f"/api/datasets/{V}/edit",{"by":"x5","reason":"x5 fl signal","ops":[{"op":"add_column","name":"scaled2","formula":"income * (1 + y + tenure_months / 120)"}]})
    print(" ",st,str(r)[:160])
    j=poll_job(r["job_id"]) if st==202 else {"result":r}
res=j.get("result") or {}; V2=res.get("id"); print("  edited",V2,res.get("default"), (j.get("error") or "")[:200])
with Peak(PID,"feature-lab ratio + apply (job)"):
    st,f=c("POST",f"/api/datasets/{V2}/feature-lab",{"by":"x5","candidates":[{"op":"ratio","a":"scaled2","b":"income"}],"time_limit":60,"apply":True})
    print(" ",st,str(f)[:200])
    while True:
        st,x=c("GET",f"/api/datasets/{V2}/feature-lab/{f['id']}")
        if x.get("status") in ("done","failed","succeeded","cancelled"): break
        time.sleep(3)
r=x.get("result") or {}
print("  FL",x.get("status"),"kept",[k.get("name") for k in r.get("kept") or []],"version",x.get("version") or r.get("version") or r.get("dataset"),"err",str(x.get("error"))[:300])
print("  keys", list(x), list(r)[:30])
