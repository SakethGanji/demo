"""Stage 6 AI policy: model allowlist (residency hook) and secret / PII scrubbing (DESIGN-STAGE6-AI-BUILDER §10.2, §10.3).

Model policy: ``allowed(model) -> (bool, reason)``. One function, so a per-project policy table or a call to the
bank's model gateway replaces one body later.

Scrubbing: ``scrub_pii(text) -> (text, counts)`` masks emails, phones, SSNs, card numbers (Luhn), IBANs (mod 97) and
account numbers that follow the word account/acct. ``scrub_secrets`` composes the existing 3B ``redact_for_prompt``
(imported, not copied) with PEM / key-shape / ``password=`` patterns. ``scrub_payload`` runs both over a prompt payload.
Regex scrubbing is imperfect by nature (DESIGN §16 risk 10): the counts are shown to the user and real DLP is the
bank's gateway.
"""

from __future__ import annotations

import copy
import os
import re
import unicodedata
from typing import Any

DEFAULT_MODELS = "gemini-3.5-flash"
DEFAULT_PROVIDERS = "gemini"

PII_KINDS = ("email", "phone", "ssn", "card", "iban", "account")
_TOKENS = {"email": "[EMAIL]", "phone": "[PHONE]", "ssn": "[SSN]", "card": "[CARD]", "iban": "[IBAN]", "account": "[ACCOUNT]"}


# ── model policy (§10.2) ────────────────────────────────────────────────────────────────────


def _csv(name: str, default: str) -> list[str]:
    raw = os.environ.get(name)
    raw = default if raw is None or not raw.strip() else raw
    return [p.strip() for p in raw.split(",") if p.strip()]


def allowed_models() -> list[str]:
    return _csv("WF_AI_ALLOWED_MODELS", DEFAULT_MODELS)


def allowed_providers() -> list[str]:
    return _csv("WF_AI_ALLOWED_PROVIDERS", DEFAULT_PROVIDERS)


def region() -> str | None:
    """``WF_AI_REGION``: recorded on every proposal; informational until the owner names regions."""
    return (os.environ.get("WF_AI_REGION") or "").strip() or None


def send_run_values() -> bool:
    """``WF_AI_SEND_RUN_VALUES=1`` lets run values (not just shapes) reach the model, after scrubbing (O3)."""
    return os.environ.get("WF_AI_SEND_RUN_VALUES", "0").strip().lower() in ("1", "true", "yes")


def provider_of(model: str) -> str:
    """The provider name for ``model`` (same routing as llm_provider)."""
    m = (model or "").lower()
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("meta/", "llama")):
        return "llama"
    return "openai"


def allowed(model: str | None) -> tuple[bool, str]:
    """``(True, "")`` when ``model`` may be used, else ``(False, reason)``."""
    if not model or not isinstance(model, str):
        return False, "no model given"
    models = allowed_models()
    if model not in models:
        return False, f"model '{model}' is not on the allowed list ({', '.join(models)})"
    provider = provider_of(model)
    providers = allowed_providers()
    if provider not in providers:
        return False, f"provider '{provider}' is not on the allowed list ({', '.join(providers)})"
    return True, ""


# ── secrets (§10.3) ─────────────────────────────────────────────────────────────────────────

_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
_KEY_SHAPES = (
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),            # Google API key
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),             # OpenAI / Anthropic style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),        # GitHub
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),      # Slack
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),         # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
)
_PAIR_RE = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|secret|api[-_]?key|apikey|access[-_]?token|auth[-_]?token|client[-_]?secret)\b\s*[=:]\s*)"
    r"(?!\*\*\*|\[REDACTED\])(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s\"',;&]{3,})")
CANARY_RE = re.compile(r"CANARY-[0-9a-fA-F-]{8,}")


def _redacted() -> str:
    from ..services.test_ai_service import REDACTED  # 3B's marker, imported lazily (services import the engine)

    return REDACTED


def _known_values(secret_values: set[str] | None) -> list[str]:
    return sorted((v for v in (secret_values or set()) if isinstance(v, str) and len(v) >= 4), key=len, reverse=True)


def _pattern_scrub(text: str, marker: str) -> str:
    text = _PEM_RE.sub(marker, text)
    for rx in _KEY_SHAPES:
        text = rx.sub(marker, text)
    return _PAIR_RE.sub(lambda m: m.group(1) + marker, text)


def _known_scrub(text: str, values: list[str], marker: str) -> str:
    for v in values:
        text = text.replace(v, marker)
    return text


def scrub_secrets(value: Any, secret_values: set[str] | None = None) -> tuple[Any, int]:
    """A copy of ``value`` without secrets, and how many were removed.

    Composes 3B ``redact_for_prompt`` (auth-like keys, ``$secrets`` refs, bearer tokens, credential query values and every
    known secret value) with PEM blocks, well-known API-key shapes and ``password=`` style pairs.
    """
    from ..services.test_ai_service import redact_for_prompt

    marker = _redacted()
    values = _known_values(secret_values)
    base = redact_for_prompt(value, secret_values)

    def _walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {(_pattern_scrub(_known_scrub(k, values, marker), marker) if isinstance(k, str) else k): _walk(v)
                    for k, v in x.items()}
        if isinstance(x, list):
            return [_walk(v) for v in x]
        if isinstance(x, str):
            return _pattern_scrub(x, marker)
        return x

    out = _walk(base)
    return out, max(0, _count(out, marker) - _count(value, marker))


def _count(value: Any, marker: str) -> int:
    if isinstance(value, str):
        return value.count(marker)
    if isinstance(value, dict):
        return sum(_count(v, marker) for v in value.values())
    if isinstance(value, list):
        return sum(_count(v, marker) for v in value)
    return 0


def find_secret(value: Any, secret_values: set[str] | None = None) -> str | None:
    """A short reason when ``value`` (any JSON) holds a known secret value or a secret-shaped string, else ``None``.

    Used to refuse model OUTPUT (op args) and to scan recorded replies. Never returns the secret itself.
    """
    values = _known_values(secret_values)

    def _walk(x: Any) -> str | None:
        if isinstance(x, dict):
            for k, v in x.items():
                r = _walk(k) or _walk(v)
                if r:
                    return r
            return None
        if isinstance(x, list):
            for v in x:
                r = _walk(v)
                if r:
                    return r
            return None
        if isinstance(x, str):
            for v in values:
                if v in x:
                    return "a known secret value"
            if _PEM_RE.search(x):
                return "a private key block"
            if CANARY_RE.search(x):
                return "a canary secret"
            if any(rx.search(x) for rx in _KEY_SHAPES):
                return "an API key or token"
            if _PAIR_RE.search(x):
                return "a password or secret assignment"
        return None

    return _walk(value)


# ── PII (§10.3) ─────────────────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")

_IBAN_LEN = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29, "CH": 21,
    "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18,
    "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27,
    "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29,
    "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26,
    "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}
_IBAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{2,4}){3,8}(?![A-Za-z0-9])")
# 13-19 digits, groups joined only by one space or hyphen; never touches amounts (comma/dot formatted) or longer numbers.
# A run is digit groups joined by single spaces/hyphens; _mask_cards tries every contiguous window of groups (13-19 digits).
_CARD_RE = re.compile(r"(?<![\d.,$\w-])\d+(?:[ -]\d+)*(?![\d]|[.,]\d)")
_SSN_RE = re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}(?![\d-])")
_SSN_BARE_RE = re.compile(r"(?i)(\b(?:ssn|social security(?: number| no\.?)?)\b\s*[:#=]?\s*)(?!000|666|9\d\d)(\d{3})(?!00)(\d{2})(?!0000)(\d{4})\b")
_ACCOUNT_RE = re.compile(r"(?i)(\b(?:account|acct)\b(?:\s*(?:number|no\.?|num|#))?\s*[:#=]?\s*)(\d{8,17})(?!\d)")
_PHONE_INTL_RE = re.compile(r"(?<![\w+])\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}(?!\d)")
_PHONE_US_RE = re.compile(r"(?<![\w.$,/-])(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\d-])")
_PHONE_BARE_RE = re.compile(r"(?i)(\b(?:phone|tel|telephone|mobile|cell|call|fax|whatsapp)\b\s*(?:number|no\.?|#)?\s*[:#=]?\s*)(1?\d{10})(?!\d)")


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _plausible_issuer(d: str) -> bool:
    """Guards against Luhn-valid digit runs that are not cards (epoch millis, ids): the leading digits must be a major issuer."""
    n = len(d)
    if d[0] == "4":
        return n in (13, 16, 19)
    if d[:2] in ("34", "37"):
        return n == 15
    if d[:2] in ("51", "52", "53", "54", "55") or 2221 <= int(d[:4]) <= 2720:
        return n == 16
    if d[:4] == "6011" or d[:2] in ("65", "62") or d[:3] in ("644", "645", "646", "647", "648", "649"):
        return n in (16, 17, 18, 19)
    if d[:2] in ("36", "38", "39") or d[:3] in ("300", "301", "302", "303", "304", "305"):
        return n in (14, 15, 16)
    if 3528 <= int(d[:4]) <= 3589:
        return n in (16, 17, 18, 19)
    if d[:2] in ("50", "56", "57", "58", "67"):  # Maestro
        return 12 <= n <= 19
    return False


def iban_ok(raw: str) -> bool:
    s = re.sub(r"[ -]", "", raw)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    want = _IBAN_LEN.get(s[:2])
    if want is None or len(s) != want:
        return False
    rearranged = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in rearranged)
    return int(num) % 97 == 1


def scrub_pii(text: str) -> tuple[str, dict[str, int]]:
    """Mask PII in ``text``. Returns ``(clean_text, counts)`` with a count for each kind in ``PII_KINDS`` (zeros included).

    False-positive guards: amounts and years (``$1,234,567.89``, ``2026``), ISO and slash dates, ZIP and ZIP+4 codes, order
    ids and epoch timestamps are not touched. Cards need Luhn AND a major-issuer prefix; IBANs need the country length and
    mod-97; SSNs are dashed/spaced ``AAA-GG-SSSS`` (bare digits only after ``SSN``); phones are international (``+...``) or
    formatted US/national (bare 10-11 digits only after ``phone/tel/mobile/cell``); account numbers only after the word
    ``account``/``acct``. ``$500`` thresholds are kept.
    """
    counts = {k: 0 for k in PII_KINDS}
    if not isinstance(text, str) or not text:
        return text, counts
    if not text.isascii():  # fullwidth / other Unicode decimal digits must not hide a number from detection
        text = "".join(str(unicodedata.decimal(c)) if not c.isascii() and c.isdecimal() else c for c in text)

    def sub(rx: re.Pattern[str], kind: str, s: str, ok: Any = None, group: int | None = None) -> str:
        def repl(m: re.Match[str]) -> str:
            if ok is not None and not ok(m):
                return m.group(0)
            counts[kind] += 1
            if group is not None:
                return m.group(group) + _TOKENS[kind]
            return _TOKENS[kind]
        return rx.sub(repl, s)

    text = sub(_EMAIL_RE, "email", text)
    text = sub(_IBAN_RE, "iban", text, ok=lambda m: iban_ok(m.group(0)))

    def mask_cards(m: re.Match[str]) -> str:
        run = m.group(0)
        groups = list(re.finditer(r"\d+", run))
        out: list[str] = []
        pos, i = 0, 0
        while i < len(groups):
            hit = None
            digits = ""
            for j in range(i, len(groups)):
                digits += groups[j].group(0)
                if len(digits) > 19:
                    break
                if len(digits) >= 13 and luhn_ok(digits) and _plausible_issuer(digits):
                    hit = j  # keep extending: prefer the longest valid window from this start
            if hit is None:
                i += 1
                continue
            out.append(run[pos:groups[i].start()])
            out.append(_TOKENS["card"])
            counts["card"] += 1
            pos, i = groups[hit].end(), hit + 1
        out.append(run[pos:])
        return "".join(out)

    text = _CARD_RE.sub(mask_cards, text)
    text = sub(_SSN_RE, "ssn", text)
    text = _SSN_BARE_RE.sub(lambda m: (counts.__setitem__("ssn", counts["ssn"] + 1), m.group(1) + _TOKENS["ssn"])[1], text)
    text = sub(_ACCOUNT_RE, "account", text, group=1)

    def intl_ok(m: re.Match[str]) -> bool:
        return 8 <= len(re.sub(r"\D", "", m.group(0))) <= 15

    text = sub(_PHONE_INTL_RE, "phone", text, ok=intl_ok)
    text = sub(_PHONE_US_RE, "phone", text)
    text = sub(_PHONE_BARE_RE, "phone", text, group=1)
    return text, counts


def has_high_confidence_pii(text: str) -> bool:
    """Cards, IBANs and SSNs only (no emails or phones: model replies legitimately carry example ones). Used by the recorder."""
    _, c = scrub_pii(text)
    return bool(c["card"] or c["iban"] or c["ssn"])


def _merge(into: dict[str, int], add: dict[str, int]) -> None:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v


def scrub_value_pii(value: Any) -> tuple[Any, dict[str, int]]:
    """``scrub_pii`` over every string (keys excluded) inside any JSON value."""
    counts = {k: 0 for k in PII_KINDS}

    def _walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: _walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_walk(v) for v in x]
        if isinstance(x, str):
            clean, c = scrub_pii(x)
            _merge(counts, c)
            return clean
        return x

    return _walk(value), counts


def scrub_payload(payload: dict[str, Any], secret_values: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scrub a whole prompt payload. Returns ``(clean, {"secrets": n, "pii": {kind: n}, "pii_total": n})``.

    Secrets are removed everywhere; PII only under ``untrusted_*`` keys (the request, OpenAPI summaries, descriptions,
    code bodies, run error messages), which is where free text from users and documents lives.
    """
    clean, n_secrets = scrub_secrets(copy.deepcopy(payload), secret_values)
    pii = {k: 0 for k in PII_KINDS}
    if isinstance(clean, dict):
        clean = _scrub_untrusted(clean, pii)
    return clean, {"secrets": n_secrets, "pii": pii, "pii_total": sum(pii.values())}


def _scrub_untrusted(x: Any, pii: dict[str, int], inside: bool = False) -> Any:
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            flag = inside or (isinstance(k, str) and k.startswith("untrusted_"))
            out[k] = _scrub_untrusted(v, pii, flag)
        return out
    if isinstance(x, list):
        return [_scrub_untrusted(v, pii, inside) for v in x]
    if inside and isinstance(x, str):
        clean, c = scrub_pii(x)
        _merge(pii, c)
        return clean
    return x


def scrub_request(text: str, secret_values: set[str] | None = None) -> tuple[str, dict[str, Any]]:
    """The user's request text: secrets and PII removed. Returns ``(clean, {"secrets": n, "pii": {...}, "pii_total": n})``."""
    clean, n = scrub_secrets(text or "", secret_values)
    clean, pii = scrub_pii(clean)
    return clean, {"secrets": n, "pii": pii, "pii_total": sum(pii.values())}
