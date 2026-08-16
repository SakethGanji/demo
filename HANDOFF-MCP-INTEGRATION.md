# Handoff — MCP integration for the analytics platform

> ## ⚠️ SUPERSEDED — this is a historical planning document
>
> Written 2026-08-06 as a forward plan. **The plan was executed the same day.** It is kept
> for its reasoning, not as instructions; several claims below were disproved in the doing
> and are annotated inline.
>
> **For current state, read `apps/analytics-service/HANDOFF.md` and `ARCHITECTURE.md`.**
>
> | §6 step | Outcome |
> |---|---|
> | 1. Three `workflow-engine` defects | **Done.** Engine suite 1 → 107 tests. |
> | 2. `analytics-service` defects (§4a) | **Done**, and 4 more found. Suite 1028 → 1153. |
> | 4. Identity/auth | **Resolved without SSO.** MCP uses the service's own `get_principal`, so it inherits the SSO/JWT swap when it lands rather than blocking on it. |
> | 5. Fold MCP into the service | **Done.** Mounted at `/api/v1/mcp`; `apps/analytics-mcp` is now a ~220-line stdio shim holding zero tool definitions. |
> | 6. Bridge `workflow-engine` to it | **Not done — descoped.** |
> | 7. Studio picker | **Not done — descoped.** |
>
> **Two corrections that matter more than the rest:**
>
> 1. **§5's "cost is low … a transport swap; the 27 tools stay byte-identical" was wrong**,
>    and it was marked `[VERIFIED]`. Transport was one seam; **identity was a second**.
>    `AnalyticsClient` baked `X-User-Id` into one client at construction and every tool
>    closure captured it — correct for one process per user, an identity-laundering hole
>    when mounted multi-tenant. Worse, binding identity *per connection* would still have
>    laundered it, because the session manager runs a session's handlers in the task of
>    whichever request opened it. Only **per-message** binding is correct.
> 2. **§4c's "not interceptable without patching the SDK" was wrong.** `MCPServer` takes a
>    `middleware` sequence that sees raw params before validation. The mounted server now
>    uses one for identity.
>
> Also: **§4a's descriptions were right about symptoms and wrong about causes.** See the
> annotations in that section — following them literally would have sent you to edit
> working code.

Date: 2026-08-06. Written for a fresh session picking this up cold.

Tags used throughout: **[VERIFIED]** reproduced by running it · **[READ]** read out of source,
file/line cited · **[ASSESSMENT]** judgement, argue with it.

---

## 0. What happened this session

1. Reviewed `WORKFLOW-ANALYTICS-INTEGRATION.md` and fact-checked it against the code.
2. Built **`apps/analytics-mcp`** — an MCP server exposing the analytics platform to LLM
   clients. 27 tools. Working, verified end to end.
3. Evaluated it by having agents *use* it blind, which surfaced defects in this server, in
   `analytics-service`, and in the MCP SDK.
4. Investigated giving `workflow-engine` an MCP client, and found three engine defects that
   block it.
5. Settled the long-term architecture: **fold the MCP surface into `analytics-service`**
   rather than run it as a separate service.

Nothing is half-finished. `analytics-mcp` works today as a stdio server. The remaining work
is listed in §6 and is all optional-but-recommended, not repair.

---

## 1. The apps

```
                    ┌─────────────────────────────────────┐
                    │  analytics-service   (the platform) │
                    │  REST, 134 ops under /api/v1        │
                    │  Postgres control plane             │
                    │  Object store artifacts             │
                    │  DuckDB compute + SQL sandbox       │
                    └──────────────▲──────────────────────┘
                                   │ HTTP  (X-User-Id)
                 ┌─────────────────┼──────────────────┐
        ┌────────┴───────┐  ┌──────┴────────┐  ┌──────┴───────────┐
        │ analytics-mcp  │  │ workflow-     │  │ curl, scripts,   │
        │ 27 MCP tools   │  │ engine        │  │ notebooks        │
        │ (NEW, works)   │  │ (7 call sites,│  │                  │
        │                │  │  all broken)  │  │                  │
        └────────┬───────┘  └──────┬────────┘  └──────────────────┘
                 │                 │
        Claude Desktop /     workflow-studio (React UI)
        Claude Code
```

`analytics-service` owns everything — all data, permissions, computation. `analytics-mcp`
holds no state; it is an HTTP client of that API with the same identity and the same RBAC.

---

## 2. What `apps/analytics-mcp` is

Full detail in `apps/analytics-mcp/README.md`. Summary:

**27 tools, not 134 one-per-endpoint wrappers.** Related operations are grouped behind one
tool with an `action`/`target` parameter, because every tool schema is serialized into context
on every model call — tool count is a real cost.

- **Understand** (11) — `whoami`, `search_datasets`, `describe_dataset`, `get_data_dictionary`,
  `search_columns`, `get_dataset_health`, `get_lineage`, `get_activity`, `list_relationships`,
  `list_saved_objects`, `compare_versions`
- **Analyse** (6) — `query_rows`, `run_sql`, `aggregate`, `pivot`, `profile_column`,
  `check_quality`
- **Chain** (2) — `list_artifacts`, `read_artifact`
- **Act** (8, all state-changing) — `write_documentation`, `manage_quality_rules`,
  `run_quality_check`, `manage_tags`, `manage_relationships`, `join_datasets`,
  `transform_data`, `publish_result`

**The design principle** is a ladder: the first three rungs answer most questions with no data
rows at all (they read Postgres, never open a file). `run_sql` is the workhorse — one sandboxed
SELECT covers more ground than a dozen hand-wrapped endpoints. Results too large for a response
become artifacts; `read_artifact` reads them back with server-side projection and filtering, so
intermediate results never enter the model's context.

**Transport:** stdio only. The client launches it as a subprocess; identity comes from
`ANALYTICS_MCP_USER_ID`, one process per user. This is a local adapter, **not a deployed
service**.

---

## 3. Decisions — do not silently reverse these

| Decision | Rationale |
|---|---|
| **No masking layer** | The server applies none of its own; whatever the service returns for the configured user is what the caller sees. A uniform-masking mode was built and then removed on request. |
| **Writes yes, deletes never** | Documentation, quality rules and runs, tags, relationships, transforms, joins, publish are all exposed. Deleting datasets, rules, views, tags is deliberately absent. |
| **Grouped tools, not one-per-endpoint** | Blind evaluation found 11 of the original 16 tools felt redundant with `run_sql`. Adding ~40 more would make the surface *less* usable. Resist growth. |
| **Identity is per-process** | `X-User-Id` from env, so RBAC matches the UI exactly. No service account, no privilege escalation. |
| **`query_rows` doubles as preview** | A separate `preview_rows` existed and was removed as redundant. |

---

## 4. Defects found

### 4a. In `analytics-service` — these affect every client, not just MCP **[VERIFIED]**

| Defect | Evidence | Status |
|---|---|---|
| `ColumnProfile.count` emits total rows, not the non-null count its schema claims | 60-row sheet with 17 nulls reports `count: 60`; true non-null is 43. The same response's `uniqueness` (3/43) is computed against the correct figure | **FIXED** — as documentation |
| A malformed filter silently returns the whole table | `op: "greater_than"` returned all 304 rows instead of 118. The service coerces an unparseable condition into an empty filter group | **FIXED** — cause was elsewhere |
| `aggregate`'s `totals` sums non-additive aggregates | A `max` footer read 75000 where the true maximum was 25000. `pivot` computes true grand totals, so correct behaviour already exists upstream | **FIXED** — plus a worse unlisted bug |
| Invalid `sort_order` silently reverses results | Anything not exactly `desc` falls back to ascending, so `"ASC"` returns the opposite of what was asked | **FIXED** — direction stated backwards |
| `PUT` of sheet/column metadata is a whole-record replace | Omitted fields are written as NULL | **FIXED** — PUT kept, PATCH added |

> **All five fixed 2026-08-06, and every client-side workaround deleted. Four of the five
> descriptions above were materially wrong about the cause or the behaviour** — recorded
> here because the error pattern is the useful part:
>
> - **`ColumnProfile.count`** — "the non-null count its schema claims" is false. The field
>   was a bare `count: int` with *no* description. And `explorer/service.py` derives
>   `non_null = count - null_count`, i.e. the service *depends* on `count` being the total.
>   Changing the value would have silently broken `uniqueness`. It was an undocumented,
>   misleadingly-named field — fixed with a description plus a new `non_null_count`.
> - **Malformed filter** — the cause was not filter compilation. `compile_filter` *did*
>   raise on an unknown operator; it was unreachable, because `list[Filter | FilterGroup]`
>   is a union in which `FilterGroup` has zero required fields, so a bad `op` matched the
>   group branch and `extra="ignore"` discarded the real keys. Anyone following this row
>   would have gone and edited correct code.
> - **`totals`** — true, but it understated the damage. The footer also summed only the
>   *returned page* of groups, so even `sum`/`count` were silently partial when truncated —
>   which means the MCP's "only show totals for sum/count" workaround was **also wrong**.
> - **`sort_order`** — backwards for the endpoint in question. On `/aggregate` the code was
>   `"ASC" if sort_order == "asc" else "DESC"`, so anything unrecognised sorted
>   **descending**. Ascending was the fallback on a *different* route
>   (`GET /samples/{filename}/data`), which is probably where the observation came from.
>   Both are now `Literal["asc","desc"]`.
> - **`PUT` metadata** — accurate. Resolution differs from the implied one: `PUT` is a
>   correct HTTP replace and merging it would have removed the only way to clear a field,
>   so PUT was documented and a merging `PATCH` added.

**[ASSESSMENT]** These are the strongest argument for folding MCP into the service (§5). Every
workaround above is a bug that should have been fixed at the source. *(This held up — the
fold-in deleted four of the five workarounds outright.)*

### 4b. In `workflow-engine` — blocks the MCP bridge **[READ]**

Three defects, fully specified with file:line and suggested fixes in
**`WORKFLOW-ENGINE-TOOL-DEFECTS.md`**. Summary:

1. **Schema pipeline corrupts optional parameters.** Pydantic `Optional[int]` arrives as
   `anyOf` with no top-level `type`; the engine injects `"type": "string"` then strips `anyOf`.
   Integers silently become strings. Strict mode separately forces every optional parameter to
   be required and non-nullable.
2. **Results over 8,000 chars can become invalid JSON.** Nested numeric structures fall through
   to a head+tail slice that does not parse.
3. **The tool filter drops untried tools after iteration 3.** The code comment claims the
   opposite. This would dismantle any ladder-shaped tool set.

All three already damage the engine's existing tools. Fix regardless of the MCP plan.

Separately, the engine's seven analytics call sites are independently broken (no `/api/v1`
prefix, no auth header, a `/report` route that never existed) — see
`WORKFLOW-ANALYTICS-INTEGRATION.md` §2. **Do not repair those**; the plan is to replace them.

### 4c. In the MCP Python SDK **[VERIFIED]**

Unknown tool arguments are silently dropped — the argument model is built with pydantic's
default `extra="ignore"`, so a misspelled parameter never reaches the tool and the call appears
to succeed. Not interceptable without patching the SDK. Documented in the README.

---

## 5. Architecture decision: fold MCP into `analytics-service`

**[ASSESSMENT]** Long term, the MCP surface should be a mounted endpoint inside
`analytics-service`, not a separately deployed service.

Three shapes were considered: (A) stay a stdio subprocess, (B) deploy as a sidecar service,
(C) fold into `analytics-service`. **C wins; B is the worst of the three.**

Why:

- **Drift.** A standalone adapter drifts from the API it wraps. This repo already contains the
  proof: `workflow-engine`'s data tools are exactly that failure mode, now totally broken with
  nobody noticing. Folded in, the tools live beside the routes they call.
- **Identity.** A sidecar that trusts a forwarded `X-User-Id` is an identity-laundering hole.
  Folded in, MCP requests arrive through the service's own `get_principal` and the problem
  disappears.
- **Incentives.** Every workaround in §4a should have been an upstream fix. A permanent
  separate adapter institutionalises papering over them.
- **Cost is low.** An earlier claim that folding in requires a rewrite was **wrong**.
  `analytics-service` already runs in-process HTTP against itself in its own test suite
  (`tests/conftest.py:218`, `ASGITransport(app=app)`), and `AnalyticsClient` has exactly one
  construction seam — all 57 tool calls go through `ctx.client.{get,post,put,patch}` with zero
  raw `httpx` in the tool modules. Folding in is a **transport swap**; the 27 tools stay
  byte-identical. **[VERIFIED]**

> **Correction (2026-08-06, written while doing the fold-in).** The last bullet is wrong,
> and it is the bullet that makes the work look cheap. Transport is one seam; **identity is
> a second one, and it is the hard one.** `AnalyticsClient.__init__` bakes `X-User-Id` into
> a single `httpx.AsyncClient`'s default headers, `build(config)` constructs one `Ctx`, and
> all 27 `register(server, ctx)` closures capture it. One process per user makes that
> correct; mounted in a multi-tenant service it means whichever user's id was present at
> startup serves every caller. Making `Ctx` request-scoped — and rebinding per JSON-RPC
> *message* rather than per connection, because a stateful streamable-HTTP session runs its
> handlers in a task spawned by whoever opened it — is the actual core of the work. Done:
> see `analytics-service/app/features/mcp/identity.py` and the two-user tests in
> `analytics-service/tests/test_mcp_endpoint.py`.

**What would change this decision:** if `analytics-mcp` should ever front more than
`analytics-service` — several backends behind one MCP gateway — folding it into one of them is
clearly wrong. That is a product question nobody has answered.

**Dependency:** folded in, desktop users connect over HTTP and must authenticate, rather than
running a local subprocess with an env var. That is better, but it only exists once the
SSO/JWT work lands — `get_principal` is still POC header identity.

---

## 6. What to do next, in order

**1. Fix the three `workflow-engine` defects.** Spec is ready in
`WORKFLOW-ENGINE-TOOL-DEFECTS.md` — file:line, failing branch, and a suggested fix for each.
Worth doing whether or not the MCP integration ever happens. **Start here.**

**2. Fix the `analytics-service` defects in §4a.** Independent of everything else, and they
currently produce silently wrong answers for every client. The filter-coercion one is the most
serious: a misspelled operator returns the whole table with no error.

**3. Stop and reassess.** Steps 1 and 2 stand on their own. Nothing below is urgent.

**4. Decide identity/auth.** Same unresolved question as `WORKFLOW-ANALYTICS-INTEGRATION.md`
§8.1. This gates everything after it.

**5. Fold MCP into `analytics-service`** (§5) — mount the MCP endpoint, swap
`AnalyticsClient`'s transport to `ASGITransport`, keep the tools unchanged.

**6. Bridge `workflow-engine` to it.** The engine's tool contract is duck-typed
(`{name, description, input_schema, execute}`) and
`src/nodes/ai/tools/api_request_tool.py:151-219` already builds all four at runtime with an
`async def execute` closure — that is the pattern to copy. Two gotchas: `resolve_tools()` is
sync and cannot `await list_tools()`, and one MCP server must expand into N engine tools.

**7. Studio picker.** `builtinTools` is a static list. Needs a new property type plus a small
React component; `WorkflowSelectorField.tsx` is the exact precedent.

---

## 7. Running and verifying

**Start the service** (Postgres and MinIO containers `analytics-pg` / `analytics-minio` should
be running; storage defaults to local disk so MinIO is optional):

```bash
cd apps/analytics-service
venv/bin/python -m app.infra.db.postgres.migrate apply
ACCELERATOR_AUTH_ENABLED=true venv/bin/python -m uvicorn app.main:app --port 8001
```

**Exercise the MCP server:**

```bash
cd apps/analytics-mcp
export ANALYTICS_MCP_USER_ID=00000000-0000-0000-0000-000000000001

venv/bin/python scripts/mcp_cli.py list                       # real stdio JSON-RPC client
venv/bin/python scripts/mcp_cli.py describe run_sql
venv/bin/python scripts/mcp_cli.py call search_datasets '{"limit":5}'

PYTHONPATH=src venv/bin/python scripts/smoke.py <dataset_id>  # regression suite
```

**The testing method that actually found things** — spawn agents that use the server *blind*,
forbidden from reading its source, given a realistic analytical task and told to log every
point of friction. That is what surfaced the misleading tool descriptions and most of §4a.
Testing that the code runs found none of it.

---

## 8. Environment state — read before you trust the data

**Every dataset and user id this section used to list is gone, and any replacement would
go the same way.** The test suite's autouse `_db_cleanup` truncates all mutable domain
tables, so one `pytest` run empties the dev database. Recording ids here was a mistake;
mint your own instead.

```bash
cd apps/analytics-service
# the seeded System superuser always exists — migrations create it
#   00000000-0000-0000-0000-000000000001   (team 00000000-…-0001)
venv/bin/python -m app.features.auth.bootstrap --email you@example.com   # extra users
```

Upload a fixture via `POST /api/v1/datasets` and read the id back; `brands_accountmanagement_sample_dataset.csv`
at the repo root is the CSV the suite itself uses. For masking behaviour you need a
non-superuser plus a column tagged sensitive in the data dictionary — the suite builds
both in `tests/test_pii_masking.py`, which is the fastest reference.

**Two environment traps, both of which cost real time:**

1. **Never run two `pytest` invocations at once.** They share one Postgres and one storage
   directory, and the truncating fixture makes the other run fail in ways that look like
   genuine bugs.
2. **Stop any dev `uvicorn` before running the suite.** A server on :8001 runs a job worker
   polling the same Postgres every second, and `_claim_next` is a global
   `FOR UPDATE SKIP LOCKED` — so it steals the pending job and
   `test_relationships.py::test_discovery_runs_as_a_job_the_worker_can_claim` fails
   spuriously. Check with `pgrep -af "[u]vicorn"`.

---

## 9. Open questions needing a human

1. **How should services authenticate to `analytics-service`?** Still open from
   `WORKFLOW-ANALYTICS-INTEGRATION.md` §8.1. SSO/JWT is outstanding and isolated to
   `app/features/auth/deps.py::get_principal`. Gates §6 steps 5–7.
2. **Will `analytics-mcp` ever front more than `analytics-service`?** Answering "yes" reverses
   the fold-in decision in §5.
3. **Should `workflow-engine` keep its own data tools at all?** The plan assumes no — that it
   becomes an MCP client. Not yet ratified.

---

## 10. Document map

| Document | Contents |
|---|---|
| **this file** | Session handoff, architecture decision, next steps |
| `WORKFLOW-ENGINE-TOOL-DEFECTS.md` | The three engine defects — start work here |
| `WORKFLOW-ANALYTICS-INTEGRATION.md` | Original integration assessment. Accurate; corrections noted in §4 of it are already folded into this session's understanding |
| `apps/analytics-mcp/README.md` | The MCP server: tools, setup, auth, known issues |
| `apps/analytics-service/HANDOFF.md` | The platform, as built |
| `apps/analytics-service/ARCHITECTURE.md` | Platform architecture reference |
