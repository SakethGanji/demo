"""
Tier 1/2 eval harness — the actual experiment SDK-DESIGN.md §9 proposed and never ran:
can a real model write a correct script against the generated-signature-reference SDK,
in one shot, with no conversation history and no hand-holding?

Calls Gemini directly via REST (bypassing the app's own llm_provider.py, which has a
known, separate bug around multi-turn tool loops on Gemini 3.x — irrelevant here since
this is a single-shot text completion, not a tool-calling loop).

Grading is MECHANICAL, not an LLM judge: did the script execute without raising, and
does the resulting graph satisfy structural assertions (trigger type present, the right
downstream node types reachable, branches wired to the right ports). Same principle as
Tier 0 — a script either raises or it doesn't, validate() either finds problems or it
doesn't.

Usage:
    venv/bin/python scripts/sdk_eval_harness.py            # 3 trials per task
    venv/bin/python scripts/sdk_eval_harness.py --trials 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import httpx  # noqa: E402

from src.engine.workflow_sdk import execute_workflow_script, signature_reference  # noqa: E402

GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _load_key() -> str:
    env_path = os.path.join(_ROOT, ".env")
    with open(env_path) as f:
        for line in f:
            if line.startswith("WORKFLOW_GEMINI_API_KEY="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("WORKFLOW_GEMINI_API_KEY not found in .env")


SYSTEM_PREAMBLE = """You are writing a Python script against a workflow-building SDK. \
The SDK below is generated from a real node registry — every function name and \
parameter name is exact. Do not invent parameters that aren't listed.

SDK REFERENCE:
{signature_reference}

RULES:
- Output ONLY the Python script. No markdown fences, no prose, no explanation.
- Assign each node to a variable, wire them with >>, and use .PORTNAME >> for named \
ports (e.g. check.true >> node).
- Call validate() as the last line.
- Do not call test_run(), call_tool(), list_nodes(), or describe() — just build and \
validate the graph.

TASK:
{task}
"""


@dataclass
class Task:
    key: str
    prompt: str
    grade: "callable"


@dataclass
class Trial:
    task_key: str
    script: str
    error: str | None
    passed: bool
    reason: str


def call_gemini(prompt: str, api_key: str) -> str:
    # Key goes in a HEADER, never a query param. A query param ends up inside the
    # request URL, and httpx's raise_for_status() embeds the full URL in its
    # exception message — a transient 503 during this eval run leaked the key in
    # cleartext into stdout and a results JSON file. Headers never appear there.
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                GEMINI_URL,
                headers={"x-goog-api-key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except httpx.HTTPStatusError as e:
            last_exc = e
            # 429 (rate limit) and 5xx are infra, not a model mistake — retry with
            # real backoff instead of counting it as a failed trial (the previous
            # run did exactly that: 10/12 "failures" were actually rate limiting).
            if e.response.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                wait = 15 * (attempt + 1)
                print(f"    ({e.response.status_code}, retrying in {wait}s...)")
                time.sleep(wait)
                continue
            raise
    raise last_exc  # pragma: no cover


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.index("\n")
        text = text[first_nl + 1:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return text.strip()


# ---------------------------------------------------------------------------
# Grading functions — mechanical, over the real Workflow object.
# ---------------------------------------------------------------------------

def grade_finance_export(result) -> tuple[bool, str]:
    if result.error:
        return False, result.error
    types = [n.type for n in result.workflow.nodes]
    if "Cron" not in types:
        return False, "no Cron trigger"
    if "HttpRequest" not in types:
        return False, "no HttpRequest fetch"
    if "If" not in types:
        return False, "no If branch"
    if "Postgres" not in types:
        return False, "no Postgres write"
    if "StopAndError" not in types:
        return False, "no StopAndError on the empty path"
    ports = {c.source_output for c in result.workflow.connections}
    if "true" not in ports or "false" not in ports:
        return False, f"If node not wired with true/false ports (saw: {ports})"
    return True, "ok"


def grade_weather_email(result) -> tuple[bool, str]:
    if result.error:
        return False, result.error
    types = [n.type for n in result.workflow.nodes]
    if "Cron" not in types:
        return False, "no Cron trigger"
    if "HttpRequest" not in types:
        return False, "no HttpRequest check"
    if "If" not in types:
        return False, "no If branch"
    if "SendEmail" not in types:
        return False, "no SendEmail"
    if "StopAndError" not in types:
        return False, "no StopAndError on the empty path"
    return True, "ok"


def _has_serial_same_type_chain(result, node_type: str) -> bool:
    """True if two nodes of the same type are wired directly to each other — the
    bug the first run found: postgres_us >> postgres_eu means eu's INPUT becomes
    us's OUTPUT, not independent data from the branch. Fan-out (parent >> [a,b,c])
    does not trigger this."""
    by_name = {n.name: n.type for n in result.workflow.nodes}
    for c in result.workflow.connections:
        if by_name.get(c.source_node) == node_type and by_name.get(c.target_node) == node_type:
            return True
    return False


def _make_regional_grader(min_count: int):
    def grade(result) -> tuple[bool, str]:
        if result.error:
            return False, result.error
        pg_nodes = [n for n in result.workflow.nodes if n.type == "Postgres"]
        if len(pg_nodes) < min_count:
            return False, f"expected >={min_count} Postgres nodes (one per region), got {len(pg_nodes)}"
        if "Cron" not in [n.type for n in result.workflow.nodes]:
            return False, "no Cron trigger"
        if "If" not in [n.type for n in result.workflow.nodes]:
            return False, "no If branch for the emptiness check"
        if _has_serial_same_type_chain(result, "Postgres"):
            return False, "Postgres nodes chained to each other (serial), not fanned out from the branch"
        return True, "ok"
    return grade


grade_regional_loop = _make_regional_grader(3)
grade_regional_loop_large = _make_regional_grader(8)


TASKS = [
    Task(
        key="finance_export",
        prompt=(
            "Every Monday at 07:00 UK time, GET https://example.com/export. "
            "If the response body is not empty, upsert it into the Postgres table "
            "finance.ledger. If it IS empty, stop the workflow with an error message "
            "explaining the export was empty."
        ),
        grade=grade_finance_export,
    ),
    Task(
        key="weather_email",
        prompt=(
            "Every day at 06:00, GET https://api.weather.example.com/current. "
            "If the response is empty, stop with an error. Otherwise send an email "
            "to ops@example.com with subject 'Daily weather check' and a short body "
            "saying the check completed."
        ),
        grade=grade_weather_email,
    ),
    Task(
        key="regional_loop",
        prompt=(
            "Every Monday at 07:00 UK time, GET https://example.com/export. "
            "If the response is not empty, upsert it into Postgres table "
            "finance.ledger separately for each of three regions: US, EU, and APAC "
            "(three separate Postgres nodes, one per region — consider using a loop). "
            "If the response IS empty, stop with an error."
        ),
        grade=grade_regional_loop,
    ),
    Task(
        key="regional_loop_large",
        prompt=(
            "Every Monday at 07:00 UK time, GET https://example.com/export. "
            "If the response is not empty, upsert it into Postgres table "
            "finance.ledger separately for EACH of these 8 regions: US, CA, MX, UK, "
            "DE, FR, JP, AU — one independent Postgres node per region, all fed from "
            "the same branch. If the response IS empty, stop with an error."
        ),
        grade=grade_regional_loop_large,
    ),
]


def run(trials: int) -> None:
    api_key = _load_key()
    ref = signature_reference()
    print(f"Signature reference: {len(ref)} chars\n{'=' * 70}")

    all_results: dict[str, list[Trial]] = {}
    for task in TASKS:
        all_results[task.key] = []
        prompt = SYSTEM_PREAMBLE.format(signature_reference=ref, task=task.prompt)
        for i in range(trials):
            try:
                raw = call_gemini(prompt, api_key)
            except Exception as e:  # noqa: BLE001
                all_results[task.key].append(Trial(task.key, "", str(e), False, f"API error: {e}"))
                continue
            script = _strip_fences(raw)
            result = execute_workflow_script(script)
            passed, reason = task.grade(result)
            all_results[task.key].append(
                Trial(task.key, script, result.error, passed, reason)
            )
            status = "PASS" if passed else "FAIL"
            print(f"[{task.key}] trial {i+1}/{trials}: {status} — {reason}")
            time.sleep(8)  # this model's tier rate-limits well below 1 req/sec (verified: 10/12
                           # "failures" in the prior run were 429s, not model mistakes)

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    overall_pass = 0
    overall_total = 0
    for task in TASKS:
        trials_ = all_results[task.key]
        n_pass = sum(1 for t in trials_ if t.passed)
        n_loop = sum(1 for t in trials_ if "for " in t.script and " in " in t.script)
        overall_pass += n_pass
        overall_total += len(trials_)
        print(f"{task.key}: {n_pass}/{len(trials_)} passed  |  {n_loop}/{len(trials_)} used a for-loop")
        for t in trials_:
            if not t.passed:
                print(f"    FAILED ({t.reason}):\n    {'-' * 40}")
                for line in t.script.splitlines():
                    print(f"    {line}")
                print(f"    {'-' * 40}")
    print(f"\nOVERALL: {overall_pass}/{overall_total}")

    out_path = os.path.join(_ROOT, "scripts", "sdk_eval_results.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                task.key: [
                    {"passed": t.passed, "reason": t.reason, "error": t.error, "script": t.script}
                    for t in all_results[task.key]
                ]
                for task in TASKS
            },
            f,
            indent=2,
        )
    print(f"\nFull trial data written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    run(args.trials)
