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


# ── HTML Template ────────────────────────────────────────────────────
# Single template — all dynamic values come from {{ $json.xxx }}
# resolved by the expression engine at runtime.

ACTION_CARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            background-color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .card {
            width: 100%;
            max-width: 380px;
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
            overflow: hidden;
            border: 1px solid #e2e8f0;
        }
        .card-header {
            background-color: #cbd5e1;
            padding: 20px 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .card-title {
            font-size: 18px;
            font-weight: 600;
            color: #1e293b;
            margin: 0;
        }
        .badge {
            background-color: #1e4d46;
            color: #fff;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .badge svg { width: 14px; height: 14px; }
        .card-body {
            background: #f1f5f9;
            padding: 16px;
            margin: 0 12px 12px;
            border-radius: 10px;
            border: 1px solid #e2e8f0;
        }
        .bullet-list {
            margin: 0;
            padding: 0;
            list-style: none;
        }
        .bullet-item {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 4px 0;
            color: #334155;
            font-size: 14px;
            line-height: 1.5;
        }
        .bullet-dot {
            width: 6px;
            height: 6px;
            background-color: #1d4ed8;
            border-radius: 50%;
            margin-top: 7px;
            flex-shrink: 0;
        }
        .card-label {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 11px;
            font-weight: 700;
            color: #64748b;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-top: 14px;
        }
        .card-label svg { width: 16px; height: 16px; }
        .card-btn {
            display: block;
            box-sizing: border-box;
            background-color: #1d4ed8;
            color: #fff;
            width: 100%;
            padding: 12px 0;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            text-align: center;
            border: none;
            margin-top: 12px;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">{{ $json.title }}</h2>
            <div class="badge">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                <span>{{ $json.accountId }}</span>
            </div>
        </div>
        <div class="card-body">
            <!-- bullets injected as pre-built HTML -->
            <ul class="bullet-list">{{ $json.bulletsHtml }}</ul>
            <div class="card-label">
                <svg fill="#f59e0b" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0111 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd"/></svg>
                <span>{{ $json.actionLabel }}</span>
            </div>
            <div class="card-btn">{{ $json.buttonText }}</div>
        </div>
    </div>

    <!--
        ══════════════════════════════════════════
        VARIABLES (resolved by expression engine):
          $json.title       — card header title
          $json.accountId   — badge account ID
          $json.actionLabel — action label text
          $json.buttonText  — button text
          $json.bulletsHtml — bullet list items (pre-built HTML)
        ══════════════════════════════════════════
    -->
</body>
</html>
"""


import os as _os
_SAMPLE_DIR = _os.path.join(_os.path.dirname(__file__), "..", "..", "..", "..", "demo-workflows", "sample-data")
_SAMPLE_DIR = _os.path.abspath(_SAMPLE_DIR)

EXAMPLE_WORKFLOWS = [
    # ========================================
    # INTENT-BASED UI ROUTER
    # ========================================
    {
        "name": "Intent UI Router",
        "description": "Classifies user query intent (cards, transactions, or balance) and renders a different HTML UI for each. Run with manual trigger or test with input data.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Classify Intent",
                    "type": "Code",
                    "parameters": {
                        "code": (
                            'def make_bullets(items):\n'
                            '    return "".join(\n'
                            '        \'<li class="bullet-item"><div class="bullet-dot"></div><span>\' + t + \'</span></li>\'\n'
                            '        for t in items\n'
                            '    )\n'
                            '\n'
                            'raw = json_data.get("query", "")\n'
                            'query = str(raw).lower() if raw else ""\n'
                            '\n'
                            'if "card" in query or "credit" in query or "debit" in query:\n'
                            '    return [{"json": {\n'
                            '        "intent": "cards",\n'
                            '        "title": "Lock / Unlock",\n'
                            '        "accountId": "1258",\n'
                            '        "actionLabel": "Next Best Action",\n'
                            '        "buttonText": "Lock / Unlock Account",\n'
                            '        "bulletsHtml": make_bullets([\n'
                            '            "Card ending 7891 temporarily locked",\n'
                            '            "Last transaction: $45.00 at Amazon",\n'
                            '            "Unlock requires SMS verification"\n'
                            '        ])\n'
                            '    }}]\n'
                            'elif "transaction" in query or "history" in query or "payment" in query:\n'
                            '    return [{"json": {\n'
                            '        "intent": "transactions",\n'
                            '        "title": "Recent Activity",\n'
                            '        "accountId": "1258",\n'
                            '        "actionLabel": "Next Best Action",\n'
                            '        "buttonText": "View Transactions",\n'
                            '        "bulletsHtml": make_bullets([\n'
                            '            "Payment of $45.00 processed successfully",\n'
                            '            "New login detected from unknown device",\n'
                            '            "Wire transfer of $200.00 pending approval"\n'
                            '        ])\n'
                            '    }}]\n'
                            'else:\n'
                            '    return [{"json": {\n'
                            '        "intent": "balance",\n'
                            '        "title": "Account Balance",\n'
                            '        "accountId": "1258",\n'
                            '        "actionLabel": "Next Best Action",\n'
                            '        "buttonText": "Check Balance",\n'
                            '        "bulletsHtml": make_bullets([\n'
                            '            "Current balance: $24,580.50",\n'
                            '            "Last deposit: $4,500.00 on Feb 14",\n'
                            '            "Monthly spending: $1,842.30 across 16 transactions"\n'
                            '        ])\n'
                            '    }}]'
                        ),
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Route by Intent",
                    "type": "Switch",
                    "parameters": {
                        "numberOfOutputs": 3,
                        "mode": "rules",
                        "rules": [
                            {
                                "output": 0,
                                "field": "intent",
                                "operation": "equals",
                                "value": "cards",
                            },
                            {
                                "output": 1,
                                "field": "intent",
                                "operation": "equals",
                                "value": "transactions",
                            },
                            {
                                "output": 2,
                                "field": "intent",
                                "operation": "equals",
                                "value": "balance",
                            },
                        ],
                    },
                    "position": {"x": 600, "y": 300},
                },
                {
                    "name": "Cards UI",
                    "type": "HTMLDisplay",
                    "parameters": {
                        "content": ACTION_CARD_HTML,
                    },
                    "position": {"x": 900, "y": 100},
                },
                {
                    "name": "Transactions UI",
                    "type": "HTMLDisplay",
                    "parameters": {
                        "content": ACTION_CARD_HTML,
                    },
                    "position": {"x": 900, "y": 300},
                },
                {
                    "name": "Balance UI",
                    "type": "HTMLDisplay",
                    "parameters": {
                        "content": ACTION_CARD_HTML,
                    },
                    "position": {"x": 900, "y": 500},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Classify Intent"},
                {"source_node": "Classify Intent", "target_node": "Route by Intent"},
                {"source_node": "Route by Intent", "target_node": "Cards UI", "source_output": "output0"},
                {"source_node": "Route by Intent", "target_node": "Transactions UI", "source_output": "output1"},
                {"source_node": "Route by Intent", "target_node": "Balance UI", "source_output": "output2"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 2. SALES REPORT — File → Report → Output (HTML)
    # ========================================
    {
        "name": "Sales Report",
        "description": "Reads sales_data.csv, generates a full HTML report with stats, distributions, and data preview, then displays it.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Generate Report",
                    "type": "Report",
                    "parameters": {
                        "sourceType": "file",
                        "filePath": f"{_SAMPLE_DIR}/sales_data.csv",
                        "title": "Q1–Q4 Sales Report",
                        "previewRows": 15,
                        "topN": 10,
                        "showOverview": True,
                        "showColumnStats": True,
                        "showDistributions": True,
                        "showTopValues": True,
                        "showCorrelations": True,
                        "showDataPreview": True,
                        "outputFormat": "html",
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Show Report",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "html",
                        "contentField": "html",
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Generate Report"},
                {"source_node": "Generate Report", "target_node": "Show Report"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 3. CUSTOMER PROFILE — File → Profile → Output (table)
    # ========================================
    {
        "name": "Customer Profile",
        "description": "Profiles customers.csv — shows column stats, distributions, and top values for every column.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Profile Data",
                    "type": "Profile",
                    "parameters": {
                        "sourceType": "file",
                        "filePath": f"{_SAMPLE_DIR}/customers.csv",
                        "columns": "",
                        "includeHistograms": True,
                        "includeCorrelations": True,
                        "topN": 10,
                    },
                    "position": {"x": 400, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Profile Data"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 4. SALES AGGREGATE — File → Aggregate → Output (table)
    # ========================================
    {
        "name": "Sales by Region",
        "description": "Aggregates sales_data.csv by Region — sums Revenue, counts orders, computes avg Profit. Displays result as a table.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Aggregate",
                    "type": "Aggregate",
                    "parameters": {
                        "sourceType": "file",
                        "filePath": f"{_SAMPLE_DIR}/sales_data.csv",
                        "groupBy": "Region",
                        "aggregations": [
                            {"column": "Revenue", "function": "sum", "alias": "Total Revenue"},
                            {"column": "Profit", "function": "sum", "alias": "Total Profit"},
                            {"column": "Units", "function": "sum", "alias": "Units Sold"},
                            {"column": "Revenue", "function": "mean", "alias": "Avg Order Value"},
                            {"column": "Revenue", "function": "count", "alias": "Order Count"},
                        ],
                        "sortBy": "Total Revenue",
                        "sortOrder": "desc",
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Show Table",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "table",
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Aggregate"},
                {"source_node": "Aggregate", "target_node": "Show Table"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 5. EMPLOYEE SAMPLE — File → Sample → Output (table)
    # ========================================
    {
        "name": "Employee Sample",
        "description": "Takes a stratified sample of employees.csv by Department (50 rows), then displays the sampled data as a table.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Sample",
                    "type": "Sample",
                    "parameters": {
                        "sourceType": "file",
                        "filePath": f"{_SAMPLE_DIR}/employees.csv",
                        "method": "stratified",
                        "sampleSize": 50,
                        "stratifyColumn": "Department",
                        "seed": 42,
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Show Sample",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "table",
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Sample"},
                {"source_node": "Sample", "target_node": "Show Sample"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 6. CSV TABLE DISPLAY — File → Output (auto-detect)
    # ========================================
    {
        "name": "CSV Table Display",
        "description": "Displays a CSV file directly as a sortable table. Edit the Output node's filePath to point at any .csv, .xlsx, .tsv, or .parquet file.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Display CSV",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": f"{_SAMPLE_DIR}/sales_data.csv",
                    },
                    "position": {"x": 400, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Display CSV"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 7. PRODUCT DEEP DIVE — Aggregate by Product+Channel → Report
    # ========================================
    {
        "name": "Product Deep Dive",
        "description": "Multi-group aggregation (Product × Channel) on sales data, piped into a Report node for a full visual breakdown.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Aggregate",
                    "type": "Aggregate",
                    "parameters": {
                        "sourceType": "file",
                        "filePath": f"{_SAMPLE_DIR}/sales_data.csv",
                        "groupBy": "Product,Channel",
                        "aggregations": [
                            {"column": "Revenue", "function": "sum", "alias": "Revenue"},
                            {"column": "Profit", "function": "sum", "alias": "Profit"},
                            {"column": "Units", "function": "sum", "alias": "Units"},
                            {"column": "Discount", "function": "mean", "alias": "Avg Discount %"},
                        ],
                        "sortBy": "Revenue",
                        "sortOrder": "desc",
                        "limit": 20,
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Build Report",
                    "type": "Report",
                    "parameters": {
                        "sourceType": "input",
                        "dataField": "data",
                        "title": "Product × Channel Breakdown",
                        "showOverview": True,
                        "showColumnStats": True,
                        "showDistributions": True,
                        "showCorrelations": True,
                        "showDataPreview": True,
                        "previewRows": 20,
                        "outputFormat": "html",
                    },
                    "position": {"x": 700, "y": 300},
                },
                {
                    "name": "Show Report",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "html",
                        "contentField": "html",
                    },
                    "position": {"x": 1000, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Aggregate"},
                {"source_node": "Aggregate", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 8. HR SALARY REPORT — Employees → Report with correlations
    # ========================================
    {
        "name": "HR Salary Report",
        "description": "Full profiling report on employee data — salary distributions, experience correlations, department breakdowns.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Report",
                    "type": "Report",
                    "parameters": {
                        "sourceType": "file",
                        "filePath": f"{_SAMPLE_DIR}/employees.csv",
                        "title": "HR Salary & Headcount Report",
                        "previewRows": 15,
                        "topN": 8,
                        "showOverview": True,
                        "showColumnStats": True,
                        "showDistributions": True,
                        "showTopValues": True,
                        "showCorrelations": True,
                        "showDataPreview": True,
                        "outputFormat": "html",
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Display",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "html",
                        "contentField": "html",
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Report"},
                {"source_node": "Report", "target_node": "Display"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 9. NEO4J AGENT — AI Agent with Neo4j graph query tool
    # ========================================
    {
        "name": "Neo4j Org Chart Agent",
        "description": "AI Agent that queries a company org chart stored in Neo4j. Ask about people, projects, teams, and technologies. Requires Neo4j at bolt://localhost:7687.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "AI Agent",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.0-flash",
                        "systemPrompt": (
                            "You are a helpful assistant with access to a company org chart "
                            "stored in Neo4j. Use the neo4j_query tool to answer questions "
                            "about people, projects, teams, and technologies. "
                            "Always use the tool to look up data — never guess."
                        ),
                        "task": "Give me a full overview of the engineering organization: who leads it, the active projects, and what tech stacks they use.",
                        "maxIterations": 8,
                        "temperature": 0.2,
                    },
                    "position": {"x": 500, "y": 300},
                },
                {
                    "name": "Neo4j Tool",
                    "type": "Neo4jQueryTool",
                    "parameters": {
                        "uri": "bolt://localhost:7687",
                        "username": "neo4j",
                        "password": "testpassword",
                        "database": "neo4j",
                        "toolName": "neo4j_query",
                        "description": "Query the company org chart in Neo4j. Use this to find people, projects, teams, and technologies.",
                        "queryRegistry": json.dumps({
                            "list_people": {
                                "description": "List all people with their roles and departments",
                                "query": "MATCH (p:Person) RETURN p.name AS name, p.role AS role, p.department AS department ORDER BY p.name",
                                "parameters": {},
                            },
                            "find_person": {
                                "description": "Find a person by name",
                                "query": "MATCH (p:Person) WHERE toLower(p.name) CONTAINS toLower($name) RETURN p.name AS name, p.role AS role, p.department AS department, p.email AS email",
                                "parameters": {
                                    "name": {"type": "string", "required": True},
                                },
                            },
                            "person_reports": {
                                "description": "Find who reports to (is managed by) a given person",
                                "query": "MATCH (mgr:Person)-[:MANAGES]->(report:Person) WHERE toLower(mgr.name) CONTAINS toLower($manager_name) RETURN report.name AS name, report.role AS role",
                                "parameters": {
                                    "manager_name": {"type": "string", "required": True},
                                },
                            },
                            "person_projects": {
                                "description": "Find all projects a person is working on",
                                "query": "MATCH (p:Person)-[w:WORKS_ON]->(proj:Project) WHERE toLower(p.name) CONTAINS toLower($name) RETURN proj.name AS project, w.role AS role, proj.status AS status, proj.priority AS priority",
                                "parameters": {
                                    "name": {"type": "string", "required": True},
                                },
                            },
                            "project_team": {
                                "description": "Find all people working on a given project",
                                "query": "MATCH (p:Person)-[w:WORKS_ON]->(proj:Project) WHERE toLower(proj.name) CONTAINS toLower($project_name) RETURN p.name AS person, w.role AS project_role, proj.name AS project, proj.status AS status",
                                "parameters": {
                                    "project_name": {"type": "string", "required": True},
                                },
                            },
                            "project_tech_stack": {
                                "description": "Get the technology stack used by a project",
                                "query": "MATCH (proj:Project)-[:USES]->(t:Technology) WHERE toLower(proj.name) CONTAINS toLower($project_name) RETURN t.name AS technology, t.category AS category",
                                "parameters": {
                                    "project_name": {"type": "string", "required": True},
                                },
                            },
                            "person_skills": {
                                "description": "Get the skills/technologies a person knows",
                                "query": "MATCH (p:Person)-[:SKILLED_IN]->(t:Technology) WHERE toLower(p.name) CONTAINS toLower($name) RETURN t.name AS skill, t.category AS category",
                                "parameters": {
                                    "name": {"type": "string", "required": True},
                                },
                            },
                            "active_projects": {
                                "description": "List all active projects",
                                "query": "MATCH (proj:Project) WHERE proj.status = 'active' RETURN proj.name AS name, proj.description AS description, proj.priority AS priority",
                                "parameters": {},
                            },
                            "team_members": {
                                "description": "List members of a team",
                                "query": "MATCH (p:Person)-[:MEMBER_OF]->(t:Team) WHERE toLower(t.name) CONTAINS toLower($team_name) RETURN p.name AS person, p.role AS role",
                                "parameters": {
                                    "team_name": {"type": "string", "required": True},
                                },
                            },
                        }, indent=2),
                        "resultLimit": 50,
                        "queryTimeout": 15,
                    },
                    "position": {"x": 500, "y": 100},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "AI Agent"},
                {
                    "source_node": "Neo4j Tool",
                    "target_node": "AI Agent",
                    "connection_type": "subnode",
                    "slot_name": "tools",
                },
            ],
            "settings": {},
        },
    },
]

# ── Shared config for Prompt Validator workflows ─────────────────────
_PROMPT_VALIDATOR_SYSTEM_PROMPT = (
    "You are a master prompt evaluation engine. Your job is to comprehensively evaluate the quality of LLM prompts.\n\n"
    "You will receive a prompt to evaluate, and optionally a set of requirements the prompt should satisfy.\n\n"
    "## Evaluation Process\n\n"
    "1. Use spawn_agents_parallel to dispatch ALL specialist evaluators at once:\n\n"
    "   a) **Grammar Analyst** — Analyze grammatical correctness, spelling errors, punctuation, "
    "sentence structure, readability, and word choice. Score 1-10. List every specific error found "
    "with the exact text and correction.\n\n"
    "   b) **Clarity & Structure Analyst** — Evaluate logical flow, ambiguity, instruction clarity, "
    "completeness, organization, and whether the prompt can be misinterpreted. Score 1-10. "
    "Identify any vague, contradictory, or incomplete instructions.\n\n"
    "   c) **Prompt Engineering Analyst** — Check against LLM prompt engineering best practices: "
    "role/persona definition, specificity of instructions, inclusion of examples (few-shot), "
    "output format specification, constraint definition, edge case handling, "
    "chain-of-thought guidance, and appropriate length. Score 1-10.\n\n"
    "   d) **Requirements Compliance Analyst** (ONLY if requirements are provided) — "
    "For each requirement, determine if the prompt satisfies it (met/unmet with explanation). Score 1-10.\n\n"
    "2. After ALL sub-agents complete, synthesize their findings into a comprehensive evaluation.\n\n"
    "3. Write an improved version of the prompt that fixes all identified issues.\n\n"
    "## Sub-Agent Instructions\n"
    "- Give each sub-agent the full prompt text via context_snippets\n"
    "- If requirements exist, also pass them as a context snippet to the Requirements Compliance agent\n"
    "- Use expected_output on each sub-agent to get structured JSON results\n"
    "- Each sub-agent should return: {score, issues[], suggestions[]}\n"
    "- Requirements agent should additionally return: {met[], unmet[]}\n"
    "- If no requirements are provided, spawn only agents a-c (skip requirements compliance)\n"
)

_PROMPT_VALIDATOR_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "overall_score": {
            "type": "number",
            "description": "Overall prompt quality score from 1 (terrible) to 10 (perfect)",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence overall assessment of the prompt",
        },
        "dimensions": {
            "type": "object",
            "properties": {
                "grammar": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "issues": {"type": "array", "items": {"type": "string"}},
                        "suggestions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "clarity": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "issues": {"type": "array", "items": {"type": "string"}},
                        "suggestions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "prompt_engineering": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "issues": {"type": "array", "items": {"type": "string"}},
                        "suggestions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "requirements_compliance": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number"},
                        "met": {"type": "array", "items": {"type": "string"}},
                        "unmet": {"type": "array", "items": {"type": "string"}},
                        "suggestions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
        "critical_issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Top issues that must be fixed immediately",
        },
        "improved_prompt": {
            "type": "string",
            "description": "A rewritten version of the prompt with all issues fixed",
        },
    },
}, indent=2)

# JSON-only agent config (used by API variant)
_PROMPT_VALIDATOR_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _PROMPT_VALIDATOR_SYSTEM_PROMPT,
    "maxIterations": 8,
    "temperature": 0.3,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "outputSchema": _PROMPT_VALIDATOR_OUTPUT_SCHEMA,
}

# ── HTML-direct agent (no Code node, no sub-agents) ──────────────────
# The agent analyses the prompt directly and outputs an HTML report.

_PROMPT_VALIDATOR_HTML_SYSTEM = (
    "You are a prompt quality evaluator. Analyse the given LLM prompt across four dimensions:\n\n"
    "1. **Grammar & Language** — spelling, punctuation, sentence structure, readability. Score 1-10. List every error with exact text + fix.\n"
    "2. **Clarity & Structure** — logical flow, ambiguity, completeness, whether instructions can be misread. Score 1-10.\n"
    "3. **Prompt Engineering** — role/persona, specificity, examples, output format spec, edge-case handling, chain-of-thought guidance. Score 1-10.\n"
    "4. **Requirements Compliance** (only if requirements given) — for each requirement state met/unmet + reason. Score 1-10.\n\n"
    "Compute an overall integer score 1-10 (weighted average). Write a 2-3 sentence summary, list critical issues, "
    "and produce an improved version of the prompt that fixes every issue.\n\n"
    "## Output — HTML Report\n"
    "Your structured output is `{\"html\": \"<full HTML document>\"}`. Rules:\n"
    "- Start with `<!DOCTYPE html>`. ALL CSS in a `<style>` tag — no external resources.\n"
    "- Font: system-ui, -apple-system, sans-serif. Background: #f1f5f9. Max-width 720px centered.\n"
    "- **Header**: dark gradient (#0f172a→#334155), border-radius 20px, white text. "
    "Left side: title + summary. Right side: SVG donut ring showing the overall score.\n"
    "- **Critical Issues banner** (red, only if issues exist).\n"
    "- **Dimension cards** (white, rounded, subtle shadow): header with emoji + title + score bar + number; "
    "body with ISSUES (red ✕), SUGGESTIONS (blue ▲). Requirements card also has MET (green ✓) / UNMET (red ✕).\n"
    "- **Improved Prompt** section: green box with monospace inner box.\n"
    "- **Footer**: centered gray text.\n"
    "- Score colors: ≥8 #16a34a, 5-7 #d97706, <5 #dc2626. Labels: 9-10 Excellent, 7-8 Good, 5-6 Fair, 3-4 Poor, 1-2 Critical.\n"
)

_PROMPT_VALIDATOR_HTML_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "html": {
            "type": "string",
            "description": "Complete self-contained HTML document with the prompt quality report",
        },
    },
    "required": ["html"],
}, indent=2)

_PROMPT_VALIDATOR_HTML_AGENT = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _PROMPT_VALIDATOR_HTML_SYSTEM,
    "maxIterations": 4,
    "temperature": 0.3,
    "enableSubAgents": False,
    "enablePlanning": False,
    "outputSchema": _PROMPT_VALIDATOR_HTML_SCHEMA,
}

_PROMPT_VALIDATOR_SAMPLE = {
    "prompt": (
        "You are a helpful assitant. Help me write python code. "
        "Make sure the code is clean and follows best practices. "
        "If you dont know something just say so. "
        "Always provide explanations with your code and handle edge cases. "
        "Use type hints where possible."
    ),
    "requirements": (
        "1. Must define a clear role/persona for the AI\n"
        "2. Should include output format instructions\n"
        "3. Must handle edge cases (unknown topics, ambiguous requests)\n"
        "4. Should include at least one example of expected behavior\n"
        "5. Must be under 200 words"
    ),
}

EXAMPLE_WORKFLOWS += [
    # ========================================
    # 10. PROMPT VALIDATOR (UI) — Start → Set → Agent (HTML) → Output
    # ========================================
    {
        "name": "Prompt Validator",
        "description": "Evaluates LLM prompts for quality across grammar, clarity, prompt engineering best practices, and optional requirements compliance. AI agent generates a styled HTML report directly — no Code node needed. Edit the Input node to change the prompt and requirements.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_PROMPT_VALIDATOR_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Prompt Evaluator",
                    "type": "AIAgent",
                    "parameters": {
                        **_PROMPT_VALIDATOR_HTML_AGENT,
                        "task": (
                            "Evaluate the following prompt:\n\n"
                            "---\n"
                            "{{ $json.prompt }}\n"
                            "---\n\n"
                            "Requirements (empty means none provided — skip requirements compliance):\n"
                            "{{ $json.requirements }}\n\n"
                            "Run your full evaluation pipeline and generate the HTML quality report."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {
                    "name": "Show Report",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "html",
                        "contentField": "{{ $json.structured.html }}",
                    },
                    "position": {"x": 1000, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Prompt Evaluator"},
                {"source_node": "Prompt Evaluator", "target_node": "Show Report"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 11. PROMPT VALIDATOR API — Webhook → Agent → JSON response
    # ========================================
    {
        "name": "Prompt Validator API",
        "description": "Webhook API for prompt validation. POST {prompt, requirements?} to /webhook/<id> and receive structured JSON scores. Requirements field is optional.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {
                        "method": "POST",
                        "responseMode": "lastNode",
                    },
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Prompt Evaluator",
                    "type": "AIAgent",
                    "parameters": {
                        **_PROMPT_VALIDATOR_AGENT_BASE,
                        "task": (
                            "Evaluate the following prompt:\n\n"
                            "---\n"
                            "{{ $json.body.prompt }}\n"
                            "---\n\n"
                            "Requirements (empty means none provided — skip requirements compliance):\n"
                            "{{ $json.body.requirements }}\n\n"
                            "Run your full evaluation pipeline and provide a comprehensive quality report."
                        ),
                    },
                    "position": {"x": 450, "y": 300},
                },
                {
                    "name": "Respond",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "responseMode": "lastNode",
                        "responseField": "structured",
                        "contentType": "application/json",
                        "wrapResponse": False,
                    },
                    "position": {"x": 800, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Prompt Evaluator"},
                {"source_node": "Prompt Evaluator", "target_node": "Respond"},
            ],
            "settings": {},
        },
    },
]

# ── Customer Feedback Analyzer ───────────────────────────────────────

_FEEDBACK_SYSTEM_PROMPT = (
    "You are a product intelligence engine that analyzes raw customer feedback.\n\n"
    "## Process\n"
    "Use spawn_agents_parallel to dispatch ALL analysts at once:\n\n"
    "a) **Sentiment Analyst** — For each piece of feedback classify sentiment as positive / neutral / negative, "
    "extract the emotion (frustrated, delighted, confused, etc.), and identify the specific trigger. "
    "Compute an overall Net Sentiment Score from -100 to +100.\n\n"
    "b) **Feature Request Extractor** — Identify every feature request or enhancement suggestion, "
    "whether explicit or implied. Group similar ones. Estimate demand based on frequency and urgency.\n\n"
    "c) **Bug & Pain Point Detector** — Find every bug report, complaint, friction point, or UX issue. "
    "Classify severity (critical/major/minor). Note exact quotes as evidence.\n\n"
    "d) **Theme & Trend Analyst** — Identify overarching themes, recurring patterns, and emerging trends. "
    "Spot any concerning signals (churn risk, competitor mentions, pricing complaints).\n\n"
    "After ALL sub-agents complete, synthesize into a prioritized product intelligence report.\n\n"
    "## Sub-Agent Instructions\n"
    "- Pass ALL feedback items to each sub-agent via context_snippets\n"
    "- Use expected_output to get structured JSON from each\n"
)

_FEEDBACK_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string", "description": "3-4 sentence overview for leadership"},
        "net_sentiment_score": {"type": "number", "description": "Overall sentiment from -100 (very negative) to +100 (very positive)"},
        "total_feedback_items": {"type": "number"},
        "sentiment_breakdown": {
            "type": "object",
            "properties": {
                "positive": {"type": "number"},
                "neutral": {"type": "number"},
                "negative": {"type": "number"},
            },
        },
        "top_feature_requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string"},
                    "demand": {"type": "string", "description": "high/medium/low"},
                    "mentions": {"type": "number"},
                    "sample_quotes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "bugs_and_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "severity": {"type": "string", "description": "critical/major/minor"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "signal": {"type": "string", "description": "positive/negative/neutral"},
                    "description": {"type": "string"},
                },
            },
        },
        "recommended_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "priority": {"type": "string", "description": "P0/P1/P2/P3"},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}, indent=2)

_FEEDBACK_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _FEEDBACK_SYSTEM_PROMPT,
    "maxIterations": 8,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "outputSchema": _FEEDBACK_OUTPUT_SCHEMA,
}

_FEEDBACK_SAMPLE = {
    "feedback": (
        "1. Love the new dashboard redesign! So much cleaner. But the export to PDF button is broken — just shows a blank page.\n\n"
        "2. Why can't I filter by date range on the analytics page? This is basic functionality. Very frustrating.\n\n"
        "3. The mobile app crashes every time I try to upload a photo. Been happening for 2 weeks now.\n\n"
        "4. Your customer support team was incredibly helpful — resolved my billing issue in under 5 minutes. Would love to see a live chat option though.\n\n"
        "5. The API documentation is outdated. Half the endpoints listed don't exist anymore. Wasted 3 hours debugging.\n\n"
        "6. Just switched from CompetitorX to your product. The onboarding flow is 10x better. Only thing I miss is their Slack integration.\n\n"
        "7. Performance has degraded significantly since the last update. Pages that used to load in 1s now take 5-6s.\n\n"
        "8. Would be amazing to have a dark mode. I work late nights and the bright UI is killing my eyes.\n\n"
        "9. The collaboration features are fantastic — real-time editing is smooth and reliable. Best in class.\n\n"
        "10. Pricing feels steep for small teams. We're a 3-person startup and the per-seat model is hard to justify. Considering downgrading.\n\n"
        "11. Scheduled reports feature is a game changer. Saves me 2 hours every Monday morning.\n\n"
        "12. SSO setup was a nightmare. Took our IT team 3 days. Documentation says '5 minutes'. Please improve the SAML config flow."
    ),
}

_FEEDBACK_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "summary = d.get('executive_summary', '')\n"
    "nss = d.get('net_sentiment_score', 0)\n"
    "total = d.get('total_feedback_items', 0)\n"
    "sb = d.get('sentiment_breakdown', {})\n"
    "features = d.get('top_feature_requests', [])\n"
    "bugs = d.get('bugs_and_issues', [])\n"
    "themes = d.get('themes', [])\n"
    "actions = d.get('recommended_actions', [])\n"
    "\n"
    "def nss_color(s):\n"
    "    if s >= 30: return '#16a34a'\n"
    "    if s >= 0: return '#d97706'\n"
    "    return '#dc2626'\n"
    "\n"
    "def nss_label(s):\n"
    "    if s >= 50: return 'Excellent'\n"
    "    if s >= 20: return 'Good'\n"
    "    if s >= 0: return 'Mixed'\n"
    "    if s >= -30: return 'Concerning'\n"
    "    return 'Critical'\n"
    "\n"
    "def sev_color(s):\n"
    "    s = s.lower()\n"
    "    if s == 'critical': return '#dc2626'\n"
    "    if s == 'major': return '#d97706'\n"
    "    return '#6b7280'\n"
    "\n"
    "def pri_color(p):\n"
    "    p = p.upper()\n"
    "    if p == 'P0': return '#dc2626'\n"
    "    if p == 'P1': return '#d97706'\n"
    "    if p == 'P2': return '#2563eb'\n"
    "    return '#6b7280'\n"
    "\n"
    "def demand_badge(d):\n"
    "    colors = {'high': '#dc2626', 'medium': '#d97706', 'low': '#6b7280'}\n"
    "    c = colors.get(d.lower(), '#6b7280')\n"
    "    return f'<span style=\"background:{c};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase\">{d}</span>'\n"
    "\n"
    "# Sentiment bar\n"
    "pos = sb.get('positive', 0)\n"
    "neu = sb.get('neutral', 0)\n"
    "neg = sb.get('negative', 0)\n"
    "t = pos + neu + neg or 1\n"
    "sent_bar = (\n"
    "    f'<div style=\"display:flex;height:8px;border-radius:4px;overflow:hidden;margin-top:8px\">'\n"
    "    f'<div style=\"width:{pos*100//t}%;background:#16a34a\"></div>'\n"
    "    f'<div style=\"width:{neu*100//t}%;background:#d97706\"></div>'\n"
    "    f'<div style=\"width:{neg*100//t}%;background:#dc2626\"></div></div>'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;font-size:12px;color:#64748b;margin-top:4px\">'\n"
    "    f'<span>Positive: {pos}</span><span>Neutral: {neu}</span><span>Negative: {neg}</span></div>'\n"
    ")\n"
    "\n"
    "# Feature requests\n"
    "feat_html = ''\n"
    "for f in features:\n"
    "    quotes = ''.join(f'<div style=\"font-size:12px;color:#64748b;font-style:italic;padding:2px 0\">&ldquo;{q}&rdquo;</div>' for q in f.get('sample_quotes', [])[:2])\n"
    "    feat_html += (\n"
    "        f'<div style=\"padding:12px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "        f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "        f'<span style=\"font-weight:600;font-size:14px;color:#1e293b\">{f.get(\"feature\",\"\")}</span>'\n"
    "        f'<div style=\"display:flex;gap:8px;align-items:center\">{demand_badge(f.get(\"demand\",\"low\"))}'\n"
    "        f'<span style=\"font-size:12px;color:#64748b\">{f.get(\"mentions\",0)} mentions</span></div></div>'\n"
    "        f'{quotes}</div>'\n"
    "    )\n"
    "\n"
    "# Bugs\n"
    "bug_html = ''\n"
    "for b in bugs:\n"
    "    sc = sev_color(b.get('severity', 'minor'))\n"
    "    ev = ''.join(f'<span style=\"font-size:12px;color:#64748b;font-style:italic\">&ldquo;{e}&rdquo;</span>' for e in b.get('evidence', [])[:1])\n"
    "    bug_html += (\n"
    "        f'<div style=\"display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "        f'<span style=\"background:{sc};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;flex-shrink:0\">{b.get(\"severity\",\"minor\")}</span>'\n"
    "        f'<div><div style=\"font-weight:500;font-size:14px;color:#1e293b\">{b.get(\"issue\",\"\")}</div>{ev}</div></div>'\n"
    "    )\n"
    "\n"
    "# Actions\n"
    "act_html = ''\n"
    "for a in actions:\n"
    "    pc = pri_color(a.get('priority', 'P3'))\n"
    "    act_html += (\n"
    "        f'<div style=\"display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "        f'<span style=\"background:{pc};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;flex-shrink:0\">{a.get(\"priority\",\"P3\")}</span>'\n"
    "        f'<div><div style=\"font-weight:500;font-size:14px;color:#1e293b\">{a.get(\"action\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:2px\">{a.get(\"rationale\",\"\")}</div></div></div>'\n"
    "    )\n"
    "\n"
    "# Themes\n"
    "theme_html = ''\n"
    "for th in themes:\n"
    "    sig = th.get('signal', 'neutral')\n"
    "    sig_c = {'positive': '#16a34a', 'negative': '#dc2626'}.get(sig, '#d97706')\n"
    "    sig_icon = {'positive': '&#x25B2;', 'negative': '&#x25BC;'}.get(sig, '&#x25CF;')\n"
    "    theme_html += (\n"
    "        f'<div style=\"padding:10px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "        f'<div style=\"display:flex;align-items:center;gap:6px\">'\n"
    "        f'<span style=\"color:{sig_c};font-size:10px\">{sig_icon}</span>'\n"
    "        f'<span style=\"font-weight:600;font-size:14px;color:#1e293b\">{th.get(\"theme\",\"\")}</span></div>'\n"
    "        f'<div style=\"font-size:13px;color:#64748b;margin-top:2px\">{th.get(\"description\",\"\")}</div></div>'\n"
    "    )\n"
    "\n"
    "nc = nss_color(nss)\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:12px 20px'\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header\n"
    "    f'<div style=\"background:linear-gradient(135deg,#1e293b,#334155);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "    f'<div><div style=\"font-size:24px;font-weight:700\">Customer Feedback Analysis</div>'\n"
    "    f'<div style=\"font-size:14px;color:#94a3b8;margin-top:6px\">{summary}</div></div>'\n"
    "    f'<div style=\"text-align:center\">'\n"
    "    f'<div style=\"font-size:48px;font-weight:800;color:{nc}\">{nss:+d}</div>'\n"
    "    f'<div style=\"font-size:12px;font-weight:600;color:{nc};text-transform:uppercase;letter-spacing:.1em\">{nss_label(nss)}</div>'\n"
    "    f'<div style=\"font-size:11px;color:#94a3b8;margin-top:4px\">{total} reviews</div>'\n"
    "    f'</div></div>{sent_bar}</div>'\n"
    "    # Bugs\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F41B; Bugs & Issues ({len(bugs)})</div><div style=\"{card_body}\">{bug_html}</div></div>'\n"
    "    # Feature Requests\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4A1; Feature Requests ({len(features)})</div><div style=\"{card_body}\">{feat_html}</div></div>'\n"
    "    # Themes\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4CA; Themes & Trends ({len(themes)})</div><div style=\"{card_body}\">{theme_html}</div></div>'\n"
    "    # Actions\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F3AF; Recommended Actions ({len(actions)})</div><div style=\"{card_body}\">{act_html}</div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)

EXAMPLE_WORKFLOWS += [
    # ========================================
    # 12. CUSTOMER FEEDBACK ANALYZER (UI) — HTML report
    # ========================================
    {
        "name": "Customer Feedback Analyzer",
        "description": "Analyzes raw customer reviews using parallel sub-agents for sentiment, feature requests, bugs, and trends. Produces a styled product intelligence report.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_FEEDBACK_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Feedback Analyzer",
                    "type": "AIAgent",
                    "parameters": {
                        **_FEEDBACK_AGENT_BASE,
                        "task": (
                            "Analyze the following customer feedback:\n\n"
                            "{{ $json.feedback }}\n\n"
                            "Run your full analysis pipeline and produce a comprehensive product intelligence report."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _FEEDBACK_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Feedback Analyzer"},
                {"source_node": "Feedback Analyzer", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 13. CUSTOMER FEEDBACK ANALYZER API — Webhook JSON
    # ========================================
    {
        "name": "Customer Feedback Analyzer API",
        "description": "Webhook API for feedback analysis. POST {feedback: '...'} and receive structured JSON with sentiment scores, feature requests, bugs, and prioritized actions.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Webhook", "type": "Webhook", "parameters": {"method": "POST", "responseMode": "lastNode"}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Feedback Analyzer",
                    "type": "AIAgent",
                    "parameters": {
                        **_FEEDBACK_AGENT_BASE,
                        "task": (
                            "Analyze the following customer feedback:\n\n"
                            "{{ $json.body.feedback }}\n\n"
                            "Run your full analysis pipeline and produce a comprehensive product intelligence report."
                        ),
                    },
                    "position": {"x": 450, "y": 300},
                },
                {"name": "Respond", "type": "RespondToWebhook", "parameters": {"statusCode": "200", "responseMode": "lastNode", "responseField": "structured", "contentType": "application/json", "wrapResponse": False}, "position": {"x": 800, "y": 300}},
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Feedback Analyzer"},
                {"source_node": "Feedback Analyzer", "target_node": "Respond"},
            ],
            "settings": {},
        },
    },
]

# ── Meeting Notes → Executive Summary ────────────────────────────────

_MEETING_SYSTEM_PROMPT = (
    "You are an executive meeting intelligence engine that transforms raw meeting notes into actionable summaries.\n\n"
    "## Process\n"
    "Use spawn_agents_parallel to dispatch ALL analysts at once:\n\n"
    "a) **Decision Tracker** — Identify every decision made during the meeting. "
    "For each, note what was decided, who decided it, the context/rationale, and any conditions or caveats. "
    "Flag any decisions that seem incomplete or need follow-up approval.\n\n"
    "b) **Action Item Extractor** — Find every commitment, task, or follow-up. "
    "Assign an owner (from context), determine deadline (explicit or inferred), "
    "and classify priority (P0=urgent/P1=this week/P2=this sprint/P3=backlog). "
    "Look for implicit actions too (e.g. 'we should think about...' = action for someone).\n\n"
    "c) **Risk & Blocker Analyst** — Identify risks, blockers, dependencies, concerns, "
    "and unresolved disagreements. Classify impact (high/medium/low) and note who raised them.\n\n"
    "d) **Key Topics Summarizer** — Summarize each major topic discussed in 2-3 sentences. "
    "Note participants involved, time spent (if apparent), and outcome.\n\n"
    "After ALL sub-agents complete, synthesize into a clean executive summary.\n\n"
    "## Sub-Agent Instructions\n"
    "- Pass the full meeting notes to each sub-agent via context_snippets\n"
    "- Use expected_output to get structured JSON from each\n"
)

_MEETING_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "meeting_title": {"type": "string", "description": "Inferred meeting title/topic"},
        "executive_summary": {"type": "string", "description": "3-5 sentence executive summary for people who weren't there"},
        "participants": {"type": "array", "items": {"type": "string"}, "description": "People mentioned in the notes"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "decided_by": {"type": "string"},
                    "rationale": {"type": "string"},
                    "status": {"type": "string", "description": "final/tentative/needs-approval"},
                },
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "owner": {"type": "string"},
                    "deadline": {"type": "string"},
                    "priority": {"type": "string", "description": "P0/P1/P2/P3"},
                },
            },
        },
        "risks_and_blockers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "risk": {"type": "string"},
                    "impact": {"type": "string", "description": "high/medium/low"},
                    "raised_by": {"type": "string"},
                    "mitigation": {"type": "string"},
                },
            },
        },
        "topics_discussed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "summary": {"type": "string"},
                    "outcome": {"type": "string"},
                },
            },
        },
    },
}, indent=2)

_MEETING_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _MEETING_SYSTEM_PROMPT,
    "maxIterations": 8,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "outputSchema": _MEETING_OUTPUT_SCHEMA,
}

_MEETING_SAMPLE = {
    "notes": (
        "Q1 Product Planning — Jan 15, 2025\n"
        "Attendees: Sarah (VP Product), Mike (Eng Lead), Priya (Design), James (Data), Lisa (QA)\n\n"
        "Sarah opened by reviewing Q4 results. Revenue up 15% but churn increased to 8%. "
        "James showed data indicating churn is concentrated in SMB segment — mostly citing missing integrations and slow support response.\n\n"
        "Mike proposed prioritizing the API v2 rewrite this quarter. Current API has rate limiting issues causing customer complaints. "
        "Sarah agreed but wants to see a phased approach — phase 1 by end of Feb, full rollout by March. "
        "Mike said he needs 2 more backend engineers to hit that timeline. Sarah will discuss with HR.\n\n"
        "Priya presented the new onboarding flow mockups. Team loved the simplified 3-step wizard. "
        "James raised concern about data migration — current wizard handles complex imports that the new design drops. "
        "Decision: keep the simplified flow but add an 'Advanced Import' option behind a toggle. Priya will update designs by Friday.\n\n"
        "Lisa flagged that the test automation coverage dropped to 62% after the December release. "
        "She needs dedicated time from Mike's team to write integration tests. Mike committed to allocating 1 engineer (Tom) for 2 weeks starting next Monday.\n\n"
        "James wants to build a customer health score dashboard. Sarah loves it but says it's P2 — "
        "focus on API and onboarding first. James should draft a proposal by end of month so we can slot it into Q2.\n\n"
        "Open concern from Mike: the CI/CD pipeline is fragile. 3 production incidents in December traced back to flaky deployments. "
        "He wants to invest in infrastructure but it keeps getting deprioritized. Sarah acknowledged it — 'we can't keep kicking this can.' "
        "Agreed to dedicate 20% of engineering time to infra improvements starting Feb 1.\n\n"
        "Meeting ended. Next sync: Jan 29."
    ),
}

_MEETING_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "title = d.get('meeting_title', 'Meeting Summary')\n"
    "summary = d.get('executive_summary', '')\n"
    "participants = d.get('participants', [])\n"
    "decisions = d.get('decisions', [])\n"
    "actions = d.get('action_items', [])\n"
    "risks = d.get('risks_and_blockers', [])\n"
    "topics = d.get('topics_discussed', [])\n"
    "\n"
    "def pri_color(p):\n"
    "    p = p.upper()\n"
    "    if p == 'P0': return '#dc2626'\n"
    "    if p == 'P1': return '#d97706'\n"
    "    if p == 'P2': return '#2563eb'\n"
    "    return '#6b7280'\n"
    "\n"
    "def impact_color(i):\n"
    "    i = i.lower()\n"
    "    if i == 'high': return '#dc2626'\n"
    "    if i == 'medium': return '#d97706'\n"
    "    return '#6b7280'\n"
    "\n"
    "def status_badge(s):\n"
    "    colors = {'final': '#16a34a', 'tentative': '#d97706', 'needs-approval': '#dc2626'}\n"
    "    c = colors.get(s.lower(), '#6b7280')\n"
    "    return f'<span style=\"background:{c};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600\">{s}</span>'\n"
    "\n"
    "parts = ', '.join(participants) if participants else 'Not specified'\n"
    "\n"
    "# Decisions\n"
    "dec_html = ''\n"
    "for dc in decisions:\n"
    "    dec_html += (\n"
    "        f'<div style=\"padding:12px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "        f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "        f'<span style=\"font-weight:600;font-size:14px;color:#1e293b\">{dc.get(\"decision\",\"\")}</span>'\n"
    "        f'{status_badge(dc.get(\"status\",\"final\"))}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:4px\">By: {dc.get(\"decided_by\",\"—\")} | {dc.get(\"rationale\",\"\")}</div></div>'\n"
    "    )\n"
    "\n"
    "# Action items\n"
    "act_html = ''\n"
    "for a in actions:\n"
    "    pc = pri_color(a.get('priority', 'P3'))\n"
    "    act_html += (\n"
    "        f'<div style=\"display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "        f'<span style=\"background:{pc};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;flex-shrink:0\">{a.get(\"priority\",\"P3\")}</span>'\n"
    "        f'<div style=\"flex:1\"><div style=\"font-weight:500;font-size:14px;color:#1e293b\">{a.get(\"action\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:2px\">Owner: <strong>{a.get(\"owner\",\"TBD\")}</strong> | Due: {a.get(\"deadline\",\"TBD\")}</div></div></div>'\n"
    "    )\n"
    "\n"
    "# Risks\n"
    "risk_html = ''\n"
    "for r in risks:\n"
    "    ic = impact_color(r.get('impact', 'low'))\n"
    "    risk_html += (\n"
    "        f'<div style=\"display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "        f'<span style=\"background:{ic};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;flex-shrink:0\">{r.get(\"impact\",\"low\")}</span>'\n"
    "        f'<div><div style=\"font-weight:500;font-size:14px;color:#1e293b\">{r.get(\"risk\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:2px\">Raised by: {r.get(\"raised_by\",\"—\")} | Mitigation: {r.get(\"mitigation\",\"None identified\")}</div></div></div>'\n"
    "    )\n"
    "\n"
    "# Topics\n"
    "topic_html = ''\n"
    "for tp in topics:\n"
    "    topic_html += (\n"
    "        f'<div style=\"padding:12px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "        f'<div style=\"font-weight:600;font-size:14px;color:#1e293b\">{tp.get(\"topic\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:13px;color:#334155;margin-top:4px\">{tp.get(\"summary\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:4px\">Outcome: {tp.get(\"outcome\",\"—\")}</div></div>'\n"
    "    )\n"
    "\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:12px 20px'\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header\n"
    "    f'<div style=\"background:linear-gradient(135deg,#1e293b,#334155);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"font-size:24px;font-weight:700\">{title}</div>'\n"
    "    f'<div style=\"font-size:13px;color:#94a3b8;margin-top:6px\">Participants: {parts}</div>'\n"
    "    f'<div style=\"font-size:14px;color:#cbd5e1;margin-top:12px;line-height:1.6\">{summary}</div></div>'\n"
    "    # Action items\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x2705; Action Items ({len(actions)})</div><div style=\"{card_body}\">{act_html}</div></div>'\n"
    "    # Decisions\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x2696;&#xFE0F; Decisions ({len(decisions)})</div><div style=\"{card_body}\">{dec_html}</div></div>'\n"
    "    # Risks\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x26A0;&#xFE0F; Risks & Blockers ({len(risks)})</div><div style=\"{card_body}\">{risk_html}</div></div>'\n"
    "    # Topics\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4CB; Topics Discussed ({len(topics)})</div><div style=\"{card_body}\">{topic_html}</div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)

EXAMPLE_WORKFLOWS += [
    # ========================================
    # 14. MEETING NOTES (UI) — HTML report
    # ========================================
    {
        "name": "Meeting Notes Analyzer",
        "description": "Transforms raw meeting notes into a structured executive summary with decisions, action items, risks, and topic summaries using parallel sub-agents.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_MEETING_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Meeting Analyzer",
                    "type": "AIAgent",
                    "parameters": {
                        **_MEETING_AGENT_BASE,
                        "task": (
                            "Analyze the following meeting notes and produce a comprehensive executive summary:\n\n"
                            "{{ $json.notes }}\n\n"
                            "Run your full analysis pipeline."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _MEETING_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Meeting Analyzer"},
                {"source_node": "Meeting Analyzer", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 15. MEETING NOTES API — Webhook JSON
    # ========================================
    {
        "name": "Meeting Notes Analyzer API",
        "description": "Webhook API for meeting analysis. POST {notes: '...'} and receive structured JSON with decisions, action items, risks, and topic summaries.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Webhook", "type": "Webhook", "parameters": {"method": "POST", "responseMode": "lastNode"}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Meeting Analyzer",
                    "type": "AIAgent",
                    "parameters": {
                        **_MEETING_AGENT_BASE,
                        "task": (
                            "Analyze the following meeting notes and produce a comprehensive executive summary:\n\n"
                            "{{ $json.body.notes }}\n\n"
                            "Run your full analysis pipeline."
                        ),
                    },
                    "position": {"x": 450, "y": 300},
                },
                {"name": "Respond", "type": "RespondToWebhook", "parameters": {"statusCode": "200", "responseMode": "lastNode", "responseField": "structured", "contentType": "application/json", "wrapResponse": False}, "position": {"x": 800, "y": 300}},
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Meeting Analyzer"},
                {"source_node": "Meeting Analyzer", "target_node": "Respond"},
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

            # Support both formats:
            # - Backend format: { name, nodes, connections }
            # - Legacy format: { name, definition: { nodes, connections } }
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
