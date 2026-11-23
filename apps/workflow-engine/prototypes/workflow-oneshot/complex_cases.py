"""Writes the complex suite: complex/apis/*.yaml and complex/eval/*.json (same format as the engine's eval cases).

Bindings: $api:<api>.<op>, $type:<T>, $all:<T>, $op:DataTable.<operation>[|<operation>] (all DataTable nodes with that
operation; added by wf_build), $trigger, $last; a trailing ? makes a binding optional.
"""
import json
from pathlib import Path

import yaml

OUT = Path(__file__).parent / "complex"
(OUT / "apis").mkdir(parents=True, exist_ok=True)
(OUT / "eval").mkdir(parents=True, exist_ok=True)


def obj(**props):
    return {"type": "object", "properties": {k: ({"type": v} if isinstance(v, str) else v) for k, v in props.items()},
            "required": list(props)}


def api(name, ops):
    paths = {}
    for op_id, method, path, summary, req, reply in ops:
        o = {"operationId": op_id, "summary": summary,
             "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": reply}}}}}
        params = [p.strip("{}") for p in path.split("/") if p.startswith("{")]
        if params:
            o["parameters"] = [{"name": p, "in": "path", "required": True, "schema": {"type": "string"}} for p in params]
        if req:
            o["requestBody"] = {"required": True, "content": {"application/json": {"schema": req}}}
        paths.setdefault(path, {})[method] = o
    doc = {"openapi": "3.0.3", "info": {"title": name, "version": "1.0"}, "servers": [{"url": f"http://{name}.bank.test"}], "paths": paths}
    (OUT / "apis" / f"{name}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))


def http(body, status=200):
    return {"http": {"status": status, "body": body}}


def st(node, value):
    return {"node": node, "subject": "node", "op": "status", "value": value}


def req(node, path, op, value, item="any"):
    return {"node": node, "subject": "request", "item": item, "path": path, "op": op, "value": value}


def calls(node, n):
    return {"node": node, "subject": "request", "op": "count", "value": n}


def case(id_, request, apis, expect, tests, tables=None):
    (OUT / "eval" / f"{id_}.json").write_text(json.dumps({
        "id": id_, "request": request, "apis": apis, "tables": tables or {}, "expect": expect,
        "hidden_tests": [{"target": {"kind": "workflow"}, "category": "normal", **t} for t in tests]}, indent=1))


def hook(**body):
    return {"items": [{"body": body, "headers": {}, "query": {}, "method": "POST"}]}


APPROVE = {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {}, "decided_by": "test"}}
REJECT = {"port": "rejected", "approval": {"decision": "rejected", "comment": "", "form_data": {}, "decided_by": "test"}}

# ── APIs ──────────────────────────────────────────────────────────────────────────────────────
api("fraud", [
    ("listFlagged", "get", "/flagged-transactions", "Card transactions flagged by the fraud model since the last pull", None,
     obj(transactions={"type": "array", "items": obj(txn_id="string", card_id="string", customer_phone="string", score="integer")})),
    ("clear", "post", "/transactions/{txn_id}/clear", "Mark a flagged transaction as fine", None, obj(txn_id="string", status="string")),
    ("openCase", "post", "/cases", "Open a fraud case", obj(card_id="string", txn_id="string"), obj(case_id="string")),
])
api("cardctl", [
    ("freeze", "post", "/cards/{card_id}/freeze", "Temporarily freeze a card", None, obj(card_id="string", status="string")),
    ("block", "post", "/cards/{card_id}/block", "Permanently block a card", None, obj(card_id="string", status="string")),
])
api("sms", [("send", "post", "/sms", "Text a customer", obj(to="string", text="string"), obj(sent="boolean"))])
api("core", [("debit", "post", "/accounts/{account_id}/debit", "Debit an account (cash withdrawal)", obj(amount="number"),
               obj(txn_id="string"))])
api("bureau", [("creditScore", "post", "/credit-checks", "Credit score of an applicant", obj(applicant_id="string"),
                obj(applicant_id="string", score="integer"))])
api("fraudcheck", [("check", "post", "/fraud-checks", "Fraud check of an applicant", obj(applicant_id="string"),
                    obj(applicant_id="string", flagged="boolean"))])
api("loans", [
    ("book", "post", "/loans", "Book an approved loan", obj(applicant_id="string", product="string", amount="number"), obj(loan_id="string")),
    ("decline", "post", "/loans/declines", "Record a declined application", obj(applicant_id="string", reason="string"), obj(decline_id="string")),
])
api("screening", [("screen", "post", "/screen", "Screen a person or company name against sanctions lists", obj(name="string", country="string"),
                   obj(hit="boolean", list="string"))])
api("compliance", [("openCase", "post", "/cases", "Open a compliance case", obj(subject="string", reason="string"), obj(case_id="string"))])
api("payments", [
    ("hold", "post", "/payments/{payment_id}/hold", "Hold a payment", obj(reason="string"), obj(payment_id="string", status="string")),
    ("release", "post", "/payments/{payment_id}/release", "Release a payment for sending", None, obj(payment_id="string", status="string")),
])

# ── C1: fraud sweep: schedule, split, 3-way routing, per-item calls, conditional digest ───────
FLAG = "$api:fraud.listFlagged"
def txns(*rows):
    return http({"transactions": [{"txn_id": f"T{i}", "card_id": f"C{i}", "customer_phone": f"+1555000{i}", "score": s} for i, s in rows]})
case("fraud_sweep",
     "Every 15 minutes, pull the flagged card transactions from the fraud service. For each one: a score under 40 is fine, so clear the "
     "transaction. A score from 40 to 79 means freeze the card and text the customer that their card is frozen. A score of 80 or more means "
     "freeze the card, block it permanently and open a fraud case. When all of them are done, if any card was frozen, email the fraud team "
     "how many cards were frozen. If none were frozen, send no email.",
     ["fraud.yaml", "cardctl.yaml", "sms.yaml"],
     {"trigger": "Cron", "must_have_types": ["Switch|If", "SendEmail"],
      "must_call": ["fraud.listFlagged", "fraud.clear", "cardctl.freeze", "cardctl.block", "fraud.openCase", "sms.send"], "max_nodes": 20},
     [{"name": "Each score band does its own thing", "input": {"items": [{}]}, "mocks": {FLAG: txns((1, 10), (2, 50), (3, 90))},
       "expect": [calls("$api:fraud.clear", 1), req("$api:fraud.clear", "url", "contains", "T1"),
                  calls("$api:cardctl.freeze", 2), req("$api:cardctl.freeze", "url", "contains", "C2"), req("$api:cardctl.freeze", "url", "contains", "C3"),
                  calls("$api:cardctl.block", 1), req("$api:cardctl.block", "url", "contains", "C3"),
                  calls("$api:fraud.openCase", 1), req("$api:fraud.openCase", "body.txn_id", "eq", "T3"),
                  calls("$api:sms.send", 1), req("$api:sms.send", "body.to", "eq", "+15550002"),
                  calls("$all:SendEmail", 1), req("$all:SendEmail", "params.body", "contains", "2")]},
      {"name": "All low scores: cleared, nothing frozen, no email", "input": {"items": [{}]}, "mocks": {FLAG: txns((1, 5), (2, 39))},
       "expect": [calls("$api:fraud.clear", 2), st("$api:cardctl.freeze", "not_run"), st("$all:SendEmail", "not_run")]},
      {"name": "40 freezes but does not block", "input": {"items": [{}]}, "mocks": {FLAG: txns((1, 40))},
       "expect": [calls("$api:cardctl.freeze", 1), st("$api:cardctl.block", "not_run"), st("$api:fraud.openCase", "not_run"),
                  calls("$api:sms.send", 1), calls("$all:SendEmail", 1)]},
      {"name": "79 is not blocked, 80 is", "input": {"items": [{}]}, "mocks": {FLAG: txns((1, 79), (2, 80))},
       "expect": [calls("$api:cardctl.freeze", 2), calls("$api:cardctl.block", 1), req("$api:cardctl.block", "url", "contains", "C2"),
                  calls("$api:sms.send", 1), req("$api:sms.send", "body.to", "eq", "+15550001")]},
      {"name": "Nothing flagged: nothing happens", "input": {"items": [{}]}, "mocks": {FLAG: http({"transactions": []})},
       "expect": [st("$api:fraud.clear", "not_run"), st("$api:cardctl.freeze", "not_run"), st("$all:SendEmail", "not_run")]}])

# ── C2: dual-control withdrawal: form, tiered approvals, error branch, data table record ──────
W = {"account_id": "AC-7", "customer_name": "Dana Reyes"}
TBL = "$op:DataTable.insert|upsert"
case("cash_withdrawal_dual_control",
     "Tellers request a large cash withdrawal on a form with the account id, the amount and the customer's name. Up to $10,000 is approved "
     "automatically. Over $10,000 needs the branch manager's approval, and over $50,000 needs regional operations to approve as well, after "
     "the branch manager. If anyone rejects, nothing is debited. When it is approved, debit the account through core banking. If the debit "
     "call fails, show the teller 'Debit failed - do not hand out cash'. Record every request in the withdrawals table (account id, amount, "
     "customer name and the final status: approved, rejected or failed) and show the teller the outcome.",
     ["core.yaml"],
     {"trigger": "FormTrigger", "must_have_types": ["Approval", "If|Switch", "DataTable", "Respond"], "must_call": ["core.debit"],
      "interface_kind": "form", "max_nodes": 22, "min_types": {"Approval": 2}},
     [{"name": "Small amount is debited without approval", "input": {"items": [{**W, "amount": 5000}]},
       "mocks": {"$api:core.debit": http({"txn_id": "X1"}), "$all:Approval?": APPROVE, TBL: {"items": [{"id": 1}]}},
       "expect": [st("$all:Approval?", "not_run"), req("$api:core.debit", "body.amount", "eq", 5000), req("$api:core.debit", "url", "contains", "AC-7"),
                  req(TBL, "params.data.status", "eq", "approved")]},
      {"name": "Exactly 10,000 needs no approval", "input": {"items": [{**W, "amount": 10000}]},
       "mocks": {"$api:core.debit": http({"txn_id": "X1"}), "$all:Approval?": APPROVE, TBL: {"items": [{"id": 1}]}},
       "expect": [st("$all:Approval?", "not_run"), st("$api:core.debit", "success")]},
      {"name": "Manager approves a mid amount", "input": {"items": [{**W, "amount": 20000}]},
       "mocks": {"$api:core.debit": http({"txn_id": "X1"}), "$all:Approval": APPROVE, TBL: {"items": [{"id": 1}]}},
       "expect": [st("$api:core.debit", "success"), calls("$all:Approval", 1), req(TBL, "params.data.status", "eq", "approved"), req(TBL, "params.data.customer_name", "eq", "Dana Reyes")]},
      {"name": "Over 50,000 needs both approvals before the debit", "input": {"items": [{**W, "amount": 60000}]},
       "mocks": {"$api:core.debit": http({"txn_id": "X1"}), "$all:Approval": APPROVE, TBL: {"items": [{"id": 1}]}},
       "expect": [calls("$all:Approval", 2), st("$api:core.debit", "success"), req("$api:core.debit", "body.amount", "eq", 60000)]},
      {"name": "Large amount rejected: nothing debited, recorded rejected", "input": {"items": [{**W, "amount": 80000}]},
       "mocks": {"$api:core.debit": http({"txn_id": "X1"}), "$all:Approval": REJECT, TBL: {"items": [{"id": 1}]}},
       "expect": [st("$api:core.debit", "not_run"), req(TBL, "params.data.status", "eq", "rejected")]},
      {"name": "Debit fails: recorded failed", "input": {"items": [{**W, "amount": 900}]},
       "mocks": {"$api:core.debit": {"error": {"message": "core banking unavailable", "code": "ECONN"}}, "$all:Approval?": APPROVE, TBL: {"items": [{"id": 1}]}},
       "allow_node_errors": True,
       "expect": [req(TBL, "params.data.status", "eq", "failed")]}],
     tables={"withdrawals": [{"name": "account_id", "type": "string"}, {"name": "amount", "type": "number"},
                             {"name": "customer_name", "type": "string"}, {"name": "status", "type": "string"}]})

# ── C3: loan pipeline: two checks, fraud stop, score bands, product exception, AI summary, approval ──
L = {"applicant_id": "AP-1", "product": "personal", "amount": 15000, "income": 70000}
def loan_mocks(score, flagged=False, approval=None):
    m = {"$api:bureau.creditScore": http({"applicant_id": "AP-1", "score": score}), "$api:fraudcheck.check": http({"applicant_id": "AP-1", "flagged": flagged}),
         "$api:loans.book": http({"loan_id": "LN-1"}), "$api:loans.decline": http({"decline_id": "D-1"}),
         "$all:AIAgent?": {"items": [{"response": "Applicant summary", "structured": {"summary": "Applicant summary"}}]},
         "$all:LLMChat?": {"items": [{"response": "Applicant summary"}]}}
    m["$all:Approval?"] = approval or APPROVE
    return m
case("loan_decision_pipeline",
     "Loan applications are posted to us with applicant_id, product (personal, auto or mortgage), amount and income. Run a credit check "
     "and a fraud check on the applicant. If the fraud check flags them, decline straight away. Otherwise decide by credit score: below "
     "580 is declined; 580 to 719 goes to an underwriter; 720 or above is approved automatically, except mortgages, which always go to an "
     "underwriter. For the underwriter, have the AI write a short summary of the application to read alongside the numbers. Book approved "
     "loans in the loan system; record declined ones in the loan system as declines with a reason. Reply with the decision.",
     ["bureau.yaml", "fraudcheck.yaml", "loans.yaml"],
     {"trigger": "Webhook", "must_have_types": ["If|Switch", "Approval", "AIAgent|LLMChat", "Respond"],
      "must_call": ["bureau.creditScore", "fraudcheck.check", "loans.book", "loans.decline"], "max_nodes": 20},
     [{"name": "Good score personal loan is booked without an underwriter", "input": hook(**L), "mocks": loan_mocks(720),
       "expect": [req("$api:loans.book", "body.product", "eq", "personal"), req("$api:loans.book", "body.amount", "eq", 15000),
                  st("$all:Approval?", "not_run"), st("$api:loans.decline", "not_run")]},
      {"name": "Fraud flag declines even with a great score", "input": hook(**L), "mocks": loan_mocks(810, flagged=True),
       "expect": [st("$api:loans.decline", "success"), st("$api:loans.book", "not_run"), st("$all:Approval?", "not_run")]},
      {"name": "579 is declined", "input": hook(**L), "mocks": loan_mocks(579),
       "expect": [st("$api:loans.decline", "success"), req("$api:loans.decline", "body.applicant_id", "eq", "AP-1"), st("$api:loans.book", "not_run")]},
      {"name": "580 goes to an underwriter who approves", "input": hook(**L), "mocks": loan_mocks(580),
       "expect": [st("$all:Approval", "success"), st("$api:loans.book", "success")]},
      {"name": "Mortgage with a great score still needs an underwriter; rejected is declined", "input": hook(**{**L, "product": "mortgage"}),
       "mocks": loan_mocks(800, approval=REJECT),
       "expect": [st("$all:Approval", "success"), st("$api:loans.book", "not_run"), st("$api:loans.decline", "success")]}])

# ── C4: data-table batch: schedule, list pending rows, per-row screening, row updates, case, digest ──
ROWS = [{"id": 2, "name": "Ana Silva", "country": "BR", "status": "pending"}, {"id": 1, "name": "Ivan Petrov", "country": "RU", "status": "pending"}]  # the hit is NOT first
SCREEN = http({"hit": "{{ 'Ivan Petrov' in json_stringify($json) }}", "list": "OFAC"})
case("sanctions_review_batch",
     "Every weekday at 7am, take the rows in the sanctions_review table whose status is pending. Screen each person's name and country with "
     "the screening service. On a hit, set that row's status to hit and open a compliance case naming the person. With no hit, set the row's "
     "status to cleared. Finally, email compliance a list of the names that were hits. If nobody was a hit, send no email.",
     ["screening.yaml", "compliance.yaml"],
     {"trigger": "Cron", "must_have_types": ["DataTable", "SendEmail"], "must_call": ["screening.screen", "compliance.openCase"], "max_nodes": 18},
     [{"name": "One hit, one cleared", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": {"items": ROWS}, "$op:DataTable.update|upsert": {"items": [{"id": 1}]}, "$api:screening.screen": SCREEN},
       "expect": [calls("$api:screening.screen", 2), calls("$api:compliance.openCase", 1), req("$api:compliance.openCase", "body.subject", "contains", "Ivan Petrov"),
                  st("$op:DataTable.update|upsert", "success"), calls("$all:SendEmail", 1), req("$all:SendEmail", "params.body", "contains", "Ivan Petrov"),
                  req("$op:DataTable.update|upsert", "params.rowId", "one_of", [1, "1"]), req("$op:DataTable.update|upsert", "params.rowId", "one_of", [2, "2"])]},
      {"name": "Nobody is a hit: no case, no email", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": {"items": [ROWS[0]]}, "$op:DataTable.update|upsert": {"items": [{"id": 2}]}, "$api:screening.screen": SCREEN},
       "expect": [calls("$api:screening.screen", 1), st("$api:compliance.openCase", "not_run"), st("$all:SendEmail", "not_run"),
                  req("$op:DataTable.update|upsert", "params.data.status", "eq", "cleared")]},
      {"name": "A hit row is marked hit", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": {"items": [ROWS[1]]}, "$op:DataTable.update|upsert": {"items": [{"id": 1}]}, "$api:screening.screen": SCREEN},
       "expect": [req("$op:DataTable.update|upsert", "params.data.status", "eq", "hit"), req("$api:screening.screen", "body.name", "eq", "Ivan Petrov")]},
      {"name": "No pending rows: nothing screened", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": {"items": []}, "$op:DataTable.update|upsert?": {"items": []}, "$api:screening.screen": SCREEN},
       "expect": [st("$api:screening.screen", "not_run"), st("$all:SendEmail", "not_run")]},
      {"name": "Only pending rows are asked for", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": {"items": [ROWS[0]]}, "$op:DataTable.update|upsert": {"items": [{"id": 2}]}, "$api:screening.screen": SCREEN},
       "expect": [req("$op:DataTable.list", "params.filter.status", "eq", "pending")]}],
     tables={"sanctions_review": [{"name": "name", "type": "string"}, {"name": "country", "type": "string"}, {"name": "status", "type": "string"}]})

# ── C5: payment batch: split, per-item screening, hold hits, sum, batch approval, release each, reply counts ──
def batch(*pays):
    return hook(batch_id="B-9", payments=[{"payment_id": f"P{i}", "beneficiary": b, "country": "US", "amount": a} for i, b, a in pays])
SCR = {"$api:screening.screen": SCREEN, "$api:payments.hold": http({"status": "held"}), "$api:payments.release": http({"status": "released"})}
case("payment_batch_release",
     "Treasury posts a payment batch to us: batch_id and a list of payments, each with payment_id, beneficiary, country and amount. Screen "
     "every beneficiary with the screening service. Any payment whose beneficiary is a hit is held (call hold for that payment with the "
     "reason) and never released. If the total of the remaining payments is over $1,000,000, the head of treasury must approve the batch "
     "first; if they reject it, hold all the remaining payments too. Release each remaining payment that is cleared to go. Reply with how "
     "many payments were released and how many were held.",
     ["screening.yaml", "payments.yaml"],
     {"trigger": "Webhook", "must_have_types": ["Approval", "Respond"], "must_call": ["screening.screen", "payments.hold", "payments.release"], "max_nodes": 22},
     [{"name": "Clean small batch is all released", "input": batch((1, "Acme", 100000), (2, "Globex", 200000), (3, "Initech", 300000)),
       "mocks": {**SCR, "$all:Approval?": APPROVE},
       "expect": [calls("$api:screening.screen", 3), calls("$api:payments.release", 3), st("$api:payments.hold", "not_run"), st("$all:Approval?", "not_run")]},
      {"name": "A hit is held and the rest released", "input": batch((1, "Acme", 100000), (2, "Ivan Petrov", 200000), (3, "Initech", 300000)),
       "mocks": {**SCR, "$all:Approval?": APPROVE},
       "expect": [calls("$api:payments.hold", 1), req("$api:payments.hold", "url", "contains", "P2"), calls("$api:payments.release", 2),
                  req("$api:payments.release", "url", "contains", "P1"), req("$api:payments.release", "url", "contains", "P3")]},
      {"name": "Big batch approved is released", "input": batch((1, "Acme", 700000), (2, "Globex", 600000)),
       "mocks": {**SCR, "$all:Approval": APPROVE},
       "expect": [st("$all:Approval", "success"), calls("$api:payments.release", 2)]},
      {"name": "Big batch rejected: everything held", "input": batch((1, "Acme", 700000), (2, "Globex", 600000)),
       "mocks": {**SCR, "$all:Approval": REJECT},
       "expect": [st("$api:payments.release", "not_run"), calls("$api:payments.hold", 2)]},
      {"name": "Exactly 1,000,000 needs no approval; the held hit doesn't count", "input": batch((1, "Acme", 600000), (2, "Globex", 400000), (3, "Ivan Petrov", 500000)),
       "mocks": {**SCR, "$all:Approval?": APPROVE},
       "expect": [st("$all:Approval?", "not_run"), calls("$api:payments.release", 2), calls("$api:payments.hold", 1)]}])

print("wrote", sorted(p.name for p in (OUT / "eval").glob("*.json")))

# ══ Round 2: bigger flows, and cases that depend on the project context (variables, sub-workflows, timing) ══════════

def child(fields, outputs):
    """A minimal callable sub-workflow definition: ExecuteWorkflowTrigger(fields) -> Set(outputs)."""
    return {"name": "child", "nodes": [
        {"name": "Called", "type": "ExecuteWorkflowTrigger", "parameters": {"inputSource": "fields", "fields": [
            {"name": f, "type": t, "required": True} for f, t in fields]}},
        {"name": "Result", "type": "Set", "parameters": {"mode": "manual", "keepOnlySet": True,
                                                         "fields": [{"name": o, "value": v} for o, v in outputs]}}],
        "connections": [{"source_node": "Called", "target_node": "Result", "source_output": "main", "target_input": "main"}]}


KYC = {"id": "wf_kyc_verify", "name": "Verify customer identity", "description": "Checks a customer's ID document against our KYC provider and records the result.",
       "inputs": [{"name": "customer_id", "type": "string", "required": True}, {"name": "document_id", "type": "string", "required": True}],
       "output_fields": ["verified", "reason"],
       "definition": child([("customer_id", "string"), ("document_id", "string")], [("verified", "{{ true }}"), ("reason", "ok")])}
NOTIFY = {"id": "wf_notify_customer", "name": "Notify customer", "description": "Sends a customer a message on their preferred channel (sms or email) and logs it.",
          "inputs": [{"name": "customer_id", "type": "string", "required": True}, {"name": "message", "type": "string", "required": True}],
          "output_fields": ["sent", "channel"],
          "definition": child([("customer_id", "string"), ("message", "string")], [("sent", "{{ true }}"), ("channel", "sms")])}
SUBS = [KYC, NOTIFY]
VARS = {"WIRE_OPS_EMAIL": {"value": "wireops@bank.test"}, "BRANCH_OPS_EMAIL": {"value": "branchops@bank.test"},
        "FINANCE_OPS_EMAIL": {"value": "finops@bank.test"}, "ATM_DAILY_LIMIT": {"value": "1000", "description": "standard ATM limit per day, USD"},
        "COMPLIANCE_EMAIL": {"value": "compliance@bank.test"}, "CARDS_API_KEY": {"value": "sk-test-123", "secret": True}}

def ok(items):
    return {"items": items}

api("bizbureau", [("companyRating", "post", "/company-ratings", "Credit rating of a company (A best to D worst)", obj(registration_number="string"),
                   obj(registration_number="string", rating={"type": "string", "enum": ["A", "B", "C", "D"]}))])
api("accounts", [("openBusiness", "post", "/business-accounts", "Open a business account", obj(company_name="string", registration_number="string"),
                  obj(account_number="string"))])
api("crm", [("updateAddress", "put", "/customers/{customer_id}/address", "Change a customer's address", obj(address="string"), obj(customer_id="string"))])
api("wires", [("wireStatus", "get", "/wires/{wire_id}/status", "Where a sent wire is", None,
               obj(wire_id="string", status={"type": "string", "enum": ["pending", "settled", "failed"]}))])
api("cards", [("setLimit", "post", "/cards/{customer_id}/atm-limit", "Set a customer's ATM limit for today", obj(limit="number"), obj(limit="number"))])
api("network", [("settlements", "get", "/settlements/yesterday", "Yesterday's card settlement report", None,
                 obj(rows={"type": "array", "items": obj(reference="string", amount="number")}))])
api("ledger", [("entries", "get", "/entries/yesterday", "Our ledger's card entries for yesterday", None,
                obj(entries={"type": "array", "items": obj(reference="string", amount="number")}))])

# ── C6: business account opening: the biggest one (sub-workflow, screening, rating, 3 risk tiers, AI note, 1 or 2 approvals, error path, table, email)
B = {"company_name": "Northwind Ltd", "registration_number": "RN-55", "country": "GB", "director_customer_id": "C-9",
     "director_document_id": "D-9", "expected_monthly_volume": 500000, "contact_email": "cfo@northwind.test"}
APPS = "$op:DataTable.insert|upsert"
def biz(rating="A", hit=False, verified=True, approval=None, open_ok=True):
    return {"$wf:wf_kyc_verify": ok([{"verified": verified, "reason": "ok" if verified else "document expired"}]),
            "$api:screening.screen": http({"hit": hit, "list": "OFAC"}), "$api:compliance.openCase": http({"case_id": "CC-1"}),
            "$api:bizbureau.companyRating": http({"registration_number": "RN-55", "rating": rating}),
            "$api:accounts.openBusiness": http({"account_number": "BA-100"}) if open_ok else {"error": {"message": "core down", "code": "ECONN"}},
            "$all:Approval?": approval or APPROVE, APPS: ok([{"id": 1}]),
            "$all:AIAgent?": ok([{"response": "note", "structured": {"note": "Risk note", "summary": "Risk note"}}]), "$all:LLMChat?": ok([{"response": "Risk note"}])}
case("business_account_opening",
     "Business account applications are posted to us with company_name, registration_number, country, director_customer_id, "
     "director_document_id, expected_monthly_volume and contact_email. First verify the director's identity with our identity check; if "
     "they are not verified, decline. Then screen the company name and country for sanctions; a hit is declined and a compliance case is "
     "opened naming the company. Then get the company's credit rating. Risk is low if the rating is A or B and the expected monthly volume "
     "is at most 1,000,000; high if the rating is D or the volume is over 5,000,000; otherwise medium. Low risk opens automatically. Medium "
     "risk needs a compliance officer to approve. High risk needs a compliance officer and then the head of compliance (two different "
     "people). Approvers should see a short AI-written risk note about the application. Open approved accounts in the accounts system; if "
     "opening fails, reply with an error. Record every application in the business_applications table with company_name, "
     "registration_number, risk (when known) and status (opened, declined, rejected or failed). Email the contact the new account number "
     "when it opens. Reply with the decision.",
     ["screening.yaml", "compliance.yaml", "bizbureau.yaml", "accounts.yaml"],
     {"trigger": "Webhook", "must_have_types": ["ExecuteWorkflow", "Approval", "DataTable", "SendEmail", "Respond", "AIAgent|LLMChat"],
      "must_call": ["screening.screen", "compliance.openCase", "bizbureau.companyRating", "accounts.openBusiness"], "max_nodes": 40,
      "min_types": {"Approval": 2}},
     [{"name": "Low risk opens without approval and emails the account number", "input": hook(**B), "mocks": biz("A"),
       "expect": [st("$all:Approval?", "not_run"), req("$api:accounts.openBusiness", "body.registration_number", "eq", "RN-55"),
                  req("$wf:wf_kyc_verify", "params.inputs.customer_id", "eq", "C-9"), req("$api:screening.screen", "body.name", "eq", "Northwind Ltd"),
                  req(APPS, "params.data.status", "eq", "opened"), req(APPS, "params.data.risk", "eq", "low"),
                  req("$all:SendEmail", "params.toEmail", "eq", "cfo@northwind.test"), req("$all:SendEmail", "params.body", "contains", "BA-100")]},
      {"name": "Unverified director is declined before screening", "input": hook(**B), "mocks": biz("A", verified=False),
       "expect": [st("$api:accounts.openBusiness", "not_run"), st("$api:screening.screen", "not_run"), req(APPS, "params.data.status", "eq", "declined")]},
      {"name": "Sanctions hit is declined with a case", "input": hook(**B), "mocks": biz("A", hit=True),
       "expect": [req("$api:compliance.openCase", "body.subject", "contains", "Northwind"), st("$api:accounts.openBusiness", "not_run"),
                  req(APPS, "params.data.status", "eq", "declined")]},
      {"name": "Rating C is medium: one approval", "input": hook(**{**B, "expected_monthly_volume": 2000000}), "mocks": biz("C"),
       "expect": [calls("$all:Approval", 1), st("$api:accounts.openBusiness", "success"), req(APPS, "params.data.risk", "eq", "medium")]},
      {"name": "Exactly 1,000,000 with rating B is still low", "input": hook(**{**B, "expected_monthly_volume": 1000000}), "mocks": biz("B"),
       "expect": [st("$all:Approval?", "not_run"), st("$api:accounts.openBusiness", "success")]},
      {"name": "Over 5,000,000 is high: two approvals", "input": hook(**{**B, "expected_monthly_volume": 6000000}), "mocks": biz("B"),
       "expect": [calls("$all:Approval", 2), st("$api:accounts.openBusiness", "success"), req(APPS, "params.data.risk", "eq", "high")]},
      {"name": "High risk rejected: nothing opened, recorded rejected", "input": hook(**B), "mocks": biz("D", approval=REJECT),
       "expect": [st("$api:accounts.openBusiness", "not_run"), req(APPS, "params.data.status", "eq", "rejected"), st("$all:SendEmail", "not_run")]},
      {"name": "Opening fails: recorded failed, no email", "input": hook(**B), "mocks": biz("A", open_ok=False), "allow_node_errors": True,
       "expect": [req(APPS, "params.data.status", "eq", "failed"), st("$all:SendEmail", "not_run")]}],
     tables={"business_applications": [{"name": "company_name", "type": "string"}, {"name": "registration_number", "type": "string"},
                                       {"name": "risk", "type": "string"}, {"name": "status", "type": "string"}]})
ROUND2 = ["business_account_opening"]
for cid in ROUND2:
    c = json.loads((OUT / "eval" / f"{cid}.json").read_text()); c["variables"] = VARS; c["subworkflows"] = SUBS
    (OUT / "eval" / f"{cid}.json").write_text(json.dumps(c, indent=1))

# ── C7: reuse: two existing sub-workflows instead of rebuilding them
AC = {"customer_id": "C-4", "new_address": "12 High St, Leeds", "document_id": "D-4"}
def addr(verified=True):
    return {"$wf:wf_kyc_verify": ok([{"verified": verified, "reason": "ok"}]), "$wf:wf_notify_customer?": ok([{"sent": True, "channel": "sms"}]),
            "$api:crm.updateAddress": http({"customer_id": "C-4"})}
case("address_change_reuse",
     "Customers change their address on a form with their customer id, the new address and the id of the document they uploaded. Verify "
     "their identity with our identity check. If verified, update the address in the CRM and let the customer know their address was "
     "changed (use our usual customer notification). If not verified, show 'We could not verify you' and change nothing.",
     ["crm.yaml"],
     {"trigger": "FormTrigger", "must_have_types": ["ExecuteWorkflow", "If|Switch"], "must_call": ["crm.updateAddress"], "max_nodes": 10,
      "min_types": {"ExecuteWorkflow": 2}, "interface_kind": "form"},
     [{"name": "Verified: address changed and customer told", "input": {"items": [AC]}, "mocks": addr(),
       "expect": [req("$api:crm.updateAddress", "body.address", "eq", "12 High St, Leeds"), req("$api:crm.updateAddress", "url", "contains", "C-4"),
                  req("$wf:wf_kyc_verify", "params.inputs.document_id", "eq", "D-4"), req("$wf:wf_notify_customer", "params.inputs.customer_id", "eq", "C-4"),
                  req("$wf:wf_notify_customer", "params.inputs.message", "matches", "(?i)address")]},
      {"name": "Not verified: nothing changes, nobody notified", "input": {"items": [AC]}, "mocks": addr(False),
       "expect": [st("$api:crm.updateAddress", "not_run"), st("$wf:wf_notify_customer?", "not_run"),
                  req("$last", "body.message", "matches", "(?i)could not verify")]},
      {"name": "Identity check gets the customer", "input": {"items": [AC]}, "mocks": addr(),
       "expect": [req("$wf:wf_kyc_verify", "params.inputs.customer_id", "eq", "C-4"), st("$wf:wf_notify_customer", "success")]}])

# ── C8: hours-long tracking must become a schedule + table (not an in-run Poll), with variables for the address
def wire_rows(*rows):
    return ok([{"id": i, "wire_id": w, "status": "sent", "checks": c} for i, (w, c) in enumerate(rows, 1)])
def wst(expr):
    return http({"wire_id": "x", "status": expr})
UPD = "$op:DataTable.update|upsert"
case("wire_settlement_tracker",
     "Sent wires are tracked in the wire_tracking table. Every 10 minutes, check each wire whose status is 'sent' with the wire status "
     "service. Settled: set its status to settled. Failed: set it to failed and email wire ops the wire id. Still pending: add one to its "
     "check count, and if it had already been checked 12 times (two hours), set it to stuck instead and email wire ops the wire id.",
     ["wires.yaml"],
     {"trigger": "Cron", "must_have_types": ["DataTable", "SendEmail"], "must_call": ["wires.wireStatus"], "max_nodes": 16, "forbid_types": ["Poll", "Wait"]},
     [{"name": "Settled is marked settled, no email", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": wire_rows(("W-1", 2)), UPD: ok([{"id": 1}]), "$api:wires.wireStatus": wst("settled")},
       "expect": [req(UPD, "params.data.status", "eq", "settled"), st("$all:SendEmail", "not_run"), req("$api:wires.wireStatus", "url", "contains", "W-1"),
                  req("$op:DataTable.list", "params.filter.status", "eq", "sent")]},
      {"name": "Failed second wire: marked failed, wire ops told which", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": wire_rows(("W-1", 0), ("W-2", 0)), UPD: ok([{"id": 1}]),
                 "$api:wires.wireStatus": wst("{{ 'failed' if 'W-2' in json_stringify($json) else 'settled' }}")},
       "expect": [calls("$api:wires.wireStatus", 2), req(UPD, "params.data.status", "eq", "failed"), calls("$all:SendEmail", 1),
                  req("$all:SendEmail", "params.toEmail", "eq", "wireops@bank.test"), req("$all:SendEmail", "params.body", "contains", "W-2")]},
      {"name": "Pending: check count goes up", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": wire_rows(("W-3", 3)), UPD: ok([{"id": 1}]), "$api:wires.wireStatus": wst("pending")},
       "expect": [req(UPD, "params.data.checks", "eq", 4), st("$all:SendEmail", "not_run")]},
      {"name": "Pending after 12 checks is stuck", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": wire_rows(("W-4", 12)), UPD: ok([{"id": 1}]), "$api:wires.wireStatus": wst("pending")},
       "expect": [req(UPD, "params.data.status", "eq", "stuck"), req("$all:SendEmail", "params.body", "contains", "W-4")]},
      {"name": "11 checks is not stuck yet", "input": {"items": [{}]},
       "mocks": {"$op:DataTable.list": wire_rows(("W-5", 11)), UPD: ok([{"id": 1}]), "$api:wires.wireStatus": wst("pending")},
       "expect": [req(UPD, "params.data.checks", "eq", 12), st("$all:SendEmail", "not_run")]}],
     tables={"wire_tracking": [{"name": "wire_id", "type": "string"}, {"name": "status", "type": "string"}, {"name": "checks", "type": "number"}]})

# ── C9: a limit that lives in a variable (text!), an address that already exists, a secret that must not leak
L9 = {"customer_id": "C-7", "requested_limit": 1500, "reason": "travel"}
def lim(approval=None):
    return {"$api:cards.setLimit": http({"limit": 1}), "$all:Approval?": approval or APPROVE}
case("atm_limit_increase",
     "Branch staff request a one-day ATM limit change for a customer on a form: customer id, requested limit and reason. Our standard "
     "limit is the ATM daily limit setting. Requests up to twice the standard limit are applied straight away; above that, the branch "
     "manager must approve first. Apply the limit with the cards service. Email branch ops (we already have their address set up) about "
     "every limit applied above the standard limit, with the customer id and the new limit. Show the staff member the outcome.",
     ["cards.yaml"],
     {"trigger": "FormTrigger", "must_have_types": ["Approval", "If|Switch", "SendEmail"], "must_call": ["cards.setLimit"], "max_nodes": 14,
      "interface_kind": "form"},
     [{"name": "1500 is applied straight away and branch ops told", "input": {"items": [L9]}, "mocks": lim(),
       "expect": [st("$all:Approval?", "not_run"), req("$api:cards.setLimit", "body.limit", "eq", 1500), req("$api:cards.setLimit", "url", "contains", "C-7"),
                  req("$all:SendEmail", "params.toEmail", "eq", "branchops@bank.test"), req("$all:SendEmail", "params.body", "contains", "C-7")]},
      {"name": "Exactly twice the standard needs no approval", "input": {"items": [{**L9, "requested_limit": 2000}]}, "mocks": lim(),
       "expect": [st("$all:Approval?", "not_run"), st("$api:cards.setLimit", "success")]},
      {"name": "2001 waits for the manager, then applies", "input": {"items": [{**L9, "requested_limit": 2001}]}, "mocks": lim(),
       "expect": [st("$all:Approval", "success"), req("$api:cards.setLimit", "body.limit", "eq", 2001)]},
      {"name": "At or below the standard: applied, no email", "input": {"items": [{**L9, "requested_limit": 1000}]}, "mocks": lim(),
       "expect": [st("$api:cards.setLimit", "success"), st("$all:SendEmail", "not_run")]},
      {"name": "Rejected: not applied, no email", "input": {"items": [{**L9, "requested_limit": 5000}]}, "mocks": lim(REJECT),
       "expect": [st("$api:cards.setLimit", "not_run"), st("$all:SendEmail", "not_run")]}])

# ── C10: reconciliation: comparing two lists needs a Code step; writes one row per break; one summary email
def recon(net, led):
    return {"$api:network.settlements": http({"rows": [{"reference": r, "amount": a} for r, a in net]}),
            "$api:ledger.entries": http({"entries": [{"reference": r, "amount": a} for r, a in led]}),
            "$op:DataTable.insert|upsert?": ok([{"id": 1}])}
BRK = "$op:DataTable.insert|upsert"
case("card_settlement_recon",
     "Every day at 6am, get yesterday's card settlement report from the card network and our ledger's card entries for yesterday, and "
     "match them by reference. A reference in the report but not in our ledger is a 'missing' break; one in both with different amounts is "
     "an 'amount_mismatch'; one in our ledger but not in the report is 'unsettled'. Add one row per break to the recon_breaks table "
     "(reference, network_amount, ledger_amount, kind; leave an amount empty when that side doesn't have it). Then email finance ops how "
     "many breaks of each kind there were. If there are no breaks, send nothing.",
     ["network.yaml", "ledger.yaml"],
     {"trigger": "Cron", "must_have_types": ["Code", "DataTable", "SendEmail"], "must_call": ["network.settlements", "ledger.entries"], "max_nodes": 12},
     [{"name": "Everything matches: no rows, no email", "input": {"items": [{}]}, "mocks": recon([("R1", 100), ("R2", 50)], [("R2", 50), ("R1", 100)]),
       "expect": [st(BRK + "?", "not_run"), st("$all:SendEmail", "not_run")]},
      {"name": "Missing in our ledger", "input": {"items": [{}]}, "mocks": recon([("R1", 100), ("R2", 50)], [("R1", 100)]),
       "expect": [req(BRK, "params.data.reference", "eq", "R2"), req(BRK, "params.data.kind", "eq", "missing"), calls("$all:SendEmail", 1),
                  req("$all:SendEmail", "params.toEmail", "eq", "finops@bank.test")]},
      {"name": "Amount mismatch keeps both amounts", "input": {"items": [{}]}, "mocks": recon([("R1", 100)], [("R1", 90)]),
       "expect": [req(BRK, "params.data.kind", "eq", "amount_mismatch"), req(BRK, "params.data.network_amount", "eq", 100),
                  req(BRK, "params.data.ledger_amount", "eq", 90)]},
      {"name": "Unsettled ledger entry", "input": {"items": [{}]}, "mocks": recon([("R1", 100)], [("R1", 100), ("R9", 70)]),
       "expect": [req(BRK, "params.data.reference", "eq", "R9"), req(BRK, "params.data.kind", "eq", "unsettled")]},
      {"name": "Three kinds at once: three rows, one email", "input": {"items": [{}]},
       "mocks": recon([("R1", 100), ("R2", 50), ("R3", 10)], [("R1", 90), ("R3", 10), ("R8", 5)]),
       "expect": [calls(BRK, 3), calls("$all:SendEmail", 1), req(BRK, "params.data.kind", "eq", "missing"),
                  req(BRK, "params.data.kind", "eq", "amount_mismatch"), req(BRK, "params.data.kind", "eq", "unsettled")]}],
     tables={"recon_breaks": [{"name": "reference", "type": "string"}, {"name": "network_amount", "type": "number"},
                              {"name": "ledger_amount", "type": "number"}, {"name": "kind", "type": "string"}]})

for cid in ["address_change_reuse", "wire_settlement_tracker", "atm_limit_increase", "card_settlement_recon"]:
    c = json.loads((OUT / "eval" / f"{cid}.json").read_text()); c["variables"] = VARS; c["subworkflows"] = SUBS
    (OUT / "eval" / f"{cid}.json").write_text(json.dumps(c, indent=1))
