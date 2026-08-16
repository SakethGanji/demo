# Handoff — test coverage for `analytics-service`

Date: 2026-08-06. Written for a fresh session picking this up cold.
Goal of the next session: **raise meaningful test coverage.**

Tags: **[VERIFIED]** reproduced by running it · **[READ]** read out of source · **[ASSESSMENT]** judgement, argue with it.

---

## 0. Read this first — the 60-second version

- The suite is **1331 tests, all passing, ~2 minutes**. Coverage is **80%**.
- You are on branch **`feat/analytics-mcp-consolidation`** (9 commits, nothing pushed, `main` untouched).
- **Do not start by writing tests.** §3 has a decision to make first: roughly **26% of the
  uncovered code appears to be dead**, left behind by a feature that was deleted. Testing it
  would be wasted effort.
- The single richest *real* target is **`app/features/mcp/tools/`** — the newest code in the
  repo, ~563 uncovered statements, and the layer an LLM client actually talks to.
- §6 lists environment traps. Two of them cost real debugging time in the last session and
  will silently produce fake test failures.

---

## 1. What this repo is

Four apps; **only `analytics-service` is in scope.**

| App | Role | In scope? |
|---|---|---|
| `apps/analytics-service` | Python/FastAPI/DuckDB/Postgres dataset platform. REST at `/api/v1/*` **and** an MCP surface at `/api/v1/mcp` in the same process. | **Yes** |
| `apps/analytics-mcp` | ~220-line stdio→HTTP relay so desktop MCP clients can reach the above. Holds **zero** tool definitions. | Only incidentally |
| `apps/workflow-engine` | Node-based workflow + LLM agent engine. | **No** |
| `apps/workflow-studio` | React builder UI. | **No** |

The two are independent — **no Python import crosses the boundary** **[VERIFIED]**.

Authoritative docs: `apps/analytics-service/HANDOFF.md` (as-built) and `ARCHITECTURE.md`.
The three root-level `WORKFLOW-*.md` / `HANDOFF-MCP-*.md` files are **historical**; they carry
banners saying so. Don't take them as instructions.

---

## 2. Current state

```
feat/analytics-mcp-consolidation   b69bfc0   <- you are here, 9 commits, 1331 tests green
fix/workflow-engine-tooling        65fe4a3   <- 1 commit, deliberately unmerged
main                               941288b   <- untouched
```

Nothing has been pushed. The last session fixed 9 silent-wrong-answer defects in
`analytics-service` and folded the MCP tool surface into it. Test count went 1028 → 1331.

**`fix/workflow-engine-tooling` is held back on purpose** — two of its changes have unvalidated
production effects (tool schemas are no longer trimmed on long agent runs). Out of scope here;
don't merge it as a side effect of anything.

---

## 3. DO THIS FIRST — is the biggest gap actually dead code? **[VERIFIED]**

An AI-assistance feature was built and then deliberately removed (commit `941288b`). The LLM
gateway under `app/infra/llm/` was **kept** because it had one non-AI consumer. Coverage
suggests most of it is now orphaned:

| File | Statements | Coverage | Importers outside `app/infra/llm/` |
|---|---|---|---|
| `app/infra/llm/llm_provider.py` | 421 | **0%** | **One function only** — `get_embeddings_batch`, from `data_accelerator/services/methods.py:337`. The module defines 19 top-level functions. |
| `app/infra/llm/tool_schema.py` | 156 | **0%** | **None. Zero importers anywhere in the repo.** |

That is **577 of 2229 missed statements — 26% of the entire coverage gap.**

**[ASSESSMENT]** Decide *delete vs keep* before writing a single test here. If it is dead,
deleting it raises coverage from 80% to ~84% while removing code, which is strictly better than
testing it. If some of it is wanted for future work, say so explicitly and exclude it from the
coverage target rather than leaving it looking like a gap.

Two smaller 0% files that are **probably fine as-is** — both are CLI entry points, both are
exercised manually every session:
- `app/infra/db/postgres/migrate.py` (246 stmts) — you run this to set up the DB.
- `app/features/auth/bootstrap.py` (28 stmts) — mints users.

**[ASSESSMENT]** Consider `# pragma: no cover` or a coverage `omit` for these rather than
writing CLI-harness tests of marginal value. Make the decision visible either way, so the number
means something.

---

## 4. The real targets, ranked

Generated with `--cov=app --cov-report=term-missing`. **[VERIFIED]**

### 4a. `app/features/mcp/tools/` — the best target by a distance

The newest code in the repo, added last session, and the layer an LLM client actually drives.

| File | Missed | Coverage |
|---|---|---|
| `context.py` | 210 | **15%** |
| `pipeline.py` | 107 | **24%** |
| `curate.py` | 99 | **40%** |
| `_common.py` | 33 | 67% |
| `orient.py` | 33 | 55% |
| `compute.py` | 32 | 63% |
| `look.py` | 29 | 57% |
| `artifacts.py` | 20 | 41% |

≈**563 statements**, 25% of the whole gap, in one directory.

**Why it matters more than the raw number.** These tools translate service errors into guidance
a model can act on in one step — `sheet-selection-required` returns the sheet names,
`unknown-column` returns the available columns, a SQL catalog error returns the real table
names. That translation layer is **the product** for an MCP client, and it is mostly untested.
`_common.py::explain` is where that logic lives.

**Existing tests to read before adding any:** `tests/test_mcp_endpoint.py` (integration, drives
the real MCP call path) and `tests/unit/test_mcp_patch_body.py`,
`tests/unit/test_mcp_unknown_arguments.py`, `tests/unit/test_mcp_identity_errors.py` (pure).

**[ASSESSMENT]** Prefer unit tests over the DB — see §5. Much of `_common.py` and the render
layer is pure and needs no Postgres at all.

### 4b. `data_accelerator/services/`

| File | Missed | Coverage |
|---|---|---|
| `methods.py` | 120 | **20%** |
| `sampling.py` | 93 | 76% |

`methods.py` is where the one live `app/infra/llm/` consumer sits, so §3's decision touches it.

### 4c. Worth a look, lower value

`shared/filters.py` 75% (35 missed — operator branches, and this file was central to a fixed
defect), `files/services/downloads.py` 83%, `shared/data_io.py` 87%, `main.py` 74% (lifespan).

---

## 5. How tests work here — read before writing any

**Two layers, and the distinction is load-bearing:**

- `tests/unit/` — **no Postgres, no storage.** Exempt from the DB-truncating fixture. Fast.
- `tests/` — integration. In-process ASGI (`ASGITransport`) against the real app + real
  Postgres, parametrized over **both** local-disk and S3/MinIO backends, so **each test runs
  twice**. Budget accordingly: one integration test = two entries in the count.

**Prefer `tests/unit/`.** It is faster, it has no fixture coupling, and it does not multiply.
Reach for integration only when the behaviour genuinely needs the DB — e.g. a SQL predicate.
Precedent: `tests/test_completed_run_demotion.py` is integration *because* the fix under test is
an `AND status = 'running'` predicate, and its docstring says exactly that.

**Conventions to match:**
- Fixtures and helpers live in `tests/conftest.py` — `auth()`, `upload_inline()`,
  `upload_file()`, `make_orders_workbook()`, `make_crm_workbook()`. **Read their signatures.**
  (`upload_inline` takes a JSON *string* and returns a response dict — a wrong guess here cost
  time last session.)
- Test names in this repo are full sentences describing the guarantee, not
  `test_foo_returns_bar`. e.g. `test_a_completed_run_is_not_clobbered_when_only_the_job_fails`.
- Docstrings explain *why the test exists* — usually the bug it pins. Follow this; it is the
  most valuable thing about the existing suite.
- Several files carry deliberate **tripwire tests** that assert structural properties (e.g.
  `tests/unit/test_run_close_guards.py` asserts which `fail_run` functions carry a status
  guard). If one fires, **go read what it points at** — it is telling you a contract exists,
  not that it is stale.

**The bar the last session used, and worth keeping:** a test for a bug fix should be verified to
**fail against the pre-fix code**. Do it by copying the file to `/tmp`, hand-editing it back,
running, and restoring. **Never with git** — see §6.

---

## 6. Environment traps — these produce fake failures

**[VERIFIED]** All of these bit someone last session.

1. **Never run two `pytest` invocations at once.** They share one Postgres and one storage dir,
   and the autouse `_db_cleanup` fixture truncates every mutable domain table before each test.
   A concurrent run fails in ways that look exactly like real bugs.

2. **Stop any dev `uvicorn` before running the suite.** A server on :8001 runs a job worker
   polling the same Postgres every second, and `_claim_next` is a global
   `FOR UPDATE SKIP LOCKED` — it steals the pending job and makes
   `test_relationships.py::test_discovery_runs_as_a_job_the_worker_can_claim` fail spuriously.
   Check with `pgrep -af "[u]vicorn.app.main"`. (Note the `[u]` — a plain `uvicorn` pattern
   matches your own shell command.)

3. **`pytest.ini` already sets `-q`.** Adding another `-q` makes it `-qq` and **suppresses the
   summary line entirely** — the run looks like it produced no result. Use
   `venv/bin/python -m pytest tests/ -p no:randomly`.

4. **Some `venv/bin/*` shebangs are stale**, pointing at a pre-move path
   (`/home/saketh/Projects/playground/demo/...`, missing `work/`). `venv/bin/pip` is broken in
   `analytics-service`; `venv/bin/pytest` is broken in `workflow-engine`. **Always use
   `venv/bin/python -m <tool>`.**

5. **NEVER use `git stash`, `git checkout <file>`, `git restore`, or `git reset`** to test a
   pre-fix state. An agent did this last session and destroyed a large body of uncommitted work,
   recovering only by luck. Copy to `/tmp` and restore from there.

**Setup:**
```bash
docker start analytics-pg analytics-minio          # both were already up last session
cd apps/analytics-service
venv/bin/python -m app.infra.db.postgres.migrate apply      # or `reset` for a clean slate
venv/bin/python -m pytest tests/ -p no:randomly             # ~2 min, expect 1331 passed
```

The dev DB is **safe to reset** — the user confirmed this. It currently has 23 migrations and
0 domain rows. Any dataset/user ids you record in a doc will be destroyed by the next test run;
mint fixtures instead of hardcoding ids.

---

## 7. Coverage tooling

`pytest-cov 7.1.0` was installed into the venv last session and **added to
`requirements.txt`**. It was not there before, which is why no baseline existed.

```bash
venv/bin/python -m pytest tests/ -p no:randomly --cov=app --cov-report=term-missing:skip-covered
venv/bin/python -m coverage report --format=total      # just the number
venv/bin/python -m coverage html                       # htmlcov/index.html
```

Baseline to beat: **80%**, 11385 statements, 2229 missed, 44 files already at 100%.

---

## 8. Suggested order

1. **Settle §3** (dead `app/infra/llm/`). Delete or explicitly exclude. Biggest single move, and
   it is a *deletion*, not test-writing.
2. **`app/features/mcp/tools/_common.py`** — the error-translation logic. Pure, high value,
   and it is the product surface for an MCP client.
3. **`context.py` / `pipeline.py` / `curate.py`** — the three worst files, ~416 statements.
4. **`data_accelerator/services/methods.py`** — after §3, since they are entangled.
5. Re-measure. **[ASSESSMENT]** A number is not a goal on its own; a covered line that no
   assertion depends on is worse than an honest gap, because it looks like safety.

---

## 9. Open, not blocking

- Nothing is pushed. `main` is untouched. Both branches are local.
- History is **not bisectable** — the final state is verified green, but the 9 commits were not
  individually run. Commits are file-granular because interactive staging was unavailable.
- `fix/workflow-engine-tooling` still needs its prompt-size effect validated against a live
  agent run before merging. Not a testing task.
- Known non-defects deliberately left alone, all documented in the commit messages:
  `files/repo.py::fail_version` is unguarded (versions, not runs); a `transformation_runs` reaper
  is wanted but needs its own classifier; saved views corrupted before the filter fix are
  **unrepairable** and `scripts/audit_collapsed_view_filters.py` only identifies them.
