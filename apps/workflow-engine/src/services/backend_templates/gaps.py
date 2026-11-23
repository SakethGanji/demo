"""Domain-aware requirements dialogue for the backend builder.

``gap_questions`` in ``module_generator`` checks the SPEC's shape (no fields, unknown refs). This file asks what a careful
developer asks about the DOMAIN before building: dangerous actions, who approves what, state machines, retention, ids,
imports, environments, failure semantics, concurrency, credentials and identity. It also removes options that make no
sense or are unsafe (mock credentials for a bank, a role read from a request header), and plans a second round when the
answers to the first open new gaps. Deterministic: no LLM, so it is testable and cannot be talked out of a question.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_ROUNDS = 2

# Options that would produce an insecure or meaningless design. Dropped from every question, whoever wrote it.
_DENY = re.compile(
    r"mock(ed)?[ _-]?(credential|login|password|auth|user)|fake[ _-]?(credential|login|password)|dummy[ _-]?(credential|password)|"
    r"hard-?cod|plain-?text|in the browser|browser[- ]supplied|browser[- ]side|user types? (the )?(password|credential|token)|"
    r"x-?(user|role|actor)[\w-]* header|(role|identity|actor|user)\b.{0,30}\bfrom (a |the )?(request )?header|"
    r"trust (the )?(client|header|caller)|skip auth|no auth(entication)?\b|credentials? (in|from) the (request|body|ui)",
    re.IGNORECASE)
_MONEY = re.compile(r"\b(bank|banking|payment|payments|money|ledger|wire|transfer|fintech|loan|card|account holder|settlement)\b", re.IGNORECASE)

SECURITY_RULES = """Security (offer and write nothing that breaks these):
- Credentials are project secrets, referenced by name (studio.secret('NAME')), resolved server-side. Never take a password, token
  or key from the browser or request body, never store one in a table, never offer mock/demo credentials for a real system.
- Identity is `req.caller` (kind, id, name, email, roles): the engine resolves it server-side and strips forged identity headers.
  A role or actor from a header or body is a CLAIM: grant nothing from it; check approvers against a data table by `caller.id`.
  Restrict state-changing routes with auth="app_token" unless asked otherwise.
- A dangerous action (money, delete, apply-a-fix, deploy) needs a stated guard: preview then confirm, second approver, limit, or
  environment restriction."""


# Most dangerous gaps first: the question list is capped, and an insecure default costs more than a missing report column.
_PRIORITY = ["safety", "credentials", "identity", "approvals", "state_machines", "concurrency", "failure", "environments", "ids",
             "retention", "import", "notifications"]


_WRITE_INTENT = (r"\b(creat\w*|add\w*|edit\w*|updat\w*|submit\w*|approv\w*|reject\w*|issu\w*|process\w*|record\w*|"
                 r"log (a|an|the|new)|enter\w*|book\w*|assign\w*|cancel\w*|delet\w*|remov\w*|chang\w*|manag(e|es|ing)\b|"
                 r"track\w*|request\w*|send\w*|pay\w*|refund (a|an|the|it|them|orders?|customers?)|import\w*|upload\w*|"
                 r"write\w*|sav(e|es|ing)\b|mark\w*|clos(e|es|ing)\b|transfer\w*|deploy\w*|"
                 r"workflow|form|crud|triage|review\w*|sign.?off)\b")


def _hay(prompt: str, spec: dict[str, Any]) -> str:
    return (prompt + "\n" + json.dumps({k: v for k, v in spec.items() if k not in ("open_questions", "answered")},
                                       default=str)).lower()


def clean_options(options: Any, context: str = "") -> list[str]:
    """The options that survive: unsafe ones and, in a money context, any 'mock' option are removed."""
    money = bool(_MONEY.search(context or ""))
    out: list[str] = []
    for o in options if isinstance(options, list) else []:
        s = str(o).strip()
        if not s or _DENY.search(s) or (money and re.search(r"\bmock|\bdemo\b|\bfake\b", s, re.IGNORECASE)):
            continue
        out.append(s[:200])
    return out[:8]


def clean_default(default: Any, options: list[str], context: str = "") -> str | None:
    if default is None:
        return None
    d = str(default).strip()
    if not d or _DENY.search(d) or (_MONEY.search(context or "") and re.search(r"\bmock|\bfake\b", d, re.IGNORECASE)):
        return options[0] if options else None
    return d[:300]


def _q(qid: str, topic: str, question: str, why: str, options: list[str], default: str | None = None) -> dict[str, Any]:
    return {"id": qid, "topic": topic, "question": question, "why": why, "options": options,
            "default": default if default is not None else (options[0] if options else None), "source": "domain"}


def _entities(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in spec.get("entities") or [] if isinstance(e, dict)]


def _field_names(spec: dict[str, Any]) -> set[str]:
    return {str(f.get("name")) for e in _entities(spec) for f in e.get("fields") or [] if isinstance(f, dict)}


# Domain question id -> topic, so a stored answer (key ``module:qid``) can be matched to a topic without re-deriving the question.
_ID_TOPIC = {"dom_safety": "safety", "dom_approvals": "approvals", "dom_state_machines": "state_machines",
             "dom_retention": "retention", "dom_ids": "ids", "dom_import": "import", "dom_envs": "environments",
             "dom_failure": "failure", "dom_concurrency": "concurrency", "dom_notify": "notifications",
             "dom_credentials": "credentials", "dom_identity": "identity"}


DOMAIN_TOPICS = tuple(_ID_TOPIC.values())


def topic_of(qid: str, stored: Any = None) -> str | None:
    """Topic of a stored clarify answer: the topic saved with it, else the one its standard question id stands for."""
    if isinstance(stored, str) and stored:
        return stored
    base = str(qid).rsplit(":", 1)[-1]
    if base in _ID_TOPIC:
        return _ID_TOPIC[base]
    for fid, topic, *_ in _FOLLOWUPS:
        if fid == base:
            return topic
    return None


def domain_questions(prompt: str, spec: dict[str, Any], environments: list[str] | None = None,
                     answered: Any = None) -> list[dict[str, Any]]:
    """Questions for the gaps this request has, in priority order. Each one fires only when the request touches its topic.
    Topics in ``answered`` (answers an earlier module's dialogue already collected) are not asked again."""
    done = set(answered or ())
    h = _hay(prompt, spec)
    envs = [e for e in (environments or []) if e]
    ents = _entities(spec)
    wfs = [w for w in spec.get("workflows") or [] if isinstance(w, dict) and w.get("states")]
    out: list[dict[str, Any]] = []

    # a request that only reads (a dashboard, a report, a lookup) has nothing to guard or approve: "refund rate" is a metric
    reads_only = not re.search(_WRITE_INTENT, prompt.lower())
    danger = [] if reads_only else sorted({m.group(0) for m in re.finditer(
        r"\b(refund\w*|transfer\w*|payment\w*|charge\w*|wire\w*|withdraw\w*|disburs\w*|delet\w+|purg\w+|rollback|deploy\w*|"
        r"overwrit\w+|appl(?:y|ies|ying)\b.{0,30}\b(?:fix|change|patch|update)\w*|(?:fix|patch)\w*\b.{0,40}\bappl(?:y|ies|ied|ying)\b|"
        r"auto-?(?:apply|fix|heal)\w*|irreversible|destructive|\bbank\w*|money)", h)})
    if danger:
        out.append(_q("dom_safety", "safety",
                      f"Some actions here cannot be undone or move money/data ({', '.join(d[:40] for d in danger[:5])}). What must be true "
                      "before each one runs?",
                      "Without a guard the first wrong click or a retry does real damage.",
                      ["Preview/dry-run first, then a separate confirm call", "A second person must approve it",
                       "Server-side limits (amount/count per action and per day)",
                       "Refused in the live environment; allowed only in non-live ones",
                       "No guard needed: everything is logged and reversible"],
                      "Preview/dry-run first, then a separate confirm call"))
    if not reads_only and re.search(r"approv|review|sign.?off|triage|reject|authori[sz]|decid", h) or any(
            isinstance(t, dict) and t.get("who") for w in wfs for t in w.get("transitions") or []):
        out.append(_q("dom_approvals", "approvals",
                      "Who approves what? May the person who created or requested an item approve it themself, and is a "
                      "second approver needed for high-risk items?",
                      "Separation of duties changes who may call each transition.",
                      ["A different person than the requester, any lead", "Only a named role (tell me which)",
                       "The requester may approve their own low-risk items only", "Two approvers for high-risk items"],
                      "A different person than the requester, any lead"))
    if wfs:
        desc = "; ".join(f"{w.get('entity') or 'item'} ({', '.join(map(str, w['states'][:6]))})" for w in wfs[:3])
        out.append(_q("dom_state_machines", "state_machines",
                      f"Status lifecycles: {desc}. Which states are final, can a finished item be reopened, and what should an "
                      "illegal move do?",
                      "The route guards are generated from this.",
                      ["Final states stay final; an illegal move returns 409 and changes nothing",
                       "A finished item can be reopened by a lead", "Any state may move to any other (no guard)"],
                      "Final states stay final; an illegal move returns 409 and changes nothing"))
    if re.search(r"history|\blogs?\b|audit|results?|\bruns?\b|artifact|screenshot|events?|trace|record", h):
        out.append(_q("dom_retention", "retention",
                      "How long are runs, results, artifacts and history kept, and may records be deleted?",
                      "Decides soft-retire vs delete routes, and whether a purge job is needed.",
                      ["Keep everything; retire with a status instead of deleting",
                       "Keep 90 days, then purge old results and artifacts",
                       "Keep the last N runs per item (tell me N)",
                       "Allow delete only when nothing references the record"],
                      "Keep everything; retire with a status instead of deleting"))
    id_fields = _field_names(spec) & {"key", "code", "number", "ref", "slug", "identifier", "reference"}
    if id_fields or re.search(r"\b[a-z]{2,5}-\d{2,}\b|\bids?\b like|prefix", h):
        out.append(_q("dom_ids", "ids",
                      "How are business ids (like TC-204) created, and can one be reused after a record is deleted?",
                      "Decides uniqueness checks and whether a counter table is needed.",
                      ["Server-generated, sequential per prefix, never reused",
                       "Typed by the user; must be unique (409 on a clash)",
                       "Server-generated, not human-readable (no business id)"],
                      "Server-generated, sequential per prefix, never reused"))
    if not reads_only and (re.search(r"existing|import|migrat|legacy|spreadsheet|excel|csv|jira|testrail|xray|current tool|old tool", h)
                           or len(ents) >= 3):
        out.append(_q("dom_import", "import",
                      "Is there data in an existing tool to bring in (CSV, Jira, TestRail, spreadsheets)? In what format, "
                      "and what happens to duplicates?",
                      "An import route needs a format, a key column and a duplicate rule.",
                      ["No import needed", "CSV upload; skip rows whose id already exists",
                       "CSV upload; update rows whose id already exists", "One-off migration done outside this backend"],
                      "No import needed"))
    if len(envs) > 1 or spec.get("external_systems") or re.search(r"https?://|\bhosts?\b|target (app|url)|\burl\b|staging|production|\bprod\b", h):
        where = f" This project has the environments {', '.join(envs)}." if envs else ""
        out.append(_q("dom_envs", "environments",
                      f"Should behavior differ per environment (target URLs, secrets), and where must dangerous actions be refused?{where}",
                      "Secrets are scoped per environment; a missing one fails only in that environment.",
                      ["Same behavior in every environment", "Dangerous actions are refused in the live environment",
                       "Targets and secrets differ per environment (same secret names in each)"],
                      "Dangerous actions are refused in the live environment"))
    if re.search(r"\bjobs?\b|external|call(s|ing)? |http|\bapi\b|jira|email|retry|timeout|block|flak|unavailable|runs?\b", h) or spec.get("external_systems"):
        out.append(_q("dom_failure", "failure",
                      "When a run or an outside call fails, is slow, or comes back blocked or flaky: retry how often, and "
                      "which status does the record get (error, blocked, failed)?"
                      + (" Also: what exactly counts as 'flaky' (e.g. both outcomes in the last 10 runs)?" if "flak" in h else ""),
                      "These are different outcomes; merging them hides the cause.",
                      ["No automatic retry: mark error or blocked and let a person re-run",
                       "Retry twice with a pause, then mark error", "Keep polling until a deadline, then mark error"],
                      "No automatic retry: mark error or blocked and let a person re-run"))
    if wfs or re.search(r"\bsync|idempot|approve|concurren|simultan|at the same time|duplicate|twice", h):
        out.append(_q("dom_concurrency", "concurrency",
                      "If two people act on the same record at once, or an approve/sync call is sent twice, what should happen?",
                      "Without a rule a double click corrupts state or double-counts.",
                      ["The second action gets 409; repeating the same action is a harmless no-op",
                       "Last write wins", "Caller sends the version it saw; a mismatch gets 409"],
                      "The second action gets 409; repeating the same action is a harmless no-op"))
    # emailing as an action, not an email column ("search by name/email" asks nothing)
    if re.search(r"notif|slack|\bteams\b|alert|\bpage (me|them|someone|on.?call)|message the|"
                 r"\b(send|sends|sent)\b[^.]{0,30}\be-?mails?\b|\be-?mail(s|ed|ing)? (the|them|me|us|him|her|a|an|when|on|to)\b|"
                 r"by e-?mail|e-?mail (alerts?|notifications?|digests?|summar)", h):
        out.append(_q("dom_notify", "notifications",
                      "Who is notified about what (which events, which channel), and may users mute it?",
                      "The request mentions notifications.", []))
    if re.search(r"credential|password|log.?in|sign.?in|token|api.?key|secret|\bauth", h):
        out.append(_q("dom_credentials", "credentials",
                      "Where do the credentials the backend needs live? (They are never typed into the browser or stored in tables.)",
                      "Credentials must be secret references resolved on the server.",
                      ["Project secrets referenced by name, one per system, same name in every environment",
                       "Project secrets with a different value per environment (dev/live)",
                       "No credentials needed"],
                      "Project secrets referenced by name, one per system, same name in every environment"))
    if spec.get("roles") or re.search(r"\brole|permission|\blead\b|admin|who may|who can", h):
        out.append(_q("dom_identity", "identity",
                      "How should 'who did this' and role limits be handled?",
                      "A role or name read from a header or body can be forged; req.caller is resolved by the engine.",
                      ["Studio sign-in (req.caller): record caller.id and check approvers against a data table by caller.id (recommended)",
                       "Restrict state-changing routes to the studio app (auth=app_token) and treat the team as trusted",
                       "Claimed name from the request body (not secure: anyone can forge it)",
                       "Not needed: single trusted team"],
                      "Studio sign-in (req.caller): record caller.id and check approvers against a data table by caller.id (recommended)"))
    out = [q for q in out if q["topic"] not in done]
    out.sort(key=lambda q: _PRIORITY.index(q["topic"]) if q["topic"] in _PRIORITY else len(_PRIORITY))
    return out


_FOLLOWUPS: list[tuple[str, str, re.Pattern[str], str, str, list[str]]] = [
    ("dom2_approver_roles", "approvals", re.compile(r"second|two approv|named role|specific role|tell me which", re.IGNORECASE),
     "Name the role(s) that may approve, and whether the requester can ever approve their own item.",
     "The previous answer needs a concrete rule per transition.", []),
    ("dom2_limits", "safety", re.compile(r"limit", re.IGNORECASE),
     "Which limits apply (maximum amount or count per action and per day), and who may raise them?",
     "A guard needs numbers.", []),
    ("dom2_retention_numbers", "retention", re.compile(r"last n|tell me n|purge", re.IGNORECASE),
     "What are the exact numbers (days or runs kept), and does the purge remove rows only or artifacts too?",
     "A purge route needs exact bounds.", []),
    ("dom2_retry", "failure", re.compile(r"retry|poll", re.IGNORECASE),
     "How many retries and how long between them? Which errors are retried (timeouts only, or also 5xx)?",
     "Unbounded retries hide an outage.", []),
    ("dom2_import_mapping", "import", re.compile(r"csv|upload|jira|testrail|spreadsheet|xray|migration", re.IGNORECASE),
     "List the columns of the source and how they map to fields; which column is the id; update or skip on a duplicate?",
     "The import route is generated from the mapping.", []),
    ("dom2_id_prefix", "ids", re.compile(r"sequential|prefix|per prefix", re.IGNORECASE),
     "Which prefixes exist (per kind or area), where does each counter start, and are gaps after a delete acceptable?",
     "A counter per prefix needs its own storage.", []),
    ("dom2_secret_names", "credentials", re.compile(r"secret|environment", re.IGNORECASE),
     "Which secret key names (UPPER_SNAKE) does each outside system use?",
     "Names must match the project secrets exactly.", []),
    ("dom2_env", "environments", re.compile(r"refus|live|differ", re.IGNORECASE),
     "Which environment is live, and exactly which actions are refused there?",
     "The guard is generated from this.", []),
]
_GATE: dict[str, str] = {"dom2_approver_roles": "dom_approvals", "dom2_limits": "dom_safety",
                         "dom2_retention_numbers": "dom_retention", "dom2_retry": "dom_failure",
                         "dom2_import_mapping": "dom_import", "dom2_id_prefix": "dom_ids",
                         "dom2_secret_names": "dom_credentials", "dom2_env": "dom_envs"}
_NO_FOLLOWUP = re.compile(r"^\s*(no\b|none|not needed|no import needed|same behavior|keep everything|last write wins|\?|idk|n/?a)", re.IGNORECASE)


_SECRET_NAME = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")


def _names_given(answer: str, spec: dict[str, Any], answers: dict[str, str], known_secrets: Any) -> bool:
    """The secret names are already known: the answer lists UPPER_SNAKE names, or names a secret the project has."""
    if _SECRET_NAME.search(answer):
        return True
    known = {str(k) for k in known_secrets or ()}
    if not known:
        return False
    text = " ".join(str(v) for v in answers.values()) + " " + json.dumps(spec.get("external_systems") or [], default=str)
    return any(k in text for k in known)


def followup_questions(spec: dict[str, Any], answers: dict[str, str], round_no: int = 1,
                       known_secrets: Any = None) -> list[dict[str, Any]]:
    """New questions the first round's answers open (at most 5). Empty in the last round or when nothing new opened.
    ``known_secrets`` (the project's secret names) lets the secret-names follow-up stay quiet when the names are known."""
    if round_no >= MAX_ROUNDS:
        return []
    asked = {str(q.get("id")) for q in spec.get("open_questions") or [] if isinstance(q, dict)}
    out: list[dict[str, Any]] = []
    for qid, topic, pat, question, why, options in _FOLLOWUPS:
        gate = _GATE[qid]
        a = str(answers.get(gate) or "")
        if qid == "dom2_secret_names" and _names_given(a, spec, answers, known_secrets):
            continue
        if gate in asked and a and not _NO_FOLLOWUP.search(a) and pat.search(a) and qid not in asked:
            out.append(_q(qid, topic, question, why, options, None))
    # a vague answer to a model question that decides the design is asked again once, shorter
    return out[:5]
