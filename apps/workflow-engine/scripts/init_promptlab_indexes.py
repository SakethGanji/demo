"""Create PromptLab Mongo indexes.

Idempotent — safe to run on every environment bootstrap. Indexes:

  sessions
    ux_session_id          unique(session_id)

  prompt_runs
    ix_session_created     (session_id, created_at desc)  — trend queries
    ix_session_prompt_hash (session_id, prompt_hash)      — anti-repetition

Run:
  WORKFLOW_MONGO_URL=mongodb://admin:admin@localhost:27017 \\
    python scripts/init_promptlab_indexes.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient

DB_NAME = os.environ.get("WORKFLOW_MONGO_DB", "promptlab")
MONGO_URL = os.environ.get(
    "WORKFLOW_MONGO_URL", "mongodb://admin:admin@localhost:27017"
)


async def main() -> int:
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    try:
        await client.admin.command("ping")
    except Exception as exc:
        print(f"Mongo ping failed at {MONGO_URL}: {exc}", file=sys.stderr)
        return 1

    db = client[DB_NAME]

    await db["sessions"].create_index(
        [("session_id", 1)], unique=True, name="ux_session_id"
    )
    await db["prompt_runs"].create_index(
        [("session_id", 1), ("created_at", -1)], name="ix_session_created"
    )
    await db["prompt_runs"].create_index(
        [("session_id", 1), ("prompt_hash", 1)], name="ix_session_prompt_hash"
    )

    print(f"PromptLab indexes ensured on {MONGO_URL} db={DB_NAME}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
