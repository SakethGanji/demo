"""F-X4a: add an explicit require()/public() to every route that had neither (one-off edit script, kept for the record).
Each entry: (file, decorator path literal, dependency source). Only the decorator line and the authz import change."""
import re
import sys
from pathlib import Path

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/app")
DS, RUN, MV, BATCH = 'path("dataset_id", "dataset")', 'path("run_id", "run")', 'path("vid", "model_version")', 'path("bid", "batch")'
LIST = 'public("list: rows filtered to visible_teams() in enforce mode")'
R = lambda a, o="": f'require("{a}"{", " + o if o else ""})'  # noqa: E731
M = {
    "lake/api.py": {"/admin/lake/selfcheck": 'public("re-hashes fixed test vectors in code; no data")',
                    "/admin/lake/status": R("admin.lake"),
                    "/datasets/{dataset_id}/manifest": R("data.read_meta", DS),
                    "/datasets/{dataset_id}/verify": R("data.verify", DS)},
    "main.py": {"/api/health": 'public("liveness")', "/api/datasets": LIST,
                "/api/datasets/{dataset_id}/splits": R("data.read_meta", DS),
                "/api/datasets/{dataset_id}/split-options": R("data.preview", DS),
                "/api/datasets/{dataset_id}/intake": R("data.preview", DS),
                "/api/admin/reset-requests": R("admin.reset")},
    "engine/api.py": {"/models": 'public("the model-family catalogue; no data")', "/runs": LIST,
                      "/status": 'public("machine status bar: active run ids only for visible teams")',
                      **{p: R("run.read", RUN) for p in ("/runs/{run_id}", "/runs/{run_id}/split-comparison", "/runs/{run_id}/events",
                         "/runs/{run_id}/monotone", "/runs/{run_id}/scorecard", "/runs/{run_id}/ladder",
                         "/runs/{run_id}/feature-importance", "/runs/{run_id}/threshold", "/runs/{run_id}/cost",
                         "/runs/{run_id}/cost-comparison", "/runs/{run_id}/fairness")}},
    "catalog/datasets_api.py": {"/data/search": LIST, "/datasets/{dataset_id}/history": R("data.read_meta", DS),
                                "/datasets/{dataset_id}/usage": R("data.read_meta", DS),
                                "/uploads/{up}": R("data.upload", 'path("up", "upload")')},
    "catalog/api.py": {"/projects": LIST, "/projects/{project_id}": R("data.read_meta", 'path("project_id", "project")'),
                       "/lineage/{kind}/{node_id}": R("data.read_meta", 'path("node_id", "node")'),
                       "/runs/{run_id}/children": R("run.read", RUN),
                       "/annotations/{kind}/{node_id}": R("data.read_meta", 'path("node_id", "node")')},
    "catalog/teams_api.py": {"/me": 'public("the caller\'s own principal and teams")',
                             "/teams": 'public("team directory: names and counts; members need team.read")',
                             "/teams/{team}": R("team.read", 'path("team", "team")'),
                             "/teams/{team}/members": R("team.read", 'path("team", "team")'),
                             "/principals": 'public("people directory for the member picker")',
                             "/principals/{principal}": 'public("people directory: one principal and its team names")'},
    "catalog/certify.py": {"/datasets/{dataset_id}/certifications": R("data.read_meta", DS),
                           "/certifications/{cid}": R("data.read_meta", 'path("cid", "certification")')},
    "catalog/shares.py": {"/shares": 'public("list: filtered to shares from or to the caller\'s teams in enforce mode")',
                          "/shares/{sid}": R("data.read_meta", 'path("sid", "share")')},
    "jobs/api.py": {"/jobs": 'public("list: filtered to the caller\'s visible teams in enforce mode")',
                    "/jobs/{job_id}": R("data.read_meta", 'path("job_id", "job")'), "/workers": R("admin.config")},
    "engine/learning_curve_api.py": {"/runs/{run_id}/learning-curve": R("run.read", RUN)},
    "engine/active_learning_api.py": {"/runs/{run_id}/active-learning": R("run.read", RUN)},
    "engine/annotations_api.py": {"/runs/{run_id}/annotations": R("run.read", RUN)},
    "engine/shift_api.py": {"/runs/{run_id}/shift": R("run.read", RUN), "/runs/{run_id}/shift/{key}": R("run.read", RUN),
                            "/registry/batches/{bid}/shift": R("model.read", BATCH)},
    "engine/slices_api.py": {"/runs/{run_id}/slices": R("run.read", RUN)},
    "forecast/api.py": {"/options": 'public("forecast presets and metrics; no data")', "/mapping/{dataset_id}": R("data.preview", DS),
                        "/runs/{run_id}/chart": R("run.read", RUN)},
    "anomaly/api.py": {"/mapping/{dataset_id}": R("data.preview", DS), "/runs/{run_id}/labels": R("run.read", RUN),
                       "/runs/{run_id}/supervised": R("run.read", RUN)},
    "registry/api.py": {"/runs/{run_id}/latency": R("run.read", RUN), "/registry": LIST, "/registry/summary": LIST,
                        "/registry/versions": LIST, "/registry/serving": R("admin.config"),
                        "/registry/batches/{bid}": R("model.read", BATCH),
                        **{p: R("model.read", MV) for p in ("/registry/versions/{vid}", "/registry/versions/{vid}/mlflow",
                           "/registry/versions/{vid}/retire", "/registry/versions/{vid}/card", "/registry/versions/{vid}/monotone",
                           "/registry/versions/{vid}/batches", "/registry/versions/{vid}/reasons", "/registry/versions/{vid}/recourse")}},
    "registry/monitoring_api.py": {"/registry/versions/{vid}/monitoring": R("model.read", MV),
                                   "/registry/versions/{vid}/monitoring/retrain": R("model.read", MV),
                                   "/monitoring/stream": 'public("the bundled loan-stream sample\'s simulation spec")'},
    "registry/changes_api.py": {"/registry/versions/{vid}/changes": R("model.read", MV)},
    "registry/credit_report_api.py": {"/registry/versions/{vid}/credit-report": R("model.read", MV)},
    "fairlending/api.py": {"/runs/{run_id}/fair-lending": R("run.read", RUN)},
    "sqlsource/api.py": {"/demo": 'public("the bundled demo SQLite file\'s tables")', "/datasets/{dataset_id}/versions": R("data.read_meta", DS)},
    "gym/api.py": {"/gym/schema": 'public("the Gym\'s request schema; no data")', "/runs/{run_id}/gym": R("run.read", RUN),
                   "/runs/{run_id}/export.py": R("run.read", RUN)},
    "vela/api.py": {p: R("run.read", RUN) for p in ("/runs/{run_id}/vela", "/runs/{run_id}/doctor", "/runs/{run_id}/experiments",
                                                    "/runs/{run_id}/review", "/runs/{run_id}/review/{review_id}")},
    "autopilot/api.py": {"/autopilot": 'public("list: filtered to the caller\'s visible teams in enforce mode")',
                         "/autopilot/{aid}": R("run.read", 'path("aid", "autopilot")'),
                         "/autopilot/{aid}/inbox": R("run.read", 'path("aid", "autopilot")')},
    "llm_api.py": {"": 'public("LLM route status and latencies; no keys")'},
    "data_doctor_api.py": {"/datasets/{dataset_id}/data-doctor": R("data.preview", DS),
                           "/datasets/{dataset_id}/decision-time": R("data.read_meta", DS)},
    "featurelab_api.py": {"/datasets/{dataset_id}/feature-lab": R("data.read_meta", DS),
                          "/datasets/{dataset_id}/feature-lab/{job_id}": R("data.preview", DS)},
    "authz/api.py": {"/authz/coverage": 'public("the route map and its markers; no data")'},
}
for d in ("el", "segmented", "survival", "multilabel", "reject"):
    M[f"{d}/api.py"] = {"/detect": R("data.preview", 'query("dataset_id", "dataset")'), "/runs/{run_id}": R("run.read", RUN)}

NAMES = ("path", "query", "require", "public")
for f, routes in M.items():
    p = APP / f
    s = p.read_text()
    for route, dep in routes.items():
        rx = re.compile(r'(@\w+\.get\("' + re.escape(route) + r'")(\)|,)')
        hits = rx.findall(s)
        if len(hits) != 1:
            sys.exit(f"{f}: {route!r} matched {len(hits)}")
        s = rx.sub(lambda m: m.group(1) + f", dependencies=[{dep}]" + m.group(2), s)
    used = {n for n in NAMES if re.search(rf"\b{n}\(", "".join(routes.values()))}
    dots = "." * (f.count("/") + 1)
    imp = re.search(rf"^from {re.escape(dots)}authz import ([\w, ]+)(.*)$", s, re.M)
    if imp:
        have = {x.strip() for x in imp.group(1).split(",")}
        s = s[:imp.start()] + f"from {dots}authz import {', '.join(sorted(have | used))}{imp.group(2)}" + s[imp.end():]
    elif f == "authz/api.py":
        pass
    else:  # after the last top-level `from fastapi` import
        m = list(re.finditer(r"^from fastapi[^\n]*\n", s, re.M))[-1]
        s = s[:m.end()] + f"\nfrom {dots}authz import {', '.join(sorted(used))}  # F-X4a\n" + s[m.end():]
    p.write_text(s)
    print(f, len(routes))
