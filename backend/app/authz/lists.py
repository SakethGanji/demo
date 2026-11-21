"""P19 (plan §6.2, from P18's `team_rows` middleware): the team on every list row, added in the list handlers, and the
`visible_teams()` filter.

`off` / `audit`: lists are not filtered, every row carries `team_id` and `team: {id, slug, name}` so the UI can group.
`enforce`: rows of teams outside `visible_teams()` are dropped (and `total` follows).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ..catalog.schema import DEFAULT_TEAM

#: list → (list key, what the row's id names, the row field holding it). /api/projects rows carry team_id already.
LISTS: dict[str, list[tuple[str, str, str]]] = {
    "datasets": [("datasets", "dataset", "id")],
    "search": [("datasets", "project", "project_id")],
    "runs": [("runs", "run", "id")],
    "registry": [("versions", "run", "run_id")],
    "registry_versions": [("versions", "run", "run_id")],
    "registry_summary": [("production", "run", "run_id")],
    "projects": [("projects", "project", "id")],
}

_RESOLVE = {
    "dataset": "SELECT d.id, COALESCE(d.team_id, p.team_id) FROM datasets d LEFT JOIN projects p ON p.id = d.project_id WHERE d.id IN ({})",
    "run": "SELECT r.id, COALESCE(r.team_id, p.team_id) FROM runs r LEFT JOIN projects p ON p.id = r.project_id WHERE r.id IN ({})",
    "project": "SELECT id, team_id FROM projects WHERE id IN ({})",
}


def with_team(request: Request, data: Any, which: str) -> Any:
    """Add the owning team to each row of a list response (rows whose owner is unknown are in tm_default: everything
    made before teams); in enforce mode keep only the caller's visible teams."""
    from .deps import mode, principal, visible_teams

    spec = LISTS[which]
    if not isinstance(data, dict):
        return data
    app = request.app
    enforce = mode() == "enforce"
    with app.state.db.connect() as con:
        refs = {r[0]: {"id": r[0], "slug": r[1], "name": r[2]} for r in con.execute("SELECT id, slug, name FROM teams")}
        vis = visible_teams(con, principal(request)) if enforce else None
        for key, kind, field in spec:
            rows = [r for r in data.get(key) or [] if isinstance(r, dict)]
            ids = list({str(r.get(field)) for r in rows if r.get(field)})
            owner: dict[str, str] = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                owner.update({a: b for a, b in con.execute(_RESOLVE[kind].format(",".join("?" * len(chunk))), chunk) if b})
            for r in rows:
                tid = owner.get(str(r.get(field))) or r.get("team_id") or DEFAULT_TEAM
                r["team_id"] = tid
                r["team"] = refs.get(tid)
            if vis is not None and isinstance(data.get(key), list):
                before = len(data[key])
                data[key] = [r for r in data[key] if not isinstance(r, dict) or r.get("team_id") in vis]
                if isinstance(data.get("total"), int):
                    data["total"] -= before - len(data[key])
    if which == "registry":  # a model's team is that of its newest version (versions are listed newest first)
        first: dict[str, Any] = {}
        for v in data.get("versions") or []:
            first.setdefault(v.get("model_name"), v)
        for m in data.get("models") or []:
            v = first.get(m.get("name"))
            m["team_id"] = v["team_id"] if v else None
            m["team"] = v["team"] if v else None
        if vis is not None:
            data["models"] = [m for m in data.get("models") or [] if m.get("team_id") in vis]
    return data
