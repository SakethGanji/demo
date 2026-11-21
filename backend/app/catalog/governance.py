"""Governance helpers shared by the registry and the catalog: how names are compared (four eyes, admins) and the
"no Production / champion while a critical leak is open" gate.

Names, not authenticated identities: there is no sign-in. identity_key only stops the easy spoofs (punctuation,
spacing, accents, lookalike letters); anyone can still type someone else's name."""

from __future__ import annotations

import inspect
import os
import unicodedata
from typing import Any

# Cyrillic and Greek letters that look like Latin ones (upper and lower case, before casefolding: Greek Η is H, η is n).
_CONFUSABLES = {
    # Cyrillic
    "А": "a", "а": "a", "В": "b", "в": "b", "Ь": "b", "ь": "b", "С": "c", "с": "c", "ԁ": "d", "Е": "e", "е": "e", "Ё": "e",
    "ё": "e", "Һ": "h", "һ": "h", "Н": "h", "н": "h", "І": "i", "і": "i", "Ї": "i", "ї": "i", "Ј": "j", "ј": "j", "К": "k",
    "к": "k", "Ӏ": "l", "ӏ": "l", "М": "m", "м": "m", "О": "o", "о": "o", "Р": "p", "р": "p", "Ԛ": "q", "ԛ": "q", "Ѕ": "s",
    "ѕ": "s", "Т": "t", "т": "t", "У": "y", "у": "y", "Ү": "y", "ү": "y", "Х": "x", "х": "x", "Ԝ": "w", "ԝ": "w", "п": "n",
    "Ѵ": "v", "ѵ": "v", "г": "r",
    # Greek
    "Α": "a", "α": "a", "Β": "b", "β": "b", "Ε": "e", "ε": "e", "Ζ": "z", "Η": "h", "η": "n", "Ι": "i", "ι": "i", "Κ": "k",
    "κ": "k", "Μ": "m", "Ν": "n", "ν": "v", "Ο": "o", "ο": "o", "Ρ": "p", "ρ": "p", "Τ": "t", "τ": "t", "Υ": "y", "υ": "u",
    "Χ": "x", "χ": "x", "γ": "y", "ω": "w", "μ": "m", "ζ": "z", "ϲ": "c", "Ϲ": "c", "ϳ": "j", "ı": "i", "ȷ": "j",
    # Latin lookalikes and digits that pass for letters
    "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d", "ħ": "h", "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe",
    "0": "o", "1": "l",
}


def identity_key(s: str | None) -> str:
    """A name as four-eyes compares it: letters and digits only. NFKD (accents and full-width forms folded), lookalike
    Cyrillic / Greek letters mapped to Latin, casefolded; punctuation, spaces, symbols, zero-width and other format
    characters dropped. "a.l.i.c.e", "Al-ice", "Al ice", "Alicé", "Аlice" (Cyrillic А) and "Ali​ce" are all "alice"; A3: "AIice" (capital I), "A1ice" and "ᴀlice" (small capitals) too: I / l / 1 are one class.
    Collisions only make four eyes stricter (two different people whose names fold together must use other names)."""
    t = unicodedata.normalize("NFKD", s or "")
    out = []
    for ch in t:
        ch = _CONFUSABLES.get(ch, ch)
        ch = _small_capital(ch)
        for c in ch.casefold():
            c = _CONFUSABLES.get(c, c)
            for d in c:
                if unicodedata.category(d)[0] in "LN" and unicodedata.category(d) != "Lm":
                    out.append(_ONE_STROKE.get(d, d))
    return "".join(out)


# A3: capital I, small l and digit 1 look alike in most fonts ("AIice" for "Alice"), so they are one class
_ONE_STROKE = {"i": "l", "1": "l", "ı": "l"}


def _small_capital(ch: str) -> str:
    """A3: small-capital letters ("ᴀlice") fold to the Latin letter."""
    if len(ch) != 1 or ord(ch) < 0x250:
        return ch
    try:
        n = unicodedata.name(ch)
    except ValueError:
        return ch
    if n.startswith("LATIN LETTER SMALL CAPITAL "):
        rest = n[len("LATIN LETTER SMALL CAPITAL "):]
        if len(rest) == 1:
            return rest.lower()
    return ch


def same_person(a: str | None, b: str | None) -> bool:
    ka, kb = identity_key(a), identity_key(b)
    return bool(ka) and ka == kb


def admins() -> set[str]:
    """ENSEMBLE_ADMINS (comma-separated names), as identity keys. F-P2 (A2 m6): no default, so typing "admin" is not
    enough; with the variable unset nobody runs retention. Names, not authenticated identities: there is no sign-in."""
    return {identity_key(a) for a in os.environ.get("ENSEMBLE_ADMINS", "").split(",") if identity_key(a)}


def is_admin(name: str | None) -> bool:
    k = identity_key(name)
    return bool(k) and k in admins()


def allow_admin_self_approve() -> bool:
    """DL3 (m1): may an admin approve their own retire / delete request? Default no: a second person decides, admins
    included (ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE=1 allows it; the audit then says "self-approved by admin")."""
    return os.environ.get("ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE", "").strip().lower() in ("1", "true", "yes")


SELF_APPROVED = "SELF-APPROVED BY ADMIN (no second person; ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE=1)"


def allow_self_promote() -> bool:
    """Policy: may the person who registered a version also move it to Production once someone else approved it?
    Default no (ENSEMBLE_ALLOW_SELF_PROMOTE=1 to allow; the audit records it as a self-promotion either way)."""
    return os.environ.get("ENSEMBLE_ALLOW_SELF_PROMOTE", "").strip().lower() in ("1", "true", "yes")


def open_critical(app: Any, run_id: str) -> list[str] | None:
    """Open critical Doctor findings (a leak) on the run, from vela.doctor.open_critical. None when that check is not
    available in this build (it is then reported as 'not checked', never as clean)."""
    try:
        from ..vela import doctor
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(doctor, "open_critical", None)
    if fn is None:
        return None
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    try:
        if "runs" in params:  # F-A2: open_critical(run_id, runs=RunStore, datasets=DatasetStore)
            out = fn(run_id, runs=app.state.runs, datasets=app.state.datasets)
        elif "app" in params:
            out = fn(app, run_id)
        else:
            out = fn(run_id)
    except Exception as e:  # noqa: BLE001 — a failing check blocks like an open leak would
        return [f"the leak check failed ({type(e).__name__}: {e}); override with a reason if you have checked by hand"]
    return [str(x) for x in (out or [])]


def leak_gate(app: Any, run_id: str, override: Any, what: str) -> dict[str, Any]:
    """Block `what` (Production, champion) while the run has an open leak question that gates (critical, or A3 N2 a
    high / major suspect the model uses), unless an override reason is
    given. Returns the details for the audit row; raises ValueError(message) when blocked."""
    crit = open_critical(app, run_id)
    reason = str(override or "").strip()
    if crit:
        if not reason:
            raise ValueError(f"{what} is blocked while a leak question is open on {run_id}: " + "; ".join(q.rstrip(".") for q in crit[:5])
                             + '. Fix it, or send {"override_reason": "…"} to proceed on the record (audited).')
        if len(reason) < 10:
            raise ValueError("An override needs a reason of at least 10 characters (it goes in the audit log).")
        return {"open_critical": crit, "override_reason": reason[:1000]}
    return {"leak_check": "clean" if crit is not None else "not available in this build"}


# -- DL2: who is acting on a request without a `by` field, refusals and reads in the audit log ---------------------------

import contextvars  # noqa: E402

#: the X-Ensemble-Actor header of the current request (the UI's "Acting as" name), set by a middleware in main.py
ACTOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("ensemble_actor", default=None)
#: the X-Ensemble-Reason header: an optional reason / change ticket for a version made by this request
REASON: contextvars.ContextVar[str | None] = contextvars.ContextVar("ensemble_reason", default=None)

#: a read (row lookup, preview, export) by the same person of the same version is logged once per this many seconds
READ_AUDIT_DEDUPE_S = 600


def current_actor(default: str | None = None) -> str | None:
    s = (ACTOR.get() or "").strip()[:80]
    return s if identity_key(s) else default


def current_reason() -> str | None:
    s = (REASON.get() or "").strip()
    return s[:500] or None


def refused(db: Any, by: str | None, action: str, status: int, message: Any, details: dict[str, Any] | None = None) -> None:
    """A governance action that was refused (403 / 409) goes in the audit log too, as `<action>_refused`."""
    from .schema import audit

    msg = message if isinstance(message, str) else (message or {}).get("message") if isinstance(message, dict) else str(message)
    try:
        with db.connect() as con:
            audit(con, by or current_actor("unknown"), f"{action}_refused", comment=str(msg)[:500],
                  details={"status": status, **(details or {}),
                           **({"blockers": message.get("blockers")} if isinstance(message, dict) and message.get("blockers") else {})})
    except Exception:  # noqa: BLE001 - the refusal is returned to the caller whatever happens to its audit row
        pass


def reader(by: str | None = None) -> str:
    """DL3 (m2): the name behind a read of rows: `by`, else the X-Ensemble-Actor header. Anonymous reads are refused (400)."""
    from fastapi import HTTPException

    s = str(by or "").strip()[:80] or current_actor()
    if not s or not identity_key(s):
        raise HTTPException(400, "Say who is reading these rows: send by=<your name> or the X-Ensemble-Actor header "
                                 "(reads of rows are audited; there is no sign-in).")
    return s


def read_audit(db: Any, what: str, dataset_id: str, details: dict[str, Any] | None = None, by: str | None = None) -> bool:
    """DL2: a read of a dataset's rows (row lookup, preview, export) is audited as dataset_read, once per person,
    version and kind of read every READ_AUDIT_DEDUPE_S seconds. Returns True when a row was written. DL3 (m2): the
    person is required (reader(): `by` or the header; anonymous → 400) and the dedupe compares identity keys, so
    "Erin" and "e.rin" are one reader."""
    from datetime import datetime, timedelta, timezone

    from .schema import audit

    who = reader(by)
    key = identity_key(who)
    since = (datetime.now(timezone.utc) - timedelta(seconds=READ_AUDIT_DEDUPE_S)).isoformat(timespec="seconds")
    with db.connect() as con:
        r = con.execute("SELECT root_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        root = (r[0] if r else None) or dataset_id
        if con.execute("SELECT 1 FROM audit_log WHERE action = 'dataset_read' AND ts >= ? AND json_extract(details, '$.actor_key') = ?"
                       " AND json_extract(details, '$.dataset_id') = ? AND json_extract(details, '$.read') = ? LIMIT 1",
                       (since, key, dataset_id, what)).fetchone():
            return False
        audit(con, who, "dataset_read", {"dataset_id": dataset_id, "root_id": root, "read": what, "actor_key": key,
                                         "dedupe_seconds": READ_AUDIT_DEDUPE_S, **(details or {})})
    return True


def run_read_audit(app: Any, what: str, run_id: str, details: dict[str, Any] | None = None, by: str | None = None) -> None:
    """DL3 (m2): a read of a run's rows (anomaly top rows, shift rows, holdout predictions) is a read of its dataset."""
    who = reader(by)
    rec = app.state.runs.get(run_id) if hasattr(app.state, "runs") else None
    ds = (rec or {}).get("dataset_id") or ((rec or {}).get("config") or {}).get("dataset_id")
    if ds:
        read_audit(app.state.db, what, ds, {"run_id": run_id, **(details or {})}, by=who)
