# Analytics Platform — End-to-End Validation Summary

**Status: 104 of 104 checks passing. 0 failures.**
Last run: 2026-08-05 against a live server.

---

## 1. What we did, in one paragraph

We built an automated rehearsal of a full working day on the platform. It starts
with an administrator setting up a team, hires an editor and a viewer, uploads
real spreadsheets, explores the data, sets a quality rule, ships a second
version, catches the data problems that second version introduced, builds
reports, joins two datasets, and finally tries to break into all of it from an
account that shouldn't have access. Every step runs against a real, running
system over the network — not a simulation. If any single step misbehaves, the
run fails and tells us which promise was broken.

---

## 2. Why this matters more than a normal test count

Most test suites check parts in isolation: "does the upload function work?"
That answers whether a component is correct. It does not answer the question the
business actually cares about, which is **"can a real person get their job done,
start to finish, without hitting a wall?"**

This validation answers the second question. It is one continuous story — the
dataset created in step 2 is the dataset explored in step 3, reported on in step
6, and locked down in step 12. Nothing is set up artificially. That means it
also proves the *seams* between features hold, which is where systems usually
break in production.

It also runs over real HTTP against a real server, so it exercises the network
layer, real file uploads, real downloads, and real permission enforcement —
none of which a unit test touches.

---

## 3. The twelve scenarios

### A. Setting up an organisation *(7 checks)*
An administrator creates a team, adds an editor and a read-only viewer, then
creates a **second, unrelated team** with its own user.

> That second team exists purely so we can later prove one company's data is
> invisible to another. It's the control group for every security check.

### B. Getting data in *(3 checks)*
Two uploads, exactly as a user would do from a file picker:
- a **CSV** of regional order data
- a **real multi-tab Excel workbook** (a "Revenue" tab and an "Expenses" tab)

**Proved:** both land successfully, and the system correctly recognises the
workbook's separate tabs and records the structure of each.

### C. Exploring the data *(6 checks)*
Preview rows, filter and sort them ("show me orders over $60, largest first"),
and drill into a single column's statistics.

**Proved, and worth highlighting:** when a user queries a *multi-tab* workbook
without saying which tab, the system **refuses and asks** rather than silently
guessing. Guessing wrong here means a business decision made on the wrong tab.
It also returns errors a screen can render — a mistyped column name comes back
with the list of valid names, not a stack trace.

### D. The quality gate *(3 checks)*
A data steward writes a rule ("the amount column must never be blank"), runs
validation, and promotes the version to **production**.

**Proved:** the promote/tag workflow that governs what downstream consumers see.

### E. Catching a bad release *(4 checks)* ⭐
A second version of the data is uploaded. It deliberately contains problems: a
new region (LATAM) and **blank amounts**.

**Proved — this is the headline result:** the system detected the regression
*by itself*, with no one asking it to look. It flagged:
- a **spike in blank values**
- **new categories** appearing that weren't there before
- and quantified it precisely: the blank rate on `amount` rose by **50 percentage points**

> Business value: this is the difference between finding out about a data
> problem from the system, and finding out from a customer.

### F. Reporting and reuse *(13 checks)*
Save a reusable view, build a pivot table, export it to CSV and download it, run
ad-hoc SQL, save the pivot as a reusable definition, run it, and **publish the
result as a brand-new dataset in its own right**.

**Proved:** the numbers are correct (the pivot totals to exactly $525, verified
cell by cell), the CSV downloads with the right headers, the published dataset
records where it came from, and the activity timeline shows every event.

**Two security checks sit inside this section:**
- the SQL tool **rejects any attempt to modify or export data** — it is read-only, enforced
- an attempt to read a server file (`/etc/passwd`) is blocked, **and the error message doesn't echo the path back**, so it can't be used to probe the file system

### G. Documentation and health *(9 checks)*
Find duplicate records, get a missing-data report, write business definitions
into the data dictionary (business name, unit, sensitivity), and view the
dataset's health scorecard.

**Proved:** the health scorecard is honest — it reported *warning* on drift and
correctly identified `amount` as the worst column for missing data. The catalogue
lets you filter to "which datasets are only partly documented?"

### H. Reshaping data without touching the original *(10 checks)*
Build a four-step cleanup pipeline (standardise text case → drop blank rows →
add a calculated column → sort), preview it, run it, and publish the result.

**Proved:**
- an **invalid pipeline is rejected when you save it**, not when it runs — so a scheduled job can't fail at 3am on a mistake made weeks earlier
- preview is a genuine dry run — nothing is written
- **the original data is untouched** (verified explicitly after the run)
- the output is automatically profiled and compared against its source

### I. Connecting datasets together *(13 checks)* ⭐
A CRM workbook (Customers + Orders) is uploaded with **no relationship declared
between the tabs**. The system is asked to find one.

**Proved:**
- it **discovered the undeclared link** (Orders → Customers) on its own, and showed its evidence
- an **unreviewed** link **cannot be used** — the system refuses (this is the guardrail)
- before running the join, it **predicts the outcome**: how many rows, whether the data will multiply, and that **33% of customers have no matching orders**
- only after a human confirms the link does the join run

> Business value: joining datasets is the single easiest way to produce
> confidently wrong numbers. The system forces a human review, then tells you
> what will happen *before* it happens.

### K. Governance and controls *(14 checks)* ⭐
- **Row-level comparison** between two versions: exactly which rows were added, removed, changed, unchanged — and *which field* moved. When the chosen identifier isn't unique, the system **refuses to answer rather than answering wrongly.**
- **Confidential data masking:** a column is marked confidential. A viewer then sees `***` instead of values — **and their attempt to download the raw file is blocked.** An administrator sees the real values.
- **Notifications:** an external system can subscribe to events. The delivery is cryptographically signed, the signing secret **can never be read back after creation**, and every delivery attempt is recorded.

> Business value: "we masked the field on screen" is worthless if the export
> button bypasses it. We test the bypass.

### L2. Storage governance *(9 checks)*
Generated files are organised by team and dataset, listed only to people
entitled to see them, and are subject to a **retention policy** — short-lived
scratch output expires quickly, while published business data is **kept
indefinitely**. Cleanup is administrator-only and verified not to touch live data.

### L. The break-in attempt *(14 checks)* ⭐
Finally, every major feature is probed from the wrong seat:

| Who | What happens |
|---|---|
| Read-only viewer | Can read. Every write attempt is **refused** |
| User from another company | Datasets, files, reports, and relationships are **invisible** — reported as "not found", not "access denied" |
| Ordinary user | Cannot read the audit log or touch storage administration |

> The "not found" detail is deliberate. Saying "access denied" confirms the
> thing exists, which leaks information about another customer. We verify the
> system doesn't leak it.

---

## 4. What this gives you, as assurances

| Business concern | Evidence |
|---|---|
| **Can users complete real work?** | Yes — 12 connected scenarios, uploading through publishing, all passing |
| **Will bad data reach production silently?** | No — the system detected a deliberately broken release unprompted |
| **Can one customer see another's data?** | No — probed on 6 different features; all correctly hidden |
| **Can confidential fields leak via export?** | No — masking is enforced on screen *and* on download |
| **Can a user damage data through the query tool?** | No — read-only, enforced and tested |
| **Can a bad join produce wrong numbers quietly?** | No — human confirmation required, with a forecast beforehand |
| **Do we know who did what?** | Yes — full audit trail plus a per-dataset activity timeline |
| **Will storage costs grow forever?** | No — retention policy by data type; published data kept, scratch expires |

---

## 5. Scorecard

| Measure | Value |
|---|---|
| End-to-end checks | **104 / 104 passing** |
| Automated tests (component level) | **1,028 passing** |
| Test execution time | ~2 minutes |
| API endpoints delivered | 134 |
| Storage configurations validated | 2 (local disk and S3-compatible cloud) |

Every component-level test runs **twice** — once against local disk storage and
once against cloud object storage — so the platform is proven on both.

---

## 6. Stated limits (what this does *not* cover)

Presented deliberately, so nothing is over-claimed:

1. **This is correctness, not load.** We have not measured behaviour under
   many concurrent users or at large data volumes. Performance testing is a
   separate exercise.
2. **Sign-on is a placeholder.** User identity is currently passed directly
   rather than through corporate single sign-on. The permission model itself is
   complete and tested; connecting it to the corporate identity provider is a
   contained, known piece of work.
3. **Automated cleanup is not yet scheduled.** The retention rules and the
   cleanup process exist and are tested, but nothing triggers them on a timer
   yet — today it is run on demand.
4. **Background job retries.** If a long-running job fails, it is recorded as
   failed but not automatically retried.

None of these block a demonstration or a pilot. Items 2–4 are the remaining
operational work.
