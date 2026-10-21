"""``jobs``: python routes drive the Jobs service through the broker (broker/jobs.py, ``studio.jobs``).

A stub Jobs service runs on a random local port; the broker reaches it at the engine-configured URL. No database:
``BrokerService._load_env`` / ``_log`` are replaced so each test sees exactly what would be stored.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tests.unit.conftest import anyio_backend  # noqa: F401

pytestmark = pytest.mark.anyio

TOKEN = "jobs-test-token-9f8e7d6c5b4a"


class Stub:
    """A tiny Jobs service. ``routes[(method, path)] = (status, headers, body bytes, delay_s)``."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.routes: dict[tuple[str, str], tuple[int, dict, bytes, float]] = {}
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _do(self):
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                path = self.path.split("?")[0]
                stub.requests.append({"method": self.command, "path": self.path, "auth": self.headers.get("authorization"),
                                      "body": json.loads(body) if body else None})
                status, headers, out, delay = stub.routes.get((self.command, path), (404, {}, b'{"detail":"nope"}', 0))
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                headers = {"content-type": "application/json", **headers}
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                try:
                    self.wfile.write(out)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = _do

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def json(self, method: str, path: str, data, status: int = 200, delay: float = 0) -> None:
        self.routes[(method, path)] = (status, {}, json.dumps(data).encode(), delay)

    def raw(self, method: str, path: str, data: bytes, ctype: str, status: int = 200) -> None:
        self.routes[(method, path)] = (status, {"content-type": ctype}, data, 0)


@pytest.fixture
def stub(monkeypatch):
    from src.core.config import settings

    s = Stub()
    monkeypatch.setattr(settings, "jobs_service_url", s.url)
    monkeypatch.setattr(settings, "jobs_service_token", TOKEN)
    monkeypatch.setattr(settings, "jobs_dev", False)
    monkeypatch.setattr(settings, "jobs_call_timeout_s", 5.0)
    yield s
    s.server.shutdown()


class Env:
    """What ``_load_env`` returns, plus the rows ``_log`` stored."""

    def __init__(self) -> None:
        self.variables: dict[str, str] = {}
        self.rows: dict[str, SimpleNamespace] = {}
        self.allowlist: list[str] = []
        self.logged: list[dict] = []

    def secret(self, key: str, value: str, hosts: list[str] | None) -> None:
        self.variables[key] = value
        self.rows[key] = SimpleNamespace(type="secret", allowed_hosts=hosts)


@pytest.fixture
def env(monkeypatch):
    from src.broker.service import BrokerService

    e = Env()

    async def load_env(self, claims):
        project = SimpleNamespace(settings={"egress_allowlist": e.allowlist})
        return dict(e.variables), set(e.variables.values()), dict(e.rows), project

    async def log(self, call_id, claims, node_type, parameters, variants, body, status, duration_ms):
        from src.services.vars_substitution import redact_deep

        e.logged.append({"node_type": node_type, "params": redact_deep(parameters, variants, expanded=True),
                         "status": status, "body": body})

    monkeypatch.setattr(BrokerService, "_load_env", load_env)
    monkeypatch.setattr(BrokerService, "_log", log)
    return e


def claims(**kw):
    from src.broker.tokens import BrokerClaims

    return BrokerClaims(project_id="proj_x", environment="default", execution_id="exec_1", origin="python_route", **kw)


async def jobs(params: dict, c=None):
    from src.broker.service import BrokerService

    return await BrokerService(session_factory=object()).call(c or claims(), "jobs", None, params, None)


START = {"type": "test-run", "label": "smoke", "target": {"base_url": "http://127.0.0.1:9100",
                                                            "allowed_hosts": ["127.0.0.1:9100"]}, "cases": []}


# ── token never exposed ──────────────────────────────────────────────────────


async def test_start_sends_the_engine_token_and_never_returns_or_logs_it(stub, env):
    # a misbehaving service that echoes the auth header back still never leaks it to the caller or the log
    stub.json("POST", "/jobs", {"job_id": "job_1", "status": "queued", "echo": f"Bearer {TOKEN}"}, status=201)
    status, body = await jobs({"operation": "start", "body": START})
    assert status == 200, body
    assert body["ok"] is True and body["items"][0]["job_id"] == "job_1"
    assert stub.requests[0]["auth"] == f"Bearer {TOKEN}" and stub.requests[0]["body"] == START
    assert TOKEN not in json.dumps(body) and TOKEN not in json.dumps(env.logged)
    assert env.logged[0]["node_type"] == "Jobs" and env.logged[0]["status"] == 200


async def test_the_generated_sdk_carries_no_url_or_token(stub):
    from src.sdk.generate import generate_python_sdk

    files = generate_python_sdk()["files"]
    blob = "\n".join(files.values())
    assert TOKEN not in blob and stub.url not in blob and "8095" not in blob


async def test_credentials_are_masked_in_the_call_log(stub, env):
    stub.json("POST", "/jobs", {"job_id": "job_2", "status": "queued"}, status=201)
    body = {**START, "credentials": {"username": "qa-user", "password": "hunter2-literal"}}
    status, _ = await jobs({"operation": "start", "body": body})
    assert status == 200
    assert stub.requests[0]["body"]["credentials"]["password"] == "hunter2-literal"     # the service gets it
    assert env.logged[0]["params"]["body"]["credentials"] == {"username": "***", "password": "***"}


async def test_a_secret_in_the_start_body_is_resolved_server_side_when_bound_to_the_target(stub, env):
    stub.json("POST", "/jobs", {"job_id": "job_3", "status": "queued"}, status=201)
    env.secret("BANK_PW", "s3cret-value-123", ["127.0.0.1"])
    body = {**START, "credentials": {"username": "qa", "password": "{{ $vars.BANK_PW }}"}}
    status, out = await jobs({"operation": "start", "body": body})
    assert status == 200, out
    assert stub.requests[0]["body"]["credentials"]["password"] == "s3cret-value-123"
    assert "s3cret-value-123" not in json.dumps(env.logged)


async def test_a_secret_not_bound_to_the_target_host_is_refused(stub, env):
    env.secret("BANK_PW", "s3cret-value-123", ["bank.example.com"])
    body = {**START, "credentials": {"password": "{{ $vars.BANK_PW }}"}}
    status, out = await jobs({"operation": "start", "body": body})
    assert status == 403 and out["error"]["code"] == "host_not_allowed"
    assert stub.requests == []


async def test_not_configured_is_a_503_and_dev_mode_uses_the_dev_token(stub, env, monkeypatch):
    from src.broker.jobs import DEV_TOKEN
    from src.core.config import settings

    monkeypatch.setattr(settings, "jobs_service_token", None)
    status, out = await jobs({"operation": "get", "job_id": "job_1"})
    assert status == 503 and out["error"]["code"] == "jobs_not_configured" and stub.requests == []
    monkeypatch.setattr(settings, "jobs_dev", True)
    stub.json("GET", "/jobs/job_1", {"job_id": "job_1", "status": "running"})
    status, out = await jobs({"operation": "get", "job_id": "job_1"})
    assert status == 200 and stub.requests[0]["auth"] == f"Bearer {DEV_TOKEN}"


async def test_upstream_401_never_says_anything_about_the_credentials(stub, env):
    stub.json("GET", "/jobs/job_1", {"detail": f"bad token {TOKEN}"}, status=401)
    status, out = await jobs({"operation": "get", "job_id": "job_1"})
    assert status == 502 and out["error"]["code"] == "jobs_auth_failed" and TOKEN not in json.dumps(out)


# ── host fixed by config ─────────────────────────────────────────────────────


async def test_the_host_is_the_configured_one_whatever_the_caller_passes(stub, env):
    stub.json("GET", "/jobs/job_1", {"job_id": "job_1", "status": "running"})
    status, _ = await jobs({"operation": "get", "job_id": "job_1", "url": "http://evil.example/", "base_url": "http://x"})
    assert status == 200 and stub.requests[0]["path"] == "/jobs/job_1"


@pytest.mark.parametrize("bad", ["../health", "job_1/../../x", "http://evil.example", "", "a/b", "x" * 200])
async def test_a_job_id_cannot_steer_the_path(stub, env, bad):
    status, out = await jobs({"operation": "get", "job_id": bad})
    assert status == 400 and out["error"]["code"] == "invalid_request" and stub.requests == []


async def test_redirects_are_not_followed(stub, env):
    stub.routes[("GET", "/jobs/job_1")] = (302, {"location": "http://evil.example/steal"}, b"", 0)
    status, out = await jobs({"operation": "get", "job_id": "job_1"})
    assert status == 502 and out["error"]["code"] == "jobs_failed" and len(stub.requests) == 1


async def test_the_project_allowlist_applies_to_the_job_target(stub, env):
    env.allowlist = ["bank.internal"]
    status, out = await jobs({"operation": "start", "body": START})
    assert status == 403 and out["error"]["code"] == "host_not_allowed" and stub.requests == []


async def test_read_only_tokens_may_poll_but_not_start_or_cancel(stub, env):
    stub.json("GET", "/jobs/job_1", {"job_id": "job_1", "status": "running"})
    assert (await jobs({"operation": "get", "job_id": "job_1"}, claims(read_only=True)))[0] == 200
    for op in ({"operation": "start", "body": START}, {"operation": "cancel", "job_id": "job_1"},
               {"operation": "control", "job_id": "job_1", "action": "pause"}):
        status, out = await jobs(op, claims(read_only=True))
        assert status == 403 and out["error"]["code"] == "read_only"
    assert [r["path"] for r in stub.requests] == ["/jobs/job_1"]


async def test_control_pause_step_resume_and_reserved_takeover(stub, env):
    for action in ("pause", "step", "resume"):
        stub.json("POST", "/jobs/job_1/control", {"job_id": "job_1", "status": "running", "action": action,
                                                  "paused": action != "resume", "step_credits": int(action == "step")})
        status, out = await jobs({"operation": "control", "job_id": "job_1", "action": action})
        assert status == 200 and out["items"][0]["action"] == action
        assert stub.requests[-1]["path"] == "/jobs/job_1/control" and stub.requests[-1]["body"] == {"action": action}
        assert stub.requests[-1]["auth"] == f"Bearer {TOKEN}"
    # takeover is reserved: the service answers 501, surfaced as jobs_not_implemented
    stub.json("POST", "/jobs/job_1/control", {"error": "not implemented", "reserved": "takeover"}, status=501)
    status, out = await jobs({"operation": "control", "job_id": "job_1", "action": "takeover"})
    assert status == 501 and out["error"]["code"] == "jobs_not_implemented"
    # a finished job: 409 from the service is a client error, not a 502
    stub.json("POST", "/jobs/job_1/control", {"error": "job is passed; nothing to control"}, status=409)
    status, out = await jobs({"operation": "control", "job_id": "job_1", "action": "pause"})
    assert status == 400 and out["error"]["code"] == "jobs_rejected"
    n = len(stub.requests)
    for bad in (None, "", "rewind", 3):
        status, out = await jobs({"operation": "control", "job_id": "job_1", "action": bad})
        assert status == 400 and out["error"]["code"] == "invalid_request"
    assert len(stub.requests) == n


async def test_triage_output_evidence_fields_pass_through(stub, env):
    out_body = {"job_id": "job_t", "status": "passed", "output": {
        "verdict": "app_changed", "dom_diff": {"summary": "renamed", "changes": []}, "network_diff": [],
        "screenshots": {"before": "TC-1.before.png", "after": "TC-1.after.png"}}}
    stub.json("GET", "/jobs/job_t", out_body)
    status, out = await jobs({"operation": "get", "job_id": "job_t"})
    assert status == 200 and out["items"][0]["output"]["screenshots"]["after"] == "TC-1.after.png"
    assert out["items"][0]["output"]["dom_diff"]["summary"] == "renamed"


# ── size caps ────────────────────────────────────────────────────────────────


async def test_frame_is_base64_and_204_is_none(stub, env):
    import base64

    jpeg = b"\xff\xd8\xff" + b"x" * 1000
    stub.raw("GET", "/jobs/job_1/frame", jpeg, "image/jpeg")
    status, out = await jobs({"operation": "frame", "job_id": "job_1"})
    frame = out["items"][0]["frame"]
    assert status == 200 and frame["content_type"] == "image/jpeg" and base64.b64decode(frame["base64"]) == jpeg
    stub.routes[("GET", "/jobs/job_1/frame")] = (204, {}, b"", 0)
    status, out = await jobs({"operation": "frame", "job_id": "job_1"})
    assert status == 200 and out["items"] == [{"frame": None}]


async def test_an_oversized_frame_is_dropped_not_returned(stub, env, monkeypatch):
    from src.broker import jobs as j

    monkeypatch.setattr(j, "FRAME_MAX_BYTES", 1000)
    stub.raw("GET", "/jobs/job_1/frame", b"y" * 5000, "image/jpeg")
    status, out = await jobs({"operation": "frame", "job_id": "job_1"})
    assert status == 200 and out["items"] == [{"frame": None, "too_large": True}]


async def test_artifacts_under_the_cap_are_base64_and_over_it_a_reference(stub, env, monkeypatch):
    import base64

    from src.broker import jobs as j

    monkeypatch.setattr(j, "ARTIFACT_MAX_BYTES", 4096)
    stub.raw("GET", "/jobs/job_1/artifacts/evidence.json", b'{"a":1}', "application/json")
    stub.raw("GET", "/jobs/job_1/artifacts/video.webm", b"v" * 10_000, "video/webm")
    status, out = await jobs({"operation": "artifact", "job_id": "job_1", "name": "evidence.json"})
    item = out["items"][0]
    assert status == 200 and base64.b64decode(item["base64"]) == b'{"a":1}' and item["size"] == 7
    status, out = await jobs({"operation": "artifact", "job_id": "job_1", "name": "video.webm"})
    item = out["items"][0]
    assert status == 200 and "base64" not in item and item["too_large"] is True
    assert item["size"] == 10_000 and item["ref"] == {"job_id": "job_1", "name": "video.webm"}


def test_the_default_caps_fit_the_sandbox_output_cap():
    from src.broker import jobs as j
    from src.sandbox.bwrap import OUTPUT_CAP

    def b64(n: int) -> int:
        return 4 * ((n + 2) // 3)
    assert b64(j.ARTIFACT_MAX_BYTES) + 64 * 1024 < OUTPUT_CAP < 16 * 1024 * 1024
    assert b64(j.FRAME_MAX_BYTES) < OUTPUT_CAP


async def test_artifact_listing_is_normalised(stub, env):
    stub.json("GET", "/jobs/job_1/artifacts", [{"name": "a.png"}])
    status, out = await jobs({"operation": "artifacts", "job_id": "job_1"})
    assert status == 200 and out["items"] == [{"artifacts": [{"name": "a.png"}]}]


# ── timeout ──────────────────────────────────────────────────────────────────


async def test_a_slow_service_is_cut_off_at_the_call_timeout(stub, env, monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "jobs_call_timeout_s", 1.0)
    stub.json("GET", "/jobs/job_1", {"status": "running"}, delay=2.5)
    t0 = time.perf_counter()
    status, out = await jobs({"operation": "get", "job_id": "job_1"})
    assert status == 504 and out["error"]["code"] == "jobs_timeout"
    assert time.perf_counter() - t0 < 2.0


def test_the_configured_timeout_is_clamped_under_the_route_limit(monkeypatch):
    from src.broker.jobs import config
    from src.core.config import settings

    monkeypatch.setattr(settings, "jobs_call_timeout_s", 120.0)
    assert config().timeout_s <= 25.0


async def test_an_unreachable_service_is_a_503(env, monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "jobs_service_url", "http://127.0.0.1:9")   # discard port: nothing listens
    monkeypatch.setattr(settings, "jobs_service_token", TOKEN)
    status, out = await jobs({"operation": "get", "job_id": "job_1"})
    assert status == 503 and out["error"]["code"] == "jobs_unavailable"


async def test_upstream_404_and_cancel(stub, env):
    status, out = await jobs({"operation": "get", "job_id": "job_missing"})
    assert status == 404 and out["error"]["code"] == "job_not_found"
    stub.json("POST", "/jobs/job_1/cancel", {"job_id": "job_1", "status": "cancelled"}, status=202)
    status, out = await jobs({"operation": "cancel", "job_id": "job_1"})
    assert status == 200 and out["items"][0]["status"] == "cancelled"
    stub.json("GET", "/jobs/job_1/events", {"events": [{"seq": 4}], "next": 5})
    status, out = await jobs({"operation": "events", "job_id": "job_1", "after": 3})
    assert status == 200 and out["items"][0]["next"] == 5 and stub.requests[-1]["path"] == "/jobs/job_1/events?after=3"
