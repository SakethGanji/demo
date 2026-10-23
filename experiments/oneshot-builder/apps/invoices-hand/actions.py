from kit import action, ActionError

APPROVAL_LIMIT = 5000


@action(roles=["ap", "manager"])
def add_invoice(db, user, vendor: str, amount: float, due_date: str = None, submitter_email: str = None):
    """Add an invoice. Over $5,000 needs a manager's approval before it can be paid."""
    if not vendor or not vendor.strip():
        raise ActionError("Vendor is required")
    if amount <= 0:
        raise ActionError("Amount must be more than 0")
    status = "needs_approval" if amount > APPROVAL_LIMIT else "ready_to_pay"
    return db.insert("invoices", vendor=vendor.strip(), amount=amount, status=status,
                     due_date=due_date,
                     submitter_email=(submitter_email or user.email).lower())


@action(roles=["manager"])
def approve_invoice(db, user, invoice_id: int):
    """Approve an invoice that is waiting for approval so it can be paid."""
    inv = db.get("invoices", invoice_id)
    if inv["status"] != "needs_approval":
        raise ActionError("Only invoices waiting for approval can be approved")
    if inv["submitter_email"] == user.email:
        raise ActionError("You can't approve an invoice you submitted")
    return db.update("invoices", invoice_id, status="ready_to_pay", approved_by=user.email)


@action(roles=["manager"])
def reject_invoice(db, user, invoice_id: int, reason: str):
    """Reject an invoice that is waiting for approval and email the submitter."""
    if not reason or not reason.strip():
        raise ActionError("Please give a reason for rejecting")
    inv = db.get("invoices", invoice_id)
    if inv["status"] != "needs_approval":
        raise ActionError("Only invoices waiting for approval can be rejected")
    if inv["submitter_email"] == user.email:
        raise ActionError("You can't reject an invoice you submitted")
    row = db.update("invoices", invoice_id, status="rejected",
                    approved_by=user.email, rejection_reason=reason.strip())
    db.notify(inv["submitter_email"],
              "Invoice rejected: " + inv["vendor"],
              "Your invoice from %s for $%.2f was rejected by %s.\n\nReason: %s"
              % (inv["vendor"], inv["amount"], user.email, reason.strip()))
    return row


@action(roles=["ap", "manager"])
def mark_paid(db, user, invoice_id: int):
    """Record that an invoice has been paid. Only invoices that are ready to pay can be paid."""
    inv = db.get("invoices", invoice_id)
    if inv["status"] != "ready_to_pay":
        raise ActionError("Only invoices that are ready to pay can be marked paid")
    return db.update("invoices", invoice_id, status="paid", paid_on=db.today())


def test_large_invoice_needs_approval(t):
    """Adding a $12,400 invoice sets it to needs approval"""
    row = t.run("add_invoice", as_role="ap", vendor="Iron Mountain", amount=12400)
    assert row["status"] == "needs_approval", row


def test_small_and_exactly_limit_invoices_are_ready(t):
    """Invoices of $5,000 or less can be paid without approval"""
    small = t.run("add_invoice", as_role="ap", vendor="Cintas", amount=300)
    edge = t.run("add_invoice", as_role="ap", vendor="Cintas", amount=5000)
    assert small["status"] == "ready_to_pay", small
    assert edge["status"] == "ready_to_pay", edge


def test_submitter_defaults_to_current_user(t):
    """A new invoice is credited to the person who entered it"""
    row = t.run("add_invoice", as_role="ap", as_user="R.Alvarez@bank.example", vendor="Cintas", amount=100)
    assert row["submitter_email"] == "r.alvarez@bank.example", row


def test_add_refuses_zero_amount(t):
    """An invoice with no amount is refused"""
    msg = t.fails("add_invoice", as_role="ap", vendor="Cintas", amount=0)
    assert "more than 0" in msg, msg


def test_manager_approves_invoice(t):
    """A manager can approve an invoice so it becomes ready to pay"""
    inv = t.find("invoices", status="needs_approval")[0]
    row = t.run("approve_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=inv["id"])
    assert row["status"] == "ready_to_pay", row
    assert row["approved_by"] == "m.chen@bank.example", row


def test_ap_cannot_approve(t):
    """An AP clerk is not allowed to approve invoices"""
    inv = t.find("invoices", status="needs_approval")[0]
    t.fails("approve_invoice", as_role="ap", invoice_id=inv["id"])
    assert t.get("invoices", inv["id"])["status"] == "needs_approval"


def test_manager_cannot_approve_own_invoice(t):
    """A manager can't approve an invoice they submitted themselves"""
    row = t.run("add_invoice", as_role="manager", as_user="m.chen@bank.example", vendor="Dell", amount=9000)
    msg = t.fails("approve_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=row["id"])
    assert "submitted" in msg, msg


def test_cannot_approve_twice(t):
    """An invoice that is already ready to pay can't be approved again"""
    inv = t.find("invoices", status="ready_to_pay")[0]
    t.fails("approve_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=inv["id"])


def test_reject_emails_submitter(t):
    """Rejecting an invoice emails the person who submitted it"""
    inv = t.find("invoices", status="needs_approval", submitter_email="r.alvarez@bank.example")[0]
    row = t.run("reject_invoice", as_role="manager", as_user="m.chen@bank.example",
                invoice_id=inv["id"], reason="Duplicate of an earlier invoice")
    assert row["status"] == "rejected", row
    assert len(t.outbox) == 1, t.outbox
    sent = str(t.outbox[0])
    assert "r.alvarez@bank.example" in sent, sent
    assert "Duplicate of an earlier invoice" in sent, sent


def test_reject_needs_reason(t):
    """A manager must give a reason when rejecting an invoice"""
    inv = t.find("invoices", status="needs_approval")[0]
    t.fails("reject_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=inv["id"], reason="  ")
    assert t.outbox == [], t.outbox


def test_cannot_reject_paid_invoice(t):
    """An invoice that has already been paid can't be rejected"""
    inv = t.find("invoices", status="paid")[0]
    t.fails("reject_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=inv["id"], reason="Too late")


def test_mark_paid_ready_invoice(t):
    """An invoice that is ready to pay can be marked paid"""
    inv = t.find("invoices", status="ready_to_pay")[0]
    row = t.run("mark_paid", as_role="ap", invoice_id=inv["id"])
    assert row["status"] == "paid", row
    assert row["paid_on"], row


def test_cannot_pay_unapproved_invoice(t):
    """An invoice still waiting for approval can't be paid"""
    inv = t.find("invoices", status="needs_approval")[0]
    msg = t.fails("mark_paid", as_role="ap", invoice_id=inv["id"])
    assert "ready to pay" in msg, msg