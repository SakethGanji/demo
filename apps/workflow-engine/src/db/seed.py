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
