# BACKEND-PROGRESS — implementation log for BACKEND-DESIGN.md phases 1–9

Branch `revert-to-5684ba5` (local commits only, never pushed).

## How to run tests
```bash
cd apps/workflow-engine
venv/bin/python -m pytest tests -q                 # unit (28) + integration
venv/bin/python -m pytest tests/integration -q -k phase3
KEEP_TEST_DB=1 venv/bin/python -m pytest tests/integration -x
```
Harness (`tests/integration/conftest.py`): creates `workflows_test_<hex>` on localhost:5433
(override with WORKFLOW_DB_HOST/PORT/USER/PASSWORD), applies migrations with the real migrator,
starts `uvicorn src.main:app` on a free port (SEED=0), drops the DB afterwards. Refuses the
`workflows` DB and port 8000. Engine logs: `tests/integration/.logs/`. Never run two pytest
processes at once (each makes its own DB, so it is safe, but slow + noisy).

## Status
| Step | Status | Tests (total) | Commit |
|---|---|---|---|
| 0 harness + characterization | done | 51 (28 unit + 23 char) | see git log |
