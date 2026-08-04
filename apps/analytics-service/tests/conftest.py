"""Pytest fixtures — in-process ASGI client against the real app + Postgres.

These are integration tests: they require the ``accelerator`` Postgres schema
(run migrations). Identity is the POC ``X-User-Id`` header; the seeded System
superuser (DEFAULT_USER_ID) plays the admin role, so no bootstrap is needed.
"""

from __future__ import annotations

import os
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Auth must be on for the RBAC tests to be meaningful.
os.environ.setdefault("ACCELERATOR_AUTH_ENABLED", "true")

from app.main import app  # noqa: E402

DEFAULT_TEAM_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"  # seeded System superuser
SAMPLE_CSV = os.environ.get(
    "TEST_SAMPLE_CSV",
    "/home/saketh/Projects/playground/work/demo/brands_accountmanagement_sample_dataset.csv",
)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth(user_id: str) -> dict:
    """Identity headers for the POC header-auth scheme."""
    return {"X-User-Id": user_id}


@pytest_asyncio.fixture
def admin_id() -> str:
    """The seeded System superuser's id."""
    return DEFAULT_USER_ID


@pytest_asyncio.fixture
async def admin_dataset(client, admin_id):
    """Upload a throwaway dataset to the Default team; return its id."""
    with open(SAMPLE_CSV, "rb") as f:
        r = await client.post(
            "/api/v1/upload",
            headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
            files={"file": ("sample.csv", f, "text/csv")},
        )
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


def rid() -> str:
    """A unique suffix for test-created resources."""
    return uuid.uuid4().hex[:8]
