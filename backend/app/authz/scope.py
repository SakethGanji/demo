"""P19 (C §1.6): object → owning team. Every resolver answers a `Scope`; an id that does not exist answers
`found=False` (the route then answers its own 404, and no decision is recorded).

Chains: dataset version → team (its own team_id, else its project's); run → team (likewise); model version → its run;
model name → its newest version; batch → its model version; champion → project; dataset / version request → the
object asked about; upload → its parent version or project; autopilot → the dataset of its brief.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..catalog.schema import DEFAULT_TEAM


@dataclass(frozen=True)
class Scope:
    team_id: str | None
    project_id: str | None = None
    object_kind: str | None = None
    object_id: str | None = None
    found: bool = True


GLOBAL = Scope(None, object_kind="platform")


def _one(con: Any, q: str, a: Any) -> Any:
    return con.execute(q, (a,)).fetchone()


def _dataset(con: Any, i: str) -> Scope | None:
    r = _one(con, "SELECT COALESCE(d.team_id, p.team_id), d.project_id FROM datasets d LEFT JOIN projects p ON p.id = d.project_id"
                  " WHERE d.id = ?", i)
    return Scope(r[0] or DEFAULT_TEAM, r[1], "dataset", i) if r else None


def _run(con: Any, i: str) -> Scope | None:
    r = _one(con, "SELECT COALESCE(r.team_id, p.team_id, d.team_id), r.project_id FROM runs r LEFT JOIN projects p ON"
                  " p.id = r.project_id LEFT JOIN datasets d ON d.id = r.dataset_id WHERE r.id = ?", i)
    return Scope(r[0] or DEFAULT_TEAM, r[1], "run", i) if r else None


def _project(con: Any, i: str) -> Scope | None:
    r = _one(con, "SELECT team_id FROM projects WHERE id = ?", i)
    return Scope(r[0] or DEFAULT_TEAM, i, "project", i) if r else None


def _team(con: Any, i: str) -> Scope | None:
    r = con.execute("SELECT id FROM teams WHERE id = ? OR slug = ?", (i, i)).fetchone()
    return Scope(r[0], None, "team", r[0]) if r else None


def _via(kind: str, q: str, then: Callable[[Any, str], Scope | None]) -> Callable[[Any, str], Scope | None]:
    def go(con: Any, i: str) -> Scope | None:
        r = _one(con, q, i)
        s = then(con, r[0]) if r and r[0] else None
        return Scope(s.team_id, s.project_id, kind, i) if s else None
    return go


_version = _via("model_version", "SELECT run_id FROM model_versions WHERE id = ?", _run)
RESOLVERS: dict[str, Callable[[Any, str], Scope | None]] = {
    "dataset": _dataset,
    "run": _run,
    "project": _project,
    "team": _team,
    "model_version": _version,
    "model": _via("model", "SELECT id FROM model_versions WHERE model_name = ? ORDER BY created_at DESC LIMIT 1", _version),
    "batch": _via("batch", "SELECT version_id FROM batch_jobs WHERE id = ?", _version),
    "champion": _via("champion", "SELECT project_id FROM champions WHERE id = ?", _project),
    "dataset_request": _via("dataset_request", "SELECT dataset_id FROM dataset_requests WHERE id = ?", _dataset),
    "version_request": _via("version_request", "SELECT version_id FROM version_requests WHERE id = ?", _version),
}


def _upload(con: Any, i: str) -> Scope | None:
    r = _one(con, "SELECT team_id, project_id, parent_version_id FROM uploads WHERE id = ?", i)
    if not r:
        return None
    s = (_dataset(con, r[2]) if r[2] else None) or (_project(con, r[1]) if r[1] else None)
    return Scope(s.team_id if s else (r[0] or DEFAULT_TEAM), s.project_id if s else r[1], "upload", i)


RESOLVERS["upload"] = _upload
RESOLVERS["certification"] = _via("certification", "SELECT version_id FROM certifications WHERE id = ?", _dataset)


def _shareable(con: Any, i: str) -> Scope | None:
    """P21: what a share names: a project (prj_…) or a dataset (any of its versions; the share is on the root)."""
    s = _project(con, i) if i.startswith("prj_") else _dataset(con, i)
    return Scope(s.team_id, s.project_id, "project" if i.startswith("prj_") else "dataset", i) if s else None


def _share(con: Any, i: str) -> Scope | None:
    """P21: a share belongs to its owner team (the team that shares)."""
    r = _one(con, "SELECT owner_team, object_kind, object_id FROM shares WHERE id = ?", i)
    if not r:
        return None
    s = _shareable(con, r[2])
    return Scope(r[0], s.project_id if s else None, "share", i)


RESOLVERS["shareable"] = _shareable
RESOLVERS["share"] = _share
#: the lineage / annotation node kinds (/annotations/{kind}/{id}, /lineage/{kind}/{id}) → resolver kinds
NODE_KINDS = {"dataset": "dataset", "run": "run", "project": "project", "version": "model_version", "model_version": "model_version",
              "model": "model"}


def resolve(app: Any, con: Any, kind: str, i: Any) -> Scope:
    """The owning scope of (kind, id). Unknown kinds raise (a wrong decorator is a bug, caught at the first call)."""
    if i is None or i == "":
        return Scope(None, object_kind=kind, found=False)
    i = str(i)
    if kind == "autopilot":
        st = app.state.autopilot.store.get(i) if hasattr(app.state, "autopilot") else None
        ds = ((st or {}).get("brief") or {}).get("dataset_id")
        s = _dataset(con, ds) if ds else None
        return Scope(s.team_id, s.project_id, "autopilot", i) if s else Scope(None, object_kind="autopilot", object_id=i, found=False)
    if kind == "principal":
        return Scope(None, object_kind="principal", object_id=i)
    if kind.startswith("node:"):
        kind = NODE_KINDS.get(kind[5:], "")
        if not kind:
            return Scope(None, object_kind="node", object_id=i, found=False)
    s = RESOLVERS[kind](con, i)
    return s or Scope(None, object_kind=kind, object_id=i, found=False)
