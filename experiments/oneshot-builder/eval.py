"""Run the bank use cases in parallel and summarise.   python eval.py [N_PARALLEL]"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build import HERE, build, show, slug

CASES = [
    "Track vendor invoices. Anyone in AP can add one; over $5,000 needs a manager to approve before it can be paid. Rejecting emails the submitter.",
    "Branch daily cash count sheet. Tellers submit counted vs expected cash; differences over $50 get flagged and need a branch manager sign-off.",
    "KYC review queue: reviews get assigned to analysts, have a due date, and can be completed or escalated to compliance. Show who's overdue.",
    "Audit findings tracker: raise a finding with severity, assign an owner and due date, close only with evidence notes. High severity closes need risk-manager approval.",
    "Loan exception requests: relationship managers request a waiver with an amount; up to $250k a credit officer approves, above that the chief credit officer must.",
    "Customer complaint log with SLA: log, assign, respond, close. Complaints must be responded to within 5 business days; list the ones breaching SLA.",
    "System access requests: staff request access to a system, their manager approves, then IT marks it done. Requester gets an email at each step.",
    "Reconciliation breaks between the GL and the core banking system: assign breaks, resolve with a reason, write-offs over $1,000 need controller approval.",
]

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(lambda c: build(c, HERE / "out" / slug(c)), CASES))
    for r in results:
        show(r)
    first = sum(r["first_pass"] for r in results)
    final = sum(r["final_pass"] for r in results)
    secs = sorted(r["seconds"] for r in results)
    print(f"\n== {len(results)} cases: first-call pass {first}/{len(results)}, "
          f"after fixes {final}/{len(results)}, median {secs[len(secs)//2]}s, max {secs[-1]}s")
    (HERE / "out" / "summary.json").write_text(json.dumps(results, indent=2))
