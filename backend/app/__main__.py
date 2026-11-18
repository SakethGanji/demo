"""`python -m app` from backend/: start the API on :8000."""

import os

import uvicorn

uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("ENSEMBLE_PORT", "8000")))
