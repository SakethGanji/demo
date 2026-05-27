"""Seed database with demo workflows."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

from sqlalchemy import select, delete, text

from .session import async_session_factory, init_db
from .models import WorkflowModel, WorkflowVersionModel

logger = logging.getLogger(__name__)


def generate_workflow_id(name: str) -> str:
    """Generate a unique workflow ID."""
    from ..utils.ids import workflow_id
    return workflow_id()


# ── Prompt Evaluator ─────────────────────────────────────────────────
# Webhook → Set → AIAgent → RespondToWebhook
#
# Hub & spoke architecture:
#   - Orchestrator stores prompt in shared store (shared:prompt)
#   - 3 skill sub-agents read from shared store, write results back
#   - 1 dynamically spawned Simulator reads prompt + utterances from shared store
#   - Orchestrator reads all results from shared store for cross-referencing
#   - Data never re-enters LLM context — stays in shared store until recalled

_PROMPT_EVAL_SYSTEM_PROMPT = (
    "You are a Prompt Evaluation Orchestrator. A user has submitted a prompt "
    "(system prompt / instruction) along with test_utterances. Your job: dispatch "
    "4 parallel expert evaluations, collect results, cross-reference, and produce "
    "a comprehensive structured report.\n\n"
    "## Protocol\n"
    "1. **Store** the prompt and test_utterances in the SHARED store so all agents can access them:\n"
    "   - memory_store(key='shared:prompt', value=<the prompt text>)\n"
    "   - memory_store(key='shared:utterances', value=<the test utterances JSON>)\n\n"
    "2. **Dispatch ALL 4 evaluations in ONE turn** (they run in parallel):\n"
    "   a. delegate_to_skill('structural_linguist', task='Analyze the prompt stored in shared:prompt')\n"
    "   b. delegate_to_skill('context_auditor', task='Analyze the prompt stored in shared:prompt')\n"
    "   c. delegate_to_skill('domain_specialist', task='Analyze the prompt stored in shared:prompt')\n"
    "   d. delegate_to_skill('simulator', task='Simulate the prompt against the test utterances')\n\n"
    "3. **Recall results** from the shared store:\n"
    "   - memory_recall(key='shared:structural_linguist')\n"
    "   - memory_recall(key='shared:context_auditor')\n"
    "   - memory_recall(key='shared:domain_specialist')\n"
    "   - memory_recall(key='shared:simulation')\n\n"
    "4. **Cross-reference**: Compare simulation results against analyst findings. "
    "Where did theory predict issues that simulation confirmed? Where did "
    "simulation reveal issues analysts missed?\n\n"
    "5. **Write the final report** with ALL of the following sections:\n\n"
    "## Required Output Sections\n\n"
    "### scores\n"
    "- clarity (0-100): from structural_linguist\n"
    "- completeness (0-100): from context_auditor\n"
    "- precision (0-100): from domain_specialist\n"
    "- simulation_pass_rate (0-100): % of utterances that passed all checks\n"
    "- overall: clarity*0.2 + completeness*0.3 + precision*0.2 + simulation*0.3\n\n"
    "### structure_evaluation\n"
    "Evaluate the prompt's STRUCTURAL qualities:\n"
    "- format: 'structured' | 'semi-structured' | 'unstructured'\n"
    "- has_sections, has_bullet_points, has_numbered_steps, has_examples: bool\n"
    "- has_persona, has_output_format, has_constraints, has_edge_cases: bool\n"
    "- word_count, estimated_token_count: number\n"
    "- information_density: 'sparse' | 'moderate' | 'dense'\n"
    "- readability: 'easy' | 'moderate' | 'complex'\n"
    "- verdict: one-sentence assessment\n\n"
    "### analysis\n"
    "Include the FULL output from each analyst, not summaries:\n"
    "- structural_linguist: complete JSON from the skill\n"
    "- context_auditor: complete JSON from the skill\n"
    "- domain_specialist: complete JSON from the skill\n"
    "- theory_vs_practice: paragraph comparing analyst predictions vs simulation\n\n"
    "### simulation\n"
    "- utterance_results: array with utterance, output, on_topic, format_match, "
    "refusal, quality_rating (1-5), issues\n"
    "- pass_count, fail_count\n"
    "- overall_issues: array of patterns across all utterances\n\n"
    "### issues (master issue list)\n"
    "Deduplicated list across ALL analysts. Each issue:\n"
    "- issue, severity (critical|major|minor|suggestion), source, quote, fix\n\n"
    "### perfect_prompt\n"
    "Complete rewrite fixing EVERY issue. Apply CO-STAR, add structure, "
    "output format, constraints, edge cases. Must be substantially different.\n\n"
    "### summary\n"
    "2-3 sentence executive summary.\n\n"
    "## Rules\n"
    "- ALWAYS delegate — never evaluate the prompt yourself.\n"
    "- Dispatch ALL 4 evaluations in the SAME turn.\n"
    "- Use shared: prefix for all cross-agent data.\n"
    "- Include COMPLETE analyst outputs, not summaries.\n"
    "- The perfect_prompt must be a SUBSTANTIAL rewrite.\n"
    "- If any skill returns an error, include the error in the report and still produce the other sections."
)

_STRUCTURAL_LINGUIST_PROMPT = (
    "You are a Structural Linguist specializing in prompt engineering analysis.\n\n"
    "First, recall the prompt: memory_recall(key='shared:prompt').\n"
    "Then perform deep structural evaluation.\n\n"
    "## Analysis Checklist\n"
    "Evaluate EVERY item. For each, note pass/fail and quote the relevant text.\n\n"
    "1. **Format & Organization** — sections/headers? bullet points? numbered steps? "
    "hierarchical? logical flow? Rate: structured / semi-structured / wall-of-text\n"
    "2. **Grammar & Syntax** — errors, run-ons, fragments, inconsistent tense/voice\n"
    "3. **Ambiguity Detection** — vague quantifiers ('short', 'few', 'some', 'good'), "
    "unclear pronouns, multi-interpretation instructions\n"
    "4. **Negative vs Positive Constraints** — find 'don't do X', suggest 'do Y instead'\n"
    "5. **Lazy/Filler Words** — 'basically', 'really', 'just', 'stuff', 'things', 'etc'\n"
    "6. **Instruction Executability** — can each instruction be executed unambiguously? "
    "success criteria defined?\n"
    "7. **Length Assessment** — word count, token count, signal-to-noise ratio\n\n"
    "## Output\n"
    "Store your results: memory_store(key='shared:structural_linguist', value=<JSON>)\n"
    "Then return the same JSON.\n\n"
    "JSON format:\n"
    "{\n"
    '  "clarity_score": 0-100,\n'
    '  "format_rating": "structured|semi-structured|wall-of-text",\n'
    '  "word_count": number,\n'
    '  "signal_to_noise": "high|medium|low",\n'
    '  "issues": [{"issue": "...", "quote": "exact text", "severity": "critical|major|minor", "fix": "..."}],\n'
    '  "vague_terms": ["word1", "word2"],\n'
    '  "negative_constraints": [{"original": "don\'t...", "rewrite": "do..."}],\n'
    '  "lazy_words": ["word1"],\n'
    '  "strengths": ["what the prompt does well"],\n'
    '  "recommendation": "paragraph with specific improvements"\n'
    "}\n"
    "Be thorough. Quote exact text from the prompt for every issue."
)

_CONTEXT_AUDITOR_PROMPT = (
    "You are a Context Auditor who performs deep CO-STAR framework analysis.\n\n"
    "First, recall the prompt: memory_recall(key='shared:prompt').\n"
    "Then evaluate completeness with granular scoring.\n\n"
    "## CO-STAR Framework (score each 0 to 16.67, total = 100)\n\n"
    "**C - Context** (0-16.67): Background info, domain knowledge, situation\n"
    "**O - Objective** (0-16.67): Specific task, goal, measurable outcome\n"
    "**S - Style** (0-16.67): Writing style — formal, casual, technical\n"
    "**T - Tone** (0-16.67): Emotional tone — professional, friendly, empathetic\n"
    "**A - Audience** (0-16.67): Who the output is for\n"
    "**R - Response Format** (0-16.67): Expected format, length expectations\n\n"
    "Score 0 if absent, 8 if vague, 16.67 if specific.\n\n"
    "## Additional Checks\n"
    "Persona, Constraints, Examples, Edge cases, Error handling\n\n"
    "## Output\n"
    "Store your results: memory_store(key='shared:context_auditor', value=<JSON>)\n"
    "Then return the same JSON.\n\n"
    "JSON format:\n"
    "{\n"
    '  "completeness_score": 0-100,\n'
    '  "costar_breakdown": {"C": {"score": N, "status": "present|partial|missing", "found": "quoted text or null", "missing": "what to add"}, ...},\n'
    '  "missing_components": ["list"],\n'
    '  "has_persona": bool, "has_output_format": bool, "has_constraints": bool,\n'
    '  "has_examples": bool, "has_edge_case_handling": bool, "has_error_handling": bool,\n'
    '  "component_suggestions": [{"component": "C|O|S|T|A|R", "suggestion": "specific text"}],\n'
    '  "recommendation": "detailed paragraph"\n'
    "}\n"
    "Quote exact text for each 'found' field."
)

_DOMAIN_SPECIALIST_PROMPT = (
    "You are a Domain Specialist who evaluates prompts through domain-specific lenses.\n\n"
    "First, recall the prompt: memory_recall(key='shared:prompt').\n"
    "Then detect the domain and apply expert-level scrutiny.\n\n"
    "## Step 1: Domain Detection\n"
    "Classify: Code/Technical, Writing/Creative, Data/Analytics, Customer-facing, General\n"
    "State confidence: high / medium / low\n\n"
    "## Step 2: Domain Checklist\n"
    "### Code/Technical: stack specified? error handling? edge cases? security? performance? testing? style?\n"
    "### Writing/Creative: voice/tone? audience? structure? brand guidelines? CTA? length? SEO?\n"
    "### Customer-facing: escalation paths? tone consistency? forbidden topics? response templates?\n"
    "### General: task decomposition? success criteria? scope? input/output format?\n\n"
    "## Step 3: Risk Assessment\n"
    "Misinterpretation risks, missing guardrails, potential for harmful output\n\n"
    "## Output\n"
    "Store your results: memory_store(key='shared:domain_specialist', value=<JSON>)\n"
    "Then return the same JSON.\n\n"
    "JSON format:\n"
    "{\n"
    '  "precision_score": 0-100,\n'
    '  "detected_domain": "string", "domain_confidence": "high|medium|low",\n'
    '  "checklist": [{"item": "...", "status": "pass|fail", "detail": "..."}],\n'
    '  "domain_specific_issues": [{"issue": "...", "severity": "critical|major|minor", "fix": "..."}],\n'
    '  "missing_specifications": ["what to add"],\n'
    '  "risks": [{"risk": "...", "likelihood": "high|medium|low", "mitigation": "..."}],\n'
    '  "recommendation": "detailed paragraph"\n'
    "}"
)

_SIMULATOR_PROMPT = (
    "You are a Prompt Simulator. Your job is to test a system prompt by role-playing as an AI "
    "that has been given that prompt, then grading the results.\n\n"
    "## Protocol\n"
    "1. Recall the prompt: memory_recall(key='shared:prompt')\n"
    "2. Recall the test utterances: memory_recall(key='shared:utterances')\n"
    "3. For EACH test utterance:\n"
    "   a. Role-play as an AI with the system prompt and generate a realistic response\n"
    "   b. Grade the response:\n"
    "      - on_topic: did it stay within the prompt's scope?\n"
    "      - format_match: did it follow any specified output format?\n"
    "      - refusal: did it inappropriately refuse a valid request?\n"
    "      - quality_rating: 1-5 (1=terrible, 5=excellent)\n"
    "      - issues: list of problems found\n"
    "4. Store results: memory_store(key='shared:simulation', value=<JSON object>)\n\n"
    "## Output Format\n"
    "Store and return a JSON object (NOT an array):\n"
    "{\n"
    '  "utterance_results": [\n'
    '    {"utterance": "...", "output": "...", "on_topic": true/false, '
    '"format_match": true/false, "refusal": true/false, '
    '"quality_rating": 1-5, "issues": ["..."]}\n'
    "  ],\n"
    '  "pass_count": number,\n'
    '  "fail_count": number,\n'
    '  "overall_issues": ["patterns across all utterances"]\n'
    "}\n\n"
    "A test passes if on_topic=true AND format_match=true AND refusal=false.\n"
    "Be honest in your simulation — generate realistic outputs, not idealized ones."
)

_PROMPT_EVAL_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {
                "clarity": {"type": "number", "description": "0-100 from structural linguist"},
                "completeness": {"type": "number", "description": "0-100 from context auditor"},
                "precision": {"type": "number", "description": "0-100 from domain specialist"},
                "simulation_pass_rate": {"type": "number", "description": "0-100 pass rate"},
                "overall": {"type": "number", "description": "weighted average"},
            },
        },
        "structure_evaluation": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["structured", "semi-structured", "unstructured"]},
                "has_sections": {"type": "boolean"},
                "has_bullet_points": {"type": "boolean"},
                "has_numbered_steps": {"type": "boolean"},
                "has_examples": {"type": "boolean"},
                "has_persona": {"type": "boolean"},
                "has_output_format": {"type": "boolean"},
                "has_constraints": {"type": "boolean"},
                "has_edge_cases": {"type": "boolean"},
                "word_count": {"type": "number"},
                "estimated_token_count": {"type": "number"},
                "information_density": {"type": "string", "enum": ["sparse", "moderate", "dense"]},
                "readability": {"type": "string", "enum": ["easy", "moderate", "complex"]},
                "verdict": {"type": "string"},
            },
        },
        "analysis": {
            "type": "object",
            "properties": {
                "structural_linguist": {"type": "object", "description": "Full output from structural linguist skill"},
                "context_auditor": {"type": "object", "description": "Full output from context auditor skill"},
                "domain_specialist": {"type": "object", "description": "Full output from domain specialist skill"},
                "theory_vs_practice": {"type": "string"},
            },
        },
        "simulation": {
            "type": "object",
            "properties": {
                "utterance_results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "utterance": {"type": "string"},
                            "output": {"type": "string"},
                            "on_topic": {"type": "boolean"},
                            "format_match": {"type": "boolean"},
                            "refusal": {"type": "boolean"},
                            "quality_rating": {"type": "number"},
                            "issues": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "pass_count": {"type": "number"},
                "fail_count": {"type": "number"},
                "overall_issues": {"type": "array", "items": {"type": "string"}},
            },
        },
        "issues": {
            "type": "array",
            "description": "Deduplicated master issue list from all analysts",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "major", "minor", "suggestion"]},
                    "source": {"type": "string"},
                    "quote": {"type": "string"},
                    "fix": {"type": "string"},
                },
            },
        },
        "perfect_prompt": {"type": "string"},
        "summary": {"type": "string"},
    },
}, indent=2)


# ── PromptLab (minimal 9-node prompt optimizer) ──────────────────────
# Thin driver over analytics-service /prompt-lab/evaluate.
#
# Inputs (POST /webhook/promptlab/run JSON body):
#   {
#     "session_id":            "sess_my_run_1",         # required, idempotency key
#     "dataset_id":            "ds_xxx",                # required, from /prompt-lab/datasets
#     "intent_classes":        ["A","B",...],           # required
#     "prompt_system_seed":    "...",                   # required, iter 0 starting point
#     "prompt_user_template":  "Utterance: {utterance}", # required, never rewritten
#     "max_iterations":        5,                        # optional, default 5
#     "max_cost_usd":          1.0                       # optional, default 1.0
#   }
#
# Flow (Webhook -> Loop[{trendQuery -> decideAgent -> promptWriter
#                       -> evalCall -> stopCheck}] -> finalize):
#
#   trendQuery   reads last N runs for session_id from `prompt_runs`
#   decideAgent  AIAgent (gemini-2.5-pro) reads trend + has mongo_query tool;
#                outputs {action:"continue"|"stop", strategy:"...", reasoning:"..."}
#   promptWriter LLMChat (gemini-2.5-flash) rewrites prompt_system given the
#                strategy + the current best prompt
#   evalCall     POSTs the new prompt to /prompt-lab/evaluate (which persists
#                the run automatically when session_id is set)
#   stopCheck    routes early-exit to `finalize`; else loops back to mainLoop
#   finalize     reads the winning run from sessions.best_run_id and emits it
#                as the webhook response

_PROMPTLAB_WORKFLOW_ID = "wf_promptlab"

_PROMPTLAB_ANALYTICS_BASE = (
    "{{ $env.ANALYTICS_SERVICE_URL or 'http://localhost:8001' }}"
)

# Env-driven so the workflow runs against the same Mongo the rest of the
# stack uses (matches the standalone's PROMPTLAB_MONGO_URL convention).
# Fallback URL matches docker-compose.yml's admin:admin so a fresh `docker
# compose up` works without extra env setup.
_PROMPTLAB_MONGO_URL = (
    "{{ $env.WORKFLOW_MONGO_URL or 'mongodb://admin:admin@localhost:27017' }}"
)
_PROMPTLAB_MONGO_DB = "promptlab"
_PROMPTLAB_SESSIONS_COLLECTION = "sessions"
_PROMPTLAB_RUNS_COLLECTION = "prompt_runs"


# Schema cheatsheet for the decisionAgent's mongo_query tool. The schema
# matches what analytics-service /prompt-lab/evaluate persists.
_PROMPTLAB_QUERY_TOOL_DESCRIPTION = (
    "Run constrained Mongo find/aggregate queries against the PromptLab "
    "session's prior runs to diagnose what to change next. session_id is a "
    "MANDATORY top-level filter (enforced by the runner).\n"
    "\n"
    "Collections: prompt_runs, sessions.\n"
    "Operations: find (filter/projection/sort/limit) and aggregate (8 stages max).\n"
    "\n"
    "prompt_runs doc {\n"
    "  run_id, session_id, prompt_hash, model, strategy, parent_run_id,\n"
    "  stage,                    // 'smoke' | 'quick' | 'full' — filter on this!\n"
    "  prompt_system,            // HEAVY — project out unless rewriting\n"
    "  prompt_user_template,\n"
    "  response: {\n"
    "    dataset_id, prompt_hash, config,\n"
    "    metrics: {\n"
    "      overall:  { accuracy, accuracy_ci_95, macro_f1, weighted_f1,\n"
    "                  macro_precision, macro_recall,\n"
    "                  weighted_precision, weighted_recall,\n"
    "                  n_rows, n_failures, per_class, confusion_matrix },\n"
    "      by_split: { <SplitName>: { same shape } }\n"
    "    },\n"
    "    per_row: [ {row_id, split, inputs, gt, predicted, correct,\n"
    "                tokens_in, tokens_out, latency_ms, raw_text, error} ],  // HEAVY\n"
    "    failure_summary: { total, by_confusion_pair, by_split, top_confusion_pairs },\n"
    "    failures_sample: [ {row_id, split, inputs, gt, predicted, raw_text,\n"
    "                        error, confusion_pair} ],  // HEAVY\n"
    "    warnings: [ '...' ],\n"
    "    cost_usd, tokens_in, tokens_out,\n"
    "    latency_p50_ms, latency_p95_ms, latency_p99_ms,\n"
    "    cached, budget_spent_usd, budget_max_usd\n"
    "  },\n"
    "  created_at\n"
    "}\n"
    "\n"
    "sessions doc {\n"
    "  session_id, name, max_cost_usd, budget_spent_usd, n_runs,\n"
    "  best_run_id, best_macro_f1,    // both only update on stage='full'\n"
    "  status, created_at, last_accessed_at\n"
    "}\n"
    "\n"
    "STAGE FILTERING DISCIPLINE:\n"
    "  - Accuracy/macro_f1 comparisons across runs MUST filter stage='full'.\n"
    "    smoke (n=10) and quick (n=100) results are NOT comparable to full.\n"
    "  - Failure-pattern queries (confusion pairs, recurring failing inputs)\n"
    "    can use any stage — diagnostic signal is useful regardless of n.\n"
    "  - To see what the agent tried but the gates rejected, look at\n"
    "    smoke/quick runs (they persist with their stage label).\n"
    "\n"
    "Common queries:\n"
    "  # Last 5 FULL runs for fair comparison\n"
    "  { collection:'prompt_runs', operation:'find',\n"
    "    filter:{session_id:'<sess>', stage:'full'},\n"
    "    projection:{prompt_system:0,'response.failures_sample':0,'response.per_row':0},\n"
    "    sort:{created_at:-1}, limit:5 }\n"
    "\n"
    "  # All strategies tried in this session (any stage), for anti-repetition\n"
    "  { collection:'prompt_runs', operation:'find',\n"
    "    filter:{session_id:'<sess>'},\n"
    "    projection:{strategy:1, stage:1, 'response.metrics.overall.accuracy':1, _id:0},\n"
    "    sort:{created_at:-1}, limit:30 }\n"
    "\n"
    "  # Top confusion pairs across all FULL runs in this session\n"
    "  { collection:'prompt_runs', operation:'aggregate', pipeline:[\n"
    "    {$match:{session_id:'<sess>', stage:'full'}},\n"
    "    {$project:{pairs:{$objectToArray:'$response.failure_summary.by_confusion_pair'}}},\n"
    "    {$unwind:'$pairs'},\n"
    "    {$group:{_id:'$pairs.k',total:{$sum:'$pairs.v'}}},\n"
    "    {$sort:{total:-1}}, {$limit:10} ] }\n"
    "\n"
    "  # Recurring failing rows across iterations (find inputs the prompt\n"
    "  # never learns) — uses per_row, any stage\n"
    "  { collection:'prompt_runs', operation:'aggregate', pipeline:[\n"
    "    {$match:{session_id:'<sess>'}},\n"
    "    {$unwind:'$response.per_row'},\n"
    "    {$match:{'response.per_row.correct':false}},\n"
    "    {$group:{_id:'$response.per_row.row_id',\n"
    "             n_failures:{$sum:1},\n"
    "             example_input:{$first:'$response.per_row.inputs'},\n"
    "             gt:{$first:'$response.per_row.gt'}}},\n"
    "    {$sort:{n_failures:-1}}, {$limit:10} ] }\n"
    "\n"
    "Cost rules: prompt_system, failures_sample, per_row are HEAVY — always "
    "project them out unless you specifically need them. Response cap is ~200KB."
)


_PROMPTLAB_DECISION_SYSTEM_PROMPT = (
    "You are a prompt-optimization controller. Each turn you decide TWO "
    "things: (1) should we keep iterating, and if so, what strategy should "
    "the prompt rewriter try next; (2) at what evaluation tier (smoke / "
    "quick / full) should this candidate be tested.\n"
    "\n"
    "You have access to a `promptlab_mongo` tool that runs constrained "
    "find/aggregate queries against the `prompt_runs` and `sessions` "
    "collections, scoped to the current session.\n"
    "\n"
    "STAGE RULES (entry_stage field):\n"
    " - 'smoke' (10 rows, random): default for risky or exploratory edits — "
    "   a brand-new strategy, a big prompt restructure, an untested class. "
    "   Cheap; just verifies the prompt produces parseable output and isn't "
    "   catastrophically broken.\n"
    " - 'quick' (100 rows, stratified): when you have evidence the prompt "
    "   works (its prior smoke had no warnings AND accuracy >= 0.3). Gives "
    "   a real accuracy signal with every class represented.\n"
    " - 'full' (entire dataset): when the prior quick run looked promising "
    "   (its accuracy_ci_95.upper >= the session's current best_macro_f1, "
    "   OR no prior best exists). ONLY full-stage runs update best_run.\n"
    "\n"
    "ESCALATION (YOU OWN THIS — there are no auto-promote gates):\n"
    "\n"
    "ESCALATION IS MANDATORY, NOT OPTIONAL. The single most important rule\n"
    "is: NEVER stay at the same stage with a NEW strategy when your most\n"
    "recent run at the SAME stage already PASSED. Passing means: warnings\n"
    "is empty AND accuracy >= 0.5. If your last run passed, the right move\n"
    "is to ESCALATE that exact strategy (copy the prior `strategy` value\n"
    "verbatim, just change `entry_stage` to the next tier). Exploration\n"
    "comes AFTER a candidate has been promoted to full.\n"
    "\n"
    " - Iter 0: entry_stage='smoke', strategy='baseline' (probe the seed).\n"
    " - Last run passed smoke (acc >= 0.5, no warnings)\n"
    "   -> NEXT: entry_stage='quick', strategy=SAME (copy verbatim).\n"
    " - Last run passed quick (acc_ci_95.upper >= current best_macro_f1)\n"
    "   -> NEXT: entry_stage='full', strategy=SAME. This iteration is the\n"
    "   one that produces a canonical best_run candidate.\n"
    " - Last run FAILED smoke (warnings OR accuracy < 0.5)\n"
    "   -> NEXT: entry_stage='smoke', strategy=DIFFERENT (start over).\n"
    " - Last run FAILED quick (ci_95.upper < best_macro_f1)\n"
    "   -> NEXT: entry_stage='smoke', strategy=DIFFERENT.\n"
    "\n"
    "ANTI-PATTERN to avoid: smoke passes -> you invent a 'better' strategy\n"
    "-> smoke again. This is WRONG — escalate the passing strategy first;\n"
    "your 'better' idea is just hypothetical until smoke+quick+full agree.\n"
    "\n"
    "ACCURACY COMPARISONS: when comparing prompts across iterations, FILTER "
    "ON stage='full'. A 10-row smoke run and a full-dataset run are not "
    "comparable. The mongoQuery tool description lists the exact filter.\n"
    "\n"
    "ANTI-REPETITION RULE:\n"
    "Never emit a `strategy` string equivalent to one already attempted in "
    "this session (any stage). Before deciding, query `prompt_runs` for "
    "prior strategies and pick something materially different — a different "
    "confusion pair, a different operation (add-rule vs add-example vs "
    "tighten-definition), or a different class. If smoke rejected your last "
    "3 attempts at the same angle, switch angles. If every meaningful angle "
    "is exhausted, emit action='stop'. 'baseline' is only valid as the "
    "iter-0 strategy.\n"
    "\n"
    "Decision rubric:\n"
    " - If n_runs == 0  -> action='continue', strategy='baseline', entry_stage='smoke'.\n"
    " - Most recent run was a passing smoke (same strategy you want to keep) "
    "   -> action='continue', SAME strategy, entry_stage='quick' (escalate).\n"
    " - Most recent run was a passing quick (same strategy) -> action='continue', "
    "   SAME strategy, entry_stage='full' (escalate to canonical candidate).\n"
    " - Most recent run was a failing smoke/quick -> action='continue' with a "
    "   DIFFERENT strategy, entry_stage='smoke' (start over).\n"
    " - Most recent FULL run has failures and you have a SPECIFIC fix idea "
    "   (e.g. 'add disambiguation rule for AccountCl vs OutOfScop based on "
    "   cue phrases like <quoted phrase from a sampled failure>') -> "
    "   action='continue', new strategy, entry_stage='smoke'.\n"
    " - Most recent FULL run REGRESSED vs prior best -> action='continue', "
    "   different angle, entry_stage='smoke'.\n"
    " - Last 3 FULL runs show a plateau (delta in macro_f1 < 0.005) AND "
    "   distinct strategies have been tried -> action='stop'.\n"
    " - budget_spent_usd > 0.7 * budget_max_usd -> action='stop'.\n"
    "\n"
    "Strategy quality bar: the `strategy` field must be specific enough that "
    "the prompt rewriter could produce a different prompt from it. Bad: "
    "'improve clarity'. Good: 'add a one-line rule that when the customer "
    "mentions \"close my account\" without specifying which product, classify "
    "as AccountCl not OutOfScope; reference the failure on row_id <id>'.\n"
    "\n"
    "Output STRICT JSON matching the schema. No prose outside the JSON."
)


_PROMPTLAB_DECISION_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "required": ["action", "strategy", "entry_stage", "reasoning"],
    "properties": {
        "action": {"enum": ["continue", "stop"]},
        "strategy": {
            "type": "string",
            "description": (
                "Free-form instruction the prompt rewriter will follow. "
                "Be specific about WHAT to change in the prompt and WHY. "
                "Empty string when action='stop'."
            ),
        },
        "entry_stage": {
            "enum": ["smoke", "quick", "full"],
            "description": (
                "Where to start the candidate's evaluation. 'smoke' = 10 "
                "rows (cheap probe); 'quick' = 100 stratified rows "
                "(refinement); 'full' = whole dataset (real candidate). "
                "Gates auto-promote smoke -> quick -> full if criteria met. "
                "Use 'smoke' on iter 0 and for any risky/exploratory edit."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": "One sentence on why this action / strategy / stage.",
        },
        "target_confusion_pair": {
            "type": "string",
            "description": "If strategy targets a specific gt->pred pair, name it (e.g. 'AccountCl->OutOfScop'). Empty string when none.",
        },
    },
}, indent=2)


_PROMPTLAB_DEFINITION: dict[str, Any] = {
    "nodes": [
        # 1. Entry: receive {session_id, dataset_id, intent_classes,
        #    prompt_system_seed, prompt_user_template, max_iterations,
        #    max_cost_usd}. The webhook responds with the output of the
        #    last reached node (finalize, on natural completion).
        {
            "name": "webhook",
            "type": "Webhook",
            "parameters": {
                "method": "POST",
                "path": "promptlab/run",
                "responseMode": "lastNode",
            },
            "position": {"x": 100, "y": 400},
        },
        # 2. Loop — maxIterations reads from the webhook payload. The Loop
        #    node synthesizes N tickets from the webhook body merged with
        #    {i: 0..N-1}, so the body fields (session_id, dataset_id,
        #    prompt_system_seed, prompt_user_template, max_cost_usd, ...)
        #    are available as $json inside the loop.
        {
            "name": "mainLoop",
            "type": "Loop",
            "parameters": {
                "batchSize": 1,
                "maxIterations": "{{ $json.body.max_iterations or 5 }}",
            },
            "position": {"x": 340, "y": 400},
        },
        # 3. Session state — read the session doc so the quickGate has a
        #    scalar best_macro_f1 to compare a candidate's CI against.
        #    best_macro_f1 is maintained by /evaluate (only on stage='full').
        #    On iter 0 the session may not exist yet → empty documents → 0.
        {
            "name": "sessionStateQuery",
            "type": "MongoDB",
            "parameters": {
                "connectionString": _PROMPTLAB_MONGO_URL,
                "database": _PROMPTLAB_MONGO_DB,
                "collection": _PROMPTLAB_SESSIONS_COLLECTION,
                "operation": "find",
                "filter": "{\"session_id\": \"{{ $json.body.session_id }}\"}",
                "projection": (
                    "{\"_id\": 0, \"session_id\": 1, \"best_run_id\": 1, "
                    "\"best_macro_f1\": 1, \"n_runs\": 1, "
                    "\"budget_spent_usd\": 1, \"max_cost_usd\": 1}"
                ),
                "sort": "{}",
                "limit": 1,
                "skip": 0,
            },
            "position": {"x": 580, "y": 200},
        },
        # 4. Trend query — last 10 runs for this session, heavy fields out.
        #    Empty result on iter 0 (no prior runs). per_row is large; strip it.
        {
            "name": "trendQuery",
            "type": "MongoDB",
            "parameters": {
                "connectionString": _PROMPTLAB_MONGO_URL,
                "database": _PROMPTLAB_MONGO_DB,
                "collection": _PROMPTLAB_RUNS_COLLECTION,
                "operation": "find",
                "filter": "{\"session_id\": \"{{ $json.body.session_id }}\"}",
                "projection": (
                    "{\"response.failures_sample\": 0, \"response.per_row\": 0}"
                ),
                "sort": "{\"created_at\": -1}",
                "limit": 10,
                "skip": 0,
            },
            "position": {"x": 820, "y": 400},
        },
        # 5. Decision agent — reads the trend + has a mongo_query tool for
        #    deep dives (top confusion pairs, regression detection, etc.).
        #    Emits {action, strategy, reasoning, target_confusion_pair?}.
        {
            "name": "decideAgent",
            "type": "AIAgent",
            "parameters": {
                "model": "gemini-2.5-flash",
                "systemPrompt": _PROMPTLAB_DECISION_SYSTEM_PROMPT,
                "task": (
                    "Session: {{ $node['webhook'].json.body.session_id }}\n"
                    "Iteration: {{ ($node['mainLoop'].json.i or 0) + 1 }} / {{ $node['webhook'].json.body.max_iterations or 5 }}\n"
                    "Intent classes: {{ json_stringify($node['webhook'].json.body.intent_classes) }}\n"
                    "Budget cap: ${{ $node['webhook'].json.body.max_cost_usd or 1.0 }}\n\n"
                    "Recent runs (newest first, heavy fields stripped):\n"
                    "{{ json_stringify($node['trendQuery'].json.documents or []) }}\n\n"
                    "Use promptlab_mongo for deeper inspection (confusion-pair "
                    "aggregation, regression diffs, etc.). Then output JSON "
                    "matching the schema."
                ),
                "builtinTools": [
                    {
                        "type": "mongoQuery",
                        "parameters": {
                            "tool_name": "promptlab_mongo",
                            "tool_description": _PROMPTLAB_QUERY_TOOL_DESCRIPTION,
                            "connection_string": _PROMPTLAB_MONGO_URL,
                            "database": _PROMPTLAB_MONGO_DB,
                            "allowed_collections": [
                                _PROMPTLAB_RUNS_COLLECTION,
                                _PROMPTLAB_SESSIONS_COLLECTION,
                            ],
                            "mandatory_filter_field": "session_id",
                            "mandatory_filter_value_expression": (
                                "{{ $node['webhook'].json.body.session_id }}"
                            ),
                            "default_projection_strip": [
                                "prompt_system",
                                "response.failures_sample",
                            ],
                            "max_pipeline_stages": 8,
                            "max_limit": 50,
                            "max_time_ms": 3000,
                            "max_response_bytes": 200000,
                        },
                    },
                ],
                "memoryType": "none",
                "maxIterations": 5,
                "temperature": 0.3,
                "maxContextTokens": 120000,
                "maxOutputTokens": 2048,
                "outputSchema": _PROMPTLAB_DECISION_OUTPUT_SCHEMA,
                "enableSubAgents": False,
                "enablePlanning": False,
                "enableScratchpad": False,
                "enablePtc": False,
                "skillProfiles": [],
            },
            "position": {"x": 1060, "y": 280},
        },
        # 6. Prompt writer — small LLM that takes the agent's strategy +
        #    the best-so-far prompt and emits the next system-prompt text.
        #    On iter 0 (no prior best), it just echoes the seed verbatim.
        {
            "name": "promptWriter",
            "type": "LLMChat",
            "parameters": {
                "model": "gemini-2.5-flash",
                "systemPrompt": (
                    "You rewrite a classification system prompt to address a "
                    "SPECIFIC weakness.\n"
                    "\n"
                    "PASS-THROUGH RULE (read first):\n"
                    "If the STRATEGY field is 'baseline', empty, or any "
                    "variant meaning 'use as-is' (e.g. 'baseline (use seed "
                    "verbatim)'), return the CURRENT PROMPT EXACTLY as "
                    "given — character-for-character, including all output "
                    "schema details, class names, and formatting "
                    "instructions. Do NOT 'improve', 'generalize', or "
                    "'clean up' the prompt. The seed prompt is canonical.\n"
                    "\n"
                    "Otherwise, make the SMALLEST possible edit to the "
                    "CURRENT PROMPT that addresses STRATEGY. Preserve all "
                    "existing class names, output schema keys, format "
                    "instructions, and examples unless STRATEGY explicitly "
                    "asks to change them.\n"
                    "\n"
                    "Return ONLY the prompt text — no commentary, no "
                    "markdown fences, no preface."
                ),
                "userMessage": (
                    "STRATEGY (what to change and why):\n"
                    "{{ $node['decideAgent'].json.structured.strategy or 'baseline' }}\n\n"
                    "TARGETED CONFUSION PAIR (if any):\n"
                    "{{ $node['decideAgent'].json.structured.target_confusion_pair or 'none' }}\n\n"
                    "CURRENT PROMPT (seed on iter 0, else the most recent run's prompt):\n"
                    # Gate the array-index attribute access on documentCount > 0
                    # so iter 0 (empty trend) cleanly falls through to the seed
                    # instead of erroring on attribute access against {}.
                    "{{ (($node['trendQuery'].json.documentCount > 0) and "
                    "$node['trendQuery'].json.documents[0].prompt_system) "
                    "or $node['webhook'].json.body.prompt_system_seed or '' }}\n\n"
                    "Return the prompt only."
                ),
                "temperature": 0.0,
                "maxTokens": 2048,
            },
            "position": {"x": 1300, "y": 280},
        },
        # 7. Evaluate — single HttpRequest. The agent's entry_stage decision
        #    is forwarded as the `stage` field; the server resolves stage to
        #    a SampleSpec via its canonical mapping (smoke=10 random,
        #    quick=100 stratified, full=whole dataset). One node, no Switch,
        #    no gates — the agent does escalation across iterations (sees
        #    the prior smoke result, picks 'quick' next; sees the quick
        #    result, picks 'full' next).
        {
            "name": "evalCall",
            "type": "HttpRequest",
            "parameters": {
                "method": "POST",
                "url": f"{_PROMPTLAB_ANALYTICS_BASE}/prompt-lab/evaluate",
                "headers": [
                    {"name": "Content-Type", "value": "application/json"},
                ],
                "body": (
                    "{\n"
                    "  \"session_id\": {{ json_stringify($node['webhook'].json.body.session_id) }},\n"
                    "  \"dataset_id\": {{ json_stringify($node['webhook'].json.body.dataset_id) }},\n"
                    "  \"intent_classes\": {{ json_stringify($node['webhook'].json.body.intent_classes) }},\n"
                    "  \"prompt_system\": {{ json_stringify($node['promptWriter'].json.response or '') }},\n"
                    "  \"prompt_user_template\": {{ json_stringify($node['webhook'].json.body.prompt_user_template) }},\n"
                    "  \"strategy\": {{ json_stringify($node['decideAgent'].json.structured.strategy or 'baseline') }},\n"
                    "  \"stage\": {{ json_stringify($node['decideAgent'].json.structured.entry_stage or 'smoke') }},\n"
                    "  \"max_cost_usd\": {{ $node['webhook'].json.body.max_cost_usd or 1.0 }},\n"
                    "  \"config\": { \"model\": {{ json_stringify($node['webhook'].json.body.model or 'gemini-2.5-flash') }} }\n"
                    "}"
                ),
                "responseType": "json",
            },
            "position": {"x": 1540, "y": 280},
        },
        # 8. Stop check — stop the loop if the agent said 'stop' OR the
        #    eval reports cumulative spend at the cap. One source, no
        #    coalesce needed.
        {
            "name": "stopCheck",
            "type": "If",
            "parameters": {
                "condition": (
                    "{{ ($node['decideAgent'].json.structured.action == 'stop') or "
                    "(($node['evalCall'].json.body.budget_spent_usd or 0) "
                    ">= ($node['webhook'].json.body.max_cost_usd or 1e9)) }}"
                ),
            },
            "position": {"x": 1780, "y": 280},
        },
        # 9. fetchAllRuns — pull every run for this session, sorted oldest
        #    first, so the LLM narrator can see the trajectory. Heavy
        #    failures_sample is projected out; prompt_system is kept so
        #    the narrator can quote the winner's actual prompt.
        {
            "name": "fetchAllRuns",
            "type": "MongoDB",
            "parameters": {
                "connectionString": _PROMPTLAB_MONGO_URL,
                "database": _PROMPTLAB_MONGO_DB,
                "collection": _PROMPTLAB_RUNS_COLLECTION,
                "operation": "find",
                "filter": (
                    "{\"session_id\": \"{{ "
                    "$node['webhook'].json.body.session_id "
                    "}}\"}"
                ),
                "projection": "{\"response.failures_sample\": 0, \"response.per_row\": 0}",
                "sort": "{\"created_at\": 1}",
                "limit": 50,
                "skip": 0,
            },
            "position": {"x": 2500, "y": 700},
        },
        # 10. summarize — LLM narrator. Reads the full trajectory and writes
        #     a human-readable verdict + the actionable winning prompt.
        #     Becomes the webhook response (last node reached).
        {
            "name": "summarize",
            "type": "LLMChat",
            "parameters": {
                "model": "gemini-2.5-flash",
                "systemPrompt": (
                    "You are a prompt-optimizer reporter. You receive the full "
                    "trajectory of prompt-rewrite iterations from one optimization "
                    "session and write a concise, demo-friendly summary in "
                    "markdown.\n\n"
                    "Structure your response as:\n\n"
                    "## Verdict\n"
                    "One line: improved / no change / regressed, with the macro_f1 "
                    "delta (baseline -> winner).\n\n"
                    "## Summary\n"
                    "- Baseline macro_f1, winner macro_f1, delta\n"
                    "- Iterations used, total cost (USD), budget remaining\n"
                    "- Dataset rows evaluated\n\n"
                    "## Iteration trajectory\n"
                    "Bullet list: `iter N: <strategy summary> -> macro_f1 = X.XX (delta +/-Y.YY)`\n\n"
                    "## Winning prompt\n"
                    "Show the full prompt_system text of the highest-macro_f1 run "
                    "in a fenced code block.\n\n"
                    "## Remaining weaknesses\n"
                    "Top 3 confusion pairs in the winner's failure_summary, with counts.\n\n"
                    "## Caveats\n"
                    "Call out: dataset size < 100 rows ('suggestive, not significant'), "
                    "single-split data ('no held-out test'), budget nearly exhausted, "
                    "agent stopped early, any iteration with `cached=true` (no new "
                    "evaluation actually ran), or repeated identical prompt_hashes.\n\n"
                    "Be honest. If macro_f1 didn't move or regressed, say so plainly. "
                    "Don't pad."
                ),
                "userMessage": (
                    "SESSION ID: {{ $node['webhook'].json.body.session_id }}\n"
                    "INTENT CLASSES: {{ json_stringify($node['webhook'].json.body.intent_classes) }}\n"
                    "BUDGET CAP: ${{ $node['webhook'].json.body.max_cost_usd or 1.0 }}\n\n"
                    "ALL RUNS (oldest first):\n"
                    "{{ json_stringify($node['fetchAllRuns'].json.documents or []) }}"
                ),
                "temperature": 0.2,
                "maxTokens": 2048,
            },
            "position": {"x": 2020, "y": 460},
        },
    ],
    "connections": [
        # Setup
        {"source_node": "webhook", "target_node": "mainLoop", "source_output": "main", "target_input": "main"},

        # Linear loop body — agent picks stage, single eval forwards it to /evaluate.
        {"source_node": "mainLoop", "target_node": "sessionStateQuery", "source_output": "loop", "target_input": "main"},
        {"source_node": "sessionStateQuery", "target_node": "trendQuery", "source_output": "main", "target_input": "main"},
        {"source_node": "trendQuery", "target_node": "decideAgent", "source_output": "main", "target_input": "main"},
        {"source_node": "decideAgent", "target_node": "promptWriter", "source_output": "main", "target_input": "main"},
        {"source_node": "promptWriter", "target_node": "evalCall", "source_output": "main", "target_input": "main"},
        {"source_node": "evalCall", "target_node": "stopCheck", "source_output": "main", "target_input": "main"},

        # Early stop -> fetch runs -> summarize.
        {"source_node": "stopCheck", "target_node": "fetchAllRuns", "source_output": "true", "target_input": "main"},
        # Continue -> back to loop top.
        {"source_node": "stopCheck", "target_node": "mainLoop", "source_output": "false", "target_input": "main"},

        # Natural loop completion -> fetch runs -> summarize.
        {"source_node": "mainLoop", "target_node": "fetchAllRuns", "source_output": "done", "target_input": "main"},

        # Both paths converge on the summarizer.
        {"source_node": "fetchAllRuns", "target_node": "summarize", "source_output": "main", "target_input": "main"},
    ],
    "settings": {},
}


async def seed_promptlab_workflow(session, *, existing_ids: set[str], existing_names: set[str]) -> bool:
    """Insert or update the PromptLab workflow.

    Idempotent on workflow id ``wf_promptlab``. If the row exists but the
    definition has drifted from the source-of-truth ``_PROMPTLAB_DEFINITION``
    (e.g. after a code change), we update the draft + publish a new version
    rather than silently skip. Returns True if anything was written.
    """
    name = "PromptLab"
    description = (
        "Prompt-optimization loop with SQLite-per-session storage. Each "
        "iteration: refresh session, ask a decision agent (gemini-2.5-pro) "
        "what to change via SELECT queries against the session DB, rewrite "
        "the prompt with gemini-2.5-flash, evaluate against the session's "
        "dataset, persist the run. Stops on budget, iteration cap, or plateau."
    )

    if _PROMPTLAB_WORKFLOW_ID in existing_ids:
        # Compare existing definition to the source of truth. If different,
        # update and publish a new version.
        existing = (
            await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == _PROMPTLAB_WORKFLOW_ID)
            )
        ).scalar_one_or_none()
        if existing is None:
            # Stale set membership; fall through to insert.
            pass
        else:
            if existing.draft_definition == _PROMPTLAB_DEFINITION:
                print(f"  Skipped (unchanged): {name}")
                return False

            # Find current max version_number and bump.
            max_v_row = (
                await session.execute(
                    select(WorkflowVersionModel.version_number)
                    .where(WorkflowVersionModel.workflow_id == _PROMPTLAB_WORKFLOW_ID)
                    .order_by(WorkflowVersionModel.version_number.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            next_v = (max_v_row or 0) + 1

            existing.name = name
            existing.description = description
            existing.draft_definition = _PROMPTLAB_DEFINITION
            existing.updated_at = datetime.now()
            await session.flush()

            new_version = WorkflowVersionModel(
                workflow_id=_PROMPTLAB_WORKFLOW_ID,
                version_number=next_v,
                definition=_PROMPTLAB_DEFINITION,
                message=f"Auto-update on seed (v{next_v})",
                created_at=datetime.now(),
            )
            session.add(new_version)
            await session.flush()
            existing.published_version_id = new_version.id
            print(f"  Updated [PUBLISHED v{next_v}]: {name}")
            return True

    if name in existing_names:
        # Name collision with a different id — skip to avoid a unique-constraint
        # crash. This shouldn't happen in practice; PromptLab owns the name.
        print(f"  Skipped (name collision): {name}")
        return False

    workflow = WorkflowModel(
        id=_PROMPTLAB_WORKFLOW_ID,
        name=name,
        description=description,
        active=True,
        draft_definition=_PROMPTLAB_DEFINITION,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    session.add(workflow)
    await session.flush()

    version = WorkflowVersionModel(
        workflow_id=_PROMPTLAB_WORKFLOW_ID,
        version_number=1,
        definition=_PROMPTLAB_DEFINITION,
        message="Initial seed",
        created_at=datetime.now(),
    )
    session.add(version)
    await session.flush()

    workflow.published_version_id = version.id
    print(f"  Added [PUBLISHED v1]: {name}")
    return True


EXAMPLE_WORKFLOWS = [
    {
        # Demo for UI-side verification of $vars secret redaction.
        # Pair with scripts/dummy_token_api.py listening on :8765.
        # User must add a `type=secret` variable DEMO_API_TOKEN whose value
        # is "demo_token_xyz_secure_123" (the only token the dummy API accepts).
        "name": "Vars Redaction Demo",
        "description": (
            "Calls a local dummy API (scripts/dummy_token_api.py on :8765) "
            "that only accepts one specific token, passed as ?api_key=. "
            "The token comes from the env-scoped secret {{ $vars.DEMO_API_TOKEN }}. "
            "After running, open the HttpRequest node detail — the resolved "
            "requestUrl shown in the UI shows ***, never the plaintext token, "
            "while the upstream still received the real token (status 200)."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {
                        "method": "POST",
                        "path": "vars-redaction-demo",
                        "responseMode": "lastNode",
                    },
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Call Protected API",
                    "type": "HttpRequest",
                    "parameters": {
                        "url": "http://127.0.0.1:8765/protected?api_key={{ $vars.DEMO_API_TOKEN }}",
                        "method": "GET",
                        "headers": [],
                        "responseType": "json",
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Respond",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "contentType": "application/json",
                        "wrapResponse": True,
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Call Protected API"},
                {"source_node": "Call Protected API", "target_node": "Respond"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Prompt Evaluator",
        "description": (
            "Hub & spoke prompt evaluator: orchestrator dispatches to "
            "3 skill sub-agents (structural linguist, context auditor, domain specialist) "
            "plus 1 dynamically spawned Simulator. Uses shared store for cross-agent "
            "communication — prompt data stays out of LLM context until recalled. "
            "Returns scored report with rewritten Perfect Prompt."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {
                        "method": "POST",
                        "path": "prompt-evaluator",
                        "responseMode": "lastNode",
                    },
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": '{{ $json.body or {"prompt": "You are a helpful assistant. Answer user questions.", "test_utterances": ["Hello!", "What is 2+2?", "Write me a poem about cats"]} }}',
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Prompt Evaluator",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.0-flash",
                        "systemPrompt": _PROMPT_EVAL_SYSTEM_PROMPT,
                        "task": "Evaluate this prompt:\n\n{{ $json.prompt }}\n\nTest utterances to simulate:\n{{ json_stringify($json.test_utterances) }}",
                        "maxIterations": 15,
                        "temperature": 0.3,
                        "enableSubAgents": False,
                        "maxAgentDepth": 2,
                        "allowRecursiveSpawn": False,
                        "enablePlanning": True,
                        "enableScratchpad": True,
                        "outputSchema": _PROMPT_EVAL_OUTPUT_SCHEMA,
                        "skillProfiles": [
                            {
                                "name": "structural_linguist",
                                "description": "Static analysis: grammar, syntax, ambiguity, vague quantifiers, negative constraints, lazy words. Reads prompt from shared:prompt, writes results to shared:structural_linguist. Scores clarity 0-100.",
                                "systemPrompt": _STRUCTURAL_LINGUIST_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "context_auditor",
                                "description": "CO-STAR framework analysis: Context, Objective, Style, Tone, Audience, Response format. Reads prompt from shared:prompt, writes results to shared:context_auditor. Scores completeness 0-100.",
                                "systemPrompt": _CONTEXT_AUDITOR_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "domain_specialist",
                                "description": "Domain detection + domain-specific checklist. Reads prompt from shared:prompt, writes results to shared:domain_specialist. Scores precision 0-100.",
                                "systemPrompt": _DOMAIN_SPECIALIST_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "simulator",
                                "description": "Role-plays the prompt against test utterances. Reads prompt from shared:prompt and utterances from shared:utterances. Writes results to shared:simulation.",
                                "systemPrompt": _SIMULATOR_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                        ],
                    },
                    "position": {"x": 650, "y": 300},
                },
                {
                    "name": "Respond",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "contentType": "application/json",
                        "wrapResponse": True,
                    },
                    "position": {"x": 1000, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Prompt Evaluator"},
                {"source_node": "Prompt Evaluator", "target_node": "Respond"},
            ],
            "settings": {},
        },
    },
]


async def ensure_promptlab_indexes() -> None:
    """Create the Mongo TTL + query indexes used by the PromptLab workflow.

    Idempotent: ``create_index`` is a no-op when an index with the same spec
    and options already exists. Failures are logged but not raised — workflow
    seeding shouldn't be blocked by an unreachable Mongo (e.g. during local
    dev where the user hasn't started Mongo yet).
    """
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        logger.warning(
            "motor is not installed; skipping PromptLab Mongo index creation"
        )
        return

    mongo_url = os.environ.get("WORKFLOW_MONGO_URL")
    if not mongo_url:
        # Fall back to pydantic-settings (loaded from .env) before bailing.
        try:
            from ..core.config import settings as _settings  # local import to avoid cycles
            mongo_url = getattr(_settings, "mongo_url", None)
        except Exception:  # noqa: BLE001
            mongo_url = None
    if not mongo_url:
        logger.warning(
            "WORKFLOW_MONGO_URL is not set; skipping PromptLab Mongo index creation"
        )
        return

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
    try:
        db = client[_PROMPTLAB_MONGO_DB]
        sessions = db[_PROMPTLAB_SESSIONS_COLLECTION]
        prompt_runs = db[_PROMPTLAB_RUNS_COLLECTION]

        try:
            await sessions.create_index(
                [("last_accessed_at", 1)],
                expireAfterSeconds=86400,
                name="ttl_last_accessed_at",
            )
            await prompt_runs.create_index(
                [("created_at", 1)],
                expireAfterSeconds=172800,
                name="ttl_created_at",
            )
            await prompt_runs.create_index(
                [("session_id", 1), ("created_at", -1)],
                name="ix_session_created",
            )
            await prompt_runs.create_index(
                [
                    ("session_id", 1),
                    ("metrics.by_split.Holdout.macro_f1", -1),
                ],
                name="ix_session_holdout_macro_f1",
            )
            await prompt_runs.create_index(
                [("session_id", 1), ("parent_run_id", 1)],
                name="ix_session_parent_run",
            )
            print("  Ensured PromptLab Mongo indexes (sessions TTL 24h, prompt_runs TTL 48h)")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to create PromptLab Mongo indexes: %s", e)
    finally:
        client.close()


async def seed_workflows(reset: bool = False) -> None:
    """Seed the database with example workflows."""
    await init_db()

    # PromptLab keeps its mutable state in Mongo. Ensure TTLs + query indexes
    # exist before we publish the workflow definition that depends on them.
    await ensure_promptlab_indexes()

    async with async_session_factory() as session:
        if reset:
            # Cascade: versions reference workflows, so delete versions first
            await session.execute(delete(WorkflowVersionModel))
            await session.execute(text("UPDATE workflows SET published_version_id = NULL"))
            await session.execute(delete(WorkflowModel))
            await session.commit()
            print("Cleared existing workflows.")

        result = await session.execute(select(WorkflowModel.name))
        existing_names = {row[0] for row in result.fetchall()}
        id_result = await session.execute(select(WorkflowModel.id))
        existing_ids = {row[0] for row in id_result.fetchall()}

        added = 0
        skipped = 0

        # PromptLab is seeded via a dedicated helper so its id stays stable.
        if await seed_promptlab_workflow(
            session, existing_ids=existing_ids, existing_names=existing_names
        ):
            added += 1
            existing_ids.add(_PROMPTLAB_WORKFLOW_ID)
            existing_names.add("PromptLab")
        else:
            skipped += 1

        for workflow_data in EXAMPLE_WORKFLOWS:
            if workflow_data["name"] in existing_names:
                skipped += 1
                continue

            wf_id = workflow_data.get("id") or generate_workflow_id(workflow_data["name"])
            is_active = workflow_data.get("active", False)

            if "definition" in workflow_data:
                definition = workflow_data["definition"]
            else:
                definition = {
                    "nodes": workflow_data.get("nodes", []),
                    "connections": workflow_data.get("connections", []),
                    "settings": workflow_data.get("settings", {}),
                }

            workflow = WorkflowModel(
                id=wf_id,
                name=workflow_data["name"],
                description=workflow_data.get("description", ""),
                active=False,
                draft_definition=definition,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(workflow)
            await session.flush()

            if is_active:
                version = WorkflowVersionModel(
                    workflow_id=wf_id,
                    version_number=1,
                    definition=definition,
                    message="Initial seed",
                    created_at=datetime.now(),
                )
                session.add(version)
                await session.flush()

                workflow.published_version_id = version.id
                workflow.active = True

            added += 1
            status = "PUBLISHED v1" if is_active else "draft"
            print(f"  Added [{status}]: {workflow_data['name']}")

        await session.commit()
        print(f"\nSeeding complete. Added {added} workflows" + (f", skipped {skipped} existing." if skipped else "."))


def main() -> None:
    """Run the seed script."""
    asyncio.run(seed_workflows(reset=True))


if __name__ == "__main__":
    main()
