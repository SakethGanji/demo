"""Run-time loader: pulls the decrypted {key: value} map for a single
environment so the workflow engine can resolve $vars expressions.

Wraps a short-lived DB session so call sites don't need to thread one in.
Never persist or return this map through the API.
"""

from __future__ import annotations


async def load_run_variables(
    environment: str = "default", team_id: str = "default"
) -> dict[str, str]:
    from ..db.session import async_session_factory
    from ..repositories.variable_repository import VariableRepository

    async with async_session_factory() as session:
        repo = VariableRepository(session)
        return await repo.load_runtime_map(team_id=team_id, environment=environment)


async def load_run_secrets(
    environment: str = "default", team_id: str = "default"
) -> set[str]:
    """Decrypted set of `type=secret` VALUES (no keys) for log/UI redaction.

    Separate from `load_run_variables` because the redaction path only needs
    the values, and we want the set form so nodes can do cheap substring
    replacement without iterating keys.
    """
    from ..db.session import async_session_factory
    from ..repositories.variable_repository import VariableRepository

    async with async_session_factory() as session:
        repo = VariableRepository(session)
        return await repo.load_runtime_secret_values(
            team_id=team_id, environment=environment
        )
