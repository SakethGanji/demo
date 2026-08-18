# Command Studio — Agents

## The thesis

**Use the expensive, unpredictable thing once to produce the cheap, predictable thing that runs forever.**

You describe what you want in plain English. An agent explores your governed data, figures out the steps, and
builds something — a workflow, an app, an answer. You watch it work and correct it as it goes. When it's right,
you accept it, and from that moment it **runs with no model in the loop**: pennies a year instead of pounds,
same result every time, auditable like any other scheduled job.

The AI is the *author*. It is not the *runtime*.

---

## The problem this actually solves

Building workflows by hand is slow, and it is slow in a specific way: you have to already know the steps. Half
the work is discovery — what tables exist, what the column is really called, whether the export is ever empty.
A DAG editor assumes you have finished that work before you open it.

An agent does the discovery *as part of* building. That is the whole difference:

> **A workflow is work you can specify. An agent is work you can only describe.**

---

## What it looks like when it works

1. Someone in finance types: *"Break down net revenue by segment year to date."*
2. The agent finds the dataset, reads its schema, writes the query, and answers — showing its working, with
   every figure carrying its denominator and masked columns still masked.
3. They say: *"Do this every Monday and email me."*
4. The agent replays what it just did as fixed steps, asks the one or two things it genuinely cannot infer, and
   proposes a scheduled workflow. They accept it.
5. It runs every Monday. **No AI involved.** ~£0.02/year instead of ~£16.
6. That workflow becomes a **tool the next agent can call** — so the platform gets more capable every time
   somebody uses it, without getting more expensive or less predictable.

Step 6 is the compounding one. Week 1 an agent has 28 building blocks. Week 20 it has 28 blocks plus 40
team-built workflows encoding *this company's* processes — which is the part a competitor cannot copy.

---

## The three jobs

| | |
|---|---|
| **Ask** | Open-ended questions over governed data. RBAC and masking enforced on the tool path, not the prompt. |
| **Build** | Produce an artifact you own — workflow, app, pipeline. Accept it and the model leaves. |
| **Watch** | A few standing agents on triggers, where the judgement can't be pre-written. |

The differentiator versus workflows alone is the **open tool surface**: a workflow has 28 fixed node types; an
agent reaches everything you register — MCP servers, OpenAPI specs, your own functions.

---

## Why this is defensible

Two spaces are empty. We checked six platforms including Google and AWS.

**1. Nobody enforces governance on the tool path.** In their own documentation: AWS Bedrock Guardrails *"will
not detect PII … when models respond with tool_use output parameters."* Google Model Armor *"does not screen"*
custom agents. Writer: permissions *"don't transfer over to Knowledge Graph."* Everyone protects the perimeter
and leaks at the tool call.

**2. Nobody compiles.** Every agent product runs the agent every time. Relevance and Writer have deterministic
artifacts but you author them by hand; Dust *deprecated* theirs in October 2025. The move — *run the agent once,
freeze the result into something that runs without it* — is unclaimed.

We can occupy both because we already own a governed data plane and a deterministic runtime. Most competitors
would have to build a data governance product first.

**The positioning follows: this is "agents over your governed data," never "agents."** The generic agent layer
is a commodity that gets cheaper monthly. The thing underneath it is not.

---

## What we are deliberately not doing

- **Not replacing the workflow engine.** Workflows are the compiled output — cheap, deterministic, debuggable.
  The canvas becomes an inspect-and-repair surface rather than the front door.
- **Not building a separate app builder.** It folds in as one agent among several.
- **Not chasing business users first.** The evidence says analysts will be the sustained users. Aim there.
- **Not pretending the agent is autonomous.** It asks when it cannot derive, stops before anything irreversible,
  and shows its working. Every figure carries its denominator; refusals are drawn, not designed past.

---

## How we would know this failed

**The one experiment that decides it:** take five real recurring tasks. Have an agent produce workflows for
them. Run them unattended for a month.

- **4 of 5 survive** → the compile thesis holds and this is a real product.
- **1 of 5 survives** → we have built a nicer chat window, and sessions, promotion and the flywheel are
  decoration on a broken premise.

Secondary failure modes, in likelihood order: nobody's *job* is to use it; one wrong number in front of an exec
because metric definitions were never agreed; it gets absorbed by the data team and never reaches anyone else.

The first is the most likely and the least dramatic — it dies quietly over two quarters with no incident.
