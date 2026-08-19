# Agent SDK — Handoff

**Written 2026-08-18, ~23:50, end of the session that built it. Corrected 2026-08-19 after a
verification review** (test-count table, line count, §5 eval-artifact caveat, §3.2/3.3 superseded
notes — each marked where it applies). Read this first. Companion docs:
`AGENT-WORKFLOW-AUTHORING.md` (intent/design), `SDK-DESIGN.md` (original mechanism spec),
`HANDOFF-AGENT-PLATFORM.md` (the broader agent-platform status this sits inside).

---

## 1. Direct answer to "is it clean, is there a better way, should we wipe it"

**No, don't wipe it.** The architecture held up under a real critical review — the validator reading the node
registry live (not hand-copied schemas), the fresh-per-call `_Draft` (not module state), the real engine
dataclasses as output — none of that needed to change. What a review *should* find in code built in one session
is implementation bugs, and it found nine real ones, all now fixed with regression tests. That's the review
working as intended, not a sign the design is wrong. See §3 for the actual list — read it before deciding
whether to trust that verdict rather than taking it on faith.

**What would make this the wrong call:** if you read §3's bug list and think the *density* of bugs (nine in
~500 lines, several genuinely serious — see #1) says something about how this was built, not just what got
caught. That's a fair reaction. My honest read: every one of them was caught by either (a) actually executing
code against the real registry, not just reasoning about it, or (b) a second independent review pass — which is
exactly why both steps happened before this handoff, not after. The counter-argument is that a from-scratch
rewrite doesn't remove the need for that same process, so it isn't obviously safer, just slower to the same
place.

---

## 2. What exists, concretely

| | Where | Status |
|---|---|---|
| SDK covering all 27 node types (all except `AIAgent`) | `apps/workflow-engine/src/engine/workflow_sdk.py` (~660 lines) | Built, tested |
| Contract tests (hand-picked + generic across all 27 types + regressions) | `tests/engine/test_workflow_sdk_contract.py` (27), `test_workflow_sdk_all_types.py` (64) | 91 tests, all passing |
| Integration tests incl. real Postgres round-trip | `tests/engine/test_workflow_sdk_integration.py` | 5 tests, all passing, DB verified clean after — 96 total with the row above |
| Eval harness (model-writes-code experiment) | `apps/workflow-engine/scripts/sdk_eval_harness.py` | Built, run twice for real (see §5) |
| Frontend feasibility spike (card node + line↔node provenance) | `apps/workflow-studio/src/app/routes/agent-sdk-demo.tsx`, `.../nodes/WorkflowNodeCard.tsx` | Built, proven live in-browser — **but see §4.4, it's an unguarded production route** |
| 3 design-state HTML mockups | `design-prototypes/terminal-agent-sdk*.html` | Static, illustrative |

Run everything:
```bash
cd apps/workflow-engine
docker compose up -d postgres          # integration tests need this running
venv/bin/python -m pytest tests/engine/ -v      # 93 passed
venv/bin/python scripts/sdk_eval_harness.py --trials 3   # needs a working LLM key, see §5
```

---

## 3. What the review actually found and fixed (read this, don't just trust the summary)

Two review passes happened: my own direct read of `workflow_sdk.py`, and an independent subagent given the same
file cold. Both found real, different things — which is itself evidence the process worked, since neither pass
was complete alone.

1. **Silent node-name collision corrupting connections.** `_unique_name`'s counter tracked names *per base
   string* but never checked the generated name against names already in use. `a=Postgres(); b=Postgres(name="Postgres 2"); c=Postgres()`
   — `c`'s auto-derived name collided with `b`'s explicit one. Confirmed by actually running it: `validate()`
   reported nothing wrong, and a connection meant for `b` silently resolved to `c` instead, because `draft.nodes`
   is keyed by name. This is the most serious one — a graph that looks fine and *is not the graph the script
   describes*. Fixed: name generation now checks against all names actually in use, and an explicit duplicate
   `name=` now raises clearly instead of silently succeeding.
2. **`test_run()` branch evaluation silently defaulted to `True` for `If` operations it didn't implement**
   (`gt`, `contains`, `regex`, ⅔ of the real operator list) and **ignored the `condition` expression field
   entirely** — meaning `test_run()` could report a confident, wrong branch decision. I had explicitly claimed
   in the code and to the user that branch decisions were "real, not faked." That was only true for a subset.
   Fixed: raises `TestRunLimitation` naming exactly what's unimplemented, instead of guessing.
   *Superseded 2026-08-19*: the raise itself was judged an adoption killer (an agent whose dry-run errors on
   common graphs stops calling it) — `test_run()` now walks undecidable branches as `maybe_reached`, reports
   `writes_staged` as an explicit upper bound plus `writes_staged_definite`, and names each undecided branch
   in `limitations`. The honesty invariant survives in a directional form: never understate, never refuse.
3. **Same problem, `Switch` node**: every outgoing branch was treated as reached, overcounting `writes_staged`
   behind branches that would never fire. Fixed the same way — raises rather than guesses. *Superseded
   2026-08-19 the same way as #2*: all Switch branches are now `maybe_reached` with a named limitation.
4. **Documented escape hatch didn't exist.** `AGENT-WORKFLOW-AUTHORING.md` §2.3 promised `Node(type, **params)`
   for anything the fixed constructors can't express. It was never implemented — calling it raised a bare
   `NameError`. Fixed: implemented for real, `AIAgent` deliberately still reachable through it (per the
   design's own "bind it by hand if wanted"), just not through the convenience constructors.
5. **`Neo4j` missing from the write-node set** used to compute `writes_staged` — undercounted real writes for
   any workflow using Neo4j instead of Postgres/MongoDB. Fixed.
6. **No cycle detection in `validate()`.** A workflow with an actual back-edge (not through a `Loop` node's
   own loop-back pattern, which is legitimate) validated as fine. Fixed: DFS cycle detection, with an explicit
   carve-out for cycles passing through a `Loop` node.
7. **`NodeHandle` accepted arbitrary attribute assignment silently.** Found because a real eval trial did
   exactly this by mistake (`node.someTypo = []`) — it "worked" and did nothing. Fixed: `__setattr__` now
   rejects anything that isn't an internal attribute.
8. **`list_nodes(query=...)`** was documented in `AGENT-WORKFLOW-AUTHORING.md` but the real signature only took
   `group=`. Fixed: added.
9. **Two test-quality gaps**: a test named `test_test_run_never_executes_writes` that never actually asserted
   the write guarantee (only checked `error is None`), and zero test coverage for the "value is a template
   expression, skip option validation" bypass path. Both fixed with real assertions.

All nine have regression tests now. 93/93 pass after every fix, re-run from a clean slate, not just incrementally.

**What I did NOT fix, on purpose, for time:** the duplicated adjacency-building logic between `_validate_draft`
and `_test_run_draft` turned out to need genuinely different data shapes (one needs full `Connection` objects
for port filtering, one only needs target names) — factoring it further would be a forced abstraction, not a
real simplification. Left as two separate, small functions.

---

## 4. What's still open — real gaps, not fixed tonight

1. **`agent_tool_resolver` has no path to this SDK.** A running agent cannot reach it yet. Separate, smaller
   piece of work — the resolver pattern already exists for the tool ladder, this is the same shape.
2. **The exec sandbox is still in-process**, not isolated. Fine for synthetic data, not fine for anything real
   — this was flagged before tonight and remains true.
3. **This is a hand-written slice, not the generator SDK-DESIGN.md describes.** No `.pyi` stubs, no build-time
   codegen, no equality test pinning three projections together. The validator reads the registry live, which
   makes drift structurally hard, but "hard" isn't "impossible" the way a generated-and-pinned system would be.
4. ~~`agent-sdk-demo.tsx` registered as a real, unguarded route~~ **Resolved 2026-08-19**: pulled from the
   route tree (`app/routes/index.ts` keeps a comment saying how to re-add it); the spike file itself stays.
5. ~~`WorkflowNodeCard.tsx`'s `groupVars` duplicates `nodeStyles.ts`~~ **Resolved 2026-08-19**: the card (and
   the demo route) now resolve colors through `getNodeStyles()`; the duplicate record is gone.
6. **The eval harness's Gemini key is on a 20-req/day free-tier quota** and is likely still exhausted (see §5).
   The Agent-tool-based eval (spinning up subagents instead) is the workaround that's actually proven to work.
7. **`test_run()` walks both of a `Loop` node's ports (`loop`/`done`) as definitely reached.** Deliberate, not
   an oversight: a loop body normally executes at least once, so counting its writes in the definite total is
   the less-wrong default — but a zero-iteration loop makes it an overstatement. If that matters for a graph,
   the `maybe_reached` mechanism added 2026-08-19 is the right home for a stricter treatment.

---

## 5. The eval results that got us here (context for why the architecture is trusted)

Two real experiments ran tonight, not simulated:
- **Gemini 3.6-flash, 9 trials, cold key**: 9/9 produced correct, executable scripts. But 0/9 used a `for`-loop
  even when the task explicitly hinted at one, and all serially chained same-type write nodes instead of
  fanning out — a real correctness gap the SDK's `validate()`/docs didn't catch at the time.
- **Claude subagents (via the Agent tool, after Gemini's daily quota died), 6 trials, after adding an explicit
  loop+fan-out example to `signature_reference()`**: 6/6 used a real `for`-loop, 5/6 passed outright (the one
  failure added an unnecessary `import` statement — a doc gap, now fixed in `signature_reference()`), and
  crucially **0/6 repeated the chaining bug**. Checked afterward for contamination (subagents were told not to
  use tools): logs show only inert `Bash: true` calls and one `ToolSearch`, no file reads of the repo.

This is real signal that the interface, as documented, gets used correctly by at least one capable model — not
just that the plumbing compiles.

**Caveat added 2026-08-19, after a verification review:** `scripts/sdk_eval_results.json` — the only eval
artifact persisted in the repo — is from a *later* run made after the Gemini quota died: 12 trials, 1 pass,
11 HTTP-429 quota errors. It does **not** contain either run described above; neither the 9/9 Gemini results
nor the 6/6 subagent results were persisted. A reader opening that file without this note would reasonably
conclude this section is fabricated. The claims above are from the session transcript, not from a re-loadable
artifact — re-run the experiment (the subagent route works without the Gemini key) before building on them.

---

## 6. Also found tonight, out of scope, not touched

A broad code-review skill was launched in parallel (scoped to the whole branch diff vs `main`, ~59K lines — much
wider than intended) and surfaced real, unrelated findings across the rest of the codebase: an SSRF TOCTOU
bypass in the MCP connector egress guard, a blocking synchronous DNS call on the event loop in the same module,
several stale-state bugs across dataset-editor lens components (Library/Quality/Relationships/Transform/Sampling/
Pivot pages — state not reset on sheet/version navigation, causing wrong-data-shown or silent-wrong-query
scenarios), an unconfirmed destructive "Run sweep now" button, and four bugs in `agent_run_service.py`/
`agent_repository.py` from the earlier agent-platform build (a closed-session-used-in-closure bug that would
break every non-builtin tool call in a real agent run, among others). None of this was fixed — it's unrelated to
the SDK and this was already a long session. Worth its own triage pass; ask the reviewing session to pull the
full list back out if wanted, since it wasn't transcribed in full here to keep this handoff focused.

**One of these is worth calling out specifically rather than leaving buried in that pile**, because it lands
directly on the claim this whole platform's differentiation rests on ("agents over *governed* data," per
`AGENT-WORKFLOW-AUTHORING.md` §1 and `VISION.md`): `apps/analytics-service/app/shared/masking.py`'s
`redact_profile` nulls out min/max/mean/std/examples/etc. for masked columns, but **never touches the
correlation matrix** (`profile["correlations"]`) — so a masked column's exact Pearson correlation with every
visible column comes back untouched, which is a statistical fingerprint of withheld values through the back
door the masking design otherwise explicitly guards against. Worse, the related `redact_insights` (in
`explorer/insights.py`) has a second, independent bug: the `high-correlation` insight's `column_name` is chosen
by *alphabetically sorting* the two column names in the pair — so if the masked column happens to sort second,
the redaction check (which only ever inspects `column_name`, never `other_column`) passes clean and the insight
returns fully unredacted, full coefficient and all. The dedicated test file for this exact feature
(`tests/test_profile_run_redaction.py`) greps for a planted string sentinel in every response, which is
structurally blind to a leak that's a bare float — nothing failed because nothing could have caught this shape
of leak. Not fixed tonight (different app, different session's worth of care), but flagged here because it's
exactly the class of gap that undermines the "governed data" pitch if it surfaces later without warning instead
of now, with warning.

---

## 7. If you're picking this up fresh

1. Read §3 in full before trusting "96/96 pass" (93 at the time §3 was written) as the whole story — the
   number is real, but know what it does and doesn't cover (see §4's open gaps).
2. Decide on `agent-sdk-demo.tsx`'s route exposure (§4.4) — quick decision, cheap fix either way.
3. The two pieces that would make this *usable* by a real agent, not just testable: wire `agent_tool_resolver`
   (§4.1), and settle process isolation for the sandbox (§4.2) before any non-synthetic data touches it.
4. If extending past 27 types is ever needed: it isn't, all 28 (minus the deliberately excluded `AIAgent`) are
   already covered.
