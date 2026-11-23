"""Edit cases: a change asked of an already-built workflow (the live-tested set in runs/crazy-live).

Each case says what the edit must do and what it must not break:
- `request`: the change, as a user would type it.
- `checks`: python checks on the edited workflow (name, fn(d) -> True or a failure sentence).
- `drop_hidden`: indices of the base case's hidden tests the change makes wrong; every other base hidden test must still pass
  ("kept"), except one that already failed before the edit.
- `new_hidden`: hidden tests for the new behaviour, mostly derived from a base test with `derive()`.
- `add_mocks`: mocks added to every kept test (for a step the edit adds, e.g. a new approval).
- `relax_email_counts`: drop "$all:SendEmail request count" assertions from kept and new tests (the edit adds emails).
- `may_change`: regexes of original node names the edit is allowed to change or remove; any other original node that changes
  is drift.
- `extra_variables`: variables the edit needs that the project doesn't have yet.
"""
import copy
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
EVAL = HERE / "crazy" / "eval"


def base(cid: str) -> dict:
    return json.loads((EVAL / f"{cid}.json").read_text())


def derive(cid: str, idx: int, name: str, *, input=None, body=None, mocks=None, expect=None, drop=()) -> dict:
    """A copy of base hidden test `idx` with a new name; `body` updates the webhook body, `mocks` overrides mocks,
    `drop` removes assertions whose node+path+value mention any of the strings, `expect` appends assertions."""
    t = copy.deepcopy(base(cid)["hidden_tests"][idx])
    t["name"] = name
    if input is not None:
        t["input"] = input
    if body:
        t["input"]["items"][0]["body"].update(body)
    for k, v in (mocks or {}).items():
        if v is None:
            t["mocks"].pop(k, None)
        else:
            t["mocks"][k] = v
    t["expect"] = [a for a in t["expect"] if not any(s in json.dumps(a) for s in drop)]
    t["expect"] += expect or []
    return t


def ok(port="approved", form=None, decision=None):
    return {"port": port, "approval": {"decision": decision or port, "comment": "", "form_data": form or {}, "decided_by": "test"}}


def req(node, path, op, value):
    return {"node": node, "subject": "request", "item": "any", "path": path, "op": op, "value": value}


def count(node, n):
    return {"node": node, "subject": "request", "op": "count", "value": n}


def not_run(node):
    return {"node": node, "subject": "node", "op": "status", "value": "not_run"}


# ── python checks ──────────────────────────────────────────────────────────────────────────

def _node(d, name):
    return next((n for n in d["nodes"] if n["name"] == name), None)


def _reach(d, start):
    out, todo = set(), [start]
    while todo:
        n = todo.pop()
        for c in d["connections"]:
            if c["source_node"] == n and c["target_node"] not in out:
                out.add(c["target_node"])
                todo.append(c["target_node"])
    return out


def _mentions(n, text):
    return text in json.dumps(n.get("parameters") or {})


def summary_reaches_rm(d):
    start = next((n["name"] for n in d["nodes"] if n["type"] == "Postgres" and "COUNT(" in json.dumps(n["parameters"]).upper()), None)
    if not start:
        return "no summary count step left"
    hits = [n for n in d["nodes"] if n["name"] in _reach(d, start) and n["type"] == "SendEmail" and _mentions(n, "RM_EMAIL")]
    return True if hits else "no email after the daily count goes to $vars.RM_EMAIL"


def committee_parallel(d):
    com = [n["name"] for n in d["nodes"] if n["type"] == "Approval" and _mentions(n, "CREDIT_COMMITTEE_EMAIL")]
    if len(com) == 1 and str((_node(d, com[0])["parameters"]).get("per_item")).lower() == "true":
        return True
    if len(com) < 2:
        return f"expected two committee tasks, found {com}"
    for a in com:
        if any(b in _reach(d, a) for b in com if b != a):
            return f"committee tasks are still one after the other ({a} leads to another committee task)"
    return True


def no_risk_review(d):
    bad = [n["name"] for n in d["nodes"] if _mentions(n, "RISK_MANAGER_EMAIL")]
    return True if not bad else f"risk manager still referenced by {bad}"


def mlro_on_timeout_escalates(d):
    mlro = [n for n in d["nodes"] if n["type"] == "Approval" and _mentions(n, "MLRO_EMAIL") and "4h" in json.dumps(n["parameters"])]
    if not mlro:
        return "no 4h MLRO review"
    p = mlro[0]["parameters"]
    if (p.get("on_timeout") or "timed_out") != "timed_out":
        return f'MLRO review on_timeout is {p.get("on_timeout")!r}; it must route timeouts to the escalation'
    targets = [c["target_node"] for c in d["connections"] if c["source_node"] == mlro[0]["name"] and c["source_output"] == "timed_out"]
    reach = set(targets)
    for t in targets:
        reach |= _reach(d, t)
    head = [n for n in d["nodes"] if n["name"] in reach and n["type"] == "Approval" and _mentions(n, "HEAD_OF_COMPLIANCE_EMAIL")]
    return True if head else "MLRO timeout does not reach a head-of-compliance task"


# ── fixtures ───────────────────────────────────────────────────────────────────────────────

W, M, K, H, R = "wire_release", "mortgage_offer", "kyc_document_chase", "hardship_restructure", "access_recertification"
REVIEW = {"approval": {"decision": "approved", "comment": "", "form_data": {"revoke": "{{ 'u3' if 'm1@' in json_stringify($json) else '' }}"},
                       "decided_by": "test"}}
FOUR_USERS = base(R)["hidden_tests"][0]["mocks"]["$pgr:user_access"]
FIVE_USERS = copy.deepcopy(FOUR_USERS)
FIVE_USERS["items"][0]["rows"].append({"user_id": "u5", "user_email": "u5@bank.test", "system": "core", "manager_email": "m2@bank.test"})
FIVE_USERS["items"][0]["rowCount"] = 5

CASES = [
    # ── small tweaks ───────────────────────────────────────────────────────────────────────
    {"id": "T1_mortgage_3_days", "base": M, "tier": "tweak",
     "request": "Give the underwriter 3 days for the first decision instead of 2.",
     "drop_hidden": [1], "may_change": [r"^Underwriter decision$"],
     "new_hidden": [derive(M, 1, "Underwriter gets 3 days", drop=['"2d"'],
                           expect=[req("$nth:Approval:UNDERWRITER_EMAIL:1", "params.due_in", "one_of", ["3d", "72h"])])]},
    {"id": "T2_wire_25k", "base": W, "tier": "tweak",
     "request": "Release wires under 25,000 straight away (the limit was 10,000).",
     "may_change": [r"^Under 10000\?$"],
     "new_hidden": [derive(W, 0, "24,999.99 is released at once", body={"amount": 24999.99}),
                    derive(W, 1, "Exactly 25,000 needs the callback", body={"amount": 25000})]},
    {"id": "T3_kyc_summary_to_rm", "base": K, "tier": "tweak",
     "request": "Send the daily summary to the RM ($vars.RM_EMAIL) as well as the KYC team.",
     "relax_email_counts": True, "may_change": [r"^Email KYC team$"],
     "checks": [("summary reaches the RM", summary_reaches_rm)]},
    {"id": "T4_recert_10_days", "base": R, "tier": "tweak",
     "request": "Give managers 10 days for the first review instead of 7.",
     "drop_hidden": [0], "may_change": [r"^Manager review$"], "relax_email_counts": True,
     "new_hidden": [derive(R, 0, "Managers get 10 days", drop=['"7d"'],
                           expect=[req("$nth:Approval::1", "params.due_in", "one_of", ["10d", "240h"])])]},
    {"id": "T5_hardship_risk_timeout_approves", "base": H, "tier": "tweak",
     "request": "If the risk manager doesn't answer in time, approve the plan instead of declining it.",
     "drop_hidden": [2], "may_change": [r"^Risk review$"],
     "new_hidden": [derive(H, 2, "Risk manager silent: the plan goes ahead", drop=["on_timeout"],
                           mocks={"$has:Approval:RISK_MANAGER_EMAIL?": ok("timed_out")},
                           expect=[count("$api:accounts.restructureLoan", 1)])]},
    # ── add a step ─────────────────────────────────────────────────────────────────────────
    {"id": "A1_mortgage_email_on_booking", "base": M, "tier": "add",
     "request": "When a mortgage is booked, also email the underwriting team ($vars.UNDERWRITER_EMAIL) with the application id "
                "and the booked amount.",
     "relax_email_counts": True, "may_change": [],
     "new_hidden": [derive(M, 1, "Booked: underwriters told", expect=[
         req("$all:SendEmail", "params.toEmail", "eq", "underwriters@bank.test"),
         req("$all:SendEmail", "params.body", "contains", "M1"), req("$all:SendEmail", "params.body", "contains", "400")])]},
    {"id": "A2_wire_fraud_tells_customer", "base": W, "tier": "add",
     "request": "When the customer says the wire is not theirs, also email the customer that the wire is held and their account "
                "is frozen.",
     "relax_email_counts": True, "may_change": [],
     "new_hidden": [derive(W, 5, "Fraud: customer emailed too", expect=[req("$all:SendEmail", "params.toEmail", "eq", "kai@mail.test")])]},
    {"id": "A3_kyc_upload_tells_rm", "base": K, "tier": "add",
     "request": "When a customer's documents arrive, also email the RM ($vars.RM_EMAIL) that the customer is complete, with the "
                "customer id.",
     "relax_email_counts": True, "may_change": [],
     "new_hidden": [derive(K, 2, "Upload: RM told", expect=[req("$all:SendEmail", "params.toEmail", "eq", "rm@bank.test"),
                                                           req("$all:SendEmail", "params.body", "contains", "cu7")])]},
    {"id": "A4_hardship_accept_tells_officer", "base": H, "tier": "add",
     "request": "When the restructuring is applied, also email the collections officer ($vars.COLLECTIONS_OFFICER_EMAIL) with the "
                "loan id.",
     "relax_email_counts": True, "may_change": [],
     "new_hidden": [derive(H, 1, "Applied: officer told", expect=[req("$all:SendEmail", "params.toEmail", "eq", "collections@bank.test"),
                                                                 req("$all:SendEmail", "params.body", "contains", "L3")])]},
    {"id": "A5_recert_tell_revoked_users", "base": R, "tier": "add",
     "request": "Email each user whose access is revoked, saying which system they lost access to.",
     "relax_email_counts": True, "may_change": [r"^Group by manager$", r"^Plan from (answer|timeout)$"],
     "new_hidden": [derive(R, 0, "Revoked user told", expect=[
         req("$all:SendEmail", "params.toEmail", "eq", "u3@bank.test"), req("$all:SendEmail", "params.body", "contains", "crm")])]},
    # ── restructure ────────────────────────────────────────────────────────────────────────
    {"id": "R1_wire_mlro_escalation", "base": W, "tier": "restructure",
     "request": "If the MLRO doesn't decide within 4 hours, escalate to the head of compliance ($vars.HEAD_OF_COMPLIANCE_EMAIL), "
                "who gets 2 hours and the same choice (true match or false positive). If they don't decide either, block the wire.",
     "extra_variables": {"HEAD_OF_COMPLIANCE_EMAIL": {"value": "compliance-head@bank.test"}},
     "drop_hidden": [6], "may_change": [r"^MLRO review$"],
     "add_mocks": {"$has:Approval:HEAD_OF_COMPLIANCE_EMAIL?": ok(form={"disposition": "false_positive"})},
     "checks": [("MLRO timeout escalates", mlro_on_timeout_escalates)],
     "new_hidden": [
         derive(W, 6, "Sanctions hit, true match (unchanged)", drop=["on_timeout"],
                mocks={"$has:Approval:HEAD_OF_COMPLIANCE_EMAIL?": ok(form={"disposition": "false_positive"})}),
         derive(W, 6, "MLRO silent, head of compliance clears it: released", drop=["sar-77", "blocked", "wires.block", "on_timeout", "release"],
                mocks={"$nth:Approval:MLRO_EMAIL:1?": ok("timed_out"),
                       "$has:Approval:HEAD_OF_COMPLIANCE_EMAIL?": ok(form={"disposition": "false_positive"})},
                expect=[count("$has:Approval:HEAD_OF_COMPLIANCE_EMAIL", 1),
                        req("$has:Approval:HEAD_OF_COMPLIANCE_EMAIL", "params.due_in", "one_of", ["2h", "120m"]),
                        count("$api:wires.release", 1), not_run("$api:wires.block")]),
         derive(W, 6, "MLRO and head of compliance both silent: blocked", drop=["sar-77", "on_timeout", "MLRO_EMAIL"],
                mocks={"$nth:Approval:MLRO_EMAIL:1?": ok("timed_out"), "$has:Approval:HEAD_OF_COMPLIANCE_EMAIL?": ok("timed_out")},
                expect=[count("$has:Approval:HEAD_OF_COMPLIANCE_EMAIL", 1)])]},
    {"id": "R2_mortgage_committee_parallel", "base": M, "tier": "restructure",
     "request": "Ask both credit committee members at the same time instead of one after the other. Both must approve; if either "
                "rejects, decline.",
     "may_change": [r"^Committee member", r"^Over 500000\?$"], "drop_hidden": [6],
     "checks": [("committee asked in parallel", committee_parallel)],
     "new_hidden": [derive(M, 6, "Second committee member rejects: declined, nothing sent for signing",
                           mocks={"$nth:Approval:CREDIT_COMMITTEE_EMAIL:1?": {"approval": {
                               "decision": "{{ 'rejected' if $itemIndex == 1 else 'approved' }}", "comment": "", "form_data": {}, "decided_by": "test"}}})]},
    {"id": "R3_kyc_team_confirms_restriction", "base": K, "tier": "restructure",
     "request": "On day 10, before restricting, ask the KYC team ($vars.KYC_TEAM_EMAIL, 1 day to answer) to confirm. If they approve, "
                "restrict as today. If they reject or don't answer, don't restrict: email the RM to follow up instead.",
     "relax_email_counts": True, "may_change": [r"^Day 10 and 2 reminders\?$", r"^Restrict customer$"],
     "add_mocks": {"$has:Approval:KYC_TEAM_EMAIL?": ok()},
     "new_hidden": [derive(K, 0, "KYC team says no: not restricted, RM told",
                           drop=["restrictCustomer", '"restricted"', "cu5@mail.test", '"value": 4'],
                           mocks={"$has:Approval:KYC_TEAM_EMAIL?": ok("rejected")},
                           expect=[count("$has:Approval:KYC_TEAM_EMAIL", 1), not_run("$api:accounts.restrictCustomer"),
                                   req("$all:SendEmail", "params.toEmail", "eq", "rm@bank.test")]),
                    derive(K, 0, "KYC team approves: restricted", expect=[count("$has:Approval:KYC_TEAM_EMAIL", 1)],
                           mocks={"$has:Approval:KYC_TEAM_EMAIL?": ok()})]},
    {"id": "R4_hardship_no_risk_review", "base": H, "tier": "restructure",
     "request": "Remove the risk review completely: every plan the officer proposes goes straight to the customer.",
     "drop_hidden": [2, 3], "may_change": [r"^Risk review$", r"^Needs risk review\?$"],
     "checks": [("risk review gone", no_risk_review)],
     "new_hidden": [derive(H, 2, "4-month holiday goes straight to the customer", drop=["RISK_MANAGER"],
                           expect=[count("$api:accounts.restructureLoan", 1), count("$all:Approval", 2)]),
                    derive(H, 3, "Over 50,000 goes straight to the customer", drop=["RISK_MANAGER", "restructureLoan", "COLLECTIONS_OFFICER_EMAIL:2", "declined"],
                           expect=[count("$api:accounts.restructureLoan", 1)])]},
    {"id": "R5_recert_auto_confirm_single", "base": R, "tier": "restructure",
     "request": "Managers with only one person in their team are confirmed automatically: no task for them, and record "
                "'confirmed' in the audit log just like a confirmed review.",
     "drop_hidden": [0, 1, 2], "relax_email_counts": True, "may_change": [r"^Group by manager$"],
     "new_hidden": [
         derive(R, 0, "m2 has one person: auto-confirmed, m1 still asked", drop=["Approval::1"],
                expect=[count("$nth:Approval::1", 1), req("$nth:Approval::1", "params.notify_email", "eq", "m1@bank.test")]),
         derive(R, 2, "m2 has one person: never asked, so never times out", drop=["Approval::", "iam.revoke", "revoked_all_timeout", "m2@bank.test"],
                expect=[not_run("$api:iam.revoke?"), req("$pgw:audit_log", "text", "contains", "confirmed")]),
         derive(R, 1, "Both managers have two or more people: both asked", mocks={"$pgr:user_access": FIVE_USERS},
                expect=[count("$nth:Approval::1", 2)])]},
]
for c in CASES:
    c.setdefault("checks", [])
    c.setdefault("drop_hidden", [])
    c.setdefault("new_hidden", [])
    c.setdefault("add_mocks", {})
    c.setdefault("extra_variables", {})
    c["may_change"] = [re.compile(p) for p in c["may_change"]]
