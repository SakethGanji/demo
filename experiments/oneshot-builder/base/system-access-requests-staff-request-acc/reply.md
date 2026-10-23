Assumed: Roles are member (any staff), manager and it. The requester names their manager on the request, and only that manager can approve or reject it. Nobody can name themselves as approver. A person can't have two open requests for the same system. Rejections need a reason. IT can only complete approved requests. Requesters are emailed on submission, approval, rejection and completion, and the manager is also emailed when a request needs their approval.

```json file="schema.json"
{
  "roles": ["member", "manager", "it"],
  "tables": [
    {"name": "access_requests",
     "columns": [
       {"name": "system", "type": "text", "required": true},
       {"name": "reason", "type": "text"},
       {"name": "requester_email", "type": "person", "required": true},
       {"name": "manager_email", "type": "person", "required": true},
       {"name": "status", "type": "choice", "choices": ["pending_approval", "approved", "rejected", "completed"], "required": true},
       {"name": "decision_note", "type": "text"},
       {"name": "decided_at", "type": "datetime"},
       {"name": "completed_by", "type": "person"},
       {"name": "completed_at", "type": "datetime"}
     ],
     "sample_rows": [
       {"system": "Bloomberg Terminal", "reason": "Market research for treasury desk", "requester_email": "r.alvarez@bank.example", "manager_email": "d.okafor@bank.example", "status": "pending_approval"},
       {"system": "Core Banking Admin", "reason": "Cover month-end reconciliation", "requester_email": "m.chen@bank.example", "manager_email": "d.okafor@bank.example", "status": "approved", "decision_note": "Approved for month-end only", "decided_at": "2026-10-19T14:05:00"},
       {"system": "Salesforce", "reason": "Needs to log client calls", "requester_email": "p.singh@bank.example", "manager_email": "l.novak@bank.example", "status": "pending_approval"},
       {"system": "Jira", "reason": "Joined the payments project", "requester_email": "j.park@bank.example", "manager_email": "l.novak@bank.example", "status": "completed", "decision_note": "OK", "decided_at": "2026-10-12T09:30:00", "completed_by": "it.desk@bank.example", "completed_at": "2026-10-13T11:00:00"},
       {"system": "Payments Gateway", "reason": "Curious about the dashboards", "requester_email": "t.nguyen@bank.example", "manager_email": "l.novak@bank.example", "status": "rejected", "decision_note": "Not needed for your role", "decided_at": "2026-10-15T16:20:00"},
       {"system": "Splunk", "reason": "Investigating login alerts", "requester_email": "r.alvarez@bank.example", "manager_email": "d.okafor@bank.example", "status": "completed", "decision_note": "Approved", "decided_at": "2026-09-30T10:00:00", "completed_by": "it.desk@bank.example", "completed_at": "2026-10-01T08:45:00"}
     ]}
  ],
  "queries": [
    {"name": "my_requests", "sql": "SELECT id, system, status, decision_note, created_at FROM access_requests WHERE requester_email = :me ORDER BY created_at DESC"},
    {"name": "awaiting_my_approval", "sql": "SELECT id, system, reason, requester_email, created_at FROM access_requests WHERE manager_email = :me AND status = 'pending_approval' ORDER BY created_at"},
    {"name": "it_queue", "sql": "SELECT id, system, requester_email, decided_at, CAST(julianday(:today) - julianday(decided_at) AS INTEGER) AS days_waiting FROM access_requests WHERE status = 'approved' ORDER BY decided_at"},
    {"name": "open_by_status", "sql": "SELECT status, COUNT(*) AS total FROM access_requests WHERE status IN ('pending_approval', 'approved') GROUP BY status"}
  ]
}
```

```python file="actions.py"
from kit import action, ActionError


def _load(db, request_id):
    row = db.get("access_requests", request_id)
    if not row:
        raise ActionError("Request not found")
    return row


@action(roles=["member", "manager", "it"])
def request_access(db, user, system: str, manager_email: str, reason: str = None):
    """Ask for access to a system; your manager must approve it."""
    system = system.strip()
    manager_email = manager_email.strip().lower()
    if not system:
        raise ActionError("Say which system you need access to")
    if manager_email == user.email:
        raise ActionError("You can't approve your own request; pick your manager")
    for r in db.find("access_requests", requester_email=user.email):
        if r["system"].lower() == system.lower() and r["status"] in ("pending_approval", "approved"):
            raise ActionError("You already have an open request for " + system)
    row = db.insert("access_requests", system=system, reason=reason,
                    requester_email=user.email, manager_email=manager_email,
                    status="pending_approval")
    db.notify(user.email, "Access request submitted: " + system,
              "Your request for " + system + " was sent to " + manager_email + " for approval.")
    db.notify(manager_email, "Approval needed: " + system,
              user.email + " has asked for access to " + system + ". Reason: " + (reason or "none given"))
    return row


@action(roles=["manager"])
def approve_request(db, user, request_id: int, note: str = None):
    """Manager approves a request so IT can set it up."""
    row = _load(db, request_id)
    if row["manager_email"] != user.email:
        raise ActionError("Only the requester's named manager can approve this")
    if row["status"] != "pending_approval":
        raise ActionError("This request is not waiting for approval")
    row = db.update("access_requests", request_id, status="approved",
                    decision_note=note, decided_at=db.now())
    db.notify(row["requester_email"], "Access approved: " + row["system"],
              "Your manager approved your request for " + row["system"] + ". IT will set it up next.")
    return row


@action(roles=["manager"])
def reject_request(db, user, request_id: int, reason: str):
    """Manager rejects a request and says why."""
    row = _load(db, request_id)
    if row["manager_email"] != user.email:
        raise ActionError("Only the requester's named manager can reject this")
    if row["status"] != "pending_approval":
        raise ActionError("This request is not waiting for approval")
    if not reason or not reason.strip():
        raise ActionError("Give a reason for rejecting")
    row = db.update("access_requests", request_id, status="rejected",
                    decision_note=reason.strip(), decided_at=db.now())
    db.notify(row["requester_email"], "Access rejected: " + row["system"],
              "Your request for " + row["system"] + " was rejected. Reason: " + reason.strip())
    return row


@action(roles=["it"])
def complete_request(db, user, request_id: int):
    """IT marks an approved request as done."""
    row = _load(db, request_id)
    if row["status"] != "approved":
        raise ActionError("Only approved requests can be completed")
    row = db.update("access_requests", request_id, status="completed",
                    completed_by=user.email, completed_at=db.now())
    db.notify(row["requester_email"], "Access ready: " + row["system"],
              "IT has set up your access to " + row["system"] + ".")
    return row


def _sent_to(t, email):
    return any(email in str(m) for m in t.outbox)


def test_new_request_waits_for_manager(t):
    """A new request starts as waiting for approval and emails the requester and manager"""
    row = t.run("request_access", as_role="member", as_user="k.diaz@bank.example",
                system="Tableau", manager_email="D.Okafor@bank.example", reason="Reporting")
    assert row["status"] == "pending_approval", row
    assert row["manager_email"] == "d.okafor@bank.example", row
    assert _sent_to(t, "k.diaz@bank.example") and _sent_to(t, "d.okafor@bank.example"), t.outbox


def test_cannot_name_self_as_manager(t):
    """You can't choose yourself as the approver"""
    msg = t.fails("request_access", as_role="member", as_user="k.diaz@bank.example",
                  system="Tableau", manager_email="k.diaz@bank.example")
    assert "own request" in msg, msg


def test_no_duplicate_open_request(t):
    """You can't ask again for a system you already have an open request for"""
    t.fails("request_access", as_role="member", as_user="r.alvarez@bank.example",
            system="bloomberg terminal", manager_email="d.okafor@bank.example")


def test_manager_approves(t):
    """The named manager approves a request and the requester is emailed"""
    rid = t.find("access_requests", status="pending_approval", manager_email="d.okafor@bank.example")[0]["id"]
    row = t.run("approve_request", as_role="manager", as_user="d.okafor@bank.example", request_id=rid)
    assert row["status"] == "approved", row
    assert _sent_to(t, "r.alvarez@bank.example"), t.outbox


def test_other_manager_cannot_approve(t):
    """A manager who isn't the requester's manager can't approve"""
    rid = t.find("access_requests", status="pending_approval", manager_email="d.okafor@bank.example")[0]["id"]
    t.fails("approve_request", as_role="manager", as_user="l.novak@bank.example", request_id=rid)
    assert t.get("access_requests", rid)["status"] == "pending_approval"


def test_manager_rejects_with_reason(t):
    """A manager rejects a request with a reason and the requester is emailed"""
    rid = t.find("access_requests", status="pending_approval", manager_email="l.novak@bank.example")[0]["id"]
    row = t.run("reject_request", as_role="manager", as_user="l.novak@bank.example",
                request_id=rid, reason="Not part of your job")
    assert row["status"] == "rejected" and row["decision_note"] == "Not part of your job", row
    assert _sent_to(t, "p.singh@bank.example"), t.outbox


def test_rejection_needs_reason(t):
    """A rejection without a reason is refused"""
    rid = t.find("access_requests", status="pending_approval", manager_email="l.novak@bank.example")[0]["id"]
    t.fails("reject_request", as_role="manager", as_user="l.novak@bank.example",
            request_id=rid, reason="  ")


def test_it_completes_approved_request(t):
    """IT marks an approved request done and the requester is emailed"""
    rid = t.find("access_requests", status="approved")[0]["id"]
    row = t.run("complete_request", as_role="it", as_user="it.desk@bank.example", request_id=rid)
    assert row["status"] == "completed" and row["completed_by"] == "it.desk@bank.example", row
    assert _sent_to(t, "m.chen@bank.example"), t.outbox


def test_it_cannot_complete_unapproved_request(t):
    """IT can't complete a request that hasn't been approved"""
    rid = t.find("access_requests", status="pending_approval")[0]["id"]
    msg = t.fails("complete_request", as_role="it", request_id=rid)
    assert "approved" in msg, msg
```
