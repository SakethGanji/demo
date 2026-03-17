# Prompt Analyzer API Spec

## Endpoint

```
POST http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a
Content-Type: application/json
```

Single endpoint, 6 modes routed by the `mode` field.

---

## Request

```json
{
  "prompt": "string (required)",
  "mode": "chat | optimize | clarity | structure | examples | efficiency (required)",
  "session_id": "string (optional, chat mode only, defaults to 'default')"
}
```

---

## Modes

### 1. `clarity` — Quick clarity analysis

Finds ambiguity, vague terms, contradictions, undefined terms.

**Request:**
```json
{
  "prompt": "Your prompt text here...",
  "mode": "clarity"
}
```

**Response:**
```json
{
  "status": "success",
  "executionId": "exec_...",
  "data": [
    {
      "response": "markdown string — clarity score X/10, issues by severity",
      "model": "gemini-2.0-flash"
    }
  ]
}
```

**Tested curl:**
```bash
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "You are a helpful assistant. Be good and do things properly.", "mode": "clarity"}'
```

---

### 2. `structure` — Quick structure analysis

Evaluates section ordering, redundancy, formatting consistency, logical grouping.

**Request:**
```json
{
  "prompt": "Your prompt text here...",
  "mode": "structure"
}
```

**Response:** Same shape as clarity. `response` contains structure score X/10, issues, suggested reorganization.

**Tested curl:**
```bash
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "Your prompt text here...", "mode": "structure"}'
```

---

### 3. `examples` — Quick examples audit

Finds contradictory/redundant/missing examples, coverage gaps, quality issues.

**Request:**
```json
{
  "prompt": "Your prompt text here...",
  "mode": "examples"
}
```

**Response:** Same shape as clarity. `response` contains examples score X/10, issues, recommendations.

**Tested curl:**
```bash
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "Your prompt text here...", "mode": "examples"}'
```

---

### 4. `efficiency` — Quick efficiency analysis

Finds verbosity, filler, token waste, compression opportunities.

**Request:**
```json
{
  "prompt": "Your prompt text here...",
  "mode": "efficiency"
}
```

**Response:** Same shape as clarity. `response` contains efficiency score X/10, estimated % token savings, top 5 quick wins.

**Tested curl:**
```bash
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "Your prompt text here...", "mode": "efficiency"}'
```

---

### 5. `chat` — Conversational prompt discussion (has memory)

Stateful AI agent. Remembers prior messages within the same `session_id`. Use for back-and-forth discussion about a prompt.

**Request (first message):**
```json
{
  "prompt": "Here is my prompt: You are a helpful assistant... What are the biggest issues?",
  "mode": "chat",
  "session_id": "user-123-session"
}
```

**Request (follow-up, same session):**
```json
{
  "prompt": "Can you rewrite the first sentence to be more specific for customer support?",
  "mode": "chat",
  "session_id": "user-123-session"
}
```

**Response:**
```json
{
  "status": "success",
  "executionId": "exec_...",
  "data": [
    {
      "response": "markdown string — conversational reply",
      "toolCalls": [],
      "iterations": 1
    }
  ]
}
```

**Tested curl:**
```bash
# First message
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "Here is my prompt: You are a helpful assistant. What are the issues?", "mode": "chat", "session_id": "test-1"}'

# Follow-up (remembers context)
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "Rewrite the first sentence for customer support", "mode": "chat", "session_id": "test-1"}'
```

---

### 6. `optimize` — Full multi-agent optimization (slowest, most thorough)

Spawns 4 sub-agents (clarity, structure, examples, efficiency) in parallel, then synthesizes a scored report with a full optimized rewrite.

**Request:**
```json
{
  "prompt": "Your full prompt text here...",
  "mode": "optimize"
}
```

**Response:**
```json
{
  "status": "success",
  "executionId": "exec_...",
  "data": [
    {
      "response": "markdown string — full report with scorecard table, critical issues, quick wins, optimized prompt, changelog",
      "toolCalls": [
        {
          "tool": "spawn_agent",
          "input": { "name": "Clarity Analysis", "task": "..." },
          "output": { "response": "...", "iterations": 5 },
          "id": "uuid",
          "is_error": false
        }
      ],
      "iterations": 5
    }
  ]
}
```

The `response` field contains a markdown report with this structure:
- **Scorecard** — table with Clarity/Structure/Examples/Efficiency scores out of 10
- **Critical Issues** — most impactful problems with quoted text
- **Quick Wins** — easy fixes under 5 min each
- **Optimized Prompt** — full rewritten prompt
- **Changelog** — bullet list of every change and why

**Tested curl:**
```bash
curl -s http://localhost:8000/webhook/wf_019cf9a1-dec8-79d9-a60b-3817ce07b23a \
  -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "You are a helpful assistant. Be good and do things properly...", "mode": "optimize"}'
```

---

## Response Shape Summary

All modes return:
```json
{
  "status": "success",
  "executionId": "exec_...",
  "data": [ { "response": "markdown string", ...extra_fields } ]
}
```

| Mode | Extra fields in `data[0]` | Speed |
|------|--------------------------|-------|
| `clarity` | `model`, `usage` | ~3-5s |
| `structure` | `model`, `usage` | ~3-5s |
| `examples` | `model`, `usage` | ~3-5s |
| `efficiency` | `model`, `usage` | ~3-5s |
| `chat` | `toolCalls`, `iterations` | ~3-5s |
| `optimize` | `toolCalls`, `iterations` | ~15-30s |

The `response` field is always **markdown-formatted** text. Render it with a markdown renderer.

---

## Error Cases

**Invalid/missing mode** — falls through to Switch fallback, returns empty:
```json
{ "status": "success", "executionId": "exec_...", "data": [] }
```

**Missing prompt** — agent returns generic error in `response` field.

**Workflow not active** — HTTP 400:
```json
{ "detail": "Workflow is not active" }
```

---

## App JSON

The app UI definition is at `~/Downloads/prompt-analyzer-app.json` with all webhook URLs pre-configured.

Import it and all 6 buttons (Chat, Full Optimize, Clarity, Structure, Examples, Efficiency) will work out of the box.
