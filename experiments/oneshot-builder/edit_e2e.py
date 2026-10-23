"""An edit, end to end: backend (1 call) -> page (1 call) -> old journey as a regression
check -> new journey (1 call). Same three calls as a new app.

    python edit_e2e.py           # runs EDITS in parallel, results in apps/<app>-edit/

If apps/<app> isn't there yet, it is built first from the original request (backend, page, journey).
"""
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import eval2
from build import build
from journey_build import build_journey, run_journey
from ui_build import build_ui

HERE = Path(__file__).parent
EDITS = [  # (app, its original request, the change)
    ("vendor-payments", eval2.MULTI[0],
     "Record how each payment was made: ACH, wire or check (required). Show it in the payments list."),
    ("audit-program", eval2.MULTI[3],
     "Action items can be extended once: the owner asks for a new due date with a reason, and the auditor approves or declines it."),
]


def ensure_base(app, request, port):
    src = HERE / "apps" / app
    if not (src / "journey.json").exists():
        build(request, src)
        build_ui(src, request, port)
        build_journey(src, request, port)
    return src


def run(app, request, change, port):
    t0, src, d = time.time(), ensure_base(app, request, port), HERE / "apps" / (app + "-edit")
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(src, d, ignore=shutil.ignore_patterns("app.db", "ui", "*.log", "*-failed.png"))
    out = {"app": app, "change": change}
    b = build(change, d, edit_from=src)                      # 1. backend
    out["backend"] = {"pass": b["final_pass"], "first_pass": b["first_pass"], "seconds": b["seconds"]}
    if not b["final_pass"]:
        return out
    u = build_ui(d, request, port, change=change)            # 2. page
    out["page"] = {"pass": u["final_pass"], "first_pass": u["first_pass"], "seconds": u["seconds"],
                   "failed": [c["name"] + ": " + c["error"][:200] for c in u["checks"] if not c["ok"]]}
    if not u["final_pass"]:
        return out
    old = run_journey(d, port)                               # 3. old journey still pass?
    steps = len(json.loads((d / "journey.json").read_text()))
    bad = next((r for r in old if not r["ok"]), None)
    out["old_journey"] = f'{sum(r["ok"] for r in old)}/{steps}' + (f' (stopped at {bad["step"]}: {bad["error"][:200]})' if bad else "")
    j = build_journey(d, request + "\n\nLater change: " + change, port)   # 4. new journey
    out["new_journey"] = {"pass": j["pass"], "steps": f'{j["ran"] if j["pass"] else j["ran"] - 1}/{j["steps"]}',
                          "covers": j["covers"], "failed": j["failed"]}
    out["seconds"] = round(time.time() - t0, 1)
    return out


if __name__ == "__main__":
    with ThreadPoolExecutor(len(EDITS)) as ex:
        results = list(ex.map(lambda a: run(*a[1], 8180 + a[0]), enumerate(EDITS)))
    print(json.dumps(results, indent=1))
