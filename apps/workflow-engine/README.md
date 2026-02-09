# Workflow Engine — Architecture Guide

A Python/FastAPI backend that executes DAG-based workflows with first-class AI agent support. Think n8n, but with built-in multi-LLM orchestration, autonomous agents, and composable memory systems.

This guide explains **how and why** the system is designed the way it is — the mental models, design patterns, tradeoffs, and architectural decisions behind the code.

---

## Table of Contents

- [The Plain-English Version](#the-plain-english-version)
- [Why No LangChain / Google ADK / Frameworks?](#why-no-langchain--google-adk--frameworks)

**Part 1: Foundations**
- [The Mental Model](#the-mental-model)
- [LLMs from Scratch](#llms-from-scratch)
  - [What an LLM Actually Is](#what-an-llm-actually-is)
  - [The Conversation Protocol](#the-conversation-protocol)
  - [Tool Calling: How LLMs Take Actions](#tool-calling-how-llms-take-actions)
  - [The Agent Pattern: LLMs in a Loop](#the-agent-pattern-llms-in-a-loop)
  - [The Context Window Problem](#the-context-window-problem)

**Part 2: System Architecture**
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [The LLM Provider: One Interface, Four Backends](#the-llm-provider-one-interface-four-backends)
- [The Workflow Engine: DAGs, Queues, and BFS](#the-workflow-engine-dags-queues-and-bfs)
- [The Expression Engine: Safe User Code](#the-expression-engine-safe-user-code)
- [The Node System: Plugin Architecture](#the-node-system-plugin-architecture)

**Part 3: AI Agent Design**
- [The AI Agent Node: Anatomy of an Agent](#the-ai-agent-node-anatomy-of-an-agent)
- [Tool Resolution: Where Do Tools Come From?](#tool-resolution-where-do-tools-come-from)
- [The Sub-Agent Pattern: Agents Spawning Agents](#the-sub-agent-pattern-agents-spawning-agents)
- [Structured Output: Forcing JSON from Free Text](#structured-output-forcing-json-from-free-text)
- [Safety Systems: Preventing Runaway Agents](#safety-systems-preventing-runaway-agents)

**Part 4: Memory Architecture**
- [Why Memory Matters](#why-memory-matters)
- [The Memory Interface Contract](#the-memory-interface-contract)
- [Memory Strategies: A Spectrum of Tradeoffs](#memory-strategies-a-spectrum-of-tradeoffs)
- [Choosing the Right Memory Strategy](#choosing-the-right-memory-strategy)

**Part 5: The AI Chat Service**
- [Two AI Systems, One Codebase](#two-ai-systems-one-codebase)
- [The Workflow Generator: An Agent That Builds Agents](#the-workflow-generator-an-agent-that-builds-agents)
- [Prompt Engineering: How the Generator Stays on Track](#prompt-engineering-how-the-generator-stays-on-track)

**Part 6: Reference**
- [Key Design Patterns Summary](#key-design-patterns-summary)
- [Data Types Quick Reference](#data-types-quick-reference)
- [File Map](#file-map)

---

## The Plain-English Version

Before diving into the technical details — here's how this whole thing actually works, explained simply.

### What does this thing do?

It's a workflow automation engine. Users build workflows by connecting boxes (nodes) together on a canvas — like a flowchart. "When I get a webhook, fetch some data, process it with AI, and send an email." The engine takes that flowchart and actually runs it.

Some of those boxes are AI-powered. A user can drop an "AI Agent" node into their workflow, give it some tools (like "search the web" or "run code"), and the agent will figure out how to complete a task on its own. It thinks, uses tools, looks at the results, thinks some more, and eventually gives you an answer.

### How does the AI part actually work?

Here's the thing that surprised me when I first learned this: **there's almost no AI code**. The "intelligence" is entirely inside models like GPT-4 or Gemini that we call over an API. Our code is just plumbing.

Here's what really happens when an AI agent runs:

1. We build a big text message that says: "You are a helpful assistant. Here are some tools you can use. Here's the user's task."
2. We send that to an LLM API (Gemini, OpenAI, whoever).
3. The LLM either says "I want to call this tool with these arguments" or "Here's my final answer."
4. If it wants a tool: we run that tool ourselves, send the result back, and go to step 2 again.
5. If it gives a final answer: we're done.

That's literally it. An AI agent is a **while loop**. The LLM is just a really smart autocomplete that happens to be good at deciding what to do next. All the actual work — calling APIs, running code, managing databases — is normal code that we wrote.

### Why is there so much code then?

Because the plumbing is complicated:

- **Different LLM providers speak different languages.** Gemini wants messages formatted one way, OpenAI another way, Anthropic yet another. We need a translation layer so the rest of the code doesn't care which model it's using.

- **Conversations get too long.** LLMs have a size limit on how much text they can read. When an agent has been working for a while, the conversation history gets huge. We need to intelligently trim old messages without losing important context.

- **Agents need memory between runs.** LLMs forget everything between API calls. If a user talks to an agent today and comes back tomorrow, we need to store and replay the relevant history. There are many strategies for this — keep the last 20 messages, summarize old ones, search for relevant ones, extract key facts — each with tradeoffs.

- **Agents need to be safe.** An autonomous agent calling tools in a loop could go haywire — infinite loops, calling the wrong things, running dangerous code. We need circuit breakers, depth limits, sandboxing, timeouts.

- **The whole thing needs to work inside a workflow engine.** The AI agent is just one node type. It needs to receive data from upstream nodes, pass results downstream, handle errors, retry on failure, and report progress in real-time.

### What are the two AI systems?

There are two completely separate uses of AI in this codebase:

1. **The AI Agent node** — this is what users build with. They put it in their workflows, connect tools to it, and it does tasks autonomously. A user might build a workflow: "Webhook triggers → AI Agent researches the topic → Send email with findings."

2. **The sidebar chat** — this is a built-in assistant that helps users build workflows. You type "make me a workflow that monitors a website and alerts me when prices drop" and it actually builds the workflow for you. It's an agent too — it has tools like "look up what nodes are available", "create a workflow", "test it", "fix errors." It's an agent that builds workflows containing agents. Meta.

### How does the workflow engine run things?

Picture a flowchart. Start at the top, follow the arrows. When the path splits, run both branches at the same time. When branches rejoin (Merge node), wait for all of them before continuing.

That's it. Under the hood it's a breadth-first search on a graph — but conceptually it's just "follow the arrows, run things in parallel when you can, wait when you must."

### What's a "subnode"?

Regular nodes do things — fetch data, call an API, make a decision. Subnodes are different: they **configure** another node.

An AI Agent needs a model, maybe some memory, and some tools. Instead of putting all that configuration inside the agent's settings panel, we let users connect separate subnode boxes to it:

- Connect a "GPT-4o" model subnode → the agent uses GPT-4o
- Swap it for a "Gemini Flash" subnode → now it uses Gemini
- Connect a "Buffer Memory" subnode → it remembers the last 20 messages
- Connect a "Code Tool" subnode → now the agent can run Python code
- Connect an "HTTP Tool" subnode too → now it can also make web requests

This makes everything mix-and-match. You don't need different agent types for different configurations — just plug in different subnodes.

---

## Why No LangChain / Google ADK / Frameworks?

This is a common question. There's an entire ecosystem of LLM frameworks — LangChain, LlamaIndex, Google ADK (Agent Development Kit), CrewAI, AutoGen, Semantic Kernel — and we don't use any of them. Here's why, what we built instead, and what we're giving up.

### What do these frameworks actually do?

At their core, they all provide roughly the same things:

| Feature | What it is | Framework example |
|---------|-----------|-------------------|
| **LLM abstraction** | One interface to call any model | LangChain's `ChatModel`, ADK's `LlmAgent` |
| **Tool/function calling** | Let the LLM call your functions | LangChain's `Tool`, ADK's `FunctionTool` |
| **Agent loop** | The while-loop that calls LLM → tools → LLM | LangChain's `AgentExecutor`, ADK's agent loop |
| **Memory** | Store and replay conversation history | LangChain's `ConversationBufferMemory`, etc. |
| **Chains/pipelines** | Compose multiple steps together | LangChain's `Chain`, LlamaIndex's `QueryPipeline` |
| **RAG** | Retrieve documents to give the LLM context | LlamaIndex's whole thing, LangChain's retrievers |
| **Prompt templates** | Reusable prompt patterns | LangChain's `PromptTemplate` |
| **Output parsers** | Force structured output from LLMs | LangChain's `PydanticOutputParser` |
| **Callbacks/tracing** | Observe what the agent is doing | LangChain's callbacks, LangSmith |

### How do we do each of these ourselves?

Here's the mapping between what frameworks give you and what we built:

| Feature | Framework approach | Our approach | Our file |
|---------|-------------------|-------------|----------|
| **LLM abstraction** | `ChatOpenAI()`, `ChatGoogleGenerativeAI()` — provider-specific classes behind a base class | Single `call_llm(model, messages, tools)` function that routes by model name prefix. We wrote the format converters ourselves. | `engine/llm_provider.py` |
| **Tool calling** | `@tool` decorators, `Tool(name, func, description)` classes | Dict with `{name, description, input_schema, execute}`. The LLM sees the schema, we call the execute function. | `nodes/subnodes/tools/*.py` |
| **Agent loop** | `AgentExecutor.run()` — a class you configure | A `while` loop in `_run_agent_loop()`. ~180 lines of code. That's the entire agent. | `nodes/ai/ai_agent.py` |
| **Memory** | `ConversationBufferMemory()` and 10+ other classes | 9 memory subnodes, each implementing `getHistory()` / `addMessage()`. Same strategies, we just wrote them. | `nodes/subnodes/memory/*.py` |
| **Chains/pipelines** | `chain = prompt \| llm \| parser` — LCEL syntax | The workflow DAG itself. Nodes are connected visually, the engine runs them in order. We don't need a code-level chaining abstraction because the visual workflow IS the chain. | `engine/workflow_runner.py` |
| **Prompt templates** | `PromptTemplate("Tell me about {topic}")` | Expression engine: `{{ $json.topic }}` resolved at runtime by `simpleeval`. | `engine/expression_engine.py` |
| **Output parsers** | `PydanticOutputParser(pydantic_object=MySchema)` | Two-pass structured output: agent thinks freely, then a second LLM call formats to JSON schema. | `ai_agent.py` `outputSchema` |
| **Callbacks/tracing** | `LangChainTracer`, callback handlers | `ExecutionEvent` system — emits events (THINKING, TOOL_CALL, TOOL_RESULT) via callbacks, streamed as SSE. | `engine/types.py`, event emission in agent |
| **Sub-agents** | CrewAI crews, AutoGen multi-agent, ADK sub-agents | `spawn_agent` / `spawn_agents_parallel` tools with `AgentContext` depth tracking. | `ai_agent.py` |

### Why build it ourselves?

**1. The core loop is trivially simple.**

The entire agent pattern is ~30 lines of meaningful logic:

```python
while iterations < max:
    response = call_llm(messages, tools)
    if response.tool_calls:
        for tc in response.tool_calls:
            result = execute(tc.name, tc.args)
            messages.append(tool_result(result))
    else:
        return response.text
```

LangChain wraps this in multiple layers of abstraction — `AgentExecutor`, `BaseSingleActionAgent`, `AgentAction`, `AgentFinish`, `OutputParser`, callback managers — making it harder to debug and modify. When the core logic is this simple, the abstraction costs more than it saves.

**2. We already have a pipeline system.**

LangChain's biggest value-add is chaining — composing LLM calls, tools, and transformations into pipelines. But we already have a workflow engine that does exactly this. Nodes connected by edges, executed in order, with branching, merging, and looping. Adding LangChain's chaining on top would be a second pipeline system inside the first one.

**3. Format conversion is ~200 lines, not 20,000.**

The LLM provider layer — converting between OpenAI, Gemini, and Anthropic message formats — is about 200 lines of straightforward code. LangChain's equivalent spans dozens of files with inheritance hierarchies, retry decorators, serialization layers, and callback hooks. Our version is easy to read, easy to debug, and easy to update when providers change their APIs (which they do frequently).

**4. Full control over every API parameter.**

When Gemini adds a new feature (like `response_schema` for structured output), we add one line. With frameworks, you wait for the framework to support it, or fight with their abstraction to pass it through. We've hit this wall before — wanting to use a provider-specific feature that the framework hasn't wrapped yet.

**5. No dependency hell.**

LangChain alone pulls in 50+ transitive dependencies. Version conflicts between `langchain-core`, `langchain-community`, `langchain-openai`, etc. are a constant source of breakage. Our LLM dependencies are just the three SDKs: `google-genai`, `openai`, `anthropic`.

### What are we giving up?

Let's be honest about what frameworks do better:

**1. RAG (Retrieval-Augmented Generation)**

This is the big one. LlamaIndex and LangChain have mature, battle-tested RAG pipelines: document loaders for 100+ file types, chunking strategies, vector store integrations (Pinecone, Weaviate, Chroma, etc.), hybrid search, re-ranking, parent-document retrieval.

We have basic vector memory (`vector_memory.py`) that embeds individual messages — but no document ingestion pipeline, no chunking, no vector database integration beyond SQLite. If a user needed "chat with my PDF" or "search across 10,000 documents," they'd need to build that themselves or we'd need to add it.

**What it would take to add:** A document loader node, a chunking strategy, a vector store integration (probably Chroma or pgvector), and a retrieval node. Meaningful work but not architecturally difficult — it's just another node type.

**2. Pre-built integrations (100+ connectors)**

LangChain has ready-made integrations for Slack, Gmail, Google Drive, Notion, GitHub, SQL databases, Wikipedia, Arxiv, and hundreds more. Each one handles auth, pagination, rate limiting, and data formatting.

We have `HttpRequestTool` (generic HTTP) and `WorkflowTool` (call other workflows). Everything else, users build themselves with the HTTP node or Code node.

**What it would take to add:** Each integration is an independent node/tool — add them as needed. The architecture supports it (plugin system), we just haven't built the catalog yet.

**3. Advanced agent patterns**

- **ReAct (Reasoning + Acting)** — LangChain's default agent prompt format that interleaves thinking and actions in a structured way. We just use a basic system prompt + tools. The LLM naturally does something similar but it's not as explicitly structured.
- **Plan-and-Execute** — an agent that first creates a plan, then executes each step. We could build this as a two-phase agent loop but haven't.
- **Multi-agent debate** — multiple agents with different perspectives discuss and converge. CrewAI and AutoGen specialize in this. Our sub-agent system supports parallel agents but not conversational multi-agent patterns.

**What it would take to add:** These are mostly prompt engineering patterns + agent loop variations. ReAct is basically just a different system prompt. Plan-and-Execute would be a second agent loop mode. Multi-agent debate would need agents to read each other's outputs, which our current spawn system doesn't support (children return results to the parent, they don't talk to each other).

**4. Observability and tracing**

LangSmith (LangChain's companion) gives you detailed traces of every LLM call, token usage, latency, cost tracking, prompt versioning, and evaluation tools. We emit execution events (AGENT_THINKING, TOOL_CALL, TOOL_RESULT) for the UI, but there's no persistent trace storage, cost tracking, or evaluation framework.

**What it would take to add:** Log execution events to a database with token counts and timing. The events are already there — they just need to be persisted and given a dashboard.

**5. Prompt management and versioning**

Frameworks offer prompt templates with versioning, A/B testing, and evaluation. Our prompts are hardcoded strings in Python files. For a production system with many prompts that evolve over time, this gets unwieldy.

### The bottom line

We traded **breadth of features** for **simplicity and control**. The frameworks give you 100 integrations, mature RAG, and observability dashboards out of the box. We give ourselves a system where every line of the agent loop is ours, every API call is transparent, and there are zero framework-specific concepts to learn.

For what this project does — a workflow engine where AI agents are one node type among many — that tradeoff makes sense. The workflow engine IS the pipeline framework. Adding LangChain would be layering a framework on top of a framework.

If we needed serious RAG or 50 pre-built integrations, we'd probably add LlamaIndex specifically for the RAG pipeline (it's more focused than LangChain) rather than adopting a full agent framework.

### Are we reinventing the wheel? How hard is it to add more?

Short answer: **no, and not very.**

The reason it doesn't feel like reinventing the wheel is that the wheel is small. The entire LLM agent pattern is a while-loop, a function call, and some message formatting. Frameworks make it *look* complicated by wrapping it in layers of abstraction, but the actual moving parts are simple. We wrote our agent loop in ~180 lines. Our LLM provider is ~700 lines. The memory strategies are each 100-200 lines. None of this is pushing the boundaries of computer science — it's just careful plumbing.

The real question is: **if we want to keep expanding, does it stay manageable or does it snowball?** Here's an honest look at features we don't have yet, how hard each one would be, and whether it's "build it" or "bring in a library" territory:

| Feature | What it is | Effort | Build or buy? |
|---------|-----------|--------|---------------|
| **Streaming responses** | Token-by-token output instead of waiting for the full response. Users see the agent "typing." | **Small.** All three SDKs support streaming. Swap `client.create()` for `client.create(stream=True)`, iterate chunks, yield SSE events. Maybe a day of work. | Build — it's just a different API call mode. |
| **Guardrails / content filtering** | Block harmful inputs, detect PII in outputs, validate the agent isn't going off-script. | **Small-Medium.** Input guardrails = a pre-check LLM call or regex before the agent loop. Output guardrails = a post-check. PII detection could use a library like `presidio`. | Build the hooks, maybe bring in `presidio` for PII. |
| **Semantic caching** | If someone asks the same (or very similar) question twice, return the cached answer instead of calling the LLM again. Saves cost and latency. | **Medium.** Embed the query, check similarity against cached query embeddings, return cached response if above threshold. We already have `get_embedding()` and `cosine_similarity()`. | Build — we have all the primitives already. |
| **Model fallback / routing** | Try Gemini first; if it fails or is slow, fall to GPT-4o. Or route simple questions to a cheap model and hard ones to an expensive model. | **Small.** Wrap `call_llm()` with retry-on-different-model logic. Routing = a fast classifier call (we already do this in the chat service for intent detection). | Build — it's just control flow around `call_llm()`. |
| **Human-in-the-loop** | Agent pauses before executing a dangerous tool and asks the user "should I proceed?" | **Medium.** Need to serialize agent state (messages + pending tool call), yield a "confirmation needed" event, wait for user response, then resume the loop. The hard part is the pause/resume, not the UI. | Build — but requires changes to the agent loop and frontend. |
| **Agent handoff** | One agent realizes "this isn't my area" and transfers the conversation to a different specialized agent. | **Small.** It's just a tool: `transfer_to_agent(agent_name, context)`. The current agent stops, the new one starts with the transferred context. OpenAI's Swarm pattern does exactly this. | Build — it's a tool + agent routing logic. |
| **MCP (Model Context Protocol)** | Anthropic's open standard for connecting tools to LLMs. Instead of hardcoding tool implementations, tools are external servers that any agent can discover and use. | **Medium.** Add an MCP client that discovers tools from MCP servers and converts them to our tool format (`{name, description, input_schema, execute}`). Our tool interface already matches MCP's shape closely. | Build the client, connect to existing MCP servers. |
| **Skills / reusable agent configs** | Pre-packaged agent setups — "Research Analyst" = specific system prompt + web search tool + summary output schema. Users pick a skill instead of configuring from scratch. | **Small.** It's just saved templates — a system prompt, a tool set, and default parameters bundled under a name. Could be a JSON file or database table. The agent itself doesn't change at all. | Build — it's a UI/template feature, not an engine feature. |
| **Reflection / self-critique** | Agent reviews its own output before returning. "Wait, let me double-check that." Improves accuracy on complex tasks. | **Small.** Add an optional post-loop step: append the agent's response as a user message asking "review your answer for errors," call the LLM one more time. Similar to how we already do the structured output two-pass. | Build — it's 10-20 lines in the agent loop. |
| **Multi-modal input** | Send images, audio, or video to the agent, not just text. "Analyze this screenshot." | **Medium.** The LLM APIs all support images already. Main work is handling binary data through the message pipeline (our `NodeData` already has a `binary` field) and updating the format converters for each provider. | Build — the APIs support it, we just need to pipe the data through. |
| **RAG / document Q&A** | Ingest documents (PDFs, web pages, databases), chunk them, embed them, retrieve relevant chunks to answer questions. | **Large.** This is the one area where a library genuinely saves weeks. Document loaders, chunking strategies, embedding pipelines, vector stores, retrieval strategies, re-ranking — lots of moving parts. | **Bring in a library.** LlamaIndex or just use a vector DB directly (pgvector, Chroma). Don't write a chunking pipeline from scratch. |
| **Cost tracking** | Track tokens used and dollars spent per agent run, per workflow, per user. | **Small.** The LLM APIs return token counts in responses. Capture them, multiply by per-token pricing, store in DB. We just don't parse that part of the response today. | Build — it's bookkeeping. |
| **Evaluation / testing** | Automated tests: "given this input, does the agent produce acceptable output?" Run against a test suite, measure accuracy. | **Medium-Large.** The framework part is easy (run agent, compare output). The hard part is defining "acceptable" — exact match? LLM-as-judge? Human review? This is an open research problem. | Build the harness, use an LLM-as-judge for evaluation. |

**The pattern:** Most of these are **small to medium** because our architecture is set up for them. The agent loop is a simple while-loop we fully control — adding a reflection step, a guardrail check, or a caching layer is just adding a few lines before/after the LLM call. The tool system is a simple dict interface — adding MCP, agent handoff, or skills is just adding new tool sources. The memory system is a pluggable strategy — adding semantic caching follows the same pattern.

The only feature that genuinely warrants a library is **RAG**, because document processing is a deep domain with many edge cases (PDF parsing, table extraction, overlapping chunks, metadata filtering) that aren't worth building from scratch.

**So are we reinventing the wheel?** We're reinventing the *hub* — the small central piece that everything connects to. The frameworks reinvented the hub AND built 200 spokes you might never use. Our hub is ~1,500 lines of core code (LLM provider + agent loop + memory interface). Adding a new spoke (feature) is typically a day or two of work because each spoke is independent. The architecture doesn't fight you.

Where it *would* start to hurt is if we needed 50+ pre-built integrations (Slack, Gmail, Salesforce, etc.) or production-grade RAG across millions of documents. At that point, adopting specific libraries (not full frameworks) for those specific needs makes sense — plug in LlamaIndex for RAG, plug in individual API client libraries for integrations — while keeping our own agent loop, memory system, and workflow engine.

---

# Part 1: Foundations

## The Mental Model

The entire system can be understood through one sentence:

> **Workflows are graphs of nodes. Some of those nodes happen to be AI agents. Those agents are just while-loops that call LLMs and execute tools until they're done.**

There's no magic. The "intelligence" comes from the LLM's decisions — the code is pure orchestration. Understanding this is the key to understanding the entire architecture.

```
Workflow = DAG of nodes
    ↓
Node = a function that takes input data and produces output data
    ↓
AI Agent Node = a node whose function is "run an LLM in a loop with tools"
    ↓
Tool = a function the LLM can ask you to call
    ↓
Memory = a strategy for what conversation history to replay to the LLM
    ↓
Sub-Agent = the LLM decides to spawn another LLM loop as a tool call
```

Every layer builds on the one below it. There are no circular dependencies.

---

## LLMs from Scratch

### What an LLM Actually Is

An LLM is a **stateless function**: text in, text out. It has no memory, no state, no persistence. Every call is independent.

```
f("What's 2+2?") → "4"
f("Now multiply that by 3") → "Multiply what by 3?"  // It doesn't remember!
```

This has a profound architectural consequence: **you must manage all state yourself**. The LLM is just a very smart text completion engine that you call repeatedly.

### The Conversation Protocol

To make LLMs useful for multi-turn conversations, the industry settled on a **message-based protocol**. Instead of sending raw text, you send a list of messages with roles:

```python
messages = [
    {"role": "system",    "content": "You are a helpful assistant."},
    {"role": "user",      "content": "What's 2+2?"},
    {"role": "assistant", "content": "4"},                    # You replay its previous answer
    {"role": "user",      "content": "Now multiply that by 3"},
]
# LLM sees the full history and can now respond "12"
```

**The roles:**
| Role | Who writes it | Purpose |
|------|---------------|---------|
| `system` | You (the developer) | Instructions that define behavior. Placed first. The LLM treats these as authoritative. |
| `user` | The human | What the person said |
| `assistant` | The LLM (replayed) | What the LLM previously responded. You store its old responses and replay them so it "remembers." |
| `tool` | Your code | Results from tool calls (explained below) |

**Key insight:** The LLM has no memory. "Memory" is an illusion created by replaying the full conversation every time. This is why the memory system exists — to manage what gets replayed and what gets dropped.

### Tool Calling: How LLMs Take Actions

Plain LLMs can only generate text. **Tool calling** (also called "function calling") lets them request actions in the real world.

**The protocol:**

```
Step 1: You send the question + descriptions of available tools
   messages: "What's the weather in NYC?"
   tools: [{name: "get_weather", description: "...", parameters: {city: string}}]

Step 2: LLM returns a structured request (NOT text)
   response.tool_calls = [ToolCall(name="get_weather", args={"city": "NYC"})]
   response.text = null

Step 3: You execute the function yourself
   result = your_get_weather_function("NYC") → "72°F, sunny"

Step 4: You send the result back as a "tool" message
   messages.append({role: "tool", content: "72°F, sunny", tool_call_id: "abc123"})

Step 5: LLM generates final answer using the result
   response.text = "The weather in NYC is 72°F and sunny!"
```

**Critical design points:**

1. **The LLM never executes anything.** It only generates a structured request saying "please call function X with arguments Y." Your code does the execution. This is a security boundary.

2. **Tools are described by JSON Schema.** The LLM decides which tool to use based on the `name`, `description`, and `parameters` schema you provide. Good descriptions = better tool selection.

3. **The LLM can call multiple tools at once.** A single response might contain several tool calls. You execute them all and send all results back.

4. **Different providers have different wire formats** for tool calls (Gemini uses `FunctionCall` parts, Anthropic uses `tool_use` content blocks, OpenAI uses `tool_calls` on the message). The LLM provider layer normalizes all of these into a single `ToolCall(id, name, args)` format.

### The Agent Pattern: LLMs in a Loop

An agent is the simplest possible pattern built on tool calling:

```python
while iterations < max:
    response = call_llm(messages, tools)

    if response.tool_calls:
        # LLM wants to do something → execute and continue
        for tool_call in response.tool_calls:
            result = execute(tool_call.name, tool_call.args)
            messages.append(tool_result_message(result))
        continue
    else:
        # LLM is done thinking → return final answer
        return response.text
```

**That's it.** An agent is a while-loop. The "intelligence" is the LLM deciding:
- *Which* tool to call (or none, meaning "I'm done")
- *What arguments* to pass
- *When* to stop and give a final answer
- *How* to interpret tool results and decide next steps

**Why this works:** The LLM sees the entire conversation history — including all previous tool calls and results. It can reason about what worked, what failed, and what to try next. Each iteration adds to the conversation, giving the LLM more context for its next decision.

**Why it's powerful:** The agent can chain arbitrary sequences of actions. "Search the web → read the results → write code → test it → fix errors → try again." You don't program this sequence — the LLM figures it out.

### The Context Window Problem

Every LLM has a maximum input size (the "context window"):
- Gemini 2.0 Flash: ~1M tokens
- GPT-4o: ~128K tokens
- Claude Sonnet: ~200K tokens

As the agent loops — calling tools, getting results, reasoning — the message history grows. Eventually it won't fit.

**The solution: context trimming.** Before each LLM call, estimate the token count and drop old messages if over budget. But you can't just drop randomly:

- **Always preserve:** System prompt (instructions) + first user message (the task)
- **Drop from middle:** Oldest assistant/tool exchanges
- **Drop as units:** An assistant message with tool calls must be dropped together with its tool result messages (orphaned tool results confuse the LLM)

This is implemented in `_trim_messages()` in both the AI Agent and AI Chat Service.

---

# Part 2: System Architecture

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Routes (SSE)                        │
├──────────────┬───────────────────────────────────────────────┤
│              │                                                 │
│  AI Chat Service                  Execution Service            │
│  (sidebar assistant)              (workflow runner)             │
│              │                         │                       │
│  ┌───────────▼──────────┐    ┌────────▼────────┐             │
│  │  Agent Loop           │    │  DAG BFS Queue   │             │
│  │  (workflow generator) │    │                  │             │
│  │  tools:               │    │  ┌────────────┐ │             │
│  │   get_catalog         │    │  │ AI Agent   │ │             │
│  │   validate_workflow   │    │  │ Node       │ │             │
│  │   save_workflow       │    │  │            │ │             │
│  │   execute_workflow    │    │  │ Agent Loop │ │             │
│  │   ...                 │    │  │ ┌────────┐│ │             │
│  └───────────┬──────────┘    │  │ │Sub-Agt.││ │             │
│              │                │  │ └────────┘│ │             │
│              │                │  └────────────┘ │             │
│              │                └────────┬────────┘             │
│              └──────────┬─────────────┘                       │
│                         ▼                                     │
│                ┌─────────────────┐                            │
│                │   call_llm()     │  Unified LLM Provider     │
│                ├─────┬─────┬─────┤                            │
│                │Gemni│OpenAI│Claud│                            │
│                │     │Llama │  e  │                            │
│                └─────┴─────┴─────┘                            │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                   │
│  │ Memory   │  │ Tools    │  │ Models   │  ← Subnodes        │
│  │ Subnodes │  │ Subnodes │  │ Subnodes │                    │
│  └────┬─────┘  └──────────┘  └──────────┘                    │
│       ▼                                                       │
│  agent_memory.db                                              │
└──────────────────────────────────────────────────────────────┘
```

**Two independent AI systems share one LLM provider:**

| System | What it does | Where |
|--------|-------------|-------|
| **AI Agent Node** | A workflow node users drag onto the canvas — runs agents with tools | `nodes/ai/ai_agent.py` |
| **AI Chat Service** | Sidebar assistant that builds entire workflows conversationally | `services/ai_chat_service.py` |

---

## Project Structure

```
src/
├── engine/                     # THE CORE — execution infrastructure
│   ├── llm_provider.py         # Unified LLM API (4 providers behind 1 function)
│   ├── workflow_runner.py       # DAG execution engine (BFS queue)
│   ├── expression_engine.py     # Safe {{ }} template evaluation
│   ├── types.py                 # Every data structure in the system
│   └── node_registry.py        # Plugin registration
│
├── nodes/                       # ALL NODE IMPLEMENTATIONS
│   ├── base.py                  # BaseNode abstract class
│   ├── ai/
│   │   ├── ai_agent.py         # Agent loop + sub-agents
│   │   └── llm_chat.py         # Simple single-turn LLM
│   ├── subnodes/
│   │   ├── base_subnode.py      # BaseSubnode (config-only, no execution)
│   │   ├── models/llm_model.py  # Model override configuration
│   │   ├── memory/              # 9 memory strategies
│   │   └── tools/               # 7 tool implementations
│   ├── triggers/                # Start, Webhook, Cron
│   ├── flow/                    # If, Loop, Merge, Switch
│   ├── data/                    # Set, Code, CSV, JSON
│   ├── integrations/            # HTTP, Database, Email
│   └── output/                  # Output nodes
│
├── services/                    # BUSINESS LOGIC LAYER
│   ├── ai_chat_service.py       # Sidebar chat + workflow generator
│   ├── workflow_service.py      # CRUD
│   ├── execution_service.py     # Run management
│   └── node_service.py         # Node info for UI
│
├── utils/memory.py              # Shared: token counting, embeddings, summarization
├── routes/                      # FastAPI endpoints
├── schemas/                     # Pydantic models
├── repositories/                # Data access
├── db/                          # SQLAlchemy models
└── core/                        # Config, exceptions
```

---

## The LLM Provider: One Interface, Four Backends

**File:** `engine/llm_provider.py`

### Design Philosophy

Every LLM provider (Google, OpenAI, Anthropic) has a different SDK, different message formats, different tool calling conventions, and different response structures. The LLM provider layer exists to **hide all of that behind one function:**

```python
response = await call_llm(model="gemini-2.0-flash", messages=[...], tools=[...])
# response.text and/or response.tool_calls — same shape regardless of provider
```

**No LangChain, no LlamaIndex.** Direct SDK calls with manual format conversion. This is a deliberate tradeoff: more code to maintain, but zero framework lock-in, full control over every API parameter, and no hidden abstractions that break when providers change their APIs.

### How Routing Works

The `model` string prefix determines the backend:

| Prefix | Backend | SDK | Notes |
|--------|---------|-----|-------|
| `gemini-*` | Google Vertex AI | `google.genai` | Sync SDK, wrapped in `asyncio.to_thread` |
| `gpt-*`, `o1-*`, `o3-*` | OpenAI | `openai.AsyncOpenAI` | Native async |
| `claude-*` | Anthropic | `anthropic.AsyncAnthropic` | Native async |
| `meta/llama-*` | OpenAI-compatible proxy | `openai.AsyncOpenAI` | Custom base URL |

### The Format Translation Layer

Each provider has its own message format. The provider handles all translation:

**Gemini** requires `Content` objects with `Part`s. Role `"assistant"` becomes `"model"`. Tool results must be wrapped as `Part.from_function_response()`. System prompts go to a separate `system_instruction` parameter, not in the messages. The `_convert_messages_to_gemini_content()` function handles all of this.

**Anthropic** requires system prompts passed as a separate `system` parameter. Tool calls are `tool_use` content blocks. Tool results are `tool_result` content blocks inside `user` role messages. The `_convert_messages_to_anthropic()` function handles this.

**OpenAI** is the de facto standard format — messages are used as-is. Tool calls use `function` objects with string-encoded JSON arguments.

### Tool Schema Normalization

Tools can be defined as either dicts or Python callables. `_tool_to_schema()` normalizes both into `{name, description, parameters}`. For callables, `_function_to_schema()` extracts the schema from the function's docstring and type hints using `docstring_parser`.

Each backend then converts this normalized schema to its native format (Gemini's `FunctionDeclaration`, Anthropic's `input_schema`, OpenAI's `function.parameters`).

### Client Management

Clients are **lazy singletons** — created on first use, cached in `_clients: dict`. This avoids startup overhead for unused providers. The Gemini client has an auto-refresh mechanism (`_reset_gemini_client()`) that recreates the client on auth failures.

API keys are resolved via `_get_env(key)`: checks `WORKFLOW_{KEY}` → `{KEY}` → pydantic settings from `.env` file.

### Standardized Response

All backends return `LLMResponse(text, tool_calls)`. The `get_assistant_message()` method converts back to OpenAI format for appending to conversation history — this is the internal canonical format.

---

## The Workflow Engine: DAGs, Queues, and BFS

**File:** `engine/workflow_runner.py`

### Design Philosophy

A workflow is a **directed acyclic graph (DAG)** of nodes connected by edges. The engine's job is to execute nodes in dependency order — a node runs only after all its upstream nodes have produced output.

The chosen algorithm is **breadth-first search (BFS) with a job queue**, which naturally handles:
- Parallel execution of independent nodes (same BFS layer)
- Sequential dependencies (downstream nodes wait for upstream)
- Multi-input nodes (like Merge) that need data from several sources

### The Execution Loop

```python
queue = [start_node_job]

while queue:
    current_batch = queue[:]   # Snapshot current layer
    queue.clear()

    # Run all jobs in this layer concurrently
    await asyncio.gather(*[process_job(job) for job in current_batch])
    # Each process_job may enqueue new downstream jobs
```

Each BFS layer represents nodes that can run in parallel. When a node finishes, it queues its downstream neighbors for the next layer.

### Job Processing Pipeline

For each job, `_process_job()` follows a fixed pipeline:

1. **Skip subnodes** — subnodes provide configuration, they don't execute in the graph
2. **Multi-input collection** — for Merge-type nodes, buffer inputs and wait for all connections
3. **Pinned data shortcut** — if node has pinned test data, use that instead of executing
4. **Expression resolution** — evaluate `{{ }}` templates in node parameters (but leave `$json` unresolved for per-item evaluation by the node itself)
5. **Subnode resolution** — discover connected subnodes, validate slot compatibility, call `get_config()` on each
6. **Execute with retry** — run the node with configurable retry count and delay
7. **Error handling** — `WorkflowStopSignal` for graceful stop, `continue_on_fail` for error passthrough, `NO_OUTPUT` propagation for dead branches
8. **Queue downstream** — find outgoing connections and enqueue target nodes

### The Subnode Resolution System

This is one of the more intricate parts. When a node like AI Agent has subnode slots (model, memory, tools), the runner must:

1. Find all `connection_type="subnode"` edges targeting this node
2. For each subnode connection:
   - Look up the subnode's `NodeDefinition`
   - Validate: is it actually a subnode? Does its `subnode_type` match the slot's `slot_type`?
   - Check multiplicity: does the slot allow multiple? (tools: yes, model: no)
   - Resolve expressions in the subnode's own parameters
   - Call `subnode_instance.get_config(resolved_def)` to get the config dict
3. Return `SubnodeContext(models=[...], memory=[...], tools=[...])` to the parent node

This design means the **parent node never knows how subnodes are implemented** — it just receives config dicts through a standardized interface.

### Multi-Input Nodes and Dead Branches

Merge nodes wait for input from all connections. But what if one branch has an error or produces no output? The `NO_OUTPUT_SIGNAL` mechanism handles this: when a node fails, it propagates `NO_OUTPUT` signals downstream. Multi-input nodes count these signals as "received" so they don't wait forever for dead branches.

### Subworkflow Execution

`run_subworkflow()` creates a child `ExecutionContext` that inherits the parent's HTTP client, workflow repository, and event callback — but has its own node states. Execution depth increments and is checked against `max_execution_depth` (10) to prevent infinite recursion through mutually-calling workflows.

---

## The Expression Engine: Safe User Code

**File:** `engine/expression_engine.py`

### Design Philosophy

Users write expressions like `{{ $json.price * 1.1 }}` in node parameters. These are essentially user-supplied code that runs server-side. The expression engine must be:

1. **Safe** — cannot access the filesystem, network, or Python internals
2. **Expressive** — supports field access, math, string operations, cross-node references
3. **Python-style** — NOT JavaScript (despite n8n using JS). This is explicit and enforced.

### How It Works

The engine uses `simpleeval` — a safe expression evaluator that whitelists allowed operations. No `eval()`, no `exec()`, no `ast.literal_eval()`.

The transformation pipeline:
1. Find all `{{ expression }}` blocks in a string
2. Transform n8n-style syntax to Python: `$json.field` → chained `.get()` calls, `$node["Name"].json.x` → variable lookups, `false` → `False`
3. Build an evaluation context with node states flattened as variables
4. Evaluate via `simpleeval.EvalWithCompoundTypes`

### Why Python and Not JavaScript?

n8n uses JavaScript for expressions. This codebase deliberately chose Python because:
- The backend is Python — no need for a JS runtime (V8/Node) just for expressions
- `simpleeval` provides a safe Python subset — sandboxing JS server-side is harder
- The expression function library (`join()`, `first()`, `keys()`, etc.) follows Python conventions

---

## The Node System: Plugin Architecture

**File:** `nodes/base.py`, `engine/node_registry.py`

### Design Philosophy

Every node — from a simple "Set a variable" to a complex AI Agent — shares the same interface:

```python
class MyNode(BaseNode):
    node_description = NodeTypeDescription(
        name="MyNode",
        inputs=[...],
        outputs=[...],
        properties=[...],   # Configurable parameters shown in UI
    )

    async def execute(self, context, node_definition, input_data) -> NodeExecutionResult:
        # Do work, return output
        return self.output([NodeData(json={"result": "..."})])
```

This is a **plugin architecture**. The engine doesn't know what nodes exist at compile time. At startup, `register_all_nodes()` imports and registers every node class. New nodes are added by:
1. Writing a class that extends `BaseNode`
2. Adding `node_description` with inputs/outputs/properties
3. Registering it in `register_all_nodes()`

### The Subnode Pattern: Configuration Without Execution

Subnodes are a special kind of node that **don't execute in the DAG flow**. Instead, they provide configuration to a parent node:

```python
class CodeToolNode(BaseSubnode):
    def get_config(self, node_definition) -> dict:
        return {
            "name": "run_code",
            "description": "Execute Python code",
            "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}},
            "execute": _execute_code,   # The actual function
        }
```

**Why not just have parameters on the parent?** Because subnodes are **composable**. A user can:
- Connect different memory strategies to the same agent without changing the agent
- Connect multiple tools — each is a separate visual node on the canvas
- Swap models by reconnecting a different model subnode
- Reuse the same tool subnode across multiple agents

This is the [Strategy Pattern](https://en.wikipedia.org/wiki/Strategy_pattern) implemented visually.

### NodeTypeDescription: Schema-Driven UI

Each node declares its full schema — inputs, outputs, configurable properties with types/defaults/options. The frontend reads this to render the configuration panel. The AI Chat Service reads it to know what nodes are available and how to configure them.

---

# Part 3: AI Agent Design

## The AI Agent Node: Anatomy of an Agent

**File:** `nodes/ai/ai_agent.py`

### Design Philosophy

The AI Agent is a **thin orchestration layer** around `call_llm()`. It manages:
- Conversation history (building the messages list)
- Tool discovery and execution
- Context window limits
- Error recovery
- Event emission for the UI

The actual decision-making — what to do, which tool to call, when to stop — is entirely the LLM's job.

### The Agent Loop in Detail

```
┌─ Build initial messages ──────────────────────────────────┐
│  [system_prompt]                                           │
│  [chat_history from memory: user/assistant turns...]       │
│  [task + input data as user message]                       │
└───────────────────────────────┬────────────────────────────┘
                                ▼
┌─ Iteration Loop ──────────────────────────────────────────┐
│                                                            │
│  1. Trim messages if over token budget                     │
│  2. call_llm(model, messages, tools)                       │
│  3. Response has tool_calls?                               │
│     │                                                      │
│     ├─ YES:                                                │
│     │  ├─ Emit AGENT_THINKING event (if text too)          │
│     │  ├─ Append assistant message to history              │
│     │  ├─ Execute ALL tools in parallel                    │
│     │  ├─ Append each tool result to history               │
│     │  ├─ Emit AGENT_TOOL_CALL + AGENT_TOOL_RESULT events  │
│     │  ├─ Check circuit breaker (3 consecutive failures?)  │
│     │  └─ Continue loop                                    │
│     │                                                      │
│     └─ NO (final text answer):                             │
│        ├─ If outputSchema: do second pass for JSON format  │
│        └─ Return {response, toolCalls, iterations}         │
│                                                            │
│  Stop conditions:                                          │
│  - LLM returns text without tool calls (natural end)       │
│  - Max iterations reached (forced end)                     │
│  - Circuit breaker: 3+ consecutive tool failures           │
└────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ Post-loop ──────────────────────────────────────────────┐
│  Save user task + agent response to memory (if connected)  │
│  Return NodeData with response, toolCalls, iterations      │
└───────────────────────────────────────────────────────────┘
```

### Why Parallel Tool Execution?

When the LLM returns multiple tool calls in one response (e.g., "search for X" and "search for Y" simultaneously), they're independent. Executing them via `asyncio.gather` means an agent that needs to make 3 API calls does them concurrently, not sequentially. This can cut iteration time dramatically.

---

## Tool Resolution: Where Do Tools Come From?

Tools are resolved from **three sources in priority order**:

```
Priority 1: Connected tool subnodes (visual canvas connections)
    ↓ If none found, check:
Priority 2: Runtime tools in input_data._tools (dynamically injected by upstream nodes)
    ↓ If none found, check:
Priority 3: Inline parameter config (manually defined in agent properties)
```

**Why three sources?**

1. **Subnodes** (Priority 1) — the primary mechanism. Users visually connect tool nodes on the canvas. This is the composable, visual approach.

2. **Runtime tools** (Priority 2) — enables dynamic patterns. A preceding node can discover available APIs at runtime and pass them as tools. The agent gets tools that didn't exist at design time.

3. **Inline config** (Priority 3) — fallback for simple cases where you just need a quick tool without creating a subnode. Also used by the AI Chat Service's generator.

The priority means subnodes always win over inline config. This prevents confusion when both are present.

---

## The Sub-Agent Pattern: Agents Spawning Agents

### Why Sub-Agents?

Some tasks naturally decompose. Given "research 3 companies and compare them," an agent could:
- Spawn 3 sub-agents in parallel (one per company)
- Each sub-agent independently researches its company
- Parent agent collects all 3 results and writes the comparison

Without sub-agents, the parent would do everything sequentially. With parallel sub-agents, the three research tasks happen concurrently.

### How It Works

When `enableSubAgents=True`, two special tools are added to the agent's tool set:

- **`spawn_agent`** — run a child agent with its own task, model, system prompt, and tool subset
- **`spawn_agents_parallel`** — run multiple child agents concurrently via `asyncio.gather`

The LLM decides when to delegate. It sees these tools alongside its other tools and chooses whether to do the work itself or spawn helpers.

### The AgentContext: Controlling Recursion

```python
@dataclass
class AgentContext:
    agent_depth: int = 0          # How deep are we?
    max_agent_depth: int = 3      # How deep can we go?
    inheritable_tools: list       # Tools children can use
    allow_recursive_spawn: bool   # Can children spawn grandchildren?
```

**Recursion safety mechanisms:**

1. **Depth tracking**: Each spawn increments `agent_depth`. When it reaches `max_agent_depth`, spawn calls return an error.

2. **Spawn tool exclusion**: The `spawn_agent` and `spawn_agents_parallel` tools are stripped from `inheritable_tools`. Children get the parent's real tools but NOT the spawn tools — unless `allow_recursive_spawn=True`, in which case spawn tools are re-added to the child's tool set.

3. **Tool filtering**: The parent can specify which tools a child inherits by name. This prevents a "web search" child from accessing a "send email" tool.

4. **Default depth of 3**: Parent → Child → Grandchild → Great-Grandchild. Deep enough for most decompositions, shallow enough to prevent runaway costs.

---

## Structured Output: Forcing JSON from Free Text

Some use cases need the agent to return valid JSON matching a specific schema (e.g., extracting structured data from text). The `outputSchema` parameter enables this.

### The Two-Pass Approach

Rather than constraining the agent's *thinking* to JSON (which degrades reasoning quality), a two-pass approach is used:

1. **Pass 1 (normal):** Agent runs with tools normally, produces a free-text response
2. **Pass 2 (formatting):** The free-text response is appended, then a new user message asks "format your answer as JSON matching this schema." A second LLM call with `response_format={"type": "json_object", "schema": ...}` produces clean JSON.

**Why two passes?** LLMs reason better in free text. Forcing JSON from the start would mean the agent's thinking (tool decisions, intermediate reasoning) happens in JSON, which is awkward and error-prone. The two-pass approach gives the LLM freedom to think naturally, then formats the final answer.

---

## Safety Systems: Preventing Runaway Agents

Agents are autonomous — they decide what to do. This creates risks. The system has multiple safety layers:

| Safety Mechanism | What It Prevents | Default |
|-----------------|-------------------|---------|
| **Max iterations** | Infinite tool-calling loops | 10 |
| **Circuit breaker** | Agent stuck in error loop (3 consecutive failures → abort) | 3 |
| **Context trimming** | Token limit overflow | 120K tokens |
| **Recursion depth** | Infinite sub-agent spawning | 3 levels |
| **Spawn tool exclusion** | Sub-agents spawning sub-agents unless explicitly allowed | On by default |
| **Tool sandboxing** | CodeTool: restricted globals, no filesystem/network. HttpRequestTool: SSRF protection. | Always on |
| **Execution timeout** | CodeTool: 60 second timeout per execution | 60s |
| **Workflow iteration limit** | Infinite workflow loops | 1000 |
| **Subworkflow depth** | Mutually recursive workflows | 10 |

---

# Part 4: Memory Architecture

## Why Memory Matters

LLMs are stateless. Without memory, every conversation starts from scratch. An AI assistant that can't remember your name from 5 messages ago is useless.

But memory isn't free:
- **More history = more tokens = more cost and latency**
- **Too much history = exceeds context window**
- **Wrong history = irrelevant context that confuses the LLM**

The memory system exists to answer: **"Given limited space, what past information is most useful for the current conversation?"**

Different strategies make different tradeoffs along three axes:
- **Completeness** — how much history is preserved?
- **Cost** — how many tokens are used?
- **Relevance** — is the included history actually useful?

## The Memory Interface Contract

Every memory strategy, regardless of implementation, returns the same interface:

```python
{
    "type": "buffer",             # Strategy name
    "sessionId": "user_123",      # Groups conversations
    "getHistory": callable,       # () -> [{role: "user", content: "..."}, ...]
    "addMessage": callable,       # (role, content) -> None
    "clearHistory": callable,     # () -> None
    "getHistoryText": callable,   # () -> "User: ...\nAssistant: ..."
}
```

The AI Agent calls `getHistory()` before its loop and `addMessage()` after. It never knows which strategy is behind the interface. This is the Strategy Pattern — the agent is decoupled from the memory implementation.

**Session IDs** enable multi-tenant memory. Different users or conversation threads use different session IDs, so their histories don't mix.

## Memory Strategies: A Spectrum of Tradeoffs

### Buffer Memory — "Keep the last N messages"

**File:** `subnodes/memory/buffer_memory.py`

The simplest strategy. Stores messages in a FIFO buffer and returns the last N.

```
History: [msg1, msg2, msg3, msg4, msg5, msg6, msg7]
maxMessages: 5
getHistory() → [msg3, msg4, msg5, msg6, msg7]  // oldest 2 dropped
```

| Axis | Rating | Why |
|------|--------|-----|
| Completeness | Low | Everything before the window is gone forever |
| Cost | Low & predictable | Always exactly N messages |
| Relevance | Medium | Recent messages are usually relevant, but not always |

**Storage:** In-memory dict OR SQLite (`buffer_messages` table). Configurable per instance.

**When to use:** Simple chatbots, testing, short conversations where early context doesn't matter.

### Summary Memory — "Summarize old messages with an LLM"

**File:** `subnodes/memory/summary_memory.py`

When history exceeds a threshold, calls an LLM to **compress old messages into a summary**. The agent sees `[summary] + [recent N messages]`.

```
After 15+ messages:
  [Summary: "User asked about Python, discussed lists, wanted sorting help"]
  [recent message 1]
  [recent message 2]
  [recent message 3]
  [recent message 4]
  [recent message 5]
```

The summarization happens inside `_perform_summarization()`: it takes all messages except the last N recent ones, calls `call_llm_for_summary()` with a "summarize concisely" prompt, deletes the old messages from storage, and stores the summary.

| Axis | Rating | Why |
|------|--------|-----|
| Completeness | Medium | Key points preserved in summary, details lost |
| Cost | Medium | Summary tokens + recent messages tokens + LLM call to summarize |
| Relevance | Medium-High | Summary captures themes; recent messages provide detail |

**When to use:** Long conversations where you need to remember early context but can't afford to keep everything.

### Progressive Summary Memory — "Continuously update a rolling summary"

**File:** `subnodes/memory/progressive_summary_memory.py`

Most aggressive compression. Instead of waiting for a threshold, updates a rolling summary every N messages.

```
Every 2 messages:
  Old summary + new messages → LLM → updated summary
```

The key insight is the `previous_summary` parameter in `call_llm_for_summary()`. The LLM is told: "Here's what you previously summarized. Here are new messages. Update the summary." This is **incremental** — it never re-summarizes from scratch.

| Axis | Rating | Why |
|------|--------|-----|
| Completeness | Low-Medium | Aggressive compression loses detail |
| Cost | Low tokens, higher LLM calls | Small summary, but LLM called frequently |
| Relevance | Medium | Summary captures main threads, loses nuance |

**When to use:** Very long-running agents, task managers, agents processing hundreds of messages.

### Vector Memory — "Retrieve semantically relevant messages"

**File:** `subnodes/memory/vector_memory.py`

Stores every message with its embedding vector. At retrieval time, embeds the current query, finds the most similar past messages via cosine similarity, and returns those + a few recent messages.

```
Query: "What did we decide about the database?"
→ Embed query
→ Compare against all stored message embeddings
→ Return top-K most similar + N most recent
```

This fundamentally changes the memory model: instead of "remember recent things," it's "remember relevant things."

| Axis | Rating | Why |
|------|--------|-----|
| Completeness | High (stores everything) | Nothing is deleted, just not always retrieved |
| Cost | Medium (top-K) + embedding costs | Only retrieves what's relevant, but must embed every message |
| Relevance | Highest | Retrieved history is semantically matched to current query |

**When to use:** FAQ agents, knowledge-heavy assistants, agents that need to recall specific past interactions from a very long history.

### Entity Memory — "Track named entities across conversations"

**File:** `subnodes/memory/entity_memory.py`

Uses an LLM to **extract entities** (people, places, organizations, concepts) from each message and stores them in a structured table. Returns entity summaries alongside recent messages.

```
Message: "John from engineering said the deadline for Project Alpha is Friday"
→ Extracts: {name: "John", type: "person", description: "colleague in engineering"}
→ Extracts: {name: "Project Alpha", type: "concept", description: "project due Friday"}
```

On retrieval, the agent sees: "Known entities: John (person, colleague in engineering), Project Alpha (concept, project due Friday)" as context before the recent messages.

| Axis | Rating | Why |
|------|--------|-----|
| Completeness | Medium | Entities tracked, but raw conversation details lost |
| Cost | Medium | Entity context + recent messages + LLM extraction calls |
| Relevance | High for entity-centric tasks | Remembers WHO/WHAT, not necessarily WHEN/HOW |

**When to use:** Customer support agents, project management, agents that track many named entities across long conversations.

### Knowledge Graph Memory — "Store relationships in a graph database"

**File:** `subnodes/memory/knowledge_graph_memory.py`

The most sophisticated strategy. Uses an LLM to extract **subject-predicate-object relationships** and stores them in Neo4j.

```
Message: "Alice manages the marketing team and reports to Bob"
→ Extracts: ("Alice", "manages", "marketing team"), ("Alice", "reports_to", "Bob")
→ Stored as Neo4j graph: (Alice)-[:MANAGES]->(marketing team), (Alice)-[:REPORTS_TO]->(Bob)
```

On retrieval, queries the graph for relevant relationships and formats them as context.

| Axis | Rating | Why |
|------|--------|-----|
| Completeness | High for relationships | Relationship structure preserved, free-form details lost |
| Cost | High | LLM extraction + Neo4j operations + recent messages |
| Relevance | Highest for relational reasoning | Graph structure enables complex queries |

**When to use:** Complex multi-entity scenarios, organizational knowledge, agents that need to reason about relationships between concepts.

## Choosing the Right Memory Strategy

```
Do you need memory at all?
├─ No: Single-turn tasks → No memory subnode
└─ Yes:
   │
   Is the conversation short (<20 messages)?
   ├─ Yes → Buffer Memory (simplest, cheapest)
   └─ No:
      │
      Do you need to recall SPECIFIC past messages?
      ├─ Yes → Vector Memory (semantic search)
      └─ No:
         │
         Do you track named entities (people, projects, etc.)?
         ├─ Yes:
         │  │
         │  Need to reason about relationships between entities?
         │  ├─ Yes → Knowledge Graph Memory (Neo4j required)
         │  └─ No  → Entity Memory
         └─ No:
            │
            How aggressive should compression be?
            ├─ Moderate → Summary Memory (summarize once when threshold hit)
            └─ Aggressive → Progressive Summary Memory (summarize every N messages)
```

---

# Part 5: The AI Chat Service

## Two AI Systems, One Codebase

| | AI Agent Node | AI Chat Service |
|---|---|---|
| **Purpose** | Execute user-designed agent workflows | Help users build workflows via chat |
| **Triggered by** | Workflow execution | User typing in sidebar |
| **Tools** | User-configured (code, HTTP, etc.) | Internal (get_catalog, validate, save...) |
| **Model** | User-configurable | gemini-2.0-flash (chat) / gemini-2.5-pro (generator) |
| **Memory** | User-configured subnode | Conversation history from frontend |
| **Output** | Data flowing to next node | SSE events rendered in chat UI |

They're architecturally similar (both are agent loops calling `call_llm`) but serve completely different purposes.

## The Workflow Generator: An Agent That Builds Agents

**File:** `services/ai_chat_service.py`

When a user says "create a workflow that fetches weather data and sends a Slack message," the generator kicks in.

### Intent Detection

First, a fast LLM call classifies the user's message as "GENERATE" or "CHAT":
- Uses gemini-2.0-flash, temperature 0, max 8 tokens
- Falls back to keyword matching if the LLM call fails
- Keywords: "create a workflow", "build a workflow", "automate", etc.

### The Generator's Tool Set

The generator has 9 tools — all internal, operating on the platform itself:

| Tool | Purpose | Why it exists |
|------|---------|---------------|
| `get_node_catalog` | Browse available nodes | LLM needs to know what's possible |
| `get_node_schema(s)` | Get exact parameter definitions | LLM must not guess parameter names or types |
| `validate_workflow` | Check structural correctness | Catches errors before saving |
| `save_workflow` | Persist to database | Creates the actual workflow |
| `execute_workflow` | Test-run it | Verifies it actually works |
| `update_workflow` | Fix errors after testing | Enables the fix loop |
| `delete_workflow` | Cleanup on failure | Prevents broken workflows from persisting |
| `get_workflow` | Retrieve saved workflow | Needed for inspection and modification |

### The Lifecycle: Discover → Design → Validate → Save → Test → Fix

The generator's system prompt enforces a strict 6-step lifecycle:

1. **Discover** — Call `get_node_catalog()` to see what's available, then `get_node_schemas()` for the specific nodes needed. **The LLM must never guess parameter names.**

2. **Design** — Construct the workflow JSON: nodes with names, types, parameters, positions; connections between them.

3. **Validate** — Call `validate_workflow()` which performs deep structural validation: node types exist, names unique, connections valid, subnode slots correct, required slots filled.

4. **Save** — Call `save_workflow()` (which auto-validates again as a safety net).

5. **Test** — Call `execute_workflow()` to actually run it. This is where runtime errors surface.

6. **Fix** — If the test fails, analyze the error, call `update_workflow()` with corrections, then loop back to Validate. Max 3 fix attempts.

### The Validation Engine

`_tool_validate_workflow()` is remarkably thorough:

- Nodes: at least one exists, no duplicate names, all types exist in registry, Start node present
- Normal connections: source/target exist, output/input ports exist on those node types, subnodes can't be endpoints of normal connections
- Subnode connections: source is actually a subnode, target has the specified slot, subnode's `provides_to_slot` matches the connected slot, slot type compatibility (model/memory/tool)
- Required slots: checks that all required subnode slots are filled

This catches most LLM mistakes before they reach the execution engine.

### Context Trimming with Pinned Messages

The generator's context management is more sophisticated than the agent's. It has **pinned tools**: `{save_workflow, execute_workflow, get_workflow}`.

When trimming for context window, results from these tools are **never dropped** — even if they're old. Why? Because they contain critical state:
- `save_workflow` result has the `workflow_id` needed for updates/execution
- `execute_workflow` result has the error messages needed for debugging
- `get_workflow` result has the current state needed for modifications

Without pinning, the LLM might "forget" the workflow ID or the error it's trying to fix.

### How the Generated Workflow Reaches the Frontend

After the generator finishes, if a workflow was saved, it emits a special `operations` SSE event containing the full workflow structure (nodes + connections). The frontend receives this event and renders the workflow on the canvas — the user sees the workflow materialize in real-time.

## Prompt Engineering: How the Generator Stays on Track

The generator system prompt is carefully constructed to prevent common LLM mistakes:

**Expression syntax rules:** The engine uses Python-based expressions (`{{ $json.field }}`), NOT JavaScript. The prompt explicitly lists:
- Supported syntax: `$json.field`, `$node["Name"].json.field`, `$env.VAR`, arithmetic, Python-style operators (`and`/`or`/`not`)
- Available functions: `join()`, `length()`, `upper()`, `lower()`, `first()`, `last()`, `keys()`, etc.
- **Explicitly FORBIDDEN:** `.map()`, `.filter()`, `.reduce()`, arrow functions (`=>`), template literals, `JSON.stringify()`, `Math.*` — all JavaScript patterns that LLMs default to

Without this explicit prohibition, LLMs trained on JavaScript-heavy codebases will generate JS expressions that silently fail in the Python expression engine.

**Lifecycle enforcement:** The prompt mandates "you MUST validate before saving" and "you MUST test after saving." Without this, LLMs tend to save and declare success without testing.

---

# Part 6: Reference

## Key Design Patterns Summary

| Pattern | Where Used | Why |
|---------|-----------|-----|
| **Strategy Pattern** | Memory subnodes, tool subnodes, model subnodes | Swap implementations without changing the consumer (AI Agent doesn't know if memory is Buffer or Vector) |
| **Plugin Architecture** | Node registry | Add new node types without modifying the engine |
| **Adapter Pattern** | LLM provider format converters | Normalize 4 different APIs behind one interface |
| **Singleton (Lazy)** | LLM client instances | One client per provider, created on demand |
| **BFS Queue** | Workflow execution | Natural parallel execution of independent nodes |
| **Circuit Breaker** | Agent tool loop | Prevent infinite error loops (3 consecutive failures → abort) |
| **Decorator/Subnode** | Subnodes attached to parent nodes | Composable configuration without modifying the parent |
| **Observer** | Execution event system | Decouple execution from UI updates (engine emits events, routes stream them) |

## Data Types Quick Reference

**File:** `engine/types.py`

### Core Data Flow
| Type | Purpose |
|------|---------|
| `NodeData(json, binary)` | Single item passed between nodes |
| `NodeExecutionResult(outputs)` | Multi-output result from node execution |
| `ExecutionJob(node_name, input_data, source_node, source_output, run_index)` | Job in BFS queue |

### Workflow Definition
| Type | Purpose |
|------|---------|
| `Workflow(name, nodes, connections, settings)` | Complete DAG |
| `NodeDefinition(name, type, parameters, position, retry_on_fail, continue_on_fail)` | Node in DAG |
| `Connection(source_node, target_node, source_output, target_input, connection_type, slot_name)` | Edge (normal or subnode) |

### Execution State
| Type | Purpose |
|------|---------|
| `ExecutionContext(workflow, execution_id, node_states, pending_inputs, errors, execution_depth, on_event, ...)` | Full execution state |
| `ExecutionError(node_name, error, timestamp)` | Error record |
| `ExecutionEvent(type, execution_id, node_name, data, error, progress)` | Real-time event for SSE |

### Subnode System
| Type | Purpose |
|------|---------|
| `SubnodeSlotDefinition(name, slot_type, required, multiple)` | What a node accepts |
| `ResolvedSubnode(node_name, node_type, slot_name, config)` | Resolved subnode ready to use |
| `SubnodeContext(models, memory, tools)` | All resolved subnodes for a parent |

### Events
| Event Type | When Emitted |
|-----------|-------------|
| `EXECUTION_START` / `EXECUTION_COMPLETE` | Workflow begins / ends |
| `NODE_START` / `NODE_COMPLETE` / `NODE_ERROR` | Node lifecycle |
| `AGENT_THINKING` | Agent LLM returned reasoning text alongside tool calls |
| `AGENT_TOOL_CALL` | Agent is calling a tool (name + args) |
| `AGENT_TOOL_RESULT` | Tool returned a result |

## File Map

| File | What it does | Key functions |
|------|-------------|---------------|
| `engine/llm_provider.py` | Unified multi-LLM API | `call_llm()`, `get_embedding()`, `_call_gemini_vertex()`, `_call_openai_compat()`, `_call_anthropic()` |
| `engine/workflow_runner.py` | DAG execution engine | `run()`, `run_subworkflow()`, `_process_job()`, `_resolve_subnodes()`, `_queue_next_nodes()` |
| `engine/expression_engine.py` | Safe template evaluation | `resolve()`, `_evaluate()`, `_transform_expression()` |
| `engine/types.py` | All data structures | `NodeData`, `ExecutionContext`, `Workflow`, `SubnodeContext`, ... |
| `engine/node_registry.py` | Plugin registration | `register()`, `get()`, `get_node_info_full()`, `register_all_nodes()` |
| `nodes/base.py` | Base node classes | `execute()`, `get_parameter()`, `output()` |
| `nodes/ai/ai_agent.py` | Agent with tool loop | `execute()`, `_run_agent_loop()`, `_execute_tool()`, `_handle_spawn_agent()`, `_trim_messages()` |
| `nodes/ai/llm_chat.py` | Single-turn LLM | `execute()`, `_call_llm()` |
| `nodes/subnodes/base_subnode.py` | Subnode base | `get_config()` (abstract) |
| `nodes/subnodes/models/llm_model.py` | Model config | `get_config()` → `{model, temperature, maxTokens}` |
| `nodes/subnodes/memory/*.py` | 9 memory strategies | `get_config()` → `{getHistory, addMessage, clearHistory}` |
| `nodes/subnodes/tools/*.py` | 7 tool implementations | `get_config()` → `{name, description, input_schema, execute}` |
| `services/ai_chat_service.py` | Chat + workflow generator | `stream_chat()`, `_stream_generate()`, `_run_agent_loop()`, `_tool_validate_workflow()` |
| `utils/memory.py` | Shared utilities | `call_llm_for_summary()`, `extract_entities()`, `extract_relationships()`, `get_db_connection()`, `cosine_similarity()` |
