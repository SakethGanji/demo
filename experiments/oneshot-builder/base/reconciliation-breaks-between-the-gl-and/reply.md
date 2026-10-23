Assumed: roles are member and controller. Break statuses are open, assigned, pending_writeoff, resolved and written_off. The break amount is GL minus core. Write-offs of $1,000 or less go through immediately, and anything larger needs a controller other than the requester. Only the assignee or a controller can resolve or write off a break.

```json file="schema.json"
{
  "roles": ["member", "controller"],
  "tables": [
    {"name": "breaks",
     "columns": [
       {"name": "reference", "type": "text", "required": true},
       {"name": "break_date", "type": "date", "required": true},
       {"name": "gl_amount", "type": "money", "required": true},
       {"name": "core_amount", "type": "money", "required": true},
       {"name": "difference", "type": "money", "required": true},
       {"name": "status", "type": "choice", "choices": ["open", "assigned", "pending_writeoff", "resolved", "written_off"], "required": true},
       {"name": "assignee", "type": "person"},
       {"name": "resolution_reason", "type": "text"},
       {"name": "writeoff_requested_by", "type": "person"},
       {"name": "writeoff_approved_by", "type": "person"}
     ],
     "sample_rows": [
       {"reference": "ACH-20931", "break_date": "2026-10-15", "gl_amount": 4200, "core_amount": 0, "difference": 4200, "status": "open"},
       {"reference": "WIRE-88213", "break_date": "2026-10-14", "gl_amount": 15000, "core_amount": 14250, "difference": 750, "status": "assigned", "assignee": "j.park@bank.example"},
       {"reference": "CHK-55120", "break_date": "2026-10-19", "gl_amount": 980.5, "core_amount": 980, "difference": 0.5, "status": "open"},
       {"reference": "FEE-1009", "break_date": "2026-10-08", "gl_amount": 1500, "core_amount": 2600, "difference": -1100, "status": "pending_writeoff", "assignee": "t.nguyen@bank.example", "resolution_reason": "Fee waived in core, never reversed in GL", "writeoff_requested_by": "t.nguyen@bank.example"},
       {"reference": "ATM-7731", "break_date": "2026-10-10", "gl_amount": 320, "core_amount": 0, "difference": 320, "status": "resolved", "assignee": "j.park@bank.example", "resolution_reason": "Timing difference; posted in core on 10/13"},
       {"reference": "INT-4420", "break_date": "2026-10-05", "gl_amount": 60, "core_amount": 55, "difference": 5, "status": "written_off", "assignee": "m.chen@bank.example", "resolution_reason": "Rounding difference on interest accrual", "writeoff_requested_by": "m.chen@bank.example"},
       {"reference": "ACH-20977", "break_date": "2026-10-16", "gl_amount": 8800, "core_amount": 0, "difference": 8800, "status": "assigned", "assignee": "m.chen@bank.example"}
     ]}
  ],
  "queries": [
    {"name": "open_breaks_by_age", "sql": "SELECT reference, difference, status, assignee, CAST(julianday(:today) - julianday(break_date) AS INTEGER) AS age_days FROM breaks WHERE status IN ('open', 'assigned', 'pending_writeoff') ORDER BY age_days DESC"},
    {"name": "my_breaks", "sql": "SELECT reference, difference, status, break_date FROM breaks WHERE assignee = :me AND status IN ('assigned', 'pending_writeoff') ORDER BY break_date"},
    {"name": "pending_writeoffs", "sql": "SELECT reference, difference, resolution_reason, writeoff_requested_by FROM breaks WHERE status = 'pending_writeoff' ORDER BY ABS(difference) DESC"},
    {"name": "unassigned_breaks", "sql": "SELECT reference, difference, break_date FROM breaks WHERE status = 'open' ORDER BY break_date"}
  ]
}
```

```python file="actions.py"
from kit import action, ActionError

WRITEOFF_LIMIT = 1000


def _can_work(row, user):
    return user.role == "controller" or row["assignee"] == user.email


@action(roles=["member", "controller"])
def add_break(db, user, reference: str, break_date: str, gl_amount: float, core_amount: float):
    """Log a new break between the GL and the core banking system."""
    if not reference.strip():
        raise ActionError("Reference is required")
    difference = round(gl_amount - core_amount, 2)
    if difference == 0:
        raise ActionError("GL and core amounts match, so there is no break to log")
    return db.insert("breaks", reference=reference.strip(), break_date=break_date,
                     gl_amount=gl_amount, core_amount=core_amount,
                     difference=difference, status="open")


@action(roles=["member", "controller"])
def assign_break(db, user, break_id: int, assignee_email: str):
    """Assign an open break to someone to investigate."""
    row = db.get("breaks", break_id)
    if row["status"] not in ("open", "assigned"):
        raise ActionError("Only open or assigned breaks can be assigned")
    email = assignee_email.strip().lower()
    if "@" not in email:
        raise ActionError("Enter a valid email address for the assignee")
    row = db.update("breaks", break_id, assignee=email, status="assigned")
    db.notify(email, "Break assigned: " + row["reference"],
              "You have been assigned break %s (difference %s)." % (row["reference"], row["difference"]))
    return row


@action(roles=["member", "controller"])
def resolve_break(db, user, break_id: int, reason: str):
    """Resolve an assigned break and record why."""
    row = db.get("breaks", break_id)
    if row["status"] != "assigned":
        raise ActionError("Only assigned breaks can be resolved")
    if not _can_work(row, user):
        raise ActionError("Only the assignee or a controller can resolve this break")
    if len(reason.strip()) < 5:
        raise ActionError("A reason is required to resolve a break")
    return db.update("breaks", break_id, status="resolved", resolution_reason=reason.strip())


@action(roles=["member", "controller"])
def request_writeoff(db, user, break_id: int, reason: str):
    """Write off a break. Over $1,000 needs controller approval."""
    row = db.get("breaks", break_id)
    if row["status"] != "assigned":
        raise ActionError("Only assigned breaks can be written off")
    if not _can_work(row, user):
        raise ActionError("Only the assignee or a controller can write off this break")
    if len(reason.strip()) < 5:
        raise ActionError("A reason is required to write off a break")
    if abs(row["difference"]) > WRITEOFF_LIMIT:
        return db.update("breaks", break_id, status="pending_writeoff",
                         resolution_reason=reason.strip(),
                         writeoff_requested_by=user.email)
    return db.update("breaks", break_id, status="written_off",
                     resolution_reason=reason.strip(),
                     writeoff_requested_by=user.email)


@action(roles=["controller"])
def approve_writeoff(db, user, break_id: int):
    """Approve a pending write-off as controller."""
    row = db.get("breaks", break_id)
    if row["status"] != "pending_writeoff":
        raise ActionError("This break is not waiting for write-off approval")
    if row["writeoff_requested_by"] == user.email:
        raise ActionError("You cannot approve a write-off you requested")
    row = db.update("breaks", break_id, status="written_off", writeoff_approved_by=user.email)
    if row["writeoff_requested_by"]:
        db.notify(row["writeoff_requested_by"], "Write-off approved: " + row["reference"],
                  "The write-off for break %s was approved." % row["reference"])
    return row


@action(roles=["controller"])
def reject_writeoff(db, user, break_id: int, reason: str):
    """Reject a pending write-off and send the break back to its assignee."""
    row = db.get("breaks", break_id)
    if row["status"] != "pending_writeoff":
        raise ActionError("This break is not waiting for write-off approval")
    if len(reason.strip()) < 5:
        raise ActionError("A reason is required to reject a write-off")
    requester = row["writeoff_requested_by"]
    row = db.update("breaks", break_id, status="assigned",
                    resolution_reason="Write-off rejected: " + reason.strip(),
                    writeoff_requested_by=None)
    if requester:
        db.notify(requester, "Write-off rejected: " + row["reference"], reason.strip())
    return row


def _id(t, reference):
    return t.find("breaks", reference=reference)[0]["id"]


def test_add_break_computes_difference(t):
    """Logging a break with GL 5,000 and core 4,200 records an 800 difference"""
    row = t.run("add_break", as_role="member", reference="WIRE-1", break_date="2026-10-20",
                gl_amount=5000, core_amount=4200)
    assert row["difference"] == 800, row
    assert row["status"] == "open", row


def test_add_break_refuses_matching_amounts(t):
    """A break cannot be logged when the GL and core amounts agree"""
    t.fails("add_break", as_role="member", reference="WIRE-2", break_date="2026-10-20",
            gl_amount=100, core_amount=100)


def test_assign_break_notifies_assignee(t):
    """Assigning a break marks it assigned and emails the assignee"""
    row = t.run("assign_break", as_role="member", break_id=_id(t, "ACH-20931"),
                assignee_email="J.Park@bank.example")
    assert row["status"] == "assigned", row
    assert row["assignee"] == "j.park@bank.example", row
    assert len(t.outbox) == 1, t.outbox


def test_cannot_assign_resolved_break(t):
    """A resolved break cannot be assigned again"""
    t.fails("assign_break", as_role="member", break_id=_id(t, "ATM-7731"),
            assignee_email="j.park@bank.example")


def test_assignee_resolves_with_reason(t):
    """The assignee can resolve a break by giving a reason"""
    row = t.run("resolve_break", as_role="member", as_user="j.park@bank.example",
                break_id=_id(t, "WIRE-88213"), reason="Core posting delayed one day")
    assert row["status"] == "resolved", row
    assert row["resolution_reason"] == "Core posting delayed one day", row


def test_resolve_requires_reason_and_assignee(t):
    """Resolving needs a reason and only the assignee or a controller may do it"""
    bid = _id(t, "WIRE-88213")
    t.fails("resolve_break", as_role="member", as_user="j.park@bank.example", break_id=bid, reason="")
    t.fails("resolve_break", as_role="member", as_user="m.chen@bank.example", break_id=bid,
            reason="Not my break but resolving")


def test_small_writeoff_is_immediate(t):
    """A write-off of $1,000 or less is completed without approval"""
    row = t.run("request_writeoff", as_role="member", as_user="j.park@bank.example",
                break_id=_id(t, "WIRE-88213"), reason="Immaterial FX rounding")
    assert row["status"] == "written_off", row


def test_large_writeoff_needs_controller(t):
    """A write-off over $1,000 waits for controller approval"""
    row = t.run("request_writeoff", as_role="member", as_user="m.chen@bank.example",
                break_id=_id(t, "ACH-20977"), reason="Duplicate vendor credit, unrecoverable")
    assert row["status"] == "pending_writeoff", row
    assert row["writeoff_requested_by"] == "m.chen@bank.example", row


def test_controller_approves_writeoff(t):
    """A controller can approve a pending write-off and the requester is told"""
    row = t.run("approve_writeoff", as_role="controller", as_user="c.owens@bank.example",
                break_id=_id(t, "FEE-1009"))
    assert row["status"] == "written_off", row
    assert row["writeoff_approved_by"] == "c.owens@bank.example", row
    assert len(t.outbox) == 1, t.outbox


def test_members_and_requesters_cannot_approve(t):
    """Members cannot approve write-offs and controllers cannot approve their own"""
    bid = _id(t, "FEE-1009")
    t.fails("approve_writeoff", as_role="member", break_id=bid)
    t.fails("approve_writeoff", as_role="controller", as_user="t.nguyen@bank.example", break_id=bid)


def test_reject_writeoff_returns_to_assignee(t):
    """Rejecting a write-off sends the break back to assigned with the reason"""
    row = t.run("reject_writeoff", as_role="controller", as_user="c.owens@bank.example",
                break_id=_id(t, "FEE-1009"), reason="Reverse the fee in GL instead")
    assert row["status"] == "assigned", row
    assert "Reverse the fee" in row["resolution_reason"], row
    t.fails("reject_writeoff", as_role="controller", as_user="c.owens@bank.example",
            break_id=_id(t, "ACH-20931"), reason="Not pending")
```
