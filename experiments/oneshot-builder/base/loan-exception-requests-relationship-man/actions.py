from kit import action, ActionError

CREDIT_OFFICER_LIMIT = 250000


def _load_pending(db, request_id):
    row = db.get("waiver_requests", request_id)
    if not row:
        raise ActionError("Waiver request not found")
    if row["status"] not in ("pending_credit_officer", "pending_chief_credit_officer"):
        raise ActionError("This request has already been decided")
    return row


def _check_authority(row, user):
    if row["requester_email"] == user.email:
        raise ActionError("You cannot decide your own request")
    if row["amount"] > CREDIT_OFFICER_LIMIT and user.role != "chief_credit_officer":
        raise ActionError("Requests above $250,000 must be decided by the chief credit officer")


def _tell_requester(db, row, outcome, note):
    db.notify(row["requester_email"],
              "Waiver request for %s was %s" % (row["borrower"], outcome),
              "Loan %s, amount $%s: %s.%s" % (row["loan_number"], row["amount"], outcome,
                                              (" Note: " + note) if note else ""))


@action(roles=["relationship_manager"])
def request_waiver(db, user, borrower: str, loan_number: str, amount: float, reason: str,
                   exception_type: str = None):
    """Request a loan exception waiver. Up to $250,000 goes to a credit officer, above that to the chief credit officer."""
    if amount <= 0:
        raise ActionError("Amount must be more than 0")
    if not reason.strip():
        raise ActionError("Please give a reason for the waiver")
    status = ("pending_chief_credit_officer" if amount > CREDIT_OFFICER_LIMIT
              else "pending_credit_officer")
    return db.insert("waiver_requests", borrower=borrower, loan_number=loan_number,
                     exception_type=exception_type, amount=amount, reason=reason,
                     status=status, requester_email=user.email)


@action(roles=["credit_officer", "chief_credit_officer"])
def approve_waiver(db, user, request_id: int, note: str = None):
    """Approve a pending waiver request you are authorized to decide."""
    row = _load_pending(db, request_id)
    _check_authority(row, user)
    updated = db.update("waiver_requests", request_id, status="approved",
                        decided_by=user.email, decided_at=db.now(), decision_note=note)
    _tell_requester(db, row, "approved", note)
    return updated


@action(roles=["credit_officer", "chief_credit_officer"])
def reject_waiver(db, user, request_id: int, note: str):
    """Reject a pending waiver request you are authorized to decide. A note is required."""
    if not note or not note.strip():
        raise ActionError("Please explain why the request is rejected")
    row = _load_pending(db, request_id)
    _check_authority(row, user)
    updated = db.update("waiver_requests", request_id, status="rejected",
                        decided_by=user.email, decided_at=db.now(), decision_note=note)
    _tell_requester(db, row, "rejected", note)
    return updated


def _id_for(t, loan_number):
    return t.find("waiver_requests", loan_number=loan_number)[0]["id"]


def test_small_waiver_goes_to_credit_officer(t):
    """A $100,000 waiver request is routed to a credit officer"""
    row = t.run("request_waiver", as_role="relationship_manager", borrower="Test Co",
                loan_number="LN-1", amount=100000, reason="Timing issue")
    assert row["status"] == "pending_credit_officer", row


def test_large_waiver_goes_to_chief_credit_officer(t):
    """A $300,000 waiver request is routed to the chief credit officer"""
    row = t.run("request_waiver", as_role="relationship_manager", borrower="Test Co",
                loan_number="LN-2", amount=300000, reason="Appraisal gap")
    assert row["status"] == "pending_chief_credit_officer", row


def test_only_relationship_managers_can_request(t):
    """A credit officer cannot submit a waiver request"""
    t.fails("request_waiver", as_role="credit_officer", borrower="Test Co",
            loan_number="LN-3", amount=1000, reason="x")


def test_credit_officer_approves_up_to_limit(t):
    """A credit officer can approve a waiver of exactly $250,000 and the requester is emailed"""
    rid = _id_for(t, "LN-10117")
    row = t.run("approve_waiver", as_role="credit_officer", as_user="d.nguyen@bank.example",
                request_id=rid)
    assert row["status"] == "approved", row
    assert row["decided_by"] == "d.nguyen@bank.example", row
    assert len(t.outbox) == 1, t.outbox


def test_credit_officer_cannot_approve_above_limit(t):
    """A credit officer cannot approve a $640,000 waiver"""
    rid = _id_for(t, "LN-10502")
    msg = t.fails("approve_waiver", as_role="credit_officer", as_user="d.nguyen@bank.example",
                  request_id=rid)
    assert "chief credit officer" in msg, msg
    assert t.get("waiver_requests", rid)["status"] == "pending_chief_credit_officer"


def test_chief_credit_officer_approves_large_waiver(t):
    """The chief credit officer can approve a $640,000 waiver"""
    rid = _id_for(t, "LN-10502")
    row = t.run("approve_waiver", as_role="chief_credit_officer", as_user="e.rossi@bank.example",
                request_id=rid, note="Adequate guarantor support")
    assert row["status"] == "approved", row


def test_cannot_decide_own_request(t):
    """An approver cannot approve a request they submitted themselves"""
    rid = _id_for(t, "LN-10421")
    t.fails("approve_waiver", as_role="credit_officer", as_user="a.patel@bank.example",
            request_id=rid)


def test_reject_needs_note_and_respects_limit(t):
    """Rejecting needs a written reason and follows the same approval limit"""
    small = _id_for(t, "LN-10388")
    large = _id_for(t, "LN-10502")
    t.fails("reject_waiver", as_role="credit_officer", as_user="d.nguyen@bank.example",
            request_id=small, note="  ")
    t.fails("reject_waiver", as_role="credit_officer", as_user="d.nguyen@bank.example",
            request_id=large, note="Too risky")
    row = t.run("reject_waiver", as_role="credit_officer", as_user="d.nguyen@bank.example",
                request_id=small, note="Payment history is weak")
    assert row["status"] == "rejected", row


def test_decided_request_cannot_be_decided_again(t):
    """A request that was already approved cannot be decided a second time"""
    rid = _id_for(t, "LN-09876")
    t.fails("reject_waiver", as_role="chief_credit_officer", as_user="e.rossi@bank.example",
            request_id=rid, note="Changed my mind")