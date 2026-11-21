"""Two worker processes × 4 threads claim the same queue at once: every job is leased exactly once (Postgres: FOR UPDATE
SKIP LOCKED after the per-resource advisory lock; SQLite: BEGIN IMMEDIATE). Run from backend/ with ENSEMBLE_DB_URL set
(Postgres) or not (SQLite, ENSEMBLE_DATA_DIR). Usage: python queue_race.py <n_jobs>."""
import json, os, subprocess, sys, threading, time
from pathlib import Path

sys.path.insert(0, os.getcwd())
from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.jobs import queue as Q  # noqa: E402

db = Database(config.load_settings().db_path)
if sys.argv[1] == "worker":
    got, lock = [], threading.Lock()
    wid = f"p23probe-{os.getpid()}"

    def loop():
        while True:
            j = Q.claim(db, wid, "light", cap=10_000, kinds=("p23probe",))
            if j is None:
                return
            Q.finish(db, j["id"], wid, "succeeded", result={"by": wid})
            with lock:
                got.append(j["id"])

    ts = [threading.Thread(target=loop) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print(json.dumps(got))
    sys.exit(0)

n = int(sys.argv[1])
ids = [Q.enqueue(db, "p23probe", {"i": i}, resource="light", by="p23")["id"] for i in range(n)]
t0 = time.time()
ps = [subprocess.Popen([sys.executable, __file__, "worker"], stdout=subprocess.PIPE, text=True) for _ in range(2)]
outs = [json.loads(p.communicate()[0]) for p in ps]
dt = time.time() - t0
allc = outs[0] + outs[1]
print(f"dialect {db.dialect}: {n} jobs; worker A leased {len(outs[0])}, worker B leased {len(outs[1])} in {dt:.1f}s")
print("each job leased exactly once:", sorted(allc) == sorted(ids), "| duplicates:", len(allc) - len(set(allc)))
with db.connect() as con:
    rows = con.execute(f"SELECT status, attempts, COUNT(*) FROM jobs WHERE kind = 'p23probe' GROUP BY status, attempts").fetchall()
    print("jobs by (status, attempts):", [tuple(r) for r in rows])
    con.execute("DELETE FROM jobs WHERE kind = 'p23probe'")
