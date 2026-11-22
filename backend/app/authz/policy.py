"""P19 (plan §6.1, C §1.2–1.3): roles, the action matrix and the decision.

Roles: viewer < member < maintainer < owner. `risk_reviewer` is a separate axis (allowed only where a row says so).
Platform admin is `principals.is_admin`. A rule is `Rule(min=…, rr=…, admin=…, shared=…)`:
- `min`: the lowest building role that may (None: no building role may);
- `rr`: a risk reviewer of the owning team may;
- `admin`: a platform admin may (anywhere);
- `shared`: a team holding an active share of the object may (read-only rows; P21 builds shares);
- `anyone`: any identified principal (agent sessions, /authz/check).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .identity import Principal
from .scope import Scope

RANK = {"viewer": 0, "member": 1, "maintainer": 2, "owner": 3}


@dataclass(frozen=True)
class Rule:
    min: str | None = None
    rr: bool = False
    admin: bool = False
    shared: bool = False
    anyone: bool = False
    self_ok: bool = False  # principal.manage: the principal itself
    note: str = ""


R = Rule
ACTIONS: dict[str, Rule] = {
    # data (C §1.3 + plan §6.1)
    "data.read_meta": R("viewer", rr=True, admin=True, shared=True),
    "data.preview": R("viewer", rr=True, admin=True, shared=True, note="masked (P20)"),
    "data.read_unmasked": R("maintainer", rr=True, admin=True, note="reason required, read-audited"),
    "data.upload": R("member"),
    "data.version": R("member", note="upload-as-version, delta, edit, Data Doctor fix, Feature Lab, relabel, monitoring window"),
    "data.match_confirm": R("member"),
    "data.set_default": R("maintainer"),
    "data.card": R("maintainer"),
    "data.contract": R("maintainer"),
    "data.tag_pii": R("maintainer"),
    "data.archive": R("maintainer", admin=True),
    "data.delete": R("member", admin=True, note="request; decide is data.delete_decide"),
    "data.delete_decide": R("maintainer", admin=True, note="≠ requester; refused under legal hold"),
    "data.restore": R("maintainer", admin=True, note="restore splits from run pins"),
    "data.verify": R("viewer", rr=True, admin=True),
    "data.certify": R("maintainer", note="request a certification (P21)"),
    "data.certify_decide": R("owner", rr=True, note="route gate: the handler then needs data.certify_decide_certified "
                                                     "or data.certify_decide_model for the request's level; ≠ requester"),
    "data.certify_decide_certified": R("owner", note="decide a 'certified' request; ≠ requester"),
    "data.certify_decide_model": R(None, rr=True, note="decide an 'approved_for_model' request; ≠ requester"),
    "data.share": R("maintainer", note="request or revoke a share of the team's dataset / project (P21)"),
    "data.share_decide": R("owner", note="approve an outgoing share; ≠ requester; pii='unmasked' needs grant_unmasked"),
    "data.export": R("member", rr=True, admin=True, shared=True, note="masked"),
    "data.export_unmasked": R("maintainer", admin=True, note="reason; read-audited"),
    "data.purge": R("maintainer", note="request; decide = owner of every team involved"),
    "data.purge_decide": R("owner", note="P24: an owner approves a purge for their team; ≠ requester; one person, one team"),
    "project.create": R("member"),
    "project.manage": R("maintainer", admin=True),
    "annotate": R("member", rr=True, note="tags and notes on lineage nodes"),
    # runs
    "run.read": R("viewer", rr=True, admin=True, shared=True, note="row-level run outputs: predictions, scores, SHAP rows"),
    "run.train": R("member", note="any kind of run, its analyses and twins; also join materialisation"),
    "run.manage": R("member", admin=True, note="cancel, delete"),
    "run.use": R("member", rr=True, note="score new rows with a run's model"),
    # models
    "model.read": R("viewer", rr=True, admin=True),
    "model.register": R("member"),
    "model.card": R("member"),
    "model.use": R("member", rr=True, note="predict, score, batches, benchmarks, monitoring inputs"),
    "model.review": R(None, rr=True, note="maintainer too with team policy maintainer_can_review; ≠ registrant, ≠ run creator"),
    "model.promote": R("member", note="Staging"),
    "model.promote_production": R("maintainer", note="≠ registrant unless ENSEMBLE_ALLOW_SELF_PROMOTE; leak gate kept"),
    "model.retire": R("member", rr=True, admin=True, note="request (member) / decide (maintainer, risk reviewer, admin)"),
    "model.retire_decide": R("maintainer", rr=True, admin=True),
    # champions
    "champion.request": R("member"),
    "champion.decide": R("owner", rr=True, note="≠ requester"),
    # retention, legal hold, teams, admin
    "retention.plan": R("maintainer", admin=True),
    "retention.run": R("owner", admin=True, note="global run: admin"),
    "legal_hold.place": R(None, admin=True),
    "legal_hold.release": R(None, admin=True, note="≠ placer"),
    "team.create": R(None, admin=True, note="anyone when tm_default's policy has self_service_teams"),
    "team.manage": R("maintainer", admin=True, note="members below owner"),
    "team.settings": R("owner", admin=True, note="policy, owners and maintainers"),
    "principal.manage": R(None, admin=True, self_ok=True),
    "audit.read": R("viewer", rr=True, admin=True),
    "admin.reset": R(None, admin=True),
    "admin.lake": R(None, admin=True),
    "admin.config": R(None, admin=True, note="LLM settings, verify-all"),
    "agent.use": R(anyone=True, note="the agent's own tool calls are authorised as its user"),
    "authz.check": R(anyone=True),
}


def role_of(con: Any, principal_id: str, team_id: str) -> str | None:
    r = con.execute("SELECT role FROM team_members WHERE team_id = ? AND principal_id = ?", (team_id, principal_id)).fetchone()
    return r[0] if r else None


def shared_to(con: Any, principal_id: str, scope: Scope, *, unmasked: bool = False) -> dict[str, Any] | None:
    """P21: the active share that lets one of the principal's teams see this object (a dataset version within the share's
    scope, a project, or a run of a shared project), or None. `unmasked`: only a share with pii='unmasked' counts."""
    from ..catalog import shares

    return shares.grant(con, principal_id, scope, unmasked=unmasked)


def _policy(con: Any, team_id: str) -> dict[str, Any]:
    r = con.execute("SELECT policy FROM teams WHERE id = ?", (team_id,)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


def needed(rule: Rule) -> str:
    if rule.min:
        return rule.min
    if rule.rr:
        return "risk_reviewer"
    return "admin" if rule.admin else "principal"


def decide(con: Any, p: Principal | None, action: str, scope: Scope) -> dict[str, Any]:
    """{allow, action, team, needed, role, reason}. Pure: no audit, no mode. `scope.team_id` None = a platform action."""
    rule = ACTIONS[action]
    out: dict[str, Any] = {"action": action, "team": scope.team_id, "needed": needed(rule), "role": None,
                           "object_kind": scope.object_kind, "object_id": scope.object_id}
    if p is None:
        return {**out, "allow": False, "reason": "no credentials (send X-Ensemble-Actor; sign-in comes with SSO)"}
    out["principal"] = p.display_name
    if rule.anyone:
        return {**out, "allow": True, "reason": "any identified principal"}
    if rule.self_ok and scope.object_kind == "principal" and scope.object_id in (p.id, p.display_name, p.identity_key):
        return {**out, "allow": True, "reason": "the principal itself"}
    if rule.self_ok and scope.object_kind == "principal":
        from ..catalog.governance import identity_key
        if identity_key(scope.object_id) == p.identity_key:
            return {**out, "allow": True, "reason": "the principal itself"}
    if p.is_admin and rule.admin:
        return {**out, "allow": True, "reason": "platform admin"}
    if action == "team.create":
        ok = bool(_policy(con, "tm_default").get("self_service_teams"))
        return {**out, "allow": ok, "reason": "self-service teams are on" if ok else "creating a team needs a platform admin "
                "(or tm_default policy self_service_teams)"}
    if scope.team_id is None:
        return {**out, "allow": False, "reason": f"{action} is a platform action: it needs {out['needed']}"}
    role = role_of(con, p.id, scope.team_id)
    out["role"] = role
    if role == "risk_reviewer" and rule.rr:
        return {**out, "allow": True, "reason": "risk reviewer of the team"}
    if action == "model.review" and role in RANK and RANK[role] >= RANK["maintainer"] and _policy(con, scope.team_id).get("maintainer_can_review"):
        return {**out, "allow": True, "reason": "maintainer, and the team's policy lets maintainers review"}
    if role in RANK and rule.min and RANK[role] >= RANK[rule.min]:
        return {**out, "allow": True, "reason": f"{role} of the team"}
    if rule.shared:
        g = shared_to(con, p.id, scope)
        if g:
            return {**out, "allow": True, "share": g["id"], "reason": f"shared to your team {g['grantee_team']} ({g['id']}, scope "
                                                                     f"{g['scope']}, pii {g['pii']})"}
    if action in ("data.read_unmasked", "data.export_unmasked"):  # P21: only an explicit pii='unmasked' share unmasks
        g = shared_to(con, p.id, scope, unmasked=True)
        if g:
            return {**out, "allow": True, "share": g["id"], "reason": f"an unmasked share to your team {g['grantee_team']} "
                                                                     f"({g['id']}): reason required, read-audited"}
    have = f"you are {role}" if role else "you are not a member"
    return {**out, "allow": False, "reason": f"{action} needs {out['needed']} in {scope.team_id}; {have}"}


# -- four eyes (C §1.5): one check for every decide path ---------------------------------------------------------------

FOUR_EYES: dict[str, dict[str, Any]] = {
    "data.delete_decide": {"not": ["requested_by"]},
    "data.certify_decide": {"not": ["requested_by"]},
    "data.share_decide": {"not": ["requested_by"]},
    "model.review": {"not": ["registered_by", "change_by", "run_created_by"]},
    "model.retire_decide": {"not": ["requested_by"]},
    "champion.decide": {"not": ["requested_by", "change_by"]},
    "legal_hold.release": {"not": ["placed_by"]},
    "data.purge_decide": {"not": ["requested_by"]},
    "admin.reset": {"not": ["requested_by"]},
}


ROLE_WORDS = {"requested_by": "requester", "registered_by": "registrant", "change_by": "author of the change experiment",
              "run_created_by": "run's creator", "placed_by": "person who placed it"}


def same_person_p(p: Principal, other: str | None) -> bool:
    """As strict as governance.same_person: the principal id or the identity key matching is the same person."""
    from ..catalog.governance import identity_key, same_person

    if not other:
        return False
    return other == p.id or identity_key(other) == p.identity_key or same_person(p.display_name, other)


def decide_check(p: Principal | str | None, action: str, req: dict[str, Any]) -> str | None:
    """None when `p` (a principal, or a typed name) may decide `req`, else the refusal text ("a second person must
    decide"). Names fold through identity_key (punctuation, spacing, accents, confusables, I/l/1)."""
    from ..catalog.governance import same_person

    if p is None or (isinstance(p, str) and not p.strip()):
        return None
    for f in FOUR_EYES[action]["not"]:
        other = req.get(f)
        hit = same_person_p(p, other) if isinstance(p, Principal) else same_person(p, other)
        if hit:
            return f"Four eyes: {other} is the {ROLE_WORDS.get(f, f)}, so a second person must decide."
    return None


def four_eyes(action: str, req: dict[str, Any], by: str | None = None) -> str | None:
    """Every decide path calls this (C §1.5): the name recorded as the decider (`by`) AND the request's principal (when
    one was resolved) are each checked, so a typed `by` cannot launder a requester's own header, nor the reverse."""
    from .context import current

    ctx = current()
    p = ctx.principal if ctx is not None and ctx.resolved else None
    for who in (by, p):
        msg = decide_check(who, action, req)
        if msg:
            return msg
    return None
