# One Engine, Many Uses — story (words only, no design)

A scroll-driven film for a 7-minute talk. The landing page snaps one scroll step per scene; the scene plays itself, then rests on its last frame. Nine scenes, in this order. Never say "n8n clone". Theme: "macro elements, not micro nodes". The 2 targeted use cases are data analysis and testing, both done in the notebook.

0. **Opening** (no kicker, still background, no motion behind it)
   - "It started as a workflow builder."
   - "Then its engine became the base for everything else."

1. **ENGINE** — REVISED 2026-11-03: kicker "01 · The engine underneath" — caption "Take away the workflow layer. This is what it can do."
   - The point of the whole talk: it is not just a workflow tool. The Opening's workflow builder window lifts away (the workflow layer peels off) and reveals the engine core below it, visualised as a 3D glass machine (the glass gears belong here, at the heart of it). Around the core, the engine's real capabilities (take them from apps/workflow-engine, not invented). For example: run any graph (branches, loops, parallel, sub-workflows); durable waits that last days; people in the loop (approvals); every step declares its effect; sandboxed code; read-only connections by default; written once with an audit; triggers (schedule, webhook, form); 35 node types. Then show that the same core powers more than workflows: notebook, tests, scheduled checks, app builder.
   - Old ENGINE detail (still true, use only if it helps):
   - A real workflow: Schedule (Weekdays · 07:00) → Code (Window = 1 day) → Postgres (SELECT on bank, read-only) → Code (amount over 50) → Approval (A person decides) → Postgres (UPDATE on bank_rw).
   - Trigger types: Cron, Webhook (card dispute in), Form (ops request). Steps: HTTP (check source API), Merge (wait for both), Loop (for each item), Sub-workflow (customer-notify), Email (notify the team). 35 node types: branches, loops, parallel.

2. **CONNECT** — kicker "02 · What it connects to" — caption "It talks to the systems a bank already has."
   - REVISED 2026-11-03: a 3D glass globe (or globe-like sphere) with many integrations around and on it, turning slowly, leaning to the pointer. Include the work tools the owner wants: Jira, GitHub, Microsoft Teams, Outlook, Slack, Confluence, ServiceNow, SharePoint, plus the data and system ones below. Then one or two integrations come forward to show detail (e.g. a Jira ticket created from a run, a Teams message to the ops channel).
   - Postgres (read-only by default), MongoDB (documents), Neo4j (graphs), REST APIs (import an OpenAPI spec), Webhooks (trigger from any system), Schedules (cron · business days), Email (send and notify), Forms (a screen on any workflow), Python (sandboxed code steps).

3. **SECURE** — kicker "03 · How it stays safe" — caption "Built in from the start, not added later."
   - Read-only by default: connections run SELECT only, inside a read-only transaction.
   - Every step declares its effect: none, network, database or a person. Test runs refuse anything unmocked.
   - Writes wait for a person: notebook changes go through an approval gate first.
   - Written once, on the record: idempotency key, before and after values, who applied it and when.
   - Secrets locked to their hosts: encrypted, bound to allowed hosts, removed from run output.
   - Code runs in a sandbox: no network, no privileges, memory and CPU limits.

4. **REUSE** — kicker "04 · Macro elements" (sub: "What it is now · macro elements, not micro nodes") — caption "Macro elements: each block is a whole capability." Second line: "Two use cases we are targeting: data analysis and testing, both in the notebook."
   - Each block is a whole capability, not a tiny node. Real blocks: Data check (query → rule → look up the source API → one alert); Browser test (portal sign-in, steps you can take over, exports a Playwright spec); Approval chain (maker → checker → treasury, each decision kept); Scheduled check (every business day 07:00, same code, same answer). Also: Notebook (the way in), Reconciliation (match two systems, flag the breaks), App builder (describe a screen, it builds a React app on a workflow).

5. **ASK** — kicker "05 · Use case 1 · data analysis" — captions: "Use case 1 we are targeting: data analysis." → "AI drafts the cells. The engine runs them." → "Nothing writes until a person says so." → "Written once. On the record."
   - In the notebook, a person asks: "Find wires stuck past the settlement window and release the oldest over the limit."
   - AI drafts 4 cells: DATA (settlement window · 1 day), QUERY (stuck wires older than 1 day in bank.wires), RULE (amount is over 50), CHANGE (release the oldest · status → released).
   - The analytics service can do a lot — show it: dataset versions (bank.wires v14, read-only snapshot today 07:00), profiling (row count, histograms, status mix, freshness), quality checks, PII masking (beneficiary IBAN masked for AI and output), aggregates/pivots, charts, relationships between tables.
   - Realistic data: 1,284 wires today, 3 stuck, $12,600.50 held, oldest 4 days. W1 $100.50 stuck 3 days, W2 $12,500.00 stuck 4 days (the oldest over 50), W3 $75.00 sent, W4 $40.00 stuck 2 hours (inside the window).
   - The proposed change (W2 stuck → released) waits; a person clicks Apply; it writes through bank_rw once, with an audit row (who, when, before/after, idempotency key).

6. **KEEP** — kicker "06 · Keep it running" — caption "Keep it, and it runs every business day exactly as you saw it."
   - Keep the answer as: a Workflow (run on demand or from another workflow), a Test suite (pass/fail every run; browser steps export as Playwright tests), or a Scheduled check (runs on a schedule, flags what it finds).
   - Trigger every business day 07:00; the query runs on bank, read-only; the change asks a person first, then writes through bank_rw. Same code, same answer: the rule node runs the exact source the notebook ran. No model in the loop: nothing is regenerated.

7. **TEST** — kicker "07 · Use case 2 · testing" — caption "Use case 2 we are targeting: testing."
   - Business cases in, tests run. UI test · portal sign-in (open the portal, fill the username, password typed by you and not recorded, click Sign in, the accounts table shows), browser steps you can take over, exports Playwright tests. API test · two calls with checks. Data test · for each, then wait.

8. **Close / Recap** — "One engine." then "Many uses."
   - Stats: "35 · node types in an n8n-style engine I built"; "2 use cases · we are targeting: data analysis and testing, both in the notebook"; "Read-only · by default. Notebook writes wait for a person."
   - "What would you build on it?"
