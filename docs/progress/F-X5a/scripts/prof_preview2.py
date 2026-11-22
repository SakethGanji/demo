import os, sys, time, json
sys.path.insert(0, os.getcwd())
from app import config
from app.datasets import DatasetStore
from app.db import Database
from app.lake import duck
s = config.load_settings(); db = Database(s.db_path); st = DatasetStore(s, db)
V = sys.argv[1]
orig = duck.job
import contextlib
class W:
    def __init__(s, c): s.c = c
    def execute(s, q, *a):
        t = time.time(); r = s.c.execute(q, *a); dt = time.time() - t
        if dt > 0.05: print(f"  {dt:6.2f}s  {q[:150]!r}")
        return r
    def __getattr__(s, k): return getattr(s.c, k)
@contextlib.contextmanager
def job(**kw):
    with orig(**kw) as c:
        yield W(c)
duck.job = job
import app.datasets as D
ops=[{"op":"set_cells","cells":[{"row_id":7,"column":"amount","value":99.5}]}]
t=time.time(); out = st.edit_version(V, ops, by="x5", reason="x5", preview=True); print("total", round(time.time()-t,1))
print(config.duck_job_mb(), config.duck_job_threads())
