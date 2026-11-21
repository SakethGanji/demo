# PLAN-C: teams, governance, product flows and every consumer of a dataset

Lens: multi-team tenancy and permissions, product flows for **team → project → dataset → version → tables**, and keeping
every reader of a dataset version working once storage is Parquet and inputs can span several tables. Storage layout and
the version/table manifest are mainly the storage plans' job. This plan names the few storage facts it depends on (§0) and
keeps to them, so the three plans can be merged.

Read for this plan (head c3334f2): `backend/app/datasets.py`, `db.py`, `catalog/{schema,governance,dsets,datasets_api,
api,store,lineage,compare,retention}.py`, `main.py`, `engine/{api,worker,serving,explain,ladder,...}.py`, the kind
packages (`anomaly/ forecast/ survival/ el/ segmented/ reject/ multilabel/ sqlsource/`), `registry/*`, `vela/*`,
`agent/tools.py`, `gym/export.py`, `autopilot/*`, `src/app/routes.ts`, `src/features/{build,models/data}`.

---

## 0. The compatibility idea this plan rests on: the **frame**

Today one id, `ds_xxxx`, is three things at once: a dataset version, its one table, and the training input. About 70
code sites read it through `cfg["csv_path"]` / `store.csv_path(id)` / `store.load_frame(id)` (inventory in §3). Every row
position they use (splits, `root_rows`, holdout predictions' `row`, slices, annotations, active learning, SHAP row,
paired compare) is a **position in that one file**.

The new model splits those three things apart and keeps the old id for the third:

| concept | id | what it is |
|---|---|---|
| dataset | `root_id` (unchanged: the first version's `ds_` id, keyed in `dataset_cards`) | the named thing in a project |
| dataset version | `dv_xxxxxxxx` | an immutable set of tables plus a manifest |
| table | (`dv_id`, `table_name`) | one Parquet file (or a set of parts) with its own sha256, schema and `root_rows` |
| **frame** | `ds_xxxxxxxx` (the **existing `datasets` table**) | **one training input of one version**: a single table, or a saved join over that version's tables, materialised to `frame.parquet` |

- Every consumer keeps its contract: it takes a `dataset_id` (now a frame id), reads one rectangular file and uses
  row positions in that file. Runs, registry, monitoring, lineage edges, compare and the agent keep their keys.
- **Today's single-table datasets become one version with one table named `data` and one frame.** The frame keeps the
  old `ds_` id, so no run config, edge, champion or card is rewritten (§5.4).
- **Frame row identity.** A join frame has exactly one row per row of its **spine** table, in spine order. Other tables
  join many-to-one (key uniqueness is checked) or through a declared aggregate (`sum`, `count`, `max`, `last by
  <col>`, …). So `frame.root_rows == spine.root_rows`, and the holdout, folds and paired comparisons carry over from
  the spine table unchanged.
- **Reads** go through one function, `app/frames.py: read(path, columns=None, nrows=None, compat=True)`. It handles
  `.parquet` and legacy `.csv`, so old runs whose `csv_path` points at a kept `data.csv` still work.

Storage facts this plan assumes (the storage plans may rename them; nothing here depends on the names):
`data/teams/<team>/datasets/<root>/<dv>/tables/<table>.parquet`, `.../<dv>/manifest.json` (tables, sha256 per table,
row counts, schema, `root_rows` location, parent `dv`, op), and `.../frames/<ds>/frame.parquet` plus `frame.json`
(spec, input sha256s, output sha256). The **metadata database is the source of truth**; the manifest is a
tamper-evident copy.

---

## 1. Teams and tenancy

### 1.1 Schema (SQLite now, Postgres-ready: plain types, no SQLite-only features)

```sql
-- people and service identities; today one row per typed name (auth = 'typed'), later linked to an IdP subject
CREATE TABLE principals (
  id            TEXT PRIMARY KEY,               -- pr_xxxxxxxx
  display_name  TEXT NOT NULL,
  identity_key  TEXT NOT NULL UNIQUE,           -- governance.identity_key(display_name): the four-eyes comparison key
  email         TEXT,
  idp           TEXT,                           -- NULL (typed) | 'oidc:<issuer>' | 'saml:<entity>' | 'kerberos:<realm>' | 'header'
  subject       TEXT,                           -- IdP 'sub' / NameID / user@REALM; UNIQUE (idp, subject)
  kind          TEXT NOT NULL DEFAULT 'person' CHECK (kind IN ('person','service','system')),
  is_admin      INTEGER NOT NULL DEFAULT 0,     -- replaces ENSEMBLE_ADMINS (which seeds it at startup)
  created_at    TEXT NOT NULL, last_seen_at TEXT, disabled_at TEXT
);
CREATE UNIQUE INDEX ux_principal_subject ON principals (idp, subject) WHERE subject IS NOT NULL;

CREATE TABLE teams (
  id              TEXT PRIMARY KEY,             -- tm_xxxxxxxx; 'tm_default' holds everything that exists today
  slug            TEXT NOT NULL UNIQUE,         -- folder name and URL part: [a-z0-9-]{2,40}
  name            TEXT NOT NULL,
  description     TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL, created_by TEXT NOT NULL,
  state           TEXT,                         -- NULL active | 'archived'
  policy          TEXT NOT NULL DEFAULT '{}'    -- JSON: retention, four-eyes options, PII defaults (§4)
);

CREATE TABLE team_members (
  team_id      TEXT NOT NULL REFERENCES teams(id),
  principal_id TEXT NOT NULL REFERENCES principals(id),
  role         TEXT NOT NULL CHECK (role IN ('owner','maintainer','member','viewer','risk_reviewer')),
  source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','idp_group')),
  added_by     TEXT NOT NULL, added_at TEXT NOT NULL,
  removed_by   TEXT, removed_at TEXT,
  PRIMARY KEY (team_id, principal_id)            -- one role per person per team; the history lives in audit_log
);
CREATE INDEX ix_members_principal ON team_members (principal_id);

-- IdP groups → team roles (AD / Okta / Entra groups drive membership once SSO exists)
CREATE TABLE idp_group_roles (
  idp TEXT NOT NULL, group_name TEXT NOT NULL, team_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (idp, group_name, team_id)
);

-- read-only shares of a dataset (root) or a whole project to another team
CREATE TABLE shares (
  id            TEXT PRIMARY KEY,               -- sh_xxxxxxxx
  object_kind   TEXT NOT NULL CHECK (object_kind IN ('dataset','project')),
  object_id     TEXT NOT NULL,                  -- root_id or project id
  owner_team    TEXT NOT NULL,
  grantee_team  TEXT NOT NULL,
  scope         TEXT NOT NULL DEFAULT 'certified' CHECK (scope IN ('certified','default','pinned','all')),
  pinned_dv     TEXT,                           -- scope = 'pinned': exactly this version
  pii           TEXT NOT NULL DEFAULT 'masked' CHECK (pii IN ('masked','unmasked')),
  status        TEXT NOT NULL CHECK (status IN ('pending','active','rejected','revoked','expired')),
  requested_by  TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by    TEXT, decided_at TEXT, expires_at TEXT, revoked_by TEXT, revoked_at TEXT
);
CREATE INDEX ix_shares_object ON shares (object_kind, object_id, status);
CREATE INDEX ix_shares_grantee ON shares (grantee_team, status);

-- columns added to existing tables (catalog/schema.py DATASET_COLS pattern: ALTER only when missing)
-- projects:       team_id TEXT NOT NULL DEFAULT 'tm_default', visibility TEXT DEFAULT 'team', state TEXT
-- dataset_cards:  team_id TEXT, project_id TEXT, certified_dv TEXT, contract_id TEXT
-- datasets (frames): team_id TEXT, dv_id TEXT, input_kind TEXT ('table'|'join'), table_name TEXT, join_id TEXT,
--                    frame_path TEXT, format TEXT ('csv'|'parquet')
-- runs:           team_id TEXT, created_by TEXT
-- audit_log:      team_id TEXT, project_id TEXT, object_kind TEXT, object_id TEXT, principal_id TEXT,
--                 auth TEXT ('typed'|'sso'|'header'|'system'), request_id TEXT, decision TEXT, prev_hash TEXT, row_hash TEXT
```

Indexes added: `ix_audit_team_ts (team_id, ts)`, `ix_runs_team (team_id, created_at)`, `ix_datasets_team (team_id)`,
`ix_projects_team (team_id)`. `team_id` is **denormalised** onto runs, datasets and audit rows. A list query is then
one indexed predicate, not a walk up the tree. A project's team never changes in place; moving a project is an audited
copy-and-share, not a rewrite (§1.4).

### 1.2 Roles

| role | for | can |
|---|---|---|
| viewer | stakeholders, auditors | see metadata, masked previews, runs, models and lineage; no row-level unmasked reads |
| member | modellers | + upload, make versions, train, register, request (delete / champion / certification / share), export masked |
| maintainer | data owners and leads | + archive, set default version, edit card / contract / PII tags, decide deletes, export unmasked (with a reason), manage members below owner |
| owner | team lead (≥ 1 per team, ≥ 2 recommended) | + team settings and retention policy, approve outgoing shares, add or remove maintainers and owners |
| risk_reviewer | model risk (MRM), usually **not** in the developing team's chain | review and approve model versions, approve champions and "approved for model" certifications, read everything a viewer can plus unmasked holdout rows **for validation**; cannot train, register or upload in that team |
| admin | platform (global flag `principals.is_admin`) | retention run, admin reset, legal holds, break-glass read; **still bound by four eyes**: an admin cannot approve their own request unless `ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE` (kept, audited "self-approved") |

Role order for "at least": viewer < member < maintainer < owner. `risk_reviewer` is a separate axis, not above or
below them. A person can be `member` of team A and `risk_reviewer` of team B. Being a `risk_reviewer` on a team
together with any building role on the same team is allowed but flagged in the team settings ("not independent"). A
team policy `require_independent_reviewer` makes it a refusal.

### 1.3 Permission matrix

Scope: the action's object resolves to an owning team (§1.6). "Shared" means the caller's team holds an active share
of that object.

| action (policy key) | viewer | member | maint. | owner | risk rev. | shared (read-only team) | admin | four eyes |
|---|---|---|---|---|---|---|---|---|
| `data.read_meta` (list, card, history, lineage, profile) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (shared scope only) | ✓ | |
| `data.preview` (grid, rows, diff rows) — masked | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `data.read_unmasked` (PII columns in clear) | | | ✓ | ✓ | ✓ (validation) | only if share `pii='unmasked'` | break-glass | reason required, always read-audited |
| `data.upload` (new dataset in a project) | | ✓ | ✓ | ✓ | | | | |
| `data.version` (upload-as-version, delta, edit, Data Doctor fix, Feature Lab, relabel, monitoring window) | | ✓ | ✓ | ✓ | | | | |
| `data.set_default` / `data.card` / `data.contract` / `data.tag_pii` | | | ✓ | ✓ | | | | |
| `data.archive` / unarchive | | | ✓ | ✓ | | | ✓ | |
| `data.delete` request | | ✓ | ✓ | ✓ | | | ✓ | requester ≠ decider |
| `data.delete` decide | | | ✓ | ✓ | | | ✓ | ≠ requester; refused under legal hold |
| `data.certify` request | | | ✓ | ✓ | | | | |
| `data.certify` decide (`certified`) | | | | ✓ | | | | ≠ requester |
| `data.certify` decide (`approved_for_model`) | | | | | ✓ | | | ≠ requester |
| `data.share` request / decide | | | request | decide | | | | ≠ requester |
| `data.export` masked | | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `data.export` unmasked | | | ✓ | ✓ | | | break-glass | reason; read-audited |
| `run.train` (any kind; also join materialisation) | | ✓ | ✓ | ✓ | | ✓ **into the caller's own project**, reading the shared version | | |
| `model.register` | | ✓ | ✓ | ✓ | | | | |
| `model.review` (approve / reject) | | | (team policy `maintainer_can_review`) | | ✓ | | | ≠ registrant and ≠ run creator |
| `model.promote` Staging | | ✓ | ✓ | ✓ | | | | |
| `model.promote` Production | | | ✓ | ✓ | | | | ≠ registrant unless `ENSEMBLE_ALLOW_SELF_PROMOTE`; leak gate kept |
| `model.retire` request / decide | | req | ✓ | ✓ | ✓ decide | | ✓ | ≠ requester |
| `champion.request` | | ✓ | ✓ | ✓ | | | | |
| `champion.decide` | | | | ✓ | ✓ | | | ≠ requester |
| `retention.plan` | | | ✓ | ✓ | | | ✓ | |
| `retention.run` (team scope) | | | | ✓ | | | ✓ | global run: admin |
| `legal_hold.place` | | | | | | | ✓ (and compliance principals) | |
| `legal_hold.release` | | | | | | | ✓ | ≠ placer |
| `team.manage` (members, policy) | | | below owner | ✓ | | | ✓ | removing the last owner refused |
| `admin.reset` | | | | | | | ✓ | another admin (existing `admin_requests`) |
| `audit.read` (team) | ✓ (own team) | ✓ | ✓ | ✓ | ✓ | | ✓ (all) | |

### 1.4 Sharing and certification across teams

- **A share is read-only.** The grantee team can see metadata, take masked previews and **train in its own projects on
  the shared versions**. Runs record a `trained_on` edge across teams, and the owning team sees that in the dataset's
  "Used by" panel. The grantee can never make a version of the owner's dataset. If it wants changes, it "forks": it
  creates its own dataset whose first version is a **copy-free reference** (the manifest points to the owner's table
  files by sha256, see §4.3 purge), with `derived_from` lineage to the owner's version.
- **Scope** decides which versions are visible: `certified` (default: only versions that carry a live certification),
  `default` (the owner's current default), `pinned` (one `dv`) or `all`.
- **Certified dataset.** A certification belongs to one version, not to the dataset, so a new upload is never
  certified automatically. A maintainer requests it, and the owner (`certified`) or a risk reviewer
  (`approved_for_model`) decides. It requires that the active contract passes (§4.5) and that the PII tags are
  reviewed (`pii_reviewed_at` is set). The catalog shows a ✓ badge. It **expires** after `policy.certify_days` (default
  365). Certifying a new version supersedes the old one, which stays readable by existing runs.
- **Moving a project between teams** is not a rewrite. The source team shares the project to the target with
  `scope=all`, the target forks what it needs, and the source archives. Both steps are audited. This keeps team_id
  immutable on every row, so per-team audit is never rewritten.

```sql
CREATE TABLE certifications (
  id TEXT PRIMARY KEY, root_id TEXT NOT NULL, dv_id TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('certified','approved_for_model')),
  status TEXT NOT NULL CHECK (status IN ('pending','active','rejected','superseded','expired','revoked')),
  contract_id TEXT, contract_result TEXT,         -- JSON snapshot of the checks at request time
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '', expires_at TEXT
);
CREATE INDEX ix_cert_root ON certifications (root_id, status);
```

### 1.5 Four eyes, mapped onto roles

The existing code already has four requester/decider tables (`dataset_requests`, `version_requests`, `champions`,
`admin_requests`) and `governance.same_person`. They are kept. They gain one generic table for the new kinds, and every
decide path calls one check:

```python
# app/authz/policy.py
def decide_check(p: Principal, action: str, req: Request_, obj_scope: Scope) -> None:
    rule = FOUR_EYES[action]           # e.g. {"decider": {"owner","maintainer"}, "not": ["requested_by"]}
    if not has_role(p, obj_scope.team_id, rule["decider"]) and not (rule.get("admin") and p.is_admin):
        deny(403, f"{action}: needs {' or '.join(rule['decider'])} in {obj_scope.team_slug}")
    for field in rule["not"]:          # requested_by, registered_by, run_created_by, placed_by
        if same_person_p(p, req[field]): deny(409, "a second person must decide")   # same text as today
```

`same_person_p` compares `principal_id` when both sides have one, and falls back to `identity_key` for typed names. It
stays as strict as today: if either key matches, it is the same person. Once SSO is on, comparing ids makes the
lookalike-name folding unnecessary, but it is still applied, so it never gets weaker.

```sql
CREATE TABLE approval_requests (            -- certify, share, legal-hold release, unmasked export (if team policy asks)
  id TEXT PRIMARY KEY, action TEXT NOT NULL, object_kind TEXT NOT NULL, object_id TEXT NOT NULL, team_id TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn','used','expired')),
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT ''
);
```

### 1.6 Enforcement design: works now with typed names, switches on with SSO

**Package `backend/app/authz/`:**

```
authz/
  identity.py    Principal, IdentityProvider protocol, the providers
  scope.py       object → (team_id, project_id, root_id): cached resolvers per kind
  policy.py      ACTIONS: action → min role / allowed roles / share rule / four-eyes rule; check(); decide_check()
  middleware.py  resolves the principal per request into a contextvar; mode handling; request_id
  deps.py        FastAPI dependencies: require(action, obj=...) and visible_teams()
  coverage.py    walks app.routes: which mutating / row-reading routes carry a require(); GET /api/authz/coverage
```

**Identity provider interface**

```python
@dataclass(frozen=True)
class Principal:
    id: str; display_name: str; identity_key: str
    auth: Literal["typed", "oidc", "saml", "kerberos", "header", "system"]
    groups: tuple[str, ...] = (); is_admin: bool = False

class IdentityProvider(Protocol):
    name: str
    def authenticate(self, request: Request) -> Principal | None: ...   # None = no credential on this request
    def challenge(self) -> Response | None: ...                         # 401 + WWW-Authenticate, or a redirect to login
```

Providers are configured as an ordered chain (`ENSEMBLE_IDP=oidc,typed`). The first one that returns a principal wins.

| provider | how | when |
|---|---|---|
| `TypedNameProvider` | `X-Ensemble-Actor` (today's header). Upserts `principals` by `identity_key` with `auth='typed'` | now; the POC |
| `TrustedHeaderProvider` | a reverse proxy authenticates the user and sets `X-Remote-User` / `X-Remote-Groups`. Accepted **only** from `ENSEMBLE_TRUSTED_PROXIES` (CIDRs) and with a shared header secret | the fastest SSO path inside a bank: oauth2-proxy (OIDC), Apache `mod_auth_mellon` / Shibboleth SP (SAML), `mod_auth_gssapi` / nginx SPNEGO (Kerberos) all end in this one provider |
| `OIDCProvider` | `Authorization: Bearer <JWT>` checked against the issuer's JWKS (cached; `iss`, `aud`, `exp`, `nbf`); claims `sub`, `email`, `name`, `groups`. The UI does Authorization Code + PKCE with a public client | direct OIDC (Entra ID, Okta, Keycloak, Ping) without a proxy |
| `SAMLProvider` | **not in-app.** SAML is terminated by an SP proxy (above) or bridged to OIDC by the IdP (Entra, Okta and Keycloak all do this). An in-app SAML library adds XML-signature risk for no gain | only through the proxy |
| `KerberosProvider` | SPNEGO `Authorization: Negotiate` checked with python-`gssapi` against a keytab; principal `user@REALM`; groups through an LDAP lookup (cached 10 min) | Windows-domain desktops where neither a proxy nor OIDC is available |
| `ServiceTokenProvider` | `Authorization: Bearer ens_svc_<random>`, hashed in a `service_tokens` table, bound to one principal of kind `service` and one team | drop-folder watcher, schedulers, CI |

Group sync: on every authenticated request, a principal's `idp_group` memberships are reconciled from `groups`
against `idp_group_roles`. Manual memberships are never removed by the sync. Changes are audited.

**Modes** (`ENSEMBLE_AUTHZ`, default `audit` after phase C2):

| mode | typed names | behaviour |
|---|---|---|
| `off` | yes | today's behaviour; decisions not computed |
| `audit` | yes | every `require()` computes the decision. A denial is written to `audit_log` (`action='authz_would_deny'`, decision JSON) and the request goes through, so the matrix is tuned on real traffic. The response carries the header `X-Ensemble-Authz: would-deny <action>` |
| `enforce` | **refused at startup** unless `ENSEMBLE_AUTHZ_ALLOW_TYPED=1` (demo only, logged loudly) | denials return 403 `{code:'forbidden', action, team, needed}`. Requests without credentials return 401 plus the provider's challenge |

**Enforcement point.** It is a dependency on each route, not a path-pattern table in middleware. The object id is
inside the path or the body, and a pattern table drifts away from the routes. The middleware only establishes *who*.
The route says *what*:

```python
@router.post("/datasets/{dataset_id}/edit", status_code=201,
             dependencies=[require("data.version", obj=path("dataset_id", kind="frame"))])
@router.post("/runs", dependencies=[require("run.train", obj=body("dataset_id", kind="frame"),
                                            also=[("run.train_into", body("project_id", kind="project"))])])
```

- `require` resolves scope through `scope.py`: frame → dv → root → project → team; run → project; model version → run;
  batch → version; champion → project. The resolvers are cached per request. At the measured **4 µs per role
  resolution** (SQLite, 100k memberships, 20k projects, 5k shares, in this container), this costs nothing next to a
  request.
- **List endpoints filter** with `visible_teams()`: the caller's teams plus teams sharing to them. The filters apply to
  `GET /api/datasets`, `/api/runs`, `/api/registry*`, `/api/data/search`, `/api/projects`, monitoring stream, Home
  and audit. In `off` / `audit` mode the lists are not filtered, but every row carries `team` so the UI can group.
- **Background work** (runner, heavy pool, retention, drop-folder watcher, autopilot) runs as principal
  `system:<job>`. The *initiating* principal is stored in the job (`cfg["by"]` exists already; it is renamed
  `cfg["by_principal"]` with `by` kept). Authorisation happens once, when the work is queued, never inside the worker.
- **The Vela agent** (`agent/tools.py` → `call(ctx, ...)`) runs every tool through the same ASGI app. It now passes
  the session owner's principal explicitly (a signed internal header that only the in-process client can set), never a
  service identity. The agent can therefore never do more than its user, and `require()` applies to it unchanged.
- **Coverage gate** (instead of a test suite): `GET /api/authz/coverage` lists every route with its method and
  dependency marker. Every non-GET route and every row-returning GET (`/rows`, `/grid`, `/export`, holdout
  predictions, scores CSV, batch download, SHAP row) must carry `require()` or the explicit `public()` marker. At
  startup the server logs the uncovered routes, and in `enforce` mode it **refuses to start** if any are uncovered.
- `governance.current_actor()` keeps working: it returns `principal.display_name`. The ~40 call sites that take
  `by=` from the body continue to work. In `enforce` mode, a body `by` that differs from the principal is refused
  (409 "you are signed in as X"). This removes the one remaining spoof.

### 1.7 Audit per team

- `audit_log` rows gain `team_id, project_id, object_kind, object_id, principal_id, auth, request_id`. `schema.audit()`
  fills them from the request's scope. The existing `details` JSON is kept.
- **Hash chain per team.** `row_hash = sha256(prev_hash || canonical_json(row))`, where `prev_hash` is the team's
  previous row (`catalog_meta['audit_head:<team>']`). `GET /api/teams/{t}/audit/verify` walks the chain and reports
  the first break. Inserts are serialised per team (SQLite single writer already serialises; Postgres uses
  `SELECT … FOR UPDATE` on the head row). This is cheap tamper evidence for model-risk examiners, without a ledger
  product.
- `GET /api/teams/{t}/audit?action=&object=&actor=&from=&to=&format=csv` reads the audit; team viewers and above, and
  admins for every team. Read-audit rows (`governance.read_audit`) carry team_id too, so "who looked at the bureau
  table's rows" is one query per team.

---

## 2. Product flows for the new hierarchy

UI rules kept: the five nav places (Home, Build, Models, Registry, Vela; Settings in the footer), one primary action per
screen, and API-only features labelled as such. The changes are:

- a **team switcher** in the sidebar header (not a nav item);
- **Data** moves from a tab under Models to a **section of Build**, because Build is "where data comes in and models
  start". Build gets a segmented control: `Start` (today's intake) · `Data` (catalog, datasets).

### 2.1 Create a team or project

```
┌ Sidebar ────────────┐   ┌ Settings › Teams ─────────────────────────────────────────────┐
│ [Credit Risk ▾]     │   │ Teams you are in                                    [New team] │ ← primary
│  Home               │   │  Credit Risk     owner   12 members  4 projects                 │
│  Build              │   │  Collections     member   8 members  2 projects                 │
│  Models             │   │ Team › Credit Risk                                              │
│  Registry           │   │  Members  [name/email…] [role ▾] Add      (IdP groups: read-only)│
│  Vela               │   │  Policy   retention 730 d · cert expiry 365 d · indep. reviewer ☑│
│ ─ Settings ─        │   └────────────────────────────────────────────────────────────────┘
└─────────────────────┘
```

- A new team requires an admin, or `policy.self_service_teams`. Its creator becomes the owner.
- A project is created from Build › Data with **New project** (name, description). Its team is the current team.
- API: `POST /api/teams`, `POST /api/teams/{t}/members`, `POST /api/projects {team_id}`.

### 2.2 Upload: a multi-sheet Excel file or several files as one dataset version

```
Build › Data › Credit Risk / PD 2026 › [Upload]
┌ Upload a dataset version ──────────────────────────────────────────────────────────────┐
│ Drop files: applications.csv  bureau.parquet  book.xlsx (3 sheets)                       │
│ Tables in this version                                                                   │
│  ☑ applications    from applications.csv          1,204,331 rows  38 cols  key: app_id ▾ │
│  ☑ bureau          from bureau.parquet            1,198,002 rows  61 cols  key: app_id ▾ │
│  ☑ payments        from book.xlsx › Payments      9,882,113 rows   7 cols  key: — ▾      │
│  ☐ Notes           from book.xlsx › Notes                 12 rows  (unchecked: not data) │
│ Dataset: ( ) new  (•) next version of [PD 2026 ▾]     Reason / ticket: [CHG-1182      ]  │
│                                                         [Check files]  ← primary          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- Each sheet and each file becomes one table. The table name defaults to the sheet or file stem (snake_cased) and
  can be edited. Matching **by table name** to the parent version's tables is how "next version" knows which table is
  which. Unmatched tables are shown as `added` or `removed`.
- Excel: every sheet is read with its header row detected. Merged header cells and empty sheets get a warning.
  Columns that look like ids (all digits with a leading zero) are kept as text; this fixes today's CSV pain.
- The upload is **staged** (`POST /api/uploads` returns a staging id). Then *Check files* runs the preview and contract
  checks (§2.4) per table, and *Create version* commits. There is one primary action per step.
- `POST /api/uploads` is chunked or multipart to staging. `POST /api/uploads/{u}/commit` is the only write, and it is
  idempotent on `(u)`.

### 2.3 Upload as next version, and delta files

- **Full extract** (default): the file replaces the table. Rows are matched by the table's row key, else by row hash
  (today's DL1 `match_rows`, moved to DuckDB). Matched rows keep their root row, holdout and fold.
- **Delta file**: the user ticks *This file is a change set for* `[table ▾]`, with `key = [app_id]` and an op column
  (`I/U/D`, or `_op`, chosen) or the rule *rows here replace / are added*. The new table is computed as
  `parent ⊖ deleted keys ⊕ upserts`, in DuckDB, and written as a full Parquet table (the storage plans may store only
  the delta file and compact it; the flow does not change). A delta without a key column is refused with a message.
  Keys duplicated in the delta are refused, and the first ten are listed.
- Delta preview shows `+ inserted  ~ updated  − deleted  = unchanged` per table, plus keys in the delta that are not in
  the parent (an update to a missing row counts as an insert, with a warning).
- Tables not in the upload are **carried forward by reference** (same file, same sha256), so a monthly bureau refresh
  makes a version that shares the applications table byte for byte.

### 2.4 Preview and contract checks per table

```
Check files › PD 2026 v8 (from v7)
┌ applications  ✓ contract passes   1,204,331 rows (+12,004 / −310 / ~2,113)              ┐
│ bureau        ⚠ 2 warnings        column 'util_ratio' type float → text (34 values 'N/A') │
│               ⚠                   'score' null share 0.4% → 6.1% (contract max 2%)        │
│ payments      ✗ blocks            key (app_id, pay_dt) not unique: 18 duplicates          │
│ [Show rows ▾ per table: first 50, masked PII]                                             │
│ This version would be: not the default (warnings) · certification lost (new version)      │
│                                        [Create v8]  ← primary  (disabled while ✗ blocks)  │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

- Checks are warnings, except where the dataset's **contract** (§4.5) marks them `block`. Today's contract checks
  (`dataset_versions.contract_checks`: columns added/removed, type changes, target missing, null shift, identical file)
  run per table, plus key uniqueness, referential checks between tables (foreign key coverage %) and row-count bounds.
- Masked preview: PII-tagged columns are shown masked (§4.3), for everyone below `data.read_unmasked`.

### 2.5 Edit a table

The DL3 edit grid stays as it is, scoped to one table: `Data › dataset › v8 › bureau › Edit`. Typed ops (set cell,
fill, delete rows, rename, recode…) go to `POST /api/datasets/{root}/versions/{dv}/tables/{t}/edit`. The edit makes
`v9`, in which only `bureau` changes and the other tables are carried by reference. Its preview, its reason field
(required) and its single primary action (*Create v9*) are unchanged from DL3.

### 2.6 Choose a training input: one table, or a saved join

```
Build › New Model › Data
┌ What does the model learn from? ──────────────────────────────────────────────────────┐
│ Dataset  [PD 2026 ▾]  Version [v8 ★default ✓certified ▾]                                │
│ (•) One table      [applications ▾]                                                     │
│ ( ) Saved join     [apps + bureau + payments agg ▾]   [New join…]                       │
│ Rows: 1,204,331 (one per application)  Columns: 38 + 59 + 3                             │
│                                                       [Continue: target & split →]      │
└─────────────────────────────────────────────────────────────────────────────────────────┘

New join (drawer)
  Spine (one row per)  [applications ▾]
  + [bureau ▾]    on applications.app_id = bureau.app_id     many-to-one ✓ (checked: 0 dup keys)
  + [payments ▾]  on app_id     many-to-many → aggregate: [sum(amt) ▾][count ▾][max(days_late) ▾]
  Prefix columns from joined tables: ☑ (bureau__score)
  Coverage: bureau 99.5% of applications, payments 92.1%        [Save join]  ← primary
```

- A **saved join** is a named spec on the dataset (not on a version), so it applies to every version that has its
  tables. Its spec is JSON: spine, joins (table, keys, `kind: many_to_one | aggregate`, aggregates, prefix) and an
  optional `where` on the spine (a DuckDB expression, validated against the schema). There is no free SQL: SQL stays
  in `sqlsource` for database sources.
- Choosing a join for a version **materialises a frame** (one DuckDB `COPY … TO frame.parquet`, measured **3.2 s for
  a 10M-row spine with a 10M many-to-one table and a 30M-row aggregated table** in this 4-vCPU container). The frame is
  cached by `(dv, join_id, join_spec_sha)`, so the same choice never materialises twice, and the frame id is what the
  run gets.
- Columns used to join are not features by default. They are offered as group-split candidates (a customer id
  joined in is the classic group).

### 2.7 Diff versions per table

```
Data › PD 2026 › Compare v7 ↔ v8
  applications  +12,004  −310  ~2,113 rows   columns: =          [rows ▸]
  bureau        +0       −0    ~388,121       columns: util_ratio float→text   [rows ▸]
  payments      (same file, sha 9c1e…)                                            —
  Joins: "apps + bureau + payments agg" frame differs in 390,234 rows (spine-paired)
```

Per-table diff is today's `dsets.diff` (by root row), with DuckDB doing the matching. A diff of the same file is
skipped by sha256. Row pages are masked. Unchanged tables are named as such.

### 2.8 Lineage per table and per join

The lineage tree (`catalog/lineage.py`) gets two node kinds, `table` (dv, name) and `frame`, and two relations:
`table_of` (dv → table) and `built_from` (table → frame, meta: join id and spec sha). A run hangs off its frame, as it
hangs off its dataset today. The view answers *which bureau file fed model PD-v12*: run → frame → (applications @ v8
sha…, bureau @ v7 sha…) → upload event, file name, uploader and reason. §4.6 shows how that becomes the model-risk
document.

### 2.9 Data catalog and search across teams

```
Build › Data
┌ [search: "bureau score"            ] Team [all I can see ▾] ☐ certified only  ☐ has PII ┐
│ PD 2026 · Credit Risk · ✓ certified v8 · tables: applications, bureau, payments · 41 runs│
│ Bureau monthly · Collections (shared with you, masked) · v23 · bureau · 3 runs           │
│                                                         [Upload]  ← primary              │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- This extends `GET /api/data/search` (dsets.search) to search table and column names, descriptions, tags, owners and
  certification, filtered by `visible_teams()`. Datasets from other teams that are **not** shared appear only as
  "exists, owned by Team X: [Request access]". That creates a share request when `policy.discoverable` is on, which
  is the default for metadata only. When it is off, they are not shown at all.
- Index: a `catalog_search` table (root_id, team_id, text) is rebuilt on each version commit. SQLite FTS5 is used
  now, and Postgres `tsvector` later. At thousands of datasets × hundreds of columns, this stays well under 50 ms.

### 2.10 Drop-folder ingestion (option)

- Per project: `Settings › Project › Drop folder: \\share\credit\pd2026\in` with the rule
  `file pattern → dataset / table / full|delta / key`.
- A watcher (a separate process, `python -m app.dropwatch`) holds a team **service token**, which carries
  `data.version` in that project only. It polls (no inotify on SMB), waits until the file size is stable for 2
  intervals, and copies the file to staging. It then calls the same `POST /api/uploads` → `/check` → `/commit` path
  as the UI. If the checks block, it does not commit: the result goes to the project's inbox, and a person commits
  or discards. Processed files move to `done/` or `rejected/` with a `.result.json` beside them.
- A file changed in the folder after ingestion is a new file (new sha256), never an edit of a version.

---

## 3. Every consumer of a dataset version: inventory, change and risk

**The pattern of change.** Almost every consumer either (a) calls `store.load_frame(id)` or `store.csv_path(id)`, or
(b) reads `pd.read_csv(cfg["csv_path"], …)` inside a run or job. The plan:

1. `app/frames.py` provides `path(frame_id)`, `read(path, columns=None, nrows=None, compat=True)`,
   `columns(path)` and `n_rows(path)`. The `usecols=lambda` pattern becomes `columns=[c for c in columns(path) if …]`.
2. `store.csv_path()` is renamed `frame_path()`, with a deprecation alias. `cfg["csv_path"]` is kept as the key, and
   `cfg["frame_path"]` is added with the same value. For new runs it points at a `.parquet` file. Every
   `pd.read_csv(cfg["csv_path"]…)` becomes `frames.read(cfg["csv_path"]…)`. A grep gate in each phase's verification:
   `grep -rn "read_csv(.*csv_path\|read_csv(cfg\[" backend/app | grep -v batch` must print nothing.
3. **`compat=True`** reproduces what the engine saw from CSV:
   - datetime and date columns become ISO strings;
   - nullable ints become float64 when they hold nulls;
   - the Arrow string type becomes object.
   Consumers move to `compat=False` one by one once checked; the first is the profiler, so the "date" kind is
   detected from the type. Without this, the first Parquet run would silently change features (dates becoming
   datetime64 changes AutoGluon's feature generators), which would break paired comparisons with older runs.
4. **Batch inputs that users upload for scoring** (`registry/api.py:978` `input.csv`, anomaly and forecast batches,
   credit tape, annotations upload) stay CSV in and CSV out. They are not dataset versions. They also accept
   `.parquet` uploads via `frames.read`.
5. **Hashes.** `worker.load_frames` hashes the bytes it reads (DL3 M1). For a frame, it hashes `frame.parquet`, and
   the run records `frame_sha256` plus `input_sha256s` (one per table) from `frame.json`. `data_gate` compares
   `frame_sha256` with the stored one, and the table hashes against the manifest.

| # | consumer | where (file:line at c3334f2) | change | risk |
|---|---|---|---|---|
| 1 | AutoGluon training (classic) | `engine/worker.py:46 load_frames` (read + hash), `:631`, `:840 data_gate`; `engine/api.py:306,379,399,419,526` (label read, cfg build, pseudolabel) | read through `frames.read(compat=True)`; hash the frame file; cfg adds `frame_path`, `dv_id`, `input_kind`, `join_id`, `input_sha256s`; the label-only read uses `columns=[label]` | **M**: dtype drift if compat is skipped; memory is unchanged (0.71 GB for 10M×9 read into pandas, 2.2 s measured) |
| 2 | splits and holdout | `splits.py` (positions over n rows), `datasets.py:197-283 ensure_splits/split_options`, `engine/serving.py:140 holdout_frame`, `engine/heavy.py:21,39,68`, `engine/custom_ensemble.py:95` | splits belong to the frame (`frames/<ds>/splits/<key>.json`). A join frame **inherits the spine table's split**: same positions, because the frame is in spine order. `holdout_frame` reads the frame | **H** if any join could change row count or order, hence the spine rule (§0), enforced at materialisation: `count(frame) == count(spine)`, else refuse |
| 3 | Data Doctor | `data_doctor_api.py:74,77,121,176`; `data_doctor.py:1174` (label issues read `csv`) | reads the frame for checks. **Apply** makes a new *version* where the fix lands on the **spine table** (or on the named table for a column that came from a join, mapped through the prefix); the frame is re-materialised | **M**: a fix on a joined column must find its source table; a fix on an aggregate column is refused ("fix the source table") |
| 4 | Model Doctor | `vela/doctor.py:145`, `vela/findings.py:90,509` | `frames.read` on `cfg["csv_path"]` | L |
| 5 | What's missing | `vela/missing.py:427` | same | L |
| 6 | Feature Lab | `featurelab_api.py:52`, `featurelab.py` | reads the frame. Its result is a new version in which the engineered columns are added **as a new table `features_<n>`** keyed by the spine key, plus a saved join that includes it. Spine tables are not rewritten | **M**: it changes how Feature Lab versions look; the old single-table path stays for `input_kind='table'` without a key |
| 7 | ladder | `engine/ladder.py:375` (`load_frames`) | none beyond #1 | L |
| 8 | review experiments | `vela/experiments.py:156,226,357,376` | `frames.columns` / `frames.read`; `:376` builds paths next to `csv_path` (it must use the frame dir) | L |
| 9 | Explain / SHAP / fairness / slices / shift / learning curve / active learning / annotations | `engine/explain.py:189,331,437,529`; `fairlending/api.py:47`; `engine/slices_api.py:82,197,269`; `engine/shift_api.py:100-155,438-445`, `engine/shift.py:210`; `engine/learning_curve.py:311,316`; `engine/active_learning.py:251`, `_api.py:250,309,330`; `engine/annotations.py:138,272`, `_api.py:121,245` | `frames.read`, `usecols` → `columns`. `shift.py` takes `{"csv": path}` references, which become `{"path": …}` with `frames.read`. `annotations.py:138` hard-codes `csv_path("support-tickets")` → `frame_path` | L (mechanical, ~30 sites) |
| 10 | anomaly | `anomaly/api.py:95,111,127,271-377`, `anomaly/core.py:246,445` (scoring a batch CSV stays CSV) | frame read; `nrows=20000` → `frames.read(nrows=)` (pyarrow batch slice) | L |
| 11 | forecast | `forecast/api.py:87,121,157`, `forecast/core.py:100,173,889` | `read_long` reads the frame. **The time column arrives as a string under compat** (today's behaviour), and moving it to native datetime happens later, under its own check | M (time parsing) |
| 12 | survival / EL / segmented / reject inference / multilabel | `survival/api.py:76,89,129`, `core.py:203`; `el/api.py:87,100,151,205-219`, `core.py:259`; `segmented/api.py:91,104,156`, `core.py:157,248,303`; `reject/api.py:73,87,144`, `core.py:196`; `multilabel/api.py:69,82,133`, `core.py:203` | `detect()` and the run cfg use the frame. `segmented/core.py:248 train_ref` stores `csv_path`, which is kept; reading goes through `frames.read` | L |
| 13 | SQL source | `sqlsource/core.py:267-282` (demo DB built from three samples' `data.csv`), `sqlsource/api.py:117,139,160` | `create_from_frame` writes a one-table version (`table_name='query'`) with Parquet, so types come from the database instead of a CSV round trip. A refresh is upload-as-next-version of that table. The demo DB builder reads frames | L |
| 14 | import a predictor | `engine/importer.py:65,132` | cfg gets `frame_path` | L |
| 15 | registry build and model card | `registry/build.py:234,477,485,532,635-728,753,763`; `registry/card.py:99` | `data_hash = file_hash(frame)`, plus **`inputs: [{table, dv, sha256}], join: {id, spec_sha}`** in `hashes`. `data_file` becomes the frame name. The card's "Data" section lists the tables (§4.6) | **M**: the card format changes; old cards keep their `data_file` |
| 16 | registry batches and monitoring | `registry/api.py:200,951-1017`, `registry/scoring.py:86,105,170,423,661,742,763,822`, `registry/monitoring_api.py:141-151,273,291,538-566`, `registry/changes_api.py:65,98-108`, `registry/changes.py:156`, `registry/drift.py:166`, `registry/credit_report_api.py:93-211`, `engine/shift_api.py:483` | the training reference (`cfg["csv_path"]`) is read through `frames.read`. **Scoring a batch of a join-trained model needs the same join**: the batch upload accepts either a flat file with all model columns (today) or **several files / a multi-sheet book** that is joined with the model's stored join spec at scoring time (`registry/scoring.py` calls `frames.materialise(spec, tables)`). The monitoring window (`monitoring_api.py:566 record_version`) becomes a version of a one-table dataset | **H** for join models (training/serving skew if a batch is joined differently). Mitigation: the join spec and its sha are frozen in the model version; a batch is refused if its tables' schemas do not satisfy the spec |
| 17 | batch scoring for other kinds | `registry/scoring.py:404-575 score_flow_batch` | same as #16, per kind | M |
| 18 | export | `catalog/datasets_api.py:222-253` (CSV file or zip with data.csv + lineage + manifest), `dsets.py:695 export_manifest` | export per **table or frame**: CSV (or Parquet, or xlsx with one sheet per table), PII **masked by default**, unmasked only with `data.read_unmasked` and a reason. The zip gains one file per table plus `manifest.json` with table sha256s; `how_to_check` covers each file | M (masking must be applied in the DuckDB `COPY`, never after) |
| 19 | grid / rows / preview | `catalog/datasets_api.py:255-288 grid`, `main.py:298 rows`, `main.py:243-267 intake` | DuckDB `SELECT … LIMIT/OFFSET` on the table or frame with masking expressions (**18 ms** for a masked page at offset 5M of a 10M-row frame) instead of loading the whole CSV | L (a big win) |
| 20 | lineage tree | `catalog/lineage.py:54 tree`, `catalog/schema.py` edges | new node kinds `table`, `frame`, relations `table_of`, `built_from(table→frame)`; `src_kind/dst_kind` CHECK widened (SQLite: table rebuild once, as done for champions in F-P2) | M (schema rebuild) |
| 21 | paired compare | `catalog/compare.py:47 root_info`, `dsets.py:244 _pairs`, `:285` | `root_rows` come from the frame (the spine's). Two frames pair when their spine tables share a root table lineage. Different joins over the same spine **still pair** (same rows, different features), which is the experiment users want | **M**: frames over different spines must say "unpaired: different row grain" |
| 22 | retention | `catalog/retention.py` (runs, predictors, pins), `dsets.py:238 _retention_days`, `:504-602` delete and freeze holdout | the team's policy (§4.1); a table file is purged only when **no live manifest in any team** references its sha256 (§4.3) | **H** (shared files) |
| 23 | integrity | `dsets.py:626 verify`, `:651 verify_all`, `:660 integrity_gate`, `datasets_api.py:333 adopt-disk-file`, `catalog/store.py:25,37` | verify hashes each table file against the manifest and the DB, and each frame against `frame.json`. A changed table file is MISMATCH for **every version that references it**. Adopting a disk file makes a new version of that one table | M |
| 24 | Vela agent tools | `agent/tools.py:508 _dataset_csv`, tool map `:719-739`, `_guard_path :71` | `_dataset_csv` serialises the frame (masked). New tools: `list_tables`, `preview_table`, `choose_input` (frame for a table or join). `_guard_path` adds the new write paths to the confirm list. Calls carry the session principal (§1.6) | M |
| 25 | autopilot | `autopilot/engine.py:272,364,416,427,505,593,943`, `autopilot/audit.py:66,200` | frame reads. Relabel / drop / synthetic versions (`create_version`) write the change to the spine table | L |
| 26 | gym export (`export.py` script) | `gym/export.py:62,73,193-230` | the exported script reads `DATA_PATH` with `pd.read_parquet` when it ends in `.parquet`, and `data_sha256` becomes the frame hash. The hold-out bitmask is unchanged (positions) | L |
| 27 | samples | `datasets.py:349 ensure_samples`, `samples*.py` | written as one-table Parquet versions. **Add one multi-table sample** (applications + bureau + payments) so every flow above has a real case | L |
| 28 | admin reset | `reset.py` | also clears teams/shares only on a "factory" reset; keeps `principals`, `teams`, audit (like the audit log today) | L |

Order of risk: #2/#21 (row identity), #16 (serving joins), #22 (purge of shared files), #1 (dtype compat), #18
(masking in export).

---

## 4. Governance details

### 4.1 Retention per team

`teams.policy.retention`:

```json
{"versions": {"keep_days": 730, "keep_min": 5, "keep_certified": true, "keep_used_by_live_models": true},
 "frames":   {"evict_unused_days": 30},
 "runs":     {"slim_days": 90, "evict_days": 365},
 "staging":  {"days": 7}}
```

- Frames are a cache: they can be rebuilt from tables plus the spec, and their sha256 is checked on rebuild. They are
  evicted after N days unused **unless** a registered model version or an open review uses them.
- Versions follow today's rule: archive, then a four-eyes delete. A version is pinned if a run, a live model, a
  champion, a certification or a legal hold uses it (`retention.pins` and `dsets.blockers`, extended).
- `POST /api/retention/plan?team=` and `/run?team=`: an owner can run their own team; admins can run all teams. A
  global plan shows bytes per team.

### 4.2 Legal hold

```sql
CREATE TABLE legal_holds (
  id TEXT PRIMARY KEY, scope_kind TEXT NOT NULL CHECK (scope_kind IN ('team','project','dataset','version','run','model')),
  scope_id TEXT NOT NULL, case_ref TEXT NOT NULL, reason TEXT NOT NULL,
  placed_by TEXT NOT NULL, placed_at TEXT NOT NULL, released_by TEXT, released_at TEXT
);
```

- A hold freezes deletion, retention purge, eviction of frames and predictors, archive-with-purge and PII purge on
  everything inside its scope, including descendants and the table files they reference. One check,
  `holds.blocking(kind, id)`, is called from every destructive path: `dsets.decide_delete`, `retention.execute`,
  frame eviction and PII purge. The refusal names the case reference.
- Reads and new versions are allowed. A release needs a second admin (§1.5).

### 4.3 PII tagging per column, masking and purge

```sql
CREATE TABLE column_tags (
  root_id TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  tag TEXT NOT NULL CHECK (tag IN ('pii_direct','pii_quasi','protected','sensitive','key','none')),
  mask TEXT NOT NULL CHECK (mask IN ('redact','hash','last4','year_only','bucket','none')),
  source TEXT NOT NULL CHECK (source IN ('suggested','confirmed')),
  set_by TEXT NOT NULL, set_at TEXT NOT NULL,
  PRIMARY KEY (root_id, table_name, column_name)
);
```

- **Tags belong to the dataset (root), not to a version**, so they carry forward. A column first seen in a new
  version is tagged `suggested` by the profiler. The rules: name patterns (ssn, dob, email, phone, name, address,
  account no.), value patterns (Luhn, email regex, 9-digit SSN shape) and the G4 roles already in use (surname, zip,
  protected attributes are "never model inputs"). Suggested tags are masked until a maintainer confirms them, so the
  default is safe.
- **Masking happens in SQL**, in the one DuckDB view builder that grid, rows, diff rows, export, agent CSV and preview
  all use: `redact → '•••'`, `hash → sha256(salt_team || v)[:12]` (stable within a team, so joins and counts still
  work), `last4`, `year_only`, `bucket`. Training reads the frame unmasked. Holdout-prediction pages and the SHAP row
  view mask the PII columns too. PII columns used as features are flagged in Model Doctor.
- **Purge** ("forget this column / these rows"): it makes a **new version** without them, which is the only legal
  shape since nothing changes in place. The old versions that contain them are then deleted through the four-eyes
  delete. Files are reference-counted by sha256 across every manifest in every team: a table file is removed from
  disk **only when no live manifest references it**. If a share or a fork in another team still references the file,
  the purge request lists those references and is blocked until each owning team acts. The purge request records
  which files remain and why. A legal hold blocks a purge.

```sql
CREATE TABLE blob_refs (sha256 TEXT NOT NULL, dv_id TEXT NOT NULL, team_id TEXT NOT NULL, PRIMARY KEY (sha256, dv_id));
CREATE INDEX ix_blob_refs_team ON blob_refs (team_id);
```

### 4.4 Certified and approved-for-model datasets

These are covered in §1.4. Training on a non-certified version is allowed. Registering a model that will go to
**Production** checks the team policy `require_certified_inputs`. When it is on, every input table version of the
run's frame must be `approved_for_model` (or certified, per policy) at registration time. Otherwise the transition
is refused with the list of tables. The certification is recorded in the model card, so a later expiry does not
invalidate history.

### 4.5 Data contracts per dataset

```sql
CREATE TABLE data_contracts (
  id TEXT PRIMARY KEY, root_id TEXT NOT NULL, version INTEGER NOT NULL, spec TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE (root_id, version)
);
```

Spec (per table, plus cross-table):

```json
{"tables": {
   "applications": {"key": ["app_id"], "required": true, "rows": {"min": 100000},
     "columns": {"app_id": {"type": "string", "nullable": false, "unique": true},
                 "income": {"type": "number", "max_null_share": 0.02, "range": [0, 1e7]},
                 "y": {"type": "int", "values": [0, 1], "role": "target"}},
     "allow_new_columns": "warn", "on_removed_column": "block"},
   "bureau": {"key": ["app_id"], "columns": {"score": {"type": "int", "range": [300, 850], "max_null_share": 0.02}}}},
 "relations": [{"from": "bureau.app_id", "to": "applications.app_id", "min_coverage": 0.95}],
 "severity_default": "warn"}
```

- A contract can be created from a version ("freeze what this looks like") and then edited. Each rule is `warn` or
  `block`. The checks run in DuckDB at the upload check step, on edits and on certification requests. The result is
  stored on the version: `data_versions.contract_result`, JSON with per-rule pass/fail and numbers.
- A contract change is itself versioned and audited. A certification pins the contract version it was checked
  against.

### 4.6 Model-risk documentation: which table versions and which join fed a model

At registration (`registry/build.py`), the model version stores an immutable **input record**:

```json
{"frame": {"id": "ds_…", "sha256": "…", "rows": 1204331},
 "dataset": {"root": "ds_…", "name": "PD 2026", "team": "credit-risk", "project": "prj_…"},
 "version": {"dv": "dv_…", "v": 8, "created_by": "…", "reason": "CHG-1182", "contract": {"id": "…", "result": "pass"},
             "certification": {"level": "approved_for_model", "by": "…", "at": "…"}},
 "tables": [{"name": "applications", "sha256": "…", "rows": 1204331, "source_file": "applications.csv", "uploaded_by": "…",
             "from_dv": "dv_… (carried)"},
            {"name": "bureau", "sha256": "…", "rows": 1198002, "source_file": "book.xlsx › Bureau", "uploaded_by": "…"}],
 "join": {"id": "jn_…", "name": "apps + bureau + payments agg", "spec": {…}, "spec_sha256": "…",
          "coverage": {"bureau": 0.995, "payments": 0.921}},
 "split": {"key": "…", "sha256": "…"}, "pii_features": ["zip3"], "excluded_protected": ["race_proxy"]}
```

- `GET /api/registry/versions/{vid}/card` renders it as the **Data** section of the model document (SR 11-7 style
  "data used in development"), with each table's lineage back to the upload event.
- `GET /api/registry/versions/{vid}/inputs.zip` contains the manifest, the join spec, contract results and the hashes.
  It contains no rows; the rows are a separate masked export.

---

## 5. API contract

All paths are under `/api`. New bodies take `by` and `reason` like today; `X-Ensemble-Actor` / `X-Ensemble-Reason`
headers are honoured. Every mutating route carries `require(action)`. Errors keep today's shape `{detail}`, with 403
`{detail:{code:'forbidden', action, team, needed}}` added.

### 5.1 Teams, identity and authz

| method & path | body / query | returns |
|---|---|---|
| `GET /me` | – | principal, auth kind, teams with roles, `authz_mode` |
| `GET /teams` · `POST /teams` | `{name, slug, description}` | team |
| `GET /teams/{t}` · `PATCH /teams/{t}` | `{name?, description?, policy?}` | team (policy changes audited with a diff) |
| `GET /teams/{t}/members` · `POST /teams/{t}/members` · `PATCH/DELETE /teams/{t}/members/{principal}` | `{principal|name|email, role}` | member list |
| `GET/PUT /teams/{t}/idp-groups` | `[{idp, group, role}]` | mappings |
| `GET /teams/{t}/audit` | `action, object_kind, object_id, actor, from, to, format=json|csv, cursor` | page of audit rows |
| `GET /teams/{t}/audit/verify` | – | `{ok, rows, first_break?}` |
| `GET /authz/coverage` | – | routes with their action or `public`, plus uncovered ones |
| `POST /authz/check` | `{action, object_kind, object_id, principal?}` | `{allow, reason, needed}` (an admin may ask on someone's behalf; used by the UI to grey out buttons) |
| `POST /service-tokens` · `DELETE /service-tokens/{id}` | `{team_id, scopes, name}` | token (shown once) |

### 5.2 Projects, datasets, versions, tables

| method & path | body | notes |
|---|---|---|
| `POST /projects` | `{name, description, team_id?}` | `team_id` defaults to the caller's single team, else 400 |
| `POST /uploads` (multipart, many files) | `files[]`, `project_id`, `root_id?` (next version), `mode: full|delta` | returns `{upload_id, tables:[{name, source, rows, cols, sheet?, suggested_key}]}` |
| `PATCH /uploads/{u}` | `{tables:[{name, include, rename_to, key, delta:{op_column, map}?}]}` | edit the staging plan |
| `POST /uploads/{u}/check` | – | per table: match counts, contract results, PII suggestions, `would_be_default`, `blocks[]` |
| `POST /uploads/{u}/commit` | `{reason, make_default: auto|yes|no}` | **201** `{root_id, dv_id, v, tables[], frames[]}` (frames: one per table when the version has one table, so the old flow gets its `ds_` id back) |
| `GET /datasets/{root}/versions` | – | versions with tables, sha256s, contract result, certification |
| `GET /datasets/{root}/versions/{dv}` | – | manifest view |
| `GET /datasets/{root}/versions/{dv}/tables/{t}` | – | schema, profile, tags |
| `GET /datasets/{root}/versions/{dv}/tables/{t}/rows` | `offset, limit, where?, columns?, unmasked=0|1, reason?` | masked page; read-audited |
| `POST /datasets/{root}/versions/{dv}/tables/{t}/edit` | `{ops, reason, preview}` | DL3 body unchanged; new dv |
| `GET /datasets/{root}/diff/{dv_a}/{dv_b}` | `table?` | per-table summary; `…/tables/{t}/rows?change=added|removed|changed` |
| `GET/POST /datasets/{root}/joins` · `PATCH/DELETE /datasets/{root}/joins/{j}` | `{name, spine, joins[], where?}` | validate returns coverage and key-uniqueness results |
| `POST /datasets/{root}/versions/{dv}/frames` | `{table}` or `{join_id}` | **201** `{frame_id: ds_…, rows, sha256, cached: bool}`: this is what `POST /runs` takes as `dataset_id` |
| `GET/PUT /datasets/{root}/contract` · `POST /datasets/{root}/contract/from-version/{dv}` | spec | versioned |
| `GET/PUT /datasets/{root}/tags` | `[{table, column, tag, mask}]` | `source` becomes `confirmed` |
| `POST /datasets/{root}/certifications` · `POST /certifications/{c}/decide` | `{dv_id, level, reason}` / `{approve, comment}` | four eyes |
| `POST /shares` · `POST /shares/{s}/decide` · `POST /shares/{s}/revoke` | `{object_kind, object_id, grantee_team, scope, pii, expires_at, reason}` | four eyes |
| `POST /legal-holds` · `POST /legal-holds/{h}/release` · `GET /legal-holds` | `{scope_kind, scope_id, case_ref, reason}` | admin |
| `POST /datasets/{root}/purge-requests` | `{columns?: [{table, column}], rows?: {table, keys[]}, reason}` | new version + delete requests + a blob reference report |
| `GET /datasets/{root}/versions/{dv}/export` | `table|frame, format=csv|parquet|xlsx|zip, unmasked, reason` | masked by default |
| `GET /data/search` | `q, team, certified, pii, cursor` | extended; adds `team`, `tables`, `certified`, `access: own|shared|discoverable` |

### 5.3 Changed endpoints (body additions only)

- `POST /runs` and every kind's `POST /{kind}/runs`: `dataset_id` (a frame id, as today) **or** `{root_id, dv_id,
  table|join_id}`. The server resolves the latter to a frame by calling the frames endpoint internally, so the UI makes
  one call. `project_id` is optional and defaults to the frame's project. A shared frame requires a `project_id` in
  the caller's team.
- `POST /registry/versions/{vid}/batches`: also accepts several files or one xlsx when the model's input is a join.
- `GET /registry/versions/{vid}/card`: adds `inputs` (§4.6).
- `GET /lineage/{kind}/{id}`: `kind` adds `table` and `frame`.
- Lists (`/datasets`, `/runs`, `/registry*`, `/projects`, `/monitoring/stream`): `team` field on each row, `?team=`
  filter, visibility filtering in `enforce` mode.

### 5.4 Backward compatibility for today's single-table datasets

- Migration (one idempotent step in `catalog/schema.py: migrate_c1`, recorded in `catalog_meta`):
  1. Create `tm_default` and assign every project to it.
  2. Create a `principals` row for every distinct name in `audit_log.actor`, `datasets.created_by`,
     `runs.config.by`, the request tables and `ENSEMBLE_ADMINS`. Add each as a **member** of `tm_default`. Admins
     become owners. Nobody loses an action they can do today in `audit` mode.
  3. Give every existing `ds_` version a `dv_` with one table `data`, whose `table.parquet` is written from
     `data.csv` (read with the **same `pd.read_csv` call the engine used**, so the Parquet holds the values runs
     saw). The frame row is the existing `datasets` row (same id), with `frame_path` set to the Parquet file.
  4. **`data.csv` is kept**, read-only, for every version that has a run, model or champion. Those runs' `csv_path`
     and `data_sha256` stay valid and their integrity gate still passes. A retention task drops `data.csv` only once
     no pinned run references it, after recording in `frame.json` that the Parquet holds the same frame
     (`legacy_csv_sha256`, and a `frames.read(parquet, compat) ≡ read_csv(csv)` check, frame-equal, run once at
     migration).
- Every existing endpoint keeps working on a `ds_` id:
  - `GET /datasets/{id}`, `/rows`, `/grid`, `/history`, `/diff/{a}/{b}`, `/edit`, `/versions` (upload) and `/export`
    act on that frame's version. When the version has one table, they act on that table.
  - On a multi-table version, `/edit` and `/versions` need `?table=`, and without it return 400 naming the tables.
- `POST /datasets` (single CSV) is a thin wrapper over uploads, check and commit, with one table and auto commit. It
  returns the old record shape plus `dv_id`.
- UI calls in `src/shared/api/catalog.ts` and `client.ts` keep their paths. New views use the new paths.

---

## 6. Phased build plan (sessions of about 2 h; manual API verification only, plus `npm run build`)

Conventions:
- `$A` is `curl -s -H 'X-Ensemble-Actor: Ana' -H 'Content-Type: application/json' localhost:8000/api`, and `$B` is
  the same as `Ben`.
- Each phase ends with its grep gates and `npm run build` when the UI changed, and commits a
  `docs/progress/C<n>.md` with the calls and outputs.
- **Read-only audit sessions (marked ⟂)** are run by a separate session that only reads code and calls the API, never
  edits, and reports findings by severity.

Dependency on the storage plan: phases C3 onwards need the Parquet table/manifest layer (from plan A/B: `dv` manifest,
DuckDB helper). If that lands first, C3 shrinks. If not, C3 includes the minimal writer described in §0.

**C1: Teams, principals, membership (schema and API, mode `off`).**
- Build: tables from §1.1, `migrate_c1` steps 1–2, `/me`, `/teams*`, `/teams/{t}/members*`, `projects.team_id`.
- Verify:
  - `$A/me` shows Ana in `tm_default`.
  - `POST /teams {name:"Credit Risk",slug:"credit-risk"}` → 201. Ana is the owner. `POST /teams/{t}/members {name:"Ben",role:"member"}`.
  - `$A/projects` rows carry `team`. Run the migration twice; the second run changes nothing.

**C2: Authz core in `audit` mode.**
- Build: `authz/` package, `TypedNameProvider`, `require()` on the data, registry, champion, retention and admin
  routes, `/authz/coverage`, `/authz/check`, the 403 shape, and the audit columns.
- Verify:
  - Ben (viewer) calls `POST /datasets/{id}/edit` → 201, with `X-Ensemble-Authz: would-deny data.version`, and
    `$A/teams/{t}/audit?action=authz_would_deny` shows it.
  - `/authz/coverage` lists 0 uncovered mutating routes.
  - `ENSEMBLE_AUTHZ=enforce` without `ALLOW_TYPED` → the server refuses to start with a message. With it set, Ben's
    edit → 403 `{code:'forbidden', needed:'member'}`.

**C3: Frames, `frames.read`, and the consumer sweep (single table, Parquet).**
- Build: `app/frames.py` (compat mode); migration step 3 (data.csv → table.parquet, frame rows); `csv_path` →
  `frame_path` alias; the reads in §3 rows 1–14 and 24–27 switched over.
- Verify:
  - Grep gate (no `read_csv(cfg[` left).
  - For three samples, `POST /runs` on the migrated `ds_` id. The run's `info.data_sha256` equals the frame hash,
    holdout rows equal the pre-migration run's holdout rows (`GET /runs/{old}/holdout-predictions` vs new, `row`
    columns identical), and the score is within seed noise.
  - `GET /datasets/{id}/rows?offset=…` works.
  - One run each of anomaly, forecast, survival, EL, segmented, reject and multilabel on their samples → succeeded.
  - An old run's `/explain` still works (it reads its kept data.csv).

**C4 ⟂: Independent read-only check of C1–C3.**
- Scope: row identity (splits, root_rows), the compat dtypes per kind, the coverage list, and the migration on a copy
  of a populated `data/` folder. Output `docs/progress/CA1.md`.

**C5: Multi-file and multi-sheet upload as one version; upload-as-next-version per table; delta.**
- Build: `/uploads*`, Excel reading (openpyxl, already a pandas optional dependency; check `requirements.txt`),
  per-table matching, carry-forward by reference, delta merge in DuckDB, the multi-table sample.
- Verify:
  - Upload `book.xlsx` (3 sheets) plus `bureau.csv` → `/check` shows 4 tables → `/commit` → `GET
    /datasets/{root}/versions/{dv}` lists 4 tables with sha256s.
  - Upload a delta for `bureau` with an `_op` column → v2 has `bureau` changed and the others with the **same
    sha256** as v1.
  - A delta with duplicate keys → 400 listing them.
  - The old `POST /datasets` with a CSV still returns the old shape.

**C6: Saved joins and join frames; training on a join.**
- Build: `/joins*`, validation (key uniqueness, coverage), materialisation, frame cache, the spine-row-count assert,
  the run body `{root_id, dv_id, join_id}`, and the run cfg `input_sha256s`.
- Verify:
  - Create the join (apps + bureau m:1 + payments aggregate) → coverage numbers.
  - `POST …/frames {join_id}` → rows equal the spine's rows. A second call returns `cached:true`.
  - `POST /runs {root_id, dv_id, join_id, label}` → succeeded. `GET /runs/{id}` cfg has `input_sha256s`.
  - `POST /compare` of a table run and a join run on the same spine → **paired**.
  - A join with an m:n relationship and no aggregate → 400.

**C7: Registry, batches and monitoring for join models; model-risk input record.**
- Build: §4.6 input record in `registry/build.py`, the card's Data section, `inputs.zip`, multi-file batch scoring
  with the frozen join spec, the monitoring window as a one-table version.
- Verify:
  - Register the C6 run → `/card` shows its tables and join sha.
  - `POST /registry/versions/{vid}/batches` with three files → scored; with a file missing a joined column → 400
    naming the column.
  - `/monitoring` works for the version.

**C8 ⟂: Independent read-only check of C5–C7.**
- Scope: delta correctness on edge cases (an update to a missing key, delete plus insert of the same key), join
  coverage and fan-out guard, serving/training skew. Output `CA2.md`.

**C9: PII tags, masking, unmasked reads, export.**
- Build: `column_tags`, profiler suggestions, the one masked-view builder used by grid, rows, diff rows, export and
  agent CSV, `unmasked=1` with a reason and read audit, xlsx/parquet/zip export.
- Verify:
  - The upload suggests `ssn` as `pii_direct` → `GET …/rows` shows `•••` for Ben.
  - Ana (maintainer) with `unmasked=1&reason=QA` sees values, and `$A/teams/{t}/audit?action=dataset_rows_read` has
    the row.
  - Export the zip → `manifest.json` sha256s match, and the `ssn` column is masked.

**C10: Contracts, certification, sharing across teams.**
- Build: `data_contracts`, checks at the upload check step, certifications with four eyes, `shares` with four eyes,
  `visible_teams()` list filtering, cross-team training into the grantee's project, `require_certified_inputs`.
- Verify:
  - Freeze a contract from v1. Upload v2 with a removed column (set to block) → `/check` blocks and `/commit` → 409.
  - Ana requests certification; Ana deciding → 409; Ben (owner) → active.
  - Share to Collections with `scope=certified`. Carl (Collections) sees only v1 in `/data/search`, trains into their
    own project → 201, and `POST …/edit` → 403 in enforce mode.

**C11: Retention per team, legal hold, purge with shared-file reference counts.**
- Build: team retention policy, frame eviction, `legal_holds` in every destructive path, `blob_refs`, purge requests.
- Verify:
  - Place a hold on a project → `/retention/plan?team=` lists 0 candidates in it, and a delete decide → 409 naming
    the case.
  - Purge the `ssn` column → new version without it. Delete the old versions (four eyes). The file is kept while
    Collections' fork references it (the report says so), and removed after the fork is deleted.

**C12: UI. Team switcher, Build › Data catalog, upload/check/commit, join builder, per-table diff and lineage.**
- Build: the §2 screens, one primary action each, and buttons greyed out from `/authz/check`.
- Verify: `npm run build`. Chain the API calls as the UI makes them for upload → check → commit → frame → run,
  recorded in `C12.md`.

**C13 ⟂: Independent read-only check of C9–C12 plus a whole-system authz pass.**
- Scope: every route in `/authz/coverage` × the matrix in §1.3, spoofing via body `by`, agent tools acting beyond the
  user, masking leaks (holdout predictions, SHAP row, agent CSV, error messages that echo values). Output `CA3.md`.

**C14: SSO providers (when an IdP is available).**
- Build: `TrustedHeaderProvider` and `OIDCProvider`, IdP group → role sync, service tokens, the drop-folder watcher
  (§2.10).
- Verify:
  - With a local Keycloak or a mocked JWKS: a bearer token → `/me` shows `auth:"oidc"` and groups → roles.
  - A body `by` different from the token → 409.
  - The watcher ingests a dropped file into a version, and a blocked file lands in `rejected/` with its result.

---

## 7. Scale numbers

Measured in this container (4 vCPU, 15 GB; DuckDB 2.0.1, pyarrow 26):

| operation | measured |
|---|---|
| write 3 Parquet tables (10M, 10M, 30M rows) | 5.4 s; 129 / 59 / 360 MB |
| key uniqueness check, 10M rows | 0.15 s |
| materialise a 3-way join frame (m:1 + 30M-row aggregate, ordered by spine), 10M rows out | 3.2 s; 245 MB |
| masked preview page, 50 rows at offset 5M | 18 ms |
| full-row EXCEPT diff, 10M vs 10M | 1.1 s |
| frame.parquet → pandas (10M × 9) | 2.2 s; 0.71 GB in memory |
| read one column of the frame | 245 ms |
| role resolution (SQLite, 100k memberships, 20k projects, 5k shares) | 4 µs per check |

Implications:
- Permission checks and team filtering are free.
- Materialising a join is seconds, so frames can be a cache, not stored data.
- The pandas hand-off to AutoGluon stays the memory ceiling (about 70 bytes per cell). For tens of millions of rows
  × hundreds of columns, frames need `columns=` pushdown (consumers already name their columns in most of §3) and
  the training-side sampling of the storage plans.
- SQLite's single writer is fine for permissions and audit at thousands of runs. The audit hash chain serialises per
  team, which needs Postgres once there are more than ~50 writes/s.

---

## 8. Risks

1. **Row identity across joins.** A fan-out join would silently break splits, pairing and every row-indexed view.
   Mitigation: the spine rule; m:1 checked at materialisation; `count(frame) == count(spine)` asserted; aggregates
   required for m:n.
2. **Serving/training skew for join models.** Mitigation: the join spec is frozen in the model version; batches are
   joined by the same code path (`frames.materialise`); a schema check runs before scoring.
3. **Dtype drift CSV → Parquet** changes features and breaks comparisons with old runs. Mitigation: `compat=True`
   everywhere first, then per-consumer opt-out, checked by a paired compare on a sample.
4. **Typed-name enforcement gives false security.** Mitigation: `enforce` refuses typed names unless a demo flag is
   set; `audit` mode is the default until SSO; the UI says "names are not verified" while auth is `typed`.
5. **Route coverage drift.** A new route without `require()` would be open. Mitigation: `/authz/coverage`, the
   startup refusal in enforce mode, and an audit pass (C13).
6. **Purging shared files.** Deleting a table referenced by another team's fork breaks their runs. Mitigation:
   `blob_refs` reference counts; purge is blocked with a named list; legal hold wins.
7. **Masking leaks** through side channels: error messages quoting values, SHAP row views, holdout predictions, the
   agent's CSV, Model Doctor examples. Mitigation: one view builder; the C13 checklist; Doctor examples run through
   the same masker.
8. **Migration of a populated `data/`**: thousands of `data.csv` files take time and double the disk until legacy
   CSVs are dropped. Mitigation: lazy per-version conversion on first access, plus a background converter with a
   progress endpoint; legacy CSVs kept only for pinned runs.
9. **Role design friction**: too many approvals slow teams down. Mitigation: team policy flags
   (`maintainer_can_review`, `require_independent_reviewer`, `require_certified_inputs`) default to today's
   behaviour.
10. **Agent privilege.** The agent must never act as a service. Mitigation: the principal travels in the in-process
    call with a signature only the in-process client holds; checked in C13.
