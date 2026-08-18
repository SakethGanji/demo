# Agent Platform — Handoff

**Written 2026-08-18.** Read this first in a new session; it is the single authoritative record.
Companion files: the 16 HTML prototypes in `design-prototypes/terminal-agent-*.html` (start at
`design-prototypes/agents-index.html`), and the long-form plan at `~/.claude/plans/fizzy-popping-gray.md`.

---

## 1. What this is

Adding **AI agents** to a product that already has a governed data platform (`apps/analytics-service`), an
n8n-shaped workflow engine (`apps/workflow-engine`, 28 node types), and a React studio (`apps/workflow-studio`).

**The thesis, in one line:** *use the expensive nondeterministic thing ONCE to produce the cheap deterministic
thing that runs forever.* You describe a task in English; an agent explores your data, builds a workflow or an
app, you accept it, and from then on it runs with **no model in the loop** — pennies a year instead of pounds.

**Why it might be defensible** (from competitive research, six platforms incl. Google/AWS): nobody enforces
governance on the *tool path* — AWS Bedrock Guardrails "will not detect PII … when models respond with
tool_use output parameters"; Google Model Armor "does not screen" custom agents; Writer states permissions
"don't transfer over to Knowledge Graph". And nobody does agent-run → frozen deterministic artifact (Dust
*deprecated* theirs in Oct 2025). **Both bets are on empty ground.**

---

## 2. Status — built vs designed vs neither

### Actually built and proven (real rows in Postgres: 12 agents, 21 runs, 31 trace events)

| | Where |
|---|---|
| 9 tables, migration applied | `src/db/migrations/20260818120000_agents.sql` |
| SQLModel classes + id helpers | `src/db/models.py` (appended), `src/utils/ids.py` |
| **`AgentRuntime`** — runs the 2,745-line loop OUTSIDE a workflow, **zero lines of it changed** | `src/engine/agent_runtime.py` |
| Event recorder → `agent_run_events` | `src/engine/agent_event_recorder.py` |
| Agent CRUD, sessions, runs, cancel, replay | `src/services/agent_*.py`, `src/routes/agents.py`, `src/routes/agent_runs.py` |
| 16 routes registered and live | `src/routes/__init__.py` |
| **Node ladder** — 7 tools, 1,293 tok, drove a real model that self-corrected | `src/nodes/ai/tools/node_ladder.py` |

Proven end to end over HTTP: create agent → session → run → `calculator` invoked → `391` → events persisted
with contiguous seq; 409 on concurrent turns; cancel drives terminal.

### Built but NOT connected
- **MCP connector** (`src/nodes/ai/connectors/**`, `src/services/connector_service.py`, `src/routes/connectors.py`)
  — imports cleanly, **zero connectors ever registered, zero tools discovered.** That subagent died during proof.
- **Node ladder** — works standalone; **no agent can reach it.** `agent_tool_resolver.py` has no `node` resolver.

### Tables exist, ZERO code
`agent_approvals` (checkpoints) · `promoted_tools` (the flywheel) · session→schedule ("make it recurring")

### Not built at all
- **SSE live streaming.** Only polling via `GET /api/agent-runs/{id}/events?after_seq=`.
- **Any UI.** Everything is API-only.
- The node→tool adapter (~40 lines) that would let an agent *use* a node directly.

**Honest fraction: roughly a third of what was designed, and it is the third everything else needs.**

---

## 3. Locked decisions (settled with the user — do not relitigate)

1. **Agent = definition, not actor.** Own tables, not a facade over `workflows` — `ExecutionRepository.start()`
   hard-deletes all but the newest 100 executions, which would evaporate traces.
2. **Three objects: agent → session → run.** The **session owns the workspace and memory key; the run owns the
   trace.** Runs serialize within a session; sessions run concurrently.
3. **Reuse the loop by instantiation.** It touches `ExecutionContext` on 3 lines. Proven.
4. **Tools ride the `_tools` input-item seam** (`ai_agent.py:552-564`). No loop edits, ever.
5. **Memory keyed `(agent, session)`**, never the literal `"default"` — that default is a live cross-tenant leak
   (`simple_memory.py:18`, a module-global dict).
6. **Session pins agent config** at creation, with an opt-in adopt.
7. **The SDK supersedes the tool ladder** — see §4.
8. **Privacy/security deferred for the POC** (user's explicit call). Mitigation: **run on synthetic data**, which
   removes the risk entirely without giving up any capability.
9. Dense cockpit aesthetic, not airy. Task-first entry, but as a **command strip**, not a hero prompt box.

---

## 4. The current architecture question — the node SDK

The agent writes **one Python script** against an SDK generated from the node registry, instead of calling
`add_node`/`connect` one at a time:

```python
cron  = Cron(expression="0 7 * * 1")
fetch = HttpRequest(method="GET", url="{{ $vars.EXPORT_URL }}")
cron >> fetch >> check
for region in ("US","EU","APAC"):        # the thing a ladder cannot do
    check.true >> Postgres(name=f"Load {region}", operation="upsert")
```

Generated from `node_registry` — verified every node has uniform `node_description.properties` and an identical
`execute(context, node_definition, input_data)`, so nodes added later appear free. `AIAgent` excluded
deliberately (recursion/cost). Runs on the existing `run_script` PTC mechanism. Design + UI:
`design-prototypes/terminal-agent-sdk.html`.

### TWO CORRECTIONS the mockup overstates — fix these before building

**(a) Token cost. Measured, 2026-08-18:**

| Surface | Tokens |
|---|---|
| 28 nodes as 28 tools | ~5,412 |
| 7-tool ladder (measured) | 1,293 |
| SDK tool schema alone | 204 |
| **SDK + signature reference in the system prompt** | **~650** ← the honest number |

The agent cannot use the SDK without knowing the signatures; that reference is 325 tok for 28 types, ~445 with
wiring helpers. So the SDK is **2× better than the ladder, 8× better than 28 tools** — not the 6× claimed.

**(b) "Watch it build line by line" is not real at execution time.** Composition is pure Python — a 28-line
script creating 8 nodes finishes in **single-digit milliseconds**. You would see nothing, then the whole graph.
What IS genuinely watchable:
- **The model WRITING the script** (token streaming, several seconds) ← this is the show, and it is better
- **`call_tool(...)` inside the script** (real network, seconds)
- **`test_run()`** (real work)
- **The failure state**, which the mockup draws correctly — a raise at line 22 genuinely leaves lines 23-28 unrun

**So the line↔node link is PROVENANCE, not animation.** Hover a line → its nodes highlight; click a node → its
line highlights. Always true, genuinely useful, and honest. Animating a millisecond as if it were telemetry
would be decoration pretending to be data — the one thing this design system forbids.

---

## 5. What to do first in a new session

1. **Protect the design.** 16 prototypes + `_agents-base.css` are **untracked**. `git add design-prototypes/`.
   They hold the only copy of the data model, state machines and governance rules.
2. **Get a working model key.** All three are dead: Anthropic = valid key, *out of credit*; OpenAI = **empty**
   in `.env`; Gemini 2.x retired/quota-exhausted, 3.x blocked by (3).
3. **Fix the Gemini `thought_signature` bug** — `grep -n thought src/engine/llm_provider.py` returns nothing.
   Gemini 3.x requires it echoed back, so **every multi-turn tool loop dies on turn 2**. This breaks the AIAgent
   node on every currently-available Gemini model. Cheapest path to a working demo with keys you already have.
4. **Attach three wires:** node ladder → `agent_tool_resolver`; MCP discovery proven against a live server;
   SSE streaming.
5. **Then the front door UI** — the user's stated main thing: *"if I can just type something or create an agent,
   that would be beautiful."*

### The experiment that decides whether any of this is worth it
Take **five real recurring tasks**. Have the agent produce workflows. Run them a month.
**4 of 5 survive unattended → real product. 1 of 5 → it is a chat window and the flywheel is decoration.**
Still not done. Everything else is downstream of it.

---

## 6. Environment

```bash
cd apps/workflow-engine
docker compose up -d postgres                       # workflow-engine-postgres-1, db=workflows
venv/bin/python -m src.db.migrate status            # 20260818120000_agents applied
nohup venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 > /tmp/engine.log 2>&1 &
curl -s localhost:8000/health
```
NOTE: the `20260307120000_baseline` checksum was re-stamped on 2026-08-18 — the file had been edited after
being applied (pre-existing drift). Schema was verified intact first.

Screenshot prototypes: playwright lives in `apps/workflow-studio`; drive it from a throwaway `.mjs`.

---

## 7. Known live defects (verified, pre-existing, not introduced here)

| | Evidence |
|---|---|
| `code_tool.py:129` `restricted_globals.update(variables)` where `variables` is **LLM-supplied** → the model overwrites the sandbox allow-list by passing `__builtins__` | verified |
| `transform_data` / `join_datasets` do **not** propagate column sensitivity → derived datasets are unmasked. `grep -c sensitivity` = **0** across all 15 files in `app/features/transform` and `app/features/relationships` | verified |
| Workflow engine has **no authentication on any route** | verified |
| Gemini `thought_signature` never sent → multi-turn tool loops die on turn 2 | verified |
| 3 defects in `WORKFLOW-ENGINE-TOOL-DEFECTS.md` live on `clean-main`; fix sits on unmerged `fix/workflow-engine-tooling`. Worst rewrites `anyOf:[T,null]` → `{"type":"string"}`, i.e. **every optional param of every MCP/OpenAPI tool** | verified |
| The agent loop is **forked 3×**: `ai_agent.py`, `ai_chat_service.py:288`, `app_builder_ai_service.py:301` | verified |

---

## 8. Strategic position

- **Workflows are NOT redundant** — they are the compiled artifact (cost, determinism, latency, debuggability).
- **The app builder IS redundant** — fold it into an agent; that collapses two of the three loop forks.
- **Adoption evidence:** analysts will be the sustained users, not business users. Accuracy ceiling is set by
  whether governed metric definitions exist, not by the agent (Spider 2.0: 21.3% on real enterprise schemas vs
  91.2% on toy ones). Entry point should be a **migration motion** — *"show me your month-end pack, let's kill
  three items"* — not the serendipity motion currently drawn.
- **Position as "agents over YOUR governed data", never as "agents".** The generic layer is a commodity; the
  governed data plane is not.
