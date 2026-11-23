"""Dashboard numbers computed in Python from a few table reads. Tables: runs(status string*, finished_at datetime),
run_results(run_id integer*, case_key string*, status string*, duration_ms integer)
Rules: read each table ONCE, index by id in a dict, never one table call per row; every figure has a clear denominator."""
from collections import Counter, defaultdict
from studio import api
import studio


@api.get("/stats")
def stats(req):
    """Totals, pass rate, per-day trend and the flakiest cases.
    Returns {totals: {runs, results}, pass_rate, trend: [{date, passed, failed}], flaky: [{case_key, runs, pass_rate}]}"""
    runs = studio.db.table("runs").all()
    results = studio.db.table("run_results").all()
    by_status = Counter(r["status"] for r in results)
    decided = by_status["passed"] + by_status["failed"]            # blocked/error/cancelled are not "failed"
    per_case = defaultdict(list)
    for r in results:
        per_case[r["case_key"]].append(r["status"])
    flaky = []
    for key, sts in per_case.items():
        seen = set(sts) & {"passed", "failed"}
        if len(sts) >= 3 and seen == {"passed", "failed"}:           # both outcomes in its history = flaky
            flaky.append({"case_key": key, "runs": len(sts), "pass_rate": round(sts.count("passed") / len(sts), 3)})
    day = defaultdict(lambda: {"passed": 0, "failed": 0})
    finished = {r["id"]: str(r.get("finished_at") or r["created_at"])[:10] for r in runs}
    for r in results:
        if r["status"] in ("passed", "failed"):
            day[finished.get(r["run_id"], "unknown")][r["status"]] += 1
    return {"totals": {"runs": len(runs), "results": len(results)},
            "pass_rate": round(by_status["passed"] / decided, 3) if decided else None,
            "trend": [{"date": d, **v} for d, v in sorted(day.items())],
            "flaky": sorted(flaky, key=lambda f: f["pass_rate"])[:10]}
