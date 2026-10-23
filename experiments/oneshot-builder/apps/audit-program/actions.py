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