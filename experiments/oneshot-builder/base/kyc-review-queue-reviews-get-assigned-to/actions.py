from kit import action, ActionError

COMPLIANCE_EMAIL = "compliance@bank.example"


def _open_review(db, review_id):
    review = db.get("kyc_reviews", review_id)
    if not review:
        raise ActionError("Review not found")
    return review


def _check_owner(user, review):
    if user.role != "lead" and review["assignee"] != user.email:
        raise ActionError("Only the assigned analyst can do this")


@action(roles=["lead"])
def create_review(db, user, customer_name: str, risk_level: str, due_date: str):
    """Add a new KYC review to the queue, unassigned."""
    if due_date < db.today():
        raise ActionError("Due date can't be in the past")
    return db.insert("kyc_reviews", customer_name=customer_name, risk_level=risk_level,
                     due_date=due_date, status="unassigned")


@action(roles=["lead"])
def assign_review(db, user, review_id: int, analyst_email: str, due_date: str = None):
    """Assign a review to an analyst, optionally setting a new due date."""
    review = _open_review(db, review_id)
    if review["status"] not in ("unassigned", "in_progress"):
        raise ActionError("Only open reviews can be assigned")
    new_due = due_date or review["due_date"]
    if due_date and due_date < db.today():
        raise ActionError("Due date can't be in the past")
    row = db.update("kyc_reviews", review_id, assignee=analyst_email.lower(),
                    due_date=new_due, status="in_progress")
    db.notify(analyst_email.lower(), "KYC review assigned: " + review["customer_name"],
              "You have been assigned the KYC review for %s. Due %s." % (review["customer_name"], new_due))
    return row


@action(roles=["analyst", "lead"])
def complete_review(db, user, review_id: int, notes: str):
    """Mark a review as completed with the analyst's notes."""
    review = _open_review(db, review_id)
    if review["status"] != "in_progress":
        raise ActionError("Only in-progress reviews can be completed")
    _check_owner(user, review)
    if not notes.strip():
        raise ActionError("Completion notes are required")
    return db.update("kyc_reviews", review_id, status="completed",
                     completed_at=db.now(), completion_notes=notes)


@action(roles=["analyst", "lead"])
def escalate_review(db, user, review_id: int, reason: str):
    """Escalate a review to compliance and notify them."""
    review = _open_review(db, review_id)
    if review["status"] != "in_progress":
        raise ActionError("Only in-progress reviews can be escalated")
    _check_owner(user, review)
    if not reason.strip():
        raise ActionError("Please give a reason for escalating")
    row = db.update("kyc_reviews", review_id, status="escalated", escalation_reason=reason)
    db.notify(COMPLIANCE_EMAIL, "KYC escalation: " + review["customer_name"],
              "%s escalated this %s-risk review. Reason: %s" % (user.email, review["risk_level"], reason))
    return row


@action(roles=["compliance"])
def resolve_escalation(db, user, review_id: int, decision: str):
    """Close an escalated review with a compliance decision."""
    review = _open_review(db, review_id)
    if review["status"] != "escalated":
        raise ActionError("Only escalated reviews can be resolved")
    if not decision.strip():
        raise ActionError("A decision is required")
    return db.update("kyc_reviews", review_id, status="closed", compliance_decision=decision)


def _id(t, name):
    return t.find("kyc_reviews", customer_name=name)[0]["id"]


def test_new_review_starts_unassigned(t):
    """A newly created review is unassigned and waiting in the queue"""
    row = t.run("create_review", as_role="lead", customer_name="Zenith Imports",
                risk_level="medium", due_date="2099-01-01")
    assert row["status"] == "unassigned", row


def test_past_due_date_refused(t):
    """A review can't be created with a due date in the past"""
    msg = t.fails("create_review", as_role="lead", customer_name="Zenith Imports",
                  risk_level="low", due_date="2020-01-01")
    assert "past" in msg


def test_assigning_notifies_analyst(t):
    """Assigning a review moves it to in progress and emails the analyst"""
    rid = _id(t, "Brightwell Dental Group")
    row = t.run("assign_review", as_role="lead", review_id=rid, analyst_email="j.okafor@bank.example")
    assert row["status"] == "in_progress" and row["assignee"] == "j.okafor@bank.example", row
    assert len(t.outbox) == 1, t.outbox


def test_cannot_assign_completed_review(t):
    """A completed review can't be reassigned"""
    rid = _id(t, "Elena Petrova")
    t.fails("assign_review", as_role="lead", review_id=rid, analyst_email="m.chen@bank.example")


def test_assignee_completes_review(t):
    """The assigned analyst can complete their review with notes"""
    rid = _id(t, "Priya Nair")
    row = t.run("complete_review", as_role="analyst", as_user="j.okafor@bank.example",
                review_id=rid, notes="All checks passed")
    assert row["status"] == "completed" and row["completed_at"], row


def test_other_analyst_cannot_complete(t):
    """An analyst can't complete a review assigned to someone else"""
    rid = _id(t, "Priya Nair")
    t.fails("complete_review", as_role="analyst", as_user="m.chen@bank.example",
            review_id=rid, notes="Looks fine")


def test_escalation_notifies_compliance(t):
    """Escalating a review marks it escalated and emails compliance"""
    rid = _id(t, "Harbor Logistics LLC")
    row = t.run("escalate_review", as_role="analyst", as_user="m.chen@bank.example",
                review_id=rid, reason="Possible sanctions match")
    assert row["status"] == "escalated", row
    assert len(t.outbox) == 1, t.outbox


def test_escalation_needs_reason(t):
    """Escalating without a reason is refused"""
    rid = _id(t, "Harbor Logistics LLC")
    t.fails("escalate_review", as_role="analyst", as_user="m.chen@bank.example",
            review_id=rid, reason="  ")


def test_compliance_resolves_escalation(t):
    """Compliance can close an escalated review with a decision"""
    rid = _id(t, "Calloway Holdings")
    row = t.run("resolve_escalation", as_role="compliance", review_id=rid,
                decision="Ownership verified, approved")
    assert row["status"] == "closed", row


def test_cannot_resolve_non_escalated(t):
    """Compliance can't resolve a review that was never escalated"""
    rid = _id(t, "Priya Nair")
    t.fails("resolve_escalation", as_role="compliance", review_id=rid, decision="OK")