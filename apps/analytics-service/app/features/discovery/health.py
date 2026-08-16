"""Dataset health (§17) — a multi-dimension read-model over existing signals.

Deliberately NO single opaque score (ROADMAP is explicit): each dimension
carries a status, a one-line summary, and evidence pointers (version numbers,
run ids) so the caller can inspect the underlying facts.

Evaluators are pure functions over plain data so the unit layer can pin the
thresholds without Postgres; ``dataset_health`` does the signal fetching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.features.data_accelerator.services.diffs import compute_profile_drift

from . import repo

OK, WARNING, ATTENTION, UNKNOWN = "ok", "warning", "attention", "unknown"

# Worst per-column null rate (latest profiled version).
MISSING_WARNING_PCT = 5.0
MISSING_ATTENTION_PCT = 25.0
# Duplicate rows / total rows (latest profiled version).
DUPLICATE_ATTENTION_RATIO = 0.05
# A null-rate move this large between profiled versions is notable drift.
DRIFT_NULL_DELTA_PCT = 5.0
# Column-dictionary coverage counted as "fully documented".
DOC_COLUMN_COVERAGE = 0.5

_FREQ_SECONDS = {
    "hourly": 3600, "daily": 86400, "weekly": 7 * 86400,
    "monthly": 31 * 86400, "quarterly": 92 * 86400,
    "yearly": 366 * 86400, "annual": 366 * 86400,
}


def iso_or_none(value: Any) -> str | None:
    """One timestamp encoding for every evidence field: ISO-8601, or null.

    The signals this read-model merges arrive in two shapes — ``datetime``
    objects from ``dataset_versions`` and Postgres ``::text`` strings ("2026-08-
    07 12:00:00+00", a space where ISO-8601 wants a ``T``) from the run repos.
    Emitting both meant one response carried two encodings of the same concept,
    and a client that parsed one dimension's timestamp choked on another's.

    ``str()`` was worse than inconsistent: a null ``completed_at`` came out as
    the four-character string ``"None"``, which is not a timestamp, is not
    null, and parses as neither. Anything unparseable is passed through
    verbatim rather than raised — evidence is diagnostic, and losing the whole
    health response over one odd timestamp helps nobody.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class HealthDimension(BaseModel):
    status: str = Field(description="ok | warning | attention | unknown")
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class DatasetHealthResponse(BaseModel):
    """Per-dimension health with evidence pointers; no aggregate score."""

    dataset_id: str
    current_version_number: int | None = None
    dimensions: dict[str, HealthDimension]


# ---------------------------------------------------------------------------
# Pure evaluators (unit-tested)
# ---------------------------------------------------------------------------

def evaluate_schema_stability(rows: list[dict]) -> HealthDimension:
    """Fingerprint churn per logical sheet across recent ready versions."""
    if not rows:
        return HealthDimension(status=UNKNOWN, summary="No ready versions")
    per_sheet: dict[str, list[dict]] = {}
    for r in rows:  # rows arrive version-ascending
        key = r.get("logical_sheet_id") or f"key:{r['sheet_name']}"
        per_sheet.setdefault(key, []).append(r)
    changes = []
    for seq in per_sheet.values():
        for a, b in zip(seq, seq[1:]):
            if (a.get("schema_fingerprint") and b.get("schema_fingerprint")
                    and a["schema_fingerprint"] != b["schema_fingerprint"]):
                changes.append({"sheet": b["sheet_name"],
                                "from_version": a["version_number"],
                                "to_version": b["version_number"]})
    versions = sorted({r["version_number"] for r in rows})
    evidence = {"versions_considered": versions, "changes": changes}
    if changes:
        return HealthDimension(
            status=WARNING, evidence=evidence,
            summary=f"{len(changes)} schema change(s) across the last "
                    f"{len(versions)} ready versions")
    return HealthDimension(
        status=OK, evidence=evidence,
        summary=f"Schema stable across the last {len(versions)} ready versions")


def evaluate_validation(run: dict | None,
                        current_version_number: int | None) -> HealthDimension:
    if run is None:
        return HealthDimension(
            status=UNKNOWN,
            summary="No completed validation run for the current version",
            evidence={"version_number": current_version_number})
    errors = run.get("error_failures") or 0
    warnings = run.get("warning_failures") or 0
    status = ATTENTION if errors else (WARNING if warnings else OK)
    return HealthDimension(
        status=status,
        summary=f"Latest run: {run.get('rules_failed', 0)}/{run.get('rules_total', 0)} "
                f"rules failed ({errors} error-level)",
        evidence={"run_id": str(run["id"]),
                  "version_number": current_version_number,
                  "rules_total": run.get("rules_total"),
                  "rules_passed": run.get("rules_passed"),
                  "rules_failed": run.get("rules_failed"),
                  "error_failures": errors, "warning_failures": warnings,
                  "completed_at": iso_or_none(run.get("completed_at"))})


def _latest_profiles_or_unknown(latest: list[dict]) -> HealthDimension | None:
    if not latest:
        return HealthDimension(
            status=UNKNOWN, summary="No completed profile runs")
    return None


def evaluate_missing(latest: list[dict]) -> HealthDimension:
    """Worst per-column null rate over the latest profiled version per sheet."""
    if (unknown := _latest_profiles_or_unknown(latest)) is not None:
        return unknown
    worst: dict | None = None
    for entry in latest:
        for col in (entry["profile"].get("columns") or []):
            pct = col.get("null_percent") or 0.0
            if worst is None or pct > worst["null_percent"]:
                worst = {"column": col["name"], "null_percent": round(pct, 2),
                         "sheet": entry["sheet_name"],
                         "version_number": entry["version_number"],
                         "profile_run_id": entry["run_id"]}
    evidence = {"profiled_versions": sorted({e["version_number"] for e in latest}),
                "worst": worst}
    if worst is None or worst["null_percent"] == 0:
        return HealthDimension(status=OK, summary="No missing values",
                               evidence=evidence)
    pct = worst["null_percent"]
    status = (ATTENTION if pct >= MISSING_ATTENTION_PCT
              else WARNING if pct >= MISSING_WARNING_PCT else OK)
    return HealthDimension(
        status=status, evidence=evidence,
        summary=f"Worst column null rate {pct}% ({worst['column']} in "
                f"{worst['sheet']}, v{worst['version_number']})")


def evaluate_duplicates(latest: list[dict]) -> HealthDimension:
    if (unknown := _latest_profiles_or_unknown(latest)) is not None:
        return unknown
    total = sum(e["profile"].get("row_count") or 0 for e in latest)
    dup = sum(e["profile"].get("duplicate_row_count") or 0 for e in latest)
    evidence = {
        "profiled_versions": sorted({e["version_number"] for e in latest}),
        "sheets": [{"sheet": e["sheet_name"],
                    "duplicate_rows": e["profile"].get("duplicate_row_count") or 0,
                    "row_count": e["profile"].get("row_count") or 0}
                   for e in latest]}
    if not dup:
        return HealthDimension(status=OK, summary="No duplicate rows",
                               evidence=evidence)
    ratio = dup / total if total else 0.0
    status = ATTENTION if ratio >= DUPLICATE_ATTENTION_RATIO else WARNING
    return HealthDimension(
        status=status, evidence=evidence,
        summary=f"{dup} duplicate rows across {total} profiled rows "
                f"({round(ratio * 100, 2)}%)")


def evaluate_drift(drifts: list[dict]) -> HealthDimension:
    """Notable deltas between each sheet's last two profiled versions."""
    if not drifts:
        return HealthDimension(
            status=UNKNOWN,
            summary="Fewer than two profiled versions — no drift baseline")
    notable = []
    for d in drifts:
        drift = d["drift"]
        cols = [c.column for c in drift.columns
                if (c.null_percent_delta is not None
                    and abs(c.null_percent_delta) >= DRIFT_NULL_DELTA_PCT)
                or c.added_categories or c.removed_categories]
        if cols or (drift.duplicate_rows_delta or 0) > 0:
            notable.append({"sheet": d["sheet_name"],
                            "from_version": d["from_version"],
                            "to_version": d["to_version"],
                            "columns": cols,
                            "duplicate_rows_delta": drift.duplicate_rows_delta})
    evidence = {"compared": [{"sheet": d["sheet_name"],
                              "from_version": d["from_version"],
                              "to_version": d["to_version"]} for d in drifts],
                "notable": notable}
    if notable:
        return HealthDimension(
            status=WARNING, evidence=evidence,
            summary=f"Notable drift in {len(notable)} sheet(s): "
                    + ", ".join(n["sheet"] or "?" for n in notable))
    return HealthDimension(status=OK, evidence=evidence,
                           summary="No notable drift between profiled versions")


def evaluate_freshness(created_at: datetime | str | None,
                       refresh_frequency: str | None,
                       *, version_number: int | None,
                       now: datetime | None = None) -> HealthDimension:
    if created_at is None:
        return HealthDimension(status=ATTENTION, summary="No ready version")
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age = (now - created_at).total_seconds()
    freq = (refresh_frequency or "").strip().lower()
    evidence = {"version_number": version_number,
                "created_at": iso_or_none(created_at),
                "age_seconds": int(age),
                "refresh_frequency": refresh_frequency}
    threshold = _FREQ_SECONDS.get(freq)
    if threshold is None:
        return HealthDimension(
            status=UNKNOWN, evidence=evidence,
            summary="No recognized refresh frequency — freshness not assessable"
                    if freq else "No refresh frequency declared")
    days = round(age / 86400, 1)
    if age <= threshold:
        return HealthDimension(status=OK, evidence=evidence,
                               summary=f"Fresh: latest version is {days}d old "
                                       f"({freq} cadence)")
    status = ATTENTION if age > 2 * threshold else WARNING
    return HealthDimension(
        status=status, evidence=evidence,
        summary=f"Stale: latest version is {days}d old ({freq} cadence)")


def evaluate_documentation(has_description: bool, has_domain: bool, *,
                           total_sheets: int, documented_sheets: int,
                           total_columns: int, documented_columns: int) -> HealthDimension:
    sheets_full = total_sheets > 0 and documented_sheets >= total_sheets
    column_coverage = documented_columns / total_columns if total_columns else None
    columns_full = column_coverage is None or column_coverage >= DOC_COLUMN_COVERAGE
    if has_description and has_domain and sheets_full and columns_full:
        bucket = "full"
    elif (not has_description and not has_domain
          and documented_sheets == 0 and documented_columns == 0):
        bucket = "none"
    else:
        bucket = "partial"
    missing = [label for present, label in (
        (has_description, "description"), (has_domain, "domain"),
        (sheets_full, "sheet grain/PK"), (columns_full, "column dictionary"),
    ) if not present]
    status = {"full": OK, "partial": WARNING, "none": ATTENTION}[bucket]
    return HealthDimension(
        status=status,
        summary="Fully documented" if bucket == "full"
                else f"Documentation {bucket}; missing: {', '.join(missing)}",
        evidence={"bucket": bucket,
                  "has_description": has_description, "has_domain": has_domain,
                  "documented_sheets": documented_sheets,
                  "total_sheets": total_sheets,
                  "documented_columns": documented_columns,
                  "total_columns": total_columns})


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def split_profiled_pairs(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rows from ``latest_profiled_pairs`` -> (latest per sheet, drift entries)."""
    by_sheet: dict[str, list[dict]] = {}
    for r in pairs:
        by_sheet.setdefault(r["logical_sheet_id"], []).append(r)
    latest, drifts = [], []
    for seq in by_sheet.values():
        seq = sorted(seq, key=lambda r: r["version_number"], reverse=True)
        latest.append(seq[0])
        if len(seq) > 1 and seq[0]["profile"] and seq[1]["profile"]:
            drifts.append({
                "sheet_name": seq[0]["sheet_name"],
                "from_version": seq[1]["version_number"],
                "to_version": seq[0]["version_number"],
                "drift": compute_profile_drift(seq[1]["profile"], seq[0]["profile"],
                                               sheet_key=seq[0]["sheet_name"]),
            })
    latest.sort(key=lambda r: r["sheet_name"] or "")
    drifts.sort(key=lambda d: d["sheet_name"] or "")
    return latest, drifts


async def dataset_health(ds: dict) -> DatasetHealthResponse:
    """Assemble every dimension from persisted signals — no file I/O."""
    from app.features.explorer.service import PROFILE_ALGORITHM_VERSION
    from app.features.quality.repo import latest_completed_run
    from app.shared.repo import get_current_version

    dataset_id = str(ds["id"])
    current = await get_current_version(dataset_id)
    cur_vn = current["version_number"] if current else None

    recent = await repo.recent_version_sheets(dataset_id)
    val_run = (await latest_completed_run(dataset_id, str(current["id"]))
               if current else None)
    pairs = await repo.latest_profiled_pairs(dataset_id, PROFILE_ALGORITHM_VERSION)
    doc = await repo.documentation_stats(
        dataset_id, str(current["id"]) if current else None)

    latest, drifts = split_profiled_pairs(pairs)
    return DatasetHealthResponse(
        dataset_id=dataset_id,
        current_version_number=cur_vn,
        dimensions={
            "schema_stability": evaluate_schema_stability(recent),
            "validation": evaluate_validation(val_run, cur_vn),
            "missing_data": evaluate_missing(latest),
            "duplicates": evaluate_duplicates(latest),
            "drift": evaluate_drift(drifts),
            "freshness": evaluate_freshness(
                current.get("created_at") if current else None,
                ds.get("refresh_frequency"), version_number=cur_vn),
            "documentation": evaluate_documentation(
                bool(ds.get("description")), bool(ds.get("domain")), **doc),
        })
