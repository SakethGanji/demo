Assumed: roles are agent (logs, responds, closes their own) and supervisor (also assigns, and can respond or close any complaint). SLA is 5 business days (Mon–Fri, no bank holidays) from the received date, and the due date is stored when the complaint is logged. Statuses run new → assigned → responded → closed. A complaint breaches if it has no response by its due date, or if it was answered after it.

```json file="schema.json"
{
  "roles": ["agent", "supervisor"],
  "tables": [
    {"name": "complaints",
     "columns": [
       {"name": "customer_name", "type": "text", "required": true},
       {"name": "customer_email", "type": "email"},
       {"name": "channel", "type": "choice", "choices": ["phone", "email", "branch", "web", "letter"], "required": true},
       {"name": "summary", "type": "text", "required": true},
       {"name": "status", "type": "choice", "choices": ["new", "assigned", "responded", "closed"], "required": true},
       {"name": "assignee", "type": "person"},
       {"name": "received_on", "type": "date", "required": true},
       {"name": "sla_due", "type": "date", "required": true},
       {"name": "responded_at", "type": "datetime"},
       {"name": "response_note", "type": "text"},
       {"name": "closed_at", "type": "datetime"}
     ],
     "sample_rows": [
       {"customer_name": "Dana Whitfield", "customer_email": "dana.whitfield@example.com", "channel": "phone", "summary": "Overdraft fee charged twice on the same day", "status": "assigned", "assignee": "j.okafor@bank.example", "received_on": "2026-10-12", "sla_due": "2026-10-19"},
       {"customer_name": "Priya Raman", "customer_email": "priya.raman@example.com", "channel": "email", "summary": "Card declined abroad despite travel notice", "status": "new", "received_on": "2026-10-14", "sla_due": "2026-10-21"},
       {"customer_name": "Tom Hargreaves", "customer_email": "t.hargreaves@example.com", "channel": "branch", "summary": "Mortgage statement shows wrong balance", "status": "assigned", "assignee": "m.chen@bank.example", "received_on": "2026-10-19", "sla_due": "2026-10-26"},
       {"customer_name": "Lucia Moretti", "customer_email": "lucia.moretti@example.com", "channel": "web", "summary": "Online banking locked after password reset", "status": "new", "received_on": "2026-10-20", "sla_due": "2026-10-27"},
       {"customer_name": "Samuel Adeyemi", "customer_email": "s.adeyemi@example.com", "channel": "letter", "summary": "Unauthorised direct debit from savings account", "status": "responded", "assignee": "j.okafor@bank.example", "received_on": "2026-10-05", "sla_due": "2026-10-12", "responded_at": "2026-10-09T14:30:00", "response_note": "Refund issued and direct debit mandate cancelled."},
       {"customer_name": "Helen Park", "customer_email": "helen.park@example.com", "channel": "phone", "summary": "Branch closed early, could not deposit cheque", "status": "closed", "assignee": "m.chen@bank.example", "received_on": "2026-09-28", "sla_due": "2026-10-05", "responded_at": "2026-10-07T10:00:00", "response_note": "Apology sent and deposit back-dated.", "closed_at": "2026-10-08T09:15:00"},
       {"customer_name": "Marcus Bell", "customer_email": "marcus.bell@example.com", "channel": "email", "summary": "Interest rate on savings differs from advertised", "status": "closed", "assignee": "j.okafor@bank.example", "received_on": "2026-10-01", "sla_due": "2026-10-08", "responded_at": "2026-10-06T16:45:00", "response_note": "Explained tiered rate; customer accepted.", "closed_at": "2026-10-07T11:00:00"}
     ]}
  ],
  "queries": [
    {"name": "sla_breaches",
     "sql": "SELECT id, customer_name, summary, status, assignee, received_on, sla_due, responded_at FROM complaints WHERE COALESCE(substr(responded_at, 1, 10), :today) > sla_due ORDER BY sla_due"},
    {"name": "my_open_complaints",
     "sql": "SELECT id, customer_name, summary, status, received_on, sla_due FROM complaints WHERE assignee = :me AND status IN ('assigned', 'responded') ORDER BY sla_due"},
    {"name": "unassigned_complaints",
     "sql": "SELECT id, customer_name, summary, received_on, sla_due FROM complaints WHERE status = 'new' ORDER BY sla_due"}
  ]
}
```

```python file="actions.py"
from kit import action, ActionError
from datetime import date, timedelta

SLA_BUSINESS_DAYS = 5


def add_business_days(start, days):
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def _load(db, complaint_id):
    row = db.get("complaints", complaint_id)
    if not row:
        raise ActionError("Complaint not found")
    return row


def _can_work(user, row):
    return user.role == "supervisor" or row["assignee"] == user.email


@action(roles=["agent", "supervisor"])
def log_complaint(db, user, customer_name: str, summary: str, channel: str = "email",
                  customer_email: str = None, received_on: str = None):
    """Log a new complaint. The response is due within 5 business days of receipt."""
    if not customer_name.strip() or not summary.strip():
        raise ActionError("Customer name and summary are required")
    received = date.fromisoformat(received_on) if received_on else date.fromisoformat(db.today())
    if received > date.fromisoformat(db.today()):
        raise ActionError("Received date cannot be in the future")
    due = add_business_days(received, SLA_BUSINESS_DAYS)
    return db.insert("complaints", customer_name=customer_name.strip(), customer_email=customer_email,
                     channel=channel, summary=summary.strip(), status="new",
                     received_on=received.isoformat(), sla_due=due.isoformat())


@action(roles=["supervisor"])
def assign_complaint(db, user, complaint_id: int, assignee_email: str):
    """Assign (or reassign) a complaint to someone to respond to."""
    row = _load(db, complaint_id)
    if row["status"] in ("responded", "closed"):
        raise ActionError("Only complaints that have not been responded to can be assigned")
    assignee = assignee_email.strip().lower()
    updated = db.update("complaints", complaint_id, assignee=assignee, status="assigned")
    db.notify(assignee, "Complaint assigned to you: " + row["customer_name"],
              "%s\n\nResponse due by %s." % (row["summary"], row["sla_due"]))
    return updated


@action(roles=["agent", "supervisor"])
def respond_to_complaint(db, user, complaint_id: int, response_note: str):
    """Record the response sent to the customer."""
    row = _load(db, complaint_id)
    if row["status"] != "assigned":
        raise ActionError("Only assigned complaints can be responded to")
    if not _can_work(user, row):
        raise ActionError("Only the assignee or a supervisor can respond to this complaint")
    if not response_note.strip():
        raise ActionError("Enter what was said to the customer")
    return db.update("complaints", complaint_id, status="responded",
                     responded_at=db.now(), response_note=response_note.strip())


@action(roles=["agent", "supervisor"])
def close_complaint(db, user, complaint_id: int):
    """Close a complaint that has been responded to."""
    row = _load(db, complaint_id)
    if row["status"] != "responded":
        raise ActionError("A complaint can only be closed after it has been responded to")
    if not _can_work(user, row):
        raise ActionError("Only the assignee or a supervisor can close this complaint")
    return db.update("complaints", complaint_id, status="closed", closed_at=db.now())


def test_due_date_skips_weekend(t):
    """A complaint received on a Friday is due the following Friday"""
    row = t.run("log_complaint", as_role="agent", customer_name="Ana Silva",
                summary="Wrong fee on statement", received_on="2026-10-16")
    assert row["status"] == "new", row
    assert row["sla_due"] == "2026-10-23", row


def test_weekend_receipt_counts_from_monday(t):
    """A complaint received on a Saturday is due five business days after the weekend"""
    row = t.run("log_complaint", as_role="agent", customer_name="Ben Cole",
                summary="ATM kept card", received_on="2026-10-17")
    assert row["sla_due"] == "2026-10-23", row


def test_log_refuses_future_date(t):
    """A complaint cannot be logged as received in the future"""
    msg = t.fails("log_complaint", as_role="agent", customer_name="Ana Silva",
                  summary="Test", received_on="2030-01-01")
    assert "future" in msg


def test_supervisor_assigns_and_assignee_is_emailed(t):
    """Assigning a new complaint marks it assigned and emails the assignee"""
    row = t.find("complaints", customer_name="Priya Raman")[0]
    out = t.run("assign_complaint", as_role="supervisor", complaint_id=row["id"],
                assignee_email="m.chen@bank.example")
    assert out["status"] == "assigned" and out["assignee"] == "m.chen@bank.example", out
    assert len(t.outbox) == 1, t.outbox


def test_agent_cannot_assign(t):
    """Agents are not allowed to assign complaints"""
    row = t.find("complaints", customer_name="Priya Raman")[0]
    t.fails("assign_complaint", as_role="agent", complaint_id=row["id"],
            assignee_email="m.chen@bank.example")


def test_cannot_assign_closed_complaint(t):
    """A closed complaint cannot be assigned again"""
    row = t.find("complaints", customer_name="Helen Park")[0]
    t.fails("assign_complaint", as_role="supervisor", complaint_id=row["id"],
            assignee_email="m.chen@bank.example")


def test_assignee_responds(t):
    """The assigned person can record their response"""
    row = t.find("complaints", customer_name="Tom Hargreaves")[0]
    out = t.run("respond_to_complaint", as_role="agent", as_user="m.chen@bank.example",
                complaint_id=row["id"], response_note="Called customer and corrected balance.")
    assert out["status"] == "responded" and out["responded_at"], out


def test_other_agent_cannot_respond(t):
    """An agent who is not the assignee cannot respond"""
    row = t.find("complaints", customer_name="Tom Hargreaves")[0]
    t.fails("respond_to_complaint", as_role="agent", as_user="j.okafor@bank.example",
            complaint_id=row["id"], response_note="Done")


def test_cannot_respond_to_unassigned(t):
    """A complaint that has not been assigned cannot be responded to"""
    row = t.find("complaints", customer_name="Lucia Moretti")[0]
    t.fails("respond_to_complaint", as_role="supervisor", complaint_id=row["id"],
            response_note="Reset done")


def test_close_after_response(t):
    """A responded complaint can be closed by its assignee"""
    row = t.find("complaints", customer_name="Samuel Adeyemi")[0]
    out = t.run("close_complaint", as_role="agent", as_user="j.okafor@bank.example",
                complaint_id=row["id"])
    assert out["status"] == "closed" and out["closed_at"], out


def test_cannot_close_before_response(t):
    """A complaint that has not been responded to cannot be closed"""
    row = t.find("complaints", customer_name="Dana Whitfield")[0]
    t.fails("close_complaint", as_role="supervisor", complaint_id=row["id"])


def test_sla_breach_list(t):
    """The breach list shows overdue unanswered complaints and late responses, but not on-time ones"""
    names = [r["customer_name"] for r in t.query("sla_breaches")]
    assert "Dana Whitfield" in names and "Priya Raman" in names, names
    assert "Helen Park" in names, names
    assert "Tom Hargreaves" not in names and "Marcus Bell" not in names, names
```
