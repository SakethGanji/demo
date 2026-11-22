"""F-X4a: the coverage gate. 1) the real app: 0 undeclared routes (every method, GETs included); 2) the same app with
one extra GET that has neither require() nor public(): listed as uncovered, and enforce (or
ENSEMBLE_AUTHZ_STRICT_COVERAGE=1 in any mode) refuses to start.  Run from backend/: python gate.py"""
import logging
import os
import sys
import tempfile

logging.disable(logging.CRITICAL)
os.environ.setdefault("ENSEMBLE_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.getcwd())
from app.authz.coverage import coverage  # noqa: E402
from app.main import create_app  # noqa: E402

app = create_app()
c = coverage(app)
by = {}
for r in c["routes"]:
    by.setdefault(r["method"], [0, 0])
    by[r["method"]][0] += 1
    by[r["method"]][1] += bool(r["covered"])
print(f"1. app: routes {c['n_routes']} | must {c['n_must']} | covered {c['n_covered']} | uncovered {c['n_uncovered']}")
print("   per method (routes, declared):", by, "| public():", sum(1 for r in c["routes"] if r.get("public")))
print("   rule:", c["rule"])

from fastapi import APIRouter  # noqa: E402

rt = APIRouter(prefix="/api")


@rt.get("/x/undeclared/{dataset_id}")
def undeclared(dataset_id: str) -> dict:
    return {}


for m, strict in (("audit", "1"), ("enforce", "0")):
    os.environ["ENSEMBLE_AUTHZ"], os.environ["ENSEMBLE_AUTHZ_ALLOW_TYPED"] = m, "1"
    os.environ["ENSEMBLE_AUTHZ_STRICT_COVERAGE"] = strict
    import app.main as M
    from app.authz import api as A

    orig = A.install

    def install(a, _orig=orig):
        a.include_router(rt)
        _orig(a)

    A.install = install
    M.authz_api.install = install
    try:
        a2 = M.create_app()
        print(f"2. mode {m}, strict {strict}: started (unexpected); uncovered {coverage(a2)['n_uncovered']}")
    except RuntimeError as e:
        print(f"2. mode {m}, STRICT_COVERAGE={strict}: refused to start: {str(e)[:160]}")
    finally:
        A.install = orig
        M.authz_api.install = orig
os.environ["ENSEMBLE_AUTHZ"], os.environ["ENSEMBLE_AUTHZ_STRICT_COVERAGE"] = "audit", "0"
app.include_router(rt)
c = coverage(app)
print(f"3. audit mode, not strict: starts, and the gate check lists it: uncovered {c['n_uncovered']} {[u['method'] + ' ' + u['path'] for u in c['uncovered']]}")
