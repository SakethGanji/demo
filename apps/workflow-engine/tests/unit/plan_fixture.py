"""A QA-Workbench-sized ProjectPlan body that validates with no errors. Tests copy it (``qa_plan()``) and break one thing."""

from __future__ import annotations

import copy
from typing import Any

_QA_PLAN: dict[str, Any] = {
    "v": 1, "title": "QA Workbench", "summary": "Test library, runs through the Jobs service, triage of failures.",
    "requirements": {
        "answers": [{"id": "dom_credentials", "topic": "credentials", "question": "Where do credentials live?",
                     "answer": "Project secrets BANK_USER, BANK_PASS, BANK_TOTP", "round": 1, "source": "domain"},
                    {"id": "dom_retention", "topic": "retention", "question": "How long are runs kept?",
                     "answer": "Keep everything", "round": 1, "source": "domain"}],
        "assumptions": ["One team; everyone may start runs"], "limitations": ["No verified caller identity"],
        "acceptance": [{"id": "ac1", "text": "POST /runs returns a job_id", "covers": ["route:POST /runs"]},
                       {"id": "ac2", "text": "The Library lists every test", "covers": ["screen:Library"]},
                       {"id": "ac3", "text": "Every change is audited", "covers": []}],
    },
    "roles_note": "Testers start runs; leads approve fixes. approved_by is a typed name.",
    "environments": [{"name": "dev", "base_url": "http://localhost:4590", "live": False, "refused_actions": []},
                     {"name": "prod", "base_url": "https://bank.example.com", "live": True,
                      "refused_actions": ["money", "mutate"]}],
    "secrets": [{"name": n, "purpose": "bank login", "environments": ["dev", "prod"],
                 "allowed_hosts": ["localhost", "bank.example.com"]} for n in ("BANK_USER", "BANK_PASS", "BANK_TOTP")],
    "integrations": [{"key": "jobs", "kind": "jobs", "job_types": ["test-run"],
                      "contract": {"test-run": {"required": ["target.base_url", "cases", "policy"]}},
                      "secrets": ["BANK_USER", "BANK_PASS", "BANK_TOTP"], "hosts": ["localhost"],
                      "smoke": {"environment": "dev", "start": "POST /runs", "poll": "GET /runs/{id}",
                                "until": "status in [passed,failed]", "timeout_s": 180}}],
    "data": {
        "conventions": {"timestamps": "managed", "audit_columns": ["created_by", "updated_by"], "soft_delete": "archived_at"},
        "tables": [
            {"name": "tests", "owner": "tests_library", "description": "the test library",
             "columns": [{"name": "test_key", "type": "string", "required": True, "unique": True},
                         {"name": "title", "type": "string", "required": True},
                         {"name": "steps", "type": "json"}],
             "business_key": ["test_key"], "retention": {"policy": "keep", "value": None}},
            {"name": "runs", "owner": "runs", "writers": ["triage"],
             "columns": [{"name": "test_id", "type": "integer", "required": True, "ref": "tests.id", "index": True},
                         {"name": "status", "type": "string", "required": True, "default": "queued",
                          "enum": ["queued", "running", "passed", "failed", "cancelled"]},
                         {"name": "job_id", "type": "string"}],
             "indexes": [["status"]],
             "state_machine": {"field": "status", "initial": "queued", "final": ["passed", "failed", "cancelled"],
                               "transitions": [{"from": "queued", "to": "running", "route": "POST /runs", "who": "any",
                                                "effect": "start job"},
                                               {"from": "running", "to": "passed", "route": "POST /runs/{id}/sync"},
                                               {"from": "running", "to": "failed", "route": "POST /runs/{id}/sync"},
                                               {"from": "queued", "to": "cancelled", "route": "POST /runs/{id}/cancel"}]}},
            {"name": "audit_log", "owner": "common", "writers": ["tests_library", "runs", "triage"],
             "columns": [{"name": "event", "type": "string", "required": True},
                         {"name": "entity", "type": "string"}, {"name": "detail", "type": "json"}],
             "retention": {"policy": "days", "value": 365}},
        ],
    },
    "modules": [
        {"key": "common", "kind": "shared", "helpers": [
            {"name": "audit", "signature": "audit(event, entity, entity_id, caller, detail=None) -> dict", "purpose": "audit row"},
            {"name": "page", "signature": "page(rows, limit, offset) -> dict", "purpose": "pagination"}]},
        {"key": "tests_library", "kind": "routes", "depends_on": ["common"]},
        {"key": "runs", "kind": "routes", "depends_on": ["common"], "reads": ["tests"], "integrations": ["jobs"]},
        {"key": "triage", "kind": "routes", "depends_on": ["common", "runs"], "reads": ["runs"]},
    ],
    "routes": [
        {"key": "GET /tests", "module": "tests_library", "purpose": "list tests", "pagination": True,
         "response": {"fields": [{"name": "rows", "type": "json"}, {"name": "total", "type": "integer"}]},
         "tables": {"reads": ["tests"]}},
        {"key": "POST /tests", "module": "tests_library", "purpose": "create a test", "auth": "app_token",
         "request": {"fields": [{"name": "test_key", "type": "string", "required": True},
                                {"name": "title", "type": "string", "required": True}]},
         "errors": [{"status": 409, "code": "unique_violation"}], "idempotency": "natural_key:test_key",
         "tables": {"writes": ["tests", "audit_log"]}},
        {"key": "POST /runs", "module": "runs", "purpose": "start a run", "auth": "app_token",
         "request": {"fields": [{"name": "test_ids", "type": "json", "required": True}]},
         "response": {"fields": [{"name": "id", "type": "integer"}, {"name": "job_id", "type": "string"}]},
         "errors": [{"status": 422, "code": "policy_blocked"}], "idempotency": "none",
         "tables": {"reads": ["tests"], "writes": ["runs", "audit_log"]}},
        {"key": "GET /runs", "module": "runs", "purpose": "list runs", "pagination": True, "tables": {"reads": ["runs"]}},
        {"key": "GET /runs/{id}", "module": "runs", "purpose": "one run", "tables": {"reads": ["runs"]}},
        {"key": "POST /runs/{id}/sync", "module": "runs", "purpose": "sync from jobs",
         "errors": [{"status": 409, "code": "illegal_transition"}], "idempotency": "repeat_is_noop",
         "tables": {"writes": ["runs"]}},
        {"key": "POST /runs/{id}/cancel", "module": "runs", "purpose": "cancel",
         "errors": [{"status": 409, "code": "illegal_transition"}], "idempotency": "repeat_is_noop",
         "tables": {"writes": ["runs", "audit_log"]}},
        {"key": "POST /triage/{id}/retry", "module": "triage", "purpose": "re-run a failed run",
         "errors": [{"status": 409, "code": "illegal_transition"}], "idempotency": "none",
         "tables": {"reads": ["runs"], "writes": ["runs", "audit_log"]}},
    ],
    "apps": [{"key": "qa_workbench", "name": "QA Workbench", "app_id": None, "screens": [
        {"name": "Library", "purpose": "browse and run tests", "routes": ["GET /tests", "POST /runs"],
         "states": {"empty": "No tests yet", "loading": "Skeleton rows", "error": "Banner with retry"},
         "journeys": ["Pick tests, start a run, watch it finish"]},
        {"name": "Runs", "purpose": "follow runs", "routes": ["GET /runs", "GET /runs/{id}", "POST /runs/{id}/cancel"],
         "states": {"empty": "No runs", "loading": "Spinner", "error": "Banner", "live": "Polls every 5 s"}}]}],
    "nfr": {"pagination": {"shape": "{rows,total,limit,offset}", "default_limit": 50, "max_limit": 1000},
            "errors": {"shape": "{error, message, field?}", "codes": ["not_found", "illegal_transition"]}},
    "build_order": ["common", "tests_library", "runs", "triage", "app:qa_workbench"],
}


def qa_plan() -> dict[str, Any]:
    return copy.deepcopy(_QA_PLAN)
