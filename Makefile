# One-command workflows for the ENSEMBLE demo. See docs/progress/S1.md.
SHELL := /bin/bash
PYTHON ?= python3
VENV := backend/.venv
PY := $(VENV)/bin/python
API_PORT ?= 8000
UI_PORT ?= 5190

.PHONY: setup lock dev api ui smoke reset test test-full test-backend test-backend-full test-ui e2e clean-data

## Create the Python venv, install the pinned backend deps and the npm packages.
setup:
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	@# requirements.lock is requirements.txt fully resolved (make lock). uv installs it in under a minute;
	@# pip works too but is slower.
	if command -v uv >/dev/null; then uv pip install --python $(PY) --index-strategy unsafe-best-match -r backend/requirements.lock; \
	else $(PY) -m pip install --upgrade pip -q && $(PY) -m pip install -r backend/requirements.lock; fi
	@# The notebook client (clients/python), editable, so the tests use the code in the repo.
	if command -v uv >/dev/null; then uv pip install --python $(PY) -e clients/python; else $(PY) -m pip install -e clients/python; fi
	npm install

## Re-resolve backend/requirements.lock after editing backend/requirements.txt (needs uv).
lock:
	uv pip compile backend/requirements.txt --python-version 3.13 --index-strategy unsafe-best-match \
		--emit-index-url --no-header -o backend/requirements.lock

## The API on :8000 and Vite on :5190 together. Ctrl+C stops both.
## uvicorn reloads on edits to backend/app and backend/ensemble_models only (not the venv, data or tests), and with
## ENSEMBLE_DEV_RELOAD=1 a reload keeps running fits: workers outlive the API process and the new one adopts them
## (see backend/app/engine/runner.py). Ctrl+C stops the reloader, and the workers stop with it.
dev:
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && ENSEMBLE_DEV_RELOAD=1 .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --reload-dir ensemble_models --port $(API_PORT)) & \
	ENSEMBLE_API_URL=http://127.0.0.1:$(API_PORT) npx vite --port $(UI_PORT) & \
	wait

api:
	cd backend && ENSEMBLE_DEV_RELOAD=1 .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --reload-dir ensemble_models --port $(API_PORT)

ui:
	npx vite --port $(UI_PORT)

## The smoke chain (scripts/smoke.py) against the API on :$(API_PORT); starts the API itself if nothing answers there
## (and stops it afterwards). SMOKE_ONLY=core,samples,autopilot,forecast,anomaly,import,fresh,latency,targets,splits,cost,monotone,quantile,llm,reset picks sections.
SMOKE_ONLY ?= core,samples,autopilot,forecast,anomaly,import,fresh,latency,targets,splits,cost,monotone,quantile,llm,reset
smoke:
	@if curl -sf http://127.0.0.1:$(API_PORT)/api/health >/dev/null; then \
		$(PY) scripts/smoke.py --api http://127.0.0.1:$(API_PORT) --only $(SMOKE_ONLY); \
	else \
		$(PY) scripts/smoke.py --api http://127.0.0.1:$(API_PORT) --only $(SMOKE_ONLY) --start-api; \
	fi

## Fast tier: pytest without the slow marks, type check, production build.
test: test-backend test-ui

test-backend:
	cd backend && .venv/bin/python -m pytest -m "not slow" -q

test-ui:
	npx tsc --noEmit
	npx vite build --logLevel warn

## Full tier: the fast tier, the slow pytest marks and the Playwright e2e suite.
test-full: test test-backend-full e2e

test-backend-full:
	cd backend && .venv/bin/python -m pytest -m slow -q

e2e:
	npx playwright test --trace off

## Reset the workbench: delete every run, registered model, batch, autopilot, MLflow record and uploaded dataset, and
## regenerate the (seeded) samples. Through the running API when it answers on :$(API_PORT) (POST /api/admin/reset; refused
## while a run is active), else offline with python -m app.reset.
reset:
	@if curl -sf http://127.0.0.1:$(API_PORT)/api/health >/dev/null; then \
		curl -sf -X POST http://127.0.0.1:$(API_PORT)/api/admin/reset -H 'content-type: application/json' -d '{"confirm":"reset"}' \
		| $(PY) -c 'import json,sys; d=json.load(sys.stdin); print("reset via the API:", d["deleted"], "· samples regenerated:", ", ".join(d["samples"]))' \
		|| { echo "reset refused (is a run active?)"; exit 1; }; \
	else \
		cd backend && .venv/bin/python -m app.reset; \
	fi

## Delete the local data folder (datasets, database). The samples are regenerated on the next start.
clean-data:
	rm -rf backend/data
