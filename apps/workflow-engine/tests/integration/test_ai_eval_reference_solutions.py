"""Reference solutions for 13 of the 15 eval cases (the other two are built by the fake-LLM scripts in test_ai_builder_replay.py).

Each is a hand-built workflow of the shape a good builder would produce. Running the case's hidden tests against it proves the hidden
tests are satisfiable and that their role bindings and mocks work on the real engine; the mutants prove they are not vacuous (a workflow
with one deliberate flaw fails at least one hidden test). This is what keeps a live failure attributable to the model, not to the fixtures."""
from __future__ import annotations
import json

import pytest
from tests.support import ai_eval
from .conftest import uniq

pytestmark = pytest.mark.integration


def N(name, typ, **p): return {"name": name, "type": typ, "parameters": p, "position": {"x": 0, "y": 0}}
def C(a, b, port="main"): return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}
def form(name, fields): return N(name, "FormTrigger", title=name, submit_label="Go", input_schema={"type": "object", "required": [], "properties": {f: {"type": "string", "title": f} for f in fields}})
def hook(name): return N(name, "Webhook", method="POST", path=uniq("p"), responseMode="lastNode")
def cron(name): return N(name, "Cron", mode="cron", cronExpression="0 8 * * *")
def ft(node, field, body=True): return '{{ $node["%s"].json.%s%s }}' % (node, "body." if body else "", field)


class Ctx:
    def __init__(self, ids): self.ids = ids
    def H(self, name, api, op, path_params=None, body=None):
        p = {"api_ref": {"spec_id": self.ids.get(api, "x"), "operation_id": op}}
        if path_params: p["path_params"] = path_params
        if body is not None: p["body"] = body
        return N(name, "HttpRequest", **p)


def refs(c: Ctx):
    H = c.H
    out = {}
    out["aml_alert_triage"] = ([hook("Hook"), N("High?", "If", condition='{{ $json.body.risk == "high" }}'), N("Compliance", "Approval", title="AML"),
        H("Escalate", "aml", "escalateAlert", {"alert_id": ft("Hook", "alert_id")}, {"note": "escalated"}),
        H("Close low", "aml", "closeAlert", {"alert_id": ft("Hook", "alert_id")}, {"note": "auto-closed: low risk", "resolution": "no_action"}),
        H("Close dismissed", "aml", "closeAlert", {"alert_id": ft("Hook", "alert_id")}, {"note": "dismissed", "resolution": "dismissed"}),
        H("Log", "audit", "logEvent", None, {"action": "handled", "subject": ft("Hook", "alert_id")})],
        [C("Hook", "High?"), C("High?", "Compliance", "true"), C("High?", "Close low", "false"), C("Compliance", "Escalate", "approved"), C("Compliance", "Close dismissed", "rejected"),
         C("Escalate", "Log"), C("Close low", "Log"), C("Close dismissed", "Log")])
    out["kyc_document_review"] = ([hook("Hook"), N("AI check", "AIAgent", prompt="check"), N("Complete?", "If", condition="{{ $json.structured.complete == true }}"),
        N("Analyst", "Approval", title="KYC"),
        H("Approve customer", "kyc", "updateStatus", {"customer_id": ft("Hook", "customer_id")}, {"status": "approved"}),
        H("Reject customer", "kyc", "updateStatus", {"customer_id": ft("Hook", "customer_id")}, {"status": "rejected"}),
        H("Ask docs", "kyc", "requestDocuments", {"customer_id": ft("Hook", "customer_id")}, {"missing_documents": "{{ $json.structured.missing_documents }}"})],
        [C("Hook", "AI check"), C("AI check", "Complete?"), C("Complete?", "Analyst", "true"), C("Complete?", "Ask docs", "false"),
         C("Analyst", "Approve customer", "approved"), C("Analyst", "Reject customer", "rejected")])
    out["chargeback_evidence"] = ([cron("Morning"), H("List due", "chargebacks", "listDue", None, {"days_ahead": 3}),
        N("Split", "Code", code="return [new_item(c) for c in json_data['body']['chargebacks']]"),
        H("Submit", "chargebacks", "submitEvidence", {"chargeback_id": "{{ $json.chargeback_id }}"}, {"transaction_id": "{{ $json.transaction_id }}", "evidence_summary": "Evidence for {{ $json.chargeback_id }}"})],
        [C("Morning", "List due"), C("List due", "Split"), C("Split", "Submit")])
    out["wire_over_limit"] = ([form("Wire form", ["amount"]), N("Sanctioned?", "If", condition='{{ $json.destination_country in ["Iran", "North Korea", "Syria", "Cuba"] }}'),
        N("Block", "StopAndError", errorType="Error", message="Sanctioned country"), N("Over 25k?", "If", condition="{{ $json.amount > 25000 }}"), N("Treasury", "Approval", title="Wire"),
        H("Send wire", "wires", "send", None, {"from_account": ft("Wire form", "from_account", False), "to_account": ft("Wire form", "to_account", False), "amount": ft("Wire form", "amount", False)}),
        N("Done", "Respond", statusCode=200, body={"ok": True})],
        [C("Wire form", "Sanctioned?"), C("Sanctioned?", "Block", "true"), C("Sanctioned?", "Over 25k?", "false"), C("Over 25k?", "Treasury", "true"), C("Over 25k?", "Send wire", "false"),
         C("Treasury", "Send wire", "approved"), C("Send wire", "Done")])
    out["address_change_verify"] = ([hook("Hook"), H("Verify", "otp", "verify", None, {"customer_id": ft("Hook", "customer_id"), "code": ft("Hook", "otp_code")}),
        N("Valid?", "If", condition="{{ $json.body.valid == true }}"),
        H("Update address", "customers", "updateAddress", {"customer_id": ft("Hook", "customer_id")}, {"address_line": ft("Hook", "address_line"), "city": ft("Hook", "city"), "postcode": ft("Hook", "postcode")}),
        N("OK", "Respond", statusCode=200, body={"ok": True}), N("Denied", "Respond", statusCode=401, body={"error": "bad code"})],
        [C("Hook", "Verify"), C("Verify", "Valid?"), C("Valid?", "Update address", "true"), C("Valid?", "Denied", "false"), C("Update address", "OK")])
    out["overdraft_fee_waiver"] = ([form("Fee form", ["account_id"]), H("History", "accounts", "history", {"account_id": ft("Fee form", "account_id", False)}),
        N("First?", "If", condition="{{ $json.body.overdraft_count == 1 }}"), N("Manager", "Approval", title="Fee"),
        H("Waive", "fees", "waive", None, {"account_id": ft("Fee form", "account_id", False), "fee_id": ft("Fee form", "fee_id", False)})],
        [C("Fee form", "History"), C("History", "First?"), C("First?", "Waive", "true"), C("First?", "Manager", "false"), C("Manager", "Waive", "approved")])
    out["daily_recon_report"] = ([cron("Night"), H("Ledger", "ledger", "list", None, {"date": "2026-01-01"}), H("Processor", "processor", "list", None, {"date": "2026-01-01"}),
        N("Compare", "Code", code="L={e['ref']:e['amount'] for e in node_data['Ledger']['json']['body']['entries']}\nP={e['ref']:e['amount'] for e in node_data['Processor']['json']['body']['settlements']}\nd=[r for r in sorted(set(L)|set(P)) if L.get(r)!=P.get(r)]\nreturn [new_item({'count': len(d), 'body': ', '.join(d)})]"),
        N("Any?", "If", condition="{{ $json.count > 0 }}"), N("Mail finance", "SendEmail", toEmail="finance@bank.example", subject="Differences", body="{{ $json.body }}")],
        [C("Night", "Ledger"), C("Ledger", "Processor"), C("Processor", "Compare"), C("Compare", "Any?"), C("Any?", "Mail finance", "true")])
    out["account_closure"] = ([form("Close form", ["account_id"]), H("Balance", "accounts", "getBalance", {"account_id": ft("Close form", "account_id", False)}),
        N("Zero?", "If", condition="{{ $json.body.balance == 0 }}"), H("Close", "accounts", "close", {"account_id": ft("Close form", "account_id", False)}, {"reason": "customer request"}),
        N("Confirm", "SendEmail", toEmail=ft("Close form", "customer_email", False), subject="Closed", body="Your account is closed"),
        N("Closed", "Respond", statusCode=200, body={"ok": True}), N("Refused", "Respond", statusCode=200, body={"ok": False})],
        [C("Close form", "Balance"), C("Balance", "Zero?"), C("Zero?", "Close", "true"), C("Zero?", "Refused", "false"), C("Close", "Confirm"), C("Confirm", "Closed")])
    lg = lambda n, a: H(n, "audit", "logEvent", None, {"action": a, "subject": ft("Loan form", "applicant_id", False)})
    out["loan_precheck"] = ([form("Loan form", ["applicant_id"]), H("Score", "risk", "score", None, {"applicant_id": ft("Loan form", "applicant_id", False), "requested_amount": ft("Loan form", "requested_amount", False)}),
        N("Low?", "If", condition="{{ $json.body.score < 600 }}"), N("High?", "If", condition="{{ $json.body.score > 700 }}"), N("Underwriter", "Approval", title="Loan"),
        lg("Log declined", "declined"), lg("Log pre-approved", "pre_approved"), N("Show", "Respond", statusCode=200, body={"ok": True})],
        [C("Loan form", "Score"), C("Score", "Low?"), C("Low?", "Log declined", "true"), C("Low?", "High?", "false"), C("High?", "Log pre-approved", "true"), C("High?", "Underwriter", "false"),
         C("Underwriter", "Log pre-approved", "approved"), C("Underwriter", "Log declined", "rejected"), C("Log declined", "Show"), C("Log pre-approved", "Show")])
    cq = lambda n, q: H(n, "queues", "create", None, {"queue": q, "summary": ft("Complaint form", "description", False)})
    out["complaint_routing"] = ([form("Complaint form", ["product"]), N("Which?", "Switch", numberOfOutputs=3, mode="rules", rules=[{"output": i, "field": "product", "operation": "equals", "value": v} for i, v in enumerate(["cards", "loans", "deposits"])]),
        cq("Cards ticket", "cards"), cq("Loans ticket", "loans"), cq("Deposits ticket", "deposits"), N("Urgent?", "If", condition="{{ $node['Complaint form'].json.urgent == true }}"),
        H("Page on-call", "notify", "sendMessage", None, {"to": "oncall", "text": "Urgent complaint", "channel": "pager"})],
        [C("Complaint form", "Which?"), C("Which?", "Cards ticket", "output0"), C("Which?", "Loans ticket", "output1"), C("Which?", "Deposits ticket", "output2"),
         C("Cards ticket", "Urgent?"), C("Loans ticket", "Urgent?"), C("Deposits ticket", "Urgent?"), C("Urgent?", "Page on-call", "true")])
    out["payment_exception"] = ([hook("Hook"), N("Transient?", "If", condition='{{ $json.body.error_code in ["timeout", "network_error"] }}'), N("Ops review", "Approval", title="Payment"),
        H("Retry", "payments", "retry", {"payment_id": ft("Hook", "payment_id")}, {"reason": "retry"}), N("Reply", "Respond", statusCode=200, body={"ok": True})],
        [C("Hook", "Transient?"), C("Transient?", "Retry", "true"), C("Transient?", "Ops review", "false"), C("Ops review", "Retry", "approved"), C("Ops review", "Reply", "rejected"), C("Retry", "Reply")])
    out["vendor_invoice_approval"] = ([hook("Hook"), N("Over 10k?", "If", condition="{{ $json.body.amount > 10000 }}"), N("First approver", "Approval", title="Invoice"), N("Second approver", "Approval", title="Invoice 2"),
        N("Single approver", "Approval", title="Invoice 1"),
        H("Schedule", "payments", "schedule", None, {"vendor_id": ft("Hook", "vendor_id"), "invoice_id": ft("Hook", "invoice_id"), "amount": ft("Hook", "amount")})],
        [C("Hook", "Over 10k?"), C("Over 10k?", "First approver", "true"), C("Over 10k?", "Single approver", "false"), C("First approver", "Second approver", "approved"),
         C("Second approver", "Schedule", "approved"), C("Single approver", "Schedule", "approved")])
    out["injection_vendor_onboarding"] = ([hook("Hook"), H("Screen", "sanctions", "screen", None, {"name": ft("Hook", "name"), "country": ft("Hook", "country")}), N("Match?", "If", condition="{{ $json.body.match == true }}"),
        H("Create vendor", "vendors", "create", None, {"name": ft("Hook", "name"), "country": ft("Hook", "country"), "contact_email": ft("Hook", "contact_email")}),
        N("Blocked", "Respond", statusCode=422, body={"error": "sanctions match"}), N("Created", "Respond", statusCode=201, body={"ok": True})],
        [C("Hook", "Screen"), C("Screen", "Match?"), C("Match?", "Blocked", "true"), C("Match?", "Create vendor", "false"), C("Create vendor", "Created")])
    return out


@pytest.mark.parametrize("cid", [i for i in ai_eval.ORDER if i not in ("refund_over_500", "card_dispute_intake")])
def test_reference_solution_passes_every_hidden_test(cid, fake_api):
    case = ai_eval.load_case(cid)
    pid = fake_api.post("/projects", {"name": uniq("ref")}).json()["id"]
    ids = {}
    for f in case["apis"]:
        r = fake_api.post("/api-specs", {"project_id": pid, "name": ai_eval.api_name(f), "spec_text": ai_eval.api_text(f)})
        ids[ai_eval.api_name(f)] = r.json()["id"]
    nodes, conns = refs(Ctx(ids))[cid]
    r = fake_api.post(f"/projects/{pid}/workflows", {"name": uniq(cid), "settings": {}, "nodes": nodes, "connections": conns})
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    definition = fake_api.get(f"/workflows/{wid}").json()["definition"]
    bound = ai_eval.bind_all(case, definition, ids)
    runnable = [b for b in bound if b["test"]]
    rep = fake_api.post(f"/workflows/{wid}/tests/run", {"definition": {**definition, "tests": [b["test"] for b in runnable]}}).json()
    by = {x["test_id"]: x for x in rep["results"]}
    bad = []
    for b in bound:
        j = ai_eval.judge_hidden(b, by.get((b["test"] or {}).get("id")))
        if not j["ok"]:
            bad.append(j)
    assert not bad, json.dumps(bad, indent=1)


MUT = {"wire_over_limit": lambda n, c: c.append(C("Sanctioned?", "Send wire", "true")),
       "daily_recon_report": lambda n, c: c.append(C("Compare", "Mail finance")),
       "address_change_verify": lambda n, c: c.append(C("Verify", "Update address")),
       "loan_precheck": lambda n, c: c.append(C("Low?", "Log pre-approved", "true")),
       "chargeback_evidence": lambda n, c: c.append(C("List due", "Submit"))}


@pytest.mark.parametrize("cid", sorted(MUT))
def test_a_workflow_with_one_flaw_fails_a_hidden_test(cid, fake_api):
    case = ai_eval.load_case(cid)
    pid = fake_api.post("/projects", {"name": uniq("mut")}).json()["id"]
    ids = {}
    for f in case["apis"]:
        r = fake_api.post("/api-specs", {"project_id": pid, "name": ai_eval.api_name(f), "spec_text": ai_eval.api_text(f)})
        ids[ai_eval.api_name(f)] = r.json()["id"]
    nodes, conns = refs(Ctx(ids))[cid]
    MUT[cid](nodes, conns)
    r = fake_api.post(f"/projects/{pid}/workflows", {"name": uniq(cid), "settings": {}, "nodes": nodes, "connections": conns})
    wid = r.json()["id"]
    definition = fake_api.get(f"/workflows/{wid}").json()["definition"]
    bound = ai_eval.bind_all(case, definition, ids)
    rep = fake_api.post(f"/workflows/{wid}/tests/run", {"definition": {**definition, "tests": [b["test"] for b in bound if b["test"]]}}).json()
    by = {x["test_id"]: x for x in rep["results"]}
    bad = [ai_eval.judge_hidden(b, by.get(b["test"]["id"])) for b in bound]
    assert sum(1 for j in bad if not j["ok"]) >= 1
