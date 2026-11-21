"""X2: the head API with fault hooks on the migration (env):
  X2_SLOW=<s>                  sleep before each version (widens the window for concurrent work)
  X2_CRASH_AFTER_COMMIT=<id>   os._exit(9) after <id>'s DB commit, before its folder rename (kill -9 at that instant)
  X2_CRASH_BEFORE_COMMIT=<id>  os._exit(9) inside <id>'s migration after its blobs were put, before the transaction
usage (cwd = wt_head/backend): ENSEMBLE_DATA_DIR=… PORT=… python hook_api.py"""
import os, sys, time
sys.path.insert(0, os.getcwd())
import uvicorn
import app.lake.migrate as m

slow = float(os.environ.get("X2_SLOW") or 0)
after, before = os.environ.get("X2_CRASH_AFTER_COMMIT"), os.environ.get("X2_CRASH_BEFORE_COMMIT")
_mv, _ff, _lb, _ro = m.migrate_version, m.finish_folder, m._live_blobs, m._runs_of
# X2_SLEEP_AFTER_RUNS=<id>:<s>  sleep after <id>'s second _runs_of (the listing _migrate_live makes right before its
# transaction): a run created in that window is not in the list the commit backfills
sar = os.environ.get("X2_SLEEP_AFTER_RUNS")
_calls = {}


def runs_of(db, vid):
    out = _ro(db, vid)
    _calls[vid] = _calls.get(vid, 0) + 1
    if sar and vid == sar.split(":")[0] and _calls[vid] == 2:
        print(f"X2 HOOK: {vid} runs listed ({len(out)}); sleeping {sar.split(':')[1]} s before the commit", flush=True)
        time.sleep(float(sar.split(":")[1]))
    return out


def migrate_version(db, settings, store, vid, **kw):
    if slow:
        time.sleep(slow)
    m._X2_CUR = vid
    return _mv(db, settings, store, vid, **kw)


def finish_folder(db, settings, vid):
    if vid == after:
        print(f"X2 HOOK: os._exit(9) after the commit of {vid}, before its folder step", flush=True)
        os._exit(9)
    return _ff(db, settings, vid)


def live_blobs(con, blobs):
    if getattr(m, "_X2_CUR", None) == before:
        print(f"X2 HOOK: os._exit(9) in {before} after its blobs were put, before the commit", flush=True)
        os._exit(9)
    return _lb(con, blobs)


m.migrate_version, m.finish_folder, m._live_blobs, m._runs_of = migrate_version, finish_folder, live_blobs, runs_of
if __name__ == "__main__":
    uvicorn.run("app.main:app", port=int(os.environ.get("PORT", "8712")), log_level="info")
