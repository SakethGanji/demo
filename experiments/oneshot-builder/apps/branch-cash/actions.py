from kit import action, ActionError
from datetime import date

SIGNOFF_LIMIT = 50


def _branch_managed_by(db, user, branch_id):
    branch = db.get("branches", branch_id)
    if branch is None:
        raise ActionError("Branch not found")
    if branch["manager_email"] != user.email:
        raise ActionError("Only the manager of " + branch["name"] + " can do this")
    return branch


@action(roles=["manager"])
def add_branch(db, user, name: str, code: str, manager_email: str = None):
    """Add a branch. The manager defaults to you."""
    if db.find("branches", code=code):
        raise ActionError("A branch with code " + code + " already exists")
    return db.insert("branches", name=name, code=code,
                     manager_email=(manager_email or user.email).lower())


@action(roles=["manager"])
def add_teller(db, user, branch_id: int, name: str, email: str):
    """Assign a new teller to your branch."""
    _branch_managed_by(db, user, branch_id)
    email = email.lower()
    if db.find("tellers", email=email):
        raise ActionError("That person is already a teller")
    return db.insert("tellers", name=name, email=email, branch_id=branch_id, active=True)


@action(roles=["manager"])
def move_teller(db, user, teller_id: int, new_branch_id: int):
    """Move a teller from your branch to another branch."""
    teller = db.get("tellers", teller_id)
    if teller is None:
        raise ActionError("Teller not found")
    _branch_managed_by(db, user, teller["branch_id"])
    if db.get("branches", new_branch_id) is None:
        raise ActionError("Destination branch not found")
    if teller["branch_id"] == new_branch_id:
        raise ActionError("Teller is already at that branch")
    return db.update("tellers", teller_id, branch_id=new_branch_id)


@action(roles=["teller"])
def submit_cash_count(db, user, count_date: str, expected_amount: float,
                      counted_amount: float, note: str = None):
    """Submit your daily cash count. Differences over $50 need manager sign-off."""
    tellers = db.find("tellers", email=user.email)
    if not tellers:
        raise ActionError("You are not assigned to a branch as a teller")
    teller = tellers[0]
    if not teller["active"]:
        raise ActionError("Your teller profile is inactive")
    try:
        day = date.fromisoformat(count_date)
    except ValueError:
        raise ActionError("Date must look like 2026-10-22")
    if day > date.fromisoformat(db.today()):
        raise ActionError("You can't submit a count for a future date")
    if expected_amount < 0 or counted_amount < 0:
        raise ActionError("Amounts can't be negative")
    if db.find("cash_counts", teller_id=teller["id"], count_date=count_date):
        raise ActionError("You already submitted a count for " + count_date)

    variance = round(counted_amount - expected_amount, 2)
    needs_signoff = abs(variance) > SIGNOFF_LIMIT
    row = db.insert("cash_counts", teller_id=teller["id"], branch_id=teller["branch_id"],
                    count_date=count_date, expected_amount=expected_amount,
                    counted_amount=counted_amount, variance=variance,
                    status="needs_signoff" if needs_signoff else "ok", note=note)
    if needs_signoff:
        branch = db.get("branches", teller["branch_id"])
        db.notify(branch["manager_email"],
                  "Cash difference needs sign-off: " + branch["name"],
                  teller["name"] + " counted a difference of " + format(variance, ".2f")
                  + " on " + count_date + ". Please review and sign off.")
    return row


@action(roles=["manager"])
def sign_off_count(db, user, count_id: int):
    """Sign off a cash difference for a teller at your branch."""
    count = db.get("cash_counts", count_id)
    if count is None:
        raise ActionError("Cash count not found")
    _branch_managed_by(db, user, count["branch_id"])
    if count["status"] != "needs_signoff":
        raise ActionError("This count does not need sign-off")
    teller = db.get("tellers", count["teller_id"])
    if teller["email"] == user.email:
        raise ActionError("You can't sign off your own count")
    return db.update("cash_counts", count_id, status="signed_off",
                     signed_off_by=user.email, signed_off_at=db.now())


def test_small_difference_is_ok(t):
    """A $30 difference is accepted without sign-off"""
    row = t.run("submit_cash_count", as_role="teller", as_user="r.alvarez@bank.example",
                count_date="2026-10-05", expected_amount=5000, counted_amount=5030)
    assert row["status"] == "ok", row
    assert row["variance"] == 30, row


def test_large_difference_notifies_branch_manager(t):
    """A $100 shortage needs sign-off and emails that branch's manager"""
    row = t.run("submit_cash_count", as_role="teller", as_user="r.alvarez@bank.example",
                count_date="2026-10-05", expected_amount=5000, counted_amount=4900)
    assert row["status"] == "needs_signoff", row
    assert row["branch_id"] == 2, row
    assert len(t.outbox) == 1 and t.outbox[0]["to"] == "d.okafor@bank.example", t.outbox


def test_duplicate_count_refused(t):
    """A teller can't submit two counts for the same day"""
    msg = t.fails("submit_cash_count", as_role="teller", as_user="r.alvarez@bank.example",
                  count_date="2026-10-03", expected_amount=100, counted_amount=100)
    assert "already" in msg, msg


def test_future_count_refused(t):
    """A teller can't submit a count dated in the future"""
    t.fails("submit_cash_count", as_role="teller", as_user="r.alvarez@bank.example",
            count_date="2099-01-01", expected_amount=100, counted_amount=100)


def test_manager_signs_off_own_branch(t):
    """The Airport manager can sign off the Airport teller's shortage"""
    row = t.run("sign_off_count", as_role="manager", as_user="l.romero@bank.example", count_id=6)
    assert row["status"] == "signed_off", row
    assert row["signed_off_by"] == "l.romero@bank.example", row


def test_other_branch_manager_cannot_sign_off(t):
    """A manager from another branch can't sign off a count"""
    msg = t.fails("sign_off_count", as_role="manager", as_user="m.chen@bank.example", count_id=6)
    assert "Only the manager" in msg, msg


def test_cannot_sign_off_small_difference(t):
    """A count that was already fine can't be signed off"""
    t.fails("sign_off_count", as_role="manager", as_user="m.chen@bank.example", count_id=1)


def test_manager_adds_teller_to_own_branch(t):
    """A manager can assign a new teller to their own branch"""
    row = t.run("add_teller", as_role="manager", as_user="m.chen@bank.example",
                branch_id=1, name="Lena Fox", email="L.Fox@bank.example")
    assert row["email"] == "l.fox@bank.example" and row["branch_id"] == 1, row


def test_manager_cannot_add_teller_to_other_branch(t):
    """A manager can't assign tellers to someone else's branch"""
    t.fails("add_teller", as_role="manager", as_user="m.chen@bank.example",
            branch_id=2, name="Lena Fox", email="l.fox@bank.example")


def test_move_teller(t):
    """A manager can move a teller from their branch to another branch"""
    row = t.run("move_teller", as_role="manager", as_user="m.chen@bank.example",
                teller_id=2, new_branch_id=3)
    assert row["branch_id"] == 3, row


def test_move_teller_from_other_branch_refused(t):
    """A manager can't move a teller who works at another branch"""
    t.fails("move_teller", as_role="manager", as_user="s.patel@bank.example",
            teller_id=2, new_branch_id=3)


def test_add_branch_duplicate_code_refused(t):
    """A branch code can't be used twice"""
    t.fails("add_branch", as_role="manager", name="Downtown 2", code="DT01")