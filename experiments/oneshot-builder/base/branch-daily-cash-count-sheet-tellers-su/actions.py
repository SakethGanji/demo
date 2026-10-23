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