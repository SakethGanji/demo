"""F-X2: X2's hook_api.py (X2_SLOW, X2_CRASH_AFTER_COMMIT, X2_CRASH_BEFORE_COMMIT, X2_SLEEP_AFTER_RUNS) plus
  FX2_NO_LOCK=1   the per-version migration lock and the writers' holds turned off (shows the commit's re-check alone)
usage (cwd = <code>/backend): ENSEMBLE_DATA_DIR=… PORT=… python fx2_hook.py"""
import contextlib
import os
import runpy
import sys

sys.path.insert(0, os.getcwd())
import app.lake.migrate as m  # noqa: E402

if os.environ.get("FX2_NO_LOCK") and hasattr(m, "csv_write_hold"):
    m.csv_write_hold = lambda db, vid, what: contextlib.nullcontext()
    m._take_lock = lambda db, vid, job_id: None
    print("FX2 HOOK: migration lock and writer holds off", flush=True)
runpy.run_path(os.path.join(os.environ["X2_SCRIPTS"], "hook_api.py"), run_name="__main__")
