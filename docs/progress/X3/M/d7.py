"""A 10k-row delta (update/delete/insert) on the 1,048,575-row table: check + commit time, API RSS peak, verify."""
import sys, time, threading; sys.path.insert(0, "."); from x3 import *
V = sys.argv[1]; pid = int(sys.argv[2]); peak = [0]; stop = []
def watch():
    while not stop:
        try:
            for l in open(f"/proc/{pid}/status"):
                if l.startswith("VmRSS"): peak[0] = max(peak[0], int(l.split()[1]))
        except Exception: pass
        time.sleep(0.2)
threading.Thread(target=watch, daemon=True).start()
t = time.time(); s, k = put(f"/datasets/{V}/tables/big/key", {"columns": ["id"]}); print("key", s, round(time.time() - t, 1), "s"); V = k["version_id"]
lines = ["id,acct,amount,opened,target,_op"]
for i in range(0, 1_000_000, 300): lines.append(f"{i},{i:07d},1.5,2021-01-01,1,U")
for i in range(5, 1_000_000, 1000): lines.append(f"{i},x,0,2021-01-01,0,D")
for i in range(2_000_000, 2_003_000): lines.append(f"{i},{i},2.5,2022-01-01,0,I")
t = time.time(); up, err = upload([("big.csv", ("\n".join(lines) + "\n").encode())], parent=V, plan={"table": "big", "tables": {"big": {"action": "delta"}}})
s, c = check(up); print("check", s, round(time.time() - t, 1), "s", {k_: c["tables"][0]["delta"].get(k_) for k_ in ("mode", "inserted", "updated", "deleted", "rows_after")} if s == 200 else c)
t = time.time(); s, m = commit(up); print("commit", s, round(time.time() - t, 1), "s", m.get("version_id") if isinstance(m, dict) else m)
stop.append(1); print("API RSS peak", round(peak[0] / 1024 ** 2, 2), "GB")
t = time.time(); s, v = get(f"/datasets/{m['version_id']}/verify"); print("verify", s, v.get("ok"), round(time.time() - t, 1), "s")
s, n = get(f"/datasets/{m['version_id']}/rows/count", table="big"); print("rows", n)
