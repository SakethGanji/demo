Assumed: roles are member (raises, assigns and closes findings) and risk_manager (approves or rejects closures); severities are low/medium/high/critical, and high and critical closes need risk-manager approval; statuses are open → pending_approval → closed; evidence notes need at least 20 characters; due dates can't be in the past; a risk manager can't approve a closure they requested themselves.

```json file="schema.json"
{
  "roles": ["member", "risk_manager"],
  "tables": [
    {"name": "findings",
     "columns": [
       {"name": "title", "type": "text", "required": true},
       {"name": "description", "type": "text"},
       {"name": "severity", "type": "choice", "choices": ["low", "medium", "high", "critical"], "required": true},
       {"name": "status", "type": "choice", "choices": ["open", "pending_approval", "closed"], "required": true},
       {"name": "owner", "type": "person"},
       {"name": "due_date", "type": "date"},
       {"name": "evidence_notes", "type": "text"},
       {"name": "closure_requested_by", "type": "person"},
       {"name": "approved_by", "type": "person"},
       {"name": "closed_on", "type": "date"}
     ],
     "sample_rows": [
       {"title": "Terminated users still have core banking access", "description": "12 accounts of leavers active after 30 days.", "severity": "high", "status": "open", "owner": "m.chen@bank.example", "due_date": "2026-11-15"},
       {"title": "Wire transfer dual control bypassed on weekends", "description": "Single-approver wires found in Q3 sample.", "severity": "critical", "status": "pending_approval", "owner": "d.okafor@bank.example", "due_date": "2026-10-31", "evidence_notes": "Weekend dual-control enforced in system config; 30-day log review shows no exceptions.", "closure_requested_by": "d.okafor@bank.example"},
       {"title": "Vendor contract missing exit clause", "description": "Cloud hosting contract lacks termination assistance terms.", "severity": "medium", "status": "open", "owner": "r.alvarez@bank.example", "due_date": "2026-10-10"},
       {"title": "Branch cash count log not signed", "description": "3 of 20 branches missing sign-off.", "severity": "low", "status": "open"},
       {"title": "Backup restore test not performed", "description": "No restore test evidence for 2025.", "severity": "medium", "status": "closed", "owner": "m.chen@bank.example", "due_date": "2026-09-30", "evidence_notes": "Restore test completed 2026-09-18, report attached to ticket INC-4471.", "closure_requested_by": "m.chen@bank.example", "closed_on": "2026-09-20"},
       {"title": "Policy review overdue for AML procedures", "description": "AML procedures last reviewed in 2023.", "severity": "high", "status": "open", "owner": "d.okafor@bank.example", "due_date": "2026-12-01"}
     ]}
  ],
  "queries": [
    {"name": "my_open_findings", "sql": "SELECT title, severity, status, due_date FROM findings WHERE owner = :me AND status != 'closed' ORDER BY due_date"},
    {"name": "overdue_findings", "sql": "SELECT title, severity, owner, due_date FROM findings WHERE status != 'closed' AND due_date IS NOT NULL AND due_date < :today ORDER BY due_date"},
    {"name": "unassigned_findings", "sql": "SELECT title, severity, created_at FROM findings WHERE status != 'closed' AND (owner IS NULL OR due_date IS NULL)"},
    {"name": "awaiting_risk_approval", "sql": "SELECT title, severity, closure_requested_by, evidence_notes FROM findings WHERE status = 'pending_approval'"},
    {"name": "open_by_severity", "sql": "SELECT severity, COUNT(*) AS total FROM findings WHERE status != 'closed' GROUP BY severity"}
  ]
}
```

```python file="actions.py"
from kit import action, ActionError
from datetime import date

MIN_EVIDENCE = 20
APPROVAL_SEVERITIES = ("high", "critical")


def _finding(db, finding_id):
    row = db.get("findings", finding_id)
    if not row:
        raise ActionError("Finding not found")
    return row


@action(roles=["member", "risk_manager"])
def raise_finding(db, user, title: str, severity: str, description: str = None):
    """Raise a new audit finding with a severity."""
    if not title or not title.strip():
        raise ActionError("A finding needs a title")
    if severity not in ("low", "medium", "high", "critical"):
        raise ActionError("Severity must be low, medium, high or critical")
    return db.insert("findings", title=title.strip(), description=description,
                     severity=severity, status="open")


@action(roles=["member", "risk_manager"])
def assign_finding(db, user, finding_id: int, owner_email: str, due_date: str):
    """Assign an owner and due date to a finding."""
    row = _finding(db, finding_id)
    if row["status"] == "closed":
        raise ActionError("This finding is already closed")
    try:
        due = date.fromisoformat(due_date)
    except ValueError:
        raise ActionError("Due date must look like 2026-12-31")
    if due < date.fromisoformat(db.today()):
        raise ActionError("Due date can't be in the past")
    owner = (owner_email or "").strip().lower()
    if "@" not in owner:
        raise ActionError("Owner must be an email address")
    updated = db.update("findings", finding_id, owner=owner, due_date=due_date)
    db.notify(owner, "Audit finding assigned to you",
              "'%s' (%s severity) is due %s." % (row["title"], row["severity"], due_date))
    return updated


@action(roles=["member", "risk_manager"])
def close_finding(db, user, finding_id: int, evidence_notes: str):
    """Close a finding with evidence; high severity goes to risk-manager approval."""
    row = _finding(db, finding_id)
    if row["status"] == "closed":
        raise ActionError("This finding is already closed")
    if row["status"] == "pending_approval":
        raise ActionError("Closure is already waiting for risk-manager approval")
    notes = (evidence_notes or "").strip()
    if len(notes) < MIN_EVIDENCE:
        raise ActionError("Evidence notes are required (at least %d characters)" % MIN_EVIDENCE)
    if row["severity"] in APPROVAL_SEVERITIES:
        return db.update("findings", finding_id, evidence_notes=notes,
                         status="pending_approval", closure_requested_by=user.email)
    return db.update("findings", finding_id, evidence_notes=notes, status="closed",
                     closure_requested_by=user.email, closed_on=db.today())


@action(roles=["risk_manager"])
def approve_closure(db, user, finding_id: int):
    """Risk manager approves closing a high severity finding."""
    row = _finding(db, finding_id)
    if row["status"] != "pending_approval":
        raise ActionError("This finding is not waiting for approval")
    if row["closure_requested_by"] == user.email:
        raise ActionError("You can't approve a closure you requested yourself")
    updated = db.update("findings", finding_id, status="closed",
                        approved_by=user.email, closed_on=db.today())
    if row["closure_requested_by"]:
        db.notify(row["closure_requested_by"], "Finding closure approved",
                  "'%s' has been closed." % row["title"])
    return updated


@action(roles=["risk_manager"])
def reject_closure(db, user, finding_id: int, reason: str):
    """Risk manager sends a finding back to open with a reason."""
    row = _finding(db, finding_id)
    if row["status"] != "pending_approval":
        raise ActionError("This finding is not waiting for approval")
    if not reason or not reason.strip():
        raise ActionError("Please give a reason for rejecting")
    updated = db.update("findings", finding_id, status="open")
    if row["closure_requested_by"]:
        db.notify(row["closure_requested_by"], "Finding closure rejected",
                  "'%s' was sent back: %s" % (row["title"], reason.strip()))
    return updated


def _by_title(t, text):
    return [r for r in t.find("findings") if r["title"].startswith(text)][0]


def test_raise_finding_starts_open(t):
    """A newly raised finding starts as open with its severity"""
    row = t.run("raise_finding", as_role="member", title="Stale firewall rules", severity="medium")
    assert row["status"] == "open", row
    assert row["severity"] == "medium", row


def test_raise_finding_needs_title(t):
    """A finding without a title is refused"""
    t.fails("raise_finding", as_role="member", title="  ", severity="low")


def test_assign_sets_owner_and_due_date(t):
    """Assigning a finding sets the owner and due date and emails the owner"""
    f = _by_title(t, "Branch cash count")
    row = t.run("assign_finding", as_role="member", finding_id=f["id"],
                owner_email="R.Alvarez@bank.example", due_date="2026-12-15")
    assert row["owner"] == "r.alvarez@bank.example", row
    assert row["due_date"] == "2026-12-15", row
    assert len(t.outbox) == 1, t.outbox


def test_assign_refuses_past_due_date(t):
    """A due date in the past is refused"""
    f = _by_title(t, "Branch cash count")
    t.fails("assign_finding", as_role="member", finding_id=f["id"],
            owner_email="r.alvarez@bank.example", due_date="2020-01-01")


def test_close_low_severity_with_evidence(t):
    """A low severity finding closes straight away when evidence is given"""
    f = _by_title(t, "Branch cash count")
    row = t.run("close_finding", as_role="member", finding_id=f["id"],
                evidence_notes="All 3 branches submitted signed cash count logs.")
    assert row["status"] == "closed", row


def test_close_requires_evidence(t):
    """Closing without proper evidence notes is refused"""
    f = _by_title(t, "Branch cash count")
    t.fails("close_finding", as_role="member", finding_id=f["id"], evidence_notes="done")


def test_close_high_severity_waits_for_approval(t):
    """Closing a high severity finding sends it to the risk manager instead of closing it"""
    f = _by_title(t, "Terminated users")
    row = t.run("close_finding", as_role="member", as_user="m.chen@bank.example",
                finding_id=f["id"],
                evidence_notes="Access removed for all 12 accounts, HR leaver report reconciled.")
    assert row["status"] == "pending_approval", row
    assert row["closure_requested_by"] == "m.chen@bank.example", row


def test_risk_manager_approves_closure(t):
    """A risk manager can approve a pending closure and the requester is told"""
    f = _by_title(t, "Wire transfer")
    row = t.run("approve_closure", as_role="risk_manager", as_user="k.osei@bank.example",
                finding_id=f["id"])
    assert row["status"] == "closed", row
    assert row["approved_by"] == "k.osei@bank.example", row
    assert len(t.outbox) == 1, t.outbox


def test_cannot_approve_own_closure(t):
    """A risk manager can't approve a closure they requested themselves"""
    f = _by_title(t, "Wire transfer")
    t.fails("approve_closure", as_role="risk_manager", as_user="d.okafor@bank.example",
            finding_id=f["id"])


def test_member_cannot_approve(t):
    """A regular member can't approve a closure"""
    f = _by_title(t, "Wire transfer")
    t.fails("approve_closure", as_role="member", finding_id=f["id"])


def test_reject_closure_reopens_finding(t):
    """Rejecting a closure sends the finding back to open"""
    f = _by_title(t, "Wire transfer")
    row = t.run("reject_closure", as_role="risk_manager", as_user="k.osei@bank.example",
                finding_id=f["id"], reason="Log review covers only 30 days, need 90.")
    assert row["status"] == "open", row
```
