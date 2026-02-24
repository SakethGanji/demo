# Workflow Engine — Examples & Usage

Practical examples for running workflows via curl. Covers all 6 seeded workflows, ad-hoc execution, and building your own.

> Architecture deep-dive: see [README.md](./README.md)

---

## Table of Contents

- [Quick Start](#quick-start)
- [API Cheatsheet](#api-cheatsheet)
- [Fraud Investigation — Webhook API](#fraud-investigation--webhook-api)
- [Fraud Investigation — UI Report](#fraud-investigation--ui-report)
- [Deep Research Agent](#deep-research-agent)
- [Startup Due Diligence Agent](#startup-due-diligence-agent)
- [Content Quality Pipeline](#content-quality-pipeline)
- [Customer Escalation Triage](#customer-escalation-triage)
- [Simple Data Pipeline (No AI)](#simple-data-pipeline-no-ai)
- [Conditional Routing](#conditional-routing)
- [One-Shot LLM Chat](#one-shot-llm-chat)
- [Sub-Agent Architecture](#sub-agent-architecture)
- [SSE Event Format](#sse-event-format)
- [Server Logs](#server-logs)
- [E2E Test Script](#e2e-test-script)

---

## Quick Start

```bash
pip install -r requirements.txt

# At least one LLM key
export GEMINI_API_KEY=your-key

python -m src.main                 # start server → http://localhost:8000
python -m src.db.seed              # seed 6 example workflows
```

## API Cheatsheet

```bash
# Health
curl http://localhost:8000/health

# List workflows
curl http://localhost:8000/api/workflows

# List node types
curl http://localhost:8000/api/nodes

# Run saved workflow
curl -X POST http://localhost:8000/api/workflows/{id}/run

# Run ad-hoc workflow (no save)
curl -X POST http://localhost:8000/api/workflows/run-adhoc \
  -H "Content-Type: application/json" -d '{...}'

# Stream workflow via SSE
curl -N -X POST http://localhost:8000/execution-stream/adhoc \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" -d '{...}'

# Trigger webhook workflow
curl -X POST http://localhost:8000/webhooks/webhook \
  -H "Content-Type: application/json" -d '{...}'

# Execution history
curl http://localhost:8000/api/executions
curl http://localhost:8000/api/executions/{execution_id}
```

---

## Fraud Investigation — Webhook API

**What it does:** POST a transaction alert, 3 sub-agents (Transaction Analyzer, Customer Profile, Network Analyzer) investigate in parallel, result is routed by risk level (LOW → auto-clear, MEDIUM → queue review, HIGH → block, CRITICAL → escalate).

**Flow:** `Webhook → AIAgent → Switch → 4x RespondToWebhook`

```bash
curl -X POST http://localhost:8000/webhooks/webhook \
  -H "Content-Type: application/json" \
  -d '{
  "alert_id": "FRD-2026-00847",
  "timestamp": "2026-02-23T02:14:33Z",
  "transaction": {
    "id": "TXN-99281374",
    "type": "wire_transfer",
    "amount": 247500.00,
    "currency": "USD",
    "channel": "online_banking",
    "initiated_at": "2026-02-23T02:14:33Z",
    "memo": "Consulting services — Q1 retainer"
  },
  "sender": {
    "account_id": "ACCT-10034821",
    "name": "Meridian Holdings LLC",
    "account_type": "business_checking",
    "account_age_days": 45,
    "avg_monthly_volume": 12400.00,
    "kyc_status": "basic_verified",
    "previous_alerts": 0,
    "last_address_change": "2026-01-10"
  },
  "recipient": {
    "name": "Greenfield Consulting Group",
    "bank": "First National Bank of Cyprus",
    "country": "CY",
    "account_type": "corporate",
    "is_first_transfer": true,
    "swift_code": "FNBCCYNI"
  },
  "risk_signals": {
    "amount_vs_average": 19.96,
    "unusual_hour": true,
    "new_recipient": true,
    "high_risk_jurisdiction": true,
    "velocity_24h": 1,
    "velocity_7d": 3
  }
}'
```

**Expected response** (CRITICAL route):

```json
{
  "risk_score": 92,
  "risk_level": "CRITICAL",
  "summary": "Multiple converging high-severity indicators: 20x normal volume from 45-day-old account, first-time wire to Cyprus (FATF grey list), initiated at 2:14 AM.",
  "evidence": [
    {"source": "transaction", "finding": "Amount 19.96x average monthly volume — critical threshold (>10x)", "severity": "critical"},
    {"source": "transaction", "finding": "Wire initiated at 02:14 UTC outside business hours", "severity": "medium"},
    {"source": "customer", "finding": "Account age 45 days with only basic KYC verification", "severity": "high"},
    {"source": "customer", "finding": "Address changed 2026-01-10, 44 days before alert", "severity": "medium"},
    {"source": "network", "finding": "First-ever transfer to this recipient", "severity": "high"},
    {"source": "network", "finding": "Recipient in Cyprus — FATF monitored jurisdiction", "severity": "critical"}
  ],
  "recommended_action": "ESCALATE",
  "regulatory_flags": ["SAR filing required", "OFAC screen needed", "Enhanced due diligence required"],
  "confidence": 0.94,
  "disposition": "BLOCKED_ESCALATED",
  "action_taken": "Transaction blocked — escalated to BSA officer. SAR filing initiated. Account frozen pending review."
}
```

**Try lower risk** — change the amount and signals to see different routing:

```bash
# LOW risk — will auto-clear
curl -X POST http://localhost:8000/webhooks/webhook \
  -H "Content-Type: application/json" \
  -d '{
  "alert_id": "FRD-2026-00900",
  "timestamp": "2026-02-24T14:30:00Z",
  "transaction": {
    "id": "TXN-10000001",
    "type": "ach",
    "amount": 1500.00,
    "currency": "USD",
    "channel": "online_banking",
    "initiated_at": "2026-02-24T14:30:00Z",
    "memo": "Monthly rent payment"
  },
  "sender": {
    "account_id": "ACCT-55512345",
    "name": "Jane Smith",
    "account_type": "personal_checking",
    "account_age_days": 730,
    "avg_monthly_volume": 4200.00,
    "kyc_status": "full_verified",
    "previous_alerts": 0,
    "last_address_change": "2024-06-01"
  },
  "recipient": {
    "name": "Parkview Apartments LLC",
    "bank": "Chase",
    "country": "US",
    "account_type": "business",
    "is_first_transfer": false,
    "swift_code": "CHASUS33"
  },
  "risk_signals": {
    "amount_vs_average": 0.36,
    "unusual_hour": false,
    "new_recipient": false,
    "high_risk_jurisdiction": false,
    "velocity_24h": 1,
    "velocity_7d": 2
  }
}'
```

---

## Fraud Investigation — UI Report

Same analysis, but renders a styled HTML report instead of routing.

**Flow:** `Start → Set (sample data) → AIAgent → Code (HTML) → Output`

```bash
# Stream via SSE (same as UI)
curl -N -X POST http://localhost:8000/execution-stream/adhoc \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
  "name": "Fraud Investigation Agent",
  "nodes": [
    {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
    {
      "name": "Input", "type": "Set",
      "parameters": {
        "mode": "json",
        "jsonData": "{\"alert_id\":\"FRD-2026-00847\",\"transaction\":{\"type\":\"wire_transfer\",\"amount\":247500,\"currency\":\"USD\"},\"sender\":{\"name\":\"Meridian Holdings LLC\",\"account_age_days\":45,\"avg_monthly_volume\":12400},\"recipient\":{\"name\":\"Greenfield Consulting Group\",\"bank\":\"First National Bank of Cyprus\",\"country\":\"CY\",\"is_first_transfer\":true},\"risk_signals\":{\"amount_vs_average\":19.96,\"unusual_hour\":true,\"new_recipient\":true,\"high_risk_jurisdiction\":true}}",
        "keepOnlySet": true
      },
      "position": {"x": 350, "y": 300}
    },
    {
      "name": "Investigator", "type": "AIAgent",
      "parameters": {
        "model": "gemini-2.5-flash",
        "systemPrompt": "You are a Lead Fraud Investigator. Spawn 3 sub-agents: Transaction Analyzer, Customer Profile Analyst, Network Analyzer. Each investigates the alert from their specialty. Synthesize into a risk assessment.",
        "task": "Investigate this alert:\n\n{{ $json }}",
        "maxIterations": 10, "temperature": 0.2,
        "enableSubAgents": true, "maxAgentDepth": 2,
        "enablePlanning": true, "enableScratchpad": true,
        "outputSchema": "{\"type\":\"object\",\"properties\":{\"risk_score\":{\"type\":\"number\"},\"risk_level\":{\"type\":\"string\"},\"summary\":{\"type\":\"string\"},\"evidence\":{\"type\":\"array\"},\"recommended_action\":{\"type\":\"string\"}}}"
      },
      "position": {"x": 650, "y": 300}
    },
    {"name": "Output", "type": "Output", "parameters": {}, "position": {"x": 950, "y": 300}}
  ],
  "connections": [
    {"source_node": "Start", "target_node": "Input"},
    {"source_node": "Input", "target_node": "Investigator"},
    {"source_node": "Investigator", "target_node": "Output"}
  ]
}'
```

---

## Deep Research Agent

3 specialist researchers (Technology, Market, Academic) analyze a topic in parallel. Produces a maturity assessment with domain scores.

**Flow:** `Start → Set (research brief) → AIAgent → Code (HTML) → Output`

```bash
# Run via E2E test
python3 tests/test_curl_e2e.py 2

# Or stream the seeded workflow directly
WORKFLOW_ID=$(curl -s http://localhost:8000/api/workflows | python3 -c "
import json, sys
wfs = json.load(sys.stdin)
wf = next(w for w in wfs if 'Research' in w['name'])
print(wf['id'])
")
curl -N http://localhost:8000/execution-stream/$WORKFLOW_ID \
  -H "Accept: text/event-stream"
```

**Input brief:**

```json
{
  "topic": "AI Code Generation and Autonomous Software Engineering",
  "scope": "Comprehensive analysis of AI-powered code generation tools, autonomous coding agents, and their impact on software development workflows.",
  "focus_areas": [
    "LLM-based code generation (Copilot, Cursor, Claude Code, etc.)",
    "Autonomous coding agents (Devin, SWE-Agent, OpenHands, etc.)",
    "Code review and testing automation",
    "Enterprise adoption patterns and ROI data"
  ],
  "audience": "Technology leadership evaluating AI coding tools",
  "depth": "deep"
}
```

**Structured output:**

```json
{
  "maturity_score": 6.0,
  "maturity_level": "MATURING",
  "executive_summary": "The field of AI Code Generation is rapidly evolving...",
  "key_findings": [
    {"domain": "technology", "finding": "LLM-based tools now handle 30-40% of routine code...", "score": 7.0},
    {"domain": "market", "finding": "Market consolidating around IDE-integrated copilots...", "score": 6.0},
    {"domain": "academic", "finding": "Active research on code reasoning and verification...", "score": 5.0}
  ],
  "opportunities": [
    "Enterprise adoption still early — first-mover advantage in regulated industries",
    "Testing and code review automation lagging behind generation — gap to fill"
  ],
  "risks": [
    "IP and licensing concerns with training data",
    "Over-reliance creating skill atrophy in junior developers"
  ],
  "outlook": "Over the next 6-12 months, expect continued consolidation..."
}
```

---

## Startup Due Diligence Agent

3 analysts (Market Opportunity, Team & Execution, Financial & Unit Economics) evaluate a startup pitch in parallel.

**Flow:** `Start → Set (startup data) → AIAgent → Code (HTML) → Output`

```bash
python3 tests/test_curl_e2e.py 3
```

**Input data — "Synthwave AI" Series A pitch:**

```json
{
  "company": "Synthwave AI",
  "stage": "Series A",
  "ask": "$18M at $90M pre-money valuation",
  "sector": "Developer Tools / AI Testing Infrastructure",
  "pitch": "AI-native testing infrastructure that auto-generates and self-heals test suites.",
  "team": {
    "founders": [
      {"name": "Maya Chen", "role": "CEO", "background": "Ex-Google Staff (Chrome DevTools), prev exit to DataDog"},
      {"name": "Raj Patel", "role": "CTO", "background": "Ex-Meta AI Research, built LLM testing infra for 2000+ engineers"}
    ],
    "headcount": 14,
    "engineering_pct": 0.78
  },
  "metrics": {
    "arr": 1200000,
    "arr_growth_yoy": 4.2,
    "net_dollar_retention": 1.42,
    "gross_churn_monthly": 0.012,
    "ltv_cac_ratio": 7.0,
    "payback_months": 8.4,
    "gross_margin": 0.82
  },
  "financials": {
    "total_raised": 4500000,
    "monthly_burn": 180000,
    "runway_months": 11
  }
}
```

**Structured output:**

```json
{
  "investment_score": 8.7,
  "recommendation": "STRONG_BUY",
  "thesis": "Synthwave AI presents a compelling opportunity: exceptional team with domain expertise, self-healing testing addresses a critical pain point, and unit economics (7x LTV:CAC, 142% NDR) are best-in-class for stage.",
  "dimension_scores": {"market": 9, "team": 9, "financials": 8},
  "findings": [
    {"analyst": "market", "finding": "$12B TAM with strong timing — LLM costs dropped 10x", "sentiment": "positive"},
    {"analyst": "team", "finding": "Both founders have directly relevant experience", "sentiment": "positive"},
    {"analyst": "financials", "finding": "4.2x YoY ARR growth with 82% gross margins", "sentiment": "positive"},
    {"analyst": "financials", "finding": "11 months runway — needs this raise to continue growth", "sentiment": "negative"}
  ],
  "key_risks": [
    "Concentration risk: 6 enterprise customers drive majority of ARR",
    "VP Sales still open — no dedicated sales leader for Series A push",
    "Competitive moat unclear if major IDE players build native testing"
  ],
  "next_steps": [
    "Schedule partner meeting with deep dive on self-healing demo",
    "Reference calls with 3 enterprise customers",
    "Technical diligence on model architecture and test accuracy benchmarks"
  ]
}
```

---

## Content Quality Pipeline

3 sub-agents with fundamentally different roles — **Writer** (creative), **Fact-Checker** (verification), **Editor** (critique) — process a content brief in parallel.

**Flow:** `Start → Set (content brief) → AIAgent → Code (HTML) → Output`

```bash
python3 tests/test_curl_e2e.py 4
```

**Input brief:**

```json
{
  "type": "blog_post",
  "topic": "The Hidden Cost of AI-Generated Code",
  "word_count": 1500,
  "tone": "Thoughtful and provocative — challenges conventional wisdom without being contrarian for its own sake.",
  "audience": "Senior software engineers and engineering managers using AI coding tools.",
  "key_points": [
    "AI-generated code is optimized for passing code review, not maintainability",
    "Teams report 30-40% productivity gains initially, but technical debt accumulates",
    "Junior developers learning to prompt instead of learning to code",
    "The testing paradox: AI code lacks edge case coverage"
  ],
  "do_not": [
    "Don't be Luddite — acknowledge real benefits before critiquing",
    "Don't end with 'only time will tell' — provide actionable takeaways"
  ]
}
```

**Structured output:**

```json
{
  "quality_score": 6.5,
  "status": "REVISIONS_NEEDED",
  "draft": "# The Hidden Cost of AI-Generated Code\n\nThe productivity numbers are real...",
  "fact_check_results": [
    {"claim": "30-40% productivity gains from AI coding tools", "verdict": "PARTIALLY_TRUE", "source": "GitHub Copilot study showed 55% faster task completion, but real-world gains vary"},
    {"claim": "GitClear 2024 study on code churn", "verdict": "VERIFIED", "source": "Published study found 2x increase in code churn for AI-assisted repos"},
    {"claim": "AI optimizes for happy paths", "verdict": "UNVERIFIED", "source": "Anecdotally supported but no rigorous study"}
  ],
  "editorial_feedback": [
    {"dimension": "Headline Strength", "rating": 8, "feedback": "Intriguing — 'hidden cost' framing creates curiosity"},
    {"dimension": "Argument Structure", "rating": 6, "feedback": "Strong opening but middle section needs tighter transitions"},
    {"dimension": "Evidence Usage", "rating": 5, "feedback": "Too many claims without specific citations"}
  ],
  "priority_revisions": [
    "Add specific citations for the 30-40% productivity claim",
    "Strengthen the transition between 'benefits' and 'costs' sections",
    "Add a concrete code example showing AI-generated vs hand-written test coverage",
    "Rewrite conclusion with 3 actionable takeaways instead of general advice"
  ]
}
```

---

## Simple Data Pipeline (No AI)

Not every workflow needs an agent. Filter, transform, and output data:

```bash
curl -X POST http://localhost:8000/api/workflows/run-adhoc \
  -H "Content-Type: application/json" \
  -d '{
  "nodes": [
    {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
    {
      "name": "Data", "type": "Set",
      "parameters": {
        "mode": "json",
        "jsonData": "{\"users\": [{\"name\": \"Alice\", \"age\": 30, \"role\": \"engineer\"}, {\"name\": \"Bob\", \"age\": 17, \"role\": \"intern\"}, {\"name\": \"Carol\", \"age\": 25, \"role\": \"designer\"}]}",
        "keepOnlySet": true
      },
      "position": {"x": 350, "y": 300}
    },
    {
      "name": "Transform", "type": "Code",
      "parameters": {
        "code": "users = json_data.get(\"users\", [])\nadults = [u for u in users if u[\"age\"] >= 18]\nreturn [{\"json\": {\"adults\": adults, \"count\": len(adults)}}]"
      },
      "position": {"x": 600, "y": 300}
    },
    {"name": "Result", "type": "Output", "parameters": {}, "position": {"x": 850, "y": 300}}
  ],
  "connections": [
    {"source_node": "Start", "target_node": "Data"},
    {"source_node": "Data", "target_node": "Transform"},
    {"source_node": "Transform", "target_node": "Result"}
  ]
}'
```

**Response:**

```json
{
  "execution_id": "...",
  "status": "success",
  "data": {
    "Result": [{"json": {"adults": [{"name": "Alice", "age": 30, "role": "engineer"}, {"name": "Carol", "age": 25, "role": "designer"}], "count": 2}}]
  }
}
```

---

## Conditional Routing

Route by field values using If/Switch:

```bash
curl -X POST http://localhost:8000/api/workflows/run-adhoc \
  -H "Content-Type: application/json" \
  -d '{
  "nodes": [
    {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
    {
      "name": "Order", "type": "Set",
      "parameters": {
        "mode": "json",
        "jsonData": "{\"amount\": 5200, \"customer\": \"Acme Corp\", \"type\": \"enterprise\"}",
        "keepOnlySet": true
      },
      "position": {"x": 350, "y": 300}
    },
    {
      "name": "Check", "type": "If",
      "parameters": {
        "conditions": [{"field": "amount", "operation": "greaterThan", "value": 5000}]
      },
      "position": {"x": 600, "y": 300}
    },
    {
      "name": "High Value", "type": "Set",
      "parameters": {"mode": "manual", "values": [{"name": "approval", "value": "manager_review_required"}]},
      "position": {"x": 900, "y": 200}
    },
    {
      "name": "Standard", "type": "Set",
      "parameters": {"mode": "manual", "values": [{"name": "approval", "value": "auto_approved"}]},
      "position": {"x": 900, "y": 400}
    }
  ],
  "connections": [
    {"source_node": "Start", "target_node": "Order"},
    {"source_node": "Order", "target_node": "Check"},
    {"source_node": "Check", "target_node": "High Value", "source_output": "true"},
    {"source_node": "Check", "target_node": "Standard", "source_output": "false"}
  ]
}'
```

Amount is 5200 > 5000 → takes the "High Value" branch → `{"approval": "manager_review_required"}`.

---

## One-Shot LLM Chat

Simple LLM call without agent loop or tools:

```bash
curl -X POST http://localhost:8000/api/workflows/run-adhoc \
  -H "Content-Type: application/json" \
  -d '{
  "nodes": [
    {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
    {
      "name": "Prompt", "type": "Set",
      "parameters": {
        "mode": "json",
        "jsonData": "{\"text\": \"Explain quantum computing in 2 sentences.\"}",
        "keepOnlySet": true
      },
      "position": {"x": 350, "y": 300}
    },
    {
      "name": "LLM", "type": "LLMChat",
      "parameters": {
        "model": "gemini-2.5-flash",
        "prompt": "{{ $json.text }}",
        "systemPrompt": "You are a helpful science explainer.",
        "temperature": 0.5
      },
      "position": {"x": 600, "y": 300}
    },
    {"name": "Output", "type": "Output", "parameters": {}, "position": {"x": 850, "y": 300}}
  ],
  "connections": [
    {"source_node": "Start", "target_node": "Prompt"},
    {"source_node": "Prompt", "target_node": "LLM"},
    {"source_node": "LLM", "target_node": "Output"}
  ]
}'
```

---

## Sub-Agent Architecture

The `AIAgent` node supports spawning child agents via the `spawn_agent` tool:

```
Parent Agent (depth 0)
 ├─ spawn_agent("Analyze market...")  ─→  Child A (depth 1)  ─→  scratchpad + findings
 ├─ spawn_agent("Analyze team...")    ─→  Child B (depth 1)  ─→  scratchpad + findings
 └─ spawn_agent("Analyze finance...") ─→  Child C (depth 1)  ─→  scratchpad + findings
                                              ↓ (all run concurrently via asyncio.gather)
                                     Parent receives all 3 results
                                     Synthesizes into structured JSON
```

**AIAgent parameters:**

| Parameter | Default | Description |
|---|---|---|
| `model` | — | LLM model to use (e.g. `gemini-2.5-flash`, `gpt-4o`, `claude-sonnet-4-20250514`) |
| `systemPrompt` | — | System prompt for the agent |
| `task` | — | User task (supports `{{ $json }}` expressions) |
| `maxIterations` | 10 | Max agent loop iterations |
| `temperature` | 0.2 | LLM temperature |
| `enableSubAgents` | false | Enable `spawn_agent` tool |
| `maxAgentDepth` | 2 | Max nesting (1 = children only, 2 = grandchildren too) |
| `allowRecursiveSpawn` | false | Whether children can also spawn |
| `enablePlanning` | true | Agent uses `<plan>` / `<reflect>` blocks |
| `enableScratchpad` | true | Agent gets `memory_store` / `memory_recall` tools |
| `outputSchema` | — | JSON Schema for structured output validation |

**spawn_agent tool call:**

```json
{
  "task": "What the sub-agent should do (required)",
  "name": "Human-readable label",
  "model": "override model (optional, inherits parent)",
  "max_iterations": 5,
  "temperature": 0.2,
  "context_snippets": [{"label": "Prior findings", "content": "..."}],
  "expected_output": {"type": "object", "properties": {"score": {"type": "number"}}}
}
```

**What each child gets:**
- Its own agent loop with independent iterations
- Its own scratchpad (`memory_store` / `memory_recall`)
- Parent's scratchpad as read-only `parent_scratchpad`
- Inherited tools and model config from parent

**What the parent gets back:**

```json
{
  "response": "The child's final text response",
  "iterations": 3,
  "evidence": {"market_score": 8, "key_finding": "..."}
}
```

---

## SSE Event Format

When streaming via `/execution-stream/adhoc` or `/execution-stream/{id}`, events arrive as:

```
data: {"type": "...", "nodeName": "...", "data": [...], ...}
```

**Event types:**

| Event | When | Key fields |
|---|---|---|
| `execution:start` | Workflow begins | `executionId` |
| `node:start` | Node begins | `nodeName`, `nodeType` |
| `node:complete` | Node finishes | `nodeName`, `data` (output items) |
| `node:error` | Node fails | `nodeName`, `error` |
| `agent:thinking` | LLM text alongside tool calls | `content` |
| `agent:plan` | Agent wrote a `<plan>` block | `plan` |
| `agent:reflect` | Agent wrote a `<reflect>` block | `reflection` |
| `agent:tool_call` | Agent calling a tool | `tool`, `arguments`, `iteration` |
| `agent:tool_result` | Tool returned result | `tool`, `result`, `is_error` |
| `agent:spawn` | Sub-agent spawned | `task`, `model`, `depth` |
| `agent:child_complete` | Sub-agent finished | `depth`, `iterations`, `has_evidence` |
| `agent:output_validation` | Structured output validated | `status` |
| `execution:complete` | Workflow done | — |
| `execution:error` | Workflow failed | `error` |

---

## Server Logs

The server prints detailed agent traces to stdout. Watch live:

```bash
tail -f /tmp/workflow-engine.log
```

**Sample output:**

```
============================================================
AGENT START | Investment Analyst (depth=0)
  model=gemini-2.5-flash  max_iter=10  tools=3  temp=0.2
  task: Conduct due diligence on the following startup...
============================================================
  --- iteration 1/10 (Investment Analyst) ---
  PLAN: Dispatching 3 specialist analysts concurrently...

  SPAWN: Market Opportunity Analyst (depth 1/2)
    task: Evaluate the market thesis for Synthwave AI...
    ============================================================
    AGENT START | Investment Analyst/Market Opportunity Analyst (depth=1)
    ============================================================
      --- iteration 1/5 ---
      SCRATCHPAD STORE: [market_analysis] = {score: 8, justification: "TAM of $12B..."}
      --- iteration 2/5 ---
      SCRATCHPAD RECALL: [market_analysis] => {score: 8, ...}
      FINAL RESPONSE (3678 chars)
    ============================================================
    AGENT END | Investment Analyst/Market Opportunity Analyst (depth=1)
    ============================================================
  CHILD DONE: Market Opportunity Analyst
    iterations=2  scratchpad=['market_analysis']

  SPAWN: Team & Execution Analyst (depth 1/2)
    ...runs concurrently with Market Analyst above...
  CHILD DONE: Team & Execution Analyst
    iterations=2  scratchpad=['team_analysis']

  SPAWN: Financial Analyst (depth 1/2)
    ...runs concurrently...
  CHILD DONE: Financial Analyst
    iterations=3  scratchpad=['financial_analysis']

  TOOL RESULT: {response: "Market score 9/10...", evidence: {market_analysis: {...}}}
  TOOL RESULT: {response: "Team score 9/10...", evidence: {team_analysis: {...}}}
  TOOL RESULT: {response: "Financial score 8/10...", evidence: {financial_analysis: {...}}}

  --- iteration 2/10 (Investment Analyst) ---
  SCRATCHPAD RECALL: [market_analysis] => {score: 9, ...}
  SCRATCHPAD RECALL: [team_analysis] => {score: 9, ...}
  SCRATCHPAD RECALL: [financial_analysis] => {score: 8, ...}

  FINAL REFLECT: Market=9, Team=9, Financials=8. Weighted: 8.7. Recommendation: STRONG_BUY
  FINAL RESPONSE (4126 chars)
  STRUCTURED OUTPUT: validated OK — keys=['investment_score', 'recommendation', 'thesis', ...]
============================================================
AGENT END | Investment Analyst (depth=0)
  iterations=2  response_len=4126
============================================================
```

---

## E2E Test Script

```bash
# Run any seeded workflow by index (0-4)
python3 tests/test_curl_e2e.py 0   # Fraud Investigation Agent (UI)
python3 tests/test_curl_e2e.py 1   # Fraud Investigation API (webhook — needs webhook trigger)
python3 tests/test_curl_e2e.py 2   # Deep Research Agent
python3 tests/test_curl_e2e.py 3   # Startup Due Diligence Agent
python3 tests/test_curl_e2e.py 4   # Content Quality Pipeline
```

The script:
1. Checks server health
2. POSTs the workflow definition to `/execution-stream/adhoc`
3. Parses all SSE events in real-time
4. Prints colored event stream
5. Verifies: no errors, all nodes completed, sub-agents spawned, structured output produced, HTML generated
