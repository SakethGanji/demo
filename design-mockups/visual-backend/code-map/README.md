# Code-derived backend map: working prototype

This adapts the visual-backend concepts to Command's actual Python backend model.
A large module container holds function containers. Expanding a function reveals its
resolved local calls, known SDK connector calls, and unresolved/custom calls. Local calls
connect with solid arrows to the shared helper's single definition. Dotted edges inside
each function connect its origin to nested call nodes. These are dependencies, not execution paths.

The initial focused view shows the selected function alongside its callers and callees.
Use **Show all functions** for the whole module, expand/collapse function boundaries,
drag the canvas to pan, scroll or use the zoom buttons, and choose **Fit graph** to recenter.

## Run

From the repository root:

```bash
python design-mockups/visual-backend/code-map/server.py
```

Open the local prototype using the loopback port printed by the server (default 8770).
No dependencies, database, Gemini key, or external assets are required. The server is
loopback-only; it is a development prototype, not a deployment configuration.

The initial source is the actual `examples/banking-operations/banking_ops.py` file.
**Visual + code** provides an editable browser draft. Changes trigger Python AST analysis;
adding/removing functions or calls changes the diagram. **Add example helper** is a quick
way to demonstrate this. Reload restores the repository example. Draft edits are not saved
to the repository, and submitted code is never imported, executed or sent to an LLM.

## Complex application example

The application selector also loads `examples/risk_operations.py`: an illustrative
risk/case-management backend with 22 functions, 6 endpoints and 28 resolved local
call sites. It includes review and reconciliation, policy-based scoring, shared
Postgres access, Splunk-style activity search, notifications and audit ingestion.
The connector calls illustrate boundaries; their SDK contracts and external services
are not verified or executed. A custom scoring callback deliberately remains unresolved.
No secret values or real customer records are embedded.

Start with the case-review focus; choose **Show all functions** and **Collapse all**
for the application overview. Select a function and choose **Focus selection** to
inspect its immediate callers and dependencies. Layers are derived from the call
relationships. For a real multi-module application, module discovery and import
resolution would need to extend this single-module analyzer.

Screenshots and browser evidence are in `screenshots/code-graph-complex/`.

## Synchronization contract

- The AST result includes a SHA-256 hash of the exact source analyzed.
- Editing immediately marks the old diagram stale; only the latest analysis may replace it.
- Syntax errors keep the last valid diagram visibly dimmed and labeled; it never claims to
  represent the invalid draft. Source links are disabled until the graph matches the draft.
- A delayed response for an obsolete draft cannot replace a more recent result.
- Source navigation selects the corresponding function/call range in the actual draft.
- Friendly function titles are mechanical name formatting, not inferred business semantics.

## Deliberate limits

This is a conservative, single-module syntax projection, **not** an execution/control-flow
engine. It resolves direct references to top-level functions and recognized Studio SDK calls.
Shared helpers, call-site locations, literal route declarations and secret-reference names
are inspectable. Credential values are never resolved. SQL effects are explicitly unknown.
Dynamic dispatch, aliases, nested scopes and external behavior may remain unresolved. Calls
are listed in source order; dashed relationships do not prove runtime sequence or execution.

It does not create an independently editable graph. For Command integration, move the analyzer
behind existing project/module access boundaries; use draft source or a pinned release source,
cache by source hash, and reuse the editor's conflict handling. Runtime evidence would require
separate trace/callsite correlation. Visually managed editing should remain a subsequent stage.

## Checks

```bash
python -m unittest discover -s design-mockups/visual-backend/code-map
```

Checks cover source discovery, shadowed calls, nested scopes, syntax errors, non-execution,
source revisions, route binding and unresolved effects. Browser checks exercise the actual
banking module, nested calls, source navigation, code edits, stale/invalid state, out-of-order
responses, and a 390px layout. Local graph evidence is in `screenshots/code-graph/` (earlier card prototype: `screenshots/code-map/`).
