"""Harder cases: several related tables, and edits to existing apps.

    python eval2.py [N_PARALLEL]      (run eval.py first: edits start from its apps in out/)
"""
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

from build import HERE, build, show, slug

MULTI = [
    "Vendor payments: keep a vendor list (active or blocked), invoices per vendor, and payments against invoices. "
    "Invoices can't be added for blocked vendors. Partial payments are allowed; an invoice is paid when payments cover it. "
    "Over $5,000 needs manager approval before any payment.",
    "Branch cash: branches, tellers assigned to a branch, and daily cash counts per teller. Differences over $50 need the "
    "branch manager of that branch to sign off. Show the monthly variance by branch.",
    "Loan covenant tracking: loans, covenants on each loan (type, threshold, frequency), and quarterly covenant tests. "
    "A failed test marks the covenant breached and emails the loan's relationship manager. A waiver needs credit approval.",
    "Audit program: audits contain findings, findings have action items with owners and due dates. A finding can only "
    "close when all its action items are done; an audit can only close when all its findings are closed.",
]

EDITS = [  # (app folder from eval.py's run in out/, change)
    ("track-vendor-invoices-anyone-in-ap-can-a",
     "Add a due date to invoices (default 30 days after it's added) and a list of overdue unpaid invoices."),
    ("customer-complaint-log-with-sla-log-assi",
     "Add a category: fees, fraud, service, other. Fraud complaints get assigned to the fraud team automatically and must be responded to within 1 business day."),
    ("system-access-requests-staff-request-acc",
     "High-risk systems (core banking, payments) also need the security team to approve after the manager, before IT can do it."),
    ("kyc-review-queue-reviews-get-assigned-to",
     "Add a risk rating (low, medium, high). High-risk reviews need a second analyst to sign off before they count as completed."),
]

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    base = HERE / "out"
    jobs = [(c, HERE / "out2" / slug(c), None) for c in MULTI]
    for app, change in EDITS:
        target = HERE / "out2" / ("edit-" + app[:30])
        jobs.append((change, target, base / app))
    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(lambda j: build(*j), jobs))
    for kind, rs in (("multi-table", results[:len(MULTI)]), ("edit", results[len(MULTI):])):
        print(f"\n######## {kind}")
        for r in rs:
            show(r)
        print(f"== {kind}: first-call pass {sum(r['first_pass'] for r in rs)}/{len(rs)}, "
              f"after fixes {sum(r['final_pass'] for r in rs)}/{len(rs)}, "
              f"times {sorted(r['seconds'] for r in rs)}")
    (HERE / "out2" / "summary.json").write_text(json.dumps(results, indent=2))
