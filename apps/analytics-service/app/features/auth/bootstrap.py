"""Bootstrap an initial superuser (POC header identity — no password).

    python -m app.features.auth.bootstrap --email admin@bank.com [--name Admin]

Prints the user id to pass in the ``X-User-Id`` header. If the email already
exists the user is promoted to superuser/owner. Idempotent.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory, dispose_engine
from app.shared.repo import DEFAULT_TEAM_ID
from . import repo
from .permissions import Role


async def _bootstrap(email: str, name: str) -> None:
    existing = await repo.get_user_by_email(email)
    if existing:
        async with async_session_factory() as s:
            await s.execute(
                text("UPDATE users SET is_superuser = true, status = 'active' WHERE id = :id"),
                {"id": existing["id"]},
            )
            await s.commit()
        await repo.upsert_member(str(existing["team_id"] or DEFAULT_TEAM_ID), existing["id"], Role.OWNER.value)
        print(f"Updated existing user {email} (superuser, owner). X-User-Id: {existing['id']}")
    else:
        user = await repo.create_user(
            email=email, name=name,
            team_id=DEFAULT_TEAM_ID, is_superuser=True, role="admin",
        )
        await repo.upsert_member(DEFAULT_TEAM_ID, user["id"], Role.OWNER.value)
        print(f"Created superuser {email}. X-User-Id: {user['id']}")
    await dispose_engine()


def main() -> None:
    p = argparse.ArgumentParser(description="Bootstrap an admin user")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Administrator")
    args = p.parse_args()
    asyncio.run(_bootstrap(args.email, args.name))


if __name__ == "__main__":
    main()
