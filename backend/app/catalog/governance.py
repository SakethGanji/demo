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
    characters dropped. "a.l.i.c.e", "Al-ice", "Al ice", "Alicé", "Аlice" (Cyrillic А) and "Ali​ce" are all "alice".
    Collisions only make four eyes stricter (two different people whose names fold together must use other names)."""
    t = unicodedata.normalize("NFKD", s or "")
    out = []
    for ch in t:
        ch = _CONFUSABLES.get(ch, ch)
        for c in ch.casefold():
            c = _CONFUSABLES.get(c, c)
            for d in c:
                if unicodedata.category(d)[0] in "LN" and unicodedata.category(d) != "Lm":
                    out.append(d)
    return "".join(out)


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
    """Block `what` (Production, champion) while the run has an open critical finding, unless an override reason is
    given. Returns the details for the audit row; raises ValueError(message) when blocked."""
    crit = open_critical(app, run_id)
    reason = str(override or "").strip()
    if crit:
        if not reason:
            raise ValueError(f"{what} is blocked while a critical finding is open on {run_id}: " + "; ".join(crit[:5])
                             + '. Fix it, or send {"override_reason": "…"} to proceed on the record (audited).')
        if len(reason) < 10:
            raise ValueError("An override needs a reason of at least 10 characters (it goes in the audit log).")
        return {"open_critical": crit, "override_reason": reason[:1000]}
    return {"leak_check": "clean" if crit is not None else "not available in this build"}
