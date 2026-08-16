#!/usr/bin/env python3
"""Flatten a coverage-mapping workflow journal into one reviewable JSON file.

The mapping workflow spawns one agent per API domain; each returns a structured
report (endpoints + coverage verdict, unit gaps, journey gaps, risks). The
journal records those return values, but one JSON object per line with the
payload nested inside a envelope is not something you can read or diff.

Usage:
    scripts/extract_coverage_map.py <journal.jsonl> [-o coverage-map.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys


def load_reports(journal_path: str) -> list[dict]:
    """Pull every agent return value out of the journal, in completion order."""
    reports = []
    with open(journal_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") != "result":
                continue
            # The payload has lived under both keys across runs; accept either,
            # and accept it pre-parsed or as a JSON string.
            payload = record.get("result", record.get("value"))
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if isinstance(payload, dict) and payload.get("endpoints") is not None:
                reports.append(payload)
    return reports


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("journal")
    ap.add_argument("-o", "--out", default="coverage-map.json")
    args = ap.parse_args()

    reports = load_reports(args.journal)
    if not reports:
        print(f"no agent reports found in {args.journal}", file=sys.stderr)
        return 1

    endpoints, unit_gaps, journeys, risks = [], [], [], []
    for report in reports:
        domain = report.get("domain", "?")
        # Stamp the domain onto every child record so a flattened list stays
        # traceable back to the agent that produced it.
        for endpoint in report.get("endpoints", []):
            endpoints.append({"domain": domain, **endpoint})
        for gap in report.get("unit_gaps", []):
            unit_gaps.append({"domain": domain, **gap})
        for journey in report.get("journey_gaps", []):
            journeys.append({"domain": domain, **journey})
        for risk in report.get("risks", []):
            risks.append({"domain": domain, **risk})

    coverage = collections.Counter(e.get("coverage") for e in endpoints)
    out = {
        "summary": {
            "domains": len(reports),
            "endpoints": len(endpoints),
            "coverage_verdicts": dict(coverage),
            "unit_gaps": len(unit_gaps),
            "journey_gaps": len(journeys),
            "risks": len(risks),
        },
        "endpoints": endpoints,
        "unit_gaps": unit_gaps,
        "journey_gaps": journeys,
        "risks": risks,
    }

    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)

    print(json.dumps(out["summary"], indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
