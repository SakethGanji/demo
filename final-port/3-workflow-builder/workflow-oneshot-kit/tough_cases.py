"""Writes the tough suite: tough/apis/*.yaml and tough/eval/*.json. Approvals (escalation, form input, four-eyes, auto-reject)
and database calls (read-only queries against core banking, atomic writes to the ops database).

Extra bindings (wf_build): $pg:<table>[!<other>] Postgres nodes whose SQL names the table (and not <other>); $pgw:<table> the ones
that write it; $has:<Type>:<text> nodes of that type whose parameters mention the text. Postgres requests also carry `sql`
(all SQL, lower-case) and `values` (every parameter value), so a test can check a transaction without caring about statement order.
"""
import json
from pathlib import Path

import complex_cases as cc
from complex_cases import obj, http, st, req, calls, hook, child

cc.OUT = Path(__file__).parent / "tough"
(cc.OUT / "apis").mkdir(parents=True, exist_ok=True)
(cc.OUT / "eval").mkdir(parents=True, exist_ok=True)
api, case = cc.api, cc.case

CORE = {"writable": False, "description": "read replica of core banking", "tables": {
    "customers": [("customer_id", "text"), ("full_name", "text"), ("email", "text"), ("segment", "text")],
    "accounts": [("account_id", "text"), ("customer_id", "text"), ("product", "text"), ("balance", "numeric"), ("status", "text")],
    "loans": [("loan_id", "text"), ("customer_id", "text"), ("outstanding", "numeric"), ("days_past_due", "integer"),
              ("officer_email", "text"), ("status", "text")],
    "cards": [("card_id", "text"), ("customer_id", "text"), ("daily_limit", "numeric"), ("status", "text")]}}
OPS = {"writable": True, "description": "our operations database", "tables": {
    "limit_changes": [("id", "serial"), ("card_id", "text"), ("old_limit", "numeric"), ("new_limit", "numeric"), ("reason", "text"),
                      ("created_at", "timestamptz default now()")],
    "audit_log": [("id", "serial"), ("entity", "text"), ("entity_id", "text"), ("action", "text"), ("detail", "text"),
                  ("created_at", "timestamptz default now()")],
    "collection_actions": [("id", "serial"), ("loan_id", "text"), ("stage", "text"), ("created_at", "timestamptz default now()")],
    "write_offs": [("id", "serial"), ("loan_id", "text"), ("amount", "numeric"), ("approver_comment", "text"),
                   ("created_at", "timestamptz default now()")],
    "fee_refunds": [("id", "serial"), ("account_id", "text"), ("amount", "numeric"), ("approved_by_manager", "boolean"),
                    ("created_at", "timestamptz default now()")],
    "account_closures": [("id", "serial"), ("account_id", "text"), ("customer_id", "text"), ("created_at", "timestamptz default now()")]}}
DBS = {"core_banking": CORE, "ops_db": OPS}
VARS = {"CREDIT_OFFICER_EMAIL": {"value": "credit-officers@bank.test"}, "HEAD_OF_CREDIT_EMAIL": {"value": "head-credit@bank.test"},
        "COLLECTIONS_LEAD_EMAIL": {"value": "collections-lead@bank.test"}, "OPS_MANAGER_EMAIL": {"value": "ops-manager@bank.test"},
        "CREDIT_COMMITTEE_EMAIL": {"value": "credit-committee@bank.test"}, "BRANCH_MANAGER_EMAIL": {"value": "branch-manager@bank.test"},
        "AUTO_REFUND_LIMIT": {"value": "50", "description": "largest fee refunded without a manager"},
        "CORE_API_KEY": {"value": "sk-core-999", "secret": True}}
NOTIFY = {"id": "wf_notify_customer", "name": "Notify customer", "description": "Sends a customer a message on their preferred channel and logs it.",
          "inputs": [{"name": "customer_id", "type": "string", "required": True}, {"name": "message", "type": "string", "required": True}],
          "output_fields": ["sent", "channel"],
          "definition": child([("customer_id", "string"), ("message", "string")], [("sent", "{{ true }}"), ("channel", "sms")])}


def rows(*rs):
    return {"items": [{"rows": list(rs), "rowCount": len(rs)}]}


OK_TX = {"items": [{"success": True}]}


def ok(items):
    return {"items": items}


def decide(decision, comment="", form=None):
    return {"port": decision, "approval": {"decision": decision, "comment": comment, "form_data": form or {}, "decided_by": "test"}}


def tough(id_, request, apis, expect, tests):
    case(id_, request, apis, expect, tests)
    c = json.loads((cc.OUT / "eval" / f"{id_}.json").read_text())
    c.update(variables=VARS, subworkflows=[NOTIFY], databases=DBS)
    (cc.OUT / "eval" / f"{id_}.json").write_text(json.dumps(c, indent=1))


api("cards", [("setLimit", "post", "/cards/{card_id}/limit", "Set a card's daily limit", obj(daily_limit="number"),
               obj(card_id="string", daily_limit="number"))])
api("collections", [("openCase", "post", "/collection-cases", "Hand a loan to the collections agency", obj(loan_id="string", outstanding="number"),
                     obj(case_id="string"))])
api("corebank", [
    ("refundFee", "post", "/accounts/{account_id}/fee-refunds", "Refund a fee to an account", obj(amount="number"), obj(refund_id="string")),
    ("closeAccount", "post", "/accounts/{account_id}/close", "Close an account", None, obj(account_id="string", status="string")),
])

RESP = "$all:Respond"

# ── T1: card limit change: DB read, form input, escalation on timeout, atomic write + audit ─────────────────────────────
CARD = "$pg:cards"
OFFICER, HEAD = "$has:Approval:CREDIT_OFFICER_EMAIL", "$has:Approval:HEAD_OF_CREDIT_EMAIL"
LIM = "$pgw:limit_changes"
card = rows({"card_id": "C9", "customer_id": "CU1", "daily_limit": "2000.00", "status": "active"})
limit_hook = lambda amt: hook(card_id="C9", requested_limit=amt, reason="travel")  # noqa: E731
tough("card_limit_change",
      "Branch staff ask for a card's daily limit to change by calling a webhook with card_id, requested_limit and reason. Look up the "
      "card's current daily limit in core banking. If the card isn't there, reply 404 with {status: 'not_found'} and do nothing else. "
      "A decrease, or the same limit, is applied straight away. An increase needs a credit officer: send the task to CREDIT_OFFICER_EMAIL, "
      "show them the card, the current and requested limit and the reason, let them enter the limit they approve as approved_limit (it "
      "can be lower than asked), and make them give a reason when they reject. If the credit officer hasn't decided within 4 hours, "
      "escalate the same task to the head of credit (HEAD_OF_CREDIT_EMAIL), due within 1 day; if that runs out too, it is rejected. "
      "When a change goes ahead, set the new limit through the cards API (for an increase, the limit the approver entered), then record "
      "it in ops_db in one transaction: a limit_changes row (card, old limit, new limit, reason) and an audit_log row (entity 'card', the "
      "card id, action 'limit_change'). Reply 200 with {status: 'applied', limit: <new limit>} or {status: 'rejected', reason: <the "
      "approver's reason, empty if nobody decided>}.",
      ["cards.yaml"],
      {"trigger": "Webhook", "must_have_types": ["Postgres", "Approval", "Respond"], "must_call": ["cards.setLimit"], "max_nodes": 24,
       "min_types": {"Approval": 2}},
      [{"name": "Decrease applies at once, change and audit in one transaction", "input": limit_hook(1500),
        "mocks": {CARD: card, "$api:cards.setLimit": http({"card_id": "C9", "daily_limit": 1500}), LIM: OK_TX, "$all:Approval?": decide("approved")},
        "expect": [st("$all:Approval?", "not_run"), req("$api:cards.setLimit", "body.daily_limit", "eq", 1500),
                   req("$api:cards.setLimit", "url", "contains", "C9"), calls(LIM, 1), req(LIM, "sql", "contains", "audit_log"),
                   req(LIM, "values", "contains", 1500), req(LIM, "values", "contains", "C9"),
                   req(RESP, "body.status", "eq", "applied"), req(RESP, "body.limit", "eq", 1500)]},
       {"name": "Same limit is not an increase", "input": limit_hook(2000),
        "mocks": {CARD: card, "$api:cards.setLimit": http({"card_id": "C9", "daily_limit": 2000}), LIM: OK_TX, "$all:Approval?": decide("approved")},
        "expect": [st("$all:Approval?", "not_run"), req(RESP, "body.status", "eq", "applied")]},
       {"name": "Officer approves a lower limit than asked; old limit recorded", "input": limit_hook(5000),
        "mocks": {CARD: card, OFFICER: decide("approved", form={"approved_limit": 4000}), HEAD: decide("approved", form={"approved_limit": 9999}),
                  "$api:cards.setLimit": http({"card_id": "C9", "daily_limit": 4000}), LIM: OK_TX},
        "expect": [calls(OFFICER, 1), st(HEAD, "not_run"), req(OFFICER, "params.due_in", "one_of", ["4h", "240m"]),
                   req(OFFICER, "params.notify_email", "eq", "credit-officers@bank.test"),
                   req("$api:cards.setLimit", "body.daily_limit", "eq", 4000), req(LIM, "values", "contains", 4000),
                   req(LIM, "text", "contains", "2000"), req(RESP, "body.limit", "eq", 4000)]},
       {"name": "Officer rejects: nothing changes, reason replied", "input": limit_hook(5000),
        "mocks": {CARD: card, OFFICER: decide("rejected", "income too low"), HEAD: decide("approved", form={"approved_limit": 9999}),
                  "$api:cards.setLimit": http({}), LIM: OK_TX},
        "expect": [st("$api:cards.setLimit", "not_run"), st(LIM, "not_run"), st(HEAD, "not_run"),
                   req(OFFICER, "params.require_comment_on_reject", "eq", True),
                   req(RESP, "body.status", "eq", "rejected"), req(RESP, "body.reason", "eq", "income too low")]},
       {"name": "Officer times out, head of credit approves", "input": limit_hook(5000),
        "mocks": {CARD: card, OFFICER: decide("timed_out"), HEAD: decide("approved", form={"approved_limit": 3000}),
                  "$api:cards.setLimit": http({"card_id": "C9", "daily_limit": 3000}), LIM: OK_TX},
        "expect": [calls(HEAD, 1), req(HEAD, "params.due_in", "one_of", ["1d", "24h"]),
                   req(HEAD, "params.notify_email", "eq", "head-credit@bank.test"),
                   req("$api:cards.setLimit", "body.daily_limit", "eq", 3000), req(RESP, "body.status", "eq", "applied")]},
       {"name": "Both time out: rejected, nothing changes", "input": limit_hook(5000),
        "mocks": {CARD: card, OFFICER: decide("timed_out"), HEAD: decide("timed_out"), "$api:cards.setLimit": http({}), LIM: OK_TX},
        "expect": [st("$api:cards.setLimit", "not_run"), st(LIM, "not_run"), req(RESP, "body.status", "eq", "rejected")]},
       {"name": "Unknown card: 404, nothing else", "input": limit_hook(5000),
        "mocks": {CARD: rows(), "$all:Approval?": decide("approved"), "$api:cards.setLimit": http({}), LIM: OK_TX},
        "expect": [req(RESP, "statusCode", "eq", 404), req(RESP, "body.status", "eq", "not_found"), st("$all:Approval?", "not_run"),
                   st("$api:cards.setLimit", "not_run")]}])

# ── T2: overdue collections: scheduled DB read, bands, per-loan atomic writes, digest with counts ──────────────────────
LOANS = "$pg:loans"
CUST = "$pg:customers!loans?"  # a separate per-loan customer lookup, if the model doesn't join
ACT = "$pgw:collection_actions"


def loan(i, dpd):
    return {"loan_id": f"L{i}", "customer_id": f"cu{i}", "outstanding": f"{1000 * i}.00", "days_past_due": dpd,
            "officer_email": f"officer{i}@bank.test", "status": "active", "email": f"cu{i}@mail.test", "customer_email": f"cu{i}@mail.test",
            "full_name": f"Customer {i}"}


def coll_mocks(*loans):
    return {LOANS: rows(*loans), CUST: ok([{"rows": [{"customer_id": "{{ $json.customer_id }}", "email": "{{ $json.customer_id }}@mail.test",
                                                      "full_name": "Customer"}], "rowCount": 1}]),
            ACT: ok([{"success": True}]), "$api:collections.openCase": http({"case_id": "K1"}), "$all:SendEmail": ok([{"success": True}])}


tough("overdue_collections",
      "Every weekday at 7am, find the active loans in core banking that are 30 or more days past due, with each customer's email. For "
      "each loan: 30 to 59 days past due, email the customer a payment reminder (stage 'reminder'); 60 to 89, email the customer a final "
      "notice and also email the loan's officer (officer_email) (stage 'final_notice'); 90 or more, hand it to the collections agency "
      "with its outstanding amount (stage 'collections'). Record each loan's action in ops_db collection_actions (loan id and stage), "
      "one row per loan. Finally, email COLLECTIONS_LEAD_EMAIL one summary saying how many loans went to each stage. If no loans are "
      "overdue, send nothing at all.",
      ["collections.yaml"],
      {"trigger": "Cron", "must_have_types": ["Postgres", "SendEmail"], "must_call": ["collections.openCase"], "max_nodes": 24},
      [{"name": "One loan per band", "input": {"items": [{}]}, "mocks": coll_mocks(loan(1, 95), loan(2, 30), loan(3, 61)),
        "expect": [calls("$api:collections.openCase", 1), req("$api:collections.openCase", "body.loan_id", "eq", "L1"),
                   req("$api:collections.openCase", "body.outstanding", "eq", 1000),
                   calls(ACT, 3), req(ACT, "text", "contains", "reminder"), req(ACT, "text", "contains", "final_notice"),
                   req(ACT, "text", "contains", "collections"), req(ACT, "text", "contains", "l2"),
                   req("$all:SendEmail", "params.toEmail", "eq", "cu2@mail.test"), req("$all:SendEmail", "params.toEmail", "eq", "cu3@mail.test"),
                   req("$all:SendEmail", "params.toEmail", "eq", "officer3@bank.test"),
                   calls("$all:SendEmail", 4), req("$all:SendEmail", "params.toEmail", "eq", "collections-lead@bank.test")]},
       {"name": "Boundaries 59/60 and 89/90", "input": {"items": [{}]}, "mocks": coll_mocks(loan(1, 59), loan(2, 60), loan(3, 89), loan(4, 90)),
        "expect": [calls("$api:collections.openCase", 1), req("$api:collections.openCase", "body.loan_id", "eq", "L4"), calls(ACT, 4),
                   req("$all:SendEmail", "params.toEmail", "eq", "officer2@bank.test"), req("$all:SendEmail", "params.toEmail", "eq", "officer3@bank.test"),
                   calls("$all:SendEmail", 6)]},
       {"name": "Only collections: no customer emails, summary still sent", "input": {"items": [{}]}, "mocks": coll_mocks(loan(5, 120), loan(6, 200)),
        "expect": [calls("$api:collections.openCase", 2), calls(ACT, 2), calls("$all:SendEmail", 1),
                   req("$all:SendEmail", "params.toEmail", "eq", "collections-lead@bank.test")]},
       {"name": "Nothing overdue: nothing at all", "input": {"items": [{}]}, "mocks": coll_mocks(),
        "expect": [st("$api:collections.openCase", "not_run"), st(ACT, "not_run"), st("$all:SendEmail", "not_run")]},
       {"name": "The query filters on status and days past due", "input": {"items": [{}]},
        "mocks": coll_mocks(loan(1, 31)),
        "expect": [req(LOANS, "sql", "contains", "days_past_due"), req(LOANS, "text", "contains", "active")]}])

# ── T3: loan write-off: DB checks, approval tiers, four-eyes, auto-reject on deadline, atomic write ─────────────────────
WO = "$pgw:write_offs"
loanrow = lambda status="defaulted", out="9000.00": rows({"loan_id": "L7", "customer_id": "CU7", "outstanding": out,  # noqa: E731
                                                          "days_past_due": 200, "officer_email": "o@bank.test", "status": status})
wo_hook = lambda amt: hook(loan_id="L7", amount=amt, reason="uncollectable")  # noqa: E731
# the second of two chained approvals sees the first one's `approval` on its item: approve the first, reject the second
FIRST_YES_SECOND_NO = {"approval": {"decision": "{{ 'rejected' if 'approval' in $json else 'approved' }}",
                                    "comment": "{{ 'duplicate' if 'approval' in $json else 'fine' }}", "form_data": {}, "decided_by": "test"}}
tough("loan_write_off",
      "Collections staff submit loan write-offs by webhook with loan_id, amount and reason. Check the loan in core banking: if it isn't "
      "there, isn't in status 'defaulted', or the amount is more than its outstanding balance, reply 422 with {status: 'refused', reason} "
      "where reason is 'loan not found', 'loan not defaulted' or 'amount exceeds outstanding', and stop. Up to 5,000 needs one approval "
      "from the ops manager (OPS_MANAGER_EMAIL). Over 5,000 and up to 50,000 needs two different credit committee members, one after the "
      "other (four eyes, both tasks sent to CREDIT_COMMITTEE_EMAIL). Over 50,000 is refused (422, reason 'needs board approval'): the board "
      "handles those offline. Every approval is due within 2 days and is rejected automatically if nobody decides; rejecting needs a "
      "reason. When approved, write it in ops_db in one transaction: a write_offs row (loan id, amount, the last approver's comment) and an "
      "audit_log row (entity 'loan', the loan id, action 'write_off'). Reply 200 {status: 'written_off'}, or 200 {status: 'rejected', "
      "reason: <the approver's comment>}.",
      [],
      {"trigger": "Webhook", "must_have_types": ["Postgres", "Approval", "Respond"], "max_nodes": 24, "min_types": {"Approval": 3}},
      [{"name": "5,000 needs only the ops manager", "input": wo_hook(5000),
        "mocks": {"$pg:loans": loanrow(), "$all:Approval": decide("approved", "ok"), WO: OK_TX},
        "expect": [calls("$all:Approval", 1), req("$all:Approval", "params.notify_email", "eq", "ops-manager@bank.test"),
                   req("$all:Approval", "params.due_in", "one_of", ["2d", "48h"]), req("$all:Approval", "params.on_timeout", "eq", "reject"),
                   calls(WO, 1), req(WO, "sql", "contains", "audit_log"), req(WO, "values", "contains", "L7"), req(WO, "values", "contains", 5000),
                   req(RESP, "body.status", "eq", "written_off")]},
       {"name": "5,001 needs two committee members, both four-eyes", "input": wo_hook(5001),
        "mocks": {"$pg:loans": loanrow(), "$all:Approval": decide("approved", "agreed"), WO: OK_TX},
        "expect": [calls("$all:Approval", 2), req("$all:Approval", "params.four_eyes", "eq", True, item="all"),
                   req("$all:Approval", "params.notify_email", "eq", "credit-committee@bank.test", item="all"),
                   req("$all:Approval", "params.on_timeout", "eq", "reject", item="all"),
                   calls(WO, 1), req(WO, "values", "contains", "agreed"), req(RESP, "body.status", "eq", "written_off")]},
       {"name": "Second committee member rejects: nothing written", "input": wo_hook(20000),
        "mocks": {"$pg:loans": loanrow("defaulted", "30000"), "$all:Approval": FIRST_YES_SECOND_NO, WO: OK_TX},
        "expect": [calls("$all:Approval", 2), st(WO, "not_run"), req(RESP, "body.status", "eq", "rejected"),
                   req(RESP, "body.reason", "eq", "duplicate")]},
       {"name": "Over 50,000 goes to the board", "input": wo_hook(50001),
        "mocks": {"$pg:loans": loanrow("defaulted", "90000"), "$all:Approval?": decide("approved"), WO: OK_TX},
        "expect": [st("$all:Approval?", "not_run"), st(WO, "not_run"), req(RESP, "statusCode", "eq", 422),
                   req(RESP, "body.reason", "eq", "needs board approval")]},
       {"name": "Not defaulted is refused", "input": wo_hook(100),
        "mocks": {"$pg:loans": loanrow("active"), "$all:Approval?": decide("approved"), WO: OK_TX},
        "expect": [st("$all:Approval?", "not_run"), req(RESP, "statusCode", "eq", 422), req(RESP, "body.reason", "eq", "loan not defaulted")]},
       {"name": "More than outstanding is refused (text numbers compared as numbers)", "input": wo_hook(1000),
        "mocks": {"$pg:loans": loanrow("defaulted", "999.50"), "$all:Approval?": decide("approved"), WO: OK_TX},
        "expect": [st("$all:Approval?", "not_run"), req(RESP, "body.reason", "eq", "amount exceeds outstanding")]},
       {"name": "Unknown loan is refused", "input": wo_hook(100),
        "mocks": {"$pg:loans": rows(), "$all:Approval?": decide("approved"), WO: OK_TX},
        "expect": [st("$all:Approval?", "not_run"), req(RESP, "statusCode", "eq", 422), req(RESP, "body.reason", "eq", "loan not found")]}])

# ── T4: fee refund: DB read across two tables, variable threshold, select + number form, sub-workflow ───────────────────
ACC = "$pg:accounts"
SEG = "$pg:customers!accounts?"
FR = "$pgw:fee_refunds"
NOTE = "$wf:wf_notify_customer"


def acc(segment):
    r = {"account_id": "A5", "customer_id": "CU5", "product": "current", "balance": "120.00", "status": "open", "segment": segment}
    return {ACC: rows(r), SEG: rows({"customer_id": "CU5", "segment": segment, "full_name": "Kim", "email": "kim@mail.test"})}


def fee_mocks(segment, decision=None):
    m = {**acc(segment), "$api:corebank.refundFee": http({"refund_id": "R1"}), FR: OK_TX, NOTE: ok([{"sent": True, "channel": "sms"}]),
         "$all:Approval?": decision or decide("approved", form={"refund_type": "full"})}
    return m


fee = lambda amt: {"items": [{"account_id": "A5", "fee_amount": amt, "reason": "overdraft fee in error"}]}  # noqa: E731
tough("fee_refund",
      "Branch staff log fee refund requests on a form with account_id, fee_amount and reason. Look up the account and its customer's "
      "segment in core banking. If the account isn't found, show 'Account not found' and stop. A premium customer's fee up to "
      "AUTO_REFUND_LIMIT is refunded in full automatically. Everything else goes to the branch manager (BRANCH_MANAGER_EMAIL), who "
      "chooses refund_type (full or partial) and, for partial, enters refund_amount. Refund through core banking's refundFee: the full fee, "
      "or the manager's refund_amount for a partial refund. Then record it in ops_db fee_refunds (account id, amount refunded, "
      "approved_by_manager true or false) and tell the customer with the Notify customer sub-workflow: 'We have refunded <amount> to your "
      "account.' If the manager rejects, record nothing and notify the customer 'Your fee refund request was declined.' Show the staff "
      "member 'Refunded <amount>' or 'Declined'.",
      ["corebank.yaml"],
      {"trigger": "FormTrigger", "must_have_types": ["Postgres", "Approval", "ExecuteWorkflow"], "must_call": ["corebank.refundFee"],
       "interface_kind": "form", "max_nodes": 22},
      [{"name": "Premium at the limit: automatic full refund", "input": fee(50), "mocks": fee_mocks("premium"),
        "expect": [st("$all:Approval?", "not_run"), req("$api:corebank.refundFee", "body.amount", "eq", 50),
                   req("$api:corebank.refundFee", "url", "contains", "A5"), calls(FR, 1), req(FR, "values", "contains", 50),
                   req(FR, "values", "contains", False), req(NOTE, "params.inputs.message", "contains", "50"),
                   req(NOTE, "params.inputs.customer_id", "eq", "CU5")]},
       {"name": "Premium just over the limit needs the manager", "input": fee(50.01), "mocks": fee_mocks("premium"),
        "expect": [calls("$all:Approval?", 1), req("$all:Approval?", "params.notify_email", "eq", "branch-manager@bank.test"),
                   req("$api:corebank.refundFee", "body.amount", "eq", 50.01), req(FR, "values", "contains", True)]},
       {"name": "Standard customer, manager refunds part", "input": fee(20),
        "mocks": fee_mocks("standard", decide("approved", form={"refund_type": "partial", "refund_amount": 10})),
        "expect": [calls("$all:Approval?", 1), req("$api:corebank.refundFee", "body.amount", "eq", 10), req(FR, "values", "contains", 10),
                   req(NOTE, "params.inputs.message", "contains", "10")]},
       {"name": "Manager rejects: no refund, no record, customer told", "input": fee(20),
        "mocks": fee_mocks("standard", decide("rejected", "not our error")),
        "expect": [st("$api:corebank.refundFee", "not_run"), st(FR, "not_run"), req(NOTE, "params.inputs.message", "contains", "declined")]},
       {"name": "Unknown account: nothing happens", "input": fee(20),
        "mocks": {**fee_mocks("standard"), ACC: rows()},
        "expect": [st("$all:Approval?", "not_run"), st("$api:corebank.refundFee", "not_run"), st(NOTE, "not_run")]}])

# ── T5: account closure: DB checks across tables, blockers list, approval, per-account close + audit, interesting row not first
ACCS = "$pg:accounts!loans"
LNS = "$pg:loans!accounts"
CLS = "$pgw:account_closures"


def closure_mocks(accounts, loans=(), decision=None):
    return {ACCS: rows(*[{"account_id": a, "customer_id": "CU8", "product": "savings", "balance": b, "status": s} for a, b, s in accounts]),
            LNS: rows(*[{"loan_id": l, "customer_id": "CU8", "outstanding": o, "days_past_due": 0, "officer_email": "o@bank.test",
                         "status": "active"} for l, o in loans]),
            "$all:Approval?": decision or decide("approved"), "$api:corebank.closeAccount": http({"account_id": "{{ $json.account_id }}", "status": "closed"}),
            CLS: OK_TX}


tough("customer_exit",
      "When a customer leaves the bank, staff call a webhook with customer_id. In core banking, read the customer's open accounts and "
      "their active loans. If any open account has a balance other than zero, or the customer has any active loan, reply 409 with "
      "{status: 'blocked', blockers: [...]} listing every blocking account id and loan id, and stop. Otherwise the ops manager "
      "(OPS_MANAGER_EMAIL) approves the exit, seeing the customer id and the list of accounts. Once approved, close every open account "
      "through core banking, and for each one record in ops_db, in one transaction per account, an account_closures row (account id, "
      "customer id) and an audit_log row (entity 'account', the account id, action 'closed'). Reply 200 {status: 'closed', "
      "accounts: <how many were closed>}. If the manager rejects, reply 200 {status: 'rejected'} and close nothing.",
      ["corebank.yaml"],
      {"trigger": "Webhook", "must_have_types": ["Postgres", "Approval", "Respond"], "must_call": ["corebank.closeAccount"], "max_nodes": 22},
      [{"name": "All zero, no loans: every account closed and recorded", "input": hook(customer_id="CU8"),
        "mocks": closure_mocks([("A1", "0.00", "open"), ("A2", "0", "open"), ("A3", "0.00", "open")]),
        "expect": [calls("$all:Approval?", 1), calls("$api:corebank.closeAccount", 3), req("$api:corebank.closeAccount", "url", "contains", "A2"),
                   req("$api:corebank.closeAccount", "url", "contains", "A3"), calls(CLS, 3), req(CLS, "values", "contains", "A3"),
                   req(CLS, "sql", "contains", "audit_log", item="all"), req(CLS, "values", "contains", "CU8", item="all"),
                   req(RESP, "body.status", "eq", "closed"), req(RESP, "body.accounts", "eq", 3), calls(RESP, 1)]},
       {"name": "A non-zero balance on a later account blocks", "input": hook(customer_id="CU8"),
        "mocks": closure_mocks([("A1", "0.00", "open"), ("A2", "12.40", "open")]),
        "expect": [st("$all:Approval?", "not_run"), st("$api:corebank.closeAccount", "not_run"), req(RESP, "statusCode", "eq", 409),
                   req(RESP, "body.blockers", "contains", "A2"), calls(RESP, 1)]},
       {"name": "Negative balance and an active loan are both listed", "input": hook(customer_id="CU8"),
        "mocks": closure_mocks([("A1", "-5.00", "open"), ("A2", "0", "open")], [("L3", "800.00")]),
        "expect": [st("$all:Approval?", "not_run"), req(RESP, "body.blockers", "contains", "A1"), req(RESP, "body.blockers", "contains", "L3"),
                   req(RESP, "statusCode", "eq", 409)]},
       {"name": "Manager rejects: nothing closed", "input": hook(customer_id="CU8"),
        "mocks": closure_mocks([("A1", "0.00", "open")], decision=decide("rejected", "pending complaint")),
        "expect": [st("$api:corebank.closeAccount", "not_run"), st(CLS, "not_run"), req(RESP, "body.status", "eq", "rejected")]},
       {"name": "Queries take the customer id as a parameter", "input": hook(customer_id="CU8"),
        "mocks": closure_mocks([("A1", "0.00", "open")]),
        "expect": [req(ACCS, "values", "contains", "CU8"), req(LNS, "values", "contains", "CU8")]}])

print("wrote", sorted(p.name for p in (cc.OUT / "eval").glob("*.json")))
