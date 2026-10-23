import re
from kit import action, ActionError


def _open_loan(db, loan_id):
    loan = db.get("loans", loan_id)
    if loan["status"] == "paid_off":
        raise ActionError("This loan is paid off")
    return loan


@action(roles=["member", "credit_officer"])
def add_loan(db, user, borrower: str, principal: float, relationship_manager: str = None):
    """Add a loan. The relationship manager defaults to you."""
    if principal <= 0:
        raise ActionError("Principal must be more than 0")
    return db.insert("loans", borrower=borrower, principal=principal, status="active",
                     relationship_manager=(relationship_manager or user.email).lower())


@action(roles=["member", "credit_officer"])
def add_covenant(db, user, loan_id: int, covenant_type: str, direction: str,
                 threshold: float, frequency: str = "quarterly"):
    """Add a covenant to a loan."""
    _open_loan(db, loan_id)
    if threshold <= 0:
        raise ActionError("Threshold must be more than 0")
    return db.insert("covenants", loan_id=loan_id, covenant_type=covenant_type,
                     direction=direction, threshold=threshold, frequency=frequency,
                     status="compliant")


@action(roles=["member", "credit_officer"])
def record_covenant_test(db, user, covenant_id: int, period: str, actual_value: float):
    """Record a covenant test for a period like 2026-Q3. A failure marks the covenant breached and emails the relationship manager."""
    if not re.fullmatch(r"\d{4}-Q[1-4]", period):
        raise ActionError("Period must look like 2026-Q3")
    cov = db.get("covenants", covenant_id)
    loan = _open_loan(db, cov["loan_id"])
    if db.find("covenant_tests", covenant_id=covenant_id, period=period):
        raise ActionError(f"This covenant has already been tested for {period}")

    if cov["direction"] == "minimum":
        passed = actual_value >= cov["threshold"]
    else:
        passed = actual_value <= cov["threshold"]

    test = db.insert("covenant_tests", covenant_id=covenant_id, period=period,
                     test_date=db.today(), actual_value=actual_value,
                     result="passed" if passed else "failed")
    db.update("covenants", covenant_id, status="compliant" if passed else "breached")

    if not passed:
        db.notify(
            loan["relationship_manager"],
            f"Covenant breach: {loan['borrower']}",
            f"{loan['borrower']} failed its {cov['covenant_type']} covenant for {period}. "
            f"Actual {actual_value} vs {cov['direction']} {cov['threshold']}.")
    return test


@action(roles=["member", "credit_officer"])
def request_waiver(db, user, covenant_id: int, reason: str):
    """Ask credit to waive a breached covenant."""
    cov = db.get("covenants", covenant_id)
    if cov["status"] != "breached":
        raise ActionError("Only a breached covenant can be waived")
    if not reason.strip():
        raise ActionError("Give a reason for the waiver")
    if db.find("waivers", covenant_id=covenant_id, status="requested"):
        raise ActionError("A waiver request is already open for this covenant")
    return db.insert("waivers", covenant_id=covenant_id, reason=reason.strip(),
                     status="requested", requested_by=user.email)


@action(roles=["credit_officer"])
def approve_waiver(db, user, waiver_id: int):
    """Credit approves a waiver; the covenant becomes waived and the relationship manager is told."""
    waiver = db.get("waivers", waiver_id)
    if waiver["status"] != "requested":
        raise ActionError("This waiver has already been decided")
    if waiver["requested_by"] == user.email:
        raise ActionError("You can't approve a waiver you requested")
    cov = db.get("covenants", waiver["covenant_id"])
    if cov["status"] != "breached":
        raise ActionError("This covenant is no longer breached")
    loan = db.get("loans", cov["loan_id"])

    db.update("covenants", cov["id"], status="waived")
    row = db.update("waivers", waiver_id, status="approved",
                    decided_by=user.email, decided_on=db.today())
    db.notify(loan["relationship_manager"], f"Waiver approved: {loan['borrower']}",
              f"Credit approved a waiver of the {cov['covenant_type']} covenant for {loan['borrower']}.")
    return row


@action(roles=["credit_officer"])
def reject_waiver(db, user, waiver_id: int):
    """Credit rejects a waiver; the covenant stays breached."""
    waiver = db.get("waivers", waiver_id)
    if waiver["status"] != "requested":
        raise ActionError("This waiver has already been decided")
    if waiver["requested_by"] == user.email:
        raise ActionError("You can't reject a waiver you requested")
    row = db.update("waivers", waiver_id, status="rejected",
                    decided_by=user.email, decided_on=db.today())
    db.notify(waiver["requested_by"], "Waiver rejected",
              f"Your waiver request #{waiver_id} was rejected by credit.")
    return row


def test_failed_minimum_test_breaches_and_emails_rm(t):
    """A DSCR below the minimum marks the covenant breached and emails the relationship manager"""
    t.run("record_covenant_test", as_role="member", covenant_id=1, period="2026-Q3", actual_value=1.10)
    assert t.get("covenants", 1)["status"] == "breached"
    assert any(m["to"] == "j.okafor@bank.example" for m in t.outbox), t.outbox


def test_failed_maximum_test_breaches(t):
    """Leverage above the maximum counts as a failed test"""
    row = t.run("record_covenant_test", as_role="member", covenant_id=2, period="2026-Q3", actual_value=4.5)
    assert row["result"] == "failed", row
    assert t.get("covenants", 2)["status"] == "breached"


def test_passing_test_sends_no_email(t):
    """A test that exactly meets the threshold passes and nobody is emailed"""
    row = t.run("record_covenant_test", as_role="member", covenant_id=1, period="2026-Q3", actual_value=1.25)
    assert row["result"] == "passed", row
    assert t.get("covenants", 1)["status"] == "compliant"
    assert t.outbox == [], t.outbox


def test_passing_test_clears_breach(t):
    """A passing test on a breached covenant returns it to compliant"""
    t.run("record_covenant_test", as_role="member", covenant_id=3, period="2026-Q3", actual_value=1.35)
    assert t.get("covenants", 3)["status"] == "compliant"


def test_cannot_test_same_period_twice(t):
    """A covenant can't be tested twice for the same quarter"""
    msg = t.fails("record_covenant_test", as_role="member", covenant_id=1, period="2026-Q2", actual_value=1.5)
    assert "already" in msg, msg


def test_bad_period_refused(t):
    """A period that isn't a quarter like 2026-Q3 is refused"""
    t.fails("record_covenant_test", as_role="member", covenant_id=1, period="Q3 2026", actual_value=1.5)


def test_add_covenant_to_paid_off_loan_refused(t):
    """Covenants can't be added to a loan that is paid off"""
    loan = t.run("add_loan", as_role="member", borrower="Old Co", principal=1000)
    t.run("add_covenant", as_role="member", loan_id=loan["id"], covenant_type="min_dscr",
          direction="minimum", threshold=1.2)
    t.run("add_covenant", as_role="member", loan_id=4, covenant_type="min_liquidity",
          direction="minimum", threshold=250000)
    assert len(t.find("covenants", loan_id=4)) == 2


def test_waiver_only_for_breached_covenant(t):
    """You can't ask for a waiver on a covenant that isn't breached"""
    t.fails("request_waiver", as_role="member", covenant_id=1, reason="Just in case")


def test_only_one_open_waiver_per_covenant(t):
    """A second waiver request is refused while one is already waiting"""
    msg = t.fails("request_waiver", as_role="member", covenant_id=3, reason="Again")
    assert "already open" in msg, msg


def test_request_waiver_after_breach(t):
    """After a failed test, the relationship manager can request a waiver"""
    t.run("record_covenant_test", as_role="member", covenant_id=1, period="2026-Q3", actual_value=1.0)
    w = t.run("request_waiver", as_role="member", as_user="j.okafor@bank.example",
              covenant_id=1, reason="One-off repair costs")
    assert w["status"] == "requested" and w["requested_by"] == "j.okafor@bank.example", w


def test_credit_approval_waives_covenant(t):
    """When credit approves a waiver the covenant becomes waived and the relationship manager is emailed"""
    w = t.run("approve_waiver", as_role="credit_officer", as_user="c.brandt@bank.example", waiver_id=2)
    assert w["status"] == "approved" and w["decided_by"] == "c.brandt@bank.example", w
    assert t.get("covenants", 3)["status"] == "waived"
    assert any(m["to"] == "d.nguyen@bank.example" for m in t.outbox), t.outbox


def test_member_cannot_approve_waiver(t):
    """Someone without credit approval authority can't approve a waiver"""
    t.fails("approve_waiver", as_role="member", waiver_id=2)
    assert t.get("covenants", 3)["status"] == "breached"


def test_cannot_approve_own_waiver_request(t):
    """A credit officer can't approve a waiver they asked for themselves"""
    msg = t.fails("approve_waiver", as_role="credit_officer", as_user="d.nguyen@bank.example", waiver_id=2)
    assert "requested" in msg, msg


def test_rejected_waiver_keeps_breach(t):
    """Rejecting a waiver leaves the covenant breached"""
    w = t.run("reject_waiver", as_role="credit_officer", as_user="c.brandt@bank.example", waiver_id=2)
    assert w["status"] == "rejected", w
    assert t.get("covenants", 3)["status"] == "breached"


def test_decided_waiver_cannot_be_decided_again(t):
    """A waiver that was already approved can't be approved again"""
    t.fails("approve_waiver", as_role="credit_officer", as_user="c.brandt@bank.example", waiver_id=1)