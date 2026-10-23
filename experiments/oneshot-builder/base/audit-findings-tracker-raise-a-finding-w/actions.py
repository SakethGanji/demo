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