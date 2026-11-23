"""In-memory stand-ins for the interface / chat repositories (the real ones are raw SQL over Builder A's tables)."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "workflows"
PROJECT = SimpleNamespace(id="proj_1", slug="bank", live_environment="prod", environments=[], settings={})


def fixture(name: str = "refund_triage_ui.json") -> dict[str, Any]:
    d = json.loads((FIXTURES / name).read_text())
    return {"nodes": d["nodes"], "connections": d["connections"], "interface": d["interface"]}


def now() -> datetime:
    return datetime.now(timezone.utc)


class FakeInterfaceRepo:
    def __init__(self, definition: dict[str, Any] | None = None, *, slug: str | None = None, active: bool = True, project_id: str = "proj_1") -> None:
        self.definition = copy.deepcopy(definition if definition is not None else fixture())
        self.slug = slug or self.definition.get("interface", {}).get("slug", "refunds")
        self.active, self.project_id = active, project_id
        self.submissions: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.progress_by_run: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, list[dict[str, Any]]] = {}
        self.outputs: dict[str, dict[str, Any]] = {}
        self.commits = 0

    async def get_slug(self, project_id, slug):
        if project_id == self.project_id and slug == self.slug:
            return {"project_id": project_id, "slug": slug, "workflow_id": "wf_1", "kind": self.definition.get("interface", {}).get("kind", "form"), "version_id": 7}
        return None

    async def slug_of_workflow(self, workflow_id):
        return await self.get_slug(self.project_id, self.slug)

    async def workflow_row(self, workflow_id):
        return {"id": workflow_id, "name": "Refund triage (UI)", "project_id": self.project_id, "active": self.active,
                "draft_definition": self.definition, "published_version_id": 7}

    async def version_definition(self, version_id):
        return {"workflow_id": "wf_1", "definition": self.definition}

    async def insert_submission(self, *, id, project_id, workflow_id, slug, key, token_hash, mode):
        if any(s["project_id"] == project_id and s["slug"] == slug and s["idempotency_key"] == key for s in self.submissions.values()):
            return False
        self.submissions[id] = {"id": id, "project_id": project_id, "workflow_id": workflow_id, "slug": slug, "idempotency_key": key,
                                "execution_id": None, "run_token_hash": token_hash, "mode": mode}
        return True

    async def get_submission_by_key(self, project_id, slug, key):
        return next((s for s in self.submissions.values() if (s["project_id"], s["slug"], s["idempotency_key"]) == (project_id, slug, key)), None)

    async def get_submission_by_execution(self, execution_id):
        return next((s for s in self.submissions.values() if s["execution_id"] == execution_id), None)

    async def set_submission_execution(self, submission_id, execution_id):
        self.submissions[submission_id]["execution_id"] = execution_id

    async def set_submission_token_hash(self, submission_id, token_hash):
        self.submissions[submission_id]["run_token_hash"] = token_hash

    async def delete_submission(self, submission_id):
        self.submissions.pop(submission_id, None)

    async def get_run(self, execution_id):
        return self.runs.get(execution_id)

    async def progress(self, execution_id):
        return self.progress_by_run.get(execution_id, {"done": 0, "current": None})

    async def open_tasks(self, execution_id):
        return [t for t in self.tasks.get(execution_id, []) if t["status"] == "open"]

    async def tasks_of_execution(self, execution_id):
        return self.tasks.get(execution_id, [])

    async def node_outputs(self, execution_id):
        return self.outputs.get(execution_id, {})

    async def latest_run(self, workflow_id):
        return next(iter(self.runs.values()), None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    def add_run(self, exec_id: str, status: str, **meta: Any) -> None:
        self.runs[exec_id] = {"id": exec_id, "workflow_id": "wf_1", "workflow_version_id": 7, "status": status, "mode": "api",
                              "start_time": now(), "end_time": None, "error_count": 0, "metadata": meta}


class RunRecorder:
    """A fake ``start_detached_run``: records the calls and hands out execution ids."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: Exception | None = None

    async def __call__(self, workflow_id, **kw):
        if self.fail:
            raise self.fail
        self.calls.append({"workflow_id": workflow_id, **kw})
        return f"exec_{len(self.calls)}"


def make_service(repo: FakeInterfaceRepo | None = None, recorder: RunRecorder | None = None):
    from src.services.interface_service import InterfaceService

    repo = repo or FakeInterfaceRepo()
    recorder = recorder or RunRecorder()
    svc = InterfaceService(object(), repo=repo, start_run=recorder)

    async def project(ref, *, slug_only=False):
        return PROJECT if ref in (("bank",) if slug_only else ("bank", "proj_1")) else None

    svc._project = project                                  # type: ignore[method-assign]
    return svc, repo, recorder


class FakeChatRepo:
    """Same semantics as ``ChatRepository`` (lock-free, single-threaded)."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.msgs: dict[str, list[dict[str, Any]]] = {}
        self.run_status: dict[str, str] = {}              # execution id -> executions.status
        self.fresh = True                                 # False = the session was last touched long ago (a crashed turn)

    async def create_session(self, *, id, project_id, workflow_id, slug, mode, user_label, token_hash):
        self.sessions[id] = {"id": id, "project_id": project_id, "workflow_id": workflow_id, "slug": slug, "mode": mode,
                             "user_label": user_label, "token_hash": token_hash, "busy_execution_id": None, "last_seq": 0}
        self.msgs[id] = []

    async def get_session(self, session_id):
        return self.sessions.get(session_id)

    async def messages(self, session_id, after=0, limit=50):
        return [m for m in self.msgs[session_id] if m["seq"] > after][:limit]

    async def _still_busy(self, busy_execution_id, fresh):
        fresh = fresh and self.fresh
        if busy_execution_id == "pending":
            return fresh
        status = self.run_status.get(busy_execution_id)
        return status is not None and (status in ("running", "waiting") or fresh)

    async def begin_turn(self, session_id, *, message_id, content, client_message_id, execution_id=None):
        s = self.sessions.get(session_id)
        if s is None:
            return {"outcome": "missing"}
        dup = next((m for m in self.msgs[session_id] if m.get("client_message_id") == client_message_id), None)
        if dup:
            return {"outcome": "replay", "seq": dup["seq"], "execution_id": dup.get("execution_id")}
        if s["busy_execution_id"] and await self._still_busy(s["busy_execution_id"], True):
            return {"outcome": "busy"}
        history = [m for m in self.msgs[session_id] if m["role"] in ("user", "assistant")][-20:]
        s["last_seq"] += 1
        self.msgs[session_id].append({"seq": s["last_seq"], "role": "user", "content": content, "client_message_id": client_message_id,
                                      "execution_id": execution_id, "created_at": now()})
        s["busy_execution_id"] = "pending"
        return {"outcome": "started", "seq": s["last_seq"], "history": history}

    async def attach_execution(self, session_id, seq, execution_id):
        self.sessions[session_id]["busy_execution_id"] = execution_id
        next(m for m in self.msgs[session_id] if m["seq"] == seq)["execution_id"] = execution_id
        self.run_status[execution_id] = "running"

    async def add_reply(self, session_id, *, message_id, role, content, execution_id, clear_busy, dedupe_key=None):
        s = self.sessions[session_id]
        if dedupe_key and any(m.get("client_message_id") == dedupe_key for m in self.msgs[session_id]):
            return 0
        s["last_seq"] += 1
        self.msgs[session_id].append({"seq": s["last_seq"], "role": role, "content": content, "client_message_id": dedupe_key,
                                      "execution_id": execution_id, "created_at": now()})
        if clear_busy:
            s["busy_execution_id"] = None
        return s["last_seq"]

    async def rollback(self):
        pass
