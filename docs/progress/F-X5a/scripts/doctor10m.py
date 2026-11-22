import sys,os;sys.path.insert(0,'/var/tmp/fx5/scale')
from lib import *
for rid in sys.argv[1:]:
    with Peak(int(os.environ["APIPID"]),f"GET /runs/{rid}/doctor"):
        st,r=c("GET",f"/api/runs/{rid}/doctor",timeout=1800)
    print(st, str(r)[:300] if st!=200 else [(x.get('id'),x.get('status')) for x in r.get('checks',[])][:12], str(r.get('error') if isinstance(r,dict) else '')[:200])
