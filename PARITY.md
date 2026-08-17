# Parity — the build against the design, and against the API

**Written 2026-08-17.** Two questions, answered with evidence rather than impression:

1. Does every design prototype have a React implementation?
2. Does the UI use everything the analytics service can actually do?

Where the answer is no, this document gives the reason. A gap with a stated
reason is a decision; a gap without one is a defect, and there should be none of
the second kind by the time you finish reading.

## How to re-derive these numbers

Endpoint coverage is measured, not remembered:

```bash
cd apps/workflow-studio
python3 - <<'PY' > /tmp/paths.tsv
import json
spec = json.load(open('../analytics-service/openapi.json'))
for p in spec['paths']:
    segs = [s for s in p.replace('/api/v1','').split('/') if s and not s.startswith('{')]
    ops = ','.join(m.upper() for m in spec['paths'][p] if m in ('get','post','put','patch','delete'))
    print(p.replace('/api/v1','') + '\t' + (segs[-1] if segs else '') + '\t' + ops)
PY
# NOTE: exclude the generated schema file, or every path "matches" its own type.
find src -name '*.ts*' ! -name 'analyticsSchema.d.ts' -exec cat {} + \
  | perl -0pe 's{/\*.*?\*/}{}gs' | perl -pe 's{//.*$}{}' > /tmp/code.txt
while IFS=$'\t' read -r path seg ops; do
  [ -z "$seg" ] && continue
  n=$(grep -oE "['\"\`][^'\"\`]*/$seg[^'\"\`]*['\"\`]" /tmp/code.txt | wc -l)
  [ "$n" -eq 0 ] && printf "%-22s %s\n" "$ops" "$path"
done < /tmp/paths.tsv | sort -k2
```

Two traps that produced wrong answers on the first three attempts, recorded so
nobody repeats them:

- **`src/shared/lib/analyticsSchema.d.ts` is generated from the OpenAPI document
  and contains all 107 paths as type keys.** Include it and coverage reads 107 /
  107 — which is how a coverage audit tells you everything is fine.
- **The codebase documents endpoints in comments.** Strip comments before
  matching or `POST /webhooks` in a docstring counts as a call site.

## 1. Prototypes → React

All 21 prototypes are accounted for. No screen is missing; the gaps are *inside*
screens and are itemised in §3.

| Prototype | Surface | State |
|---|---|---|
| `terminal.html` | `/data` cockpit | built |
| `terminal-cmdk.html` | ⌘K palette | built |
| `terminal-query.html` | `/query` | built |
| `terminal-analytics.html` | Analytics lens | built |
| `terminal-aggregate.html` | `/aggregate` | built |
| `terminal-pivot.html` | `/pivot` | built |
| `terminal-quality.html` | Quality lens | built |
| `terminal-column.html` | `/column` | built |
| `terminal-versions.html` | Versions lens | built |
| `terminal-relations.html` | Relations lens | built |
| `terminal-transform.html` | Transform lens | built |
| `terminal-sampling.html` | `/sampling` | built |
| `terminal-library.html` | Library lens | built |
| `terminal-admin.html` | `/admin` | built |
| `terminal-runs.html` | `/runs` | built |
| `terminal-ingest.html` | `/ingest` | built |
| `terminal-adaptive.html` | shape rules R1–R11 | built |
| `terminal-shapes.html` | conformance bench | **became `npm run check:rules`** — it is a diagnostic, not a screen, so it shipped as an executable test rather than a page |
| `terminal-workflows.html` | `/projects` | **theming only, by scope decision** |
| `terminal-editor.html` | `/editor` | **theming only, by scope decision** |
| `terminal-appbuilder.html` | `/builder` | **theming only, by scope decision** |

The three theming-only pages were ruled out of scope in
`HANDOFF-INSTRUMENT-INTEGRATION.md` §1 and re-confirmed at kickoff: they pick up
new token values and nothing else. They re-theme without a code edit because the
token layer is an alias layer — verified by test (`tests/shape.spec.ts` asserts
the studio shell is absent on all four untouchable routes).

## 2. API → UI

107 paths in `apps/analytics-service/openapi.json`.

See §4 for the current count and the remaining list. Everything still uncalled
falls into one of three buckets, and each item names its bucket:

- **DEFERRED** — buildable, not yet built. This is a to-do, not a justification.
- **NOT SURFACEABLE** — the endpoint exists but has no honest home in this UI
  yet, for a stated reason.
- **DELIBERATELY UNWIRED** — wiring it would be actively wrong.

## 3. Gaps inside screens, with the reason

### Deliberately unwired

| Capability | Why |
|---|---|
| `DELETE /datasets/{id}` and any version delete | Destroys every version and its storage files, and there is no "delete one version" verb to soften it. Annotations (rules, tags, dictionary entries, charts, views) are deletable because removing one loses no rows. That line is in `useDatasetActions.ts` and was drawn deliberately. |
| Any AI/LLM surface on datasets | AI features were built into analytics-service and then **removed on purpose**. The editor's and builder's LLM features belong to workflow-engine and are legitimate; reintroducing one here would undo a decision. |
| A numbered pager anywhere on row data | Paging is cursor-based. `terminal-adaptive.html:659` draws a numbered pager with a jump to page 54,767; `terminal.html:744` says in its own markup that page jumps are impossible. **The prototype is wrong and was not ported** — see `PagerButton.tsx`. |
| A pager on `/aggregate` | `AggregateRequest` has `limit` and no offset and no cursor. There is no next page to ask for, so a pager would imply one. |

### Not surfaceable yet

| Capability | Why |
|---|---|
| MCP tool list (27 tools) | MCP is mounted as JSON-RPC at `/api/v1/mcp` and is **excluded from the OpenAPI document** (`app/main.py`). Enumerating the tools needs an out-of-band `tools/list` call; hardcoding the list would drift silently. |
| Full 7×5 RBAC permission matrix | Not exposed by any endpoint. Rendering it means 35 hardcoded policy cells that go stale without warning. The one row that carries the governance crux — `dataset:read_sensitive` withheld from `editor` — is stated in prose and derived per-seat. |
| Per-member superuser flags | There is no `GET /auth/users`. `/auth/me` reports `is_superuser` for the acting seat only, so a complete list cannot be built. The admin page says so rather than implying completeness. |
| Tag "moved by promote · gate passed" provenance | `TagInfo` carries only `created_at`/`updated_at`. The richer story needs `/tags/{tag}/history`, now wired — see §4. |
| Per-step row counts in the transform pipeline | Those come from a *run*. `compile` returns schema only, and this lens is deliberately schema-only (see below). |
| Timings and scanned-row counts on pivot/SQL | Neither response carries a duration or a scanned-row count. |
| Per-row "which step selected this" in sampling | The API returns no per-row step provenance. |

### Deliberately schema-only

`POST /transformations/compile` has two modes. Omit `rows` and it is a
schema-only compile needing `dataset:read`. Set `rows` and it becomes a read of
the pipeline's **output**, gated behind raw access — because a `compute` step can
copy a sensitive column into a new name, so masking by source column name would
not hold. The Transform lens only ever asks the first question, which is why it
works identically for every seat: a viewer can design a pipeline without ever
being handed a value they are not cleared for.

## 3b. Where the prototype and the API disagree

These are the cases that most look like "the build doesn't match the design".
In each one the prototype asserts something the service does not do, and the
build followed the service. Each was verified against `openapi.json` or the
service source, not inferred.

| # | Prototype claim | What the API actually does | Where |
|---|---|---|---|
| 1 | A numbered pager with a jump to page 54,767 | Paging is cursor-based; `QuerySpec` returns `next_cursor` and no offset. `terminal.html:744` contradicts it in its own markup, and `terminal-query.html` strikes it through | `terminal-adaptive.html:659` |
| 2 | "14 of 36 operators greyed" | A mock number. The real gate is `shared/query/validate.py`: 12 string ops need text, 4 date ops need a date, everything else works on any dtype — so **16** grey for numeric, **12** for date, **4** for text | `terminal-query.html` |
| 3 | Sort tiebreak is the primary key | `compile.py` appends **every remaining column ascending** to make the order total | `terminal-query.html` |
| 4 | Page size invalidates the cursor | `spec_hash` is a sha256 over the spec **excluding** `cursor` and `limit` | `terminal-query.html` |
| 5 | A `422 sheet-selection-required` gate at upload | That is a **read-time** contract. At ingest, `include_sheets` is an opt-in filter and every sheet is ingested by default | `terminal-ingest.html` |
| 6 | The duplicates probe shows "no cell values" | It returns whole rows (masked per seat). The build states what it actually shows instead of repeating the claim | `terminal-quality.html` |
| 7 | A `head` / first-N sampling method | No such method exists in the engine. `systematic` (every k-th row) is the nearest real thing and is what shipped | `terminal-sampling.html` |
| 8 | Saved SQL views, query history, EXPLAIN, export-from-result | None of these endpoints exist | `terminal-pivot.html` |
| 9 | Join cardinality (1:N / N:M) | Cardinality does not exist server-side at all | `terminal-relations.html` |
| 10 | "37 SQL runs today", time-window filters | `GET /jobs` has no date filter and there is no history endpoint | `terminal-runs.html` |
| 11 | "Which run produced each artifact" | `FileEntry` carries no run id — the storage key path is the only provenance available | `terminal-library.html` |

**One case went the other way.** A build pass reported "tag history — no endpoint
or hook" and omitted it. `GET /datasets/{id}/tags/{tag_name}/history` exists.
That was a wrong justification, not a real constraint, and it is now wired. It
is recorded here because it is the exact failure this document exists to catch:
a gap that arrives wearing a reason.

## 4. Current coverage

Re-run the block in "How to re-derive these numbers" to refresh. The count at
the time of writing, and the remaining items, are tracked in the section below.

<!-- COVERAGE:BEGIN -->
**107 of 107 paths are called.** (79 before the wiring round; 105 after it; the last two closed separately.)

The segment-grep above flags seven, but five are false negatives it cannot see,
because it only matches paths written as ONE literal:

| Flagged | Reality |
|---|---|
| `.../versions/{v}/duplicates` and `/missing` (×4 forms) | Called. `explorerPath()` in `useQualityExplorers.ts` composes `base + leaf`, so the path never appears as a single literal. |
| `.../relationships/{id}/reject` | Called. `useAnalysis.ts` builds `` `…/relationships/${id}/${action}` `` where `action` is `'confirm' \| 'reject'`. |

**Nothing is uncalled.** The last two were closed, and both corrected an
assumption on the way:

- `POST /datasets/{id}/sheets/{sheet}/replace` does NOT mutate a version. It
  writes the NEXT version and carries every other sheet across copy-on-write
  (reusing the base rows' `storage_key`), so immutability is preserved, tags
  keep resolving to identical bytes, and an existing diff still describes the
  base. The UI says so above the control.
- `POST /sample/coordinated` coordinates **sheets, not datasets** — the request
  carries a single `dataset_id`. The UI says that too.
<!-- COVERAGE:END -->

## 5. What this does not claim

- **Accessibility** is untested — no axe pass, no keyboard-only traversal.
- **Responsive layout** is untested; one 1440×1024 viewport.
- **Volume** is untested; the largest fixture is 512 rows and 189 columns.
- **Cross-team isolation** is only partially exercised — the outsider seat is a
  viewer of the *same* team; a genuinely foreign team is not constructed.
- Status colours in the grid come from an explicit ~30-word vocabulary in
  `CellValue.tsx`. A word outside it stays neutral. If a dataset uses `active`
  to mean something bad, the mark is wrong — which is why the vocabulary is one
  visible list rather than a regex spread across the grid.
