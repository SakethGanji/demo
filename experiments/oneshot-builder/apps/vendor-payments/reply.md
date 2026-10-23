Assumed: roles are member (adds invoices, records payments) and manager (approves or rejects invoices, blocks and unblocks vendors, edits the vendor list). Invoices over $5,000 need manager approval before any payment. A manager can't approve an invoice they submitted themselves. Payments can't exceed the invoice balance. Invoice numbers are unique per vendor. Blocking a vendor doesn't affect invoices already on file.

```json file="schema.json"
{
  "roles": ["member", "manager"],
  "tables": [
    {"name": "vendors",
     "edit_roles": ["manager"],
     "columns": [
       {"name": "name", "type": "text", "required": true},
       {"name": "status", "type": "choice", "choices": ["active", "blocked"], "required": true, "locked": true, "default": "active"},
       {"name": "contact_email", "type": "email"}
     ],
     "sample_rows": [
       {"name": "Iron Mountain", "status": "active", "contact_email": "billing@ironmountain.example"},
       {"name": "Staples Business", "status": "active", "contact_email": "ar@staples.example"},
       {"name": "Acme Security", "status": "active", "contact_email": "accounts@acmesecurity.example"},
       {"name": "Shadow Consulting LLC", "status": "blocked", "contact_email": "info@shadowconsulting.example"},
       {"name": "Metro Cleaning", "status": "active", "contact_email": "office@metrocleaning.example"}
     ]},
    {"name": "invoices",
     "edit_roles": [],
     "columns": [
       {"name": "vendor_id", "type": "ref", "table": "vendors", "required": true, "locked": true},
       {"name": "invoice_number", "type": "text", "required": true, "locked": true},
       {"name": "amount", "type": "money", "required": true, "locked": true},
       {"name": "amount_paid", "type": "money", "required": true, "locked": true, "default": 0},
       {"name": "status", "type": "choice", "choices": ["needs_approval", "ready_to_pay", "partially_paid", "paid", "rejected"], "required": true, "locked": true},
       {"name": "submitter_email", "type": "email", "locked": true},
       {"name": "approved_by", "type": "person", "locked": true}
     ],
     "sample_rows": [
       {"vendor_id": 1, "invoice_number": "INV-1001", "amount": 12400, "amount_paid": 0, "status": "needs_approval", "submitter_email": "r.alvarez@bank.example"},
       {"vendor_id": 2, "invoice_number": "INV-2040", "amount": 860, "amount_paid": 300, "status": "partially_paid", "submitter_email": "r.alvarez@bank.example"},
       {"vendor_id": 3, "invoice_number": "INV-311", "amount": 7200, "amount_paid": 3000, "status": "partially_paid", "submitter_email": "j.okafor@bank.example", "approved_by": "m.chen@bank.example"},
       {"vendor_id": 5, "invoice_number": "INV-77", "amount": 450, "amount_paid": 450, "status": "paid", "submitter_email": "j.okafor@bank.example"},
       {"vendor_id": 1, "invoice_number": "INV-1002", "amount": 2300, "amount_paid": 0, "status": "ready_to_pay", "submitter_email": "r.alvarez@bank.example"},
       {"vendor_id": 3, "invoice_number": "INV-312", "amount": 9800, "amount_paid": 0, "status": "rejected", "submitter_email": "j.okafor@bank.example"}
     ]},
    {"name": "payments",
     "edit_roles": [],
     "columns": [
       {"name": "invoice_id", "type": "ref", "table": "invoices", "required": true, "locked": true},
       {"name": "amount", "type": "money", "required": true, "locked": true},
       {"name": "paid_on", "type": "date", "required": true, "locked": true},
       {"name": "paid_by", "type": "person", "locked": true}
     ],
     "sample_rows": [
       {"invoice_id": 2, "amount": 300, "paid_on": "2026-09-30", "paid_by": "j.okafor@bank.example"},
       {"invoice_id": 3, "amount": 2000, "paid_on": "2026-09-15", "paid_by": "r.alvarez@bank.example"},
       {"invoice_id": 3, "amount": 1000, "paid_on": "2026-10-05", "paid_by": "r.alvarez@bank.example"},
       {"invoice_id": 4, "amount": 450, "paid_on": "2026-10-01", "paid_by": "j.okafor@bank.example"}
     ]}
  ],
  "queries": [
    {"name": "open_balance_by_vendor",
     "sql": "SELECT v.name AS vendor, SUM(i.amount - i.amount_paid) AS balance FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.status IN ('needs_approval', 'ready_to_pay', 'partially_paid') GROUP BY v.name ORDER BY balance DESC"},
    {"name": "awaiting_my_approval",
     "sql": "SELECT i.id, v.name AS vendor, i.invoice_number, i.amount, i.submitter_email FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.status = 'needs_approval' AND COALESCE(i.submitter_email, '') != :me ORDER BY i.created_at"},
    {"name": "payments_by_invoice",
     "sql": "SELECT i.invoice_number, p.amount, p.paid_on, p.paid_by FROM payments p JOIN invoices i ON i.id = p.invoice_id ORDER BY p.paid_on DESC"}
  ]
}
```

```python file="actions.py"
from decimal import Decimal
from kit import action, ActionError

APPROVAL_LIMIT = Decimal("5000")


def _cents(x):
    return Decimal(str(x)).quantize(Decimal("0.01"))


@action(roles=["manager"])
def block_vendor(db, user, vendor_id: int):
    """Block a vendor so no new invoices can be added for them."""
    vendor = db.get("vendors", vendor_id)
    if vendor["status"] == "blocked":
        raise ActionError("This vendor is already blocked")
    return db.update("vendors", vendor_id, status="blocked")


@action(roles=["manager"])
def unblock_vendor(db, user, vendor_id: int):
    """Make a blocked vendor active again."""
    vendor = db.get("vendors", vendor_id)
    if vendor["status"] == "active":
        raise ActionError("This vendor is already active")
    return db.update("vendors", vendor_id, status="active")


@action(roles=["member", "manager"])
def add_invoice(db, user, vendor_id: int, invoice_number: str, amount: float, submitter_email: str = None):
    """Add an invoice for an active vendor. Over $5,000 needs manager approval."""
    vendor = db.get("vendors", vendor_id)
    if vendor["status"] == "blocked":
        raise ActionError("Vendor is blocked, so invoices can't be added for them")
    amt = _cents(amount)
    if amt <= 0:
        raise ActionError("Amount must be more than 0")
    if db.find("invoices", vendor_id=vendor_id, invoice_number=invoice_number):
        raise ActionError("This vendor already has an invoice with that number")
    status = "needs_approval" if amt > APPROVAL_LIMIT else "ready_to_pay"
    return db.insert("invoices", vendor_id=vendor_id, invoice_number=invoice_number,
                     amount=float(amt), amount_paid=0, status=status,
                     submitter_email=(submitter_email or user.email).lower())


@action(roles=["manager"])
def approve_invoice(db, user, invoice_id: int):
    """Approve an invoice that is waiting for manager approval so it can be paid."""
    inv = db.get("invoices", invoice_id)
    if inv["status"] != "needs_approval":
        raise ActionError("Only invoices waiting for approval can be approved")
    if (inv.get("submitter_email") or "").lower() == user.email:
        raise ActionError("You can't approve an invoice you submitted yourself")
    row = db.update("invoices", invoice_id, status="ready_to_pay", approved_by=user.email)
    if inv.get("submitter_email"):
        db.notify(inv["submitter_email"], "Invoice approved",
                  "Invoice %s was approved and is ready to pay." % inv["invoice_number"])
    return row


@action(roles=["manager"])
def reject_invoice(db, user, invoice_id: int, reason: str):
    """Reject an invoice that is waiting for manager approval."""
    inv = db.get("invoices", invoice_id)
    if inv["status"] != "needs_approval":
        raise ActionError("Only invoices waiting for approval can be rejected")
    if not reason or not reason.strip():
        raise ActionError("Please give a reason for rejecting")
    row = db.update("invoices", invoice_id, status="rejected")
    if inv.get("submitter_email"):
        db.notify(inv["submitter_email"], "Invoice rejected",
                  "Invoice %s was rejected: %s" % (inv["invoice_number"], reason.strip()))
    return row


@action(roles=["member", "manager"])
def record_payment(db, user, invoice_id: int, amount: float, paid_on: str = None):
    """Record a full or partial payment against an approved invoice."""
    inv = db.get("invoices", invoice_id)
    if inv["status"] == "needs_approval":
        raise ActionError("This invoice needs manager approval before it can be paid")
    if inv["status"] == "rejected":
        raise ActionError("This invoice was rejected and can't be paid")
    if inv["status"] == "paid":
        raise ActionError("This invoice is already paid")
    amt = _cents(amount)
    if amt <= 0:
        raise ActionError("Payment must be more than 0")
    paid_so_far = sum((_cents(p["amount"]) for p in db.find("payments", invoice_id=invoice_id)), Decimal("0"))
    balance = _cents(inv["amount"]) - paid_so_far
    if amt > balance:
        raise ActionError("Payment is more than the remaining balance of %s" % balance)
    db.insert("payments", invoice_id=invoice_id, amount=float(amt),
              paid_on=paid_on or db.today(), paid_by=user.email)
    total = paid_so_far + amt
    status = "paid" if total >= _cents(inv["amount"]) else "partially_paid"
    return db.update("invoices", invoice_id, amount_paid=float(total), status=status)


def test_large_invoice_needs_approval(t):
    """Adding a $12,400 invoice sets it to needs approval"""
    row = t.run("add_invoice", as_role="member", vendor_id=1, invoice_number="INV-2000", amount=12400)
    assert row["status"] == "needs_approval", row


def test_small_invoice_ready_to_pay(t):
    """Adding a $900 invoice makes it ready to pay straight away"""
    row = t.run("add_invoice", as_role="member", vendor_id=2, invoice_number="INV-2041", amount=900)
    assert row["status"] == "ready_to_pay", row


def test_blocked_vendor_invoice_refused(t):
    """An invoice can't be added for a blocked vendor"""
    msg = t.fails("add_invoice", as_role="member", vendor_id=4, invoice_number="X-1", amount=100)
    assert "blocked" in msg.lower(), msg


def test_duplicate_invoice_number_refused(t):
    """The same invoice number can't be entered twice for one vendor"""
    t.fails("add_invoice", as_role="member", vendor_id=1, invoice_number="INV-1001", amount=100)


def test_block_then_unblock_vendor(t):
    """A manager can block a vendor and later unblock them"""
    t.run("block_vendor", as_role="manager", vendor_id=2)
    assert t.get("vendors", 2)["status"] == "blocked"
    t.fails("add_invoice", as_role="member", vendor_id=2, invoice_number="Z-1", amount=50)
    t.run("unblock_vendor", as_role="manager", vendor_id=2)
    assert t.get("vendors", 2)["status"] == "active"


def test_member_cannot_block_vendor(t):
    """A regular member can't block a vendor"""
    t.fails("block_vendor", as_role="member", vendor_id=1)


def test_manager_approves_and_submitter_notified(t):
    """A manager approving an invoice makes it ready to pay and emails the submitter"""
    row = t.run("approve_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=1)
    assert row["status"] == "ready_to_pay", row
    assert row["approved_by"] == "m.chen@bank.example", row
    assert len(t.outbox) == 1 and t.outbox[0]["to"] == "r.alvarez@bank.example", t.outbox


def test_cannot_approve_own_invoice(t):
    """A manager can't approve an invoice they submitted themselves"""
    msg = t.fails("approve_invoice", as_role="manager", as_user="r.alvarez@bank.example", invoice_id=1)
    assert "yourself" in msg, msg


def test_member_cannot_approve(t):
    """A regular member can't approve an invoice"""
    t.fails("approve_invoice", as_role="member", invoice_id=1)


def test_reject_needs_reason(t):
    """Rejecting an invoice requires a reason, and then marks it rejected"""
    t.fails("reject_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=1, reason=" ")
    row = t.run("reject_invoice", as_role="manager", as_user="m.chen@bank.example", invoice_id=1, reason="Duplicate billing")
    assert row["status"] == "rejected", row


def test_unapproved_invoice_cannot_be_paid(t):
    """An invoice over $5,000 can't be paid until a manager approves it"""
    msg = t.fails("record_payment", as_role="member", invoice_id=1, amount=500)
    assert "approval" in msg.lower(), msg


def test_rejected_invoice_cannot_be_paid(t):
    """A rejected invoice can't be paid"""
    t.fails("record_payment", as_role="member", invoice_id=6, amount=100)


def test_partial_payment_keeps_invoice_open(t):
    """Paying part of an approved invoice leaves it partially paid"""
    row = t.run("record_payment", as_role="member", invoice_id=5, amount=1000)
    assert row["status"] == "partially_paid", row
    assert row["amount_paid"] == 1000, row


def test_payments_cover_invoice_marks_paid(t):
    """Once payments add up to the invoice amount, it becomes paid"""
    t.run("record_payment", as_role="member", invoice_id=5, amount=1000)
    row = t.run("record_payment", as_role="member", invoice_id=5, amount=1300)
    assert row["status"] == "paid", row
    assert len(t.find("payments", invoice_id=5)) == 2


def test_overpayment_refused(t):
    """A payment larger than the remaining balance is refused"""
    t.fails("record_payment", as_role="member", invoice_id=2, amount=600)


def test_paid_invoice_refuses_more_payments(t):
    """An invoice that's already paid can't take another payment"""
    t.fails("record_payment", as_role="member", invoice_id=4, amount=10)
```
