"""Seed database with demo workflows for management presentation."""

from __future__ import annotations

import asyncio
import hashlib
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


EXAMPLE_WORKFLOWS = [
    # ========================================
    # 1. CSV TABLE DISPLAY — test tabular file via analytics service
    # ========================================
    {
        "name": "CSV Table Display",
        "description": "Displays a CSV file as a table. Edit the Output node's filePath to point at your .csv, .xlsx, .tsv, or .parquet file.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Table Output",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": "/tmp/sample.csv",
                    },
                    "position": {"x": 400, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Table Output"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 2. PDF DISPLAY — test PDF file rendering
    # ========================================
    {
        "name": "PDF Display",
        "description": "Displays a PDF file. Edit the Output node's filePath to point at your .pdf file.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "PDF Output",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": "/tmp/sample.pdf",
                    },
                    "position": {"x": 400, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "PDF Output"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 3. HTML FILE DISPLAY — test HTML file rendering
    # ========================================
    {
        "name": "HTML File Display",
        "description": "Reads an .html file from disk and renders it. Edit the Output node's filePath to point at your .html file.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "HTML Output",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": "/tmp/sample.html",
                    },
                    "position": {"x": 400, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "HTML Output"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 4. MARKDOWN FILE DISPLAY — test Markdown file rendering
    # ========================================
    {
        "name": "Markdown File Display",
        "description": "Reads a .md file from disk and renders it as Markdown. Edit the Output node's filePath to point at your .md file.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Markdown Output",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": "/tmp/sample.md",
                    },
                    "position": {"x": 400, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Markdown Output"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 5. DYNAMIC FILE VIEWER — filePath from expression
    # ========================================
    {
        "name": "Dynamic File Viewer",
        "description": "File path comes from upstream Set node via expression. Edit the Set node's file_path value, then run. Auto-detects format from extension.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Set Path",
                    "type": "Set",
                    "parameters": {
                        "mode": "manual",
                        "fields": [
                            {"name": "file_path", "value": "/tmp/sample.csv"},
                        ],
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Display",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": "{{ $json.file_path }}",
                    },
                    "position": {"x": 650, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Set Path"},
                {"source_node": "Set Path", "target_node": "Display"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 6. INLINE OUTPUT DEMO — test source=input (existing behavior)
    # ========================================
    {
        "name": "Inline Output Demo",
        "description": "Tests the original source=input behavior. Generates inline HTML from upstream Set node — no file path needed.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Build HTML",
                    "type": "Set",
                    "parameters": {
                        "mode": "manual",
                        "fields": [
                            {
                                "name": "html",
                                "value": "<h1>Inline Output Test</h1><p>This HTML was generated inline from upstream data, not from a file.</p><ul><li>source = input</li><li>format = html</li></ul>",
                            },
                        ],
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "HTML Display",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "html",
                        "content": "{{ $json.html }}",
                    },
                    "position": {"x": 650, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Build HTML"},
                {"source_node": "Build HTML", "target_node": "HTML Display"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 7. SAMPLE → TABLE — data pipeline: sample rows then display as table
    # ========================================
    {
        "name": "Sample → Table",
        "description": "Samples 20 random rows from a CSV file and displays them as a table. Edit the Sample node's filePath to point at your CSV.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Sample Data",
                    "type": "Sample",
                    "parameters": {
                        "sourceType": "file",
                        "fileLocation": "local",
                        "filePath": "/tmp/sample.csv",
                        "method": "random",
                        "sampleSize": 20,
                        "returnData": True,
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Show Table",
                    "type": "Output",
                    "parameters": {
                        "source": "input",
                        "format": "table",
                        "contentField": "data",
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Sample Data"},
                {"source_node": "Sample Data", "target_node": "Show Table"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 8. REPORT → HTML — data pipeline: generate HTML report then display
    # ========================================
    {
        "name": "Report → HTML",
        "description": "Generates a full HTML data report from a CSV and renders it. Edit the Report node's filePath to point at your CSV.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Generate Report",
                    "type": "Report",
                    "parameters": {
                        "sourceType": "file",
                        "fileLocation": "local",
                        "filePath": "/tmp/sample.csv",
                        "title": "Employee Data Report",
                        "previewRows": 10,
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
                {"source_node": "Webhook", "target_node": "Generate Report"},
                {"source_node": "Generate Report", "target_node": "Show Report"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # 9. AGENT → FILE → DISPLAY — AI generates CSV, Output displays it
    # ========================================
    {
        "name": "Agent → File → Display",
        "description": "AI agent generates a CSV file with Python, then the Output node displays it as a table. One-click demo of the full AI-to-visual pipeline.",
        "active": True,
        "definition": {
            "nodes": [
                {
                    "name": "Webhook",
                    "type": "Webhook",
                    "parameters": {"method": "POST"},
                    "position": {"x": 100, "y": 300},
                },
                {
                    "name": "Agent",
                    "type": "AIAgent",
                    "parameters": {
                        "model": "gemini-2.0-flash",
                        "systemPrompt": "You are a data generation assistant. Use the code tool to write Python that generates a CSV file at /tmp/agent_output.csv. The CSV should have realistic-looking data. After writing the file, return ONLY a JSON object: {\"file_path\": \"/tmp/agent_output.csv\"}",
                        "task": "Generate a CSV with 30 rows of sales data: columns Date, Product, Region, Units, Revenue, Profit. Use realistic values for a tech company selling 3 products across 4 regions. Save to /tmp/agent_output.csv.",
                        "temperature": 0.3,
                        "maxIterations": 10,
                    },
                    "position": {"x": 400, "y": 300},
                },
                {
                    "name": "Code Runner",
                    "type": "CodeTool",
                    "parameters": {},
                    "position": {"x": 400, "y": 100},
                },
                {
                    "name": "Display CSV",
                    "type": "Output",
                    "parameters": {
                        "source": "file",
                        "filePath": "/tmp/agent_output.csv",
                    },
                    "position": {"x": 700, "y": 300},
                },
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Agent"},
                {"source_node": "Code Runner", "target_node": "Agent", "connection_type": "subnode", "slot_name": "tools"},
                {"source_node": "Agent", "target_node": "Display CSV"},
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
