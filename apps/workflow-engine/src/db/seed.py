"""Seed database with demo workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime

from sqlalchemy import select, delete

from .session import async_session_factory, init_db
from .models import WorkflowModel


def generate_workflow_id(name: str) -> str:
    """Generate a unique workflow ID."""
    timestamp = int(time.time() * 1000)
    hash_input = f"{timestamp}_{name}"
    hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    return f"wf_{timestamp}_{hash_suffix}"


# ── Prompt Evaluator (Hub & Spoke) ────────────────────────────────────
# Webhook-triggered prompt evaluator: orchestrator dispatches to
# 3 skill sub-agents (structural_linguist, context_auditor,
# domain_specialist) + 1 dynamically spawned Simulator, collects
# results, cross-references theory vs practice, returns scored report
# with a rewritten "Perfect Prompt".

# ── Data Analysis Agent ───────────────────────────────────────────────
# AI agent with direct access to analytics service tools (profile, aggregate,
# sample, report).  Data is uploaded first to get a dataset_id (stored as
# parquet in /tmp), then only the ID is passed to the agent — no raw data
# in LLM context.

_DATA_ANALYST_SYSTEM_PROMPT = (
    "You are a Senior Data Analyst AI with access to powerful analytics tools.\n\n"
    "## Available Tools\n"
    "- **profile_data**: Profile dataset columns — types, nulls, stats, distributions, correlations\n"
    "- **aggregate_data**: Group-by aggregations — sum, mean, median, count, min, max, std, nunique\n"
    "- **sample_data**: Sample rows — random, stratified, systematic, first_n, last_n\n"
    "- **generate_report**: Generate formatted reports in HTML or Markdown\n"
    "- **run_code**: Execute ad-hoc Python for custom calculations\n"
    "- **calculator**: Quick math expressions\n\n"
    "## Data Access\n"
    "The dataset has been pre-uploaded and you receive a `dataset_id` reference.\n"
    "**Always pass `dataset_id` to every tool call** — never embed raw data in tool arguments.\n"
    "The analytics service loads the dataset from disk by ID, which is far more efficient.\n\n"
    "## Protocol\n"
    "1. **Receive** the dataset metadata (dataset_id, row/column counts, preview) and the user's question.\n"
    "2. **Plan** your approach — write a <plan> block listing which tools you will call and why.\n"
    "3. **Profile first** — always call profile_data with the dataset_id to understand column types, nulls, and distributions before deeper analysis.\n"
    "4. **Analyze** — call aggregate_data, sample_data, or run_code as needed to answer the question.\n"
    "5. **Use scratchpad** — store intermediate findings with memory_store so you can reference them later.\n"
    "6. **Reflect** — write a <reflect> block reviewing whether you have enough evidence.\n"
    "7. **Synthesize** — produce the structured output with answer, findings, quality assessment, and methodology.\n\n"
    "## Guidelines\n"
    "- Always pass dataset_id to tools, never the raw data array. The analytics service loads data by file path.\n"
    "- For large datasets, sample first (e.g. 1000 rows) before profiling if the data exceeds 10k rows.\n"
    "- Use filter_expr in aggregate_data to focus on relevant subsets.\n"
    "- When multiple aggregations are needed, batch them in a single aggregate_data call.\n"
    "- Quantify findings with numbers and percentages, not vague statements.\n"
    "- Flag data quality issues (nulls, outliers, type mismatches) proactively."
)

_DATA_ANALYST_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "Direct answer to the user's question",
        },
        "executive_summary": {
            "type": "string",
            "description": "One-paragraph overview of the analysis",
        },
        "key_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding": {"type": "string", "description": "What was found"},
                    "evidence": {"type": "string", "description": "Data supporting the finding"},
                    "impact": {"type": "string", "description": "Why this matters"},
                },
            },
            "description": "Key findings from the analysis",
        },
        "data_quality": {
            "type": "object",
            "properties": {
                "score": {"type": "string", "description": "Quality score: excellent / good / fair / poor"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue": {"type": "string"},
                            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                            "affected_columns": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
            "description": "Data quality assessment",
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Actionable recommendations based on the analysis",
        },
        "methodology": {
            "type": "string",
            "description": "How the answer was computed — tools used and steps taken",
        },
    },
}, indent=2)


# ── Prompt Evaluator (Hub & Spoke) ────────────────────────────────────

_PROMPT_EVAL_SYSTEM_PROMPT = (
    "You are a Prompt Evaluation Orchestrator. A user has submitted a prompt "
    "(system prompt / instruction) along with test_utterances. Your job: dispatch "
    "4 parallel expert evaluations, collect results, cross-reference, and produce "
    "a comprehensive structured report.\n\n"
    "## Protocol\n"
    "1. **Store** the prompt and test_utterances in scratchpad.\n"
    "2. **Dispatch ALL 4 evaluations in ONE turn** (they run in parallel):\n"
    "   a. delegate_to_skill('structural_linguist', task=the prompt text)\n"
    "   b. delegate_to_skill('context_auditor', task=the prompt text)\n"
    "   c. delegate_to_skill('domain_specialist', task=the prompt text)\n"
    "   d. spawn_agent — Simulator. Task MUST include:\n"
    "      - The FULL prompt being evaluated\n"
    "      - ALL test utterances\n"
    "      - Instruction: role-play as an AI with that system prompt, process "
    "each utterance, produce the full output, then grade: on_topic? format_match? "
    "refusal? hallucination? tone? quality? Return JSON array.\n\n"
    "3. **Collect & Store** all 4 results via memory_store.\n"
    "4. **Cross-reference**: Compare simulation results against analyst findings. "
    "Where did theory predict issues that simulation confirmed? Where did "
    "simulation reveal issues analysts missed?\n"
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
    "- has_sections: bool (does it use headers/sections?)\n"
    "- has_bullet_points: bool\n"
    "- has_numbered_steps: bool\n"
    "- has_examples: bool (few-shot examples included?)\n"
    "- has_persona: bool ('You are...' / 'Act as...')\n"
    "- has_output_format: bool (specifies expected output format?)\n"
    "- has_constraints: bool (boundaries, what NOT to do?)\n"
    "- has_edge_cases: bool (handles edge cases?)\n"
    "- word_count: number\n"
    "- estimated_token_count: number\n"
    "- information_density: 'sparse' | 'moderate' | 'dense'\n"
    "- readability: 'easy' | 'moderate' | 'complex'\n"
    "- verdict: one-sentence assessment of structure quality\n\n"
    "### analysis\n"
    "Include the FULL output from each analyst, not summaries:\n"
    "- structural_linguist: complete JSON from the skill (issues, vague_terms, "
    "lazy_words, negative_constraints, recommendation, clarity_score)\n"
    "- context_auditor: complete JSON (costar_breakdown with C/O/S/T/A/R scores, "
    "missing_components, has_persona, has_output_format, has_constraints, "
    "has_examples, recommendation, completeness_score)\n"
    "- domain_specialist: complete JSON (detected_domain, domain_specific_issues, "
    "missing_specifications, recommendation, precision_score)\n"
    "- theory_vs_practice: paragraph comparing analyst predictions vs simulation\n\n"
    "### simulation\n"
    "- utterance_results: array with utterance, output, on_topic, format_match, "
    "refusal, quality_rating (1-5), issues\n"
    "- pass_count, fail_count\n"
    "- overall_issues: array of patterns across all utterances\n\n"
    "### issues (NEW - master issue list)\n"
    "Deduplicated list across ALL analysts. Each issue:\n"
    "- issue: description\n"
    "- severity: critical | major | minor | suggestion\n"
    "- source: which analyst(s) found it\n"
    "- quote: exact text from the prompt that's problematic (if applicable)\n"
    "- fix: specific suggested fix\n\n"
    "### perfect_prompt\n"
    "Complete rewrite that fixes EVERY issue. Must:\n"
    "- Apply full CO-STAR framework\n"
    "- Add sections/structure if missing\n"
    "- Include output format specification\n"
    "- Add constraints and edge case handling\n"
    "- Be substantially different from the original\n\n"
    "### summary\n"
    "2-3 sentence executive summary.\n\n"
    "## Rules\n"
    "- ALWAYS delegate — never evaluate the prompt yourself.\n"
    "- Dispatch ALL 4 evaluations in the SAME turn.\n"
    "- Include COMPLETE analyst outputs, not summaries.\n"
    "- The issues list must be deduplicated with severity levels.\n"
    "- The perfect_prompt must be a SUBSTANTIAL rewrite."
)

_STRUCTURAL_LINGUIST_PROMPT = (
    "You are a Structural Linguist specializing in prompt engineering analysis. "
    "You perform deep structural evaluation of prompts.\n\n"
    "## Analysis Checklist\n"
    "Evaluate EVERY item. For each, note pass/fail and quote the relevant text.\n\n"
    "1. **Format & Organization**\n"
    "   - Does it use sections/headers? Bullet points? Numbered steps?\n"
    "   - Is information hierarchically organized (most important first)?\n"
    "   - Is there logical flow between instructions?\n"
    "   - Rate: structured / semi-structured / wall-of-text\n\n"
    "2. **Grammar & Syntax**\n"
    "   - Grammatical errors, run-on sentences, fragments\n"
    "   - Inconsistent tense or voice\n"
    "   - Quote each error with the exact problematic text\n\n"
    "3. **Ambiguity Detection**\n"
    "   - Vague quantifiers: 'short', 'few', 'some', 'good', 'nice', "
    "'appropriate', 'relevant', 'proper'\n"
    "   - Pronouns with unclear antecedents\n"
    "   - Instructions that could be interpreted multiple ways\n"
    "   - For each: quote the text and explain the ambiguity\n\n"
    "4. **Negative vs Positive Constraints**\n"
    "   - Find all \"don't do X\" patterns (less effective)\n"
    "   - Suggest positive rewrites: \"do Y instead\"\n\n"
    "5. **Lazy/Filler Words**\n"
    "   - Flag: 'basically', 'really', 'just', 'stuff', 'things', 'etc', "
    "'very', 'actually', 'simply'\n\n"
    "6. **Instruction Executability**\n"
    "   - Can each instruction be executed unambiguously by an AI?\n"
    "   - Are there instructions that require human judgment without criteria?\n"
    "   - Are success criteria defined?\n\n"
    "7. **Length Assessment**\n"
    "   - Word count, estimated token count\n"
    "   - Is it under-specified (too terse) or over-specified (diluted)?\n"
    "   - Signal-to-noise ratio: high / medium / low\n\n"
    "## Output Format\n"
    "Return a JSON object:\n"
    "```json\n"
    "{\n"
    "  \"clarity_score\": 0-100,\n"
    "  \"format_rating\": \"structured|semi-structured|wall-of-text\",\n"
    "  \"word_count\": number,\n"
    "  \"signal_to_noise\": \"high|medium|low\",\n"
    "  \"issues\": [{\"issue\": \"...\", \"quote\": \"exact text\", "
    "\"severity\": \"critical|major|minor\", \"fix\": \"suggested fix\"}],\n"
    "  \"vague_terms\": [\"word1\", \"word2\"],\n"
    "  \"negative_constraints\": [{\"original\": \"don't...\", "
    "\"rewrite\": \"do...\"}],\n"
    "  \"lazy_words\": [\"word1\"],\n"
    "  \"strengths\": [\"what the prompt does well\"],\n"
    "  \"recommendation\": \"paragraph with specific improvements\"\n"
    "}\n"
    "```\n"
    "Be thorough. Quote exact text from the prompt for every issue."
)

_CONTEXT_AUDITOR_PROMPT = (
    "You are a Context Auditor who performs deep CO-STAR framework analysis "
    "on prompts. You evaluate completeness with granular scoring.\n\n"
    "## CO-STAR Framework (score each 0 to 16.67, total = 100)\n\n"
    "**C - Context** (0-16.67)\n"
    "- Background info the AI needs to understand the task\n"
    "- Domain knowledge, user situation, prior context\n"
    "- Score 0 if absent, 8 if vague, 16.67 if specific\n\n"
    "**O - Objective** (0-16.67)\n"
    "- The specific task, goal, or desired outcome\n"
    "- Should be measurable or verifiable\n"
    "- Score 0 if absent, 8 if vague ('help me'), 16.67 if precise\n\n"
    "**S - Style** (0-16.67)\n"
    "- Writing style: formal, casual, technical, academic, conversational\n"
    "- Score 0 if not mentioned\n\n"
    "**T - Tone** (0-16.67)\n"
    "- Emotional tone: professional, friendly, authoritative, empathetic\n"
    "- Score 0 if not mentioned\n\n"
    "**A - Audience** (0-16.67)\n"
    "- Who the output is for: beginners, experts, children, executives\n"
    "- Score 0 if not specified\n\n"
    "**R - Response Format** (0-16.67)\n"
    "- Expected format: JSON, markdown, bullet points, essay, code, table\n"
    "- Length expectations\n"
    "- Score 0 if not specified\n\n"
    "## Additional Checks\n"
    "- **Persona**: Does it assign a role? ('You are...', 'Act as...')\n"
    "- **Constraints**: Boundaries, exclusions, limitations\n"
    "- **Examples**: Few-shot examples provided?\n"
    "- **Edge cases**: Does it handle unexpected inputs?\n"
    "- **Error handling**: What should the AI do when uncertain?\n\n"
    "## Output Format\n"
    "Return a JSON object:\n"
    "```json\n"
    "{\n"
    "  \"completeness_score\": 0-100,\n"
    "  \"costar_breakdown\": {\n"
    "    \"C\": {\"score\": 0-16.67, \"status\": \"present|partial|missing\", "
    "\"found\": \"quoted text or null\", \"missing\": \"what should be added\"},\n"
    "    \"O\": {\"score\": ..., \"status\": ..., \"found\": ..., \"missing\": ...},\n"
    "    \"S\": {...}, \"T\": {...}, \"A\": {...}, \"R\": {...}\n"
    "  },\n"
    "  \"missing_components\": [\"list of missing elements\"],\n"
    "  \"has_persona\": bool,\n"
    "  \"has_output_format\": bool,\n"
    "  \"has_constraints\": bool,\n"
    "  \"has_examples\": bool,\n"
    "  \"has_edge_case_handling\": bool,\n"
    "  \"has_error_handling\": bool,\n"
    "  \"component_suggestions\": [\n"
    "    {\"component\": \"C|O|S|T|A|R\", \"suggestion\": \"specific text to add\"}\n"
    "  ],\n"
    "  \"recommendation\": \"detailed improvement paragraph\"\n"
    "}\n"
    "```\n"
    "Quote exact text from the prompt for each 'found' field. Be specific in suggestions."
)

_DOMAIN_SPECIALIST_PROMPT = (
    "You are a Domain Specialist who evaluates prompts through domain-specific "
    "lenses. You detect the domain and apply expert-level scrutiny.\n\n"
    "## Step 1: Domain Detection\n"
    "Classify the prompt's primary domain:\n"
    "- **Code/Technical**: programming, APIs, databases, algorithms, DevOps\n"
    "- **Writing/Creative**: content, copy, blog, story, marketing, email\n"
    "- **Data/Analytics**: analysis, reports, dashboards, SQL, statistics\n"
    "- **Customer-facing**: support, chatbot, FAQ, onboarding\n"
    "- **General/Other**: everything else\n"
    "State your confidence: high / medium / low\n\n"
    "## Step 2: Domain Checklist\n"
    "Apply the relevant checklist. Mark each item pass/fail with explanation.\n\n"
    "### Code/Technical Checklist\n"
    "- [ ] Technology stack specified (language, framework, version)\n"
    "- [ ] Error handling requirements defined\n"
    "- [ ] Edge case coverage mentioned\n"
    "- [ ] Security considerations addressed\n"
    "- [ ] Performance constraints specified\n"
    "- [ ] Testing requirements included\n"
    "- [ ] Code style preferences (types, docs, naming)\n"
    "- [ ] Input/output examples provided\n\n"
    "### Writing/Creative Checklist\n"
    "- [ ] Voice and tone clearly defined\n"
    "- [ ] Target audience specified\n"
    "- [ ] Content structure requirements\n"
    "- [ ] Brand/style guidelines referenced\n"
    "- [ ] Call-to-action defined\n"
    "- [ ] Length/word count specified\n"
    "- [ ] SEO/keyword requirements\n"
    "- [ ] Example outputs provided\n\n"
    "### General/Other Checklist\n"
    "- [ ] Task decomposition clear\n"
    "- [ ] Success criteria defined\n"
    "- [ ] Scope boundaries established\n"
    "- [ ] Input format specified\n"
    "- [ ] Output format specified\n"
    "- [ ] Error/edge case handling\n\n"
    "## Step 3: Risk Assessment\n"
    "What could go wrong with this prompt?\n"
    "- Misinterpretation risks\n"
    "- Missing guardrails\n"
    "- Potential for harmful/off-topic output\n\n"
    "## Output Format\n"
    "Return a JSON object:\n"
    "```json\n"
    "{\n"
    "  \"precision_score\": 0-100,\n"
    "  \"detected_domain\": \"string\",\n"
    "  \"domain_confidence\": \"high|medium|low\",\n"
    "  \"checklist\": [{\"item\": \"...\", \"status\": \"pass|fail\", "
    "\"detail\": \"explanation\"}],\n"
    "  \"domain_specific_issues\": [{\"issue\": \"...\", "
    "\"severity\": \"critical|major|minor\", \"fix\": \"...\"}],\n"
    "  \"missing_specifications\": [\"what should be added\"],\n"
    "  \"risks\": [{\"risk\": \"...\", \"likelihood\": \"high|medium|low\", "
    "\"mitigation\": \"...\"}],\n"
    "  \"recommendation\": \"detailed improvement paragraph\"\n"
    "}\n"
    "```"
)

_REPORT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Prompt Evaluation Report</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#f8f9fa;color:#1a1a1a;line-height:1.5;padding:32px 16px}
.page{max-width:960px;margin:0 auto}
h1{font-size:1.4rem;font-weight:700;margin-bottom:4px}
.sub{color:#666;font-size:.85rem;margin-bottom:24px}
.meta{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px;font-size:.8rem;color:#666}
.section{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:20px 24px;margin-bottom:16px}
.section h2{font-size:1rem;font-weight:700;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #eee}
.section h3{font-size:.88rem;font-weight:600;margin:14px 0 6px;color:#333}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;font-weight:600;padding:8px 12px;background:#f8f9fa;border-bottom:2px solid #e0e0e0;font-size:.78rem;text-transform:uppercase;color:#666}
td{padding:8px 12px;border-bottom:1px solid #f0f0f0;vertical-align:top}
.score-row{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:8px}
.score-item{display:flex;align-items:baseline;gap:6px}
.score-label{font-size:.82rem;color:#666;font-weight:500}
.score-val{font-size:1.3rem;font-weight:700}
.score-val.high{color:#16a34a}
.score-val.mid{color:#d97706}
.score-val.low{color:#dc2626}
.tag{display:inline-block;font-size:.78rem;padding:3px 10px;border-radius:4px;margin:2px 4px 2px 0;background:#fef2f2;color:#dc2626}
.tag.ok{background:#f0fdf4;color:#16a34a}
.tag.warn{background:#fffbeb;color:#d97706}
.tag.info{background:#eff6ff;color:#2563eb}
.sev-critical{background:#dc2626;color:#fff;font-weight:700}
.sev-major{background:#fef2f2;color:#dc2626;font-weight:600}
.sev-minor{background:#fffbeb;color:#d97706}
.sev-suggestion{background:#eff6ff;color:#2563eb}
.pass{color:#16a34a;font-weight:600}
.fail{color:#dc2626;font-weight:600}
.check-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 16px;margin:8px 0}
.check-item{font-size:.85rem;padding:4px 0}
.check-item .ci{margin-right:6px}
.costar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin:8px 0}
.costar-card{border:1px solid #e0e0e0;border-radius:6px;padding:12px;font-size:.85rem}
.costar-card .cc-label{font-weight:700;font-size:.82rem;margin-bottom:4px;display:flex;justify-content:space-between}
.costar-card .cc-score{font-weight:700}
.costar-card .cc-found{color:#16a34a;margin:4px 0}
.costar-card .cc-missing{color:#dc2626;margin:4px 0}
.prompt-box{background:#1e1e1e;color:#d4d4d4;padding:16px 20px;border-radius:6px;font-family:monospace;font-size:.85rem;line-height:1.6;white-space:pre-wrap;margin-top:8px}
.trace-item{padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:.85rem}
.trace-item:last-child{border-bottom:none}
.trace-tool{font-weight:600;margin-right:8px}
.trace-badge{display:inline-block;font-size:.72rem;font-weight:600;padding:2px 8px;border-radius:4px;margin-left:4px}
.trace-badge.skill{background:#eff6ff;color:#2563eb}
.trace-badge.spawn{background:#faf5ff;color:#7c3aed}
.trace-badge.mem{background:#fffbeb;color:#d97706}
.trace-badge.err{background:#fef2f2;color:#dc2626}
.toggle{color:#2563eb;cursor:pointer;font-size:.8rem;font-weight:500;margin-left:8px}
.toggle:hover{text-decoration:underline}
.hidden{display:none}
pre.detail{background:#f8f9fa;border:1px solid #e0e0e0;border-radius:4px;padding:10px 12px;margin-top:6px;font-size:.78rem;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto}
.kv-table{margin:6px 0}
.kv-table td:first-child{font-weight:600;color:#666;white-space:nowrap;width:180px}
.text-block{background:#f8f9fa;border:1px solid #e0e0e0;border-radius:4px;padding:12px 16px;font-size:.88rem;line-height:1.6;margin:8px 0}
.quote{border-left:3px solid #d97706;padding:4px 12px;margin:4px 0;background:#fffbeb;font-size:.84rem;font-style:italic}
.fix-text{font-size:.84rem;color:#16a34a;margin:2px 0}
</style>
</head>
<body>
<div class="page">
<h1>Prompt Evaluation Report</h1>
<p class="sub">Multi-agent evaluation with simulation testing</p>
<div class="meta" id="meta"></div>
<div id="content"></div>
</div>
<script>
var RAW = {{ json_stringify($json) }};
var TC = RAW ? (RAW.toolCalls||[]) : [];
var ITERS = RAW ? (RAW.iterations||0) : 0;
function esc(s){return s?String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';}
function extractJson(md){
    if(!md) return null;
    var parts=md.split('```json');
    for(var i=1;i<parts.length;i++){
        var end=parts[i].indexOf('```');
        if(end<0) continue;
        try{var j=JSON.parse(parts[i].substring(0,end).trim());if(j&&typeof j==='object') return j;}catch(e){}
    }
    try{return JSON.parse(md);}catch(e){}
    return null;
}
function normalize(raw){
    if(!raw) return null;
    var d=JSON.parse(JSON.stringify(raw));
    if(!d.scores&&d.clarity!=null){
        d.scores={clarity:d.clarity,completeness:d.completeness,precision:d.precision,simulation_pass_rate:d.simulation_pass_rate,overall:d.overall};
    }
    if(!d.simulation&&d.simulation_results){
        d.simulation={utterance_results:d.simulation_results};
    }
    return d;
}
var D=RAW?RAW.structured:null;
if(!D||!Object.keys(D).length) D=extractJson(RAW?RAW.response:null);
D=normalize(D);
function sc(v){return v>=75?'high':v>=45?'mid':'low';}
var tid=0;
function tog(id){var e=document.getElementById(id);if(e)e.classList.toggle('hidden');}
function esc(s){return s?String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';}
function renderVal(v){
    if(v===null||v===undefined) return '<span style="color:#999">null</span>';
    if(typeof v==='boolean') return v?'<span class="pass">true</span>':'<span class="fail">false</span>';
    if(typeof v==='number') return String(v);
    if(typeof v==='string') return esc(v);
    if(Array.isArray(v)){
        if(!v.length) return '<span style="color:#999">[]</span>';
        if(typeof v[0]==='string') return v.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join(' ');
        return '<pre class="detail">'+esc(JSON.stringify(v,null,2))+'</pre>';
    }
    return '<pre class="detail">'+esc(JSON.stringify(v,null,2))+'</pre>';
}
function kvTable(obj,skip){
    if(!obj||typeof obj!=='object') return '';
    var keys=Object.keys(obj);
    if(!keys.length) return '';
    var h='<table class="kv-table">';
    keys.forEach(function(k){
        if(skip&&skip.indexOf(k)>=0) return;
        h+='<tr><td>'+esc(k)+'</td><td>'+renderVal(obj[k])+'</td></tr>';
    });
    return h+'</table>';
}
function sevBadge(sev){
    var s=(sev||'').toLowerCase();
    return '<span class="tag sev-'+s+'">'+esc(sev||'issue')+'</span>';
}
function boolIcon(v){return v?'<span class="ci pass">&#10003;</span>':'<span class="ci fail">&#10007;</span>';}
function render(){
    var meta=document.getElementById('meta');
    var parts=[];
    parts.push(ITERS+' iterations');
    parts.push(TC.length+' tool calls');
    parts.push(new Date().toLocaleDateString());
    meta.textContent=parts.join(' | ');
    var h='';
    if(!D){h='<div class="section"><p style="color:#999">No structured data returned.</p></div>';document.getElementById('content').innerHTML=h;return;}

    /* ── Scores ── */
    var s=D.scores||{};
    var scoreKeys=['overall','clarity','completeness','precision','simulation_pass_rate'];
    var hasScores=scoreKeys.some(function(k){return s[k]!=null;});
    if(hasScores){
        h+='<div class="section"><h2>Scores</h2><div class="score-row">';
        scoreKeys.forEach(function(k){
            if(s[k]==null) return;
            var label=k.replace(/_/g,' ').replace(/\\b\\w/g,function(c){return c.toUpperCase();});
            h+='<div class="score-item"><span class="score-val '+sc(s[k])+'">'+s[k]+'</span><span class="score-label">'+label+'</span></div>';
        });
        h+='</div></div>';
    }

    /* ── Summary ── */
    if(D.summary){
        h+='<div class="section"><h2>Summary</h2><div class="text-block">'+esc(D.summary)+'</div></div>';
    }

    /* ── Structure Evaluation ── */
    var se=D.structure_evaluation;
    if(se&&typeof se==='object'){
        h+='<div class="section"><h2>Structure Evaluation</h2>';
        if(se.format) h+='<p style="margin-bottom:8px"><strong>Format:</strong> <span class="tag info">'+esc(se.format)+'</span>';
        if(se.information_density) h+=' <strong>Density:</strong> <span class="tag info">'+esc(se.information_density)+'</span>';
        if(se.readability) h+=' <strong>Readability:</strong> <span class="tag info">'+esc(se.readability)+'</span>';
        h+='</p>';
        if(se.word_count!=null||se.estimated_token_count!=null){
            h+='<p style="margin-bottom:8px;font-size:.85rem">';
            if(se.word_count!=null) h+='<strong>Words:</strong> '+se.word_count+'  ';
            if(se.estimated_token_count!=null) h+='<strong>Est. tokens:</strong> '+se.estimated_token_count;
            h+='</p>';
        }
        var checks=['has_sections','has_bullet_points','has_numbered_steps','has_examples','has_persona','has_output_format','has_constraints','has_edge_cases'];
        var hasChecks=checks.some(function(k){return se[k]!=null;});
        if(hasChecks){
            h+='<div class="check-grid">';
            checks.forEach(function(k){
                if(se[k]==null) return;
                var label=k.replace(/^has_/,'').replace(/_/g,' ');
                h+='<div class="check-item">'+boolIcon(se[k])+label+'</div>';
            });
            h+='</div>';
        }
        if(se.verdict) h+='<div class="text-block">'+esc(se.verdict)+'</div>';
        var seKnown=['format','has_sections','has_bullet_points','has_numbered_steps','has_examples','has_persona','has_output_format','has_constraints','has_edge_cases','word_count','estimated_token_count','information_density','readability','verdict'];
        Object.keys(se).forEach(function(k){
            if(seKnown.indexOf(k)>=0) return;
            h+='<p style="font-size:.85rem"><strong>'+esc(k)+':</strong> '+renderVal(se[k])+'</p>';
        });
        h+='</div>';
    }

    /* ── Issues (master list) ── */
    var issues=D.issues;
    if(issues&&issues.length){
        h+='<div class="section"><h2>Issues ('+issues.length+')</h2>';
        h+='<table><thead><tr><th>Severity</th><th>Issue</th><th>Source</th><th>Fix</th></tr></thead><tbody>';
        issues.forEach(function(iss){
            h+='<tr><td>'+sevBadge(iss.severity)+'</td>';
            h+='<td>'+esc(iss.issue);
            if(iss.quote) h+='<div class="quote">'+esc(iss.quote)+'</div>';
            h+='</td>';
            h+='<td style="font-size:.82rem">'+esc(iss.source||'')+'</td>';
            h+='<td class="fix-text">'+esc(iss.fix||'')+'</td></tr>';
        });
        h+='</tbody></table></div>';
    }

    /* ── Analysis ── */
    var a=D.analysis||{};
    if(Object.keys(a).length>0){
        h+='<div class="section"><h2>Analysis</h2>';

        /* Structural Linguist */
        var sl=a.structural_linguist;
        if(sl&&typeof sl==='object'){
            h+='<h3>Structural Linguist';
            if(sl.clarity_score!=null) h+=' <span class="score-val '+sc(sl.clarity_score)+'" style="font-size:1rem;margin-left:8px">'+sl.clarity_score+'</span>';
            h+='</h3>';
            var slMeta=[];
            if(sl.format_rating) slMeta.push('<span class="tag info">'+esc(sl.format_rating)+'</span>');
            if(sl.signal_to_noise) slMeta.push('S/N: <strong>'+esc(sl.signal_to_noise)+'</strong>');
            if(sl.word_count!=null) slMeta.push(sl.word_count+' words');
            if(slMeta.length) h+='<p style="margin-bottom:8px;font-size:.85rem">'+slMeta.join(' &middot; ')+'</p>';
            if(sl.strengths&&sl.strengths.length){
                h+='<p style="margin-bottom:6px"><strong>Strengths:</strong> '+sl.strengths.map(function(x){return '<span class="tag ok">'+esc(x)+'</span>';}).join(' ')+'</p>';
            }
            if(sl.issues&&sl.issues.length){
                h+='<table style="margin:6px 0"><thead><tr><th>Severity</th><th>Issue</th><th>Fix</th></tr></thead><tbody>';
                sl.issues.forEach(function(iss){
                    h+='<tr><td>'+sevBadge(iss.severity)+'</td><td>'+esc(iss.issue||'');
                    if(iss.quote) h+='<div class="quote">'+esc(iss.quote)+'</div>';
                    h+='</td><td class="fix-text">'+esc(iss.fix||'')+'</td></tr>';
                });
                h+='</tbody></table>';
            }
            if(sl.vague_terms&&sl.vague_terms.length){
                h+='<p><strong>Vague terms:</strong> '+sl.vague_terms.map(function(x){return '<span class="tag warn">'+esc(x)+'</span>';}).join(' ')+'</p>';
            }
            if(sl.lazy_words&&sl.lazy_words.length){
                h+='<p><strong>Lazy words:</strong> '+sl.lazy_words.map(function(x){return '<span class="tag warn">'+esc(x)+'</span>';}).join(' ')+'</p>';
            }
            if(sl.negative_constraints&&sl.negative_constraints.length){
                h+='<table style="margin:6px 0"><thead><tr><th>Negative (original)</th><th>Positive (rewrite)</th></tr></thead><tbody>';
                sl.negative_constraints.forEach(function(nc){
                    h+='<tr><td><span class="fail">'+esc(nc.original)+'</span></td><td><span class="pass">'+esc(nc.rewrite)+'</span></td></tr>';
                });
                h+='</tbody></table>';
            }
            if(sl.recommendation) h+='<div class="text-block">'+esc(sl.recommendation)+'</div>';
        } else if(a.clarity_issues&&a.clarity_issues.length){
            h+='<h3>Structural Linguist</h3><p><strong>Issues:</strong> '+a.clarity_issues.map(function(x){return '<span class="tag">'+esc(typeof x==='string'?x:x.issue||JSON.stringify(x))+'</span>';}).join(' ')+'</p>';
        }

        /* Context Auditor */
        var ca=a.context_auditor;
        if(ca&&typeof ca==='object'){
            h+='<h3>Context Auditor';
            if(ca.completeness_score!=null) h+=' <span class="score-val '+sc(ca.completeness_score)+'" style="font-size:1rem;margin-left:8px">'+ca.completeness_score+'</span>';
            h+='</h3>';
            /* CO-STAR breakdown */
            var costar=ca.costar_breakdown;
            if(costar&&typeof costar==='object'){
                var costarKeys=['C','O','S','T','A','R'];
                var costarLabels={C:'Context',O:'Objective',S:'Style',T:'Tone',A:'Audience',R:'Response Format'};
                h+='<div class="costar-grid">';
                costarKeys.forEach(function(k){
                    var c=costar[k];
                    if(!c) return;
                    var statusCls=c.status==='present'?'pass':(c.status==='partial'?'':'fail');
                    h+='<div class="costar-card">';
                    h+='<div class="cc-label"><span>'+k+' - '+(costarLabels[k]||k)+'</span>';
                    if(c.score!=null) h+='<span class="cc-score '+sc(c.score*6)+'">'+c.score+'</span>';
                    h+='</div>';
                    if(c.status) h+='<span class="tag '+(c.status==='present'?'ok':(c.status==='partial'?'warn':''))+'">'+esc(c.status)+'</span>';
                    if(c.found) h+='<div class="cc-found"><strong>Found:</strong> '+esc(c.found)+'</div>';
                    if(c.missing) h+='<div class="cc-missing"><strong>Missing:</strong> '+esc(c.missing)+'</div>';
                    h+='</div>';
                });
                h+='</div>';
            }
            /* Checks */
            var caChecks=['has_persona','has_output_format','has_constraints','has_examples','has_edge_case_handling','has_error_handling'];
            var hasCaChecks=caChecks.some(function(k){return ca[k]!=null;});
            if(hasCaChecks){
                h+='<div class="check-grid">';
                caChecks.forEach(function(k){
                    if(ca[k]==null) return;
                    var label=k.replace(/^has_/,'').replace(/_/g,' ');
                    h+='<div class="check-item">'+boolIcon(ca[k])+label+'</div>';
                });
                h+='</div>';
            }
            if(ca.missing_components&&ca.missing_components.length){
                h+='<p style="margin:6px 0"><strong>Missing:</strong> '+ca.missing_components.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join(' ')+'</p>';
            }
            if(ca.component_suggestions&&ca.component_suggestions.length){
                h+='<h4 style="font-size:.84rem;margin:8px 0 4px">Suggestions</h4>';
                ca.component_suggestions.forEach(function(cs){
                    h+='<p style="font-size:.85rem;margin:2px 0"><span class="tag info">'+esc(cs.component||'')+'</span> '+esc(cs.suggestion||'')+'</p>';
                });
            }
            if(ca.recommendation) h+='<div class="text-block">'+esc(ca.recommendation)+'</div>';
        } else if(a.missing_components&&a.missing_components.length){
            h+='<h3>Context Auditor</h3><p><strong>Missing:</strong> '+a.missing_components.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join(' ')+'</p>';
        }

        /* Domain Specialist */
        var ds=a.domain_specialist;
        if(ds&&typeof ds==='object'){
            h+='<h3>Domain Specialist';
            if(ds.precision_score!=null) h+=' <span class="score-val '+sc(ds.precision_score)+'" style="font-size:1rem;margin-left:8px">'+ds.precision_score+'</span>';
            h+='</h3>';
            var dsMeta=[];
            if(ds.detected_domain) dsMeta.push('<span class="tag info">'+esc(ds.detected_domain)+'</span>');
            if(ds.domain_confidence) dsMeta.push('Confidence: <strong>'+esc(ds.domain_confidence)+'</strong>');
            if(dsMeta.length) h+='<p style="margin-bottom:8px;font-size:.85rem">'+dsMeta.join(' &middot; ')+'</p>';
            /* Checklist */
            if(ds.checklist&&ds.checklist.length){
                h+='<table style="margin:6px 0"><thead><tr><th>Item</th><th>Status</th><th>Detail</th></tr></thead><tbody>';
                ds.checklist.forEach(function(ci){
                    h+='<tr><td>'+esc(ci.item)+'</td>';
                    h+='<td>'+(ci.status==='pass'?'<span class="pass">Pass</span>':'<span class="fail">Fail</span>')+'</td>';
                    h+='<td style="font-size:.84rem">'+esc(ci.detail||'')+'</td></tr>';
                });
                h+='</tbody></table>';
            }
            if(ds.domain_specific_issues&&ds.domain_specific_issues.length){
                h+='<table style="margin:6px 0"><thead><tr><th>Severity</th><th>Issue</th><th>Fix</th></tr></thead><tbody>';
                ds.domain_specific_issues.forEach(function(iss){
                    h+='<tr><td>'+sevBadge(iss.severity)+'</td><td>'+esc(iss.issue||'')+'</td><td class="fix-text">'+esc(iss.fix||'')+'</td></tr>';
                });
                h+='</tbody></table>';
            }
            if(ds.missing_specifications&&ds.missing_specifications.length){
                h+='<p style="margin:6px 0"><strong>Missing specs:</strong> '+ds.missing_specifications.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join(' ')+'</p>';
            }
            /* Risks */
            if(ds.risks&&ds.risks.length){
                h+='<h4 style="font-size:.84rem;margin:8px 0 4px">Risks</h4>';
                h+='<table><thead><tr><th>Risk</th><th>Likelihood</th><th>Mitigation</th></tr></thead><tbody>';
                ds.risks.forEach(function(r){
                    h+='<tr><td>'+esc(r.risk||'')+'</td>';
                    var lc=(r.likelihood||'').toLowerCase();
                    h+='<td><span class="tag '+(lc==='high'?'':(lc==='low'?'ok':'warn'))+'">'+esc(r.likelihood||'')+'</span></td>';
                    h+='<td style="font-size:.84rem">'+esc(r.mitigation||'')+'</td></tr>';
                });
                h+='</tbody></table>';
            }
            if(ds.recommendation) h+='<div class="text-block">'+esc(ds.recommendation)+'</div>';
        } else if(a.domain_feedback){
            h+='<h3>Domain Specialist</h3><div class="text-block">'+esc(a.domain_feedback)+'</div>';
        }

        /* Theory vs Practice */
        if(a.theory_vs_practice){
            h+='<h3>Theory vs Practice</h3><div class="text-block">'+esc(a.theory_vs_practice)+'</div>';
        }

        /* Any other analysis keys */
        var knownKeys=['structural_linguist','context_auditor','domain_specialist','clarity_issues','missing_components','domain_feedback','domain_issues','theory_vs_practice'];
        Object.keys(a).forEach(function(k){
            if(knownKeys.indexOf(k)>=0) return;
            h+='<h3>'+esc(k)+'</h3>'+renderVal(a[k]);
        });
        h+='</div>';
    }

    /* ── Simulation ── */
    var sim=D.simulation||{};
    var ur=sim.utterance_results||[];
    if(ur.length){
        var pc=sim.pass_count!=null?sim.pass_count:ur.filter(function(u){return u.on_topic&&u.format_match&&!u.refusal;}).length;
        var fc=sim.fail_count!=null?sim.fail_count:ur.length-pc;
        h+='<div class="section"><h2>Simulation Results</h2>';
        h+='<p style="margin-bottom:12px"><span class="pass">'+pc+' passed</span> / <span class="fail">'+fc+' failed</span> / '+ur.length+' total</p>';
        if(sim.overall_issues&&sim.overall_issues.length){
            h+='<p style="margin-bottom:12px"><strong>Issues:</strong> '+sim.overall_issues.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join(' ')+'</p>';
        }
        h+='<table><thead><tr><th>Utterance</th><th>Output</th><th>On Topic</th><th>Format</th><th>Refusal</th><th>Quality</th><th>Issues</th></tr></thead><tbody>';
        ur.forEach(function(u){
            h+='<tr><td><strong>'+esc(u.utterance)+'</strong></td>';
            h+='<td style="white-space:pre-wrap;max-width:280px">'+esc(u.output)+'</td>';
            h+='<td>'+(u.on_topic?'<span class="pass">Yes</span>':'<span class="fail">No</span>')+'</td>';
            h+='<td>'+(u.format_match?'<span class="pass">Yes</span>':'<span class="fail">No</span>')+'</td>';
            h+='<td>'+(u.refusal?'<span class="fail">Yes</span>':'<span class="pass">No</span>')+'</td>';
            h+='<td>'+(u.quality_rating!=null?'<span class="score-val '+sc(u.quality_rating*20)+'" style="font-size:.9rem">'+u.quality_rating+'/5</span>':'<span style="color:#999">-</span>')+'</td>';
            h+='<td>'+(u.issues&&u.issues.length?u.issues.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join(' '):'<span style="color:#999">-</span>')+'</td></tr>';
        });
        h+='</tbody></table></div>';
    }

    /* ── Perfect Prompt ── */
    if(D.perfect_prompt){
        h+='<div class="section"><h2>Suggested Prompt</h2><div class="prompt-box">'+esc(D.perfect_prompt)+'</div></div>';
    }

    /* ── Any other top-level keys ── */
    var topKnown=['scores','summary','structure_evaluation','issues','analysis','simulation','simulation_results','perfect_prompt','clarity','completeness','precision','simulation_pass_rate','overall'];
    Object.keys(D).forEach(function(k){
        if(topKnown.indexOf(k)>=0) return;
        h+='<div class="section"><h2>'+esc(k)+'</h2>'+renderVal(D[k])+'</div>';
    });

    /* ── Agent Trace ── */
    if(TC.length){
        h+='<div class="section"><h2>Agent Trace ('+TC.length+' calls, '+ITERS+' iterations)</h2>';
        TC.forEach(function(tc,idx){
            var tool=tc.tool||'';
            var inp=tc.input||{};
            var out=tc.output||{};
            var isErr=tc.is_error;
            var id='t'+(tid++);
            h+='<div class="trace-item">';
            h+='<span class="trace-tool">#'+(idx+1)+' '+esc(tool)+'</span>';
            if(tool==='delegate_to_skill') h+='<span class="trace-badge skill">'+esc(inp.skill||'')+'</span>';
            else if(tool==='spawn_agent') h+='<span class="trace-badge spawn">spawn</span>';
            else if(tool==='memory_store') h+='<span class="trace-badge mem">'+esc(inp.key||'')+'</span>';
            else if(tool==='memory_recall') h+='<span class="trace-badge mem">recall</span>';
            if(isErr) h+='<span class="trace-badge err">error</span>';
            h+='<span class="toggle" onclick="tog(&#39;'+id+'&#39;)">details</span>';
            h+='<div id="'+id+'" class="hidden">';
            h+='<pre class="detail">Input: '+esc(JSON.stringify(inp,null,2))+'</pre>';
            h+='<pre class="detail">Output: '+esc(JSON.stringify(out,null,2))+'</pre>';
            h+='</div></div>';
        });
        h+='</div>';
    }

    /* ── Raw JSON ── */
    var rawId='t'+(tid++);
    h+='<div class="section"><h2>Raw Data <span class="toggle" onclick="tog(&#39;'+rawId+'&#39;)">show/hide</span></h2>';
    h+='<pre class="detail hidden" id="'+rawId+'" style="max-height:500px">'+esc(JSON.stringify(RAW,null,2))+'</pre></div>';
    document.getElementById('content').innerHTML=h;
}
render();
</script>
</body>
</html>"""

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
                "structural_linguist": {
                    "type": "object",
                    "description": "Full output from structural linguist skill",
                    "properties": {
                        "clarity_score": {"type": "number"},
                        "format_rating": {"type": "string"},
                        "word_count": {"type": "number"},
                        "signal_to_noise": {"type": "string"},
                        "issues": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "issue": {"type": "string"},
                                "quote": {"type": "string"},
                                "severity": {"type": "string"},
                                "fix": {"type": "string"},
                            },
                        }},
                        "vague_terms": {"type": "array", "items": {"type": "string"}},
                        "negative_constraints": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "original": {"type": "string"},
                                "rewrite": {"type": "string"},
                            },
                        }},
                        "lazy_words": {"type": "array", "items": {"type": "string"}},
                        "strengths": {"type": "array", "items": {"type": "string"}},
                        "recommendation": {"type": "string"},
                    },
                },
                "context_auditor": {
                    "type": "object",
                    "description": "Full output from context auditor skill",
                    "properties": {
                        "completeness_score": {"type": "number"},
                        "costar_breakdown": {
                            "type": "object",
                            "description": "C, O, S, T, A, R each with score/status/found/missing",
                        },
                        "missing_components": {"type": "array", "items": {"type": "string"}},
                        "has_persona": {"type": "boolean"},
                        "has_output_format": {"type": "boolean"},
                        "has_constraints": {"type": "boolean"},
                        "has_examples": {"type": "boolean"},
                        "has_edge_case_handling": {"type": "boolean"},
                        "has_error_handling": {"type": "boolean"},
                        "component_suggestions": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "component": {"type": "string"},
                                "suggestion": {"type": "string"},
                            },
                        }},
                        "recommendation": {"type": "string"},
                    },
                },
                "domain_specialist": {
                    "type": "object",
                    "description": "Full output from domain specialist skill",
                    "properties": {
                        "precision_score": {"type": "number"},
                        "detected_domain": {"type": "string"},
                        "domain_confidence": {"type": "string"},
                        "checklist": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "status": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                        }},
                        "domain_specific_issues": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "issue": {"type": "string"},
                                "severity": {"type": "string"},
                                "fix": {"type": "string"},
                            },
                        }},
                        "missing_specifications": {"type": "array", "items": {"type": "string"}},
                        "risks": {"type": "array", "items": {
                            "type": "object",
                            "properties": {
                                "risk": {"type": "string"},
                                "likelihood": {"type": "string"},
                                "mitigation": {"type": "string"},
                            },
                        }},
                        "recommendation": {"type": "string"},
                    },
                },
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
                            "quality_rating": {"type": "number", "description": "1-5"},
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
                    "source": {"type": "string", "description": "which analyst(s)"},
                    "quote": {"type": "string"},
                    "fix": {"type": "string"},
                },
            },
        },
        "perfect_prompt": {"type": "string"},
        "summary": {"type": "string"},
    },
}, indent=2)


EXAMPLE_WORKFLOWS = [
    # ── Upload Dataset ──────────────────────────────────────────────
    # Simple pipeline: receive data via webhook, upload to analytics
    # service, return dataset_id + metadata. No LLM involved.
    {
        "name": "Upload Dataset",
        "description": (
            "Upload a dataset via POST /upload-data. Forwards to the analytics service, "
            "stores as parquet, returns dataset_id, row/column counts, column types, and "
            "a 5-row preview. Use the returned dataset_id with the Query Dataset workflow."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {
                        "method": "POST",
                        "path": "upload-data",
                        "responseMode": "lastNode",
                    },
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": '{{ $json.body or {"data": [{"name": "Alice", "age": 30, "salary": 70000, "department": "Engineering"}, {"name": "Bob", "age": 25, "salary": 55000, "department": "Marketing"}]} }}',
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Upload",
                    "type": "HttpRequest",
                    "parameters": {
                        "method": "POST",
                        "url": "http://localhost:8001/upload",
                        "headers": [
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                        "body": '{{ $json }}',
                        "responseType": "json",
                    },
                    "position": {"x": 600, "y": 300},
                },
                {
                    "name": "Respond",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "contentType": "application/json",
                        "wrapResponse": False,
                    },
                    "position": {"x": 850, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Upload"},
                {"source_node": "Upload", "target_node": "Respond"},
            ],
            "settings": {},
        },
    },
    # ── Query Dataset ─────────────────────────────────────────────
    # Receives {dataset_id, question}, fetches metadata, then hands
    # off to the AI agent.  No raw data touches the LLM.
    {
        "name": "Query Dataset",
        "description": (
            "AI data analyst: POST /query-data with {dataset_id, question}. "
            "Fetches dataset metadata, then the agent uses profile, aggregate, sample, "
            "and report tools — all by reference. Returns structured JSON with answer, "
            "findings, quality assessment, and methodology."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {
                        "method": "POST",
                        "path": "query-data",
                        "responseMode": "lastNode",
                    },
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": '{{ $json.body or {"dataset_id": "ds_demo", "question": "Summarize this dataset"} }}',
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Fetch Metadata",
                    "type": "HttpRequest",
                    "parameters": {
                        "method": "GET",
                        "url": "http://localhost:8001/datasets/{{ $json.dataset_id }}",
                        "responseType": "json",
                    },
                    "position": {"x": 550, "y": 300},
                },
                {
                    "name": "Data Analyst",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.5-flash",
                        "systemPrompt": _DATA_ANALYST_SYSTEM_PROMPT,
                        "task": (
                            "Analyze this dataset and answer the question.\n\n"
                            "Dataset ID: {{ $json.body.dataset_id }}\n"
                            "Rows: {{ $json.body.row_count }}\n"
                            "Columns: {{ json_stringify($json.body.columns) }}\n"
                            "Preview (first 5 rows): {{ json_stringify($json.body.preview) }}\n\n"
                            "Question: {{ $node['Input'].json.question }}"
                        ),
                        "maxIterations": 10,
                        "temperature": 0.2,
                        "enableSubAgents": False,
                        "enablePlanning": True,
                        "enableScratchpad": True,
                        "outputSchema": _DATA_ANALYST_OUTPUT_SCHEMA,
                        "skillProfiles": [
                            {
                                "name": "data_quality_auditor",
                                "description": "Audit data quality: missing values, outliers, duplicates, type mismatches",
                                "systemPrompt": (
                                    "You are a Data Quality Auditor. Always pass dataset_id to tools, "
                                    "never raw data. Use profile_data to check column types, null rates, "
                                    "and distributions. Use run_code for custom checks like duplicate "
                                    "detection and outlier flagging. Report a quality score "
                                    "(excellent/good/fair/poor) with specific issues."
                                ),
                                "toolNames": "profile_data,run_code,calculator",
                                "outputSchema": "",
                            },
                            {
                                "name": "statistical_analyst",
                                "description": "Compute distributions, variance, percentiles, and statistical summaries",
                                "systemPrompt": (
                                    "You are a Statistical Analyst. Always pass dataset_id to tools, "
                                    "never raw data. Use profile_data for distributions and "
                                    "aggregate_data for grouped statistics. Compute means, medians, "
                                    "standard deviations, percentiles, and inter-quartile ranges. Use "
                                    "run_code for custom statistical tests."
                                ),
                                "toolNames": "profile_data,aggregate_data,run_code,calculator",
                                "outputSchema": "",
                            },
                            {
                                "name": "trend_detector",
                                "description": "Detect group-by patterns, rankings, top-N, and Pareto distributions",
                                "systemPrompt": (
                                    "You are a Trend Detector. Always pass dataset_id to tools, "
                                    "never raw data. Use aggregate_data to group, rank, and compare "
                                    "segments. Use sample_data to inspect representative rows. Look for "
                                    "Pareto patterns (80/20 rule), outlier groups, and significant "
                                    "differences between segments."
                                ),
                                "toolNames": "aggregate_data,sample_data,run_code,calculator",
                                "outputSchema": "",
                            },
                            {
                                "name": "report_writer",
                                "description": "Generate formatted executive summary reports",
                                "systemPrompt": (
                                    "You are a Report Writer. Always pass dataset_id to tools, "
                                    "never raw data. Use generate_report to create a well-formatted "
                                    "executive summary. Include overview, key metrics, and "
                                    "recommendations. Default to markdown format."
                                ),
                                "toolNames": "generate_report,run_code",
                                "outputSchema": "",
                            },
                        ],
                    },
                    "position": {"x": 800, "y": 300},
                },
                {
                    "name": "Respond",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "contentType": "application/json",
                        "wrapResponse": True,
                    },
                    "position": {"x": 1100, "y": 300},
                },
                # Tool subnodes
                {"name": "Profile Tool", "type": "DataProfileTool", "parameters": {}, "position": {"x": 600, "y": 550}},
                {"name": "Aggregate Tool", "type": "DataAggregateTool", "parameters": {}, "position": {"x": 720, "y": 550}},
                {"name": "Sample Tool", "type": "DataSampleTool", "parameters": {}, "position": {"x": 840, "y": 550}},
                {"name": "Report Tool", "type": "DataReportTool", "parameters": {}, "position": {"x": 960, "y": 550}},
                {"name": "Code", "type": "CodeTool", "parameters": {}, "position": {"x": 720, "y": 650}},
                {"name": "Calculator", "type": "CalculatorTool", "parameters": {}, "position": {"x": 840, "y": 650}},
                # Model subnode
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.2, "maxTokens": 8192}, "position": {"x": 800, "y": 500}},
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Fetch Metadata"},
                {"source_node": "Fetch Metadata", "target_node": "Data Analyst"},
                {"source_node": "Data Analyst", "target_node": "Respond"},
                # Model subnode
                {"source_node": "Gemini 2.5 Flash", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "chatModel"},
                # Tool subnodes
                {"source_node": "Profile Tool", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Aggregate Tool", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Sample Tool", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Report Tool", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Code", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Calculator", "target_node": "Data Analyst", "connection_type": "subnode", "slot_name": "tools"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Prompt Evaluator",
        "description": (
            "Hub & spoke prompt evaluator: webhook-triggered orchestrator dispatches to "
            "3 skill sub-agents (structural linguist, context auditor, domain specialist) "
            "plus 1 dynamically spawned Simulator. Cross-references theoretical analysis "
            "against actual simulation output. Returns scored report with rewritten Perfect Prompt."
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
                        "enableSubAgents": True,
                        "maxAgentDepth": 2,
                        "allowRecursiveSpawn": False,
                        "enablePlanning": True,
                        "enableScratchpad": True,
                        "outputSchema": _PROMPT_EVAL_OUTPUT_SCHEMA,
                        "skillProfiles": [
                            {
                                "name": "structural_linguist",
                                "description": "Static analysis: grammar, syntax, ambiguity detection, vague quantifiers, negative constraints, lazy words. Scores clarity 0-100.",
                                "systemPrompt": _STRUCTURAL_LINGUIST_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "context_auditor",
                                "description": "CO-STAR framework analysis: Context, Objective, Style, Tone, Audience, Response format. Detects missing persona, output format, constraints. Scores completeness 0-100.",
                                "systemPrompt": _CONTEXT_AUDITOR_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "domain_specialist",
                                "description": "Dynamic expert: auto-detects domain (code/writing/general), applies domain-specific checks (stack definitions, error handling, voice/tone, audience). Scores precision 0-100.",
                                "systemPrompt": _DOMAIN_SPECIALIST_PROMPT,
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
                # Subnodes
                {"name": "Gemini Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.0-flash", "temperature": 0.3, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Prompt Evaluator"},
                {"source_node": "Prompt Evaluator", "target_node": "Respond"},
                # Subnode connections
                {"source_node": "Gemini Flash", "target_node": "Prompt Evaluator", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Prompt Evaluator v2",
        "description": (
            "HTML report version of the Prompt Evaluator. Same hub & spoke architecture "
            "(3 skill sub-agents + 1 spawned Simulator with test utterances) but returns "
            "a styled HTML report with score bars, per-utterance PASS/FAIL cards, analysis "
            "grid, and rewritten Perfect Prompt — viewable directly in the browser."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {
                        "method": "POST",
                        "path": "prompt-evaluator-v2",
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
                        "enableSubAgents": True,
                        "maxAgentDepth": 2,
                        "allowRecursiveSpawn": False,
                        "enablePlanning": True,
                        "enableScratchpad": True,
                        "outputSchema": _PROMPT_EVAL_OUTPUT_SCHEMA,
                        "skillProfiles": [
                            {
                                "name": "structural_linguist",
                                "description": "Static analysis: grammar, syntax, ambiguity detection, vague quantifiers, negative constraints, lazy words. Scores clarity 0-100.",
                                "systemPrompt": _STRUCTURAL_LINGUIST_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "context_auditor",
                                "description": "CO-STAR framework analysis: Context, Objective, Style, Tone, Audience, Response format. Detects missing persona, output format, constraints. Scores completeness 0-100.",
                                "systemPrompt": _CONTEXT_AUDITOR_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                            {
                                "name": "domain_specialist",
                                "description": "Dynamic expert: auto-detects domain (code/writing/general), applies domain-specific checks (stack definitions, error handling, voice/tone, audience). Scores precision 0-100.",
                                "systemPrompt": _DOMAIN_SPECIALIST_PROMPT,
                                "toolNames": "",
                                "outputSchema": "",
                            },
                        ],
                    },
                    "position": {"x": 650, "y": 300},
                },
                {
                    "name": "HTML Report",
                    "type": "HTMLDisplay",
                    "parameters": {
                        "content": _REPORT_HTML_TEMPLATE,
                    },
                    "position": {"x": 900, "y": 300},
                },
                {
                    "name": "Respond",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "contentType": "text/html",
                        "responseMode": "custom",
                        "responseBody": "{{ $json.html }}",
                        "wrapResponse": False,
                    },
                    "position": {"x": 1150, "y": 300},
                },
                # Subnodes
                {"name": "Gemini Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.0-flash", "temperature": 0.3, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Prompt Evaluator"},
                {"source_node": "Prompt Evaluator", "target_node": "HTML Report"},
                {"source_node": "HTML Report", "target_node": "Respond"},
                # Subnode connections
                {"source_node": "Gemini Flash", "target_node": "Prompt Evaluator", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
]


async def seed_workflows(reset: bool = False) -> None:
    """Seed the database with example workflows."""
    await init_db()

    async with async_session_factory() as session:
        if reset:
            await session.execute(delete(WorkflowModel))
            await session.commit()
            print("Cleared existing workflows.")

        result = await session.execute(select(WorkflowModel.name))
        existing_names = {row[0] for row in result.fetchall()}

        added = 0
        skipped = 0
        for workflow_data in EXAMPLE_WORKFLOWS:
            if workflow_data["name"] in existing_names:
                skipped += 1
                continue

            workflow_id = workflow_data.get("id") or generate_workflow_id(workflow_data["name"])

            if "definition" in workflow_data:
                definition = workflow_data["definition"]
            else:
                definition = {
                    "nodes": workflow_data.get("nodes", []),
                    "connections": workflow_data.get("connections", []),
                    "settings": workflow_data.get("settings", {}),
                }

            workflow = WorkflowModel(
                id=workflow_id,
                name=workflow_data["name"],
                description=workflow_data.get("description", ""),
                active=workflow_data.get("active", False),
                definition=definition,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(workflow)
            added += 1
            status = "ACTIVE" if workflow_data.get("active") else "inactive"
            print(f"Added [{status}]: {workflow_data['name']}")

        await session.commit()
        print(f"\nSeeding complete. Added {added} workflows" + (f", skipped {skipped} existing." if skipped else "."))


def main() -> None:
    """Run the seed script."""
    asyncio.run(seed_workflows(reset=True))


if __name__ == "__main__":
    main()
