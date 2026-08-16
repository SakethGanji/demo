#!/usr/bin/env python3
"""Turn the risk files + their adversarial verdicts into one ranked DEFECTS.md.

Each suspected defect was checked by an independent agent whose instruction was
to REFUTE it. Only what survived is worth acting on, so this report leads with
the confirmed bugs, ranked by severity, and keeps the refuted ones at the bottom
as a record of what was checked and dismissed — a claim that was investigated
and killed is worth writing down, or someone re-raises it next quarter.

Usage:
    scripts/build_defect_report.py <verdict-journal.jsonl> <risk-dir> -o DEFECTS.md
"""

from __future__ import annotations

import argparse
import collections
import json
import os

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
VERDICT_ORDER = {"CONFIRMED_BUG": 0, "UNCERTAIN": 1, "BY_DESIGN": 2, "REFUTED": 3}


def load_verdicts(journal_path: str) -> list[dict]:
    out = []
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
            payload = record.get("result", record.get("value"))
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if isinstance(payload, dict) and payload.get("verdict"):
                out.append(payload)
    return out


def attach_claims(verdicts: list[dict], risk_dir: str) -> list[dict]:
    """Re-join each verdict with the original claim it was judging."""
    for verdict in verdicts:
        path = verdict.get("risk_file")
        if not path:
            index = verdict.get("risk_index")
            if index is None:
                continue
            path = os.path.join(risk_dir, f"risk-{index:03d}.json")
        try:
            with open(path) as fh:
                claim = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        verdict["_domain"] = claim.get("domain", "?")
        verdict["_where"] = claim.get("where", "")
        verdict["_impact"] = claim.get("impact_on_ui", "")
    return verdicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("journal")
    ap.add_argument("risk_dir")
    ap.add_argument("-o", "--out", default="DEFECTS.md")
    args = ap.parse_args()

    verdicts = attach_claims(load_verdicts(args.journal), args.risk_dir)
    verdicts.sort(key=lambda v: (
        VERDICT_ORDER.get(v["verdict"], 9),
        SEVERITY_ORDER.get(v.get("severity"), 9),
        v.get("_domain", ""),
    ))

    tally = collections.Counter(v["verdict"] for v in verdicts)
    sev = collections.Counter(
        v.get("severity") for v in verdicts if v["verdict"] == "CONFIRMED_BUG")
    silent = sum(1 for v in verdicts
                 if v["verdict"] == "CONFIRMED_BUG" and v.get("silent_wrong_answer"))

    lines: list[str] = []
    w = lines.append

    w("# analytics-service — verified defect report")
    w("")
    w("Every claim below was produced by a domain agent reading the source, then handed to a")
    w("SECOND, independent agent whose instruction was to **refute** it — default to REFUTED")
    w("unless the code clearly supports the claim. Only `CONFIRMED_BUG` survived that.")
    w("")
    w("`BY_DESIGN` means the behaviour is real but deliberate and defensible (this repo")
    w("documents its choices heavily — e.g. 404-not-403 for cross-tenant reads is a stated")
    w("security property, not a bug). `REFUTED` means the claim was factually wrong about the")
    w("code; they are kept at the bottom so nobody re-raises them.")
    w("")
    w("## Tally")
    w("")
    w(f"- **{tally.get('CONFIRMED_BUG', 0)} confirmed bugs** — "
      f"{sev.get('critical', 0)} critical, {sev.get('high', 0)} high, "
      f"{sev.get('medium', 0)} medium, {sev.get('low', 0)} low")
    w(f"- **{silent} of them return a confident WRONG answer** rather than an error. "
      "That is the worst class here: a UI cannot detect it, and neither can the user.")
    w(f"- {tally.get('BY_DESIGN', 0)} by design · {tally.get('REFUTED', 0)} refuted · "
      f"{tally.get('UNCERTAIN', 0)} uncertain")
    w(f"- {len(verdicts)} claims adjudicated in total")
    w("")

    current = None
    for verdict in verdicts:
        head = verdict["verdict"]
        if head != current:
            current = head
            w("")
            w(f"# {head}")
            w("")
        sev_label = verdict.get("severity", "?")
        flag = " · **SILENT WRONG ANSWER**" if verdict.get("silent_wrong_answer") else ""
        w(f"## [{sev_label}] {verdict.get('one_line', '(no summary)')}{flag}")
        w("")
        w(f"- **domain:** {verdict.get('_domain', '?')}")
        w(f"- **where:** {verdict.get('_where', '')}")
        if verdict.get("_impact"):
            w(f"- **impact on the UI:** {verdict['_impact']}")
        w("")
        w("**Evidence**")
        w("")
        w(verdict.get("evidence", "").strip())
        w("")
        if verdict.get("fix_sketch"):
            w("**Fix**")
            w("")
            w(verdict["fix_sketch"].strip())
            w("")
        if verdict.get("test_to_pin_it"):
            w("**Test to pin it**")
            w("")
            w(verdict["test_to_pin_it"].strip())
            w("")

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"{len(verdicts)} verdicts -> {args.out}")
    print(f"  confirmed={tally.get('CONFIRMED_BUG', 0)} "
          f"(critical={sev.get('critical', 0)} high={sev.get('high', 0)} "
          f"medium={sev.get('medium', 0)} low={sev.get('low', 0)}) "
          f"silent_wrong={silent}")
    print(f"  by_design={tally.get('BY_DESIGN', 0)} refuted={tally.get('REFUTED', 0)} "
          f"uncertain={tally.get('UNCERTAIN', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
