"""The studio chat, end to end, on the live :8020 engine: build and edit workflows by talking to POST /api/ai/chat (the
one-shot builder + changes.json patches, exactly what the studio's AI chat calls), Keep each proposal, then RUN the result
against real Postgres, the mock bank API (:9100) and Mailpit, reusing drive.py's scenarios and helpers.

    python3 live_chat.py [step ...]      steps: wire_fraud_email  wire_country_block  build_delinquency  edit_delinquency
                                         -> live/results-chat.json  (chat transcripts and proposals in live/chat-log/)
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import drive as D

HERE = Path(__file__).parent
LOG = HERE / "chat-log"
LOG.mkdir(exist_ok=True)
ACTOR = "live-test@bank.test"
STATE = HERE / "chat-state.json"          # ids of workflows the chat built, so later steps can edit them


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def chat(step, message, workflow_id=None, mode_hint=None):
    """POST /api/ai/chat the way the studio does; returns (events, proposal view)."""
    body = {"message": message, "project_id": D.SETUP["project"], "workflow_id": workflow_id, "mode_hint": mode_hint or "auto",
            "conversation_history": []}
    req = urllib.request.Request(f"{D.E}/ai/chat", method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Actor-Name": ACTOR})
    t0, events = time.time(), []
    with urllib.request.urlopen(req, timeout=1200) as r:
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except ValueError:
                    pass
    secs = round(time.time() - t0, 1)
    prop = next((e for e in events if e.get("type") == "proposal"), None)
    err = next((e for e in events if e.get("type") == "error"), None)
    view = D.http("GET", f"{D.E}/ai/proposals/{prop['proposal_id']}") if prop else None
    (LOG / f"{step}.json").write_text(json.dumps({"message": message, "seconds": secs, "events": events, "view": view}, indent=1, default=str))
    rounds = len(((view or {}).get("report") or {}).get("rounds") or [])
    print(f"  chat {step}: {secs}s, {rounds} model call(s) -> {prop and prop['status']} passed={prop and prop['report']['passed']}"
          + (f"  ERROR {err}" if err else "") + (f"\n    {prop['summary']}" if prop else ""), flush=True)
    if not prop or prop["status"] != "ready":
        raise RuntimeError(f"chat did not produce a ready proposal: {err or prop}")
    return prop, view, secs, rounds


def keep(prop):
    k = D.http("POST", f"{D.E}/ai/proposals/{prop['proposal_id']}/keep", {"actor": ACTOR, "accept_failing": not prop["report"]["passed"]})
    return k["workflow_id"]


def draft(wid):
    return D.http("GET", f"{D.E}/workflows/{wid}")


# ── the wire workflow, edited twice by chat ───────────────────────────────────────────────────────────────────

WIRE_ALL = ["wire_small", "wire_confirmed_clean", "wire_fraud", "wire_no_callback", "wire_sanctions_true_match_sar",
            "wire_sanctions_mlro_silent", "wire_million_treasury", "wire_million_treasury_rejects"]


def run_scenarios(names, extra=None):
    out = []
    for n in names:
        r = D.SCENARIOS[n]()
        if extra:
            extra(n, r)
        out.append(r)
    return out


def wire_fraud_email():
    prop, *_ = chat("wire_fraud_email", "When the customer says on the callback that they did not make the wire (fraud), also email "
                    "compliance@bank.test with the wire id and the amount. Nothing else changes.", D.SETUP["workflows"]["wire_release"])
    keep(prop)

    def extra(n, r):
        got = D.to("compliance@bank.test")
        if n == "wire_fraud":
            r.check("compliance emailed about the fraud (new)", len(got) == 1 and "W1" in (got[0]["subject"] + got[0]["text"])
                    and "50" in got[0]["text"], got)
        else:
            r.check("compliance not emailed (unchanged paths)", not got, [m["subject"] for m in got])

    return run_scenarios(WIRE_ALL, extra)


def wire_country_block():
    prop, *_ = chat("wire_country_block", "Wires to beneficiary country KP or IR are blocked straight away, before the callback: block the "
                    "wire via the wires API, write an audit row in ops_db (entity 'wire', the wire id, action 'blocked_country', detail = "
                    "the country) and email compliance@bank.test. No callback task, no screening for those.", D.SETUP["workflows"]["wire_release"])
    keep(prop)

    def kp():
        D.reset({})
        r = D.Run("wire_country_KP", "wire_release")
        r.start({"wire_id": "W1", "account_id": "A1", "customer_id": "C2", "amount": 50000, "beneficiary_name": "X", "beneficiary_country": "KP"})
        r.finish()
        D.wire_checks(r, "blocked_country", ["POST /wires/W1/block"], emails=["compliance@bank.test"])
        r.check("no screening call", "POST /screen" not in D.paths(), D.paths())
        r.check("no callback task / email to payops", not D.to("payops@bank.test"), None)
        audit = D.sql("ops_db", "SELECT detail FROM audit_log")
        r.check("audit detail is the country", audit and audit[0][0] == "KP", audit)
        return r

    D.SCENARIOS["wire_country_KP"] = kp
    return run_scenarios(WIRE_ALL + ["wire_country_KP"])


# ── a new workflow built by chat, then edited by chat ────────────────────────────────────────────────────────

def delinquency_run(name, wid, decisions, expect_loans, escalate):
    D.reset({})
    D.SETUP["workflows"]["_chat_delinquency"] = wid
    r = D.Run(name, "_chat_delinquency")
    r.start({})
    for node_hint, what in decisions:
        t = next(t for t in _open_tasks(r, wait=30) if node_hint.lower() in t["node_name"].lower())
        D.http("POST", f"{D.E}/tasks/{t['id']}/decide", {"decision": what, "decided_by": ACTOR, "form_data": {}},
               {"Idempotency-Key": "chat-" + t["id"]})
        r.log.append(f"{t['node_name']}: {what}")
    r.finish()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    audit = D.sql("ops_db", "SELECT entity, entity_id, action FROM audit_log ORDER BY id")
    sent = {a[1] for a in audit if a[2] == "reminder_sent"}
    r.check(f"reminder audit rows = {sorted(expect_loans)}", sent == set(expect_loans), audit)
    r.check("entity is 'loan'", all(a[0] == "loan" for a in audit), audit)
    emails = {"L1": "ann@mail.test", "L2": "cy@mail.test"}
    for loan in expect_loans:
        got = D.to(emails[loan])
        r.check(f"reminder emailed to {emails[loan]} naming {loan}", any(loan in m["subject"] + m["text"] for m in got), [m["subject"] for m in got])
    for loan in set(emails) - set(expect_loans):
        r.check(f"no reminder to {emails[loan]}", not D.to(emails[loan]), None)
    esc = [a for a in audit if a[2] == "escalated"]
    r.check(f"escalated = {escalate}", [a[1] for a in esc] == escalate, audit)
    # the approval task itself notifies the assignee ("Approval needed: ..."); that is not the escalation email
    esc_mail = [m for m in D.to("collections@bank.test") if not m["subject"].lower().startswith("approval needed")]
    r.check("collections emailed about the escalation only when escalated", bool(esc_mail) == bool(escalate), [m["subject"] for m in D.mails()])
    asked = [m for m in D.to("collections@bank.test") if m["subject"].lower().startswith("approval needed")]
    r.check("collections asked once per loan over 15000", len(asked) == len([x for x in expect_loans if x == "L1"]), [m["subject"] for m in asked])
    return r


def _open_tasks(r, wait=30):
    end = time.time() + wait
    while time.time() < end:
        items = [t for t in D.http("GET", f"{D.E}/tasks?status=open&limit=100")["items"] if t["execution_id"] == r.eid]
        if items:
            return items
        time.sleep(0.5)
    raise RuntimeError(f"no open task (execution {r.status()})")


DELINQ = ("Delinquency sweep, started by hand. Read the loans in core_banking that are 30 or more days past due with status "
          "'delinquent', with the customer's name and email. For each loan: email the customer a payment reminder (the subject names "
          "the loan id) and write an audit row in ops_db (entity 'loan', entity_id the loan id, action 'reminder_sent', detail the days "
          "past due). A loan with more than 15000 outstanding also needs a collections officer to approve escalating it (one task per "
          "loan, assigned to collections@bank.test, 2 days to decide): approved -> audit action 'escalated' and email "
          "collections@bank.test naming the loan; rejected or no answer -> nothing more.")


def build_delinquency():
    prop, view, *_ = chat("build_delinquency", DELINQ, mode_hint="generate")
    wid = keep(prop)
    STATE.write_text(json.dumps({**state(), "delinquency": wid}))
    # seed: L1 (C1 Ann, 18500, 45 days, delinquent) qualifies and is over 15000; L2 (C3, 10 days, current) does not
    return [delinquency_run("delinquency_approved", wid, [("", "approved")], ["L1"], ["L1"]),
            delinquency_run("delinquency_rejected", wid, [("", "rejected")], ["L1"], [])]


def edit_delinquency():
    wid = state()["delinquency"]
    prop, *_ = chat("edit_delinquency", "Lower the threshold to 7 days past due and include loans of any status.", wid)
    keep(prop)
    # now L2 (10 days, current, 9200) also gets a reminder; it is under 15000 so it needs no approval
    return [delinquency_run("delinquency_wider_approved", wid, [("", "approved")], ["L1", "L2"], ["L1"])]


STEPS = {"wire_fraud_email": wire_fraud_email, "wire_country_block": wire_country_block,
         "build_delinquency": build_delinquency, "edit_delinquency": edit_delinquency}

if __name__ == "__main__":
    picks = sys.argv[1:] or list(STEPS)
    results = json.loads((HERE / "results-chat.json").read_text()) if (HERE / "results-chat.json").exists() else {}
    for step in picks:
        print(f"== {step}", flush=True)
        t0 = time.time()
        try:
            runs = STEPS[step]()
            res = [{"scenario": r.name, "ok": all(c["ok"] for c in r.checks), "checks": r.checks, "log": r.log, "execution": r.eid} for r in runs]
            for x in res:
                print(f"  {'PASS' if x['ok'] else 'FAIL'} {x['scenario']}", flush=True)
                for c in [c for c in x["checks"] if not c["ok"]]:
                    print(f"     x {c['check']}  got: {json.dumps(c.get('got'), default=str)[:300]}", flush=True)
            results[step] = {"ok": all(x["ok"] for x in res), "seconds": round(time.time() - t0, 1), "runs": res}
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {str(e)[:600]}", flush=True)
            results[step] = {"ok": False, "error": str(e)[:600], "seconds": round(time.time() - t0, 1)}
        (HERE / "results-chat.json").write_text(json.dumps(results, indent=1, default=str))
    print(f"{sum(r['ok'] for r in results.values())}/{len(results)} steps passed")
