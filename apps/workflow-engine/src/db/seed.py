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


# ── Fraud Investigation & Triage ──────────────────────────────────────

_FRAUD_SYSTEM_PROMPT = (
    "You are a Lead Fraud Investigator AI for a major financial institution.\n\n"
    "## Investigation Protocol\n"
    "When you receive a transaction alert, call spawn_agent three times in the same turn to dispatch ALL three specialist investigators concurrently:\n\n"
    "a) **Transaction Analyzer** — Perform velocity analysis on the transaction amount vs. historical average. "
    "Assess amount anomalies (ratio to avg monthly volume), timing patterns (unusual hours, weekend/holiday), "
    "channel risk (wire transfer vs. ACH vs. card), and transaction frequency in the past 24h/7d/30d. "
    "Flag specific numeric thresholds breached (e.g., >10x average = critical).\n\n"
    "b) **Customer Profile Analyst** — Evaluate the sender's account age (new accounts <90 days are higher risk), "
    "historical transaction behavior and patterns, past fraud alerts or SAR filings, KYC/AML verification status, "
    "account activity consistency, and any recent profile changes (address, phone, beneficiaries).\n\n"
    "c) **Network Analyzer** — Assess counterparty risk: recipient jurisdiction (high-risk countries per FATF grey/black list), "
    "whether this is a first-time transfer to this recipient, beneficial ownership transparency, "
    "linked accounts or shell company indicators, correspondent banking chain risk, "
    "and any known adverse media about the counterparty.\n\n"
    "## Evidence Synthesis\n"
    "After ALL sub-agents complete:\n"
    "1. Use your scratchpad to accumulate and cross-reference evidence from each analyst\n"
    "2. Look for converging signals (e.g., new account + high-risk jurisdiction + 20x normal volume = multiple independent risk indicators)\n"
    "3. Apply the risk matrix:\n"
    "   - CRITICAL (80-100): Multiple high-severity indicators converging, likely structuring or laundering\n"
    "   - HIGH (60-79): Strong anomalies with at least one critical factor\n"
    "   - MEDIUM (30-59): Notable deviations but explainable patterns exist\n"
    "   - LOW (0-29): Minor anomalies, consistent with normal behavior variation\n"
    "4. Produce the structured risk assessment with full evidence chain\n\n"
    "## Sub-Agent Instructions\n"
    "- Include the full transaction alert JSON directly in each sub-agent's task string\n"
    "- Each sub-agent should be thorough — missed evidence in fraud investigation has regulatory consequences\n"
)

_FRAUD_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "risk_score": {
            "type": "number",
            "description": "Overall risk score from 0 (no risk) to 100 (confirmed fraud pattern)",
        },
        "risk_level": {
            "type": "string",
            "description": "Risk classification: LOW, MEDIUM, HIGH, or CRITICAL",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence investigation summary for the case file",
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Which analyst produced this: transaction/customer/network"},
                    "finding": {"type": "string", "description": "Specific finding"},
                    "severity": {"type": "string", "description": "critical/high/medium/low"},
                },
            },
        },
        "recommended_action": {
            "type": "string",
            "description": "APPROVE, HOLD, BLOCK, or ESCALATE",
        },
        "regulatory_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Applicable regulatory flags (e.g., SAR filing required, OFAC screen needed)",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the assessment from 0.0 to 1.0",
        },
    },
}, indent=2)

_FRAUD_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _FRAUD_SYSTEM_PROMPT,
    "maxIterations": 10,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "enableScratchpad": True,
    "outputSchema": _FRAUD_OUTPUT_SCHEMA,
}

_FRAUD_SAMPLE = {
    "alert_id": "FRD-2026-00847",
    "timestamp": "2026-02-23T02:14:33Z",
    "transaction": {
        "id": "TXN-99281374",
        "type": "wire_transfer",
        "amount": 247500.00,
        "currency": "USD",
        "channel": "online_banking",
        "initiated_at": "2026-02-23T02:14:33Z",
        "memo": "Consulting services — Q1 retainer",
    },
    "sender": {
        "account_id": "ACCT-10034821",
        "name": "Meridian Holdings LLC",
        "account_type": "business_checking",
        "account_age_days": 45,
        "avg_monthly_volume": 12400.00,
        "kyc_status": "basic_verified",
        "previous_alerts": 0,
        "last_address_change": "2026-01-10",
    },
    "recipient": {
        "name": "Greenfield Consulting Group",
        "bank": "First National Bank of Cyprus",
        "country": "CY",
        "account_type": "corporate",
        "is_first_transfer": True,
        "swift_code": "FNBCCYNI",
    },
    "risk_signals": {
        "amount_vs_average": 19.96,
        "unusual_hour": True,
        "new_recipient": True,
        "high_risk_jurisdiction": True,
        "velocity_24h": 1,
        "velocity_7d": 3,
    },
}

_FRAUD_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "score = d.get('risk_score', 0)\n"
    "level = d.get('risk_level', 'MEDIUM')\n"
    "summary = d.get('summary', '')\n"
    "evidence = d.get('evidence', [])\n"
    "action = d.get('recommended_action', 'HOLD')\n"
    "reg_flags = d.get('regulatory_flags', [])\n"
    "confidence = d.get('confidence', 0)\n"
    "\n"
    "def level_color(lv):\n"
    "    lv = lv.upper()\n"
    "    if lv == 'CRITICAL': return '#dc2626'\n"
    "    if lv == 'HIGH': return '#d97706'\n"
    "    if lv == 'MEDIUM': return '#2563eb'\n"
    "    return '#16a34a'\n"
    "\n"
    "def sev_color(s):\n"
    "    s = s.lower()\n"
    "    if s == 'critical': return '#dc2626'\n"
    "    if s == 'high': return '#d97706'\n"
    "    if s == 'medium': return '#2563eb'\n"
    "    return '#6b7280'\n"
    "\n"
    "def action_info(a):\n"
    "    a = a.upper()\n"
    "    m = {\n"
    "        'APPROVE': ('#16a34a', 'Transaction cleared — no action required'),\n"
    "        'HOLD': ('#2563eb', 'Transaction held for manual review'),\n"
    "        'BLOCK': ('#d97706', 'Transaction blocked — alert compliance team'),\n"
    "        'ESCALATE': ('#dc2626', 'Transaction blocked — escalate to BSA officer immediately'),\n"
    "    }\n"
    "    return m.get(a, ('#6b7280', 'Unknown action'))\n"
    "\n"
    "lc = level_color(level)\n"
    "ac, a_desc = action_info(action)\n"
    "\n"
    "# Group evidence by source\n"
    "ev_by_source = {}\n"
    "for e in evidence:\n"
    "    src = e.get('source', 'other')\n"
    "    ev_by_source.setdefault(src, []).append(e)\n"
    "\n"
    "source_labels = {\n"
    "    'transaction': ('&#x1F4B8;', 'Transaction Analysis'),\n"
    "    'customer': ('&#x1F464;', 'Customer Profile'),\n"
    "    'network': ('&#x1F310;', 'Network Analysis'),\n"
    "}\n"
    "\n"
    "ev_html = ''\n"
    "for src, items in ev_by_source.items():\n"
    "    icon, label = source_labels.get(src, ('&#x1F50D;', src.title()))\n"
    "    items_html = ''\n"
    "    for e in items:\n"
    "        sc = sev_color(e.get('severity', 'low'))\n"
    "        items_html += (\n"
    "            f'<div style=\"display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "            f'<span style=\"background:{sc};color:#fff;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;flex-shrink:0\">{e.get(\"severity\",\"low\")}</span>'\n"
    "            f'<span style=\"font-size:13px;color:#334155\">{e.get(\"finding\",\"\")}</span></div>'\n"
    "        )\n"
    "    ev_html += (\n"
    "        f'<div style=\"margin-bottom:16px\">'\n"
    "        f'<div style=\"font-weight:700;font-size:14px;color:#1e293b;margin-bottom:6px\">{icon} {label}</div>'\n"
    "        f'{items_html}</div>'\n"
    "    )\n"
    "\n"
    "# Regulatory flags\n"
    "flag_html = ''\n"
    "if reg_flags:\n"
    "    chips = ''.join(f'<span style=\"background:#fef2f2;color:#dc2626;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;border:1px solid #fecaca\">{f}</span>' for f in reg_flags)\n"
    "    flag_html = (\n"
    "        f'<div style=\"background:#fff;border-radius:12px;border:1px solid #fecaca;overflow:hidden;margin-bottom:12px\">'\n"
    "        f'<div style=\"padding:14px 20px;font-weight:700;font-size:14px;color:#dc2626;border-bottom:1px solid #fecaca;background:#fef2f2\">&#x1F6A8; Regulatory Flags</div>'\n"
    "        f'<div style=\"padding:14px 20px;display:flex;flex-wrap:wrap;gap:8px\">{chips}</div></div>'\n"
    "    )\n"
    "\n"
    "# Score donut SVG\n"
    "pct = min(score, 100)\n"
    "circ = 251.2  # 2 * pi * 40\n"
    "offset = circ - (circ * pct / 100)\n"
    "donut = (\n"
    "    f'<svg width=\"100\" height=\"100\" viewBox=\"0 0 100 100\">'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"#334155\" stroke-width=\"8\" opacity=\"0.3\"/>'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"{lc}\" stroke-width=\"8\" '\n"
    "    f'stroke-dasharray=\"{circ}\" stroke-dashoffset=\"{offset:.1f}\" stroke-linecap=\"round\" '\n"
    "    f'transform=\"rotate(-90 50 50)\"/>'\n"
    "    f'<text x=\"50\" y=\"46\" text-anchor=\"middle\" font-size=\"22\" font-weight=\"800\" fill=\"#fff\">{score}</text>'\n"
    "    f'<text x=\"50\" y=\"62\" text-anchor=\"middle\" font-size=\"10\" font-weight=\"600\" fill=\"{lc}\">{level}</text>'\n"
    "    f'</svg>'\n"
    ")\n"
    "\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:16px 20px'\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header with score donut\n"
    "    f'<div style=\"background:linear-gradient(135deg,#0f172a,#1e293b);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "    f'<div style=\"flex:1\"><div style=\"font-size:24px;font-weight:700\">Fraud Risk Assessment</div>'\n"
    "    f'<div style=\"font-size:14px;color:#94a3b8;margin-top:6px\">{summary}</div></div>'\n"
    "    f'<div style=\"flex-shrink:0;margin-left:24px\">{donut}</div></div></div>'\n"
    "    # Recommended Action\n"
    "    f'<div style=\"background:{ac};border-radius:12px;padding:16px 20px;margin-bottom:12px;color:#fff;display:flex;align-items:center;gap:12px\">'\n"
    "    f'<span style=\"font-size:20px\">&#x26A1;</span>'\n"
    "    f'<div><div style=\"font-weight:700;font-size:15px\">Recommended: {action}</div>'\n"
    "    f'<div style=\"font-size:13px;opacity:0.9\">{a_desc}</div></div>'\n"
    "    f'<div style=\"margin-left:auto;font-size:12px;opacity:0.8\">Confidence: {confidence:.0%}</div></div>'\n"
    "    # Regulatory flags\n"
    "    f'{flag_html}'\n"
    "    # Evidence cards\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F50D; Evidence Chain ({len(evidence)} findings)</div><div style=\"{card_body}\">{ev_html}</div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)

# ── Deep Research Agent ──────────────────────────────────────────────

_RESEARCH_SYSTEM_PROMPT = (
    "You are a Research Director AI coordinating a comprehensive technology research brief.\n\n"
    "## Research Protocol\n"
    "When you receive a research brief, call spawn_agent three times in the same turn to dispatch ALL three specialist researchers concurrently:\n\n"
    "a) **Technology Researcher** — Analyze the current state of the technology landscape. "
    "Cover core technical capabilities and recent breakthroughs, major platforms and tools (commercial and open-source), "
    "architecture patterns and implementation approaches, performance benchmarks and limitations, "
    "and the developer ecosystem maturity (documentation, community, tooling). "
    "Rate technical maturity on a 1-10 scale with justification.\n\n"
    "b) **Market Researcher** — Analyze the market dynamics and competitive landscape. "
    "Cover total addressable market size and growth trajectory, key players and their positioning "
    "(startups vs. incumbents), funding trends and notable investments in the past 12 months, "
    "adoption rates across enterprise vs. SMB vs. individual developers, "
    "geographic distribution of activity, and business model patterns (SaaS, API, open-core). "
    "Rate market maturity on a 1-10 scale with justification.\n\n"
    "c) **Academic Researcher** — Analyze the research frontier and emerging directions. "
    "Cover landmark papers and their key contributions, active research groups and institutions, "
    "open problems and unsolved challenges, emerging techniques not yet productized, "
    "safety and alignment research relevant to this domain, and benchmark datasets and evaluation frameworks. "
    "Rate research maturity on a 1-10 scale with justification.\n\n"
    "## Synthesis\n"
    "After ALL sub-agents complete:\n"
    "1. Use your scratchpad to cross-reference findings from all three domains\n"
    "2. Compute an overall maturity score (average of the three domain scores, weighted: tech 0.4, market 0.3, academic 0.3)\n"
    "3. Classify maturity level:\n"
    "   - EMERGING (1-3): Early research stage, few commercial applications\n"
    "   - GROWING (4-5): Active development, early adopters, significant investment\n"
    "   - MATURING (6-7): Mainstream adoption beginning, established players\n"
    "   - MATURE (8-10): Widespread adoption, commoditized tooling\n"
    "4. Identify the top opportunities (things to capitalize on) and top risks (things to watch out for)\n"
    "5. Write a forward-looking 6-12 month outlook\n\n"
    "## Sub-Agent Instructions\n"
    "- Include the full research brief text directly in each sub-agent's task string\n"
    "- Each sub-agent should provide specific examples, names, and data points — not vague generalities\n"
)

_RESEARCH_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "maturity_score": {
            "type": "number",
            "description": "Weighted maturity score from 1.0 to 10.0",
        },
        "maturity_level": {
            "type": "string",
            "description": "EMERGING, GROWING, MATURING, or MATURE",
        },
        "executive_summary": {
            "type": "string",
            "description": "3-4 sentence executive summary of the research landscape",
        },
        "key_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "technology, market, or academic"},
                    "finding": {"type": "string", "description": "Key finding from this domain"},
                    "score": {"type": "number", "description": "Domain maturity score 1-10"},
                },
            },
        },
        "opportunities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Top strategic opportunities to capitalize on",
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Top risks and challenges to monitor",
        },
        "outlook": {
            "type": "string",
            "description": "6-12 month forward-looking outlook paragraph",
        },
    },
}, indent=2)

_RESEARCH_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _RESEARCH_SYSTEM_PROMPT,
    "maxIterations": 10,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "enableScratchpad": True,
    "outputSchema": _RESEARCH_OUTPUT_SCHEMA,
}

_RESEARCH_SAMPLE = {
    "topic": "AI Code Generation and Autonomous Software Engineering",
    "scope": "Comprehensive analysis of AI-powered code generation tools, autonomous coding agents, and their impact on software development workflows.",
    "focus_areas": [
        "LLM-based code generation (Copilot, Cursor, Claude Code, etc.)",
        "Autonomous coding agents (Devin, SWE-Agent, OpenHands, etc.)",
        "Code review and testing automation",
        "Enterprise adoption patterns and ROI data",
        "Impact on developer productivity and software quality",
    ],
    "audience": "Technology leadership evaluating AI coding tools for engineering org adoption",
    "depth": "deep",
}

_RESEARCH_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "score = d.get('maturity_score', 0)\n"
    "level = d.get('maturity_level', 'GROWING')\n"
    "summary = d.get('executive_summary', '')\n"
    "findings = d.get('key_findings', [])\n"
    "opps = d.get('opportunities', [])\n"
    "risks = d.get('risks', [])\n"
    "outlook = d.get('outlook', '')\n"
    "\n"
    "def level_color(lv):\n"
    "    lv = lv.upper()\n"
    "    if lv == 'MATURE': return '#16a34a'\n"
    "    if lv == 'MATURING': return '#2563eb'\n"
    "    if lv == 'GROWING': return '#d97706'\n"
    "    return '#8b5cf6'\n"
    "\n"
    "def domain_info(d):\n"
    "    m = {\n"
    "        'technology': ('&#x1F4BB;', 'Technology', '#2563eb'),\n"
    "        'market': ('&#x1F4C8;', 'Market', '#d97706'),\n"
    "        'academic': ('&#x1F393;', 'Academic', '#8b5cf6'),\n"
    "    }\n"
    "    return m.get(d, ('&#x1F50D;', d.title(), '#6b7280'))\n"
    "\n"
    "lc = level_color(level)\n"
    "\n"
    "# Score donut SVG\n"
    "pct = min(score * 10, 100)\n"
    "circ = 251.2\n"
    "offset = circ - (circ * pct / 100)\n"
    "donut = (\n"
    "    f'<svg width=\"100\" height=\"100\" viewBox=\"0 0 100 100\">'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"#334155\" stroke-width=\"8\" opacity=\"0.3\"/>'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"{lc}\" stroke-width=\"8\" '\n"
    "    f'stroke-dasharray=\"{circ}\" stroke-dashoffset=\"{offset:.1f}\" stroke-linecap=\"round\" '\n"
    "    f'transform=\"rotate(-90 50 50)\"/>'\n"
    "    f'<text x=\"50\" y=\"46\" text-anchor=\"middle\" font-size=\"20\" font-weight=\"800\" fill=\"#fff\">{score:.1f}</text>'\n"
    "    f'<text x=\"50\" y=\"62\" text-anchor=\"middle\" font-size=\"9\" font-weight=\"600\" fill=\"{lc}\">{level}</text>'\n"
    "    f'</svg>'\n"
    ")\n"
    "\n"
    "# Group findings by domain\n"
    "fd_by_domain = {}\n"
    "for f in findings:\n"
    "    dom = f.get('domain', 'other')\n"
    "    fd_by_domain.setdefault(dom, []).append(f)\n"
    "\n"
    "findings_html = ''\n"
    "for dom, items in fd_by_domain.items():\n"
    "    icon, label, color = domain_info(dom)\n"
    "    dom_score = items[0].get('score', 0) if items else 0\n"
    "    bar_pct = min(dom_score * 10, 100)\n"
    "    items_html = ''\n"
    "    for f in items:\n"
    "        items_html += f'<div style=\"padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\">{f.get(\"finding\",\"\")}</div>'\n"
    "    findings_html += (\n"
    "        f'<div style=\"margin-bottom:16px\">'\n"
    "        f'<div style=\"display:flex;align-items:center;gap:8px;margin-bottom:8px\">'\n"
    "        f'<span style=\"font-size:16px\">{icon}</span>'\n"
    "        f'<span style=\"font-weight:700;font-size:14px;color:#1e293b\">{label}</span>'\n"
    "        f'<span style=\"margin-left:auto;font-weight:700;font-size:14px;color:{color}\">{dom_score:.1f}/10</span></div>'\n"
    "        f'<div style=\"background:#e2e8f0;border-radius:4px;height:6px;margin-bottom:8px\">'\n"
    "        f'<div style=\"background:{color};border-radius:4px;height:6px;width:{bar_pct}%\"></div></div>'\n"
    "        f'{items_html}</div>'\n"
    "    )\n"
    "\n"
    "# Opportunities vs Risks columns\n"
    "opp_items = ''.join(f'<div style=\"display:flex;gap:8px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"color:#16a34a;flex-shrink:0\">&#x2714;</span>{o}</div>' for o in opps)\n"
    "risk_items = ''.join(f'<div style=\"display:flex;gap:8px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"color:#dc2626;flex-shrink:0\">&#x26A0;</span>{r}</div>' for r in risks)\n"
    "\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:16px 20px'\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header\n"
    "    f'<div style=\"background:linear-gradient(135deg,#1e1b4b,#312e81);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "    f'<div style=\"flex:1\"><div style=\"font-size:24px;font-weight:700\">Deep Research Report</div>'\n"
    "    f'<div style=\"font-size:14px;color:#a5b4fc;margin-top:6px\">{summary}</div></div>'\n"
    "    f'<div style=\"flex-shrink:0;margin-left:24px\">{donut}</div></div></div>'\n"
    "    # Findings by domain\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F50E; Key Findings by Domain</div><div style=\"{card_body}\">{findings_html}</div></div>'\n"
    "    # Opportunities vs Risks\n"
    "    f'<div style=\"display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px\">'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#16a34a\">&#x1F680; Opportunities</div><div style=\"{card_body}\">{opp_items}</div></div>'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#dc2626\">&#x26A0; Risks</div><div style=\"{card_body}\">{risk_items}</div></div></div>'\n"
    "    # Outlook\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F52E; 6-12 Month Outlook</div>'\n"
    "    f'<div style=\"{card_body};font-size:14px;color:#334155;line-height:1.7\">{outlook}</div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)


# ── Startup Due Diligence Agent ──────────────────────────────────────

_DILIGENCE_SYSTEM_PROMPT = (
    "You are a Senior Investment Analyst AI conducting due diligence on a startup for a venture capital fund.\n\n"
    "## Due Diligence Protocol\n"
    "When you receive startup data, call spawn_agent three times in the same turn to dispatch ALL three specialist analysts concurrently:\n\n"
    "a) **Market Opportunity Analyst** — Evaluate the market thesis. "
    "Analyze total addressable market (TAM) and realistic serviceable obtainable market (SOM), "
    "market timing (why now?), competitive landscape and differentiation, "
    "customer pain point severity (vitamin vs. painkiller), "
    "distribution strategy and go-to-market efficiency, "
    "and secular tailwinds or headwinds. "
    "Score market opportunity 1-10 with justification.\n\n"
    "b) **Team & Execution Analyst** — Evaluate the founding team and execution capability. "
    "Analyze founder-market fit and relevant domain expertise, "
    "technical depth of the team (can they build what they claim?), "
    "execution velocity (product milestones vs. timeline), "
    "hiring ability and team composition gaps, "
    "previous startup experience and exits, "
    "and advisory board and investor quality. "
    "Score team & execution 1-10 with justification.\n\n"
    "c) **Financial & Unit Economics Analyst** — Evaluate the financial health and trajectory. "
    "Analyze revenue growth rate and trajectory (MoM and YoY), "
    "unit economics (CAC, LTV, LTV:CAC ratio, payback period), "
    "net dollar retention and churn metrics, "
    "burn rate and runway at current spend, "
    "capital efficiency (ARR per dollar raised), "
    "and realistic path to profitability or next fundraise. "
    "Score financials 1-10 with justification.\n\n"
    "## Investment Synthesis\n"
    "After ALL sub-agents complete:\n"
    "1. Use your scratchpad to cross-reference findings\n"
    "2. Compute an overall investment score (weighted: market 0.35, team 0.35, financials 0.30)\n"
    "3. Classify recommendation:\n"
    "   - STRONG_PASS (1-3): Fundamental issues, do not invest\n"
    "   - PASS (4-5): Interesting but too many concerns\n"
    "   - CONSIDER (6-7): Promising, worth deeper diligence and partner meeting\n"
    "   - STRONG_BUY (8-9): Compelling opportunity, move to term sheet\n"
    "   - CONVICTION_BET (10): Exceptional, pre-empt if possible\n"
    "4. Write a clear investment thesis (2-3 sentences: why invest or why pass)\n"
    "5. Identify key risks that could break the thesis\n"
    "6. List concrete next steps for the deal team\n\n"
    "## Sub-Agent Instructions\n"
    "- Include the full startup data JSON directly in each sub-agent's task string\n"
    "- Each sub-agent should cite specific numbers from the data to support conclusions\n"
)

_DILIGENCE_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "investment_score": {
            "type": "number",
            "description": "Overall investment score from 1 to 10",
        },
        "recommendation": {
            "type": "string",
            "description": "STRONG_PASS, PASS, CONSIDER, STRONG_BUY, or CONVICTION_BET",
        },
        "thesis": {
            "type": "string",
            "description": "2-3 sentence investment thesis",
        },
        "dimension_scores": {
            "type": "object",
            "properties": {
                "market": {"type": "number", "description": "Market opportunity score 1-10"},
                "team": {"type": "number", "description": "Team & execution score 1-10"},
                "financials": {"type": "number", "description": "Financial health score 1-10"},
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "analyst": {"type": "string", "description": "market, team, or financials"},
                    "finding": {"type": "string", "description": "Key finding"},
                    "sentiment": {"type": "string", "description": "positive, neutral, or negative"},
                },
            },
        },
        "key_risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key risks that could break the investment thesis",
        },
        "next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete next steps for the deal team",
        },
    },
}, indent=2)

_DILIGENCE_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _DILIGENCE_SYSTEM_PROMPT,
    "maxIterations": 10,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "enableScratchpad": True,
    "outputSchema": _DILIGENCE_OUTPUT_SCHEMA,
}

_DILIGENCE_SAMPLE = {
    "company": "Synthwave AI",
    "stage": "Series A",
    "ask": "$18M at $90M pre-money valuation",
    "sector": "Developer Tools / AI Testing Infrastructure",
    "founded": "2024-03",
    "pitch": "AI-native testing infrastructure that automatically generates, maintains, and evolves test suites as codebases change. Replaces brittle hand-written tests with self-healing AI test agents.",
    "team": {
        "founders": [
            {"name": "Maya Chen", "role": "CEO", "background": "Ex-Google Staff Engineer (Chrome DevTools), Stanford CS PhD dropout, 2nd-time founder (prev acquired by DataDog for $12M)"},
            {"name": "Raj Patel", "role": "CTO", "background": "Ex-Meta AI Research, built internal LLM testing infra used by 2000+ engineers, MIT MS"},
        ],
        "headcount": 14,
        "engineering_pct": 0.78,
        "open_roles": ["VP Sales", "Senior ML Engineer", "Developer Advocate"],
    },
    "metrics": {
        "arr": 1_200_000,
        "arr_growth_yoy": 4.2,
        "mrr": 100_000,
        "mrr_growth_mom": 0.18,
        "customers": 34,
        "enterprise_customers": 6,
        "net_dollar_retention": 1.42,
        "gross_churn_monthly": 0.012,
        "avg_contract_value": 35_300,
        "cac": 28_000,
        "ltv": 196_000,
        "ltv_cac_ratio": 7.0,
        "payback_months": 8.4,
        "gross_margin": 0.82,
    },
    "financials": {
        "total_raised": 4_500_000,
        "last_round": "Seed ($4.5M, Sequoia Scout + angels)",
        "monthly_burn": 180_000,
        "runway_months": 11,
        "arr_per_dollar_raised": 0.27,
    },
    "product": {
        "description": "Drop-in CI/CD integration that uses LLMs to understand code changes, generate targeted tests, and auto-fix broken tests when code evolves.",
        "integrations": ["GitHub Actions", "GitLab CI", "Jenkins", "CircleCI"],
        "languages_supported": ["Python", "TypeScript", "Java", "Go"],
        "key_differentiator": "Self-healing tests — when code changes break tests, Synthwave automatically updates the test rather than just flagging failure. Competitors require human-in-the-loop.",
    },
    "market": {
        "tam_estimate": "$12B (software testing market)",
        "competitors": ["Codium AI", "QA Wolf", "Mabl", "Testim (acquired by Tricentis)"],
        "why_now": "LLM costs dropped 10x in 18 months. Code understanding models (Claude, GPT-4) finally good enough for reliable test generation. Engineering orgs under pressure to ship faster with fewer QA resources.",
    },
}

_DILIGENCE_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "score = d.get('investment_score', 0)\n"
    "rec = d.get('recommendation', 'CONSIDER')\n"
    "thesis = d.get('thesis', '')\n"
    "dims = d.get('dimension_scores', {})\n"
    "findings = d.get('findings', [])\n"
    "key_risks = d.get('key_risks', [])\n"
    "next_steps = d.get('next_steps', [])\n"
    "\n"
    "def rec_info(r):\n"
    "    r = r.upper()\n"
    "    m = {\n"
    "        'CONVICTION_BET': ('#16a34a', 'Exceptional — pre-empt if possible'),\n"
    "        'STRONG_BUY': ('#059669', 'Compelling — move to term sheet'),\n"
    "        'CONSIDER': ('#2563eb', 'Promising — deeper diligence needed'),\n"
    "        'PASS': ('#d97706', 'Interesting but too many concerns'),\n"
    "        'STRONG_PASS': ('#dc2626', 'Fundamental issues — do not invest'),\n"
    "    }\n"
    "    return m.get(r, ('#6b7280', r))\n"
    "\n"
    "def sentiment_color(s):\n"
    "    s = s.lower()\n"
    "    if s == 'positive': return '#16a34a'\n"
    "    if s == 'negative': return '#dc2626'\n"
    "    return '#6b7280'\n"
    "\n"
    "def dim_info(d):\n"
    "    m = {\n"
    "        'market': ('&#x1F4C8;', 'Market Opportunity', '#2563eb'),\n"
    "        'team': ('&#x1F465;', 'Team & Execution', '#8b5cf6'),\n"
    "        'financials': ('&#x1F4B0;', 'Financials', '#059669'),\n"
    "    }\n"
    "    return m.get(d, ('&#x1F50D;', d.title(), '#6b7280'))\n"
    "\n"
    "rc, r_desc = rec_info(rec)\n"
    "\n"
    "# Score donut\n"
    "pct = min(score * 10, 100)\n"
    "circ = 251.2\n"
    "offset = circ - (circ * pct / 100)\n"
    "donut = (\n"
    "    f'<svg width=\"100\" height=\"100\" viewBox=\"0 0 100 100\">'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"#334155\" stroke-width=\"8\" opacity=\"0.3\"/>'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"{rc}\" stroke-width=\"8\" '\n"
    "    f'stroke-dasharray=\"{circ}\" stroke-dashoffset=\"{offset:.1f}\" stroke-linecap=\"round\" '\n"
    "    f'transform=\"rotate(-90 50 50)\"/>'\n"
    "    f'<text x=\"50\" y=\"46\" text-anchor=\"middle\" font-size=\"22\" font-weight=\"800\" fill=\"#fff\">{score:.1f}</text>'\n"
    "    f'<text x=\"50\" y=\"62\" text-anchor=\"middle\" font-size=\"8\" font-weight=\"600\" fill=\"{rc}\">/ 10</text>'\n"
    "    f'</svg>'\n"
    ")\n"
    "\n"
    "# Dimension score bars\n"
    "dim_html = ''\n"
    "for key in ['market', 'team', 'financials']:\n"
    "    ds = dims.get(key, 0)\n"
    "    icon, label, color = dim_info(key)\n"
    "    bar_pct = min(ds * 10, 100)\n"
    "    dim_html += (\n"
    "        f'<div style=\"margin-bottom:12px\">'\n"
    "        f'<div style=\"display:flex;align-items:center;gap:8px;margin-bottom:4px\">'\n"
    "        f'<span>{icon}</span><span style=\"font-weight:600;font-size:13px;color:#1e293b\">{label}</span>'\n"
    "        f'<span style=\"margin-left:auto;font-weight:700;font-size:14px;color:{color}\">{ds:.1f}</span></div>'\n"
    "        f'<div style=\"background:#e2e8f0;border-radius:4px;height:8px\">'\n"
    "        f'<div style=\"background:{color};border-radius:4px;height:8px;width:{bar_pct}%\"></div></div></div>'\n"
    "    )\n"
    "\n"
    "# Group findings by analyst\n"
    "fd_by_analyst = {}\n"
    "for f in findings:\n"
    "    a = f.get('analyst', 'other')\n"
    "    fd_by_analyst.setdefault(a, []).append(f)\n"
    "\n"
    "analyst_labels = {\n"
    "    'market': ('&#x1F4C8;', 'Market Analyst'),\n"
    "    'team': ('&#x1F465;', 'Team Analyst'),\n"
    "    'financials': ('&#x1F4B0;', 'Financial Analyst'),\n"
    "}\n"
    "\n"
    "fd_html = ''\n"
    "for a, items in fd_by_analyst.items():\n"
    "    icon, label = analyst_labels.get(a, ('&#x1F50D;', a.title()))\n"
    "    items_html = ''\n"
    "    for f in items:\n"
    "        sc = sentiment_color(f.get('sentiment', 'neutral'))\n"
    "        items_html += (\n"
    "            f'<div style=\"display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "            f'<span style=\"background:{sc};color:#fff;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;flex-shrink:0\">{f.get(\"sentiment\",\"neutral\")}</span>'\n"
    "            f'<span style=\"font-size:13px;color:#334155\">{f.get(\"finding\",\"\")}</span></div>'\n"
    "        )\n"
    "    fd_html += (\n"
    "        f'<div style=\"margin-bottom:16px\">'\n"
    "        f'<div style=\"font-weight:700;font-size:14px;color:#1e293b;margin-bottom:6px\">{icon} {label}</div>'\n"
    "        f'{items_html}</div>'\n"
    "    )\n"
    "\n"
    "# Risks\n"
    "risk_items = ''.join(f'<div style=\"display:flex;gap:8px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"color:#dc2626;flex-shrink:0\">&#x26A0;</span>{r}</div>' for r in key_risks)\n"
    "\n"
    "# Next steps\n"
    "step_items = ''.join(f'<div style=\"display:flex;gap:8px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"color:#2563eb;flex-shrink:0\">&#x27A1;</span>{s}</div>' for s in next_steps)\n"
    "\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:16px 20px'\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header\n"
    "    f'<div style=\"background:linear-gradient(135deg,#0f172a,#1e3a5f);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "    f'<div style=\"flex:1\"><div style=\"font-size:24px;font-weight:700\">Investment Due Diligence</div>'\n"
    "    f'<div style=\"font-size:14px;color:#94a3b8;margin-top:6px\">{thesis}</div></div>'\n"
    "    f'<div style=\"flex-shrink:0;margin-left:24px\">{donut}</div></div></div>'\n"
    "    # Recommendation banner\n"
    "    f'<div style=\"background:{rc};border-radius:12px;padding:16px 20px;margin-bottom:12px;color:#fff;display:flex;align-items:center;gap:12px\">'\n"
    "    f'<span style=\"font-size:20px\">&#x1F3AF;</span>'\n"
    "    f'<div><div style=\"font-weight:700;font-size:15px\">{rec.replace(\"_\", \" \")}</div>'\n"
    "    f'<div style=\"font-size:13px;opacity:0.9\">{r_desc}</div></div></div>'\n"
    "    # Dimension scores\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4CA; Dimension Scores</div><div style=\"{card_body}\">{dim_html}</div></div>'\n"
    "    # Findings\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F50D; Analyst Findings ({len(findings)} items)</div><div style=\"{card_body}\">{fd_html}</div></div>'\n"
    "    # Risks & Next Steps\n"
    "    f'<div style=\"display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px\">'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#dc2626\">&#x1F6A8; Key Risks</div><div style=\"{card_body}\">{risk_items}</div></div>'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#2563eb\">&#x27A1; Next Steps</div><div style=\"{card_body}\">{step_items}</div></div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)


# ── Content Quality Pipeline ─────────────────────────────────────────

_CONTENT_SYSTEM_PROMPT = (
    "You are a Content Director AI managing a content quality pipeline for publication.\n\n"
    "## Content Pipeline Protocol\n"
    "When you receive a content brief, call spawn_agent three times in the same turn to dispatch ALL three specialists concurrently:\n\n"
    "a) **Writer** — Write the full article based on the brief. "
    "Follow the specified tone and style guidelines. "
    "Target the specified audience. "
    "Cover all key points from the brief. "
    "Produce a complete, publication-ready draft with a compelling headline, "
    "strong opening hook, well-structured body with subheadings, "
    "and a memorable closing. Aim for the specified word count. "
    "The draft should be engaging, opinionated, and backed by concrete examples.\n\n"
    "b) **Fact-Checker** — Based on the brief's claims and key points, "
    "verify each factual claim that will likely appear in the article. "
    "For each claim, provide: the claim text, a verdict (VERIFIED, PARTIALLY_TRUE, UNVERIFIED, or FALSE), "
    "a source or reasoning for the verdict, and a suggested correction if needed. "
    "Focus on statistics, named entities, historical claims, and technical assertions. "
    "Also flag any claims that are common misconceptions.\n\n"
    "c) **Editor** — Review the brief and anticipate the article's quality dimensions. "
    "Provide ratings (1-10) and specific feedback on: headline strength, "
    "opening hook effectiveness, argument structure and flow, "
    "evidence usage and specificity, voice consistency with the target tone, "
    "audience appropriateness, and closing impact. "
    "List 3-5 specific revision suggestions ranked by priority.\n\n"
    "## Content Synthesis\n"
    "After ALL sub-agents complete:\n"
    "1. Use your scratchpad to integrate the writer's draft with fact-check results and editorial feedback\n"
    "2. Compute an overall quality score (weighted: writing quality 0.4, factual accuracy 0.3, editorial polish 0.3)\n"
    "3. Classify status:\n"
    "   - PUBLISH_READY (8-10): Minor tweaks only, can go live\n"
    "   - REVISIONS_NEEDED (5-7): Good foundation but needs specific improvements\n"
    "   - MAJOR_REWRITE (3-4): Significant structural or content issues\n"
    "   - BRIEF_REJECTED (1-2): Brief is unclear or topic is not viable\n"
    "4. Compile the final output with the draft, fact-check results, editorial feedback, and priority revisions\n\n"
    "## Sub-Agent Instructions\n"
    "- Include the full content brief directly in each sub-agent's task string\n"
    "- The Writer should produce the actual article text, not an outline\n"
    "- The Fact-Checker should check claims independently of the Writer's output\n"
    "- The Editor should evaluate based on the brief's requirements and general quality standards\n"
)

_CONTENT_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "quality_score": {
            "type": "number",
            "description": "Overall quality score from 1 to 10",
        },
        "status": {
            "type": "string",
            "description": "PUBLISH_READY, REVISIONS_NEEDED, MAJOR_REWRITE, or BRIEF_REJECTED",
        },
        "draft": {
            "type": "string",
            "description": "The full article draft text with headline",
        },
        "fact_check_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "The factual claim checked"},
                    "verdict": {"type": "string", "description": "VERIFIED, PARTIALLY_TRUE, UNVERIFIED, or FALSE"},
                    "source": {"type": "string", "description": "Source or reasoning for the verdict"},
                    "correction": {"type": "string", "description": "Suggested correction if needed"},
                },
            },
        },
        "editorial_feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "description": "What aspect is being rated"},
                    "rating": {"type": "number", "description": "Rating from 1-10"},
                    "feedback": {"type": "string", "description": "Specific feedback"},
                },
            },
        },
        "priority_revisions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ranked list of specific revision suggestions",
        },
    },
}, indent=2)

_CONTENT_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _CONTENT_SYSTEM_PROMPT,
    "maxIterations": 10,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "enableScratchpad": True,
    "outputSchema": _CONTENT_OUTPUT_SCHEMA,
}

_CONTENT_SAMPLE = {
    "type": "blog_post",
    "topic": "The Hidden Cost of AI-Generated Code",
    "word_count": 1500,
    "tone": "Thoughtful and provocative — challenges conventional wisdom without being contrarian for its own sake. Data-driven but accessible.",
    "audience": "Senior software engineers and engineering managers who are actively using AI coding tools and wondering about long-term implications.",
    "key_points": [
        "AI-generated code is optimized for passing code review, not for long-term maintainability",
        "Teams report 30-40% productivity gains initially, but technical debt accumulates silently",
        "The 'copy-paste at scale' problem: AI amplifies existing patterns, including bad ones",
        "Junior developers are learning to prompt instead of learning to code — implications for the talent pipeline",
        "The testing paradox: AI-generated code often lacks edge case coverage because models optimize for happy paths",
        "Concrete strategies for getting AI productivity gains without the hidden costs",
    ],
    "seo_keywords": ["AI code generation", "technical debt", "AI coding tools", "software quality"],
    "references_to_include": [
        "GitClear 2024 study on code churn from AI-generated code",
        "Stack Overflow developer survey data on AI tool adoption",
        "Google's internal study on code review acceptance rates",
    ],
    "do_not": [
        "Don't be Luddite — acknowledge real benefits before critiquing",
        "Don't make it about specific tools — focus on the general pattern",
        "Don't end with 'only time will tell' — provide actionable takeaways",
    ],
}

_CONTENT_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "score = d.get('quality_score', 0)\n"
    "status = d.get('status', 'REVISIONS_NEEDED')\n"
    "draft = d.get('draft', '')\n"
    "fact_checks = d.get('fact_check_results', [])\n"
    "editorial = d.get('editorial_feedback', [])\n"
    "revisions = d.get('priority_revisions', [])\n"
    "\n"
    "def status_info(s):\n"
    "    s = s.upper()\n"
    "    m = {\n"
    "        'PUBLISH_READY': ('#16a34a', 'Ready for publication'),\n"
    "        'REVISIONS_NEEDED': ('#d97706', 'Needs targeted revisions'),\n"
    "        'MAJOR_REWRITE': ('#dc2626', 'Significant rewrite required'),\n"
    "        'BRIEF_REJECTED': ('#6b7280', 'Brief needs rework'),\n"
    "    }\n"
    "    return m.get(s, ('#6b7280', s))\n"
    "\n"
    "def verdict_info(v):\n"
    "    v = v.upper()\n"
    "    m = {\n"
    "        'VERIFIED': ('#16a34a', '&#x2714;'),\n"
    "        'PARTIALLY_TRUE': ('#d97706', '&#x25CB;'),\n"
    "        'UNVERIFIED': ('#6b7280', '?'),\n"
    "        'FALSE': ('#dc2626', '&#x2718;'),\n"
    "    }\n"
    "    return m.get(v, ('#6b7280', '?'))\n"
    "\n"
    "sc, s_desc = status_info(status)\n"
    "\n"
    "# Score donut\n"
    "pct = min(score * 10, 100)\n"
    "circ = 251.2\n"
    "offset = circ - (circ * pct / 100)\n"
    "donut = (\n"
    "    f'<svg width=\"100\" height=\"100\" viewBox=\"0 0 100 100\">'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"#334155\" stroke-width=\"8\" opacity=\"0.3\"/>'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"{sc}\" stroke-width=\"8\" '\n"
    "    f'stroke-dasharray=\"{circ}\" stroke-dashoffset=\"{offset:.1f}\" stroke-linecap=\"round\" '\n"
    "    f'transform=\"rotate(-90 50 50)\"/>'\n"
    "    f'<text x=\"50\" y=\"46\" text-anchor=\"middle\" font-size=\"22\" font-weight=\"800\" fill=\"#fff\">{score:.1f}</text>'\n"
    "    f'<text x=\"50\" y=\"62\" text-anchor=\"middle\" font-size=\"8\" font-weight=\"600\" fill=\"{sc}\">/ 10</text>'\n"
    "    f'</svg>'\n"
    ")\n"
    "\n"
    "# Draft preview (convert newlines to paragraphs)\n"
    "draft_paras = ''.join(f'<p style=\"margin-bottom:12px\">{p.strip()}</p>' for p in draft.splitlines() if p.strip())\n"
    "\n"
    "# Fact-check items\n"
    "fc_html = ''\n"
    "for fc in fact_checks:\n"
    "    vc, vi = verdict_info(fc.get('verdict', 'UNVERIFIED'))\n"
    "    correction = fc.get('correction', '')\n"
    "    corr_html = f'<div style=\"font-size:12px;color:#d97706;margin-top:4px\">&#x270F; {correction}</div>' if correction else ''\n"
    "    fc_html += (\n"
    "        f'<div style=\"padding:10px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "        f'<div style=\"display:flex;gap:10px;align-items:flex-start\">'\n"
    "        f'<span style=\"background:{vc};color:#fff;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0\">{vi}</span>'\n"
    "        f'<div style=\"flex:1\">'\n"
    "        f'<div style=\"font-size:13px;color:#1e293b;font-weight:600\">{fc.get(\"claim\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:2px\">{fc.get(\"source\",\"\")}</div>'\n"
    "        f'{corr_html}</div></div></div>'\n"
    "    )\n"
    "\n"
    "# Editorial ratings\n"
    "ed_html = ''\n"
    "for ef in editorial:\n"
    "    r = ef.get('rating', 0)\n"
    "    bar_pct = min(r * 10, 100)\n"
    "    bar_color = '#16a34a' if r >= 8 else '#d97706' if r >= 5 else '#dc2626'\n"
    "    ed_html += (\n"
    "        f'<div style=\"margin-bottom:12px\">'\n"
    "        f'<div style=\"display:flex;align-items:center;gap:8px;margin-bottom:4px\">'\n"
    "        f'<span style=\"font-weight:600;font-size:13px;color:#1e293b;flex:1\">{ef.get(\"dimension\",\"\")}</span>'\n"
    "        f'<span style=\"font-weight:700;font-size:14px;color:{bar_color}\">{r}/10</span></div>'\n"
    "        f'<div style=\"background:#e2e8f0;border-radius:4px;height:6px;margin-bottom:6px\">'\n"
    "        f'<div style=\"background:{bar_color};border-radius:4px;height:6px;width:{bar_pct}%\"></div></div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b\">{ef.get(\"feedback\",\"\")}</div></div>'\n"
    "    )\n"
    "\n"
    "# Priority revisions\n"
    "rev_html = ''\n"
    "for i, rev in enumerate(revisions, 1):\n"
    "    rev_html += f'<div style=\"display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"background:#2563eb;color:#fff;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0\">{i}</span>{rev}</div>'\n"
    "\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:16px 20px'\n"
    "\n"
    "# Count fact-check verdicts\n"
    "v_counts = {}\n"
    "for fc in fact_checks:\n"
    "    v = fc.get('verdict', 'UNVERIFIED').upper()\n"
    "    v_counts[v] = v_counts.get(v, 0) + 1\n"
    "v_summary = ', '.join(f'{c} {v.lower()}' for v, c in v_counts.items())\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header\n"
    "    f'<div style=\"background:linear-gradient(135deg,#1a1a2e,#16213e);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "    f'<div style=\"flex:1\"><div style=\"font-size:24px;font-weight:700\">Content Quality Report</div>'\n"
    "    f'<div style=\"font-size:14px;color:#94a3b8;margin-top:6px\">Pipeline: Writer &#x2192; Fact-Checker &#x2192; Editor</div></div>'\n"
    "    f'<div style=\"flex-shrink:0;margin-left:24px\">{donut}</div></div></div>'\n"
    "    # Status banner\n"
    "    f'<div style=\"background:{sc};border-radius:12px;padding:16px 20px;margin-bottom:12px;color:#fff;display:flex;align-items:center;gap:12px\">'\n"
    "    f'<span style=\"font-size:20px\">&#x1F4DD;</span>'\n"
    "    f'<div><div style=\"font-weight:700;font-size:15px\">{status.replace(\"_\", \" \")}</div>'\n"
    "    f'<div style=\"font-size:13px;opacity:0.9\">{s_desc}</div></div></div>'\n"
    "    # Draft preview\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4C4; Article Draft</div>'\n"
    "    f'<div style=\"{card_body};font-size:14px;color:#334155;line-height:1.7;max-height:400px;overflow-y:auto\">{draft_paras}</div></div>'\n"
    "    # Fact-check results\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x2714; Fact-Check Results ({len(fact_checks)} claims — {v_summary})</div><div style=\"{card_body}\">{fc_html}</div></div>'\n"
    "    # Editorial feedback\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4DD; Editorial Ratings</div><div style=\"{card_body}\">{ed_html}</div></div>'\n"
    "    # Priority revisions\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F527; Priority Revisions</div><div style=\"{card_body}\">{rev_html}</div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)


# ── Customer Service Escalation Triage ────────────────────────────────

_ESCALATION_SYSTEM_PROMPT = (
    "You are an Escalation Director AI for a private banking institution.\n\n"
    "## Escalation Protocol\n"
    "When you receive an escalation case, call spawn_agent three times in the same turn to dispatch ALL three specialists concurrently:\n\n"
    "a) **Sentiment & Intent Analyst** — Analyze the full interaction history chronologically. "
    "Determine the client's emotional state at each touchpoint and the overall trajectory (escalating, stable, or de-escalating). "
    "Calculate a churn probability (0-100) based on: number of failed resolution attempts, "
    "severity of service failures, client tenure and value tier, competitor mentions, and tone progression. "
    "Identify the client's unstated needs beneath the surface complaints — what do they actually want beyond the specific fixes? "
    "(e.g., feeling valued, having a dedicated point of contact, confidence their wealth is being managed competently). "
    "Flag any language indicating imminent action (legal threats, regulatory complaints, media mentions).\n\n"
    "b) **Customer Value & Retention Analyst** — Calculate the full financial picture. "
    "Compute lifetime value based on current products, tenure, and growth trajectory. "
    "Estimate revenue-at-risk if the client leaves (annual fees, interest income, AUM fees, card interchange). "
    "Calculate total cost-of-loss including acquisition cost to replace, referral network value, and reputational risk. "
    "Model cost-of-resolution for the specific issues raised. "
    "Compute ROI of retention (cost-of-loss minus cost-of-resolution). "
    "Provide a retention offer recommendation calibrated to the client's value tier — "
    "the offer should be generous enough to retain but justified by the financial math.\n\n"
    "c) **Resolution Strategist** — Design a comprehensive multi-phase resolution plan. "
    "Immediate actions (within 24 hours): specific steps, named owner roles, and deadlines. "
    "Short-term actions (1-2 weeks): follow-up steps to rebuild trust. "
    "Long-term actions (30-90 days): systemic fixes and relationship strengthening. "
    "Each action must have a clear owner, timeline, and success metric. "
    "Flag any compliance considerations (Reg E timelines, fiduciary obligations, fair lending). "
    "Identify SLA breaches in the interaction history with severity ratings. "
    "Surface systemic issues that this case reveals about the bank's operations.\n\n"
    "## Synthesis\n"
    "After ALL sub-agents complete:\n"
    "1. Use your scratchpad to cross-reference all three analyses\n"
    "2. Assign an overall risk score (0-100) and priority level:\n"
    "   - P1_CRITICAL (85-100): Imminent loss of high-value client, regulatory exposure, or reputational risk\n"
    "   - P2_HIGH (60-84): Significant flight risk, multiple service failures, escalating sentiment\n"
    "   - P3_MEDIUM (30-59): Moderate dissatisfaction, containable with prompt action\n"
    "   - P4_LOW (0-29): Minor issue, standard resolution path\n"
    "3. Compile the executive escalation memo with all sections populated\n"
    "4. Write a concise executive summary (2-3 sentences) that a bank CIO could read in 10 seconds\n\n"
    "## Sub-Agent Instructions\n"
    "- Include the full escalation case JSON directly in each sub-agent's task string\n"
    "- Each sub-agent should be thorough — mishandling a high-value private banking client has severe financial and reputational consequences\n"
)

_ESCALATION_OUTPUT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "priority": {
            "type": "string",
            "description": "Priority level: P1_CRITICAL, P2_HIGH, P3_MEDIUM, or P4_LOW",
        },
        "overall_risk_score": {
            "type": "number",
            "description": "Overall risk score from 0 (low risk) to 100 (critical risk)",
        },
        "customer_sentiment": {
            "type": "object",
            "properties": {
                "current_state": {"type": "string", "description": "Current emotional state of the client"},
                "trajectory": {"type": "string", "description": "ESCALATING, STABLE, or DE_ESCALATING"},
                "churn_probability": {"type": "number", "description": "Probability of client departure 0-100"},
            },
        },
        "financial_impact": {
            "type": "object",
            "properties": {
                "lifetime_value": {"type": "string", "description": "Estimated client lifetime value"},
                "revenue_at_risk": {"type": "string", "description": "Annual revenue at risk if client leaves"},
                "cost_of_loss": {"type": "string", "description": "Total cost including replacement and referral loss"},
                "cost_of_resolution": {"type": "string", "description": "Cost to resolve all current issues"},
                "roi_of_retention": {"type": "string", "description": "Return on investment for retention effort"},
            },
        },
        "resolution_plan": {
            "type": "object",
            "properties": {
                "immediate_actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "owner": {"type": "string"},
                            "deadline": {"type": "string"},
                            "priority": {"type": "string"},
                        },
                    },
                },
                "short_term": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "owner": {"type": "string"},
                            "timeline": {"type": "string"},
                        },
                    },
                },
                "long_term": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "owner": {"type": "string"},
                            "timeline": {"type": "string"},
                        },
                    },
                },
            },
        },
        "retention_offer": {
            "type": "object",
            "properties": {
                "tier": {"type": "string", "description": "Offer tier based on client value"},
                "components": {"type": "array", "items": {"type": "string"}, "description": "Individual offer components"},
                "total_value": {"type": "string", "description": "Total monetary value of the retention offer"},
                "justification": {"type": "string", "description": "Financial justification for the offer"},
            },
        },
        "compliance_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Regulatory and compliance considerations",
        },
        "sla_breaches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Type of SLA breach"},
                    "details": {"type": "string", "description": "Specific details of the breach"},
                    "severity": {"type": "string", "description": "critical, high, medium, or low"},
                },
            },
        },
        "systemic_issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Systemic operational issues revealed by this case",
        },
        "executive_summary": {
            "type": "string",
            "description": "2-3 sentence executive summary for bank leadership",
        },
    },
}, indent=2)

_ESCALATION_AGENT_BASE = {
    "model": "gemini-2.5-flash",
    "systemPrompt": _ESCALATION_SYSTEM_PROMPT,
    "maxIterations": 10,
    "temperature": 0.2,
    "enableSubAgents": True,
    "maxAgentDepth": 2,
    "allowRecursiveSpawn": False,
    "enablePlanning": True,
    "enableScratchpad": True,
    "outputSchema": _ESCALATION_OUTPUT_SCHEMA,
}

_ESCALATION_SAMPLE = {
    "case_id": "ESC-2026-04291",
    "opened": "2026-02-24T09:14:00Z",
    "source": "CRM_ESCALATION_TRIGGER",
    "priority_override": "MANAGER_FLAGGED",
    "customer": {
        "name": "Margaret Chen-Whitfield",
        "client_id": "PB-00048217",
        "tier": "Private Banking — Platinum",
        "relationship_start": "2018-03-15",
        "tenure_years": 7.9,
        "total_relationship_value": 2_420_000,
        "annual_revenue": 68_400,
        "products": [
            {"type": "Premium Checking", "balance": 245_000, "annual_fees": 0, "note": "Fee-waived at Platinum tier"},
            {"type": "High-Yield Savings", "balance": 520_000, "apy": 4.85},
            {"type": "Wealth Management", "aum": 1_380_000, "fee_bps": 75, "annual_fee": 10_350, "strategy": "Growth-oriented moderate risk"},
            {"type": "Mortgage", "balance": 275_000, "rate": 3.125, "term": "30yr fixed", "note": "Originated 2020, excellent payment history"},
            {"type": "Amex Centurion", "annual_spend": 187_000, "annual_fee": 5_000, "rewards_tier": "5x points on travel"},
        ],
        "satisfaction_history": [
            {"date": "2024-Q4", "nps": 9.2, "comment": "Exceptional service, love my advisor James"},
            {"date": "2025-Q1", "nps": 8.8, "comment": "Solid as always"},
            {"date": "2025-Q2", "nps": 7.1, "comment": "Portal redesign is confusing"},
            {"date": "2025-Q3", "nps": 5.4, "comment": "Hard to reach anyone, long hold times"},
            {"date": "2025-Q4", "nps": 3.1, "comment": "Seriously considering moving everything to Goldman"},
        ],
        "previous_escalations": [
            {"date": "2023-06", "issue": "Mortgage rate lock dispute", "resolution": "Honored original rate, $500 credit", "outcome": "Satisfied"},
        ],
        "competitor_mentions": ["Goldman Sachs Private Wealth Management"],
        "referral_network": {
            "clients_referred": 3,
            "referral_aum": 890_000,
            "active_referrals": True,
        },
    },
    "interaction_history": [
        {
            "date": "2026-01-08T14:22:00Z",
            "channel": "Phone",
            "agent": "Sarah Kim (Customer Service Rep)",
            "duration_minutes": 34,
            "issue": "Unauthorized $847.00 wire fee on international transfer to Hong Kong (family support payment)",
            "outcome": "Agent confirmed fee was applied in error due to system migration. Submitted refund request. Told client 5-7 business days.",
            "client_sentiment": "Annoyed but patient",
            "resolution": "PENDING",
        },
        {
            "date": "2026-01-18T10:05:00Z",
            "channel": "Phone",
            "agent": "Call Center (18 min hold)",
            "duration_minutes": 12,
            "issue": "Fee refund still not processed after 10 days. Client also asked to speak with dedicated advisor James Morrison.",
            "outcome": "Agent could not locate refund request in system. Resubmitted. Informed client James Morrison left the bank 3 weeks ago — no transition plan in place.",
            "client_sentiment": "Frustrated, voice raised",
            "resolution": "PENDING",
        },
        {
            "date": "2026-01-29T16:40:00Z",
            "channel": "Secure Message (Portal)",
            "agent": "Auto-response",
            "duration_minutes": 0,
            "issue": "Client sent detailed message about: (1) still no fee refund, (2) no new advisor assigned, (3) wealth management portal showing incorrect allocation — equities showing 72% when target is 55%",
            "outcome": "Auto-acknowledgment sent. No human follow-up for 6 business days.",
            "client_sentiment": "Formal, documented tone — building a paper trail",
            "resolution": "NO_RESPONSE",
        },
        {
            "date": "2026-02-10T09:30:00Z",
            "channel": "Phone",
            "agent": "David Park (Senior Service Rep)",
            "duration_minutes": 47,
            "issue": "Client called demanding to speak with a branch manager. Recounted full history of failures. Mentioned she has a meeting scheduled with Goldman Sachs Private Wealth Management next week.",
            "outcome": "David escalated to branch manager who was unavailable. Promised callback within 2 hours. Callback came 26 hours later from a different agent who was unaware of the case history.",
            "client_sentiment": "Cold, controlled anger. Used phrases: 'pattern of incompetence', 'fiduciary obligation', 'regulatory complaint'",
            "resolution": "FAILED_ESCALATION",
        },
        {
            "date": "2026-02-22T11:15:00Z",
            "channel": "Email to CEO Office",
            "agent": "Executive Relations (auto-logged)",
            "duration_minutes": 0,
            "issue": "Formal complaint letter to CEO. Lists all failures with dates. States intent to move all accounts within 30 days unless 'meaningful corrective action' is taken. Cc'd personal attorney.",
            "outcome": "Routed to Executive Relations team. Case flagged P1.",
            "client_sentiment": "Resolved, methodical — has made decision, offering final chance",
            "resolution": "PENDING_EXECUTIVE_REVIEW",
        },
    ],
    "open_issues": [
        {"id": "TKT-88201", "type": "Fee Dispute", "amount": 847.00, "status": "UNRESOLVED", "age_days": 47, "sla_target_days": 10},
        {"id": "TKT-88455", "type": "Advisor Reassignment", "status": "UNRESOLVED", "age_days": 37, "sla_target_days": 5},
        {"id": "TKT-88902", "type": "Portfolio Display Error", "status": "UNRESOLVED", "age_days": 26, "sla_target_days": 3},
        {"id": "TKT-89301", "type": "Failed Escalation Callback", "status": "UNRESOLVED", "age_days": 14, "sla_target_days": 2},
    ],
    "bank_context": {
        "institution": "Meridian Private Bank",
        "advisor_departed": {
            "name": "James Morrison",
            "departure_date": "2025-12-20",
            "clients_orphaned": 24,
            "handoff_completed": False,
        },
        "system_migration": {
            "name": "CoreBanking v4.2 Migration",
            "date": "2025-12-01",
            "known_issues": ["International wire fee calculation errors", "Wealth portal data sync delays"],
        },
    },
}

_ESCALATION_HTML_CODE = (
    "d = json_data.get('structured') or json_data\n"
    "\n"
    "priority = d.get('priority', 'P2_HIGH')\n"
    "risk_score = d.get('overall_risk_score', 0)\n"
    "sentiment = d.get('customer_sentiment', {})\n"
    "financial = d.get('financial_impact', {})\n"
    "plan = d.get('resolution_plan', {})\n"
    "offer = d.get('retention_offer', {})\n"
    "compliance = d.get('compliance_flags', [])\n"
    "sla_breaches = d.get('sla_breaches', [])\n"
    "systemic = d.get('systemic_issues', [])\n"
    "exec_summary = d.get('executive_summary', '')\n"
    "\n"
    "def priority_color(p):\n"
    "    p = p.upper()\n"
    "    if 'P1' in p or 'CRITICAL' in p: return '#dc2626'\n"
    "    if 'P2' in p or 'HIGH' in p: return '#d97706'\n"
    "    if 'P3' in p or 'MEDIUM' in p: return '#2563eb'\n"
    "    return '#16a34a'\n"
    "\n"
    "def traj_color(t):\n"
    "    t = t.upper()\n"
    "    if t == 'ESCALATING': return '#dc2626'\n"
    "    if t == 'STABLE': return '#d97706'\n"
    "    return '#16a34a'\n"
    "\n"
    "def sev_color(s):\n"
    "    s = s.lower()\n"
    "    if s == 'critical': return '#dc2626'\n"
    "    if s == 'high': return '#d97706'\n"
    "    if s == 'medium': return '#2563eb'\n"
    "    return '#6b7280'\n"
    "\n"
    "pc = priority_color(priority)\n"
    "\n"
    "# Risk score donut SVG\n"
    "pct = min(risk_score, 100)\n"
    "circ = 251.2\n"
    "off = circ - (circ * pct / 100)\n"
    "donut = (\n"
    "    f'<svg width=\"100\" height=\"100\" viewBox=\"0 0 100 100\">'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"#334155\" stroke-width=\"8\" opacity=\"0.3\"/>'\n"
    "    f'<circle cx=\"50\" cy=\"50\" r=\"40\" fill=\"none\" stroke=\"{pc}\" stroke-width=\"8\" '\n"
    "    f'stroke-dasharray=\"{circ}\" stroke-dashoffset=\"{off:.1f}\" stroke-linecap=\"round\" '\n"
    "    f'transform=\"rotate(-90 50 50)\"/>'\n"
    "    f'<text x=\"50\" y=\"46\" text-anchor=\"middle\" font-size=\"22\" font-weight=\"800\" fill=\"#fff\">{risk_score}</text>'\n"
    "    f'<text x=\"50\" y=\"62\" text-anchor=\"middle\" font-size=\"9\" font-weight=\"600\" fill=\"{pc}\">RISK</text>'\n"
    "    f'</svg>'\n"
    ")\n"
    "\n"
    "# Churn probability bar\n"
    "churn = sentiment.get('churn_probability', 0)\n"
    "churn_color = '#dc2626' if churn >= 70 else '#d97706' if churn >= 40 else '#16a34a'\n"
    "traj = sentiment.get('trajectory', 'STABLE')\n"
    "tc = traj_color(traj)\n"
    "traj_arrow = '&#x2191;' if traj == 'ESCALATING' else '&#x2193;' if traj == 'DE_ESCALATING' else '&#x2194;'\n"
    "\n"
    "sentiment_html = (\n"
    "    f'<div style=\"margin-bottom:12px\">'\n"
    "    f'<div style=\"font-size:13px;color:#64748b;margin-bottom:4px\">Current State</div>'\n"
    "    f'<div style=\"font-size:15px;font-weight:600;color:#1e293b\">{sentiment.get(\"current_state\", \"Unknown\")}</div></div>'\n"
    "    f'<div style=\"display:flex;gap:20px;margin-bottom:12px\">'\n"
    "    f'<div style=\"flex:1\"><div style=\"font-size:12px;color:#64748b;margin-bottom:4px\">Trajectory</div>'\n"
    "    f'<span style=\"background:{tc};color:#fff;padding:4px 12px;border-radius:6px;font-size:13px;font-weight:600\">{traj_arrow} {traj}</span></div>'\n"
    "    f'<div style=\"flex:1\"><div style=\"font-size:12px;color:#64748b;margin-bottom:4px\">Churn Probability</div>'\n"
    "    f'<div style=\"display:flex;align-items:center;gap:8px\">'\n"
    "    f'<div style=\"flex:1;background:#e2e8f0;border-radius:4px;height:8px\">'\n"
    "    f'<div style=\"background:{churn_color};border-radius:4px;height:8px;width:{churn}%\"></div></div>'\n"
    "    f'<span style=\"font-weight:700;font-size:14px;color:{churn_color}\">{churn}%</span></div></div></div>'\n"
    ")\n"
    "\n"
    "# Financial impact\n"
    "fin_fields = [\n"
    "    ('Lifetime Value', financial.get('lifetime_value', 'N/A'), '#1e293b'),\n"
    "    ('Revenue at Risk', financial.get('revenue_at_risk', 'N/A'), '#dc2626'),\n"
    "    ('Cost of Loss', financial.get('cost_of_loss', 'N/A'), '#d97706'),\n"
    "    ('Cost of Resolution', financial.get('cost_of_resolution', 'N/A'), '#2563eb'),\n"
    "    ('ROI of Retention', financial.get('roi_of_retention', 'N/A'), '#16a34a'),\n"
    "]\n"
    "fin_html = ''\n"
    "for label, val, color in fin_fields:\n"
    "    fin_html += (\n"
    "        f'<div style=\"display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "        f'<span style=\"font-size:13px;color:#64748b\">{label}</span>'\n"
    "        f'<span style=\"font-size:14px;font-weight:700;color:{color}\">{val}</span></div>'\n"
    "    )\n"
    "\n"
    "# SLA breaches table\n"
    "sla_html = ''\n"
    "for b in sla_breaches:\n"
    "    sc = sev_color(b.get('severity', 'medium'))\n"
    "    sla_html += (\n"
    "        f'<div style=\"display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start\">'\n"
    "        f'<span style=\"background:{sc};color:#fff;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;flex-shrink:0\">{b.get(\"severity\",\"medium\")}</span>'\n"
    "        f'<div style=\"flex:1\"><div style=\"font-size:13px;font-weight:600;color:#1e293b\">{b.get(\"type\",\"\")}</div>'\n"
    "        f'<div style=\"font-size:12px;color:#64748b;margin-top:2px\">{b.get(\"details\",\"\")}</div></div></div>'\n"
    "    )\n"
    "\n"
    "# Resolution plan — three columns\n"
    "def action_list(items, show_deadline=False):\n"
    "    h = ''\n"
    "    for a in items:\n"
    "        time_field = a.get('deadline', '') if show_deadline else a.get('timeline', '')\n"
    "        time_html = f'<div style=\"font-size:11px;color:#64748b;margin-top:2px\">{a.get(\"owner\",\"\")} &middot; {time_field}</div>' if time_field else ''\n"
    "        h += (\n"
    "            f'<div style=\"padding:8px 0;border-bottom:1px solid #f1f5f9\">'\n"
    "            f'<div style=\"font-size:13px;color:#1e293b\">{a.get(\"action\",\"\")}</div>'\n"
    "            f'{time_html}</div>'\n"
    "        )\n"
    "    return h\n"
    "\n"
    "imm = action_list(plan.get('immediate_actions', []), show_deadline=True)\n"
    "short = action_list(plan.get('short_term', []))\n"
    "long = action_list(plan.get('long_term', []))\n"
    "\n"
    "# Retention offer\n"
    "offer_components = offer.get('components', [])\n"
    "comp_html = ''.join(f'<div style=\"display:flex;gap:8px;padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"color:#16a34a;flex-shrink:0\">&#x2714;</span>{c}</div>' for c in offer_components)\n"
    "\n"
    "# Compliance flags\n"
    "flag_html = ''\n"
    "if compliance:\n"
    "    chips = ''.join(f'<span style=\"background:#fef2f2;color:#dc2626;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;border:1px solid #fecaca\">{f}</span>' for f in compliance)\n"
    "    flag_html = f'<div style=\"display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px\">{chips}</div>'\n"
    "\n"
    "# Systemic issues\n"
    "sys_html = ''.join(f'<div style=\"display:flex;gap:8px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155\"><span style=\"color:#d97706;flex-shrink:0\">&#x26A0;</span>{s}</div>' for s in systemic)\n"
    "\n"
    "card_style = 'background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:12px'\n"
    "card_hdr = 'padding:16px 20px;font-weight:700;font-size:15px;color:#1e293b;border-bottom:1px solid #f1f5f9'\n"
    "card_body = 'padding:16px 20px'\n"
    "\n"
    "html = (\n"
    "    '<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">'\n"
    "    '<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;background:#f8fafc;padding:32px}</style></head><body>'\n"
    "    '<div style=\"max-width:780px;margin:0 auto\">'\n"
    "    # Header — dark navy gradient with priority badge and risk donut\n"
    "    f'<div style=\"background:linear-gradient(135deg,#0c1222,#1a2744);border-radius:16px;padding:32px;margin-bottom:20px;color:#fff\">'\n"
    "    f'<div style=\"display:flex;justify-content:space-between;align-items:center\">'\n"
    "    f'<div style=\"flex:1\">'\n"
    "    f'<div style=\"display:flex;align-items:center;gap:12px;margin-bottom:8px\">'\n"
    "    f'<span style=\"font-size:22px;font-weight:700\">Executive Escalation Memo</span>'\n"
    "    f'<span style=\"background:{pc};color:#fff;padding:4px 14px;border-radius:6px;font-size:12px;font-weight:700\">{priority.replace(\"_\", \" \")}</span></div>'\n"
    "    f'<div style=\"font-size:15px;color:#cbd5e1;margin-bottom:4px\">Client: Margaret Chen-Whitfield &middot; PB-00048217</div>'\n"
    "    f'<div style=\"font-size:13px;color:#64748b\">Private Banking Platinum &middot; 7.9yr tenure &middot; $2.4M relationship</div></div>'\n"
    "    f'<div style=\"flex-shrink:0;margin-left:24px\">{donut}</div></div></div>'\n"
    "    # Executive Summary\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4CB; Executive Summary</div>'\n"
    "    f'<div style=\"{card_body};font-size:14px;color:#334155;line-height:1.7\">{exec_summary}</div></div>'\n"
    "    # Sentiment Analysis\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4CA; Sentiment Analysis</div>'\n"
    "    f'<div style=\"{card_body}\">{sentiment_html}</div></div>'\n"
    "    # Financial Impact\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F4B0; Financial Impact</div>'\n"
    "    f'<div style=\"{card_body}\">{fin_html}</div></div>'\n"
    "    # SLA Breaches\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr};color:#dc2626\">&#x23F0; SLA Breaches ({len(sla_breaches)})</div>'\n"
    "    f'<div style=\"{card_body}\">{sla_html}</div></div>'\n"
    "    # Resolution Plan — 3 columns\n"
    "    f'<div style=\"display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px\">'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#dc2626;font-size:13px\">&#x26A1; Immediate (24h)</div><div style=\"{card_body}\">{imm}</div></div>'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#d97706;font-size:13px\">&#x1F4C5; Short-term (1-2wk)</div><div style=\"{card_body}\">{short}</div></div>'\n"
    "    f'<div style=\"{card_style};margin-bottom:0\"><div style=\"{card_hdr};color:#2563eb;font-size:13px\">&#x1F3AF; Long-term (30-90d)</div><div style=\"{card_body}\">{long}</div></div></div>'\n"
    "    # Retention Offer\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F381; Retention Offer — {offer.get(\"tier\", \"\")}'\n"
    "    f'<span style=\"float:right;color:#16a34a\">{offer.get(\"total_value\", \"\")}</span></div>'\n"
    "    f'<div style=\"{card_body}\">{comp_html}'\n"
    "    f'<div style=\"margin-top:10px;padding:10px;background:#f0fdf4;border-radius:8px;font-size:12px;color:#166534\">{offer.get(\"justification\", \"\")}</div></div></div>'\n"
    "    # Compliance & Systemic Issues\n"
    "    f'<div style=\"{card_style}\"><div style=\"{card_hdr}\">&#x1F6A8; Compliance &amp; Systemic Issues</div>'\n"
    "    f'<div style=\"{card_body}\">{flag_html}{sys_html}</div></div>'\n"
    "    '</div></body></html>'\n"
    ")\n"
    "\n"
    "return [{'json': {'html': html}}]\n"
)


EXAMPLE_WORKFLOWS = [
    # ========================================
    # FRAUD INVESTIGATION AGENT (UI) — HTML report
    # ========================================
    {
        "name": "Fraud Investigation Agent",
        "description": "AI Lead Investigator that spawns 3 parallel sub-agents (transaction, customer, network) to investigate a suspicious transaction alert. Produces a styled risk assessment report with evidence chain, regulatory flags, and recommended action.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_FRAUD_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Lead Investigator",
                    "type": "AIAgent",
                    "parameters": {
                        **_FRAUD_AGENT_BASE,
                        "task": (
                            "Investigate the following transaction alert:\n\n"
                            "{{ $json }}\n\n"
                            "Run your full investigation pipeline — dispatch all three specialist "
                            "analysts in parallel, gather evidence, cross-reference findings, "
                            "and produce your risk assessment."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _FRAUD_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
                # Subnodes for Lead Investigator
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.2, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Lead Investigator"},
                {"source_node": "Lead Investigator", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Lead Investigator", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # FRAUD INVESTIGATION API — Webhook → Switch → 4 branches
    # ========================================
    {
        "name": "Fraud Investigation API",
        "description": "Webhook API for fraud triage. POST a transaction alert and receive a risk assessment with automatic routing: LOW→auto-clear, MEDIUM→queue review, HIGH→block+alert, CRITICAL→block+escalate.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Webhook", "type": "Webhook", "parameters": {"method": "POST", "responseMode": "lastNode"}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Lead Investigator",
                    "type": "AIAgent",
                    "parameters": {
                        **_FRAUD_AGENT_BASE,
                        "task": (
                            "Investigate the following transaction alert:\n\n"
                            "{{ $json.body }}\n\n"
                            "Run your full investigation pipeline — dispatch all three specialist "
                            "analysts in parallel, gather evidence, cross-reference findings, "
                            "and produce your risk assessment."
                        ),
                    },
                    "position": {"x": 450, "y": 300},
                },
                {
                    "name": "Route by Risk",
                    "type": "Switch",
                    "parameters": {
                        "numberOfOutputs": 4,
                        "mode": "rules",
                        "rules": [
                            {"output": 0, "field": "structured.risk_level", "operation": "equals", "value": "LOW"},
                            {"output": 1, "field": "structured.risk_level", "operation": "equals", "value": "MEDIUM"},
                            {"output": 2, "field": "structured.risk_level", "operation": "equals", "value": "HIGH"},
                            {"output": 3, "field": "structured.risk_level", "operation": "equals", "value": "CRITICAL"},
                        ],
                    },
                    "position": {"x": 800, "y": 300},
                },
                {
                    "name": "Auto Clear",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "responseMode": "lastNode",
                        "responseField": "structured",
                        "contentType": "application/json",
                        "wrapResponse": True,
                        "additionalFields": json.dumps({"disposition": "AUTO_CLEARED", "action_taken": "Transaction approved — no risk indicators met threshold."}),
                    },
                    "position": {"x": 1150, "y": 100},
                },
                {
                    "name": "Queue Review",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "responseMode": "lastNode",
                        "responseField": "structured",
                        "contentType": "application/json",
                        "wrapResponse": True,
                        "additionalFields": json.dumps({"disposition": "QUEUED_FOR_REVIEW", "action_taken": "Transaction held — queued for analyst manual review within 24h."}),
                    },
                    "position": {"x": 1150, "y": 300},
                },
                {
                    "name": "Block Alert",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "responseMode": "lastNode",
                        "responseField": "structured",
                        "contentType": "application/json",
                        "wrapResponse": True,
                        "additionalFields": json.dumps({"disposition": "BLOCKED_ALERT_SENT", "action_taken": "Transaction blocked — compliance team alerted for investigation."}),
                    },
                    "position": {"x": 1150, "y": 500},
                },
                {
                    "name": "Block Escalate",
                    "type": "RespondToWebhook",
                    "parameters": {
                        "statusCode": "200",
                        "responseMode": "lastNode",
                        "responseField": "structured",
                        "contentType": "application/json",
                        "wrapResponse": True,
                        "additionalFields": json.dumps({"disposition": "BLOCKED_ESCALATED", "action_taken": "Transaction blocked — escalated to BSA officer. SAR filing initiated. Account frozen pending review."}),
                    },
                    "position": {"x": 1150, "y": 700},
                },
                # Subnodes for Lead Investigator
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.2, "maxTokens": 8192}, "position": {"x": 450, "y": 500}},
            ],
            "connections": [
                {"source_node": "Webhook", "target_node": "Lead Investigator"},
                {"source_node": "Lead Investigator", "target_node": "Route by Risk"},
                {"source_node": "Route by Risk", "target_node": "Auto Clear", "source_output": "output0"},
                {"source_node": "Route by Risk", "target_node": "Queue Review", "source_output": "output1"},
                {"source_node": "Route by Risk", "target_node": "Block Alert", "source_output": "output2"},
                {"source_node": "Route by Risk", "target_node": "Block Escalate", "source_output": "output3"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Lead Investigator", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # DEEP RESEARCH AGENT — HTML report
    # ========================================
    {
        "name": "Deep Research Agent",
        "description": "Research Director that spawns 3 parallel sub-agents (Technology, Market, Academic) to produce a comprehensive research brief. Generates a styled maturity report with domain scores, opportunities vs risks, and forward outlook.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_RESEARCH_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Research Director",
                    "type": "AIAgent",
                    "parameters": {
                        **_RESEARCH_AGENT_BASE,
                        "task": (
                            "Conduct a deep research analysis on the following brief:\n\n"
                            "{{ $json }}\n\n"
                            "Run your full research pipeline — dispatch all three specialist "
                            "researchers in parallel, gather findings from each domain, "
                            "cross-reference insights, and produce your comprehensive research report."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _RESEARCH_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
                # Subnodes for Research Director
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.3, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Research Director"},
                {"source_node": "Research Director", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Research Director", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # STARTUP DUE DILIGENCE AGENT — HTML report
    # ========================================
    {
        "name": "Startup Due Diligence Agent",
        "description": "Investment Analyst that spawns 3 parallel sub-agents (Market Opportunity, Team & Execution, Financial & Unit Economics) to evaluate a startup pitch. Produces an investment memo with dimension scores, analyst findings, risks, and next steps.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_DILIGENCE_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Investment Analyst",
                    "type": "AIAgent",
                    "parameters": {
                        **_DILIGENCE_AGENT_BASE,
                        "task": (
                            "Conduct due diligence on the following startup:\n\n"
                            "{{ $json }}\n\n"
                            "Run your full due diligence pipeline — dispatch all three specialist "
                            "analysts in parallel, gather assessments from each dimension, "
                            "cross-reference findings, and produce your investment recommendation."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _DILIGENCE_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
                # Subnodes for Investment Analyst
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.2, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Investment Analyst"},
                {"source_node": "Investment Analyst", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Investment Analyst", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # CONTENT QUALITY PIPELINE — HTML report
    # ========================================
    {
        "name": "Content Quality Pipeline",
        "description": "Content Director that spawns 3 parallel sub-agents with fundamentally different roles (Writer, Fact-Checker, Editor) to produce and evaluate a blog post. Generates a content quality dashboard with draft preview, fact-check verdicts, editorial ratings, and priority revisions.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_CONTENT_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Content Director",
                    "type": "AIAgent",
                    "parameters": {
                        **_CONTENT_AGENT_BASE,
                        "task": (
                            "Run the content quality pipeline for the following brief:\n\n"
                            "{{ $json }}\n\n"
                            "Dispatch all three specialists in parallel — Writer, Fact-Checker, "
                            "and Editor. Synthesize their outputs into a comprehensive quality "
                            "assessment with the final draft, fact-check results, editorial "
                            "feedback, and prioritized revisions."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _CONTENT_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
                # Subnodes for Content Director
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.4, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Content Director"},
                {"source_node": "Content Director", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Content Director", "connection_type": "subnode", "slot_name": "chatModel"},
            ],
            "settings": {},
        },
    },
    # ========================================
    # CUSTOMER SERVICE ESCALATION TRIAGE — HTML report
    # ========================================
    {
        "name": "Customer Escalation Triage",
        "description": "Escalation Director that spawns 3 parallel sub-agents (Sentiment & Intent, Customer Value & Retention, Resolution Strategist) to triage a high-stakes private banking escalation. Produces an executive escalation memo with sentiment trajectory, financial impact, SLA breaches, resolution plan, and calibrated retention offer.",
        "active": True,
        "definition": {
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 100, "y": 300}},
                {
                    "name": "Input",
                    "type": "Set",
                    "parameters": {
                        "mode": "json",
                        "jsonData": json.dumps(_ESCALATION_SAMPLE),
                        "keepOnlySet": True,
                    },
                    "position": {"x": 350, "y": 300},
                },
                {
                    "name": "Escalation Director",
                    "type": "AIAgent",
                    "parameters": {
                        **_ESCALATION_AGENT_BASE,
                        "task": (
                            "Triage the following customer escalation case:\n\n"
                            "{{ $json }}\n\n"
                            "Run your full escalation protocol — dispatch all three specialist "
                            "analysts in parallel (Sentiment & Intent, Customer Value & Retention, "
                            "Resolution Strategist), cross-reference their findings, and produce "
                            "your executive escalation memo."
                        ),
                    },
                    "position": {"x": 650, "y": 300},
                },
                {"name": "Build Report", "type": "Code", "parameters": {"code": _ESCALATION_HTML_CODE}, "position": {"x": 950, "y": 300}},
                {"name": "Show Report", "type": "Output", "parameters": {"source": "input", "format": "html", "contentField": "html"}, "position": {"x": 1250, "y": 300}},
                # Subnodes for Escalation Director
                {"name": "Gemini 2.5 Flash", "type": "LLMModel", "parameters": {"model": "gemini-2.5-flash", "temperature": 0.2, "maxTokens": 8192}, "position": {"x": 650, "y": 500}},
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Input"},
                {"source_node": "Input", "target_node": "Escalation Director"},
                {"source_node": "Escalation Director", "target_node": "Build Report"},
                {"source_node": "Build Report", "target_node": "Show Report"},
                # Subnode connections
                {"source_node": "Gemini 2.5 Flash", "target_node": "Escalation Director", "connection_type": "subnode", "slot_name": "chatModel"},
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
