"""Project secrets: effective (team + project) view, scope rules, missing-secret scan (§3.2)."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ProjectModel, VariableModel, WorkflowModel
from ..repositories.variable_repository import VariableRepository
from .project_service import ProjectError, env_names

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VAR_REF_RE = re.compile(r"\$vars\.([A-Za-z_][A-Za-z0-9_]*)")
VALID_TYPES = {"secret", "string", "number"}
HOST_RE = re.compile(r"^(\*\.)?[A-Za-z0-9.-]+(:\d+)?$")


def fingerprint(value: str) -> str:
    """HMAC-SHA256 of a secret value keyed by the encryption key (detects change without storing)."""
    from ..core.config import settings

    key = (settings.encryption_key or "no-key").encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def validate_hosts(hosts: Any) -> list[str] | None:
    if hosts is None:
        return None
    if not isinstance(hosts, list) or not all(isinstance(h, str) and HOST_RE.match(h) for h in hosts):
        raise ProjectError(422, "invalid_allowed_hosts", "allowed_hosts must be a list of host names (optionally *.domain)")
    return [h.lower() for h in hosts]


def _row(v: VariableModel, *, overrides_team: bool = False, overridden: bool = False) -> dict[str, Any]:
    return {
        "id": v.id,
        "key": v.key,
        "environment": v.environment,
        "type": v.type,
        "value": None if v.type == "secret" else v.value,
        "description": v.description,
        "scope": "project" if v.project_id else "team",
        "overrides_team": overrides_team,
        "overridden": overridden,
        "allowed_hosts": v.allowed_hosts,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }


class SecretService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = VariableRepository(session)

    async def effective(self, project: ProjectModel, environment: str | None = None) -> list[dict[str, Any]]:
        envs = [environment] if environment else env_names(project)
        out: list[dict[str, Any]] = []
        for env in envs:
            team = {v.key: v for v in await self._repo.list(team_id=project.team_id, environment=env)}
            proj = {v.key: v for v in await self._repo.list(team_id=project.team_id, environment=env, project_id=project.id)}
            for key in sorted(set(team) | set(proj)):
                if key in proj:
                    out.append(_row(proj[key], overrides_team=key in team))
                if key in team:
                    out.append(_row(team[key], overridden=key in proj))
        return out

    async def create(self, project: ProjectModel, body: dict[str, Any]) -> dict[str, Any]:
        key = body.get("key") or ""
        if not KEY_RE.match(key):
            raise ProjectError(422, "invalid_key", "key must match ^[A-Za-z_][A-Za-z0-9_]*$")
        env = body.get("environment") or project.default_environment
        if env not in env_names(project):
            raise ProjectError(422, "unknown_environment", f"environment '{env}' is not in this project", environment=env)
        typ = body.get("type") or "secret"
        if typ not in VALID_TYPES:
            raise ProjectError(422, "invalid_type", "type must be secret, string or number")
        if body.get("value") is None:
            raise ProjectError(422, "invalid_value", "value is required")
        hosts = validate_hosts(body.get("allowed_hosts"))
        if await self._repo.get_by_key(project.team_id, key, environment=env, project_id=project.id):
            raise ProjectError(409, "exists", f"'{key}' already exists in environment '{env}'")
        v = await self._repo.create(
            key=key, value=str(body["value"]), team_id=project.team_id, environment=env, type=typ,
            description=body.get("description"), project_id=project.id, allowed_hosts=hosts,
        )
        team = await self._repo.get_by_key(project.team_id, key, environment=env)
        return _row(v, overrides_team=team is not None)

    async def _own(self, project: ProjectModel, var_id: int) -> VariableModel:
        v = await self._repo.get(var_id)
        if not v or v.project_id != project.id:
            raise ProjectError(404, "secret_not_found", "Secret not found in this project")
        return v

    async def update(self, project: ProjectModel, var_id: int, body: dict[str, Any]) -> dict[str, Any]:
        await self._own(project, var_id)
        kwargs: dict[str, Any] = {}
        if "value" in body and body["value"] is not None:
            kwargs["value"] = str(body["value"])
        if "description" in body:
            kwargs["description"] = body["description"]
        if "allowed_hosts" in body:
            kwargs["allowed_hosts"] = validate_hosts(body["allowed_hosts"])
        v = await self._repo.update(var_id, **kwargs)
        team = await self._repo.get_by_key(project.team_id, v.key, environment=v.environment)
        return _row(v, overrides_team=team is not None)

    async def delete(self, project: ProjectModel, var_id: int) -> None:
        await self._own(project, var_id)
        await self._repo.delete(var_id)

    async def references(self, project: ProjectModel) -> dict[str, list[dict[str, Any]]]:
        """{key: [referenced_by...]} for $vars references in the project's workflows and modules."""
        refs: dict[str, list[dict[str, Any]]] = {}
        wfs = (await self._s.execute(select(WorkflowModel).where(WorkflowModel.project_id == project.id))).scalars().all()
        for w in wfs:
            for node in (w.draft_definition or {}).get("nodes", []):
                for key in sorted(set(VAR_REF_RE.findall(_flatten(node.get("parameters", {}))))):
                    refs.setdefault(key, []).append({"kind": "workflow", "id": w.id, "name": w.name, "node": node.get("name")})
        try:
            from ..db.models import CodeModuleModel  # phase 8
            mods = (await self._s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id))).scalars().all()
            secret_call = re.compile(r"""studio\.secret\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\)""")
            for m in mods:
                for lineno, line in enumerate(m.source.splitlines(), 1):
                    for key in set(VAR_REF_RE.findall(line)) | set(secret_call.findall(line)):
                        refs.setdefault(key, []).append({"kind": "module", "id": m.id, "name": m.name, "line": lineno})
        except ImportError:
            pass
        return refs

    async def missing(self, project: ProjectModel, environment: str | None = None) -> list[dict[str, Any]]:
        refs = await self.references(project)
        envs = [environment] if environment else env_names(project)
        defined: dict[str, set[str]] = {}
        for env in envs:
            rows = await self._repo.load_runtime_rows(team_id=project.team_id, environment=env, project_id=project.id)
            defined[env] = set(rows)
        out = []
        for key in sorted(refs):
            missing_in = [env for env in envs if key not in defined[env]]
            if missing_in:
                out.append({"key": key, "missing_in": missing_in, "referenced_by": refs[key]})
        return out


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    return ""
