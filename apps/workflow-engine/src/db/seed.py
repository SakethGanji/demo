"""Seed database with demo workflows."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from sqlalchemy import select, delete, text

from .session import async_session_factory, init_db
from .models import WorkflowModel, WorkflowVersionModel


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


EXAMPLE_WORKFLOWS = [
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


async def seed_workflows(reset: bool = False) -> None:
    """Seed the database with example workflows."""
    await init_db()

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

        added = 0
        skipped = 0
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
