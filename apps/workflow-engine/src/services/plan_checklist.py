"""The deployment checklist: what a drafted plan still lacks to run for real, as items a person picks from.

:func:`deployment_checklist` looks at the drafted plan (code, no model) for the gaps every deployment has - who uses it,
what data it starts with, integrations and credentials, notifications, approval rules, limits and retention, reports -
and returns one item per gap. An item is a question dict (``id``, ``topic``, ``question``, ``why``, ``options``,
``default``, ``source``) plus ``group`` (the heading it sits under), ``multi`` (pick several), ``suggested`` (the options
preselected), ``other`` (free text allowed) and ``apply`` (the plan fact its answer settles). The model's own items
(``emit_plan_core.checklist``) go through :func:`model_items` into the same shape.

An answer is the picked options joined by ``"; "`` plus any free text. :func:`apply_checklist_answers` writes the picks into
the plan deterministically (``roles_note``, ``nfr``, table retention, secrets) and reports whether any free text is left
for the model to interpret (``needs_model``).
"""

from __future__ import annotations

import re
from typing import Any

from . import project_plan as pp
from .backend_templates import gaps

SEP = "; "
GROUPS = {"roles": "People and access", "seed": "Data", "credentials": "Integrations", "notifications": "Notifications",
          "approvals": "Rules", "limits": "Limits", "retention": "Limits", "reports": "Reports"}
_LOG_LIKE = re.compile(r"(?:log|logs|event|events|audit|history|run|runs|result|results|attempt|attempts)$")
_NONE = ("none", "no notifications", "nothing")
_ROLE_TABLE = re.compile(r"(?:^|_)(?:approvers?|managers?|admins?|admin_users|roles?|staff|reviewers?|user_roles|members)$")
PREVIEW_USERS_OPTION = "Seed the preview users as those roles (alice = staff, bob = manager, carol = admin)"
PREVIEW_USERS_NOTE = "preview users seeded as roles (alice = staff, bob = manager, carol = admin)"


def has_role_table(p: dict[str, Any]) -> bool:
    """The plan keeps people and their roles in a table (approvers, managers, admins, roles, staff ...)."""
    return any(_ROLE_TABLE.search(str(t.get("name") or "").lower()) for t in p["data"]["tables"])


def wants_retry_question(p: dict[str, Any]) -> bool:
    """Retries for outside calls only matter when the plan talks to something outside: integrations or secrets."""
    return bool(p["integrations"] or p["secrets"])


def wants_environment_question(p: dict[str, Any], envs: list[str] | None = None) -> bool:
    """Per-environment behaviour only matters with more than one environment whose configuration differs."""
    named = {e for e in (envs or []) if e} | {e["name"] for e in p["environments"]}
    if len(named) < 2:
        return False
    urls = {e["base_url"] for e in p["environments"] if e["base_url"]}
    return len(urls) > 1 or any(e["refused_actions"] for e in p["environments"]) or any(s["environments"] for s in p["secrets"])


_RETENTION_WORDS = re.compile(r"retention|retain|retained|compliance|complian|gdpr|purge|\bdelet|archiv|expire|expiry|keep (?:for|history)|audit trail",
                              re.I)
_IMPORT_WORDS = re.compile(r"\bimport|bulk|csv|spreadsheet|excel|migrat|legacy|existing (?:data|tool|records)|upload", re.I)
_PAGING_WORDS = re.compile(r"pagina|per page|rows per|large|thousands|millions|\bhigh.?volume|big (?:table|list)|many (?:rows|records)|"
                           r"rate.?limit|requests per|upload|file size|\bquota|throttl", re.I)


def _mentions(pattern: re.Pattern[str], plan: dict[str, Any], prompt: str = "") -> bool:
    """The request or the plan's own text (summary, notes, table purposes) names the topic."""
    bits = [prompt, plan.get("summary") or "", plan.get("roles_note") or ""]
    bits += [str(t.get("purpose") or t.get("description") or "") for t in plan["data"]["tables"]]
    return bool(pattern.search(" ".join(str(b) for b in bits)))


def wants_retention_question(plan: dict[str, Any], prompt: str = "") -> bool:
    """Retention only matters when the request talks about keeping, purging, deleting or compliance."""
    return _mentions(_RETENTION_WORDS, pp.normalize(plan), prompt)


def wants_import_question(plan: dict[str, Any], prompt: str = "") -> bool:
    """Import only matters when the request talks about importing, bulk loads, CSVs or existing data."""
    return _mentions(_IMPORT_WORDS, pp.normalize(plan), prompt)


def wants_limits_question(plan: dict[str, Any], prompt: str = "") -> bool:
    """Paging and upload limits only matter when the request expects large tables, uploads or rate limits."""
    return _mentions(_PAGING_WORDS, pp.normalize(plan), prompt)


def prune_domain_questions(questions: list[dict[str, Any]], plan: dict[str, Any], envs: list[str] | None = None,
                           prompt: str = "") -> list[dict[str, Any]]:
    """Drop the domain questions the plan cannot need: retries without integrations, environments without differing
    environments, retention and import when neither the request nor the plan mentions them."""
    p = pp.normalize(plan)
    out = []
    for q in questions:
        if q.get("source") == "domain":
            if q.get("topic") == "retention" and not wants_retention_question(p, prompt):
                continue
            if q.get("topic") == "import" and not wants_import_question(p, prompt):
                continue
            if q.get("topic") == "failure" and not wants_retry_question(p):
                continue
            if q.get("topic") == "environments" and not wants_environment_question(p, envs):
                continue
        out.append(q)
    return out


def _item(key: str, question: str, why: str, options: list[str], suggested: list[str], *, multi: bool = False,
          context: str = "", group: str | None = None) -> dict[str, Any]:
    opts = gaps.clean_options(options, context)
    sugg = [s for s in suggested if s in opts] or opts[:1]
    return {"id": f"req_{key}", "topic": key, "group": group or GROUPS.get(key, "Other"), "question": question, "why": why,
            "options": opts, "default": SEP.join(sugg) if sugg else None, "suggested": sugg, "multi": multi, "other": True,
            "source": "checklist", "apply": key}


def _roles(p: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for t in p["data"]["tables"]:
        for x in (t.get("state_machine") or {}).get("transitions") or []:
            for who in re.split(r"[,/]| or | and ", x.get("who") or ""):
                w = who.strip()
                if w and w.lower() not in ("anyone", "any", "system", "*") and w not in seen:
                    seen.append(w)
    return seen


def deployment_checklist(plan: dict[str, Any], prompt: str = "", facts: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The deterministic checks. Each is skipped when the plan already settles it (a recorded answer, or the fact is set)."""
    p = pp.normalize(plan)
    done = pp.answered_topics(p)
    nfr = p["nfr"]
    tables = p["data"]["tables"]
    ctx = prompt + " " + p["summary"]
    out: list[dict[str, Any]] = []

    if "roles" not in done and not p["roles_note"]:
        inferred = _roles(p)
        base = inferred + [r for r in ("Admin", "Member", "Viewer") if r.lower() not in {i.lower() for i in inferred}]
        opts, sugg = base[:6], inferred[:3] or ["Admin", "Member"]
        why = "Roles decide which screens and actions each person gets."
        if has_role_table(p):
            opts, sugg = opts[:5] + [PREVIEW_USERS_OPTION], sugg + [PREVIEW_USERS_OPTION]
            why += " The preview's 'View as' users (Alice, Bob, Carol) are seeded into the role table so each role can be tried; pick other to type real ids."
        out.append(_item("roles", "Who uses this app, and what can each role do?", why, opts, sugg, multi=True, context=ctx))
    if tables and "seed" not in done and "import" not in done and not nfr.get("seed_data"):
        out.append(_item("seed", "What data should the app start with?", "Empty screens are hard to judge; sample rows are easy to delete.",
                         ["Realistic sample data", "Start empty", "I will import a CSV or spreadsheet"], ["Realistic sample data"],
                         context=ctx))
    secrets_needed = [s["name"] for s in p["secrets"]] + [i["key"] for i in p["integrations"] if i["kind"] in ("http", "email", "other")]
    if secrets_needed and not ({"credentials", "secret_names", "external_systems"} & set(done)):
        out.append(_item("credentials", "How should the app reach " + ", ".join(dict.fromkeys(secrets_needed))[:120] + "?",
                         "Secrets are stored once in the project and only sent to their allowed hosts.",
                         ["Add the credentials later in Secrets (names are planned)", "No real credentials yet: use sample responses"],
                         ["Add the credentials later in Secrets (names are planned)"], context=ctx))
    if "notifications" not in done and not nfr.get("notifications") and any(t.get("state_machine") for t in tables):
        out.append(_item("notifications", "Who should be told when a record changes state?", "Approvals stall when nobody notices them.",
                         ["In-app inbox", "Email", "Slack message", "Webhook", "No notifications"], ["In-app inbox"], multi=True,
                         context=ctx))
    review = [s for t in tables for s in ((t.get("state_machine") or {}).get("transitions") or [])
              if re.search(r"approv|review|reject|sign", (s["to"] + " " + s["from"]).lower())]
    if review and "approvals" not in done and not nfr.get("approvals"):
        out.append(_item("approvals", "How many approvals does a record need?", "This sets who can move a record to its final state.",
                         ["One approver", "Two approvers", "Any team member", "Approver cannot be the author"],
                         ["One approver"], context=ctx))
    if tables and "limits" not in done and not nfr.get("limits") and wants_limits_question(p, prompt):
        out.append(_item("limits", "Which limits should the app enforce?", "Limits keep lists fast and uploads safe.",
                         ["25 rows per page", "100 rows per page", "10 MB per upload", "60 requests per minute per user"],
                         ["25 rows per page", "10 MB per upload"], multi=True, context=ctx))
    if any(_LOG_LIKE.search(t["name"]) and t["retention"]["policy"] == "keep" for t in tables) and "retention" not in done \
            and wants_retention_question(p, prompt):
        out.append(_item("retention", "How long should logs and run history be kept?", "Old rows are purged on a schedule.",
                         ["Keep forever", "1 year", "90 days", "30 days"], ["1 year"], context=ctx))
    if tables and "reports" not in done and not nfr.get("reports"):
        out.append(_item("reports", "Which reports or exports do you need?", "Reports are built from the plan's tables, not guessed later.",
                         ["Counts by status", "Trend over time", "Export to CSV", "No reports"], ["Counts by status", "Export to CSV"],
                         multi=True, context=ctx))
    return out


def model_items(raw: Any, context: str = "") -> list[dict[str, Any]]:
    """The model's own checklist entries (``topic``, ``question``, ``options``, ``suggested``, ``multi``) in the item shape.
    Their answers need the model to apply them, so the source stays ``model``."""
    out: list[dict[str, Any]] = []
    for n, x in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(x, dict) or not str(x.get("question") or "").strip():
            continue
        opts = gaps.clean_options(x.get("options"), context + " " + str(x["question"]))
        sugg = [s for s in (x.get("suggested") if isinstance(x.get("suggested"), list) else [x.get("suggested")]) if s in opts] \
            or opts[:1]
        topic = re.sub(r"[^a-z0-9_]", "_", str(x.get("topic") or "other").lower())[:40] or "other"
        out.append({"id": f"req_m{n + 1}_{topic}"[:60], "topic": topic, "group": str(x.get("group") or topic.replace("_", " ").title())[:40],
                    "question": str(x["question"])[:300], "why": str(x.get("why") or "")[:300], "options": opts,
                    "default": SEP.join(sugg) if sugg else None, "suggested": sugg, "multi": bool(x.get("multi")), "other": True,
                    "source": "model"})
    return out


def decorate(q: dict[str, Any]) -> dict[str, Any]:
    """Any question as a checklist item: heading, single / multi, suggested picks, free-text other."""
    out = dict(q)
    out.setdefault("group", GROUPS.get(q["topic"]) or str(q["topic"]).replace("_", " ").capitalize())
    out.setdefault("multi", False)
    out.setdefault("suggested", [q["default"]] if q.get("default") and q["default"] in (q.get("options") or []) else [])
    out.setdefault("other", True)
    return out


def merge_items(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items from several lists, one per topic (the first list wins), the plan-gap ones (own topics) always kept."""
    out: list[dict[str, Any]] = []
    topics: set[str] = set()
    for lst in lists:
        taken = set(topics)     # a list may hold several items of one topic (they are combined later); other lists do not
        for q in lst:
            if q["topic"] in taken and q.get("source") != "plan":
                continue
            if q["id"] in {o["id"] for o in out}:
                continue
            topics.add(q["topic"])
            out.append(q)
    return out


def parse_answer(q: dict[str, Any], answer: str) -> tuple[list[str], str]:
    """(picked options, free text) of an answer."""
    opts = set(q.get("options") or [])
    parts = [x.strip() for x in str(answer or "").split(SEP.strip()) if x.strip()]
    picked = [x for x in parts if x in opts]
    other = SEP.join(x for x in parts if x not in opts)
    if not picked and not other and answer and answer.strip() in opts:
        picked = [answer.strip()]
    return picked, other


_DAYS = {"1 year": 365, "90 days": 90, "30 days": 30}
_PAGE = re.compile(r"(\d+) rows per page")


def apply_checklist_answers(plan: dict[str, Any], questions: list[dict[str, Any]], answers: dict[str, str]) -> tuple[dict[str, Any], bool]:
    """Write checklist answers into the plan. Returns the plan and whether free text remains that only the model can place."""
    p = pp.normalize(plan)
    needs_model = False
    for q in questions:
        a = str(answers.get(q["id"]) or "").strip()
        if not a or q.get("source") not in ("checklist", "model", "check", "domain"):
            continue
        picked, other = parse_answer(q, a)
        if q.get("source") != "checklist":
            needs_model = needs_model or True
            continue
        if other:
            needs_model = True
        key = q.get("apply")
        names = [PREVIEW_USERS_NOTE if n == PREVIEW_USERS_OPTION else n for n in picked] + ([other] if other else [])
        if key == "roles":
            p["roles_note"] = "Roles: " + ", ".join(names)
            for app in p["apps"]:
                if not app["users"]:
                    app["users"] = ", ".join(names)
        elif key == "seed":
            p["nfr"]["seed_data"] = names[0] if names else ""
        elif key == "notifications":
            chosen = [n for n in names if n.lower() not in _NONE]
            p["nfr"]["notifications"] = chosen
            _notification_secrets(p, chosen)
        elif key == "approvals":
            p["nfr"]["approvals"] = names[0] if names else ""
        elif key == "limits":
            p["nfr"]["limits"] = names
            for n in names:
                m = _PAGE.match(n)
                if m:
                    p["nfr"]["page_size"] = int(m.group(1))
        elif key == "retention":
            days = next((_DAYS[n] for n in picked if n in _DAYS), None)
            p["nfr"]["retention"] = names[0] if names else ""
            if days:
                for t in p["data"]["tables"]:
                    if _LOG_LIKE.search(t["name"]) and t["retention"]["policy"] == "keep":
                        t["retention"] = {"policy": "days", "value": days}
        elif key == "reports":
            p["nfr"]["reports"] = [n for n in names if n.lower() != "no reports"]
    return p, needs_model


def _notification_secrets(p: dict[str, Any], chosen: list[str]) -> None:
    """A channel that needs a credential adds it: Slack has a known host; a generic webhook leaves its host to a follow-up."""
    have = {s["name"] for s in p["secrets"]}
    if any(c.lower().startswith("slack") for c in chosen) and "SLACK_WEBHOOK_URL" not in have:
        p["secrets"].append({"name": "SLACK_WEBHOOK_URL", "purpose": "post state changes to Slack", "environments": [],
                             "allowed_hosts": ["hooks.slack.com"]})
    if any(c.lower().startswith("webhook") for c in chosen) and "NOTIFY_WEBHOOK_URL" not in have:
        p["secrets"].append({"name": "NOTIFY_WEBHOOK_URL", "purpose": "post state changes to a webhook", "environments": [],
                             "allowed_hosts": []})


def suggested_answers(questions: list[dict[str, Any]]) -> dict[str, str]:
    """"Accept all suggestions": every question's suggested picks (or its default, or its first option)."""
    out: dict[str, str] = {}
    for q in questions:
        sugg = q.get("suggested") or ([q["default"]] if q.get("default") else (q.get("options") or [])[:1])
        out[q["id"]] = SEP.join(str(s) for s in sugg) if sugg else "No preference: use the sensible default"
    return out
