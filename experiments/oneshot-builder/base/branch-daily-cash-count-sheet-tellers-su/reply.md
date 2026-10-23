Assumed: roles are teller and branch_manager. Flag threshold is a difference over $50, so exactly $50 is not flagged. Difference is counted minus expected. Statuses are balanced, flagged and signed_off. Each teller gets one sheet per day, with no future dates. Managers can't sign off their own sheet, and sign-off needs a note.

```json file="schema.json"
{
  "roles": ["teller", "branch_manager"],
  "tables": [
    {"name": "count_sheets",
     "columns": [
       {"name": "branch", "type": "text", "required": true},
       {"name": "count_date", "type": "date", "required": true},
       {"name": "teller_email", "type": "person", "required": true},
       {"name": "expected_cash", "type": "money", "required": true},
       {"name": "counted_cash", "type": "money", "required": true},
       {"name": "difference", "type": "money", "required": true},
       {"name": "status", "type": "choice", "choices": ["balanced", "flagged", "signed_off"], "required": true},
       {"name": "teller_note", "type": "text"},
       {"name": "signoff_by", "type": "person"},
       {"name": "signoff_at", "type": "datetime"},
       {"name": "signoff_note", "type": "text"}
     ],
     "sample_rows": [
       {"branch": "Downtown", "count_date": "2026-10-22", "teller_email": "j.okafor@bank.example", "expected_cash": 18250, "counted_cash": 18250, "difference": 0, "status": "balanced"},
       {"branch": "Downtown", "count_date": "2026-10-22", "teller_email": "p.nguyen@bank.example", "expected_cash": 9400, "counted_cash": 9375, "difference": -25, "status": "balanced", "teller_note": "Two $10 bills short, rechecked twice"},
       {"branch": "Eastside", "count_date": "2026-10-22", "teller_email": "d.patel@bank.example", "expected_cash": 12100, "counted_cash": 12018.5, "difference": -81.5, "status": "flagged", "teller_note": "Possible miskeyed deposit"},
       {"branch": "Eastside", "count_date": "2026-10-21", "teller_email": "s.romero@bank.example", "expected_cash": 7600, "counted_cash": 7690, "difference": 90, "status": "signed_off", "teller_note": "Over at close", "signoff_by": "l.haddad@bank.example", "signoff_at": "2026-10-21T17:40:00", "signoff_note": "Found unrecorded deposit slip, corrected"},
       {"branch": "Downtown", "count_date": "2026-10-21", "teller_email": "j.okafor@bank.example", "expected_cash": 15000, "counted_cash": 15000, "difference": 0, "status": "balanced"},
       {"branch": "Westgate", "count_date": "2026-10-22", "teller_email": "k.brandt@bank.example", "expected_cash": 5300, "counted_cash": 5350, "difference": 50, "status": "balanced", "teller_note": "Exactly at threshold"},
       {"branch": "Westgate", "count_date": "2026-10-21", "teller_email": "m.ito@bank.example", "expected_cash": 6200, "counted_cash": 6120, "difference": -80, "status": "flagged"}
     ]}
  ],
  "queries": [
    {"name": "awaiting_signoff", "sql": "SELECT id, branch, count_date, teller_email, expected_cash, counted_cash, difference FROM count_sheets WHERE status = 'flagged' ORDER BY count_date, branch"},
    {"name": "my_sheets", "sql": "SELECT id, branch, count_date, expected_cash, counted_cash, difference, status FROM count_sheets WHERE teller_email = :me ORDER BY count_date DESC"},
    {"name": "todays_sheets", "sql": "SELECT id, branch, teller_email, expected_cash, counted_cash, difference, status FROM count_sheets WHERE count_date = :today ORDER BY branch"},
    {"name": "branch_totals", "sql": "SELECT branch, COUNT(*) AS sheets, SUM(CASE WHEN status = 'flagged' THEN 1 ELSE 0 END) AS open_flags, SUM(difference) AS net_difference FROM count_sheets GROUP BY branch ORDER BY branch"}
  ]
}
```

```python file="actions.py"
from kit import action, ActionError

FLAG_THRESHOLD = 50

@action(roles=["teller"])
def submit_count(db, user, branch: str, count_date: str, expected_cash: float,
                 counted_cash: float, teller_note: str = None):
    """Submit a daily cash count. A difference over $50 is flagged for manager sign-off."""
    if expected_cash < 0 or counted_cash < 0:
        raise ActionError("Cash amounts can't be negative")
    if count_date > db.today():
        raise ActionError("Count date can't be in the future")
    if db.find("count_sheets", teller_email=user.email, count_date=count_date):
        raise ActionError("You already submitted a count for that date")
    difference = round(counted_cash - expected_cash, 2)
    status = "flagged" if abs(difference) > FLAG_THRESHOLD else "balanced"
    return db.insert("count_sheets", branch=branch, count_date=count_date,
                     teller_email=user.email, expected_cash=expected_cash,
                     counted_cash=counted_cash, difference=difference,
                     status=status, teller_note=teller_note)

@action(roles=["branch_manager"])
def sign_off(db, user, sheet_id: int, signoff_note: str):
    """Sign off a flagged count sheet after reviewing the difference."""
    sheet = db.get("count_sheets", sheet_id)
    if sheet["status"] != "flagged":
        raise ActionError("Only flagged sheets need sign-off")
    if sheet["teller_email"] == user.email:
        raise ActionError("You can't sign off your own count")
    if not signoff_note or not signoff_note.strip():
        raise ActionError("Please explain the difference in the sign-off note")
    row = db.update("count_sheets", sheet_id, status="signed_off",
                    signoff_by=user.email, signoff_at=db.now(),
                    signoff_note=signoff_note.strip())
    db.notify(sheet["teller_email"], "Cash count signed off",
              "Your %s count for %s (difference %.2f) was signed off by %s."
              % (sheet["branch"], sheet["count_date"], sheet["difference"], user.email))
    return row

def test_small_difference_is_balanced(t):
    """A count that is only $25 off is accepted without needing sign-off"""
    row = t.run("submit_count", as_role="teller", as_user="t.new@bank.example",
                branch="Downtown", count_date="2026-10-22",
                expected_cash=10000, counted_cash=9975)
    assert row["status"] == "balanced", row
    assert row["difference"] == -25, row

def test_exactly_fifty_is_not_flagged(t):
    """A difference of exactly $50 is not flagged"""
    row = t.run("submit_count", as_role="teller", as_user="t.new@bank.example",
                branch="Downtown", count_date="2026-10-22",
                expected_cash=10000, counted_cash=10050)
    assert row["status"] == "balanced", row

def test_large_difference_is_flagged(t):
    """A count that is $60 short is flagged for manager sign-off"""
    row = t.run("submit_count", as_role="teller", as_user="t.new@bank.example",
                branch="Downtown", count_date="2026-10-22",
                expected_cash=10000, counted_cash=9940)
    assert row["status"] == "flagged", row
    assert row["difference"] == -60, row

def test_duplicate_count_refused(t):
    """A teller can't submit two counts for the same day"""
    msg = t.fails("submit_count", as_role="teller", as_user="j.okafor@bank.example",
                  branch="Downtown", count_date="2026-10-22",
                  expected_cash=100, counted_cash=100)
    assert "already" in msg, msg

def test_future_date_refused(t):
    """A count dated in the future is refused"""
    t.fails("submit_count", as_role="teller", as_user="t.new@bank.example",
            branch="Downtown", count_date="2099-01-01",
            expected_cash=100, counted_cash=100)

def test_manager_signs_off_flagged_sheet(t):
    """A branch manager can sign off a flagged sheet and the teller is told"""
    sheet = t.find("count_sheets", status="flagged")[0]
    row = t.run("sign_off", as_role="branch_manager", as_user="l.haddad@bank.example",
                sheet_id=sheet["id"], signoff_note="Reviewed deposit slips")
    assert row["status"] == "signed_off", row
    assert row["signoff_by"] == "l.haddad@bank.example", row
    assert len(t.outbox) == 1, t.outbox

def test_balanced_sheet_cannot_be_signed_off(t):
    """A sheet that is not flagged can't be signed off"""
    sheet = t.find("count_sheets", status="balanced")[0]
    t.fails("sign_off", as_role="branch_manager", as_user="l.haddad@bank.example",
            sheet_id=sheet["id"], signoff_note="ok")

def test_manager_cannot_sign_off_own_count(t):
    """A manager can't sign off a count they submitted themselves"""
    sheet = t.find("count_sheets", status="flagged")[0]
    msg = t.fails("sign_off", as_role="branch_manager", as_user=sheet["teller_email"],
                  sheet_id=sheet["id"], signoff_note="Looks fine")
    assert "own" in msg, msg

def test_teller_cannot_sign_off(t):
    """A teller is not allowed to sign off a flagged sheet"""
    sheet = t.find("count_sheets", status="flagged")[0]
    t.fails("sign_off", as_role="teller", sheet_id=sheet["id"], signoff_note="fine")
```
