# Try it in 15 minutes

Synthetic data (fake names and SSNs):

| File | What it holds |
|---|---|
| `loans_book.xlsx` | Two sheets. **applications**: 1,500 rows, target `defaulted` yes/no, with 250 left blank to label. **payments**: 6,101 instalments. |
| `bureau.csv` | 1,200 customers |
| `applications_changes.csv` | A change file: 20 updates, 5 deletes and 10 inserts, in an `_op` column |

## Start

```bash
git checkout motion-components && git pull
make setup                     # if Python 3.13 fails: make setup PYTHON=python3.12
ENSEMBLE_POC_MODE=1 make dev   # API on :8000, UI on http://localhost:5190; you can approve your own requests
```
For Postgres instead: `ENSEMBLE_POC_MODE=1 make dev-pg`.

Set **Acting as** to any name, for example `me`.

## Steps

1. **Upload.** Data → upload `loans_book.xlsx` and `bureau.csv` together. You get 3 tables.
   - Keys: `applications.app_id` and `bureau.customer_id`.
   - Accept the suggested relations. Mark `ssn` and `applicant_name` as PII. Create the version (v1).
2. **Change file.** Upload `applications_changes.csv` as the next version, then "Apply as delta" on applications (+10 ~20 −5). Diff v1 → v2.
3. **Join.** Joins → New join: grain `applications`, payments aggregated (mean `days_late`, count), bureau n:1 on `customer_id`. Build the frame.
4. **Train.** Train `defaulted` on the join (rows with a blank label are skipped). Then register the model; the card shows parity ✓.
5. **Label by hand.** Labels tab → new layer on `applications.defaulted` → new task (classes yes/no, a few gold rows).
   - Label 10 rows with the keys **1** / **2** and **Enter**.
6. **Label with an LLM.** Labels → LLM. Destination "fake" for a free test, or your configured LLM.
   - Paste the query and template below, then: Run preview → Test on 5 rows → Save → Calibrate → Run → Accept.
7. **Snapshot and retrain.** Take a snapshot, train again with the snapshot pinned, and compare the two runs.
8. **Carry-over.** Edit a few labelled rows (Edit → set cells). The Labels tab shows them as **stale**.
9. **Materialise.** Write the snapshot into a new version (v3); the labels show as absorbed.
10. **Governance.** Share with another team, put a legal hold on the project, then try to delete the dataset: it's refused. Purge `ssn`.

### SQL query for step 6

```sql
SELECT a.__row_id, a.purpose, a.note, a.income, a.amount, a.term_months,
       b.score, b.n_inquiries_12m,
       avg(p.days_late) AS avg_days_late, count(p.*) AS n_payments
FROM applications a
LEFT JOIN bureau b   ON b.customer_id = a.customer_id
LEFT JOIN payments p ON p.app_id = a.app_id
GROUP BY ALL
ORDER BY a.__row_id
```

### Prompt template for step 6

```
Loan application. Purpose: {purpose}. Applicant note: "{note}".
Income {income}, amount {amount} over {term_months} months.
Bureau score {score}, {n_inquiries_12m} inquiries in 12 months.
Payments so far: {n_payments}, average days late {avg_days_late}.
Will this applicant default? Answer yes or no.
```
