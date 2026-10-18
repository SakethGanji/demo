"""Typed slot extractors and answer validation for Understand (PLAN-CAPABILITY-GRAPH section 4.1 step 3; CONTRACTS-CAPGRAPH.md section 4.1).

Pure and deterministic: no I/O, no LLM. A slot has one of seven types (``kg_ontology.SLOT_TYPES``). For each type there is

* an extractor that reads a value out of the request text (``extract_*``), and
* a validator that turns a person's answer (or an LLM's value) into the canonical stored form (``validate_value``).

Canonical forms: a role is a role key (``role:ops_head``) or ``{"new_role": "<label>"}``; a duration is ``<n>m|h|d|bd``
(``48h``, ``2bd``); a cron is five fields (``0 8 * * 1``); a field is a snake_case word; a choice is one of its options; text is a
trimmed string; an api_capability is ``api:<api_ref>`` or ``node:<Type>``. A slot with ``multiple: true`` holds a list of its type
(a role slot may also hold a ``{value: role}`` map: "which role owns each region").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

MAX_TEXT = 200

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "twelve": 12, "twenty four": 24, "forty eight": 48}
_UNIT = {"min": "m", "mins": "m", "minute": "m", "minutes": "m", "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
         "d": "d", "day": "d", "days": "d", "week": "w", "weeks": "w"}
_DURATION_RE = re.compile(
    r"(?<![a-z0-9])(?:(\d+)\s*(business\s+days?|working\s+days?|minutes?|mins?|hours?|hrs?|days?|weeks?|h|d)"
    r"|(an?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty four|forty eight)\s+(business\s+days?|working\s+days?|minutes?|hours?|days?|weeks?))(?![a-z0-9])",
    re.IGNORECASE)
_DURATION_VALUE = re.compile(r"^(\d{1,4})(m|h|d|bd)$")


# ── duration ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def extract_duration(text: str | None) -> str | None:
    """The first duration in ``text`` as ``<n>m|h|d|bd`` ("within 48 hours" -> ``48h``, "2 business days" -> ``2bd``, "after a day" -> ``1d``).
    A week is seven days."""
    for m in _DURATION_RE.finditer(str(text or "")):
        num_s, unit_s, word, word_unit = m.groups()
        n = int(num_s) if num_s else _NUMBER_WORDS.get((word or "").lower())
        unit_raw = re.sub(r"\s+", " ", (unit_s or word_unit or "").lower())
        if n is None or n <= 0:
            continue
        if unit_raw.startswith(("business", "working")):
            return f"{n}bd"
        unit = _UNIT.get(unit_raw)
        if unit == "w":
            return f"{n * 7}d"
        if unit:
            return f"{n}{unit}"
    return None


# ── cron ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

_DOW = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
_AT_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?|\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_CRON_FIELD = re.compile(r"^[0-9*/,\-]+$")


def _time_of(text: str, default_hour: int = 8) -> tuple[int, int]:
    m = _AT_RE.search(text)
    if not m:
        return default_hour, 0
    hour_s, min_s, ap = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
    hour, minute = int(hour_s), int(min_s or 0)
    ap = (ap or "").lower().replace(".", "")
    if ap == "pm" and hour < 12:
        hour += 12
    if ap == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return default_hour, 0
    return hour, minute


def is_cron(value: Any) -> bool:
    parts = str(value or "").split()
    return len(parts) == 5 and all(_CRON_FIELD.match(p) for p in parts)


def extract_cron(text: str | None) -> str | None:
    """A five-field cron for a schedule phrase: "every Monday at 8" -> ``0 8 * * 1``, "daily" -> ``0 8 * * *``, "weekly" -> ``0 8 * * 1``,
    "monthly" -> ``0 8 1 * *``, "hourly" -> ``0 * * * *``, "every 15 minutes" -> ``*/15 * * * *``. The default time is 08:00. A literal
    cron in the text is returned as it is."""
    t = str(text or "").lower()
    lit = re.search(r"(?<![\w*/,-])((?:[0-9*/,\-]+\s+){4}[0-9*/,\-]+)(?![\w*/,-])", t)
    if lit and is_cron(lit.group(1)):
        return " ".join(lit.group(1).split())
    m = re.search(r"every\s+(\d{1,2})\s+(minutes?|mins?|hours?|hrs?)\b", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 59:
            return f"*/{n} * * * *" if m.group(2).startswith("min") else f"0 */{n} * * *"
    if re.search(r"\b(hourly|every hour|each hour)\b", t):
        return "0 * * * *"
    hour, minute = _time_of(t)
    for name, dow in _DOW.items():
        if re.search(rf"\b(every|each|on)\s+{name}s?\b|\b{name}s\b", t):
            return f"{minute} {hour} * * {dow}"
    if re.search(r"\b(every weekday|weekdays|each weekday|every working day)\b", t):
        return f"{minute} {hour} * * 1-5"
    if re.search(r"\b(nightly|every night|each night)\b", t):
        return f"{minute} {0 if not _AT_RE.search(t) else hour} * * *"
    if re.search(r"\b(daily|every day|each day|every morning|each morning)\b", t):
        return f"{minute} {hour} * * *"
    if re.search(r"\b(weekly|every week|each week)\b", t):
        return f"{minute} {hour} * * 1"
    if re.search(r"\b(monthly|every month|each month)\b", t):
        return f"{minute} {hour} 1 * *"
    return None


# ── roles ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleMention:
    start: int
    end: int
    key: str


def _role_patterns(roles: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    pats: list[tuple[str, str]] = []
    for r in roles:
        key = str(r.get("key") or "")
        if not key:
            continue
        names = {str(a).lower() for a in r.get("aliases") or [] if str(a).strip()}
        if r.get("label"):
            names.add(str(r["label"]).lower())
        names.add(key.split(":", 1)[-1].replace("_", " "))
        pats += [(n, key) for n in names if n]
    return sorted(pats, key=lambda p: (-len(p[0]), p[0]))


#: aliases that name a kind of job, not one seeded role ("a manager" is the operations manager); they count only when no word qualifies them
#: ("the relationship manager" is a role of its own that nobody seeded)
GENERIC_ALIASES = frozenset({"manager", "managers", "supervisor", "team lead", "team leader"})
_UNQUALIFIED_BEFORE = frozenset({
    "a", "an", "the", "our", "their", "his", "her", "your", "any", "each", "every", "one", "another", "to", "by", "from", "for", "with", "and", "or",
    "then", "of", "if", "when", "ask", "asks", "tell", "email", "notify", "send", "sends", "route", "pass", "bump", "hand", "need", "needs", "require",
    "requires", "approve", "approves", "approved", "approval", "sign", "off", "escalate", "escalates", "escalated", "review", "reviews", "reviewed",
    "let", "give", "gives", "into", "up", "also", "plus", "not"})


def _qualified(t: str, start: int) -> bool:
    prev = t[:start].split()[-1:] 
    if not prev or not t[:start].endswith(" "):
        return False
    w = prev[0]
    return w[-1] not in ",;:.!?()-" and w not in _UNQUALIFIED_BEFORE


def find_roles(text: str | None, roles: Iterable[Mapping[str, Any]]) -> list[RoleMention]:
    """Every role named in ``text`` (aliases, labels, the key with spaces), longest alias first, never overlapping, in text order."""
    t = str(text or "").lower()
    taken: list[tuple[int, int]] = []
    found: list[RoleMention] = []
    for alias, key in _role_patterns(roles):
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", t):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            if alias in GENERIC_ALIASES and _qualified(t, m.start()):
                continue
            taken.append((m.start(), m.end()))
            found.append(RoleMention(m.start(), m.end(), key))
    return sorted(found, key=lambda r: r.start)


def resolve_role(value: Any, roles: Iterable[Mapping[str, Any]]) -> str | None:
    """A role key for ``value`` ("role:ops_head", "ops_head", "Head of operations"), or None when it names no known role."""
    roles = list(roles)
    s = str(value or "").strip()
    if not s:
        return None
    known = {str(r.get("key")) for r in roles}
    if s in known:
        return s
    if f"role:{s}" in known:
        return f"role:{s}"
    found = find_roles(s, roles)
    return found[0].key if len(found) == 1 else None


#: where in a request each role slot is announced: the cue comes first, the role follows within ``_CUE_REACH`` characters
ROLE_CUES: dict[str, re.Pattern[str]] = {
    "approver_role": re.compile(r"\b(approv\w*|sign\w* off|sign-off|authori[sz]\w*|needs? (?:a|an|the)?|requires? (?:a|an|the)?)\b"),
    "second_approver_role": re.compile(r"\b(second approv\w*|then|countersign\w*|second sign\w*)\b"),
    "target_role": re.compile(r"\b(escalat\w*(?:\s+\w+){0,6}?\s+to|pass\w*(?:\s+up)?\s+to|bump\w*(?:\s+up)?\s+to|hand\w* (?:it )?(?:up )?to)\b"),
    "recipients_role": re.compile(r"\b(send\w*|email\w*|mail\w*|notify\w*|tell\w*|message\w*)\b"),
    "reviewer_role": re.compile(r"\b(review\w*)\b"),
    "resolution_role": re.compile(r"\b(resolv\w*|investigat\w*|handl\w*)\b"),
}
_CUE_REACH = 60


#: a role that comes BEFORE its cue: "a manager to approve", "the head of ops must sign off"
REVERSE_CUES: dict[str, re.Pattern[str]] = {
    "approver_role": re.compile(r"^[\s,]*(?:(?:must|should|needs? to|to|for|will|who|has to|have to|can|shall)\s+)*(?:approv\w*|sign\w* off|authori[sz]\w*)"),
}
_CLAUSE_BREAK = re.compile(r"[.;!?]")


def extract_role(text: str | None, slot_name: str, roles: Iterable[Mapping[str, Any]], *, exclude: Iterable[str] = ()) -> str | None:
    """The role the request ties to ``slot_name``: the one named right after the cue that announces it ("escalate it to the head of
    operations" fills ``target_role``), or, for an approver, the one named right before "to approve". A role is never taken across a
    sentence break, nor across the word "escalate" (unless the slot is the escalation target). ``exclude`` are role keys already used by
    another slot. None when the request does not say."""
    cue = ROLE_CUES.get(slot_name)
    if cue is None:
        return None
    t = str(text or "").lower()
    skip = set(exclude)
    mentions = find_roles(t, roles)
    for cm in cue.finditer(t):
        for r in mentions:
            gap = t[cm.end():r.start]
            if r.start < cm.end() - 1 or len(gap) > _CUE_REACH or r.key in skip or _CLAUSE_BREAK.search(gap):
                continue
            if slot_name != "target_role" and "escalat" in gap:
                continue
            return r.key
    rev = REVERSE_CUES.get(slot_name)
    if rev is not None:
        for r in mentions:
            if r.key not in skip and rev.match(t[r.end:r.end + 40]):
                return r.key
    return None


# generic titles: a role the request names but the organisation has no seeded role for ("the risk committee", "a senior underwriter")
_TITLE = (r"(?:manager|supervisor|director|officer|underwriter|committee|executive|controller|approver|specialist|lead|leader|"
          r"head of [a-z]+(?: [a-z]+)?|chief [a-z]+(?: [a-z]+)?|[a-z]+ team)")
_TITLE_RE = re.compile(rf"\b(?:(?:a|an|the|our|their|his|her|one of the)\s+)?((?:[a-z]+\s+){{0,2}}?{_TITLE})\b")
_TITLE_STOP = frozenset({"a", "an", "the", "our", "their", "his", "her", "to", "for", "by", "of", "and", "or", "with", "that", "it", "is", "are", "be",
                         "then", "if", "when", "approval", "approve", "approved", "needs", "need", "send", "sent", "goes", "go", "must", "should",
                         "who", "from", "into", "on", "at", "in", "than", "larger", "bigger", "small", "smaller", "refund", "refunds", "case",
                         "cases", "item", "items", "request", "requests", "else", "someone", "anyone", "team", "same", "other", "next", "each",
                         "every", "any", "all", "no", "not", "never", "always", "also", "just", "only", "up", "out", "over", "under", "above",
                         "below", "first", "second", "automatic", "automatically", "customer", "customers"})


def extract_new_role(text: str | None, slot_name: str, roles: Iterable[Mapping[str, Any]]) -> str | None:
    """A role the request names for ``slot_name`` that no seeded role fits ("approved by the risk committee" -> ``Risk committee``).
    Same cues as ``extract_role``. The label is a stated fact: the caller stores ``{"new_role": label}`` as an assumption, never a question."""
    t = str(text or "").lower()
    seeded = find_roles(t, roles)
    cue, rev = ROLE_CUES.get(slot_name), REVERSE_CUES.get(slot_name)
    if cue is None:
        return None
    cands: list[tuple[int, int, str]] = []
    for m in _TITLE_RE.finditer(t):
        words = m.group(1).split()
        while words and words[0] in _TITLE_STOP and len(words) > 1:
            words.pop(0)
        label = " ".join(words)
        start = m.end(1) - len(label)
        if not words or any(w in _TITLE_STOP for w in words[:-1]) or (len(words) == 1 and words[0] in ("team", "lead", "head", "chief", "leader")):
            continue
        if words[-1] == "team" and (len(words) < 2 or words[0] in _TITLE_STOP):
            continue
        if any(start < r.end and r.start < m.end(1) for r in seeded):
            continue
        cands.append((start, m.end(1), label))
    for cm in cue.finditer(t):
        for start, end, label in cands:
            gap = t[cm.end():start]
            if start < cm.end() - 1 or len(gap) > _CUE_REACH or _CLAUSE_BREAK.search(gap):
                continue
            if slot_name != "target_role" and "escalat" in gap:
                continue
            return label[:1].upper() + label[1:]
    if rev is not None:
        for start, end, label in cands:
            if rev.match(t[end:end + 40]):
                return label[:1].upper() + label[1:]
    return None


# ── amounts / thresholds ──────────────────────────────────────────────────────────────────────────────────────────────

_OPERATORS = (
    (r"at least|no less than|not less than|minimum of", "at least"),
    (r"at most|no more than|not more than|maximum of", "at most"),
    (r"over|above|more than|greater than|exceed(?:s|ed|ing)?|bigger than|larger than|higher than|in excess of", "over"),
    (r"under|below|less than|smaller than|lower than|up to|fewer than", "under"),
)
_AMOUNT = r"[$\u20ac\u00a3]?\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(?P<mult>k|m|thousand|million)?(?![a-z0-9])(?!\s?(?:days?|hours?|hrs?|minutes?|mins?|weeks?|months?|years?|%|percent|times|attempts?))"
_AMOUNT_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(f"(?P<op{i}>{o})" for i, (o, _) in enumerate(_OPERATORS)) + r")\s+(?:a |an |the )?(?:total |amount |value |balance |score )?(?:of )?"
    + _AMOUNT)
_MULT = {"k": 1000, "thousand": 1000, "m": 1_000_000, "million": 1_000_000}


def extract_threshold(text: str | None) -> str | None:
    """The limit a request states: "over 500" -> ``over 500``, "above $5,000" -> ``over 5000``, "exceeds 10k" -> ``over 10000``,
    "at least 500" -> ``at least 500``, "under 20" -> ``under 20``. The first one; None when no number follows a comparison."""
    for m in _AMOUNT_RE.finditer(str(text or "").lower()):
        op = next(label for i, (_, label) in enumerate(_OPERATORS) if m.group(f"op{i}"))
        n = float(m.group("num").replace(",", "")) * _MULT.get(m.group("mult") or "", 1)
        return f"{op} {int(n) if n == int(n) else n}"
    return None

# ── field / choice / text / api_capability ────────────────────────────────────────────────────────────────────────────

_FIELD_RE = re.compile(r"\b(?:by|per|based on (?:its|their|the|each)|depending on (?:its|their|the)|according to (?:its|their|the))\s+([a-z][a-z_]{2,30})\b")
_FIELD_STOP = frozenset({"the", "and", "its", "their", "each", "who", "what", "when", "then", "hand", "email", "default", "owner", "someone",
                        "anyone", "people", "person", "mail", "phone", "hour", "hours", "day", "days", "week", "weeks", "end", "way", "us", "them"})
COMMON_FIELDS = ("category", "amount", "region", "priority", "type")


def extract_field(text: str | None) -> str | None:
    """The deciding field in "by region" / "per category" / "based on its category"."""
    for m in _FIELD_RE.finditer(str(text or "").lower()):
        w = m.group(1)
        if w not in _FIELD_STOP and not w.isdigit():
            return w
    return None


def extract_choice(text: str | None, options: Iterable[str]) -> str | None:
    """The one option of a choice slot that the request names (a word match); None when none or several do."""
    t = str(text or "").lower()
    hits = [o for o in options if re.search(rf"(?<![a-z0-9]){re.escape(str(o).lower())}s?(?![a-z0-9])", t)]
    return hits[0] if len(hits) == 1 else None


def slugify_field(value: Any) -> str | None:
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return s if s and re.match(r"^[a-z]", s) and len(s) <= 40 else None


# ── answers ───────────────────────────────────────────────────────────────────────────────────────────────────────────


class AnswerInvalid(ValueError):
    """A value does not fit its slot; ``str(e)`` is a sentence for the person."""


def _one(slot_type: str, value: Any, slot: Mapping[str, Any], roles: list[Mapping[str, Any]], allowed_caps: set[str] | None) -> Any:
    if slot_type == "role":
        if isinstance(value, Mapping) and isinstance(value.get("new_role"), str) and value["new_role"].strip():
            return {"new_role": value["new_role"].strip()[:80]}
        key = resolve_role(value, roles)
        if key is None:
            raise AnswerInvalid(f"'{str(value)[:60]}' is not a role this organisation knows. Pick one from the list, or add a new role.")
        return key
    if slot_type == "duration":
        s = str(value or "").strip().lower()
        if _DURATION_VALUE.match(s):
            return s
        d = extract_duration(s)
        if d is None:
            raise AnswerInvalid("Give a length of time such as 4h, 48 hours or 2 business days.")
        return d
    if slot_type == "cron":
        s = str(value or "").strip()
        if is_cron(s):
            return " ".join(s.split())
        c = extract_cron(s)
        if c is None:
            raise AnswerInvalid("Give a schedule such as daily, every Monday at 8, or a five-field cron expression.")
        return c
    if slot_type == "field":
        f = slugify_field(value)
        if f is None:
            raise AnswerInvalid("Give a field name such as category or region.")
        return f
    if slot_type == "choice":
        opts = [str(o) for o in slot.get("options") or []]
        for o in opts:
            if str(value or "").strip().lower() == o.lower():
                return o
        raise AnswerInvalid(f"Pick one of: {', '.join(opts)}.")
    if slot_type == "api_capability":
        s = str(value or "").strip()
        if not re.match(r"^(api|node):\S+$", s) or (allowed_caps is not None and s not in allowed_caps):
            raise AnswerInvalid("Pick one of the available channels." if allowed_caps else "That is not an available channel.")
        return s
    s = str(value if not isinstance(value, (dict, list)) else "").strip()
    if not s:
        raise AnswerInvalid("This needs a value.")
    return s[:MAX_TEXT]


def validate_value(slot: Mapping[str, Any], value: Any, roles: Iterable[Mapping[str, Any]], *, allowed_caps: set[str] | None = None) -> Any:
    """The canonical stored form of ``value`` for ``slot`` (a concept slot definition). Raises ``AnswerInvalid``.

    A ``multiple`` slot takes a list (or a comma-separated string); a ``multiple`` role slot also takes ``{value: role}``, stored as a
    dict whose values are canonical roles."""
    roles = list(roles)
    t = str(slot.get("type"))
    if slot.get("multiple"):
        if t == "role" and isinstance(value, Mapping) and "new_role" not in value:
            out = {str(k)[:60]: _one(t, v, slot, roles, allowed_caps) for k, v in value.items()}
            if not out:
                raise AnswerInvalid("This needs at least one value.")
            return out
        items = value if isinstance(value, list) else [x for x in re.split(r"\s*,\s*", str(value)) if x] if isinstance(value, str) else [value]
        if not items:
            raise AnswerInvalid("This needs at least one value.")
        return [_one(t, v, slot, roles, allowed_caps) for v in items]
    return _one(t, value, slot, roles, allowed_caps)
