Assumed: only the item's owner can ask for an extension, and only auditors can decide. An item can have one approved extension and one pending request at a time, but a declined request can be re-submitted. The new date must be after both today and the current due date. A decline needs a note. An auditor can't decide their own request. The request goes to the audit's lead by email, and the decision goes to the requester.

```json file="schema.json"
{
  "roles": [
    "auditor",
    "owner"
  ],
  "tables": [
    {
      "name": "audits",
      "columns": [
        {
          "name": "title",
          "type": "text",
          "required": true
        },
        {
          "name": "scope",
          "type": "text"
        },
        {
          "name": "lead_email",
          "type": "person",
          "required": true
        },
        {
          "name": "status",
          "type": "choice",
          "choices": [
            "open",
            "closed"
          ],
          "required": true,
          "default": "open"
        },
        {
          "name": "closed_at",
          "type": "datetime"
        }
      ],
      "sample_rows": [
        {
          "title": "Treasury Controls Q3",
          "scope": "Wire transfers and bank reconciliations",
          "lead_email": "r.alvarez@bank.example",
          "status": "open"
        },
        {
          "title": "Vendor Management 2026",
          "scope": "Critical third-party vendors",
          "lead_email": "r.alvarez@bank.example",
          "status": "open"
        },
        {
          "title": "Access Reviews 2026",
          "scope": "User access recertification",
          "lead_email": "m.chen@bank.example",
          "status": "open"
        },
        {
          "title": "BSA/AML Testing 2025",
          "scope": "Transaction monitoring",
          "lead_email": "m.chen@bank.example",
          "status": "closed",
          "closed_at": "2026-01-20T16:00:00"
        },
        {
          "title": "Business Continuity 2026",
          "scope": "Disaster recovery readiness",
          "lead_email": "r.alvarez@bank.example",
          "status": "open"
        }
      ]
    },
    {
      "name": "findings",
      "columns": [
        {
          "name": "audit_id",
          "type": "ref",
          "table": "audits",
          "required": true
        },
        {
          "name": "title",
          "type": "text",
          "required": true
        },
        {
          "name": "description",
          "type": "text"
        },
        {
          "name": "severity",
          "type": "choice",
          "choices": [
            "low",
            "medium",
            "high",
            "critical"
          ],
          "required": true
        },
        {
          "name": "status",
          "type": "choice",
          "choices": [
            "open",
            "closed"
          ],
          "required": true,
          "default": "open"
        },
        {
          "name": "closed_at",
          "type": "datetime"
        }
      ],
      "sample_rows": [
        {
          "audit_id": 1,
          "title": "Dual approval missing on wires",
          "description": "Wires under $50,000 released by one person",
          "severity": "high",
          "status": "open"
        },
        {
          "audit_id": 1,
          "title": "Stale bank reconciliations",
          "description": "Reconciling items older than 60 days",
          "severity": "medium",
          "status": "open"
        },
        {
          "audit_id": 2,
          "title": "No exit plan for critical vendor",
          "description": "Core processor has no documented exit strategy",
          "severity": "high",
          "status": "open"
        },
        {
          "audit_id": 3,
          "title": "Terminated users retained access",
          "description": "12 accounts active after termination",
          "severity": "high",
          "status": "closed",
          "closed_at": "2026-09-30T10:00:00"
        },
        {
          "audit_id": 3,
          "title": "Quarterly review not documented",
          "description": "No evidence of Q2 access review",
          "severity": "low",
          "status": "closed",
          "closed_at": "2026-09-30T10:05:00"
        },
        {
          "audit_id": 4,
          "title": "Alert thresholds not reviewed",
          "description": "Thresholds unchanged since 2023",
          "severity": "medium",
          "status": "closed",
          "closed_at": "2026-01-15T09:00:00"
        }
      ]
    },
    {
      "name": "action_items",
      "columns": [
        {
          "name": "finding_id",
          "type": "ref",
          "table": "findings",
          "required": true
        },
        {
          "name": "description",
          "type": "text",
          "required": true
        },
        {
          "name": "owner_email",
          "type": "person",
          "required": true
        },
        {
          "name": "due_date",
          "type": "date",
          "required": true
        },
        {
          "name": "status",
          "type": "choice",
          "choices": [
            "open",
            "done"
          ],
          "required": true,
          "default": "open"
        },
        {
          "name": "done_at",
          "type": "datetime"
        }
      ],
      "sample_rows": [
        {
          "finding_id": 1,
          "description": "Enforce dual approval in wire system",
          "owner_email": "t.nguyen@bank.example",
          "due_date": "2026-09-30",
          "status": "done",
          "done_at": "2026-09-25T11:00:00"
        },
        {
          "finding_id": 1,
          "description": "Retrain wire operations staff",
          "owner_email": "d.okafor@bank.example",
          "due_date": "2026-11-15",
          "status": "open"
        },
        {
          "finding_id": 2,
          "description": "Clear reconciling items older than 60 days",
          "owner_email": "p.shah@bank.example",
          "due_date": "2026-10-01",
          "status": "open"
        },
        {
          "finding_id": 3,
          "description": "Draft vendor exit plan",
          "owner_email": "l.brandt@bank.example",
          "due_date": "2026-12-01",
          "status": "open"
        },
        {
          "finding_id": 4,
          "description": "Disable the 12 terminated accounts",
          "owner_email": "t.nguyen@bank.example",
          "due_date": "2026-09-15",
          "status": "done",
          "done_at": "2026-09-12T14:00:00"
        },
        {
          "finding_id": 5,
          "description": "Document Q2 access review",
          "owner_email": "p.shah@bank.example",
          "due_date": "2026-09-20",
          "status": "done",
          "done_at": "2026-09-18T13:00:00"
        },
        {
          "finding_id": 6,
          "description": "Review and update alert thresholds",
          "owner_email": "d.okafor@bank.example",
          "due_date": "2026-01-10",
          "status": "done",
          "done_at": "2026-01-08T15:00:00"
        }
      ]
    },
    {
      "name": "extension_requests",
      "edit_roles": [],
      "columns": [
        {
          "name": "action_item_id",
          "type": "ref",
          "table": "action_items",
          "required": true,
          "locked": true
        },
        {
          "name": "requested_by",
          "type": "person",
          "required": true,
          "locked": true
        },
        {
          "name": "current_due_date",
          "type": "date",
          "required": true,
          "locked": true
        },
        {
          "name": "new_due_date",
          "type": "date",
          "required": true,
          "locked": true
        },
        {
          "name": "reason",
          "type": "text",
          "required": true,
          "locked": true
        },
        {
          "name": "status",
          "type": "choice",
          "choices": [
            "pending",
            "approved",
            "declined"
          ],
          "required": true,
          "default": "pending",
          "locked": true
        },
        {
          "name": "decided_by",
          "type": "person",
          "locked": true
        },
        {
          "name": "decided_at",
          "type": "datetime",
          "locked": true
        },
        {
          "name": "decision_note",
          "type": "text",
          "locked": true
        }
      ],
      "sample_rows": [
        {
          "action_item_id": 3,
          "requested_by": "p.shah@bank.example",
          "current_due_date": "2026-10-01",
          "new_due_date": "2026-10-31",
          "reason": "Reconciliation backlog larger than expected",
          "status": "declined",
          "decided_by": "r.alvarez@bank.example",
          "decided_at": "2026-09-28T09:30:00",
          "decision_note": "Already overdue; clear the oldest items first and resubmit with a plan"
        },
        {
          "action_item_id": 4,
          "requested_by": "l.brandt@bank.example",
          "current_due_date": "2026-12-01",
          "new_due_date": "2027-01-15",
          "reason": "Waiting on core processor to share contract terms",
          "status": "pending"
        },
        {
          "action_item_id": 5,
          "requested_by": "t.nguyen@bank.example",
          "current_due_date": "2026-09-01",
          "new_due_date": "2026-09-15",
          "reason": "Two admins on leave until September",
          "status": "approved",
          "decided_by": "m.chen@bank.example",
          "decided_at": "2026-08-28T14:00:00",
          "decision_note": "Approved"
        },
        {
          "action_item_id": 7,
          "requested_by": "d.okafor@bank.example",
          "current_due_date": "2026-01-03",
          "new_due_date": "2026-01-10",
          "reason": "Vendor delayed the threshold analysis",
          "status": "approved",
          "decided_by": "m.chen@bank.example",
          "decided_at": "2026-01-02T10:15:00",
          "decision_note": "One week is fine"
        },
        {
          "action_item_id": 2,
          "requested_by": "d.okafor@bank.example",
          "current_due_date": "2026-11-15",
          "new_due_date": "2026-12-15",
          "reason": "Training sessions overlap with year-end close",
          "status": "pending"
        }
      ]
    }
  ],
  "queries": [
    {
      "name": "overdue_action_items",
      "sql": "SELECT id, finding_id, description, owner_email, due_date FROM action_items WHERE status != 'done' AND due_date < :today ORDER BY due_date"
    },
    {
      "name": "my_action_items",
      "sql": "SELECT id, finding_id, description, due_date, status FROM action_items WHERE owner_email = :me AND status != 'done' ORDER BY due_date"
    },
    {
      "name": "open_findings_by_audit",
      "sql": "SELECT a.title AS audit, COUNT(f.id) AS open_findings FROM audits a LEFT JOIN findings f ON f.audit_id = a.id AND f.status = 'open' WHERE a.status = 'open' GROUP BY a.id, a.title"
    },
    {
      "name": "pending_extension_requests",
      "sql": "SELECT r.id, r.action_item_id, a.description, r.requested_by, r.current_due_date, r.new_due_date, r.reason FROM extension_requests r JOIN action_items a ON a.id = r.action_item_id WHERE r.status = 'pending' ORDER BY r.new_due_date"
    }
  ]
}
```

```python file="actions.py"
from kit import action, ActionError
import datetime


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ActionError("Due date must look like 2026-12-31")


@action(roles=["auditor"])
def create_audit(db, user, title: str, scope: str = None, lead_email: str = None):
    """Open a new audit. The lead defaults to you."""
    if not title.strip():
        raise ActionError("Audit needs a title")
    return db.insert("audits", title=title.strip(), scope=scope,
                     lead_email=(lead_email or user.email).lower(), status="open")


@action(roles=["auditor"])
def add_finding(db, user, audit_id: int, title: str, severity: str, description: str = None):
    """Record a finding on an open audit."""
    audit = db.get("audits", audit_id)
    if audit["status"] != "open":
        raise ActionError("This audit is closed, so findings can't be added")
    if not title.strip():
        raise ActionError("Finding needs a title")
    return db.insert("findings", audit_id=audit_id, title=title.strip(),
                     severity=severity, description=description, status="open")


@action(roles=["auditor"])
def add_action_item(db, user, finding_id: int, description: str, owner_email: str, due_date: str):
    """Assign an action item with an owner and due date to an open finding."""
    finding = db.get("findings", finding_id)
    if finding["status"] != "open":
        raise ActionError("This finding is closed, so action items can't be added")
    if not description.strip():
        raise ActionError("Action item needs a description")
    if _parse_date(due_date) < _parse_date(db.today()):
        raise ActionError("Due date can't be in the past")
    owner = owner_email.lower()
    item = db.insert("action_items", finding_id=finding_id, description=description.strip(),
                     owner_email=owner, due_date=due_date, status="open")
    db.notify(owner, "Action item assigned: " + finding["title"],
              "You own: %s\nDue: %s" % (description.strip(), due_date))
    return item


@action(roles=["auditor", "owner"])
def complete_action_item(db, user, action_item_id: int):
    """Mark an action item done. Owners can only complete their own."""
    item = db.get("action_items", action_item_id)
    if user.role == "owner" and item["owner_email"] != user.email:
        raise ActionError("Only the owner of this action item can complete it")
    if item["status"] == "done":
        raise ActionError("This action item is already done")
    return db.update("action_items", action_item_id, status="done", done_at=db.now())


@action(roles=["auditor"])
def close_finding(db, user, finding_id: int):
    """Close a finding once all of its action items are done."""
    finding = db.get("findings", finding_id)
    if finding["status"] == "closed":
        raise ActionError("This finding is already closed")
    open_items = [i for i in db.find("action_items", finding_id=finding_id) if i["status"] != "done"]
    if open_items:
        raise ActionError("Can't close: %d action item(s) are not done yet" % len(open_items))
    return db.update("findings", finding_id, status="closed", closed_at=db.now())


@action(roles=["auditor"])
def close_audit(db, user, audit_id: int):
    """Close an audit once all of its findings are closed."""
    audit = db.get("audits", audit_id)
    if audit["status"] == "closed":
        raise ActionError("This audit is already closed")
    open_findings = [f for f in db.find("findings", audit_id=audit_id) if f["status"] != "closed"]
    if open_findings:
        raise ActionError("Can't close: %d finding(s) are still open" % len(open_findings))
    return db.update("audits", audit_id, status="closed", closed_at=db.now())


@action(roles=["owner"])
def request_extension(db, user, action_item_id: int, new_due_date: str, reason: str):
    """Ask for a new due date on your own action item. Each item can be extended once."""
    item = db.get("action_items", action_item_id)
    if item["owner_email"] != user.email:
        raise ActionError("Only the owner of this action item can ask for an extension")
    if item["status"] != "open":
        raise ActionError("This action item is already done")
    if not reason.strip():
        raise ActionError("Please give a reason for the extension")
    existing = db.find("extension_requests", action_item_id=action_item_id)
    if any(r["status"] == "approved" for r in existing):
        raise ActionError("This action item has already been extended once")
    if any(r["status"] == "pending" for r in existing):
        raise ActionError("There is already an extension request waiting for a decision")
    new_date = _parse_date(new_due_date)
    if new_date <= _parse_date(db.today()):
        raise ActionError("New due date must be in the future")
    if new_date <= _parse_date(item["due_date"]):
        raise ActionError("New due date must be after the current due date")
    req = db.insert("extension_requests", action_item_id=action_item_id,
                    requested_by=user.email, current_due_date=item["due_date"],
                    new_due_date=new_due_date, reason=reason.strip(), status="pending")
    finding = db.get("findings", item["finding_id"])
    audit = db.get("audits", finding["audit_id"])
    db.notify(audit["lead_email"], "Extension requested: " + item["description"],
              "%s asks to move the due date from %s to %s.\nReason: %s"
              % (user.email, item["due_date"], new_due_date, reason.strip()))
    return req


@action(roles=["auditor"])
def approve_extension(db, user, request_id: int, note: str = None):
    """Approve an extension request and move the action item's due date."""
    req = db.get("extension_requests", request_id)
    if req["status"] != "pending":
        raise ActionError("This request has already been decided")
    if req["requested_by"] == user.email:
        raise ActionError("You can't decide your own request")
    item = db.get("action_items", req["action_item_id"])
    if item["status"] != "open":
        raise ActionError("This action item is already done, so it can't be extended")
    db.update("action_items", item["id"], due_date=req["new_due_date"])
    row = db.update("extension_requests", request_id, status="approved", decided_by=user.email,
                    decided_at=db.now(), decision_note=note)
    db.notify(req["requested_by"], "Extension approved: " + item["description"],
              "Your new due date is %s." % req["new_due_date"])
    return row


@action(roles=["auditor"])
def decline_extension(db, user, request_id: int, note: str):
    """Decline an extension request, with a note explaining why."""
    req = db.get("extension_requests", request_id)
    if req["status"] != "pending":
        raise ActionError("This request has already been decided")
    if req["requested_by"] == user.email:
        raise ActionError("You can't decide your own request")
    if not note.strip():
        raise ActionError("Please explain why the extension is declined")
    item = db.get("action_items", req["action_item_id"])
    row = db.update("extension_requests", request_id, status="declined", decided_by=user.email,
                    decided_at=db.now(), decision_note=note.strip())
    db.notify(req["requested_by"], "Extension declined: " + item["description"],
              "Your due date stays %s.\nReason: %s" % (item["due_date"], note.strip()))
    return row


def test_create_audit_defaults_lead_to_creator(t):
    """A new audit is open and led by the person who created it"""
    row = t.run("create_audit", as_role="auditor", as_user="m.chen@bank.example", title="Card Operations")
    assert row["status"] == "open" and row["lead_email"] == "m.chen@bank.example", row


def test_add_finding_to_open_audit(t):
    """An auditor can add a finding to an open audit"""
    row = t.run("add_finding", as_role="auditor", audit_id=2, title="Missing SLAs", severity="medium")
    assert row["status"] == "open" and row["audit_id"] == 2, row


def test_cannot_add_finding_to_closed_audit(t):
    """Findings can't be added to a closed audit"""
    msg = t.fails("add_finding", as_role="auditor", audit_id=4, title="Late find", severity="low")
    assert "closed" in msg, msg


def test_add_action_item_notifies_owner(t):
    """Assigning an action item emails its owner"""
    row = t.run("add_action_item", as_role="auditor", finding_id=3, description="Test exit plan",
                owner_email="L.Brandt@bank.example", due_date="2027-01-31")
    assert row["owner_email"] == "l.brandt@bank.example" and row["status"] == "open", row
    assert len(t.outbox) == 1, t.outbox


def test_cannot_add_action_item_with_past_due_date(t):
    """An action item can't be given a due date in the past"""
    msg = t.fails("add_action_item", as_role="auditor", finding_id=3, description="Old news",
                  owner_email="l.brandt@bank.example", due_date="2020-01-01")
    assert "past" in msg, msg


def test_cannot_add_action_item_to_closed_finding(t):
    """Action items can't be added to a closed finding"""
    t.fails("add_action_item", as_role="auditor", finding_id=4, description="Extra work",
            owner_email="t.nguyen@bank.example", due_date="2027-01-31")


def test_owner_completes_own_action_item(t):
    """An owner can mark their own action item done"""
    row = t.run("complete_action_item", as_role="owner", as_user="d.okafor@bank.example", action_item_id=2)
    assert row["status"] == "done" and row["done_at"], row


def test_owner_cannot_complete_someone_elses_item(t):
    """An owner can't complete an action item that belongs to someone else"""
    t.fails("complete_action_item", as_role="owner", as_user="d.okafor@bank.example", action_item_id=3)
    assert t.get("action_items", 3)["status"] == "open"


def test_auditor_can_complete_any_item(t):
    """An auditor can mark any action item done"""
    row = t.run("complete_action_item", as_role="auditor", action_item_id=3)
    assert row["status"] == "done", row


def test_finding_cannot_close_with_open_items(t):
    """A finding with unfinished action items can't be closed"""
    msg = t.fails("close_finding", as_role="auditor", finding_id=1)
    assert "not done" in msg, msg
    assert t.get("findings", 1)["status"] == "open"


def test_finding_closes_when_all_items_done(t):
    """A finding closes once every action item is done"""
    t.run("complete_action_item", as_role="auditor", action_item_id=2)
    row = t.run("close_finding", as_role="auditor", finding_id=1)
    assert row["status"] == "closed" and row["closed_at"], row


def test_audit_cannot_close_with_open_findings(t):
    """An audit with open findings can't be closed"""
    msg = t.fails("close_audit", as_role="auditor", audit_id=1)
    assert "still open" in msg, msg
    assert t.get("audits", 1)["status"] == "open"


def test_audit_closes_when_all_findings_closed(t):
    """An audit closes once all of its findings are closed"""
    row = t.run("close_audit", as_role="auditor", audit_id=3)
    assert row["status"] == "closed" and row["closed_at"], row


def test_full_chain_closes_audit(t):
    """Finishing items, then closing findings, then closing the audit works end to end"""
    t.run("complete_action_item", as_role="auditor", action_item_id=2)
    t.run("close_finding", as_role="auditor", finding_id=1)
    t.run("complete_action_item", as_role="auditor", action_item_id=3)
    t.run("close_finding", as_role="auditor", finding_id=2)
    row = t.run("close_audit", as_role="auditor", audit_id=1)
    assert row["status"] == "closed", row


def test_owner_requests_extension(t):
    """An owner can ask for a later due date and the audit lead is emailed"""
    row = t.run("request_extension", as_role="owner", as_user="p.shah@bank.example",
                action_item_id=3, new_due_date="2027-03-01", reason="Backlog is bigger than planned")
    assert row["status"] == "pending" and row["current_due_date"] == "2026-10-01", row
    assert len(t.outbox) == 1, t.outbox
    assert t.get("action_items", 3)["due_date"] == "2026-10-01"


def test_owner_cannot_request_extension_for_someone_elses_item(t):
    """An owner can't ask for an extension on another person's action item"""
    msg = t.fails("request_extension", as_role="owner", as_user="p.shah@bank.example",
                  action_item_id=4, new_due_date="2027-03-01", reason="Not mine")
    assert "owner" in msg, msg


def test_cannot_request_second_extension_while_one_is_pending(t):
    """An item with an extension request waiting for a decision can't get another"""
    msg = t.fails("request_extension", as_role="owner", as_user="d.okafor@bank.example",
                  action_item_id=2, new_due_date="2027-03-01", reason="More time please")
    assert "waiting" in msg, msg


def test_cannot_request_new_date_before_current_due_date(t):
    """The requested date must be after the current due date"""
    msg = t.fails("request_extension", as_role="owner", as_user="l.brandt@bank.example",
                  action_item_id=4, new_due_date="2026-11-30", reason="Sooner?")
    assert "pending" in msg or "waiting" in msg or "after" in msg, msg


def test_cannot_request_extension_on_done_item(t):
    """A finished action item can't be extended"""
    msg = t.fails("request_extension", as_role="owner", as_user="t.nguyen@bank.example",
                  action_item_id=1, new_due_date="2027-03-01", reason="Too late")
    assert "done" in msg, msg


def test_approval_moves_due_date_and_notifies(t):
    """Approving a request moves the due date and tells the owner"""
    row = t.run("approve_extension", as_role="auditor", as_user="r.alvarez@bank.example",
                request_id=2, note="OK")
    assert row["status"] == "approved" and row["decided_by"] == "r.alvarez@bank.example", row
    assert t.get("action_items", 4)["due_date"] == "2027-01-15"
    assert len(t.outbox) == 1, t.outbox


def test_item_can_only_be_extended_once(t):
    """After an extension is approved the owner can't ask for another"""
    t.run("approve_extension", as_role="auditor", request_id=2)
    msg = t.fails("request_extension", as_role="owner", as_user="l.brandt@bank.example",
                  action_item_id=4, new_due_date="2027-06-01", reason="Even more time")
    assert "once" in msg, msg


def test_decline_keeps_due_date_and_allows_new_request(t):
    """Declining leaves the due date alone, and the owner may ask again"""
    row = t.run("decline_extension", as_role="auditor", as_user="r.alvarez@bank.example",
                request_id=5, note="Reschedule training instead")
    assert row["status"] == "declined", row
    assert t.get("action_items", 2)["due_date"] == "2026-11-15"
    again = t.run("request_extension", as_role="owner", as_user="d.okafor@bank.example",
                  action_item_id=2, new_due_date="2027-01-31", reason="Revised plan")
    assert again["status"] == "pending", again


def test_decline_needs_a_note(t):
    """A decline must explain why"""
    msg = t.fails("decline_extension", as_role="auditor", request_id=2, note="  ")
    assert "explain" in msg, msg
    assert t.get("extension_requests", 2)["status"] == "pending"


def test_cannot_decide_a_request_twice(t):
    """A request that was already decided can't be decided again"""
    msg = t.fails("approve_extension", as_role="auditor", request_id=1)
    assert "already been decided" in msg, msg


def test_owner_cannot_approve_extension(t):
    """Only auditors can approve extension requests"""
    t.fails("approve_extension", as_role="owner", as_user="l.brandt@bank.example", request_id=2)
    assert t.get("extension_requests", 2)["status"] == "pending"
```
