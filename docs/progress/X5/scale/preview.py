import sys,os;sys.path.insert(0,'.')
from lib import *
body={"by":"x5","reason":"x5","preview":True,"ops":[{"op":"set_cells","cells":[{"row_id":7,"column":"amount","value":99.5}]}]}
with Peak(int(sys.argv[1]),"edit preview"):
    st,r=c("POST",f"/api/datasets/{sys.argv[2]}/edit",body)
print(st,str(r)[:300])
