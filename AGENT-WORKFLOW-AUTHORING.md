# Agent-Authored Workflows — Intent Design

**Written 2026-08-18.** Consolidates `VISION.md` (why), `SDK-DESIGN.md` (the composition mechanics) and eight
of the sixteen prototypes in `design-prototypes/` into one document that answers, end to end: *how does a user
actually create a workflow when the author is an agent writing code, and how do they verify it as it happens?*
This is a design document — nothing here is implementation status. For what's built, see
`HANDOFF-AGENT-PLATFORM.md`.

---

## 0. The one-sentence version

An agent writes a Python script against an SDK generated from our 28 node types. You watch it write the script
(token by token — that's the show) and watch the graph assemble live as each line executes (that's the
provenance). You verify it three ways — typed errors it can't ignore, a dry `test_run()`, and a checkpoint modal
for anything that writes — before you accept it. Once accepted, it's either a workflow you keep re-running from
the agent session, or you promote it into a scheduled job / a callable tool for the next agent, at which point
the model leaves the loop entirely.

---

## 1. Where this sits

We already have three things: a governed data plane (`apps/analytics-service`), a workflow engine with 28 fixed
node types (`apps/workflow-engine`), and a React canvas (`apps/workflow-studio`) where humans wire nodes
together by hand today. The thesis (`VISION.md`):

> Use the expensive, unpredictable thing once to produce the cheap, predictable thing that runs forever.

A DAG editor assumes you already know the steps. Most of the real work is discovery — what table, what column,
whether the export is ever empty. An agent does that discovery *as it builds*, which a canvas cannot. The
canvas becomes an inspect-and-repair surface, not the front door.

---

## 2. Technical design — how the SDK actually works

### 2.1 One source of truth

`node_registry` (`src/engine/node_registry.py`), populated by `register_all_nodes()`. Every registered node
already has the shape generation needs, with no changes required to any node:

- a `type` string that is a valid Python identifier (verified for all 28)
- `node_description.properties`: a list of `NodeProperty(name, type, required, default, options, ...)`
- `node_description.outputs`: named ports where they exist — `If → [true, false]`,
  `Switch → [output0…output14, fallback]`, `Loop → [loop, done]`
- an identical `execute(context, node_definition, input_data)` on every node

Because this is already uniform, a node added next year needs nothing written by hand to appear in the SDK.

### 2.2 Three generated projections, one equality test

| Projection | Consumer | Generated when |
|---|---|---|
| Runtime callables (`Cron(...)`, `HttpRequest(...)`, ...) | the script that actually runs | at import |
| Type stubs (`.pyi`) | humans — autocomplete, mypy | by a build step, committed |
| Signature reference (compact text) | injected into the agent's system prompt | generated at run time |

One test pins all three to the registry:

```
assert set(sdk_runtime) == set(stub_symbols) == set(reference_entries)
       == set(registry.types) - {"AIAgent"}
```

This test *is* the design. Without it, the stubs and the prompt reference rot the first time someone adds a
node in a hurry — drift becomes a failing test instead of a silent lie the agent is fed.

### 2.3 The API surface

```python
cron  = Cron(expression="0 7 * * 1", timezone="Europe/London")
fetch = HttpRequest(method="GET", url="{{ $vars.EXPORT_URL }}")
check = If(field="body", operation="isNotEmpty")

cron >> fetch >> check
for region in ("US", "EU", "APAC"):
    check.true >> Postgres(name=f"Load {region}", operation="upsert", table="finance.ledger")
check.false >> StopAndError(message="Export was empty")

validate()
test_run(input={"body": SAMPLE})
```

- **Constructors** — one per node type, name auto-derived and de-duplicated (`Code`, `Code 2`); `name=` overrides.
- **Wiring** — `a >> b` (main→main, returns `b` so chains read left-to-right); `a.true >> b` (named port);
  `a >> [b, c]` (fan out). Fan-in is a loop — `for s in (a, b): s >> merge` — NOT `[a, b] >> c`: Python
  cannot define `>>` on a plain list, and `signature_reference()` explicitly teaches the loop form.
- **Introspection**, inside the script so it costs no prompt tokens — `list_nodes(group=…, query=…)`,
  `describe("Postgres")`.
- **Checking** — `validate()` raises `WorkflowInvalid` listing every problem with the fix; `test_run(input=…)`
  is **dry**: compiles, resolves expressions, counts what *would* happen, never executes a write.
- **Reaching data mid-build** — `call_tool(name, args)`, so the agent can query a dataset and branch on the
  result while composing, not just after.
- **Escape hatch** — `Node("SomeType", **params)` for anything the generator can't express. The SDK is never a
  ceiling.

Mapping rules worth keeping straight: `NodeProperty.name` becomes the keyword arg **with camelCase preserved**
(those keys are the stored workflow JSON — a snake_case surface would need a translation table, and a
translation table drifts); `required: True` with a non-empty default is treated as *optional* in the SDK
("required" alone is a lie several nodes tell); `options` become `Literal[...]` in the stubs with a runtime
membership check; `AIAgent` is excluded from generation entirely — an agent that can place agents inside the
workflow it's building makes cost and recursion unpredictable, and the exclusion is explicit and tested.

### 2.4 Errors are the feedback loop, not an afterthought

Constructors validate eagerly:

```
TypeError: SendEmail has no parameter 'recipient'; did you mean 'toEmail'?
           required: toEmail, subject, bodyFormat, body
           at build_workflow.py, line 22
```

Three rules make this the tight loop it needs to be: name the valid vocabulary so the model can self-correct;
nothing partial on failure (a failed constructor adds no node); the traceback goes back to the agent verbatim,
not a summary. `validate()` then catches what a single constructor can't see alone — unreachable nodes, no
trigger, or a routing field that doesn't exist on the upstream node's declared output (a real mistake a real
model made against the earlier tool-ladder version, and fixed once told).

### 2.5 What the canvas receives

Every constructor call and every `>>` emits the same `addNode` / `addConnection` events the canvas already
consumes from the tool-ladder version — non-negotiable, or the script is a black box. But be honest about
timing: composition is pure Python, a 28-line script creating 8 nodes finishes in single-digit milliseconds.
There is no line-by-line build to watch, and animating one would be decoration pretending to be telemetry. What
*is* genuinely watchable — and this is what §3.3 is built around — is the model **writing** the script (token
streaming, seconds), `call_tool(...)` inside it (real network), and `test_run()` (real work). The line↔node
link is **provenance, not animation**: always true, always inspectable, never staged.

### 2.6 Cost, measured 2026-08-18

| Surface | Tokens | Round trips to build 6 nodes |
|---|---|---|
| 28 node types as 28 tools | ~5,412 | 6+ |
| 7-tool ladder (built, measured against a real model) | 1,293 | 18 — observed |
| SDK: one tool + signature reference | **~650** (204 schema + 445 signature ref, not 204 alone) | **1** |

Beyond cost: loops and conditionals. "One loader per region" is a `for` statement, not twelve tool calls the
model has to each get right — and models write Python far better than they emit long tool-call chains.

---

## 3. UX design — the path a user actually walks

### 3.1 Front door: task-first, not agent-first

`terminal-agent-home.html`. The first object is the **task**, not the agent — you type what you want, an agent
is matched to it and shown (never silently chosen), with a one-click way to change the match. This is a
correction of an earlier draft: making an agent do one thing took nine steps (new agent → 28-field config form →
tools tab → bind → save → run → type task). That's a control plane, not a product — it only ever covers the
*second* session, never the first.

The entry point is a **command strip**, not a big soft prompt box in whitespace. A large centred prompt box is
the most-copied shape in software right now; spending the first impression on it throws away the only
differentiated asset here, which is that the screen behind the strip is already full of live information
(running work, what's waiting on you, what this agent can reach).

### 3.2 The agent asks when it genuinely can't derive

`terminal-agent-ask.html`. If it can't ask, "describe it in English" collapses back into "specify everything up
front" — the exact problem this is meant to escape. An ask offers *real* options, because the agent actually
queried them: three tables with row counts and last-write times, not a blind text field. One is marked as the
recommendation with its reason. Free text always still works.

**This is deliberately drawn differently from a checkpoint** (§3.4). An ask needs information and lives inline
in the agent's own column, unblurred, low-stakes. A checkpoint asks permission for something dangerous and stops
the world. Drawing them the same way would teach people to skim both — and skimming a checkpoint is how a
dataset gets published by accident. The quality bar for an agent here is *how little it asks*: a good agent
shows nine things it worked out on its own next to two genuine questions, which reads as competence rather than
a form.

### 3.3 Watching it build: script and graph, the same event twice

`terminal-agent-sdk.html`, the direct answer to "how do we watch it happen." Split pane: the Python script on
the left, the live canvas in the middle. Every SDK constructor and every `>>` fires the same event the graph
already listens for, so the canvas still assembles one node at a time — just driven from inside the script
rather than from eighteen separate tool calls. The executing line and the node it just produced carry the same
accent colour, and **every node wears the line number that made it** (a small badge — `line 13 · APAC`). Hover a
line, its nodes highlight; click a node, its line highlights.

**Partial failure is the interesting state, and it's the one that gets drawn deliberately**: line 22 raises, but
lines 1–21 already built six real nodes that are genuinely still sitting on the canvas. The traceback pins to
the failing line and reads exactly like §2.4 — named vocabulary, verbatim to the agent. A ladder of 18 tool
calls would leave the same partial graph; the difference is the script tells you *exactly* where it stopped.

Nothing here is a magic box. You're watching code run, and the graph is its literal shadow — not a result that
appears after a black-box compile.

### 3.4 Verifying before anything happens — three distinct mechanisms

This is the part the user specifically asked about, and it's deliberately three different things, not one
"review" screen, because they answer different questions:

1. **Typed errors at construction** (§2.4) — answers *"did I call this correctly?"* Immediate, inside the
   script, before anything reaches the canvas.
2. **`validate()` and `test_run()`** — answers *"is the whole graph sound, and what would it actually do?"*
   `validate()` catches structural problems a single constructor can't see (unreachable nodes, no trigger,
   dangling ports). `test_run()` is dry — it compiles and resolves expressions and reports what *would* happen,
   and it **never executes a write**. This is how you see the shape of the plan before anything real occurs.
3. **The checkpoint modal** (`terminal-agent-checkpoint.html`) — answers *"should this specific write actually
   happen?"* A tool bound with `requires approval` suspends the run before the call goes out. This is the only
   one of the three that stops the world, and it does so on purpose:
   - **Arguments are shown verbatim and are editable** — a prose summary of a mutation is a paraphrase, and a
     paraphrase is exactly where a wrong approval hides. What you approve is byte-for-byte what gets sent.
   - **Blast radius is enumerated, not adjectival** — not "this is destructive" but "creates 1 dataset in
     Growth Analytics, visible to 12 members, and nothing on this agent's tool surface can delete it."
   - **Four answers, unequal weight**: approve once (the common path, wins on value); edit-then-approve;
     deny-with-a-reason (the reason goes back to the model as the tool result — a denial with no reason just
     reads as an unexplained failure the agent retries); "always allow," which is the dangerous one, so it's
     drawn quietest and says explicitly what it changes — the binding, permanently, for every future run.
   - **The queue is shown** ("1 of 2") so a second pending approval can't hide behind the first.
   - It's a modal over the live workspace, not its own route, because the decision is only answerable with the
     diff/graph behind it in view — the run is *paused*, so what's behind is frozen, not stale.

The three together mean: syntax errors never reach the canvas, structural problems never reach a test run, and
nothing with a side effect executes without an explicit, specific, revocable yes.

### 3.5 After you accept: two different endings

Accepting a built workflow is not the end state — what happens next depends on whether this is a one-off or a
recurring need, and the UI treats those as genuinely different actions:

**Turn a session into a scheduled workflow** (`terminal-agent-schedule.html`) — the highest-value single action
in the product, and it's one sentence: *"do this every Monday."* This is explicitly framed as **recording**, not
inventing: a two-column derivation table shows the fourteen tool calls the agent actually made on the left and
the six nodes it became on the right, with arrows — and the rows that mapped to *nothing* (dead ends, clarifying
questions) stay in the table with an em-dash, because hiding them would claim a tidier derivation than actually
happened. A cost band states the trade plainly: 44.1k tokens / 52s becomes 0 tokens / 2.1s, fifty-two times a
year. What's lost is stated, not buried — a workflow can't adapt to a renamed column the way the agent did, and
the failure path routes back into a session, said out loud rather than discovered nine weeks later.

**Promote a workflow into a callable tool** (`terminal-agent-promote.html`) — the only mechanism in the product
that compounds. Week 1 an agent has 28 node types; week 20 it has 28 node types plus every workflow a team
promoted, each one a single deterministic call encoding business logic nobody has to re-derive. This is
deliberately drawn as **zero-sum**: an agent's tool binder caps at a fixed count and a fixed schema-token
budget, so promoting doesn't just add a tool — it competes for a slot, and the screen names exactly which
existing tool gets evicted and the arithmetic behind it. The tool's *description* (not its name) is the field
that matters, because that's what the calling model actually reads to decide when to use it.

### 3.6 Multi-user model underneath both of the above

Three objects, not two: **agent** (a definition — model, prompt, tools; holds no run state), **session** (owns
the workspace and the memory key; single-writer — one person drives, others can read), **run** (owns the trace).
Sessions run concurrently; runs serialize within a session. This is why "N people run the same agent
simultaneously without touching each other" is actually true rather than asserted, and why session config is
pinned at creation (behaviour can't shift mid-conversation) with an explicit, logged opt-in to adopt a newer
agent version.

### 3.7 Two doors onto the same object

Business users get the builder UI described above. An agent definition is already a bag of parameters in a
JSONB column — which is to say, it's already a file. So the same object also has a door as
`agents/revenue-analyst.yaml` in a repo view (`terminal-agent-devsurface.html`), giving programmers review,
history, and diff — reviewing a raised `maxIterations` in a pull request is the entire point of that door
existing.

---

## 4. The path end to end, one example

1. A finance analyst types into the command strip: *"Break down net revenue by segment year to date."*
2. Nothing to build yet — this routes to the **Ask** agent, which queries the governed data plane and answers
   inline, every figure denominated, masked columns still masked.
3. They say: *"Do this every Monday and email me."*
4. A **Build** agent opens a session. It writes a Python script against the SDK. The split pane shows the
   script streaming in on the left; each executed line lights up a node on the graph in the middle, wearing that
   line's number.
5. It hits something it can't derive — say, which distribution list — and asks inline (§3.2), with real options
   drawn from what actually exists.
6. `validate()` and `test_run()` run automatically before anything is proposed as final; if a node's arguments
   are wrong, a typed exception naming the fix appears in the script pane, and the agent self-corrects and
   re-runs the affected lines.
7. If the script includes a write with `requires approval` (e.g. writing to `finance.ledger`), the run pauses
   and a checkpoint modal shows the exact arguments and blast radius. The analyst approves once.
8. The analyst reviews the finished graph, accepts it.
9. They choose **"Do this every Monday"** — the derivation table shows which of the tool calls became which of
   the nodes, the cost band shows tokens-and-pounds-per-year against near-zero, and it becomes a Cron-triggered
   workflow. No model runs again unless the schema drifts and it fails, at which point it routes back into a
   session rather than failing silently.
10. Separately, a platform team **promotes** that same workflow into a tool. It now competes for a slot in every
    other agent's binder, named by what it does, versioned, demotable if its failure rate crosses a stated
    threshold.

---

## 5. Open questions this doesn't resolve

From `SDK-DESIGN.md` §8–9, still genuinely open:

- Are generated `.pyi` stubs worth the build step, or does runtime generation alone (which agents get either
  way) make them a "programmer surface" nicety that's also the one piece that can go stale?
- Does `>>` mutate an implicit module-level draft (reads better for the common one-workflow-per-script case) or
  an explicit object (more defensible if multi-workflow scripts ever appear)?
- Does the signature reference live permanently in the system prompt (~445 tokens every call) or get fetched
  on demand via `list_nodes()` — a short always-on core plus `describe()` for detail is the current lean, but
  unmeasured against a real model.
- `displayOptions` (conditional field visibility) aren't enforced at construction time, only at `validate()` —
  one turn later than ideal.

And the one that would make the *whole thing* wrong, not just this design: if models turn out to compose more
reliably through discrete tool calls than through a script, the ladder wins on accuracy and the token argument
stops mattering — testable directly (same task, same model, both surfaces, compare success rate), and worth
doing before committing further build time. Separately, per `VISION.md`: take five real recurring tasks, have
an agent build workflows for them, run unattended for a month. Four of five surviving is a real product; one of
five means this is a nicer chat window and the promote/schedule flywheel is decoration on a broken premise.

---

## 6. What exists today vs. what's designed here

**Updated 2026-08-19 — this section was stale.** A hand-written, non-generated slice of the SDK described in §2
now exists and is tested: `apps/workflow-engine/src/engine/workflow_sdk.py`, covering all 27 node types (every
registered type except `AIAgent`, excluded per §5), with `>>`/`.port` wiring, `validate()`, a real (not faked)
`test_run()`, the `Node(type, **kwargs)` escape hatch, and `signature_reference()`. 93 tests pass across three
files in `tests/engine/`, including integration tests that persist an SDK-built workflow through the real
`WorkflowRepository` into the actual running Postgres. What's still NOT built: the production generator itself
(no `.pyi` stubs, no build-time codegen, no equality test pinning projections together — this slice is
hand-written, not generated, though its validator reads the registry live so it can't drift), the wiring into
`agent_tool_resolver` that would let a running agent actually reach it, and process isolation for the exec
sandbox (still in-process, fine for synthetic data only). Full status: `HANDOFF-AGENT-SDK.md`.
