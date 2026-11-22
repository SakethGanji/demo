"""L08 (labeling.md §5.10): free-text redaction before a value is sent to an LLM, and the prompt-injection heuristic.
Matches are replaced by `[REDACTED:<kind>]` and counted. Regexes do not find names in prose: free-text columns are
excluded by default and need an approved request to be sent at all; this scan is the second line, not the first."""

from __future__ import annotations

import re
from typing import Any

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)|\d{2,4})[ .-]?\d{3,4}[ .-]?\d{3,4}(?!\w)")
ACCOUNT = re.compile(r"(?i)\b(?:acct|account|a/c)\s*(?:no\.?|number|#)?\s*[:#]?\s*\d{6,}\b")

INJECTION = re.compile(r"(?i)(ignore (all |any |the )?(previous|prior|above|earlier)|disregard (the|all|previous)|"
                       r"\binstruction|label (this|it|me) as|classify (this|it) as|you are (now|an?)\b|"
                       r"\b(system|assistant|user)\s*:|</?row>|new rules?|override|jailbreak|do not follow)")


def luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    s = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x *= 2
            if x > 9:
                x -= 9
        s += x
    return s % 10 == 0


def redact(text: Any) -> tuple[Any, int, list[str]]:
    """(redacted text, number of redactions, kinds)."""
    if not isinstance(text, str) or not text:
        return text, 0, []
    kinds: list[str] = []

    def sub(rx: re.Pattern, kind: str, s: str, check=None) -> str:
        def f(m: re.Match) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            kinds.append(kind)
            return f"[REDACTED:{kind}]"
        return rx.sub(f, s)

    s = sub(EMAIL, "email", text)
    s = sub(IBAN, "iban", s)
    s = sub(CARD, "card", s, luhn)
    s = sub(SSN, "national_id", s)
    s = sub(ACCOUNT, "account", s)
    s = sub(PHONE, "phone", s, lambda m: sum(c.isdigit() for c in m) >= 9)
    return s, len(kinds), kinds


def injection(values: dict[str, Any]) -> bool:
    return any(isinstance(v, str) and INJECTION.search(v) for v in values.values())
