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


# ── Agent Kitchen Sink ──────────────────────────────────────────────────
# One workflow that exercises every agent feature:
#   thinking, planning, reflection, scratchpad, tool calls,
#   skill delegation, sub-agent spawning, output validation

_KITCHEN_SINK_SYSTEM_PROMPT = (
    "You are a Research Coordinator AI.\n\n"
    "## Protocol\n"
    "You must follow these steps exactly:\n\n"
    "1. **Plan**: Write a <plan>...</plan> block outlining your approach.\n"
    "2. **Use scratchpad**: Call memory_store to save your research plan.\n"
    "3. **Delegate skills**: Call delegate_to_skill TWICE in the same turn:\n"
    "   - Delegate to 'math_assistant' with task: 'Calculate the compound interest on $10,000 at 5.5% annual rate compounded monthly for 3 years'\n"
    "   - Delegate to 'time_assistant' with task: 'Get the current time and format it as ISO 8601'\n"
    "4. **Spawn sub-agent**: Call spawn_agent with task: "
    "'Research and summarize the key benefits of compound interest for long-term savings. "
    "Include at least 3 specific points with examples.'\n"
    "5. **Recall scratchpad**: Call memory_recall to retrieve your stored plan.\n"
    "6. **Reflect**: Write a <reflect>...</reflect> block reviewing what you learned.\n"
    "7. **Produce final output**: Synthesize everything into the structured output.\n\n"
    "You MUST do all these steps. Do not skip any."
)

_KITCHEN_SINK_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A 2-3 sentence summary of all findings",
        },
        "compound_interest_result": {
            "type": "string",
            "description": "The calculated compound interest amount",
        },
        "current_time": {
            "type": "string",
            "description": "The current time in ISO 8601 format",
        },
        "research_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key research points from the sub-agent",
        },
        "steps_completed": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of all steps completed during execution",
        },
    },
}, indent=2)


# ── Tool Calling Gauntlet ─────────────────────────────────────────────
# Focused test for tool calling: multiple tools, parallel batches,
# sequential chains (use result of one tool as input to another),
# error handling, and mixed tool types.

_TOOL_CALLING_SYSTEM_PROMPT = (
    "You are a Data Processing Agent with access to multiple tools.\n\n"
    "## Instructions\n"
    "Complete the following steps IN ORDER. Batch independent calls.\n\n"
    "1. **Parallel batch 1** — call ALL of these in the same turn:\n"
    "   - calculator: evaluate '(1500 * 1.055) + (3200 / 8)'\n"
    "   - current_time: get the current time\n"
    "   - random_number: generate a random number between 1 and 1000\n"
    "   - text_utils: word_count on the text 'The quick brown fox jumps over the lazy dog near the riverbank'\n\n"
    "2. **Sequential chain** — using the random number from step 1:\n"
    "   - calculator: multiply that random number by 3.14159\n"
    "   - text_utils: reverse the string representation of the calculator result\n\n"
    "3. **Code execution** — call code_tool with this Python code:\n"
    "   ```\n"
    "   import math\n"
    "   primes = [n for n in range(2, 50) if all(n % i != 0 for i in range(2, int(math.sqrt(n))+1))]\n"
    "   return {'primes_under_50': primes, 'count': len(primes), 'sum': sum(primes)}\n"
    "   ```\n\n"
    "4. **Parallel batch 2** — call both in the same turn:\n"
    "   - calculator: compute the sum of primes from step 3 divided by the count\n"
    "   - text_utils: uppercase the current time string from step 1\n\n"
    "5. Return a final summary of ALL results."
)

# ── Banking Concierge (Multi-Intent Agentic Routing) ─────────────────
# A single orchestrator agent that:
#   1. Detects N intents from a multi-part customer utterance
#   2. Plans which skills to invoke (with <plan> blocks)
#   3. Dispatches N skill sub-agents in parallel via delegate_to_skill
#   4. Each skill has deep domain context + code_tool for API simulation
#   5. Orchestrator synthesizes all results into a unified response
#
# Skills: card_services, transaction_services, account_services,
#         loan_services, dispute_services

_BANKING_SYSTEM_PROMPT = (
    "You are a banking concierge AI. Your job is to handle complex, "
    "multi-part customer requests by breaking them into intents and "
    "delegating each to the right specialist skill.\n\n"
    "## Protocol\n"
    "1. Parse the customer's message and identify ALL distinct intents.\n"
    "2. Write a <plan> listing each intent and which skill handles it.\n"
    "3. Call delegate_to_skill for EACH intent. Call ALL skills in the "
    "SAME turn so they execute in parallel.\n"
    "4. When results return, write a <reflect> reviewing completeness.\n"
    "5. Synthesize a single friendly response addressing every intent.\n\n"
    "## Available Skills\n"
    "- card_services: card status, limits, activation, block/unblock\n"
    "- transaction_services: recent transactions, pending, search by date/amount\n"
    "- account_services: balance, account details, statements, settings\n"
    "- loan_services: loan status, payment schedule, payoff amount, rate info\n"
    "- dispute_services: file disputes, check dispute status, escalation\n\n"
    "## Important\n"
    "- ALWAYS delegate — never answer banking questions directly.\n"
    "- Pass relevant context (customer_id, account hints) to each skill.\n"
    "- If a request is ambiguous, delegate to the most likely skill.\n"
    "- Use memory_store to save your intent analysis for the reflect phase."
)

_CARD_SERVICES_PROMPT = (
    "You are a Card Services specialist at National Trust Bank.\n\n"
    "## Customer Data (simulate via code_tool)\n"
    "When asked about card information, use code_tool to return data from "
    "this mock database:\n\n"
    "```\n"
    "CARDS_DB = {\n"
    "  'default': {\n"
    "    'card_number': '**** **** **** 4532',\n"
    "    'card_type': 'Visa Platinum',\n"
    "    'status': 'Active',\n"
    "    'expiry': '09/2027',\n"
    "    'credit_limit': 15000.00,\n"
    "    'available_credit': 11240.50,\n"
    "    'current_balance': 3759.50,\n"
    "    'last_payment': {'amount': 500.00, 'date': '2026-02-15'},\n"
    "    'rewards_points': 24750,\n"
    "    'contactless_enabled': True,\n"
    "    'international_enabled': True,\n"
    "  }\n"
    "}\n"
    "```\n\n"
    "Return the relevant fields based on what was asked. "
    "Always include card_number (masked) and status."
)

_TRANSACTION_SERVICES_PROMPT = (
    "You are a Transaction Services specialist at National Trust Bank.\n\n"
    "## Customer Data (simulate via code_tool)\n"
    "When asked about transactions, use code_tool to return data from "
    "this mock database:\n\n"
    "```\n"
    "TRANSACTIONS = [\n"
    "  {'date': '2026-02-24', 'description': 'Amazon.com', 'amount': -89.99, 'type': 'debit', 'category': 'Shopping', 'status': 'posted'},\n"
    "  {'date': '2026-02-23', 'description': 'Whole Foods Market', 'amount': -67.43, 'type': 'debit', 'category': 'Groceries', 'status': 'posted'},\n"
    "  {'date': '2026-02-22', 'description': 'Shell Gas Station', 'amount': -52.10, 'type': 'debit', 'category': 'Gas', 'status': 'posted'},\n"
    "  {'date': '2026-02-21', 'description': 'Netflix Subscription', 'amount': -15.99, 'type': 'debit', 'category': 'Entertainment', 'status': 'posted'},\n"
    "  {'date': '2026-02-20', 'description': 'Direct Deposit - Employer', 'amount': 3450.00, 'type': 'credit', 'category': 'Income', 'status': 'posted'},\n"
    "  {'date': '2026-02-19', 'description': 'Uber Eats', 'amount': -34.20, 'type': 'debit', 'category': 'Food', 'status': 'posted'},\n"
    "  {'date': '2026-02-18', 'description': 'Apple.com', 'amount': -129.00, 'type': 'debit', 'category': 'Shopping', 'status': 'posted'},\n"
    "  {'date': '2026-02-17', 'description': 'Venmo Transfer', 'amount': -50.00, 'type': 'debit', 'category': 'Transfer', 'status': 'posted'},\n"
    "  {'date': '2026-02-25', 'description': 'Starbucks', 'amount': -6.75, 'type': 'debit', 'category': 'Food', 'status': 'pending'},\n"
    "]\n"
    "```\n\n"
    "Filter and format transactions based on the request. Include totals "
    "and counts when showing multiple transactions."
)

_ACCOUNT_SERVICES_PROMPT = (
    "You are an Account Services specialist at National Trust Bank.\n\n"
    "## Customer Data (simulate via code_tool)\n"
    "When asked about account information, use code_tool to return data from "
    "this mock database:\n\n"
    "```\n"
    "ACCOUNTS = {\n"
    "  'checking': {\n"
    "    'account_number': '****7890',\n"
    "    'type': 'Premium Checking',\n"
    "    'balance': 8432.67,\n"
    "    'available_balance': 8232.67,\n"
    "    'pending_transactions': 200.00,\n"
    "    'last_statement': '2026-01-31',\n"
    "    'interest_rate': '0.05%',\n"
    "    'opened_date': '2019-03-15',\n"
    "  },\n"
    "  'savings': {\n"
    "    'account_number': '****4321',\n"
    "    'type': 'High-Yield Savings',\n"
    "    'balance': 25610.00,\n"
    "    'interest_rate': '4.25%',\n"
    "    'monthly_interest': 90.72,\n"
    "    'ytd_interest': 180.44,\n"
    "    'opened_date': '2020-08-01',\n"
    "  }\n"
    "}\n"
    "```\n\n"
    "Return the relevant account details based on what was asked."
)

_LOAN_SERVICES_PROMPT = (
    "You are a Loan Services specialist at National Trust Bank.\n\n"
    "## Customer Data (simulate via code_tool)\n"
    "When asked about loan information, use code_tool to return data from "
    "this mock database:\n\n"
    "```\n"
    "LOANS = {\n"
    "  'auto_loan': {\n"
    "    'loan_id': 'LN-2024-8891',\n"
    "    'type': 'Auto Loan',\n"
    "    'original_amount': 28000.00,\n"
    "    'remaining_balance': 18450.00,\n"
    "    'interest_rate': '5.9%',\n"
    "    'monthly_payment': 542.00,\n"
    "    'next_payment_date': '2026-03-01',\n"
    "    'payments_remaining': 34,\n"
    "    'payoff_amount': 18520.75,\n"
    "    'status': 'Current',\n"
    "  },\n"
    "  'personal_loan': {\n"
    "    'loan_id': 'LN-2025-1234',\n"
    "    'type': 'Personal Loan',\n"
    "    'original_amount': 10000.00,\n"
    "    'remaining_balance': 7200.00,\n"
    "    'interest_rate': '8.5%',\n"
    "    'monthly_payment': 310.00,\n"
    "    'next_payment_date': '2026-03-05',\n"
    "    'payments_remaining': 24,\n"
    "    'status': 'Current',\n"
    "  }\n"
    "}\n"
    "```\n\n"
    "Return the relevant loan details based on what was asked."
)

_DISPUTE_SERVICES_PROMPT = (
    "You are a Dispute Services specialist at National Trust Bank.\n\n"
    "## Dispute Protocol\n"
    "When a customer reports a suspicious or incorrect charge, use code_tool "
    "to simulate filing a dispute with the following process:\n\n"
    "1. Identify the transaction in question from the customer's description\n"
    "2. Generate a dispute case with:\n"
    "   - case_id: 'DSP-2026-XXXX' (generate a random 4-digit number)\n"
    "   - status: 'Under Review'\n"
    "   - provisional_credit: True (amount credited within 24h)\n"
    "   - estimated_resolution: '10-15 business days'\n"
    "   - next_steps: list of what happens next\n\n"
    "## Existing Disputes (simulate via code_tool)\n"
    "```\n"
    "DISPUTES = [\n"
    "  {'case_id': 'DSP-2026-0142', 'transaction': 'Unknown charge $299.99 on 2026-02-10',\n"
    "   'status': 'Resolved - Refunded', 'resolution_date': '2026-02-20'},\n"
    "]\n"
    "```\n\n"
    "Return the dispute details and clearly explain next steps to the customer."
)


# ── Request Router (Classification → Switch → HTTP) ────────────────
# Tests expression replacement across the full pipeline:
#   JSON input fields → LLM classification → Switch routing →
#   HTTP calls with $node["Input"].json.* cross-node references,
#   nested path access, header expressions, and URL expressions.

_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a request classifier. Classify the user's message into "
    "exactly ONE category.\n\n"
    "Categories:\n"
    "- order: orders, shipments, tracking, delivery status\n"
    "- support: technical issues, bugs, help requests, complaints\n"
    "- billing: payments, invoices, refunds, charges, subscriptions\n\n"
    "Respond with ONLY the category name. One word, no punctuation."
)

EXAMPLE_WORKFLOWS = [
    {
        "name": "Request Router",
        "description": (
            "Tests expression replacement across the full pipeline: "
            "JSON input → LLM classification → Switch routing → HTTP calls "
            "with cross-node $node references, nested paths, and header/URL/body expressions."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": '{{ $json.body or {"message": "I need to check the status of my order #ORD-98765", "userId": "usr_12345", "email": "john@example.com", "orderId": "ORD-98765", "accountId": "ACC-567", "authToken": "Bearer tk_live_abc123", "locale": "en-US", "metadata": {"source": "web", "sessionId": "sess_xyz789"}} }}',
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Classifier",
                    "type": "LLMChat",
                    "parameters": {
                        "model": "gemini-2.0-flash",
                        "systemPrompt": _CLASSIFIER_SYSTEM_PROMPT,
                        "userMessage": "{{ $json.message }}",
                        "temperature": 0.0,
                        "maxTokens": 16,
                    },
                    "position": {"x": 600, "y": 300},
                },
                {
                    "name": "Router",
                    "type": "Switch",
                    "parameters": {
                        "numberOfOutputs": 3,
                        "mode": "rules",
                        "rules": [
                            {"output": 0, "field": "response", "operation": "contains", "value": "order"},
                            {"output": 1, "field": "response", "operation": "contains", "value": "support"},
                            {"output": 2, "field": "response", "operation": "contains", "value": "billing"},
                        ],
                    },
                    "position": {"x": 850, "y": 300},
                },
                {
                    "name": "Order Lookup",
                    "type": "HttpRequest",
                    "parameters": {
                        "method": "POST",
                        "url": "https://httpbin.org/post?userId={{ $node['Input'].json.userId }}&orderId={{ $node['Input'].json.orderId }}",
                        "headers": [
                            {"name": "Authorization", "value": "{{ $node['Input'].json.authToken }}"},
                            {"name": "Accept-Language", "value": "{{ $node['Input'].json.locale }}"},
                        ],
                        "body": json.dumps({
                            "action": "order_lookup",
                            "userId": "{{ $node['Input'].json.userId }}",
                            "orderId": "{{ $node['Input'].json.orderId }}",
                            "email": "{{ $node['Input'].json.email }}",
                            "locale": "{{ $node['Input'].json.locale }}",
                            "classification": "{{ $json.response }}",
                            "source": "{{ $node['Input'].json.metadata.source }}",
                        }),
                        "responseType": "json",
                    },
                    "position": {"x": 1150, "y": 100},
                },
                {
                    "name": "Support Ticket",
                    "type": "HttpRequest",
                    "parameters": {
                        "method": "POST",
                        "url": "https://httpbin.org/post",
                        "headers": [
                            {"name": "X-Session-Id", "value": "{{ $node['Input'].json.metadata.sessionId }}"},
                            {"name": "X-User-Id", "value": "{{ $node['Input'].json.userId }}"},
                        ],
                        "body": json.dumps({
                            "action": "create_support_ticket",
                            "email": "{{ $node['Input'].json.email }}",
                            "userId": "{{ $node['Input'].json.userId }}",
                            "message": "{{ $node['Input'].json.message }}",
                            "sessionId": "{{ $node['Input'].json.metadata.sessionId }}",
                            "classification": "{{ $json.response }}",
                            "priority": "normal",
                        }),
                        "responseType": "json",
                    },
                    "position": {"x": 1150, "y": 300},
                },
                {
                    "name": "Billing Inquiry",
                    "type": "HttpRequest",
                    "parameters": {
                        "method": "POST",
                        "url": "https://httpbin.org/post?account={{ $node['Input'].json.accountId }}",
                        "headers": [
                            {"name": "Authorization", "value": "{{ $node['Input'].json.authToken }}"},
                        ],
                        "body": json.dumps({
                            "action": "billing_inquiry",
                            "accountId": "{{ $node['Input'].json.accountId }}",
                            "userId": "{{ $node['Input'].json.userId }}",
                            "email": "{{ $node['Input'].json.email }}",
                            "authToken": "{{ $node['Input'].json.authToken }}",
                            "classification": "{{ $json.response }}",
                        }),
                        "responseType": "json",
                    },
                    "position": {"x": 1150, "y": 500},
                },
                {
                    "name": "Fallback",
                    "type": "HttpRequest",
                    "parameters": {
                        "method": "POST",
                        "url": "https://httpbin.org/post",
                        "body": json.dumps({
                            "action": "unclassified_request",
                            "message": "{{ $node['Input'].json.message }}",
                            "userId": "{{ $node['Input'].json.userId }}",
                            "email": "{{ $node['Input'].json.email }}",
                            "classification": "{{ $json.response }}",
                        }),
                        "responseType": "json",
                    },
                    "position": {"x": 1150, "y": 700},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Classifier"},
                {"source_node": "Classifier", "target_node": "Router"},
                {"source_node": "Router", "target_node": "Order Lookup", "source_output": "output0"},
                {"source_node": "Router", "target_node": "Support Ticket", "source_output": "output1"},
                {"source_node": "Router", "target_node": "Billing Inquiry", "source_output": "output2"},
                {"source_node": "Router", "target_node": "Fallback", "source_output": "fallback"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Banking Concierge",
        "description": (
            "Multi-intent agentic routing: orchestrator detects N intents from a customer utterance, "
            "plans with <plan>/<reflect> blocks, dispatches N skill sub-agents in parallel "
            "(card, transactions, account, loans, disputes), each with deep domain context and "
            "code_tool for API simulation. Uses scratchpad for intermediate state."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": '{{ $json.body or {"message": "I want to check my card status, see my recent transactions, and also there is a wrong charge of $129 from Apple.com on Feb 18 that I did not make"} }}',
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Concierge",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.0-flash",
                        "systemPrompt": _BANKING_SYSTEM_PROMPT,
                        "task": "{{ $json.message }}",
                        "maxIterations": 8,
                        "temperature": 0.2,
                        "enableSubAgents": False,
                        "enablePlanning": True,
                        "enableScratchpad": True,
                        "skillProfiles": [
                            {
                                "name": "card_services",
                                "description": "Card status, credit limits, activation, block/unblock, rewards points, contactless and international settings",
                                "systemPrompt": _CARD_SERVICES_PROMPT,
                                "toolNames": "code_tool",
                                "outputSchema": "",
                            },
                            {
                                "name": "transaction_services",
                                "description": "Recent transactions, pending charges, search by date/amount/merchant, spending summaries",
                                "systemPrompt": _TRANSACTION_SERVICES_PROMPT,
                                "toolNames": "code_tool",
                                "outputSchema": "",
                            },
                            {
                                "name": "account_services",
                                "description": "Account balance, account details, statements, interest rates, checking and savings info",
                                "systemPrompt": _ACCOUNT_SERVICES_PROMPT,
                                "toolNames": "code_tool",
                                "outputSchema": "",
                            },
                            {
                                "name": "loan_services",
                                "description": "Loan status, payment schedule, remaining balance, payoff amount, interest rates for auto and personal loans",
                                "systemPrompt": _LOAN_SERVICES_PROMPT,
                                "toolNames": "code_tool",
                                "outputSchema": "",
                            },
                            {
                                "name": "dispute_services",
                                "description": "File transaction disputes, check dispute status, get provisional credits, escalation for fraudulent or incorrect charges",
                                "systemPrompt": _DISPUTE_SERVICES_PROMPT,
                                "toolNames": "code_tool",
                                "outputSchema": "",
                            },
                        ],
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Output", "type": "Output", "parameters": {}, "position": {"x": 950, "y": 300}},
                # Tool subnode — shared by all skills via code_tool
                {"name": "Code", "type": "CodeTool", "parameters": {}, "position": {"x": 650, "y": 550}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Concierge"},
                {"source_node": "Concierge", "target_node": "Output"},
                # Tool subnode connection
                {"source_node": "Code", "target_node": "Concierge", "connection_type": "subnode", "slot_name": "tools"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Banking Concierge (Stub)",
        "description": (
            "Deterministic stub of the Banking Concierge — zero LLM calls. "
            "Code nodes handle intent parsing (keyword matching) and skill "
            "simulation (mock data). Tests full data pipeline and expression resolution."
        ),
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": '{{ $json.body or {"message": "I want to check my card status, see my recent transactions, and also there is a wrong charge of $129 from Apple.com on Feb 18 that I did not make"} }}',
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Intent Parser",
                    "type": "Code",
                    "parameters": {
                        "code": "\n".join([
                            "message = json_data.get('message', '').lower()",
                            "intents = []",
                            "if any(w in message for w in ['card', 'credit card', 'debit card']):",
                            "    intents.append('card_services')",
                            "if any(w in message for w in ['transaction', 'recent activity', 'spending']):",
                            "    intents.append('transaction_services')",
                            "if any(w in message for w in ['balance', 'account', 'statement', 'savings']):",
                            "    intents.append('account_services')",
                            "if any(w in message for w in ['loan', 'payment schedule', 'payoff']):",
                            "    intents.append('loan_services')",
                            "if any(w in message for w in ['dispute', 'wrong charge', 'unauthorized', 'fraud', 'did not make']):",
                            "    intents.append('dispute_services')",
                            "if not intents:",
                            "    intents.append('account_services')",
                            "return {",
                            "    'intents': intents,",
                            "    'intent_count': len(intents),",
                            "    'original_message': json_data.get('message', ''),",
                            "}",
                        ]),
                    },
                    "position": {"x": 600, "y": 300},
                },
                {
                    "name": "Skill Dispatcher",
                    "type": "Code",
                    "parameters": {
                        "code": "\n".join([
                            "intents = json_data.get('intents', [])",
                            "original_msg = json_data.get('original_message', '')",
                            "SKILLS = {",
                            "    'card_services': {",
                            "        'card_number': '**** **** **** 4532',",
                            "        'card_type': 'Visa Platinum',",
                            "        'status': 'Active',",
                            "        'expiry': '09/2027',",
                            "        'credit_limit': 15000.00,",
                            "        'available_credit': 11240.50,",
                            "        'rewards_points': 24750,",
                            "    },",
                            "    'transaction_services': {",
                            "        'transactions': [",
                            "            {'date': '2026-02-24', 'desc': 'Amazon.com', 'amount': -89.99, 'category': 'Shopping'},",
                            "            {'date': '2026-02-23', 'desc': 'Whole Foods', 'amount': -67.43, 'category': 'Groceries'},",
                            "            {'date': '2026-02-22', 'desc': 'Shell Gas', 'amount': -52.10, 'category': 'Gas'},",
                            "            {'date': '2026-02-21', 'desc': 'Netflix', 'amount': -15.99, 'category': 'Entertainment'},",
                            "            {'date': '2026-02-20', 'desc': 'Direct Deposit', 'amount': 3450.00, 'category': 'Income'},",
                            "            {'date': '2026-02-18', 'desc': 'Apple.com', 'amount': -129.00, 'category': 'Shopping'},",
                            "        ],",
                            "        'total_debits': -354.51,",
                            "        'total_credits': 3450.00,",
                            "    },",
                            "    'account_services': {",
                            "        'checking': {'account': '****7890', 'balance': 8432.67, 'type': 'Premium Checking'},",
                            "        'savings': {'account': '****4321', 'balance': 25610.00, 'rate': '4.25%'},",
                            "    },",
                            "    'loan_services': {",
                            "        'auto_loan': {'id': 'LN-2024-8891', 'remaining': 18450.00, 'payment': 542.00},",
                            "        'personal_loan': {'id': 'LN-2025-1234', 'remaining': 7200.00, 'payment': 310.00},",
                            "    },",
                            "    'dispute_services': {",
                            "        'case_id': 'DSP-2026-' + str(random.randint(1000, 9999)),",
                            "        'transaction': 'Apple.com $129 on 2026-02-18',",
                            "        'status': 'Under Review',",
                            "        'provisional_credit': True,",
                            "        'estimated_resolution': '10-15 business days',",
                            "    },",
                            "}",
                            "results = {}",
                            "for intent in intents:",
                            "    if intent in SKILLS:",
                            "        results[intent] = SKILLS[intent]",
                            "return {",
                            "    'intents_detected': intents,",
                            "    'intent_count': len(intents),",
                            "    'skill_results': results,",
                            "    'original_message': original_msg,",
                            "}",
                        ]),
                    },
                    "position": {"x": 900, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Intent Parser"},
                {"source_node": "Intent Parser", "target_node": "Skill Dispatcher"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Tool Calling Gauntlet",
        "description": "Tests tool calling: parallel batches, sequential chains (result of one tool feeds another), code execution, and multi-tool coordination.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps({
                            "request": "Run the full data processing pipeline: parallel tool calls, sequential chains, code execution, and final aggregation.",
                        }),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Tool Agent",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.0-flash",
                        "systemPrompt": _TOOL_CALLING_SYSTEM_PROMPT,
                        "task": "{{ $json.request }}",
                        "maxIterations": 10,
                        "temperature": 0.2,
                        "enableSubAgents": False,
                        "enablePlanning": False,
                        "enableScratchpad": False,
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Output", "type": "Output", "parameters": {}, "position": {"x": 950, "y": 300}},
                # Tool subnodes
                {"name": "Calc", "type": "CalculatorTool", "parameters": {}, "position": {"x": 450, "y": 550}},
                {"name": "Clock", "type": "CurrentTimeTool", "parameters": {"timezone": "UTC"}, "position": {"x": 570, "y": 550}},
                {"name": "RNG", "type": "RandomNumberTool", "parameters": {"min": 1, "max": 1000}, "position": {"x": 690, "y": 550}},
                {"name": "Text", "type": "TextTool", "parameters": {}, "position": {"x": 810, "y": 550}},
                {"name": "Code", "type": "CodeTool", "parameters": {}, "position": {"x": 630, "y": 650}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Tool Agent"},
                {"source_node": "Tool Agent", "target_node": "Output"},
                # Tool subnode connections
                {"source_node": "Calc", "target_node": "Tool Agent", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Clock", "target_node": "Tool Agent", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "RNG", "target_node": "Tool Agent", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Text", "target_node": "Tool Agent", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Code", "target_node": "Tool Agent", "connection_type": "subnode", "slot_name": "tools"},
            ],
            "settings": {},
        },
    },
    {
        "name": "Agent Kitchen Sink",
        "description": "Exercises every agent trace event: planning, reflection, scratchpad (memory_store/recall), tool calls, skill delegation, sub-agent spawning, and structured output validation.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps({
                            "request": "Run the full research protocol: calculate compound interest, check current time, research benefits, and synthesize findings.",
                        }),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Research Coordinator",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.5-flash",
                        "systemPrompt": _KITCHEN_SINK_SYSTEM_PROMPT,
                        "task": "{{ $json.request }}",
                        "maxIterations": 12,
                        "temperature": 0.3,
                        "enableSubAgents": True,
                        "maxAgentDepth": 2,
                        "allowRecursiveSpawn": False,
                        "enablePlanning": True,
                        "enableScratchpad": True,
                        "outputSchema": _KITCHEN_SINK_OUTPUT_SCHEMA,
                        "skillProfiles": [
                            {
                                "name": "math_assistant",
                                "description": "Performs mathematical calculations using the calculator tool",
                                "systemPrompt": (
                                    "You are a math assistant. Use the calculator tool to compute "
                                    "the requested calculation. Show the formula and result clearly."
                                ),
                                "toolNames": "calculator",
                                "outputSchema": "",
                            },
                            {
                                "name": "time_assistant",
                                "description": "Gets the current date and time",
                                "systemPrompt": (
                                    "You are a time lookup assistant. Use the current_time tool "
                                    "to get the current time and report it in ISO 8601 format."
                                ),
                                "toolNames": "current_time",
                                "outputSchema": "",
                            },
                        ],
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Output", "type": "Output", "parameters": {}, "position": {"x": 950, "y": 300}},
                # Subnodes
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.3, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
                {"name": "Calculator", "type": "CalculatorTool", "parameters": {}, "position": {"x": 550, "y": 600}},
                {"name": "Time", "type": "CurrentTimeTool", "parameters": {}, "position": {"x": 750, "y": 600}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Research Coordinator"},
                {"source_node": "Research Coordinator", "target_node": "Output"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Research Coordinator", "connection_type": "subnode", "slot_name": "chatModel"},
                {"source_node": "Calculator", "target_node": "Research Coordinator", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Time", "target_node": "Research Coordinator", "connection_type": "subnode", "slot_name": "tools"},
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
