"""Backend templates the builder retrieves on demand (``search_backend_templates`` / ``get_backend_template``).

Four patterns that every complex internal tool repeats. Each is a runnable module body, not prose: the model adapts names
and columns instead of re-deriving the pattern (and its past bugs: filters ignored, history growing per poll, cancel
flipping back to running, a sync that is not idempotent) from a long rules list.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent / "templates"

_META: dict[str, dict[str, Any]] = {
    "crud-filters": {"file": "crud_filters.py.tpl", "title": "CRUD with filters, search and pagination",
                     "summary": "list with query filters, sort, limit/offset; create/update validation; 404 on bad ids; guarded delete",
                     "tags": "crud list filter search pagination validation table entity cases items"},
    "job-run-poll-sync": {"file": "job_run_poll_sync.py.tpl", "title": "Run in the Jobs service, poll, persist, cancel",
                          "summary": ("start a job, status poll, idempotent sync, sync_pending_runs for a schedule, terminal cancel, "
                                      "unknown job fields refused"),
                          "tags": "jobs job run poll status sync cancel schedule cron persist results test-run triage studio.jobs"},
    "approval-state-machine": {"file": "approval_state_machine.py.tpl", "title": "Approval state machine with audit trail",
                               "summary": "TRANSITIONS map, 409 on illegal move, repeat is a no-op, audit rows, actor from req.caller",
                               "tags": "approval approve reject state machine status transition workflow triage audit history roles"},
    "dashboard-stats": {"file": "dashboard_stats.py.tpl", "title": "Dashboard stats",
                        "summary": "totals, pass rate, per-day trend, flaky list from one read per table",
                        "tags": "dashboard stats statistics aggregate trend rate count chart flaky"},
}


@cache
def _source(file: str) -> str:
    return (_DIR / file).read_text(encoding="utf-8")


def names() -> list[str]:
    return list(_META)


def _tok(s: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9_.]+", s.lower())}


def search_backend_templates(query: str, limit: int = 4) -> list[dict[str, Any]]:
    q = _tok(query)
    scored = []
    for name, m in _META.items():
        score = len(q & _tok(m["tags"])) * 2 + len(q & _tok(m["summary"] + " " + m["title"] + " " + name))
        scored.append((score, name, m))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [{"name": n, "title": m["title"], "summary": m["summary"]} for s, n, m in scored[:limit] if s > 0] or \
           [{"name": n, "title": m["title"], "summary": m["summary"]} for _, n, m in scored[:limit]]


def get_backend_template(name: str) -> dict[str, Any] | None:
    m = _META.get((name or "").strip().lower())
    if m is None:
        return None
    return {"name": name, "title": m["title"], "summary": m["summary"], "source": _source(m["file"])}


def index_lines() -> list[str]:
    return [", ".join(_META) + " (get_backend_template)"]
