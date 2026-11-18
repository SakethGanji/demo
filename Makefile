# One-command workflows for the ENSEMBLE demo. See docs/progress/S1.md.
SHELL := /bin/bash
PYTHON ?= python3
VENV := backend/.venv
PY := $(VENV)/bin/python
API_PORT ?= 8000
UI_PORT ?= 5190

.PHONY: setup dev api ui test test-full test-backend test-backend-full test-ui e2e clean-data

## Create the Python venv, install the pinned backend deps and the npm packages.
setup:
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip -q
	$(PY) -m pip install -r backend/requirements.txt
	npm install

## The API on :8000 and Vite on :5190 together. Ctrl+C stops both.
dev:
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --port $(API_PORT)) & \
	ENSEMBLE_API_URL=http://127.0.0.1:$(API_PORT) npx vite --port $(UI_PORT) & \
	wait

api:
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --port $(API_PORT)

ui:
	npx vite --port $(UI_PORT)

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

## Delete the local data folder (datasets, database). The samples are regenerated on the next start.
clean-data:
	rm -rf backend/data
