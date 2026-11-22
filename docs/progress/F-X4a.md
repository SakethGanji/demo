# F-X4a: require() or public() on every route, masking on the value-quoting reads, review decider, member changes, scoped registry audit (lane G)

Fixes X4 **C2** (critical), **M1**, **M2**, **M3** and **M5** (`docs/progress/X4.md`). Base: `motion-components` @ 2c0e3da,
merged with F-X4b (87810f4) and the other lanes before the final runs. Scripts and raw output: `docs/progress/F-X4a/`.

## What was built

### C2: every route declares, decided route by route

**The gate** (`authz/coverage.py`). The `ROW_GET` path regex is gone. Every route of every method (GETs included) must
carry `require(action, object)` or `public(reason)`. A route with neither is `uncovered`:
- `GET /api/authz/coverage` lists it in every mode;
- `ENSEMBLE_AUTHZ=enforce` refuses to start (as before);
- new: `ENSEMBLE_AUTHZ_STRICT_COVERAGE=1` makes every mode refuse to start.

`out/gate.txt` shows the live app (316 routes, 316 declared) and an added undeclared GET refused at start-up.

**The declarations.** 112 routes had none: 108 GETs, plus the 4 agent GETs, which F-X4b has since bound to the session
owner. Each was read and given an action from the C §1.3 matrix. The one-off edit script is
`F-X4a/declare.py` (decorator line + import only).

| Kind of route | Declaration |
|---|---|
| Run pages (`/runs/{id}/…`: events, ladder, threshold, cost, fairness, scorecard, monotone, doctor, vela, review, experiments, learning curve, AL, annotations, slices, shift, latency, gym, export.py, children; `/{kind}/runs/{id}` of el / segmented / survival / multilabel / reject; forecast chart; anomaly labels / supervised) | `run.read` on the run |
| Model-version pages (`/registry/versions/{vid}` and its card, mlflow, retire, monotone, batches, reasons, recourse, monitoring, retrain, changes, credit report) | `model.read` on the version |
| Batches (`/registry/batches/{bid}`, its shift) | `model.read` on the batch |
| Dataset metadata (usage, manifest, splits, decision time, Feature Lab list, SQL versions, project, lineage / annotations nodes, `/certifications/{cid}`) | `data.read_meta` |
| Dataset root views (history, the certifications list) | `data.read_meta` on `dataset_root` (see "Found while verifying") |
| Dataset values (split options, intake, Data Doctor, Feature Lab job, anomaly / forecast mapping, the `/detect` routes by `?dataset_id`) | `data.preview` |
| `/datasets/{id}/verify` | `data.verify` |
| `/uploads/{up}` | `data.upload` on the upload (its project) |
| `/jobs/{id}` | `data.read_meta` on the job (new `job` resolver: its team, else its run / dataset / version / batch) |
| `/teams/{t}`, `/teams/{t}/members` | new key `team.read` (viewer, risk reviewer, admin) |
| `/shares/{sid}` | `data.read_meta` on the share; `shares.grant` now lets the grantee team read its own share |
| `/autopilot/{aid}` and its inbox | `run.read` on the autopilot |
| `/admin/lake/status` · `/admin/reset-requests` · `/registry/serving`, `/workers` | `admin.lake` · `admin.reset` · `admin.config` |
| Lists (`/datasets`, `/data/search`, `/runs`, `/projects`, `/registry`, `/registry/versions`, `/registry/summary`, `/shares`, `/jobs`, `/autopilot`) | `public("list: …")`; rows filtered in enforce mode. `/jobs` and `/autopilot` are newly filtered with `deps.can()`, a new pure decision helper. `/status` shows the active run only to who may read it. |
| No team data (`/health`, `/me`, `/teams` directory, `/principals` picker, `/models`, `/gym/schema`, `/forecast/options`, `/monitoring/stream`, `/sql/demo`, `/llm`, `/agent/tools`, `/agent/status`, `/authz/coverage`, `/admin/lake/selfcheck`) | `public(reason)` |

**Masking on the reads that quote column values** (P20 rules; `lake/mask.run_page`). The page goes through the run
input's mask (`Mask.scrub`) and carries `masked_columns` / `masks` / `unmasked`. `unmasked=1&reason=…` works as on the
other run pages:
- `data.read_unmasked` on the run's input version;
- audited as `dataset_rows_read {read, reason, columns}`;
- 400 `reason_required` without a reason;
- 403 in enforce when not allowed; in audit mode, masked with `unmasked_refused`.

| Page | What was raw | Now |
|---|---|---|
| `GET /runs/{id}/fairness?group=<pii col>` | quartile edges (real SSNs), up to 50 values | group labels `••• 1 … ••• n` (`groups_masked`), edges and values scrubbed |
| `GET /runs/{id}/scorecard`, `scorecard.csv` | WoE `cuts`, bin labels | `cuts` masked (`cuts` / `range` / `lower` / `upper` / `group` are value keys now); the CSV's bin labels of a masked feature → `••• bin i` |
| `GET /registry/versions/{vid}/recourse` | `ranges.ssn {min, max}` | a dict keyed by a masked column is walked in that column's context, so `ranges` and `categories` are masked |
| `GET /runs/{id}/shift` | `options.date_columns[dob] {from, to}`, the default cutoff | masked, cutoff `•••` |
| `GET /registry/versions/{vid}/credit-report` (json / md / html) | dob range in the role evidence | scrubbed through the column's stored min / max |
| `GET /registry/versions/{vid}/card` (json / md / html) | the saved fairness report | masked |

**The fairness GET is pure.** It no longer writes `fairness_reports` into the run, nor from there into the card.
`POST /runs/{id}/fairness {group, t?}` (`run.train`) computes, saves and audits `fairness_report_saved`, and answers
like the GET (masked). UI: Explain › Fairness has a **Save to Model Card** button (`api.runs.saveFairness`).

### M3: batches masked

`lake/mask.batch_mask` gives the PII-tagged columns of the model's input. For a join model it is the union over the
pinned inputs.
- `GET /registry/batches/{bid}`: the preview and the drift report are masked.
- `…/download` and `/anomaly/runs/{id}/batches/{bid}.csv`: streamed with the masked columns masked
  (`X-Ensemble-Masked-Columns`).
- `POST /anomaly/runs/{id}/score`: masked in its answer.
- `unmasked=1&reason=…` on a download is `data.export_unmasked` (maintainer, or an unmasked share). On the preview it
  is `data.read_unmasked`. Both are audited as `dataset_rows_read` (`read: batch_download` / `batch_preview`).

### M1: the review's decider is the principal

- `require(..., names=("reviewer",))` adds a route's own decider fields to `BY_FIELDS`.
- In enforce mode every name field present must be the principal: `by`, `actor`, `created_by`, `requested_by`, the
  route's names, and the query `by` / `actor`. Before, only the first one present was checked. A mismatch is a 409.
- `reviewer` is now optional and defaults to the principal.
- Audit mode records a mismatch as would-deny `by_mismatch`, so the spoof is visible before enforce. It still goes
  through, like every audit-mode decision.
- The active-learning / annotation `reviewer` is not a decider: it is an annotator label (the UI sends "you"), so it is
  not checked.

### M2: member changes (`catalog/teams_api._member_gate`)

The handler checks run on read-only lookups before the handler writes anything:
- **Self-grants:** nobody gives themselves a role or changes their own. This is 403 `self_grant` in every mode, like
  four eyes. Leaving a team with DELETE is allowed, subject to the last-owner rule.
- **Current role counts too:** if the target's current role or the new role is owner, maintainer or risk_reviewer, the
  change is `team.settings` (owner or platform admin). `risk_reviewer` was added to the route-level `OWNERS` variant.
  Below that it stays `team.manage`.
- **DELETE** gets the same checks.
- **The last owner** can be neither removed nor demoted (409), even by an admin.

### M5: `/registry/audit` with no filter

- **Scoped to the caller's own teams.** `audit.read` is team-scoped and a share does not grant it (C §1.3). This uses
  the caller's own teams, not `visible_teams()`.
- **tm_default is not a catch-all.** Its rows count only when they are registry events (`version_id` / `model_name`
  set, i.e. the samples' models). Other people's platform-level denials, and rows that named no object, are left out.
- **Admins** see every row.
- **`/registry/summary`'s audit** is scoped the same way.
- **With `version_id` / `model`** the route is unchanged (`model.read` on that object).

### Found while verifying (fixed, lane G)

- **Lineage and annotations answered 400 for everyone.** F-X3a's NUL-byte id check refused the `node` object's internal
  `kind\x00id` value. This hit `GET` and `POST /annotations/{kind}/{id}` and `GET /lineage/{kind}/{id}`, and was there
  before this phase for the POST. `node` values are now exempt, since their path params are checked already.
- **Enforce-mode catalog search dropped shared datasets for the grantee.** Search rows carry `root_id`, not `id`, so
  the share check got `None`. A root is now shown when any of its versions is in a live share's scope.
- **A grantee's dataset page.** The UI opens a dataset by its root id, which is v1. With a `default` or `certified`
  share, v1 can be outside the scope, so history, which had no check before, would now 403 and the page would not
  load. History and the certifications list are root-level views, so they now use a new object kind, `dataset_root`:
  - `shares.grant` lets a grantee in when a live share covers any version of the root;
  - in enforce mode, `history` is then narrowed to the in-scope versions (no requests, audit trail or retired versions;
    `access: "shared"`), per C §1.3 "shared scope only".
- **`/certifications/{cid}`:** `shares.grant` resolves a certification through the version it certifies, so the
  grantee reads it (`out/cert_grantee.txt`: Fay 403, Carl 200, Vic 200).

## Verification

Everything ran on fresh data dirs: the pinned stack in `/var/tmp/x4/venv`, `ENSEMBLE_ADMINS=Ada,Adam`,
`ENSEMBLE_DEMO_MODE=1`, and `ENSEMBLE_AUTHZ_ALLOW_TYPED=1` in enforce. Postgres 16.15 is a scratch `initdb` cluster
(`ENSEMBLE_DB_URL=postgresql://postgres@/<db>?host=/var/tmp/x4/pg&port=5433`). Scripts:
- `F-X4a/run.sh` runs X4's `setup`, `sweep`, `decide` and `teams` plus `F-X4a/fx4a.py` (the checks X4's scripts do not
  make). The base runs use a worktree of 2c0e3da.
- `F-X4a/gate.py` checks the coverage gate.

| Pass | Code | Output |
|---|---|---|
| SQLite enforce | base 2c0e3da | `out/base_sqlite_enforce/` |
| SQLite audit | base | `out/base_sqlite_audit/` |
| Postgres enforce | base | `out/base_postgres_enforce/` |
| SQLite enforce / audit | fixed | `out/sqlite_enforce/`, `out/sqlite_audit/` (audit ran before the last enforce-only list / history changes) |
| Postgres enforce / audit | fixed | `out/postgres_enforce/`, `out/postgres_audit/` |

**Every pass logged 0 tracebacks and 0 HTTP 500s** (`api_log_counts.txt`). **SQLite and Postgres give the same status
and `pii` on every call** in enforce and in audit. In enforce, the `sweep.txt` and `fx4a.txt` diffs are empty after
removing ids. In audit, the only difference is the predicted probabilities in the scored batch file: each pass trains
its own model.

### C2: reproduced, then fixed

`sweep.txt` calls every GET as three people: Fay (Fraud member, no role, no share), Carl (masked share) and Vic (Credit
Risk viewer). Each cell is `status[W]/pii`.

```
BASE enforce:  Fay (no role, no share) got 200 on 90 of 155 GET routes for Credit Risk objects
               raw PII: /runs/{id}/scorecard 200/2 · /fairness 200/2 · /shift 200/2 · /card 200/2 · /registry/batches/{bid} 200/200
                        (Fay, Carl, Vic) · /download 200/200 (Vic) · /recourse 200/4 · /credit-report 200/2
FIXED enforce: Fay got 200 on 27 of 155: 25 public() routes (lists filtered, no team data), /registry/audit (scoped
               to her teams, M5), /agent/sessions (her own, F-X4b). Every Credit Risk object → 403 (lineage and
               annotations included). "any raw PII in a body (any caller)": none.
BASE audit:    Fay 200 on 119 of 155, only 29 of them with a would-deny header (out/base_sqlite_audit/sweep.txt)
FIXED audit:   Fay 200 on 93 object routes: 91 carry X-Ensemble-Authz: would-deny <action>; the other two are the scoped
               /registry/audit and her own /agent/sessions.
```

`fx4a.txt` §C2: the routes the sweep cannot fill. Fay gets 403 on Ana's staged upload, on the run's job, and on
`/teams/{cr}` and its members. `/jobs` lists 20 rows for Fay (tm_default samples only) and 25 for Ana.

**Masking, as Vic (viewer):** fairness `?group=ssn` → groups `['••• 1', '••• 2', '••• 3', '••• 4']`, pii 0. Scorecard,
scorecard.csv, recourse, shift, credit report (json, md), card (json, md): all pii 0, with
`masked_columns [applicant_name, dob, email, ssn]`.

**The unmask path:**
- Ana with `unmasked=1` and no reason → 400 `reason_required`.
- Ana with `reason=QA-77` → 200, pii 2 (clear), audited `dataset_rows_read (Ana, fairness, QA-77, columns)` and
  `(Ana, scorecard, …)`.
- Vic with `reason=curious` → 403 in enforce. In audit mode it is 200, masked, with would-deny `data.read_unmasked`.

**Fairness:**
- Card reports before: `[]`.
- After GETs by Fay (403), Vic and Ana: still `[]`, so the GET writes nothing.
- POST as Vic → 403; as Fay → 403; as Ana → 200. Card reports after: `['ssn']`, audit `fairness_report_saved`.
- Vic reads the card: `{'ssn': ['•••', …]}`.

### M3: reproduced, then fixed

```
BASE:  Vic / Mo / Ana GET /registry/batches/{bid} → 200 pii 200 (applicant_name, ssn, email, dob in clear); …/download
       → 200 pii 200; unmasked=1 without a reason, or as a member / viewer → 200 pii 200 (no such parameter)
FIXED: Vic / Mo / Ana preview → 200 pii 0 ('•••', dob '1950'); download → 200 pii 0, X-Ensemble-Masked-Columns
       Ana unmasked=1 no reason → 400 reason_required · Mo (member) / Vic (viewer) unmasked=1&reason → 403 forbidden
       (audit mode: 200 masked + would-deny data.export_unmasked) · Ana (maintainer) reason=TICKET-9 → 200 pii 200,
       audited dataset_rows_read (Ana, batch_download, TICKET-9, columns) and (Ana, batch_preview, …)
       Fay preview → 403 · Carl (masked share) download → 403
```

### M1: reproduced, then fixed

`decide.txt` and `fx4a.txt` §M1 (a fresh version is registered for the latter).

```
BASE enforce:  Rita POST …/review {reviewer: "Bo"} → 200 approved; audit row actor "Bo", principal_id = Bo's
FIXED enforce: Rita {reviewer: "Bo"} → 409 "You are signed in as Rita; 'by' says Bo."; {reviewer: Rita, by: Bo} → 409;
               Rita with no reviewer → 200 approved, actor Rita (her principal_id)
FIXED audit:   Rita {reviewer: "Bo"} → 200 | X-Ensemble-Authz: would-deny by_mismatch (authz_would_deny row by Rita)
```

### M2: reproduced, then fixed

`fx4a.txt` §M2. In SQLite enforce, the team starts as Ada and Ben (owners), Bo (owner), Ana (maintainer), Mo (member),
Rita (risk reviewer) and Vic (viewer).

```
                                                    BASE      FIXED
Ana (maintainer) PATCH Bo (owner) → viewer           200       403 forbidden team.settings
Ana DELETE Bo (owner)                                200       403
Ana PATCH Mo → risk_reviewer                         200       403
Ana PATCH Ana (self) → risk_reviewer                 200       403
Ben (owner) PATCH Ben (self) → risk_reviewer         200       403 self_grant
Ana DELETE Rita (risk reviewer)                      403*      403
Ana POST add Max as maintainer / risk_reviewer       403*      403 / 403
Ana POST add Max as member, → viewer, DELETE         403*      201 / 200 / 200   (below owner: team.manage)
Ben (owner) PATCH Mo → risk_reviewer, → member       403*      200 / 200
Ben PATCH Bo (owner) → maintainer; Bo DELETE Ben     403*      200; 403
Ben DELETE Ada (owner); Ben PATCH Bo → owner; Bo DELETE Ben           200; 200; 200
Bo PATCH Bo (self) → maintainer                                403 self_grant
Ada (admin) DELETE / PATCH Bo (the last owner); Bo DELETE Bo   409 / 409 / 409
/authz/check Ana model.review                        allow True  allow False
```

\* In the base run, Ana had already made herself risk reviewer, so her later maintainer actions were refused.

Audit mode lets the role changes through with would-deny `team.settings` / `team.manage`. Self-grants are refused with
403 `self_grant` in both modes. X4's `teams.py` (run last) gives 403 on 1a–1d in enforce.

### M5: reproduced, then fixed

`fx4a.txt` §M5.

```
BASE enforce:  Fay /registry/audit 187 rows {credit-risk 101, default 77, collections 5, fraud 4} (Carl, Vic: the same)
FIXED:         Fay 4 {fraud} | Carl 5 {collections} | Vic 236 {credit-risk} | Ada (admin) 335 {all four}
               summary audit: Fay [fraud], Carl [collections], Vic [credit-risk]
               Fay ?version_id=<Credit Risk version> → 403
```

### Coverage gate

`out/*/coverage.txt` (enforce and audit, both engines) and `out/gate.txt`:
`routes 316 | must 316 | covered 316 | uncovered 0`, with every method listed (GET 156, POST 136, PATCH 8, DELETE 7,
PUT 9; 27 `public()`). An added undeclared `GET /api/x/undeclared/{dataset_id}` is refused at start in enforce, and in
audit with `ENSEMBLE_AUTHZ_STRICT_COVERAGE=1`. Without strict mode, audit starts and the gate check lists it.

### U2 walk (UI for legitimate users)

`npm run build` passes after the UI change (one button and a client call). U2's `walk.cjs` ran unchanged on the fixed
API, with Vite on :5190 and `U2/setup.sh` data. Logs and call lists are in `F-X4a/walk/`.

**Audit mode (U2's mode): all 13 steps pass** (`walk_audit.log`, screenshots 01–27). The two console errors are the
walk's intended 400 (bad join) and 409 (delete under a hold). Would-deny rows from legitimate users:
- `GET /purges` without `?team=` (Ana, Ben, Carl): pre-existing P24 marker, see hand-offs;
- Carl's panels on v1, which is outside his share's scope (`default` = v2): `lineage`, `annotations`, next to the
  already-declared `contract`, `joins` and `diff`;
- 21 anonymous `GET /runs/{id}`: the walk script's own poll, which sends no actor header.

**Enforce mode** (`ENSEMBLE_AUTHZ=enforce`, the stricter check that legitimate users are not refused):
- `walk_enforce.log`: steps 1–11 pass, including Carl's catalog listing the shared dataset
  (`19-carl-catalog-enforce.png`) and his dataset page with masked rows.
- It stops at 11b, a walk-only stub that expects audit-mode `unmasked_refused`. In enforce the API answers 403 there, by
  design (P20).
- `walk_enforce_from11.log`: a scratch copy (`walk_enforce.cjs`) that accepts the 403 at 11b runs 11–13b on the same
  API, and they all pass: Carl trains the shared version into his project; Settings › Teams; legal hold; delete blocked.

Every refusal in the enforce walk (`authz_denied` rows):

```
180 anonymous GET /runs/{id} (401)        the walk script's poll, no actor header
 18 GET /purges (Ana 12, Carl 4, Ben 2)   P24's marker: admin-only without ?team= (pre-existing; U3 / F-X4c)
  4 each, Carl: /datasets/{v1}/joins, /contract (pre-existing markers), /lineage/dataset/{v1}, /annotations/dataset/{v1}
                                           panels on v1, outside the share's scope; the page loads (history narrowed)
  2 Carl GET …/grid?unmasked=1&reason=…    step 11b: the expected refusal
```

## Deviations and notes

- **Admins on member changes.** "Only an owner can change or remove an owner" is implemented as `team.settings`, which
  is owner or platform admin. This follows C §1.3 (admin ✓ on team management). Admins are still bound by the
  self-grant and last-owner rules.
- **Self changes.** A self-demotion is refused too ("nobody changes their own role"). Leaving the team is allowed.
- **M5 scope.** It uses the caller's own teams, not `visible_teams()`: `audit.read` has no shared column in C §1.3.
- **The AL / annotation `reviewer`** is not bound to the principal (annotator labels; see M1).
- **Files of other lanes touched (decorator line + import only):**
  - `agent/api.py` (F-X4b): kept their version and added `public()` to `/tools` / `/status`; merged cleanly;
  - `sqlsource/api.py` (F-X4b): `/demo`, `/datasets/{id}/versions`;
  - `registry/*` (F-X3b): decorators, plus the handler changes this phase needs: masking on recourse, card, credit
    report and batches; review decider; `/registry/audit` scoping;
  - `catalog/purge.py` (F-X4c): untouched.
- **`deps.py` merge with F-X4b:** `by_ok` (agent `by` = "Vela agent on behalf of <caller>") is now applied to every
  name field.

## Hand-offs

- **U3:**
  - lineage and annotations for a grantee: ask for the shared version's node (`default_version_id`), not the root's;
  - `GET /purges` without `?team=` is `data.read_meta` with `default="global"` (P24), so it is admin-only and every
    non-admin gets 403 / would-deny. Send `?team=<current team>`, or F-X4c lists the caller's teams.
  - for a share grantee, open contract, joins and diff with the shared version's id too (history and certifications
    now accept the root);
  - Fairness: **Save to Model Card** exists (`api.runs.saveFairness`); grey it out with
    `/authz/check run.train <run>`. Batch downloads accept `unmasked=1&reason=` (`data.export_unmasked`).
- **U2 walk script:** its `fetch('/api/runs/{id}')` poll sends no `X-Ensemble-Actor`. In enforce mode it gets 401 and
  waits out its loop.
- **All lanes:**
  - every new route needs `require()` or `public()`; GETs are no longer exempt, and enforce or
    `ENSEMBLE_AUTHZ_STRICT_COVERAGE=1` refuses to start without one;
  - a page that quotes values of a run's input calls `lake.mask.run_page(request, rec, out, by=, unmasked=, reason=)`;
  - a file of a model's rows goes through `mask.batch_mask` + `mask.csv_stream`;
  - `deps.can(request, action, kind, id)` filters list rows in enforce mode.
- **Not covered:**
  - forecast predictions CSV and the el / survival / segmented score answers carry no PII-tag masking;
  - `GET /runs/{id}/feature-importance`, `…/ladder` (`refresh`) and `…/latency` still store computed results on a GET.
    They write no values and are `run.read` now, so an outsider can no longer trigger them.
