"""1,048,575-row sheet: upload → check → commit, timed, with the API's RSS sampled."""
import sys, time, threading, subprocess; sys.path.insert(0, "."); from x3 import *
F = sys.argv[1]; pid = int(sys.argv[2]); peak = [0]; stop = []
def watch():
    while not stop:
        try:
            for l in open(f"/proc/{pid}/status"):
                if l.startswith("VmRSS"): peak[0] = max(peak[0], int(l.split()[1]))
        except Exception: pass
        time.sleep(0.2)
threading.Thread(target=watch, daemon=True).start()
t = time.time(); up, err = upload([("big.xlsx", F)], name="x3 big xlsx", target="target"); print("upload", round(time.time() - t, 1), "s", err)
t = time.time(); s, c = check(up); print("check", s, round(time.time() - t, 1), "s", c.get("detail") if isinstance(c, dict) and "detail" in c else [(x["name"], x["n_rows"], [(y["name"], y["type"]) for y in x["schema"]]) for x in c["tables"]], c.get("warnings") if isinstance(c, dict) else "")
t = time.time(); s, m = commit(up); print("commit", s, round(time.time() - t, 1), "s", m.get("version_id") if isinstance(m, dict) else m, m.get("n_rows") if isinstance(m, dict) else "")
stop.append(1); print("API RSS peak", round(peak[0] / 1024 ** 2, 2), "GB")
if s in (200, 201):
    s, g = get(f"/datasets/{m['version_id']}/grid", limit=2, offset=1048573, table="big"); print("last rows", [{k: v for k, v in x.items() if not k.startswith('_')} for x in g["rows"]])
