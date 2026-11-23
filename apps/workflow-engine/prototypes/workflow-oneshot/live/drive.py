"""Live runs of the 5 AI-built bank workflows on the isolated :8020 engine.

Everything real except the outside world: real engine, real Postgres (core_banking / iam_db / ops_db on :5440),
the bank APIs mocked by bank_api.py on :9100, email caught by Mailpit (:1025 / :8025). People decide through the
task API (POST /api/tasks/{id}/decide), exactly as the inbox UI does. Silence is the engine's own sweeper: a task's
due_at is moved into the past and the sweeper (every 30s) expires it with the workflow's on_timeout.

python3 drive.py [scenario-prefix ...]   -> live/results.json
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SETUP = json.loads((HERE / "setup.json").read_text())
E, BANK, MAIL = "http://127.0.0.1:8020/api", "http://127.0.0.1:9100", "http://127.0.0.1:8025/api/v1"


def http(method, url, body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw and raw.strip()[:1] in (b"{", b"[") else raw.decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> {e.code} {e.read().decode()[:300]}")


def sql(db, q):
    out = subprocess.run(["psql", "-h", "127.0.0.1", "-p", "5440", "-U", "bank", "-d", db, "-qtAX", "-F", "|", "-c", q],
                         env={"PGPASSWORD": "bankdemo", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True)
    return [line.split("|") for line in out.stdout.strip().splitlines() if line]


class Run:
    def __init__(self, name, wf):
        self.name, self.wf, self.checks, self.eid, self.log = name, SETUP["workflows"][wf], [], None, []

    # --- driving -------------------------------------------------------------------------------------------
    def start(self, body=None):
        r = http("POST", f"{E}/workflows/{self.wf}/run", {"environment": "dev", "input_data": body or {}})
        self.eid, self.first = r["execution_id"], r
        self.log.append(f"run -> {r['status']} {r.get('errors') or ''}")
        return r

    def task(self, node, wait=20):
        end = time.time() + wait
        while time.time() < end:
            items = http("GET", f"{E}/tasks?status=open&workflow_id={self.wf}&limit=100")["items"]
            for t in items:
                if t["execution_id"] == self.eid and t["node_name"] == node:
                    return t
            time.sleep(0.5)
        open_ = [t["node_name"] for t in http("GET", f"{E}/tasks?status=open&workflow_id={self.wf}")["items"] if t["execution_id"] == self.eid]
        raise RuntimeError(f"no open task '{node}' (open: {open_}; execution {self.status()})")

    def decide(self, node, decision, form=None, comment=""):
        t = self.task(node)
        body = {"decision": decision, "decided_by": "live-test@bank.test", "form_data": form or {}}
        if comment:
            body["comment"] = comment
        http("POST", f"{E}/tasks/{t['id']}/decide", body, {"Idempotency-Key": "live-" + t["id"]})
        self.log.append(f"{node}: {decision} {form or ''}")

    def silence(self, node):
        """Nobody answers: due_at moves into the past and the engine's sweeper expires the task."""
        t = self.task(node)
        sql("workflows_live", f"""UPDATE "workflow-app".human_tasks SET due_at = now() - interval '1 second' WHERE id = '{t["id"]}'""")
        end = time.time() + 75
        while time.time() < end:
            st = sql("workflows_live", f"""SELECT status FROM "workflow-app".human_tasks WHERE id = '{t["id"]}'""")[0][0]
            if st != "open":
                self.log.append(f"{node}: no answer -> {st} by sweeper")
                return
            time.sleep(1)
        raise RuntimeError(f"sweeper did not expire '{node}'")

    def status(self):
        return http("GET", f"{E}/executions/{self.eid}")["status"]

    def finish(self, wait=60):
        end = time.time() + wait
        while time.time() < end:
            ex = http("GET", f"{E}/executions/{self.eid}")
            if ex["status"] not in ("running", "waiting", "pending", "queued"):
                self.exec = ex
                self.log.append(f"finished: {ex['status']} {ex.get('errors') or ''}")
                return ex
            time.sleep(0.5)
        raise RuntimeError(f"execution still {self.status()} after {wait}s")

    # --- what happened -------------------------------------------------------------------------------------
    def ran(self, node):
        return node in (self.exec.get("node_data") or {})

    def check(self, label, ok, got=None):
        self.checks.append({"check": label, "ok": bool(ok), **({} if ok else {"got": got})})


def calls():
    return [(c["method"], c["path"], c["body"]) for c in http("GET", f"{BANK}/_calls")]


def paths():
    return [f"{m} {p}" for m, p, _ in calls()]


def mails():
    out = []
    for m in http("GET", f"{MAIL}/messages?limit=200")["messages"]:
        full = http("GET", f"{MAIL}/message/{m['ID']}")
        out.append({"to": [a["Address"] for a in m["To"]], "subject": m["Subject"], "text": full.get("Text", "")})
    return out


def to(addr):
    return [m for m in mails() if addr in m["to"]]


def reset(control=None):
    http("DELETE", f"{BANK}/_calls")
    http("POST", f"{BANK}/_control", control or {})
    http("DELETE", f"{MAIL}/messages")
    sql("ops_db", "TRUNCATE audit_log, mortgage_applications, kyc_requests, restructure_plans RESTART IDENTITY")


# ======================================== scenarios ========================================

def mortgage(name, score=700, amount=400000, approved=380000, flow=(), signs=("signed",)):
    def go():
        reset({"score": score, "envelope_status": list(signs)})
        sql("ops_db", f"INSERT INTO mortgage_applications VALUES ('M1','ann@mail.test',{amount},'new')")
        r = Run(name, "mortgage_offer")
        r.start({"application_id": "M1", "applicant_email": "ann@mail.test", "amount": amount, "property_value": 600000})
        form = {"approved_amount": approved, "conditions": "buildings insurance"}
        for node, what in flow:
            r.silence(node) if what == "silence" else r.decide(node, what, form if what == "approved" and "Committee" not in node else None)
        r.finish()
        return r
    return go


def mortgage_checks(r, booked=True, committee=False, declined=False, expired=False, reminders=0, escalated=False):
    status = sql("ops_db", "SELECT status FROM mortgage_applications WHERE application_id='M1'")[0][0]
    want = "booked" if booked else "declined" if declined else "expired"
    r.check(f"execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check(f"ops_db status = {want}", status == want, status)
    p = paths()
    r.check("bureau asked for the score", "POST /credit-checks" in p, p)
    ann = [m["subject"] for m in to("ann@mail.test")]
    if booked:
        r.check("mortgage booked at the approved amount", ("POST", "/mortgages", {"application_id": "M1", "amount": 380000 if not committee else 650000}) in
                [(m, pa, {k: b.get(k) for k in ("application_id", "amount")}) for m, pa, b in calls()], calls())
        audit = sql("ops_db", "SELECT entity, entity_id, action FROM audit_log")
        r.check("audit row mortgage/M1/booked in the same transaction", audit == [["mortgage", "M1", "booked"]], audit)
        r.check("applicant got offer + welcome", any("offer" in s.lower() for s in ann) and any("welcome" in s.lower() for s in ann), ann)
    if declined:
        r.check("no envelope, no booking", not any(x.startswith("POST /envelopes") or x == "POST /mortgages" for x in p), p)
        r.check("applicant told we can't offer", len(ann) == 1, ann)
    if expired:
        gets = [x for x in p if x.startswith("GET /envelopes/")]
        r.check("signature checked 5 times", len(gets) == 5, gets)
        r.check("envelope cancelled", any(x.endswith("/cancel") for x in p), p)
        r.check("no booking", "POST /mortgages" not in p, p)
    sign_reminders = [s for s in ann if "please sign" in s.lower()]
    r.check(f"{reminders} sign reminder(s)", len(sign_reminders) == reminders, ann)
    committee_mail = to("committee@bank.test")
    r.check(f"credit committee {'asked twice' if committee else 'not involved'}", len(committee_mail) == (2 if committee else 0),
            [m["subject"] for m in committee_mail])
    if escalated:
        r.check("underwriter reminded", any("reminder" in m["subject"].lower() for m in to("underwriters@bank.test")),
                [m["subject"] for m in to("underwriters@bank.test")])
        r.check("head of lending asked", len(to("head-lending@bank.test")) >= 1, None)


def wire(name, amount=50000, flow=(), hit=False, customer="C2"):
    def go():
        reset({"hit": hit})
        r = Run(name, "wire_release")
        r.start({"wire_id": "W1", "account_id": "A1", "customer_id": customer, "amount": amount,
                 "beneficiary_name": "Orion Trading", "beneficiary_country": "AE"})
        for node, what, form in flow:
            r.silence(node) if what == "silence" else r.decide(node, what, form)
        r.finish()
        return r
    return go


def wire_checks(r, action, api, actions_all=None, detail=None, emails=()):
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    audit = sql("ops_db", "SELECT entity, entity_id, action, coalesce(detail,'') FROM audit_log ORDER BY id")
    acts = [a[2] for a in audit]
    r.check(f"audit actions = {actions_all or [action]}", acts == (actions_all or [action]), audit)
    r.check("audit rows are entity 'wire', W1", all(a[0] == "wire" and a[1] == "W1" for a in audit), audit)
    p = paths()
    for x in api:
        r.check(f"bank API: {x}", x in p, p)
    for wrong in {"POST /wires/W1/release", "POST /wires/W1/hold", "POST /wires/W1/block"} - set(api):
        r.check(f"no {wrong}", wrong not in p, p)
    if detail:
        r.check(f"SAR reference recorded ({detail})", any(a[2] == "sar_filed" and a[3] == detail for a in audit), audit)
    for addr in emails:
        r.check(f"email to {addr}", len(to(addr)) >= 1, [m["to"] for m in mails()])


def wire_callback_context(r):
    """The callback task must show the customer's real name and phone, read from core_banking."""
    t = http("GET", f"{E}/tasks/{r.task('Callback task')['id']}")
    blob = json.dumps(t)
    r.check("callback task shows Bo Park +1-555-0102 from core_banking", "Bo Park" in blob and "+1-555-0102" in blob, t.get("context"))


def kyc(name, rows, uploaded):
    def go():
        reset({"uploaded": {c: True for c in uploaded}})
        for cid, days, sent, status in rows:
            sql("ops_db", f"INSERT INTO kyc_requests (customer_id, email, requested_on, reminders_sent, status) VALUES "
                          f"('{cid}', '{cid.lower()}@mail.test', current_date - {days}, {sent}, '{status}')")
        r = Run(name, "kyc_document_chase")
        r.start({})
        r.finish()
        return r
    return go


ASK1, ASK2 = os.environ.get("RECERT_ASK1", "Manager task"), os.environ.get("RECERT_ASK2", "Second task")


def recert(name, flow):
    def go():
        reset()
        r = Run(name, "access_recertification")
        r.start({})
        if os.environ.get("RECERT_PARALLEL"):
            # per_item: every manager's first task is open at once, before anyone has answered
            pick(r, ASK1, "m1@bank.test"), pick(r, ASK1, "m2@bank.test")
            both = [t for t in http("GET", f"{E}/tasks?status=open&workflow_id={r.wf}&limit=100")["items"]
                    if t["execution_id"] == r.eid and t["node_name"] == ASK1]
            r.check("both managers' tasks open at the same time", len(both) == 2, len(both))
        for node, mgr, what, form in flow:
            # tasks for different managers share a node name; pick the one addressed to this manager
            t = pick(r, node, mgr)
            if what == "silence":
                sql("workflows_live", f"""UPDATE "workflow-app".human_tasks SET due_at = now() - interval '1 second' WHERE id = '{t["id"]}'""")
                wait_closed(t["id"])
                r.log.append(f"{node} ({mgr}): no answer")
            else:
                http("POST", f"{E}/tasks/{t['id']}/decide", {"decision": what, "decided_by": mgr, "form_data": form},
                     {"Idempotency-Key": "live-" + t["id"]})
                r.log.append(f"{node} ({mgr}): {what} {form}")
        r.finish(90)
        return r
    return go


def pick(r, node, mgr, wait=20):
    end = time.time() + wait
    while time.time() < end:
        for t in http("GET", f"{E}/tasks?status=open&workflow_id={r.wf}&limit=100")["items"]:
            if t["execution_id"] == r.eid and t["node_name"] == node:
                full = http("GET", f"{E}/tasks/{t['id']}")
                if mgr in json.dumps(full.get("notify") or {}) + json.dumps(full.get("assignees") or []) + json.dumps(full.get("context") or {}) + (full.get("title") or ""):
                    return full
        time.sleep(0.5)
    open_ = [(t["node_name"], t["title"]) for t in http("GET", f"{E}/tasks?status=open&workflow_id={r.wf}")["items"] if t["execution_id"] == r.eid]
    raise RuntimeError(f"no open '{node}' task for {mgr} (open: {open_}; execution {r.status()})")


def wait_closed(tid):
    end = time.time() + 75
    while time.time() < end:
        if sql("workflows_live", f"""SELECT status FROM "workflow-app".human_tasks WHERE id = '{tid}'""")[0][0] != "open":
            return
        time.sleep(1)
    raise RuntimeError("sweeper did not expire task")


def hardship(name, loan="L1", flow=()):
    def go():
        reset()
        r = Run(name, "hardship_restructure")
        r.start({"loan_id": loan, "customer_email": "jo@mail.test", "reason": "lost my job"})
        for node, what, form in flow:
            r.silence(node) if what == "silence" else r.decide(node, what, form)
        r.finish()
        return r
    return go


def plans():
    return sql("ops_db", "SELECT loan_id, plan, months::text, status FROM restructure_plans")


SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


@scenario
def mortgage_happy():
    r = mortgage("mortgage_happy", flow=[("Underwriter decision", "approved")])()
    mortgage_checks(r)
    return r


@scenario
def mortgage_low_score():
    r = mortgage("mortgage_low_score", score=520)()
    mortgage_checks(r, booked=False, declined=True)
    r.check("no underwriter task", len(to("underwriters@bank.test")) == 0, None)
    return r


@scenario
def mortgage_big_committee_slow_signer():
    """650k: two committee members one after the other; applicant signs on the 3rd daily check."""
    r = mortgage("mortgage_big_committee_slow_signer", amount=700000, approved=650000, signs=("sent", "sent", "signed"),
                 flow=[("Underwriter decision", "approved"), ("Committee member 1", "approved"), ("Committee member 2", "approved")])()
    mortgage_checks(r, committee=True, reminders=2)
    return r


@scenario
def mortgage_committee_rejects():
    r = mortgage("mortgage_committee_rejects", amount=700000, approved=650000,
                 flow=[("Underwriter decision", "approved"), ("Committee member 1", "approved"), ("Committee member 2", "rejected")])()
    mortgage_checks(r, booked=False, declined=True, committee=True)
    return r


@scenario
def mortgage_underwriter_silent_head_decides():
    r = mortgage("mortgage_underwriter_silent_head_decides",
                 flow=[("Underwriter decision", "silence"), ("Underwriter second ask", "silence"), ("Head of lending decision", "approved")])()
    mortgage_checks(r, escalated=True)
    return r


@scenario
def mortgage_nobody_decides():
    r = mortgage("mortgage_nobody_decides",
                 flow=[("Underwriter decision", "silence"), ("Underwriter second ask", "silence"), ("Head of lending decision", "silence")])()
    mortgage_checks(r, booked=False, declined=True, escalated=True)
    return r


@scenario
def mortgage_never_signed():
    r = mortgage("mortgage_never_signed", signs=("sent",), flow=[("Underwriter decision", "approved")])()
    mortgage_checks(r, booked=False, expired=True, reminders=4)
    return r


@scenario
def wire_small():
    r = wire("wire_small", amount=9999.99)()
    wire_checks(r, "released", ["POST /wires/W1/release"])
    r.check("no callback task for a small wire", len(to("payops@bank.test")) == 0, None)
    return r


@scenario
def wire_confirmed_clean():
    reset({})
    run = Run("wire_confirmed_clean", "wire_release")
    run.start({"wire_id": "W1", "account_id": "A1", "customer_id": "C2", "amount": 50000,
               "beneficiary_name": "Orion Trading", "beneficiary_country": "AE"})
    wire_callback_context(run)
    run.decide("Callback task", "approved", {"customer_confirmed": "yes"})
    run.finish()
    wire_checks(run, "released", ["POST /screen", "POST /wires/W1/release"])
    return run


@scenario
def wire_fraud():
    r = wire("wire_fraud", flow=[("Callback task", "approved", {"customer_confirmed": "no"})])()
    wire_checks(r, "fraud", ["POST /wires/W1/hold", "POST /accounts/A1/freeze"], emails=["fraud@bank.test"])
    return r


@scenario
def wire_no_callback():
    r = wire("wire_no_callback", flow=[("Callback task", "silence", None), ("Callback task again", "silence", None)])()
    wire_checks(r, "held_no_callback", ["POST /wires/W1/hold"], emails=["paylead@bank.test", "bo@mail.test"])
    return r


@scenario
def wire_sanctions_true_match_sar():
    r = wire("wire_sanctions_true_match_sar", hit=True, flow=[
        ("Callback task", "approved", {"customer_confirmed": "yes"}),
        ("MLRO review", "approved", {"disposition": "true_match"}),
        ("SAR task", "approved", {"sar_reference": "SAR-2026-0042"})])()
    wire_checks(r, "blocked", ["POST /screen", "POST /wires/W1/block"], actions_all=["blocked", "sar_filed"], detail="SAR-2026-0042")
    return r


@scenario
def wire_sanctions_mlro_silent():
    r = wire("wire_sanctions_mlro_silent", hit=True, flow=[
        ("Callback task", "approved", {"customer_confirmed": "yes"}),
        ("MLRO review", "silence", None),
        ("SAR task", "approved", {"sar_reference": "SAR-2026-0043"})])()
    wire_checks(r, "blocked", ["POST /wires/W1/block"], actions_all=["blocked", "sar_filed"], detail="SAR-2026-0043")
    return r


@scenario
def wire_million_treasury():
    r = wire("wire_million_treasury", amount=1500000, hit=True, flow=[
        ("Callback task", "approved", {"customer_confirmed": "yes"}),
        ("MLRO review", "approved", {"disposition": "false_positive"}),
        ("Treasury approval 1", "approved", {}), ("Treasury approval 2", "approved", {})])()
    wire_checks(r, "released", ["POST /wires/W1/release"])
    r.check("treasury asked twice", len(to("treasury@bank.test")) == 2, [m["subject"] for m in to("treasury@bank.test")])
    return r


@scenario
def wire_million_treasury_rejects():
    r = wire("wire_million_treasury_rejects", amount=1500000, flow=[
        ("Callback task", "approved", {"customer_confirmed": "yes"}),
        ("Treasury approval 1", "rejected", {})])()
    wire_checks(r, "held_by_treasury", ["POST /wires/W1/hold"])
    return r


KYC_ALL = [("C1", 3, 0, "open"), ("C2", 7, 1, "open"), ("C3", 10, 2, "open"), ("C4", 12, 2, "restricted"),
           ("C5", 9, 2, "open"), ("C6", 1, 0, "open"), ("C7", 7, 0, "open")]


@scenario
def kyc_every_state():
    r = kyc("kyc_every_state", KYC_ALL, uploaded=["C4", "C6"])()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    got = {c: (s, n) for c, s, n in sql("ops_db", "SELECT customer_id, status, reminders_sent::text FROM kyc_requests")}
    want = {"C1": ("open", "1"), "C2": ("open", "2"), "C3": ("restricted", "2"), "C4": ("complete", "2"),
            "C5": ("open", "2"), "C6": ("complete", "0"), "C7": ("open", "1")}
    for c in want:
        r.check(f"{c} -> {want[c]}", got.get(c) == want[c], got.get(c))
    p = paths()
    r.check("C3 restricted via core banking", "POST /customers/C3/restrict" in p, p)
    r.check("C4 restriction lifted", "POST /customers/C4/lift-restriction" in p, p)
    r.check("C6 (was open) not 'lifted'", "POST /customers/C6/lift-restriction" not in p, p)
    r.check("nobody else restricted", sum(1 for x in p if x.endswith("/restrict")) == 1, p)
    r.check("documents asked for every request (7)", sum(1 for x in p if x.endswith("/documents")) == 7, p)
    for c, n in {"c1": 1, "c2": 1, "c3": 1, "c4": 1, "c5": 0, "c6": 1, "c7": 1}.items():
        r.check(f"{c}@mail.test got {n} email", len(to(f"{c}@mail.test")) == n, [m["subject"] for m in to(f"{c}@mail.test")])
    r.check("RM asked to call C3", len(to("rm@bank.test")) == 1, None)
    s = to("kyc@bank.test")
    r.check("one KYC summary", len(s) == 1, [m["subject"] for m in s])
    if s:
        t = s[0]["text"]
        r.check("summary: 2 completed, 3 reminded, 1 restricted", all(x in t for x in ("2", "3", "1")), t[:300])
        r.log.append("summary text: " + t.strip().replace("\n", " / ")[:300])
    return r


@scenario
def kyc_nothing_due():
    r = kyc("kyc_nothing_due", [("C1", 1, 0, "open"), ("C5", 9, 2, "open")], uploaded=[])()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check("no emails at all", mails() == [], [m["to"] for m in mails()])
    got = sql("ops_db", "SELECT customer_id, status, reminders_sent::text FROM kyc_requests ORDER BY 1")
    r.check("rows untouched", got == [["C1", "open", "0"], ["C5", "open", "2"]], got)
    return r


@scenario
def recert_one_revokes():
    r = recert("recert_one_revokes", [(ASK1, "m1@bank.test", "approved", {"revoke": "u2"}),
                                      (ASK1, "m2@bank.test", "approved", {"revoke": ""})])()
    recert_checks(r, revoked=["u2"], outcomes={"m1@bank.test": "revoked_some", "m2@bank.test": "confirmed"})
    return r


@scenario
def recert_manager_silent():
    r = recert("recert_manager_silent", [(ASK1, "m1@bank.test", "approved", {"revoke": ""}),
                                         (ASK1, "m2@bank.test", "silence", None),
                                         (ASK2, "m2@bank.test", "silence", None)])()
    recert_checks(r, revoked=["u3"], outcomes={"m1@bank.test": "confirmed", "m2@bank.test": "revoked_all_timeout"})
    r.check("m2 reminded", len(to("m2@bank.test")) >= 2, [m["subject"] for m in to("m2@bank.test")])
    ciso = to("ciso@bank.test")
    r.check("CISO told about m2 + summary", len(ciso) == 2 and any("m2@bank.test" in m["subject"] + m["text"] for m in ciso),
            [m["subject"] for m in ciso])
    return r


@scenario
def recert_answers_second_task():
    r = recert("recert_answers_second_task", [(ASK1, "m2@bank.test", "silence", None),
                                              (ASK1, "m1@bank.test", "approved", {"revoke": "u1, u2"}),
                                              (ASK2, "m2@bank.test", "approved", {"revoke": "u3"})])()
    recert_checks(r, revoked=["u1", "u2", "u3"], outcomes={"m1@bank.test": "revoked_some", "m2@bank.test": "revoked_some"})
    r.check("m2 reminded", len(to("m2@bank.test")) >= 2, [m["subject"] for m in to("m2@bank.test")])
    ciso = to("ciso@bank.test")
    r.check("CISO gets exactly one summary", len(ciso) == 1, [m["subject"] for m in ciso])
    return r


def recert_checks(r, revoked, outcomes):
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    got = sorted(x.split("/")[2] for x in paths() if x.endswith("/revoke-access"))
    r.check(f"revoked exactly {revoked}", got == sorted(revoked), got)
    audit = {a[1]: a[2] for a in sql("ops_db", "SELECT entity, entity_id, action FROM audit_log WHERE entity='recert'")}
    r.check(f"audit outcomes {outcomes}", audit == outcomes, audit)
    for m in outcomes:
        r.check(f"{m} got a task email", len(to(m)) >= 1, None)
    r.check("CISO summary sent", len(to("ciso@bank.test")) >= 1, None)


@scenario
def hardship_not_found():
    r = hardship("hardship_not_found", loan="L9")()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check("replied 404 not_found", r.ran("Reply not found") and not r.ran("Reply in review"), list(r.exec["node_data"]))
    r.check("no officer task", len(to("collections@bank.test")) == 0, None)
    return r


@scenario
def hardship_accepted():
    r = hardship("hardship_accepted", flow=[("Officer assessment", "approved", {"plan": "payment_holiday", "months": 3}),
                                            ("Record answer", "approved", {"customer_response": "accepted"})])()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check("replied 202 in_review first", r.ran("Reply in review"), list(r.exec["node_data"]))
    r.check("no risk review (3 months, 18.5k)", len(to("risk@bank.test")) == 0, None)
    c = [b for m, p, b in calls() if p == "/loans/L1/restructure"]
    r.check("restructured via core banking with plan+months", c and c[0].get("plan") == "payment_holiday" and str(c[0].get("months")) == "3", c)
    r.check("plan row active", plans() == [["L1", "payment_holiday", "3", "active"]], plans())
    a = sql("ops_db", "SELECT entity, entity_id, action FROM audit_log")
    r.check("audit loan/L1/restructured", a == [["loan", "L1", "restructured"]], a)
    r.check("customer got offer + confirmation", len(to("jo@mail.test")) == 2, [m["subject"] for m in to("jo@mail.test")])
    return r


@scenario
def hardship_long_holiday_risk_silent():
    r = hardship("hardship_long_holiday_risk_silent", flow=[("Officer assessment", "approved", {"plan": "payment_holiday", "months": 6}),
                                                            ("Risk review", "silence", None)])()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check("risk manager asked", len(to("risk@bank.test")) == 1, None)
    r.check("plan row declined", plans() == [["L1", "payment_holiday", "6", "declined"]], plans())
    r.check("no restructure call", not any(p.endswith("/restructure") for p in paths()), paths())
    r.check("customer told", len(to("jo@mail.test")) == 1, [m["subject"] for m in to("jo@mail.test")])
    return r


@scenario
def hardship_customer_silent_lapses():
    r = hardship("hardship_customer_silent_lapses", flow=[("Officer assessment", "approved", {"plan": "term_extension", "months": 12}),
                                                          ("Record answer", "silence", None), ("Record answer again", "silence", None)])()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check("plan row lapsed", plans() == [["L1", "term_extension", "12", "lapsed"]], plans())
    r.check("customer: offer, reminder, lapsed", len(to("jo@mail.test")) == 3, [m["subject"] for m in to("jo@mail.test")])
    r.check("no restructure call", not any(p.endswith("/restructure") for p in paths()), paths())
    return r


@scenario
def hardship_officer_rejects():
    r = hardship("hardship_officer_rejects", flow=[("Officer assessment", "rejected", {})])()
    r.check("execution succeeded", r.exec["status"] == "success", r.exec.get("errors"))
    r.check("customer told", len(to("jo@mail.test")) == 1, [m["subject"] for m in to("jo@mail.test")])
    r.check("no restructure call", not any(p.endswith("/restructure") for p in paths()), paths())
    r.check("a declined plan row", len(plans()) == 1 and plans()[0][3] == "declined", plans())
    return r


if __name__ == "__main__":
    picks = [n for n in SCENARIOS if not sys.argv[1:] or any(n.startswith(a) for a in sys.argv[1:])]
    results = []
    for n in picks:
        t0 = time.time()
        try:
            r = SCENARIOS[n]()
            res = {"scenario": n, "ok": all(c["ok"] for c in r.checks), "checks": r.checks, "log": r.log, "execution": r.eid}
        except Exception as e:  # a scenario that can't be driven is a failure, with why
            res = {"scenario": n, "ok": False, "error": str(e)[:600]}
        res["seconds"] = round(time.time() - t0, 1)
        results.append(res)
        bad = [c for c in res.get("checks", []) if not c["ok"]]
        print(f"{'PASS' if res['ok'] else 'FAIL'} {n} ({res['seconds']}s)" + (f"  ERROR {res['error']}" if "error" in res else ""), flush=True)
        for c in bad:
            print(f"   x {c['check']}  got: {json.dumps(c.get('got'), default=str)[:300]}", flush=True)
    out = HERE / ("results-" + "-".join(sys.argv[1:]) + ".json" if sys.argv[1:] else "results.json")
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} scenarios passed")
