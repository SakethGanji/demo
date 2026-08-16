# Workflow ↔ Analytics Integration — Findings & Plan

> ## ⚠️ PARTLY SUPERSEDED — the analytics half is built, the workflow half is descoped
>
> Written 2026-08-06. Its central recommendation — expose the platform to LLMs properly,
> with `run_sql` as the workhorse — **was built and shipped the same day**, though not into
> `workflow-engine`. The 27-tool MCP surface now lives inside `analytics-service` at
> `/api/v1/mcp`. See `apps/analytics-service/HANDOFF.md`.
>
> | Finding / phase | Status |
> |---|---|
> | §2 seven broken call sites | **Still broken. Descoped, not scheduled.** Dead code, not work-in-progress. |
> | §4a no discovery | **Solved for MCP clients** (`search_datasets` → `describe_dataset` → `get_data_dictionary`). Still true in the Studio. |
> | §4b handles flow in, never out | **Solved for MCP** — artifacts + `read_artifact` with server-side projection. |
> | §4c volume guards inconsistent | **Fixed in the engine** — see `WORKFLOW-ENGINE-TOOL-DEFECTS.md`. |
> | §5 `run_sql` is the highest-leverage missing tool | **Built.** This was the best call in the document. |
> | Phases 0–3 | **Superseded** by the MCP surface. |
> | Phase 4 Studio UX | **Not done — descoped.** |
>
> **One recommendation here was deliberately reversed.** §6's *"Deliberately NOT agent
> tools"* — transformations, joins, publish — argued they belong on the canvas with a human
> in the loop. The MCP server ships `transform_data`, `join_datasets` and `publish_result`
> as agent tools, because blind evaluation showed a read-only server could *diagnose*
> missing documentation and absent validation runs while offering no way to fix either.
> Deletes remain unexposed, which is where the line actually landed.
>
> **§8's open questions are answered:** (1) identity is the service's own `get_principal`,
> per inbound message, not a service account — so no agent can reach data its user cannot;
> (2) consequently moot; (3) `/report` was never built and the tool referencing it is dead
> along with the rest of §2.

**Status: investigation only. No code was changed.**
Date: 2026-08-06. Written as a handoff for a fresh session.

---

## 0. What this document is

An assessment of how well `apps/analytics-service` (a 134-endpoint dataset
platform) is exposed to (a) the visual workflow builder and (b) LLM agents
running inside the workflow engine.

Three claims are made below. They are tagged so you know how much to trust them:

- **[VERIFIED]** — reproduced by running the code. Repro steps included.
- **[READ]** — read directly out of the source. File/line cited.
- **[ASSESSMENT]** — my judgement. Argue with it.

---

## 1. The three apps

| App | Stack | Role |
|---|---|---|
| `apps/analytics-service` | Python / FastAPI / DuckDB / Postgres | Dataset platform. 134 routes under `/api/v1`. Upload, version, query, profile, pivot, aggregate, transform, join, quality rules. |
| `apps/workflow-engine` | Python / FastAPI | Node-based workflow execution + LLM agent nodes. Serves the node catalog at `/api/nodes`. |
| `apps/workflow-studio` | React / TS / Vite / ReactFlow | Visual builder UI. Talks **only** to workflow-engine (`localhost:8000`). |

Only workflow-engine calls analytics-service. The studio never does.

---

## 2. FINDING 1 — the integration is broken end-to-end **[VERIFIED]**

Every one of the 7 call sites into analytics-service fails. Four independent
breakages, stacked.

### Repro

```bash
cd apps/analytics-service
venv/bin/python -m uvicorn app.main:app --port 8001 &

# The exact call the LLM tool constructs:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8001/sample \
  -H 'Content-Type: application/json' \
  -d '{"file_path":"/tmp/accelerator/datasets/abc.parquet","method":"random","sample_size":5}'
# -> 404

# With the prefix corrected, still no auth header:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8001/api/v1/sample \
  -H 'Content-Type: application/json' \
  -d '{"file_path":"/x.parquet","target_total_volume":5,"sampling_steps":[{"method":"random","sample_size":5}]}'
# -> 401  {"detail":"Missing X-User-Id header"}
```

### The four breakages

**(a) Missing `/api/v1` prefix → 404.** All call sites build
`f"{service_url}/sample"`. Analytics-service mounts everything under
`settings.api_prefix = "/api/v1"` (`app/infra/config.py:34`).

Affected files:
- `workflow-engine/src/nodes/ai/tools/data_sample_tool.py:96`
- `workflow-engine/src/nodes/ai/tools/data_aggregate_tool.py:116`
- `workflow-engine/src/nodes/ai/tools/data_report_tool.py:108`
- `workflow-engine/src/nodes/ai/tools/data_profile_tool.py:90`
- `workflow-engine/src/nodes/data/sample.py:466`
- `workflow-engine/src/nodes/data/profile.py:293`
- `workflow-engine/src/nodes/data/aggregate.py:383`

**(b) No auth header anywhere → 401.** `grep -rn "X-User-Id\|X-Team-Id"` across
all of `workflow-engine/src/` returns **nothing**. Analytics-service has
`auth_enabled: bool = True` by default (`app/infra/config.py:38`).

**(c) `/report` does not exist.** `data_report_tool.py` POSTs to `/report`.
There is no such route in analytics-service — the tool is dead on arrival even
with (a) and (b) fixed.

**(d) Stale storage path + an architectural problem.**
`workflow-engine/src/nodes/ai/tools/_analytics_helpers.py`:

```python
DATASETS_DIR = "/tmp/accelerator/datasets"

def resolve_dataset_ref(input_data):
    payload = dict(input_data)
    dataset_id = payload.pop("dataset_id", None)
    if dataset_id:
        payload.pop("data", None)
        payload["file_path"] = f"{DATASETS_DIR}/{dataset_id}.parquet"
    return payload
```

Two problems:
1. **The path shape is wrong.** Real layout is
   `datasets/{team_id}/{dataset_id}/v{NNNNNN}/parquet/dataset.parquet`.
   No flat `{id}.parquet` files exist (`ls /tmp/accelerator/datasets/*.parquet` → none).
2. **It's unfixable as designed.** It reads parquet straight off local disk,
   which bypasses team RBAC entirely and breaks under the S3 backend. And even
   with a correct path, `file_path` sources are **superuser-only**:
   `app/features/data_accelerator/api.py:107` raises 403 for non-superusers,
   deliberately — *"a raw file_path reads straight from the server filesystem
   with no team scoping."*

   → This code path must be replaced with `dataset_id` over HTTP, not patched.

**[ASSESSMENT]** This was written against an earlier analytics-service and has
drifted. The storage-layout change on 2026-08-05 widened the gap, but (a), (b)
and (c) predate it.

---

## 3. FINDING 2 — coverage is 3 of 134 endpoints **[READ]**

| Surface | What's exposed |
|---|---|
| Canvas nodes (in `register_all_nodes()`, appear in palette) | `Sample`, `Profile`, `Aggregate` |
| LLM agent tools (via agent's `builtinTools` picker) | `sample_data`, `profile_data`, `aggregate_data`, `generate_report` (phantom) |

**Not a defect:** the tool classes are absent from the node registry
(`workflow-engine/src/engine/node_registry.py:186-264`) by design — they attach
via the agent's `builtinTools` multiOptions
(`src/nodes/ai/ai_agent.py:196-216`), resolved through
`src/nodes/ai/inline_config.py:100-127`. That's the intended mechanism, not an
oversight. Don't "fix" it.

---

## 4. FINDING 3 — three structural gaps

These matter more than the plumbing bugs.

### 4a. No discovery — the fatal one **[READ]**

Every data tool takes `dataset_id`. **No tool returns one.** There is no
`list_datasets`, `search_datasets`, or `describe_dataset`.

An agent is handed verbs with no nouns: it can sample a dataset but cannot find
one, and cannot learn a dataset's columns before aggregating by them.

Same story in the Studio: the dataset is a **free-text box**. From
`workflow-engine/src/nodes/data/sample.py`:

```python
NodeProperty(
    display_name="Dataset ID", name="datasetId", type="string",
    default="", placeholder="dataset-uuid",
    description="ID of the dataset in the analytics service",
)
```

Rendered by `workflow-studio/.../ndv/DynamicNodeForm.tsx` as a plain
`StringField`. The user must obtain a UUID out-of-band and type it. Sheet names
and column names (stratify/weight/time columns) are also free-text — typed blind.

The precedent for the fix already exists: `WorkflowSelectorField.tsx` is a
`workflowSelector` property type that renders a populated `<select>`. A
`datasetSelector` would slot in identically. It doesn't exist.

Analytics-service already exposes everything a picker needs (`GET /datasets`,
`/datasets/{id}/sheets`, `/sheet-metadata/{key}/columns`, `/versions`, `/tags`).
None of it is reachable from the builder — workflow-engine has no `/datasets`
proxy route, and `workflow-studio/src/shared/lib/config.ts:12` has `analytics`
as a **commented-out placeholder**.

### 4b. Handles flow in, never out **[READ]**

`dataset_id` → `file_path` on input means rows don't transit the model on the
way *in*. Good instinct. But no tool returns a handle —
`data_sample_tool.py:100-102` explicitly deletes it:

```python
result.pop("download_url", None)
result.pop("data", None)
result.pop("output_path", None)
```

**Consequence:** you cannot chain. sample → aggregate the sample → profile the
result is impossible by handle; every intermediate must round-trip through the
LLM context as JSON.

Analytics-service already produces the right thing — a registered artifact with
a `result_file`. The tool throws it away.

`AgentContext` has `scratchpad` / `shared_store` / `context_store`
(`src/nodes/ai/ai_agent.py:111-138`) which would be the right substrate for a
handle registry — but they're only writable by the model via the `memory_store`
tool, not by a tool executor depositing a large result and returning a key.

### 4c. Volume guards are inconsistent **[READ]**

Only `data_sample_tool` sets `return_data=False`. `profile`, `aggregate` and
`report` end with a bare `return response.json()` — full histograms, correlation
matrices, entire rendered HTML documents.

The single global backstop is `_compress_tool_result` in
`src/nodes/ai/ai_agent.py`, `max_chars=8000`, **hardcoded with no override**.
Its dict branch only truncates *top-level string values* — nested numeric
structures fall through to a head+tail slice that hands the model
**syntactically invalid JSON**.

For comparison, `mongo_query_tool` does this properly: `max_response_bytes`
(200,000), `max_limit` (50), and a `_truncated` flag.

---

## 5. The "can an LLM just take the full dataset?" question

**[ASSESSMENT]** It physically can, and it shouldn't.

Context windows are 1M now, so a small dataset fits. But:
- cost and latency scale with every row, on **every turn** of the agent loop;
- LLMs are unreliable at exact arithmetic over many rows — a `SUM` over 10k rows
  is a coin flip, a `SUM` in DuckDB is not;
- it wastes the query engine you already have.

**The right split is: LLM orchestrates, DuckDB computes.** The model decides
*what* to ask; the service computes it and returns a bounded result plus a handle
to the full output.

The analytics service is *already built this way* — bounded page responses,
`truncated` flags, artifacts with `download_url`. The tools just discard the
handles (§4b). This is the single most important thing to fix conceptually.

### Which is why `run_sql` is the highest-leverage missing tool

`POST /api/v1/datasets/{id}/versions/{n}/sql` is already:
- SELECT-only, enforced (400 `select-only` on anything else)
- row-capped, with a `truncated` flag
- size-guarded — 413 over `MAX_SQL_MATERIALIZE_BYTES` (512 MB)
- file-read blocked, with error text sanitized so a rejected path isn't echoed back
- returns a `result_file` artifact (the handle)

One `run_sql` tool covers more analytical ground than twenty hand-wrapped
endpoints, and the security work is done. Verified by the e2e suite
(`scripts/e2e_curl.sh`, section F).

---

## 6. Recommended work, in priority order

### Phase 0 — repair what exists (do this first)
Until this is done, the tools that exist don't work at all, so anything new is
built on sand.

1. Add `/api/v1` prefix to all 7 call sites.
2. Add auth headers. **Design decision needed** — see Open Questions.
3. Delete `_analytics_helpers.resolve_dataset_ref`; pass `dataset_id` through to
   the API instead of rewriting it to `file_path`.
4. Either implement `/report` in analytics-service or delete `data_report_tool`.
5. Add `return_data=False` + strip `data` in the profile/aggregate tools.

### Phase 1 — discovery (unblocks everything)
- `search_datasets` → `GET /datasets`, `/datasets/search`
- `describe_dataset` → `GET /datasets/{id}` + `/sheets`
- `get_data_dictionary` → `/sheet-metadata/{key}/columns` — business names,
  units, **sensitivity**. Semantic grounding instead of guessing from column names.

### Phase 2 — the analytical workhorse
- `run_sql` → the sandboxed endpoint (§5)
- `query_dataset` → `POST .../query` for structured filter/sort/paginate

### Phase 3 — context & quality
- `get_dataset_health`, `get_column_stats`, `list_validation_failures`,
  `find_columns` (cross-dataset search)

### Phase 4 — Studio UX
- A `datasetSelector` property type mirroring `WorkflowSelectorField.tsx`
- Either an `analytics` backend entry in `config.ts`, or a `/api/datasets` proxy
  on workflow-engine to feed it

### Deliberately NOT agent tools **[ASSESSMENT]**
Transformations, joins, publish, delete. These mutate state; some are
irreversible. They belong as canvas nodes with a human in the loop. The join
builder already encodes this instinct — it refuses unconfirmed relationships.

---

## 7. Implementation notes for whoever picks this up

- **Generate, don't hand-write.** The existing `apiRequest` tool
  (`src/nodes/ai/tools/api_request_tool.py`) takes a real JSON Schema and a fixed
  URL. Tools can be generated from the OpenAPI spec at
  `http://localhost:8001/openapi.json`.
- **Don't wrap all 134.** Every tool schema is serialized on **every** LLM call.
  The agent's dynamic tool filter only engages after iteration 3.
- **OpenAI strict mode rewrites `required` to include every property**
  (`src/engine/tool_schema.py`, `harden_schema`). Endpoints with many optional
  filters will be misrepresented under GPT-4o. Check this before generating.
- **`workflow_tool` is not the right wrapper for REST endpoints** — its input
  schema is an opaque `{"input": object}` with no property definitions, so the
  model gets zero parameter guidance. Use `apiRequest` per endpoint; reserve
  `workflow_tool` for genuine multi-step orchestration.
- There is **no MCP server** anywhere in the monorepo. If these APIs should be
  usable by LLMs outside this engine (Claude Desktop, other agents), that's the
  standard surface and it doesn't exist yet.

---

## 8. Open questions — need a human decision

1. **How should workflow-engine authenticate to analytics-service?**
   Analytics-service currently uses POC header identity (`X-User-Id`), and the
   SSO/JWT swap is still outstanding (isolated to
   `app/features/auth/deps.py::get_principal`). Options: a service account with
   a fixed superuser id (simple, but loses per-user attribution and audit
   fidelity); propagate the end user's identity from the workflow execution
   context (correct, more plumbing); wait for the SSO work and do it properly
   once. **This choice affects every call site, so decide before Phase 0 item 2.**

2. **Should the agent be able to reach data it couldn't reach as a user?**
   Related to (1). If a service account is used, an agent inside a workflow can
   read any team's data. That may be acceptable inside a trusted workflow, or it
   may not — it depends on who can author workflows.

3. **Is `/report` wanted at all?** It's referenced by a tool but was never
   built. Implement, or drop the tool?

---

## 9. File reference map

**Analytics service**
- Route prefix / auth defaults: `apps/analytics-service/app/infra/config.py:34,38`
- `file_path` superuser restriction: `app/features/data_accelerator/api.py:97-111`
- Sandboxed SQL + guards: `app/features/explorer/service.py:60,381`
- Storage key layout: `app/infra/db/storage.py` (`DatasetLayout`, `ArtifactLayout`)
- Architecture reference: `apps/analytics-service/ARCHITECTURE.md`
- As-built record: `apps/analytics-service/HANDOFF.md`

**Workflow engine**
- Analytics bridge (delete this): `src/nodes/ai/tools/_analytics_helpers.py`
- Data tools: `src/nodes/ai/tools/data_{sample,profile,aggregate,report}_tool.py`
- Canvas data nodes: `src/nodes/data/{sample,profile,aggregate}.py`
- Agent loop, limits, result compression: `src/nodes/ai/ai_agent.py`
- Tool attachment/resolution: `src/nodes/ai/inline_config.py:100-127,186`
- Node registration: `src/engine/node_registry.py:186-264`
- Provider schema hardening: `src/engine/tool_schema.py`
- Generic REST tool (the model to copy): `src/nodes/ai/tools/api_request_tool.py`

**Workflow studio**
- Backend config (`analytics` is commented out): `src/shared/lib/config.ts:12`
- API client: `src/shared/lib/api.ts`
- Node palette (server-driven from `/api/nodes`): `src/features/workflow-editor/components/node-creator/NodeCreatorPanel.tsx`
- Property renderer (where `datasetSelector` would go): `src/features/workflow-editor/components/ndv/DynamicNodeForm.tsx:80`
- The pattern to copy: `src/features/workflow-editor/components/ndv/WorkflowSelectorField.tsx`
