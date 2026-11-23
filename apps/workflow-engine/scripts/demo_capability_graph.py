#!/usr/bin/env python
"""The two-team capability-graph demo (PLAN-CAPABILITY-GRAPH section 9), over HTTP, on the fake LLM. Prints a short narrated summary.

    venv/bin/python scripts/demo_capability_graph.py --serve            # starts its own throwaway engine + DB, runs the story, tears both down
    venv/bin/python scripts/demo_capability_graph.py --base-url http://127.0.0.1:PORT --token <fake LLM token> [--reset]

``--base-url`` needs an engine that was started in fake-LLM test mode (``WORKFLOW_LLM_PROVIDER=fake``, ``WORKFLOW_TEST_MODE=1``, a
``WORKFLOW_FAKE_LLM_TOKEN``, a ``workflows_test_*`` database); the script loads the demo script into it over ``/api/_test/fake-llm/script``.
It never calls a real model, and the data is sanitized (no real people, only ``.example`` hosts).

The story: Payments Ops builds an operational-exceptions app (2 questions, one repair round), Keeps it, and the org learns a pattern.
Cards Ops then asks for card fraud alerts: the plan is adapted from that pattern (1 question, no repair round) and the pattern is validated x2.

``--reset`` deletes the two demo projects first. Teams are not modelled by the project API, so "team" here is the project; what the Keep teaches
the org (the pattern, visible to every project) is not removed by a reset, so use ``--serve`` (a fresh database each time) for a clean story.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_DIR))

SCRIPT = ENGINE_DIR / "tests" / "fixtures" / "fake_llm" / "capability_graph" / "demo_two_teams.json"
PROJECTS = {"A": "Payments Ops", "B": "Cards Ops"}
REQ_A = ("We need an application to manage operational exceptions: anyone in payments ops can log an exception, each one gets an owner based on its "
         "category, if the owner hasn't resolved it within 48 hours escalate it to the head of operations, and every Monday send the ops managers a report "
         "of open and closed exceptions.")
# Team B's escalation target and report recipient are the same role (fraud lead); the pattern skeleton still matches team A's.
REQ_B = ("Build an app for card fraud alerts: log an alert, assign an analyst by region, escalate to the fraud lead if it isn't handled in 4 hours, "
         "and send fraud managers a daily summary.")
ANSWERS_A = {"q_assignment.owners": {"payments": "ops analyst", "other": "ops manager"}, "q_exception_case.fields": ["category", "details"]}
ANSWERS_B = {"q_assignment.owners": {"other": "fraud analyst"}}
KEEP_APIS = ("notify.yaml", "queues.yaml")


class Demo:
    def __init__(self, client, token: str) -> None:
        self.api, self.token = client, token
        self.say_lines: list[str] = []

    def say(self, msg: str = "") -> None:
        print(msg, flush=True)

    def ok(self, r, what: str, *codes: int):
        if r.status_code not in (codes or (200, 201)):
            raise SystemExit(f"{what} failed: {r.status_code} {r.text[:400]}")
        return r.json()

    def load_script(self) -> None:
        rules = json.loads(SCRIPT.read_text())["rules"]
        h = {"X-Test-Token": self.token}
        self.ok(self.api.raw("PUT", "/api/_test/fake-llm/script", headers=h, json={"rules": rules}), "load the fake LLM script")
        self.api.raw("DELETE", "/api/_test/fake-llm/calls", headers=h)

    def model_calls(self) -> list[str]:
        r = self.api.raw("GET", "/api/_test/fake-llm/calls", headers={"X-Test-Token": self.token})
        out = [c["messages"][0]["content"].split("\n")[0] for c in r.json()]
        self.api.raw("DELETE", "/api/_test/fake-llm/calls", headers={"X-Test-Token": self.token})
        return out

    def reset(self) -> None:
        for p in self.ok(self.api.get("/projects"), "list projects"):
            if p["name"] in PROJECTS.values():
                self.api.delete(f"/projects/{p['id']}")

    def project(self, name: str) -> str:
        pid = self.ok(self.api.post("/projects", {"name": name, "slug": f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:4]}"}), f"create {name}")["id"]
        from tests.support import ai_eval

        for f in KEEP_APIS:
            r = self.api.post("/api-specs", {"project_id": pid, "name": ai_eval.api_name(f), "spec_text": ai_eval.api_text(f)})
            if r.status_code != 200:
                self.say(f"  (could not register {f}: {r.status_code})")
        self.ok(self.api.post("/knowledge/rebuild", {"project_id": pid}), f"rebuild knowledge of {name}")
        return pid

    def matrix(self, pid: str, concept: str = "concept:escalation") -> tuple[dict, dict]:
        d = self.ok(self.api.get(f"/knowledge/capabilities?project_id={pid}"), "capabilities")
        return next(r for r in d["rows"] if r["concept"]["key"] == concept), d["totals"]

    def build(self, pid: str, request: str, answers: dict, actor: str):
        app = self.ok(self.api.post("/ai/apps", {"project_id": pid, "request": f"{request} ({uuid.uuid4().hex[:6]})", "actor": actor}), "start the app")
        asked = [q["id"] for q in app["questions"]]
        summary = app["summary"]
        app = self.ok(self.api.post(f"/ai/apps/{app['id']}/answers", {"answers": answers, "actor": actor}), "answer the questions", 200)
        app = self.ok(self.api.post(f"/ai/apps/{app['id']}/approve-plan", {"actor": actor}), "approve the plan", 200)
        for p in app["parts"]:
            self.ok(self.api.post(f"/ai/proposals/{p['proposal_id']}/spec/approve", {"actor": actor}), f"approve the spec of {p['key']}", 200)
        for _ in app["parts"]:
            app = self.ok(self.api.post(f"/ai/apps/{app['id']}/build-next", {"actor": actor}), "build the next part", 200)
        kept = self.ok(self.api.post(f"/ai/apps/{app['id']}/keep", {"actor": actor}), "keep the app", 200)
        repairs = sum(p["rounds"] - 1 for p in app["parts"])
        return {"app": app, "kept": kept, "asked": asked, "summary": summary, "repairs": repairs, "calls": self.model_calls()}

    def run(self) -> int:
        self.say("Capability graph demo: two teams, one pattern (fake LLM, sanitized data)\n")
        self.load_script()
        a_id, b_id = self.project(PROJECTS["A"]), self.project(PROJECTS["B"])
        esc, t0 = self.matrix(a_id)
        self.say(f"Act 1  What the org knows today ({PROJECTS['A']}): {t0['implemented']} of {t0['concepts']} concepts implemented; "
                 f"Escalation is {'a GAP' if esc['gap'] else 'covered'}.")

        a = self.build(a_id, REQ_A, ANSWERS_A, "Asha")
        esc, t1 = self.matrix(a_id)
        self.say(f"Act 2  {PROJECTS['A']} builds the exceptions app: {len(a['asked'])} questions ({', '.join(a['asked'])}), {len(a['calls'])} model calls, "
                 f"{a['repairs']} repair round (scripted).")
        self.say(f"       Keep installed {len(a['kept']['installed']['workflows'])} workflows; the org learned '{a['kept']['learned']['pattern_key']}' "
                 f"(validated x{a['kept']['learned']['validated_count']}). Escalation is now {'still a gap' if esc['gap'] else 'no longer a gap'} "
                 f"({t1['implemented']} of {t1['concepts']} implemented).")

        b = self.build(b_id, REQ_B, ANSWERS_B, "Bea")
        banner = b["app"].get("adapted_from") or {}
        diff = ", ".join(f"{d['slot']}: {d['from']} -> {d['to']}" for d in banner.get("diff", []) if d["slot"] in ("sla.due_in", "escalation.target_role", "report.schedule"))
        self.say(f"Act 3  {PROJECTS['B']} asks for card fraud alerts: adapted from '{banner.get('label')}' ({diff}); "
                 f"{len(b['asked'])} question, {len(b['calls'])} model calls, {b['repairs']} repair rounds.")
        self.say(f"       Keep: pattern validated x{b['kept']['learned']['validated_count']} (same pattern: "
                 f"{b['kept']['learned']['pattern_key'] == a['kept']['learned']['pattern_key']}).")

        mem = [e["summary"] for e in self.ok(self.api.get(f"/knowledge/memory?project_id={b_id}"), "memory")["items"] if e["trigger"] == "kept_app"]
        self.say("\nAct 4  The compounding table")
        self.say(f"       {'':<30}{'Team A':<14}{'Team B'}")
        self.say(f"       {'Questions asked':<30}{len(a['asked']):<14}{len(b['asked'])}")
        self.say(f"       {'Patterns reused':<30}{a['summary']['reusing']['patterns']:<14}{b['summary']['reusing']['patterns']}")
        self.say(f"       {'Pattern validated after Keep':<30}{a['kept']['learned']['validated_count']:<14}{b['kept']['learned']['validated_count']}")
        self.say(f"       {'Repair rounds (scripted)':<30}{a['repairs']:<14}{b['repairs']}")
        self.say(f"       {'Model calls':<30}{len(a['calls']):<14}{len(b['calls'])}")
        self.say(f"       Memory in {PROJECTS['B']}: {mem[0] if mem else '(none)'}")
        good = (len(a["asked"]), len(b["asked"])) == (2, 1) and b["kept"]["learned"]["validated_count"] == 2 and a["repairs"] == 1 and b["repairs"] == 0
        self.say("\n" + ("The numbers match the story." if good else "The numbers do NOT match the story."))
        return 0 if good else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--serve", action="store_true", help="start a throwaway fake-LLM engine on a free port with its own database, then stop it")
    ap.add_argument("--base-url", help="a running fake-LLM test engine")
    ap.add_argument("--token", default=os.environ.get("WORKFLOW_FAKE_LLM_TOKEN", ""), help="that engine's WORKFLOW_FAKE_LLM_TOKEN")
    ap.add_argument("--reset", action="store_true", help="delete the two demo projects first")
    args = ap.parse_args()
    from tests.integration.conftest import FAKE_LLM_TOKEN, EngineServer, StudioClient, TestDatabase, fake_env

    if args.serve == bool(args.base_url):
        ap.error("give exactly one of --serve or --base-url")
    if args.serve:
        import tempfile

        db = TestDatabase()
        db.name = db.name.replace("workflows_test_", "workflows_test_demo_", 1)
        db.create()
        srv = None
        try:
            db.migrate("apply")
            with tempfile.TemporaryDirectory() as cwd:                       # an empty cwd: no .env is ever read
                srv = EngineServer(db, extra_env=fake_env(db, E2E_UPSTREAM_URL="http://upstream.example"), cwd=cwd, label="-demo")
                srv.start()
                print(f"engine pid {srv.proc.pid} on {srv.base_url}, database {db.name}\n")
                return Demo(StudioClient(srv.base_url), FAKE_LLM_TOKEN).run()
        finally:
            if srv:
                srv.stop()
            db.drop()
    if not args.token:
        ap.error("--token (or WORKFLOW_FAKE_LLM_TOKEN) is required with --base-url")
    demo = Demo(StudioClient(args.base_url), args.token)
    if args.reset:
        demo.reset()
    return demo.run()


if __name__ == "__main__":
    sys.exit(main())
