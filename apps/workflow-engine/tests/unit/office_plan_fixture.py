"""The small "office supply requests" ProjectPlan body: staff request an item, a manager approves or rejects, a list, and a
dashboard count. Every route but the dashboard is plain CRUD or a state transition (the scaffold builds them with no model); the
manager-only rule and the dashboard are what a model writes."""

from __future__ import annotations

import copy
from typing import Any

_OFFICE_PLAN: dict[str, Any] = {
    "v": 1, "title": "Office supply requests", "summary": "Staff request supplies; a manager approves or rejects.",
    "requirements": {"answers": [], "assumptions": ["One office"], "limitations": [],
                     "acceptance": [{"id": "ac1", "text": "A manager approves a pending request",
                                     "covers": ["route:POST /requests/{id}/approve"]}]},
    "roles_note": "Staff request; managers decide.",
    "environments": [{"name": "dev", "base_url": "http://localhost:4590", "live": False, "refused_actions": []}],
    "data": {
        "conventions": {"timestamps": "managed", "audit_columns": ["created_by", "updated_by"], "soft_delete": "archived_at"},
        "tables": [
            {"name": "requests", "owner": "requests",
             "columns": [{"name": "item", "type": "string", "required": True},
                         {"name": "quantity", "type": "integer", "required": True},
                         {"name": "reason", "type": "string"},
                         {"name": "status", "type": "string", "required": True, "default": "pending",
                          "enum": ["pending", "approved", "rejected"]},
                         {"name": "approved_at", "type": "datetime"}, {"name": "approved_by", "type": "string"},
                         {"name": "rejected_at", "type": "datetime"}, {"name": "rejection_reason", "type": "string"}],
             "indexes": [["status"]],
             "state_machine": {"field": "status", "initial": "pending", "final": ["approved", "rejected"],
                               "transitions": [
                                   {"from": "pending", "to": "approved", "route": "POST /requests/{id}/approve",
                                    "who": "a manager"},
                                   {"from": "pending", "to": "rejected", "route": "POST /requests/{id}/reject",
                                    "who": "a manager"}]}},
        ],
    },
    "modules": [
        {"key": "requests", "kind": "routes"},
        {"key": "dashboard", "kind": "routes", "depends_on": ["requests"], "reads": ["requests"]},
    ],
    "routes": [
        {"key": "GET /requests", "module": "requests", "purpose": "list requests", "pagination": True,
         "tables": {"reads": ["requests"]}},
        {"key": "GET /requests/{id}", "module": "requests", "purpose": "one request", "tables": {"reads": ["requests"]}},
        {"key": "POST /requests", "module": "requests", "purpose": "request an item", "auth": "app_token",
         "request": {"fields": [{"name": "item", "type": "string", "required": True},
                                {"name": "quantity", "type": "integer", "required": True},
                                {"name": "reason", "type": "string"}]},
         "tables": {"writes": ["requests"]}},
        {"key": "POST /requests/{id}/approve", "module": "requests", "purpose": "approve a request", "auth": "app_token",
         "errors": [{"status": 409, "code": "illegal_transition"}, {"status": 403, "code": "forbidden"}],
         "idempotency": "repeat_is_noop", "tables": {"writes": ["requests"]}},
        {"key": "POST /requests/{id}/reject", "module": "requests", "purpose": "reject with a reason", "auth": "app_token",
         "request": {"fields": [{"name": "rejection_reason", "type": "string", "required": True}]},
         "errors": [{"status": 409, "code": "illegal_transition"}, {"status": 403, "code": "forbidden"}],
         "idempotency": "repeat_is_noop", "tables": {"writes": ["requests"]}},
        {"key": "GET /dashboard/summary", "module": "dashboard", "purpose": "how many requests are pending, approved, rejected",
         "response": {"fields": [{"name": "pending", "type": "integer"}, {"name": "approved", "type": "integer"},
                                 {"name": "rejected", "type": "integer"}]},
         "tables": {"reads": ["requests"]}},
    ],
    "apps": [],
    "nfr": {"pagination": {"shape": "{rows,total,limit,offset}", "default_limit": 50, "max_limit": 1000},
            "errors": {"shape": "{error, message, field?}", "codes": ["not_found", "illegal_transition"]}},
    "build_order": ["requests", "dashboard"],
}


def office_plan() -> dict[str, Any]:
    return copy.deepcopy(_OFFICE_PLAN)
