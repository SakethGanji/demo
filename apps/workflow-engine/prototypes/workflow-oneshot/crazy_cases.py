"""Writes the crazy suite: crazy/apis/*.yaml and crazy/eval/*.json. Long-running, many people: task -> wait -> reminder ->
re-ask -> escalate -> next person, day-long waits, repeated checks, per-row approvals, database state machines.

Extra bindings (wf_build): $nth:<Type>:<text>:<k> the k-th node of that type (by distance from the trigger) whose parameters
mention <text>; $pg/$pgw/$pgr:<table> Postgres nodes naming / writing / only reading a table; $has:<Type>:<text>.
"""
import json
from pathlib import Path

import complex_cases as cc
from complex_cases import obj, http, st, req, calls, hook

cc.OUT = Path(__file__).parent / "crazy"
(cc.OUT / "apis").mkdir(parents=True, exist_ok=True)
(cc.OUT / "eval").mkdir(parents=True, exist_ok=True)
api, case = cc.api, cc.case

CORE = {"writable": False, "description": "read replica of core banking", "tables": {
    "customers": [("customer_id", "text"), ("full_name", "text"), ("email", "text"), ("phone", "text"), ("segment", "text")],
    "loans": [("loan_id", "text"), ("customer_id", "text"), ("outstanding", "numeric"), ("days_past_due", "integer"), ("status", "text")]}}
IAM = {"writable": False, "description": "identity and access management", "tables": {
    "user_access": [("user_id", "text"), ("user_email", "text"), ("system", "text"), ("manager_email", "text")]}}
OPS = {"writable": True, "description": "our operations database", "tables": {
    "audit_log": [("id", "serial"), ("entity", "text"), ("entity_id", "text"), ("action", "text"), ("detail", "text"),
                  ("created_at", "timestamptz default now()")],
    "mortgage_applications": [("application_id", "text primary key"), ("applicant_email", "text"), ("amount", "numeric"), ("status", "text")],
    "kyc_requests": [("id", "serial"), ("customer_id", "text"), ("email", "text"), ("requested_on", "date"), ("reminders_sent", "integer"),
                     ("status", "text: open | restricted | complete")],
    "restructure_plans": [("id", "serial"), ("loan_id", "text"), ("plan", "text"), ("months", "integer"), ("status", "text")]}}
DBS = {"core_banking": CORE, "iam_db": IAM, "ops_db": OPS}
VARS = {k: {"value": v} for k, v in {
    "UNDERWRITER_EMAIL": "underwriters@bank.test", "HEAD_OF_LENDING_EMAIL": "head-lending@bank.test",
    "CREDIT_COMMITTEE_EMAIL": "committee@bank.test", "PAYMENTS_OPS_EMAIL": "payops@bank.test", "PAYMENTS_LEAD_EMAIL": "paylead@bank.test",
    "FRAUD_TEAM_EMAIL": "fraud@bank.test", "MLRO_EMAIL": "mlro@bank.test", "TREASURY_EMAIL": "treasury@bank.test",
    "KYC_TEAM_EMAIL": "kyc@bank.test", "RM_EMAIL": "rm@bank.test", "CISO_EMAIL": "ciso@bank.test",
    "COLLECTIONS_OFFICER_EMAIL": "collections@bank.test", "RISK_MANAGER_EMAIL": "risk@bank.test"}.items()}
VARS["BANK_API_KEY"] = {"value": "sk-bank-31337", "secret": True}


def rows(*rs):
    return {"items": [{"rows": list(rs), "rowCount": len(rs)}]}


OK_TX = {"items": [{"success": True}]}
MAIL = {"items": [{"success": True}]}


def decide(decision, comment="", form=None):
    return {"port": decision, "approval": {"decision": decision, "comment": comment, "form_data": form or {}, "decided_by": "test"}}


def to(addr):
    """At least one email to this address."""
    return req("$all:SendEmail", "params.toEmail", "eq", addr)


def crazy(id_, request, apis, expect, tests):
    case(id_, request, apis, expect, tests)
    c = json.loads((cc.OUT / "eval" / f"{id_}.json").read_text())
    c.update(variables=VARS, subworkflows=[], databases=DBS)
    (cc.OUT / "eval" / f"{id_}.json").write_text(json.dumps(c, indent=1))


api("bureau", [("creditScore", "post", "/credit-checks", "Credit score of an applicant", obj(applicant_id="string"),
                obj(applicant_id="string", score="integer"))])
api("esign", [
    ("createEnvelope", "post", "/envelopes", "Send a document for e-signature", obj(signer_email="string", document="string", reference="string"),
     obj(envelope_id="string")),
    ("getEnvelope", "get", "/envelopes/{envelope_id}", "Signature status of an envelope", None,
     obj(envelope_id="string", status={"type": "string", "enum": ["sent", "signed", "declined"]})),
    ("cancelEnvelope", "post", "/envelopes/{envelope_id}/cancel", "Withdraw an envelope", None, obj(envelope_id="string", status="string")),
])
api("mortgages", [("book", "post", "/mortgages", "Book a mortgage", obj(application_id="string", amount="number"), obj(mortgage_id="string"))])
api("wires", [
    ("release", "post", "/wires/{wire_id}/release", "Release a wire for sending", None, obj(wire_id="string", status="string")),
    ("hold", "post", "/wires/{wire_id}/hold", "Hold a wire", obj(reason="string"), obj(wire_id="string", status="string")),
    ("block", "post", "/wires/{wire_id}/block", "Block a wire permanently (sanctions)", None, obj(wire_id="string", status="string")),
])
api("accounts", [
    ("freeze", "post", "/accounts/{account_id}/freeze", "Freeze an account", None, obj(account_id="string", status="string")),
    ("restrictCustomer", "post", "/customers/{customer_id}/restrict", "Restrict all of a customer's accounts", None, obj(customer_id="string")),
    ("liftRestriction", "post", "/customers/{customer_id}/lift-restriction", "Lift a customer's restriction", None, obj(customer_id="string")),
    ("restructureLoan", "post", "/loans/{loan_id}/restructure", "Apply a restructuring plan to a loan", obj(plan="string", months="integer"),
     obj(loan_id="string", status="string")),
])
api("screening", [("screen", "post", "/screen", "Screen a name against sanctions lists", obj(name="string", country="string"),
                   obj(hit="boolean", list="string"))])
api("dms", [("documents", "get", "/customers/{customer_id}/documents", "Whether a customer has uploaded the requested KYC documents", None,
             obj(customer_id="string", uploaded="boolean"))])
api("iam", [("revoke", "post", "/users/{user_id}/revoke-access", "Remove a user's access", obj(reason="string"), obj(user_id="string"))])

# ── X1: mortgage: bureau -> underwriter (2d) -> reminder + 2nd ask (1d) -> head of lending (1d) -> committee x2 if big
#        -> offer -> e-signature checked daily up to 5 times with reminders -> book + atomic DB + welcome, or expire ───────────
UW1, UW2 = "$nth:Approval:UNDERWRITER_EMAIL:1", "$nth:Approval:UNDERWRITER_EMAIL:2?"
HEAD = "$has:Approval:HEAD_OF_LENDING_EMAIL?"
CC1, CC2 = "$nth:Approval:CREDIT_COMMITTEE_EMAIL:1?", "$nth:Approval:CREDIT_COMMITTEE_EMAIL:2?"
APPS = "$pgw:mortgage_applications"
ANN = "ann@mail.test"


def mort(score=700, uw1=None, uw2=None, head=None, cc1=None, cc2=None, sign="signed"):
    yes = decide("approved", form={"approved_amount": 400000, "conditions": "valuation required"})
    return {"$api:bureau.creditScore": http({"applicant_id": "M1", "score": score}),
            UW1: uw1 or yes, UW2: uw2 or yes, HEAD: head or yes, CC1: cc1 or decide("approved"), CC2: cc2 or decide("approved"),
            "$api:esign.createEnvelope": http({"envelope_id": "E1"}), "$api:esign.getEnvelope?": http({"envelope_id": "E1", "status": sign}),
            "$api:esign.cancelEnvelope?": http({"envelope_id": "E1", "status": "cancelled"}),
            "$api:mortgages.book?": http({"mortgage_id": "MG1"}), APPS: OK_TX, "$all:SendEmail": MAIL}


m_in = lambda amt=400000: hook(application_id="M1", applicant_email=ANN, amount=amt, property_value=600000)  # noqa: E731
crazy("mortgage_offer",
      "Mortgage applications arrive by webhook with application_id, applicant_email, amount and property_value. Get the applicant's "
      "credit score from the bureau (the applicant id is the application id). Below 580: email the applicant that we can't offer a "
      "mortgage, set the application's status to 'declined' in ops_db mortgage_applications, and stop. Otherwise an underwriter decides: "
      "a task for UNDERWRITER_EMAIL, due in 2 days, showing the amount, property value, loan-to-value and score, where they enter "
      "approved_amount and conditions. If the underwriter hasn't decided in 2 days, email them a reminder and give them one more day (a "
      "second task, same form). If that runs out too, the head of lending (HEAD_OF_LENDING_EMAIL) decides with the same form, due in 1 "
      "day, and if nobody decides it is declined. If the approved amount is over 500,000, two credit committee members "
      "(CREDIT_COMMITTEE_EMAIL) must also approve, one after the other (four eyes), seeing the approved amount and conditions. Any "
      "rejection: email the applicant that we can't offer a mortgage and set status 'declined'. Once approved: email the applicant the "
      "offer with the approved amount and the conditions, and send the offer for e-signature. Then check the signature once a day, up to "
      "5 times. When it's signed: book the mortgage for the approved amount, and in one transaction set the application's status to "
      "'booked' and add an audit_log row (entity 'mortgage', the application id, action 'booked'); then email the applicant a welcome. "
      "After each of the first four checks that finds it unsigned, email the applicant a reminder to sign. If the fifth check still finds "
      "it unsigned, cancel the envelope, email the applicant that the offer has expired, and set status 'expired'.",
      ["bureau.yaml", "esign.yaml", "mortgages.yaml"],
      {"trigger": "Webhook", "must_have_types": ["Approval", "Wait", "Postgres", "SendEmail"],
       "must_call": ["bureau.creditScore", "esign.createEnvelope", "esign.getEnvelope", "esign.cancelEnvelope", "mortgages.book"],
       "max_nodes": 45, "min_types": {"Approval": 5}},
      [{"name": "Score 579 is declined straight away", "input": m_in(), "mocks": mort(score=579),
        "expect": [st("$all:Approval", "not_run"), req("$api:bureau.creditScore", "body.applicant_id", "eq", "M1"), to(ANN),
                   req(APPS, "text", "contains", "declined"), st("$api:esign.createEnvelope", "not_run")]},
       {"name": "Score 580, underwriter approves, signed at the first check", "input": m_in(), "mocks": mort(score=580),
        "expect": [calls("$all:Approval", 1), req(UW1, "params.due_in", "one_of", ["2d", "48h"]),
                   req(UW1, "params.notify_email", "eq", "underwriters@bank.test"),
                   req("$all:SendEmail", "params.body", "contains", "valuation required"), calls("$api:esign.getEnvelope", 1),
                   req("$api:mortgages.book", "body.amount", "eq", 400000), req(APPS, "text", "contains", "booked"),
                   req(APPS, "sql", "contains", "audit_log"), st("$api:esign.cancelEnvelope", "not_run"), calls("$all:SendEmail", 2)]},
       {"name": "Underwriter misses 2 days: reminder, second ask approves a lower amount", "input": m_in(),
        "mocks": mort(uw1=decide("timed_out"), uw2=decide("approved", form={"approved_amount": 350000, "conditions": "none"})),
        "expect": [calls("$all:Approval", 2), to("underwriters@bank.test"), req(UW2, "params.due_in", "one_of", ["1d", "24h"]),
                   req("$api:mortgages.book", "body.amount", "eq", 350000), st(HEAD, "not_run")]},
       {"name": "Underwriter twice silent, head of lending approves", "input": m_in(),
        "mocks": mort(uw1=decide("timed_out"), uw2=decide("timed_out"), head=decide("approved", form={"approved_amount": 300000, "conditions": "x"})),
        "expect": [calls(HEAD, 1), req(HEAD, "params.notify_email", "eq", "head-lending@bank.test"),
                   req("$api:mortgages.book", "body.amount", "eq", 300000)]},
       {"name": "Nobody ever decides: declined", "input": m_in(),
        "mocks": mort(uw1=decide("timed_out"), uw2=decide("timed_out"), head=decide("timed_out")),
        "expect": [st("$api:esign.createEnvelope", "not_run"), st("$api:mortgages.book", "not_run"), req(APPS, "text", "contains", "declined"),
                   to(ANN)]},
       {"name": "Over 500,000 needs both committee members", "input": m_in(700000),
        "mocks": mort(uw1=decide("approved", form={"approved_amount": 600000, "conditions": "none"})),
        "expect": [calls("$has:Approval:CREDIT_COMMITTEE_EMAIL", 2), req("$has:Approval:CREDIT_COMMITTEE_EMAIL", "params.four_eyes", "eq", True, item="all"),
                   req("$api:mortgages.book", "body.amount", "eq", 600000)]},
       {"name": "Second committee member rejects: declined, nothing sent for signing", "input": m_in(700000),
        "mocks": mort(uw1=decide("approved", form={"approved_amount": 600000, "conditions": "none"}), cc2=decide("rejected", "too risky")),
        "expect": [calls("$has:Approval:CREDIT_COMMITTEE_EMAIL", 2), st("$api:esign.createEnvelope", "not_run"),
                   req(APPS, "text", "contains", "declined")]},
       {"name": "Never signed: 5 daily checks, 4 reminders, then expired", "input": m_in(), "mocks": mort(sign="sent"),
        "expect": [calls("$api:esign.getEnvelope", 5), calls("$api:esign.cancelEnvelope", 1), st("$api:mortgages.book", "not_run"),
                   req(APPS, "text", "contains", "expired"), calls("$all:SendEmail", 6)]}])

# ── X2: high-value wire: callback task (2h) -> lead reminder + re-task (1h) -> outcome branches -> sanctions -> MLRO
#        disposition (4h, auto-reject) -> SAR task -> treasury four-eyes for 1M+ ; audit row at every step ────────────────
OPS1, OPS2 = "$nth:Approval:PAYMENTS_OPS_EMAIL:1", "$nth:Approval:PAYMENTS_OPS_EMAIL:2?"
MLRO1, MLRO2 = "$nth:Approval:MLRO_EMAIL:1?", "$nth:Approval:MLRO_EMAIL:2?"
TR = "$has:Approval:TREASURY_EMAIL?"
TR2 = "$nth:Approval:TREASURY_EMAIL:2?"
AUD = "$pgw:audit_log"
CUSTQ = "$pgr:customers?"


def wire(confirmed="yes", ops1=None, ops2=None, hit=False, mlro1=None, mlro2=None, tr2=None):
    yes = decide("approved", form={"customer_confirmed": confirmed})
    return {CUSTQ: rows({"customer_id": "CU9", "full_name": "Kai Moreno", "email": "kai@mail.test", "phone": "+15550199", "segment": "retail"}),
            OPS1: ops1 or yes, OPS2: ops2 or yes, MLRO1: mlro1 or decide("approved", form={"disposition": "false_positive"}),
            MLRO2: mlro2 or decide("approved", form={"sar_reference": "SAR-77"}), TR: decide("approved"), TR2: tr2 or decide("approved"),
            "$api:screening.screen?": http({"hit": hit, "list": "OFAC" if hit else ""}),
            "$api:wires.release?": http({"wire_id": "W5", "status": "released"}), "$api:wires.hold?": http({"wire_id": "W5", "status": "held"}),
            "$api:wires.block?": http({"wire_id": "W5", "status": "blocked"}), "$api:accounts.freeze?": http({"account_id": "A9", "status": "frozen"}),
            AUD: OK_TX, "$all:SendEmail?": MAIL}


w_in = lambda amt=50000: hook(wire_id="W5", account_id="A9", customer_id="CU9", amount=amt, beneficiary_name="Orion Trading",  # noqa: E731
                              beneficiary_country="AE")
crazy("wire_release",
      "Outgoing wires arrive by webhook from the payments hub with wire_id, account_id, customer_id, amount, beneficiary_name and "
      "beneficiary_country. Write an audit_log row in ops_db at each outcome below (entity 'wire', the wire id, and the action in "
      "brackets). Under 10,000: release it [released]. Otherwise payments ops must call the customer back: look up the customer's name and "
      "phone in core banking and give PAYMENTS_OPS_EMAIL a task, due in 2 hours, showing the customer's name, phone, the amount and the "
      "beneficiary, where they record customer_confirmed (yes, no or unreachable). If nobody has done it in 2 hours, email "
      "PAYMENTS_LEAD_EMAIL a reminder and give payments ops the task again, due in 1 hour. If that runs out too, or the customer was "
      "unreachable, hold the wire and email the customer that we couldn't reach them [held_no_callback]. If the customer says no, it's "
      "fraud: hold the wire, freeze the account and email FRAUD_TEAM_EMAIL [fraud]. If the customer confirms, screen the beneficiary "
      "for sanctions. A hit goes to the MLRO (MLRO_EMAIL), due in 4 hours and rejected automatically if nobody decides, who chooses a "
      "disposition: false_positive or true_match. A true match, or a rejection: block the wire [blocked], then give the MLRO a second "
      "task, due in 1 day, to enter the sar_reference, and record it as the detail of an audit row [sar_filed]. A false positive, or no "
      "hit: wires of 1,000,000 or more also need two treasury approvals one after the other (TREASURY_EMAIL, four eyes), then release "
      "[released]; a treasury rejection holds the wire [held_by_treasury]. Under 1,000,000: release [released].",
      ["wires.yaml", "accounts.yaml", "screening.yaml"],
      {"trigger": "Webhook", "must_have_types": ["Approval", "Postgres", "SendEmail"],
       "must_call": ["wires.release", "wires.hold", "wires.block", "accounts.freeze", "screening.screen"], "max_nodes": 45,
       "min_types": {"Approval": 6}},
      [{"name": "9,999.99 is released at once", "input": w_in(9999.99), "mocks": wire(),
        "expect": [st("$all:Approval", "not_run"), req("$api:wires.release", "url", "contains", "W5"), req(AUD, "text", "contains", "released"),
                   req(AUD, "text", "contains", "w5")]},
       {"name": "Customer confirms, no hit, under a million: released", "input": w_in(), "mocks": wire(),
        "expect": [calls("$all:Approval", 1), req(OPS1, "params.notify_email", "eq", "payops@bank.test"),
                   req(OPS1, "params.due_in", "one_of", ["2h", "120m"]), req("$api:screening.screen", "body.name", "eq", "Orion Trading"),
                   calls("$api:wires.release", 1), st("$api:wires.hold", "not_run"), req(AUD, "text", "contains", "released")]},
       {"name": "Ops silent 2h: lead reminded, second task confirms", "input": w_in(), "mocks": wire(ops1=decide("timed_out")),
        "expect": [to("paylead@bank.test"), calls("$all:Approval", 2), req(OPS2, "params.due_in", "one_of", ["1h", "60m"]),
                   calls("$api:wires.release", 1)]},
       {"name": "Ops silent twice: held, customer emailed", "input": w_in(), "mocks": wire(ops1=decide("timed_out"), ops2=decide("timed_out")),
        "expect": [calls("$api:wires.hold", 1), st("$api:wires.release", "not_run"), to("kai@mail.test"),
                   req(AUD, "text", "contains", "held_no_callback"), st("$api:screening.screen", "not_run")]},
       {"name": "Customer unreachable: held", "input": w_in(), "mocks": wire(confirmed="unreachable"),
        "expect": [calls("$api:wires.hold", 1), st("$api:screening.screen", "not_run"), req(AUD, "text", "contains", "held_no_callback")]},
       {"name": "Customer says no: fraud", "input": w_in(), "mocks": wire(confirmed="no"),
        "expect": [calls("$api:wires.hold", 1), req("$api:accounts.freeze", "url", "contains", "A9"), to("fraud@bank.test"),
                   req(AUD, "text", "contains", "fraud"), st("$api:wires.release", "not_run"), st("$api:screening.screen", "not_run")]},
       {"name": "Sanctions hit, true match: blocked and SAR reference recorded", "input": w_in(), "mocks":
        wire(hit=True, mlro1=decide("approved", form={"disposition": "true_match"})),
        "expect": [calls("$api:wires.block", 1), st("$api:wires.release", "not_run"), req(MLRO1, "params.due_in", "one_of", ["4h", "240m"]),
                   req(MLRO1, "params.on_timeout", "eq", "reject"), req(AUD, "text", "contains", "sar-77"), req(AUD, "text", "contains", "blocked")]},
       {"name": "Hit cleared as false positive on 2,000,000: treasury twice, released", "input": w_in(2000000), "mocks": wire(hit=True),
        "expect": [calls("$has:Approval:TREASURY_EMAIL", 2), req("$has:Approval:TREASURY_EMAIL", "params.four_eyes", "eq", True, item="all"),
                   calls("$api:wires.release", 1), st("$api:wires.block", "not_run")]},
       {"name": "Exactly 1,000,000, second treasury approver rejects: held", "input": w_in(1000000), "mocks": wire(tr2=decide("rejected", "no")),
        "expect": [calls("$has:Approval:TREASURY_EMAIL", 2), calls("$api:wires.hold", 1), st("$api:wires.release", "not_run"),
                   req(AUD, "text", "contains", "held_by_treasury")]}])

# ── X3: KYC document chase: daily state machine over a table; DB does the date maths; per-row API + branches + writes ───
KYCQ, KYCW = "$pgr:kyc_requests", "$pgw:kyc_requests?"


def k(cid, days, sent, status="open"):
    return {"id": int(cid[2:]), "customer_id": cid, "email": f"{cid}@mail.test", "requested_on": "2026-10-01", "reminders_sent": sent,
            "status": status, "days_open": days}


def kyc(rs, uploaded=(), counts=(1, 2, 1)):
    return {KYCQ: rows(*rs), KYCW: OK_TX, "$pgr:audit_log?": rows(dict(zip(("completed", "reminded", "restricted"), counts))),
            "$api:dms.documents": http({"customer_id": "x", "uploaded": f"{{{{ $json.customer_id in {list(uploaded)!r} }}}}"}),
            "$api:accounts.restrictCustomer?": http({"customer_id": "x"}), "$api:accounts.liftRestriction?": http({"customer_id": "x"}),
            "$all:SendEmail?": MAIL}


crazy("kyc_document_chase",
      "Every day at 8am, go through the 'open' and 'restricted' requests in ops_db kyc_requests, with how many days each has been open "
      "(work it out in the query from requested_on, as days_open). For each request, first ask the document service whether the customer "
      "has uploaded the documents. If they have: set the request to 'complete', lift the restriction through core banking if it was "
      "'restricted', and email the customer a thank-you. If not: from day 3 with no reminder sent yet, email the first reminder and set "
      "reminders_sent to 1; from day 7 with one reminder sent, email the second reminder and set reminders_sent to 2; from day 10 with two "
      "reminders sent and still 'open', restrict the customer through core banking, set status 'restricted', email the customer that "
      "their accounts are restricted, and email RM_EMAIL to call them. Otherwise leave that request alone. At the end, if anything "
      "happened, email KYC_TEAM_EMAIL one summary with how many were completed, reminded and restricted.",
      ["dms.yaml", "accounts.yaml"],
      {"trigger": "Cron", "must_have_types": ["Postgres", "SendEmail"], "must_call": ["dms.documents", "accounts.restrictCustomer",
                                                                                     "accounts.liftRestriction"], "max_nodes": 30},
      [{"name": "Every state at once (interesting rows not first)", "input": {"items": [{}]},
        "mocks": kyc([k("cu1", 2, 0), k("cu2", 12, 2, "restricted"), k("cu3", 3, 0), k("cu4", 8, 1), k("cu5", 10, 2), k("cu6", 6, 1)], uploaded=["cu2"]),
        "expect": [calls("$api:dms.documents", 6), calls("$api:accounts.liftRestriction", 1), req("$api:accounts.liftRestriction", "url", "contains", "cu2"),
                   calls("$api:accounts.restrictCustomer", 1), req("$api:accounts.restrictCustomer", "url", "contains", "cu5"),
                   to("cu2@mail.test"), to("cu3@mail.test"), to("cu4@mail.test"), to("cu5@mail.test"), to("rm@bank.test"), to("kyc@bank.test"),
                   calls("$all:SendEmail", 6), calls(KYCW, 4), req(KYCW, "text", "contains", "complete"), req(KYCW, "text", "contains", "restricted")]},
       {"name": "Boundaries: day 9 waits, already restricted isn't restricted again, day 7 with none sent gets the FIRST reminder",
        "input": {"items": [{}]}, "mocks": kyc([k("cu1", 9, 2), k("cu2", 30, 2, "restricted"), k("cu3", 7, 0)]),
        "expect": [st("$api:accounts.restrictCustomer", "not_run"), to("cu3@mail.test"), calls("$all:SendEmail", 2), calls(KYCW, 1)]},
       {"name": "A restricted customer who uploads is lifted", "input": {"items": [{}]},
        "mocks": kyc([k("cu1", 1, 0), k("cu7", 15, 2, "restricted")], uploaded=["cu7"]),
        "expect": [req("$api:accounts.liftRestriction", "url", "contains", "cu7"), to("cu7@mail.test"), to("kyc@bank.test")]},
       {"name": "An open customer who uploads is not 'lifted'", "input": {"items": [{}]}, "mocks": kyc([k("cu8", 4, 1)], uploaded=["cu8"]),
        "expect": [st("$api:accounts.liftRestriction", "not_run"), req(KYCW, "text", "contains", "complete")]},
       {"name": "Nothing due: no emails, no writes", "input": {"items": [{}]}, "mocks": kyc([k("cu1", 1, 0), k("cu2", 5, 1)], counts=(0, 0, 0)),
        "expect": [st("$all:SendEmail", "not_run"), st(KYCW, "not_run"), req(KYCQ, "sql", "contains", "requested_on")]}])

# ── X4: quarterly access recertification: one task PER MANAGER (parallel, per_item or a Loop), reminder + re-ask, timeout revokes all ──
ASK1, ASK2 = "$nth:Approval::1", "$nth:Approval::2?"
UA = "$pgr:user_access"
OUTCOMES = {"$pgr:audit_log?": rows({"entity_id": "m1@bank.test", "action": "revoked_some", "detail": "u3"},
                                    {"entity_id": "m2@bank.test", "action": "confirmed", "detail": ""})}


def access(*grants):
    return rows(*[{"user_id": u, "user_email": f"{u}@bank.test", "system": s, "manager_email": m} for u, s, m in grants])


GRANTS = [("u1", "core", "m1@bank.test"), ("u4", "swift", "m2@bank.test"), ("u2", "core", "m1@bank.test"), ("u3", "crm", "m1@bank.test")]


def per_manager(rule_m1, rule_m2):
    """An approval mock that decides per manager (each manager's task decides on its own item)."""
    def pick(field):
        return "{{ " + f"{rule_m1[field]!r} if 'm1@' in json_stringify($json) else {rule_m2[field]!r}" + " }}"
    return {"approval": {"decision": pick("decision"), "comment": "", "form_data": {"revoke": pick("revoke")}, "decided_by": "test"}}


crazy("access_recertification",
      "On the first day of each quarter, read every user's access from iam_db user_access and group it by manager. Each manager gets "
      "their own task (sent to the manager's email, due in 7 days) listing their people's user ids and systems, where they enter revoke: "
      "a comma-separated list of the user ids to remove (empty keeps everyone). If a manager hasn't answered in 7 days, email them a "
      "reminder and give them a second task, due in 3 days. If that runs out too, revoke access for all of that manager's users and email "
      "CISO_EMAIL naming the manager. Revoke each listed user through the IAM API. Record each manager's outcome in ops_db audit_log "
      "(entity 'recert', the manager's email, action 'confirmed' when nobody was revoked, 'revoked_some', or 'revoked_all_timeout'). When "
      "every manager is done, email CISO_EMAIL one summary.",
      ["iam.yaml"],
      {"trigger": "Cron", "must_have_types": ["Postgres", "Approval", "SendEmail"], "must_call": ["iam.revoke"], "max_nodes": 30},
      [{"name": "One manager revokes one user, the other keeps everyone", "input": {"items": [{}]},
        "mocks": {UA: access(*GRANTS), ASK1: per_manager({"decision": "approved", "revoke": "u3"}, {"decision": "approved", "revoke": ""}),
                  ASK2: decide("approved"), "$api:iam.revoke": http({"user_id": "x"}), AUD: OK_TX, "$all:SendEmail": MAIL, **OUTCOMES},
        "expect": [calls(ASK1, 2), calls("$api:iam.revoke", 1), req("$api:iam.revoke", "url", "contains", "u3"), calls(AUD, 2),
                   req(AUD, "text", "contains", "revoked_some"), req(AUD, "text", "contains", "confirmed"),
                   req(ASK1, "params.due_in", "one_of", ["7d", "168h"]), calls("$all:SendEmail", 1), to("ciso@bank.test")]},
       {"name": "Each manager sees only their own people", "input": {"items": [{}]},
        "mocks": {UA: access(*GRANTS), ASK1: per_manager({"decision": "approved", "revoke": ""}, {"decision": "approved", "revoke": ""}),
                  ASK2: decide("approved"), "$api:iam.revoke?": http({"user_id": "x"}), AUD: OK_TX, "$all:SendEmail": MAIL, **OUTCOMES},
        "expect": [req(ASK1, "params.notify_email", "eq", "m1@bank.test"), req(ASK1, "params.notify_email", "eq", "m2@bank.test"),
                   st("$api:iam.revoke?", "not_run")]},
       {"name": "m1 answers, m2 ignores the task and the reminder: only m2's user revoked, CISO told", "input": {"items": [{}]},
        "mocks": {UA: access(*GRANTS), ASK1: per_manager({"decision": "approved", "revoke": ""}, {"decision": "timed_out", "revoke": ""}),
                  ASK2: decide("timed_out"), "$api:iam.revoke": http({"user_id": "x"}), AUD: OK_TX, "$all:SendEmail": MAIL, **OUTCOMES},
        "expect": [to("m2@bank.test"), calls(ASK2, 1), req(ASK2, "params.due_in", "one_of", ["3d", "72h"]),
                   calls("$api:iam.revoke", 1), req("$api:iam.revoke", "url", "contains", "u4"), req(AUD, "text", "contains", "revoked_all_timeout"),
                   req(AUD, "text", "contains", "confirmed"), req("$all:SendEmail", "params.body", "contains", "m2@bank.test"),
                   calls("$all:SendEmail", 3)]}])

# ── X5: hardship restructuring: early 202 reply, officer assessment form, conditional risk manager, offer, customer-response
#        task (5d) -> reminder + re-task (5d) -> lapse; accepted -> core + atomic DB + confirmation ──────────────────────────
ASSESS = "$nth:Approval:COLLECTIONS_OFFICER_EMAIL:1"
ACC1, ACC2 = "$nth:Approval:COLLECTIONS_OFFICER_EMAIL:2?", "$nth:Approval:COLLECTIONS_OFFICER_EMAIL:3?"
RISK = "$has:Approval:RISK_MANAGER_EMAIL?"
RP = "$pgw:restructure_plans?"
JO = "jo@mail.test"


def hard(plan="payment_holiday", months=3, outstanding="20000.00", assess=None, risk=None, acc1=None, acc2=None, found=True):
    return {"$pg:loans": rows({"loan_id": "L3", "customer_id": "CU3", "outstanding": outstanding, "days_past_due": 10, "status": "active"}) if found else rows(),
            ASSESS: assess or decide("approved", form={"plan": plan, "months": months}), RISK: risk or decide("approved"),
            ACC1: acc1 or decide("approved", form={"customer_response": "accepted"}), ACC2: acc2 or decide("approved", form={"customer_response": "accepted"}),
            "$api:accounts.restructureLoan?": http({"loan_id": "L3", "status": "restructured"}), RP: OK_TX, "$all:SendEmail?": MAIL}


h_in = hook(loan_id="L3", customer_email=JO, reason="lost my job")
crazy("hardship_restructure",
      "Customers in financial difficulty apply by webhook with loan_id, customer_email and reason. Look the loan up in core banking; if "
      "it isn't there, reply 404 {status: 'not_found'} and stop. Otherwise reply 202 {status: 'in_review'} straight away and carry on. A "
      "collections officer (task for COLLECTIONS_OFFICER_EMAIL, due in 2 days) assesses it and chooses plan (payment_holiday, "
      "term_extension or reduced_payments) and months; they can also reject it. A payment holiday longer than 3 months, or any plan on a "
      "loan with more than 50,000 outstanding, also needs the risk manager (RISK_MANAGER_EMAIL, due in 1 day, rejected automatically if "
      "nobody decides). Once approved, email the customer the offer (plan and months) and give COLLECTIONS_OFFICER_EMAIL a task, due in "
      "5 days, to record the customer's answer as customer_response (accepted or declined). If there's no answer in 5 days, email the "
      "customer a reminder and give the officer the task again, due in 5 more days; if that runs out too, the offer lapses: email the "
      "customer and record the plan in ops_db restructure_plans with status 'lapsed'. Accepted: apply it through core banking "
      "(restructureLoan with plan and months), and in one ops_db transaction insert the restructure_plans row (loan id, plan, months, "
      "status 'active') and an audit_log row (entity 'loan', the loan id, action 'restructured'); then email the customer a "
      "confirmation. Declined by the customer or rejected by the officer or risk manager: email the customer, and record the plan with "
      "status 'declined'.",
      ["accounts.yaml"],
      {"trigger": "Webhook", "must_have_types": ["Approval", "Postgres", "Respond", "SendEmail"], "must_call": ["accounts.restructureLoan"],
       "max_nodes": 40, "min_types": {"Approval": 4}},
      [{"name": "Unknown loan: 404 and nothing else", "input": h_in, "mocks": hard(found=False),
        "expect": [req("$all:Respond", "statusCode", "eq", 404), calls("$all:Respond", 1), st("$all:Approval", "not_run")]},
       {"name": "3-month holiday, accepted first time: 202 then restructured", "input": h_in, "mocks": hard(),
        "expect": [req("$all:Respond", "statusCode", "eq", 202), req("$all:Respond", "body.status", "eq", "in_review"), st(RISK, "not_run"),
                   req("$api:accounts.restructureLoan", "body.plan", "eq", "payment_holiday"), req("$api:accounts.restructureLoan", "body.months", "eq", 3),
                   req(RP, "text", "contains", "active"), req(RP, "sql", "contains", "audit_log"), to(JO), calls("$all:SendEmail", 2),
                   req(ACC1, "params.due_in", "one_of", ["5d", "120h"])]},
       {"name": "4-month holiday needs the risk manager", "input": h_in, "mocks": hard(months=4),
        "expect": [calls(RISK, 1), req(RISK, "params.on_timeout", "eq", "reject"), calls("$api:accounts.restructureLoan", 1)]},
       {"name": "Over 50,000 outstanding: risk manager rejects, customer told, declined recorded", "input": h_in,
        "mocks": hard(plan="term_extension", months=12, outstanding="50000.01", risk=decide("rejected", "too large")),
        "expect": [calls(RISK, 1), st("$api:accounts.restructureLoan", "not_run"), st(ACC1, "not_run"), to(JO), req(RP, "text", "contains", "declined")]},
       {"name": "Customer silent 5 days, then declines", "input": h_in,
        "mocks": hard(acc1=decide("timed_out"), acc2=decide("approved", form={"customer_response": "declined"})),
        "expect": [calls(ACC2, 1), st("$api:accounts.restructureLoan", "not_run"), req(RP, "text", "contains", "declined"), calls("$all:SendEmail", 3)]},
       {"name": "Customer silent 10 days: lapsed", "input": h_in, "mocks": hard(acc1=decide("timed_out"), acc2=decide("timed_out")),
        "expect": [st("$api:accounts.restructureLoan", "not_run"), req(RP, "text", "contains", "lapsed"), calls("$all:SendEmail", 3)]},
       {"name": "Officer rejects: no risk manager, no offer", "input": h_in, "mocks": hard(assess=decide("rejected", "not eligible")),
        "expect": [st(RISK, "not_run"), st(ACC1, "not_run"), req(RP, "text", "contains", "declined"), calls("$all:SendEmail", 1)]}])

print("wrote", sorted(p.name for p in (cc.OUT / "eval").glob("*.json")))
