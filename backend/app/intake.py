"""G0 Smart Intake: what each column is for, and what can be built from the table.

1. Column roles. Simple, explainable rules on top of the profiler (`profiling.detect_type`) and F6's date/entity
   finder (`splits.date_and_group_columns`): name patterns, value patterns, ranges and cardinality. Every role carries
   a confidence in [0, 1] and the evidence for it, so the user can see why and override it. No LLM.
2. Use-case suggestions. `SUGGESTIONS` is a plain list of `Suggestion`s; each looks at the roles and returns a score,
   a reason and the pre-fill for its flow, or None. Later G-sessions append their own entry to the list (survival,
   expected loss, fair lending, multi-label, segmented models, ...); nothing else changes.

    intake(df)                   -> {"columns": [...], "roles": {...}, "suggestions": [...]}
    intake(df, {"col": "role"})  -> the same with the user's overrides applied (confidence 1, evidence "set by you")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .profiling import MAX_CLASSES, detect_type
from .splits import date_and_group_columns

# --------------------------------------------------------------------------------------------------------------- roles

# Every role a column can have, with the words the UI shows. G-sessions add theirs here and in the detector.
ROLES: dict[str, str] = {
    "target": "target (what to predict)",
    "date": "date / time",
    "entity": "entity / customer ID (group)",
    "id": "row ID (excluded)",
    "money": "money amount",
    "duration": "duration (time to event)",
    "event": "event flag (survival)",
    "lgd": "loss given default (0–1)",
    "ead": "exposure at default",
    "surname": "surname",
    "zip": "zip / postal code",
    "geography": "geography",
    "protected": "protected attribute (fairness only, excluded)",
    "decision": "approve/decline decision (reject inference, excluded)",
    "multilabel": "multi-label list",
    "segment": "segment",
    "category": "category",
    "text": "free text",
    "leak": "leaky: known after the outcome (excluded)",
    "constant": "constant (excluded)",
    "feature": "feature",
}
EXCLUDED = ("id", "leak", "constant", "protected", "decision")
# Never model inputs on a credit decision, but kept for the fair-lending check (G4: BISG reads surname × zip).
NOT_INPUTS = ("surname", "zip")
# Roles a column can carry beside its primary one (churned: the target and the survival event; ead_usd: EAD and money).
# An explicit time to an event (the survival module's EXPLICIT): post-outcome for a yes/no model of that event.
TIME_TO_EVENT = re.compile(r"(duration|time_to|survival|days_to|weeks_to|months_to|years_to|follow_?up|_time$|^time$|time_on_study)", re.I)
SECONDARY = ("event", "money", "duration", "geography", "segment", "lgd", "ead")

MONEY = re.compile(r"(usd|eur|gbp|amount|amt|income|salary|balance|price|revenue|mrr|arr|spend|payment|limit|fee|"
                   r"principal|exposure|loss|value|cost|(^|_)loan(_|$))", re.I)
DURATION = re.compile(r"(tenure|duration|time_to|months_on_book|survival|days_to|weeks_to|months_to|_time$|^time$|"
                      r"days_active|lifetime)", re.I)
EXPLICIT_TTE = re.compile(r"(time_to|days_to|weeks_to|months_to|years_to|follow_?up|time_on_study|survival)", re.I)
EVENT = re.compile(r"(event|churn\w*|default\w*|died|death|censor\w*|observed|prepaid|prepay\w*|closed|attrit\w*|lapsed)", re.I)
LGD = re.compile(r"(^lgd|_lgd|loss_given|recovery_rate|loss_rate|loss_frac|loss_share|loss_pct)", re.I)
EAD = re.compile(r"(^ead|_ead|exposure|balance_at_default|outstanding)", re.I)
SURNAME = re.compile(r"(surname|last_name|lastname|family_name)", re.I)
ZIP = re.compile(r"(zip|postal|postcode|post_code)", re.I)
PROTECTED = re.compile(r"(^|_)(race|ethnic\w*|gender|sex|religion|national_origin|nationality|marital_status)($|_)", re.I)
GEO = re.compile(r"(^|_)(region|state|county|country|city|province|district|branch_region|geo\w*|msa|territory)($|_)", re.I)
SEGMENT = re.compile(r"(segment|product|customer_type|client_type|tier|plan|portfolio|line_of_business|business_line|"
                     r"book|programme|program)", re.I)
LEAK = re.compile(r"(recover\w*|collection\w*|charge_?off|write_?off|days_past_due|(^|_)dpd|default_date|closed_date|"
                  r"outcome_date|paid_off|settle\w*|resolution\w*|resolved|refund\w*|post_)", re.I)
REG_TARGET = re.compile(r"(amount|value|price|volume|sales|revenue|balance|spend|loss|demand|(^|_)y$|_next|next_)", re.I)
# A target's name: a generic label word on its own, an outcome word anywhere, or an is_/has_ flag. (The profiler's
# LABEL_NAME is looser: it also takes 'merchant_category', which is a feature.)
TARGET_NAME = re.compile(r"^(label|target|class|category|sentiment|outcome|y|response|result)$|churn|default|fraud|"
                         r"delinq|attrit|lapse|arrear|bad_flag|^is_|^has_|^will_", re.I)
DECISION = re.compile(r"(decision|approv\w*|accept\w*|declin\w*|reject\w*|app\w*_status|underwrit\w*)", re.I)
DECISION_WORDS = re.compile(r"^(approved?|accept(ed)?|declined?|reject(ed)?|denied|refused|booked|funded|granted)$", re.I)
RATIO = re.compile(r"(_to_|ratio|pct|percent|rate|share|score|util)", re.I)
DELIMS = (";", "|", ",")


def _role(role: str, confidence: float, *evidence: str) -> dict[str, Any]:
    return {"role": role, "confidence": round(float(min(1.0, max(0.0, confidence))), 2), "evidence": list(evidence)}


def _binary01(s: pd.Series) -> bool:
    v = set(pd.Series(s.dropna().unique()).astype(str).str.lower())
    return 0 < len(v) <= 2 and v <= {"0", "1", "0.0", "1.0", "true", "false", "yes", "no", "y", "n"}


def _multilabel(s: pd.Series) -> tuple[str, int, float] | None:
    """A delimited list of labels: (delimiter, vocabulary size, share of rows with >= 2 labels), or None."""
    vals = s.dropna().astype(str).head(2000)
    if len(vals) < 20 or vals.str.len().mean() > 120:
        return None
    for d in DELIMS:
        parts = vals.str.split(re.escape(d), regex=True).map(lambda xs: [x.strip() for x in xs if x.strip()])
        multi = float((parts.map(len) >= 2).mean())
        vocab = {t for xs in parts for t in xs}
        if multi >= 0.1 and 2 <= len(vocab) <= 60 and parts.map(len).max() <= 12:
            return d, len(vocab), multi
    return None


def _decision(s: pd.Series, y: pd.Series | None) -> dict[str, Any] | None:
    """G7: an approve/decline column (approved/declined, accepted/rejected, or a 0/1 or yes/no flag named like a
    decision). Strongest when the outcome is blank exactly for the declined rows: outcomes exist only for the approved."""
    from .reject.core import decision_values

    vals = decision_values(s)
    if not vals:
        return None
    words = all(DECISION_WORDS.match(str(v).strip()) for v in vals)
    if not words and not DECISION.search(str(s.name)):
        return None
    ev = [f"values '{vals[0]}' / '{vals[1]}' read like an approve/decline decision"]
    conf = 0.75
    if y is not None and y.isna().any():
        dec = s.astype(str) == vals[1]
        if dec.any() and (~dec).any() and y[dec].isna().mean() >= 0.99 and y[~dec].notna().mean() >= 0.95:
            conf = 0.95
            ev.append(f"'{y.name}' is blank for every '{vals[1]}' row: outcomes exist only for the approved")
    return _role("decision", conf, *ev)


def _single_auc(x: pd.Series, y: pd.Series) -> float | None:
    """AUC of one numeric column against a binary target (max of it and 1 − it): a near-perfect value means a leak."""
    ok = x.notna() & y.notna()
    if ok.sum() < 50 or y[ok].nunique() != 2 or not pd.api.types.is_numeric_dtype(x):
        return None
    from scipy.stats import rankdata
    pos = (y[ok].astype(str) == sorted(y[ok].astype(str).unique())[1]).to_numpy()
    r = rankdata(x[ok].to_numpy())
    n1, n0 = int(pos.sum()), int((~pos).sum())
    auc = (r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    return float(max(auc, 1 - auc))


def _target_candidates(df: pd.DataFrame, types: dict[str, str], skip: set[str] | None = None) -> list[tuple[float, str, str, list[str]]]:
    """(score, column, task, evidence) for every column that could be the target, best first. `skip`: label columns of
    a multi-label set (G8), which are a target together, not one by one."""
    out = []
    ncol = len(df.columns)
    for i, col in enumerate(df.columns):
        t, s = types[col], df[col]
        if t in ("text", "id") or col in (skip or ()):
            continue
        k = int(s.nunique(dropna=True))
        last = i == ncol - 1
        ev, score = [], 0.0
        named = bool(TARGET_NAME.search(str(col)))
        if named:
            score += 10; ev.append(f"name '{col}' reads like a label")
        many = named and t == "category" and MAX_CLASSES < k <= 500 and len(s) / k >= 20  # e.g. 77 banking intents
        if (2 <= k <= MAX_CLASSES or many) and (t == "category" or k <= 10):
            task = "binary" if k == 2 else "multiclass"
            score += 2 if t == "category" else 1
            ev.append(f"{k} distinct values")
        elif t == "number" and k > MAX_CLASSES and (named or (last and REG_TARGET.search(str(col)))):
            task = "regression"
            score += 3
            ev.append(f"numeric with {k:,} distinct values")
        else:
            continue
        if last:
            score += 3; ev.append("last column")
        score += i / max(1, ncol)
        if s.isna().any():
            score -= 1; ev.append(f"{int(s.isna().sum())} rows without a value")
        out.append((score, col, task, ev))
    out.sort(key=lambda c: -c[0])
    return out


def detect(df: pd.DataFrame, series_value: str | None = None) -> dict[str, Any]:
    """Every column's role with a confidence and its evidence, plus the target candidates."""
    n = len(df)
    types = {c: detect_type(str(c), df[c]) for c in df.columns}
    from .multilabel.core import indicator_groups

    # G8: a column of label lists, or a group of 0/1 columns one per label, is a multi-label target, never a single one
    ind = {c: g for g in indicator_groups(df) for c in g["columns"]}
    lists = {c for c in df.columns if types[c] in ("category", "text") and df[c].nunique() > 2 and _multilabel(df[c])}
    cands = _target_candidates(df, types, set(ind) | lists)
    # A target needs a name that reads like a label, or the last column; a table with neither has no label.
    target = cands[0] if cands and (cands[0][0] >= 5) else None
    if series_value:
        target = (10.0, series_value, "forecast", ["the numeric value of the time series"])
    tcol = target[1] if target else None
    dates, groups = date_and_group_columns(df, tcol)
    date_cols = {d["column"]: d for d in dates}
    group_cols = {g["column"]: g for g in groups}

    cols: list[dict[str, Any]] = []
    for col in df.columns:
        s, t, name = df[col], types[col], str(col)
        nn = s.dropna()
        k = int(nn.nunique())
        found: list[dict[str, Any]] = []
        if col == tcol:
            found.append(_role("target", min(0.97, 0.45 + target[0] / 25), *target[3], f"task: {target[2]}"))
        if k <= 1:
            found.append(_role("constant", 1.0, f"{k} distinct value{'s' if k != 1 else ''}"))
        if col in date_cols:
            d = date_cols[col]
            found.append(_role("date", 0.95, f"{d['parsed_share'] * 100:.0f}% of values parse as dates",
                               f"{d['distinct']:,} distinct, {d['from']} → {d['to']}"))
        if t == "id":
            found.append(_role("id", 0.95, f"{k:,} distinct values in {len(nn):,} rows (unique per row)",
                               *(["ID-like name"] if re.search(r"id|key|uuid", name, re.I) else [])))
        if col in group_cols and t != "text":
            g = group_cols[col]
            found.append(_role("entity", 0.9 if g["id_like_name"] else 0.7,
                               f"{g['groups']:,} values, {g['rows_per_group']:.2f} rows each on average (repeats)",
                               *(["ID-like name"] if g["id_like_name"] else [])))
        elif col not in date_cols and t != "id" and re.search(r"(_id$|^id_|item|series|store|branch|sku)", name, re.I) \
                and s.dtype == object and 2 <= k <= max(500, n // 5) and n / max(k, 1) >= 5:
            found.append(_role("entity", 0.75, f"{k} values with {n / k:.0f} rows each: one series per value",
                               "ID-like name"))
        num = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
        if num and k > 2:
            lo, hi = float(nn.min()), float(nn.max())
            if LGD.search(name) and lo >= 0 and hi <= 1:
                found.append(_role("lgd", 0.9, "name reads like loss given default", f"values in [{lo:g}, {hi:g}]"))
            if EAD.search(name) and lo >= 0:
                found.append(_role("ead", 0.9, "name reads like exposure at default", f"non-negative, max {hi:,.0f}"))
            if DURATION.search(name) and lo >= 0:
                found.append(_role("duration", 0.75, f"name '{col}' reads like a duration", f"non-negative, {lo:g} → {hi:g}"))
                # G6/G11: an explicit time to an event, next to a yes/no target, runs up to that outcome. It is the time T of a
                # survival model, but a yes/no model of the same target must not read it (it is known only afterwards).
                if tcol and col != tcol and target[2] == "binary" and TIME_TO_EVENT.search(name):
                    # Only confident when the target reads like an event (churned, defaulted…) or the name says "time to":
                    # a bare 'duration' next to 'class' is as often a loan term (German credit) as a call length.
                    if EVENT.search(tcol) or EXPLICIT_TTE.search(name):
                        found.append(_role("duration", 0.9, f"'{col}' names a time to an event", f"non-negative, {lo:g} → {hi:g}"))
                        found.append(_role("leak", 0.85, f"'{col}' is the time up to '{tcol}': known only after the outcome",
                                           "kept as the time T of a survival model, left out of a yes/no model"))
                    else:
                        found.append(_role("leak", 0.6, f"'{col}' may be measured up to or after '{tcol}' (a call's length, "
                                           "a time to the event): kept; leave it out if it is not known when you decide"))
            if MONEY.search(name) and lo >= 0 and not LGD.search(name) and not RATIO.search(name):
                found.append(_role("money", 0.8, "name reads like a money amount", f"non-negative, median {float(nn.median()):,.2f}"))
        if col != tcol and k == 2 and _binary01(s) and EVENT.search(name):
            found.append(_role("event", 0.7, f"0/1 flag named '{col}'"))
        if col != tcol and k == 2:
            dv = _decision(s, df[tcol] if tcol else None)
            if dv:
                found.append(dv)
        if s.dtype == object and k > 2:
            if SURNAME.search(name):
                found.append(_role("surname", 0.9, "name reads like a surname column", f"{k:,} distinct"))
            if ZIP.search(name) or (nn.astype(str).head(500).str.fullmatch(r"\d{5}(-\d{4})?").mean() > 0.95):
                found.append(_role("zip", 0.85, "name or 5-digit values read like a zip code"))
        if ZIP.search(name) and num and k > 2 and nn.between(0, 99999).all():
            found.append(_role("zip", 0.85, "name reads like a zip code", "integers up to 5 digits"))
        if col != tcol and PROTECTED.search(name) and 2 <= k <= 20:
            found.append(_role("protected", 0.9, f"name '{col}' reads like a protected attribute: never a model input",
                               "used only to check fairness"))
        if GEO.search(name) and 3 <= k <= 500:
            found.append(_role("geography", 0.7, f"name '{col}' reads like a place", f"{k} values"))
        if t in ("category", "text") and col != tcol:
            ml = _multilabel(s)
            if ml:
                d, v, m = ml
                found.append(_role("multilabel", 0.8, f"values are lists split by '{d}'", f"{v} distinct labels",
                                   f"{m * 100:.0f}% of rows carry 2 or more"))
        if col in ind:
            g = ind[col]
            found.append(_role("multilabel", 0.85, f"one of {len(g['columns'])} 0/1 columns named '{g['prefix']}_…': one per label",
                               f"{g['multi_share'] * 100:.0f}% of rows carry 2 or more"))
        if t == "category" and col != tcol and 2 <= k <= 20 and SEGMENT.search(name):
            found.append(_role("segment", 0.75, f"name '{col}' reads like a segment", f"{k} groups, smallest "
                               f"{int(nn.value_counts().iloc[-1]):,} rows"))
        if t == "text":
            found.append(_role("text", 0.9, "long strings with several words that rarely repeat"))
        if col != tcol and LEAK.search(name):
            found.append(_role("leak", 0.6, f"name '{col}' reads like something known only after the outcome"))
        if tcol and col != tcol and t != "id" and target[2] == "binary":
            auc = _single_auc(s, df[tcol]) if num else None
            if auc is not None and auc >= 0.99:
                found.append(_role("leak", 0.9, f"alone it separates {tcol} with AUC {auc:.3f}"))
            if s.isna().any() and df[tcol].notna().all():
                known = s.notna().groupby(df[tcol].astype(str)).mean()
                if len(known) == 2 and known.max() >= 0.99 and known.min() <= 0.01:
                    found.append(_role("leak", 0.9, f"filled in only when {tcol} = {known.idxmax()}: known after the outcome"))
            if not num and 2 <= k <= MAX_CLASSES:
                pure = df.groupby(col, dropna=True)[tcol].nunique()
                if len(pure) and (pure <= 1).all() and k < n / 20:
                    found.append(_role("leak", 0.85, f"every value of '{col}' has one {tcol}: it gives the answer away"))
        if t == "category" and not any(f["role"] in ("segment", "geography", "multilabel", "target", "event", "constant", "date", "entity") for f in found):
            found.append(_role("category", 0.6, f"{k} distinct values"))
        found.append(_role("feature", 0.5, f"profiled as {t}"))
        best: dict[str, dict[str, Any]] = {}
        for f in found:  # one entry per role: the most confident
            if f["role"] not in best or f["confidence"] > best[f["role"]]["confidence"]:
                best[f["role"]] = f
        found = list(best.values())
        # The primary role: the target if it is one, else the most confident (ties: the earlier, more specific role
        # in ROLES); 'feature' is the fallback.
        order = list(ROLES)
        primary = found[0] if col == tcol else max(found, key=lambda f: (f["confidence"], -order.index(f["role"])))
        cols.append({"name": name, "type": t, "unique": k, "role": primary["role"], "confidence": primary["confidence"],
                     "evidence": primary["evidence"], "also": [f for f in found if f is not primary and f["role"] != "feature"]})
    series = _series(df, cols)
    if series and not series_value and (not target or (target[0] < 10 and target[1] not in series["values"])):
        # A time series whose last column is a weak label candidate (e.g. a segment): the target is the series value.
        return detect(df, next((v for v in series["values"] if REG_TARGET.search(v)), series["values"][0]))
    return {"columns": cols, "target_candidates": [{"column": c, "task": task, "score": round(sc, 2), "evidence": ev}
                                                   for sc, c, task, ev in cands[:5]],
            "task": target[2] if target else None, "series": series}


def _series(df: pd.DataFrame, cols: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A time series table: a date with one row per (item, date), or one row per date, and a numeric value."""
    dates = [c["name"] for c in cols if c["role"] == "date" or any(a["role"] == "date" for a in c["also"])]
    if not dates:
        return None
    t = dates[0]
    items = [c["name"] for c in cols if c["role"] == "entity" and c["name"] != t]
    key = None
    if df[t].duplicated().mean() < 0.02:
        key = []
    for it in items:
        # a panel: one row per (item, date), and the items share one calendar (not one timestamp per event)
        k = df[it].nunique()
        if df[[it, t]].duplicated().mean() < 0.02 and k <= max(500, len(df) // 20) and df[t].nunique() <= 1.1 * len(df) / k:
            key = [it]
            break
    if key is None:
        return None
    values = [c["name"] for c in cols if c["type"] == "number" and c["unique"] > 10 and c["name"] not in key + [t]]
    return {"timestamp": t, "item_id": key[0] if key else None, "values": values} if values else None


def apply_overrides(found: dict[str, Any], overrides: dict[str, str] | None) -> dict[str, Any]:
    """The user's role edits: one role per column, confidence 1. A new target replaces the old one."""
    if not overrides:
        return found
    bad = {r for r in overrides.values() if r not in ROLES}
    if bad:
        raise ValueError(f"Unknown role {sorted(bad)[0]!r}; use one of {', '.join(ROLES)}.")
    names = {c["name"] for c in found["columns"]}
    missing = [c for c in overrides if c not in names]
    if missing:
        raise ValueError(f"Column '{missing[0]}' is not in the file.")
    new_target = next((c for c, r in overrides.items() if r == "target"), None)
    cols = []
    for c in found["columns"]:
        c = dict(c)
        if c["name"] in overrides:
            r = overrides[c["name"]]
            if r != c["role"]:
                c["also"] = [a for a in c["also"] if a["role"] != r] + [{"role": c["role"], "confidence": c["confidence"], "evidence": c["evidence"]}]
            c.update(role=r, confidence=1.0, evidence=["set by you"], overridden=True)
        elif new_target and c["role"] == "target":
            c.update(role="feature", confidence=0.5, evidence=["the target you chose is another column"])
        cols.append(c)
    return {**found, "columns": cols}


def summary(cols: list[dict[str, Any]]) -> dict[str, Any]:
    """Role → columns, primary roles first, then the secondary roles a column also has (e.g. churned is the target
    and the survival event)."""
    out: dict[str, list[str]] = {}
    for c in cols:
        out.setdefault(c["role"], []).append(c["name"])
    for c in cols:
        for a in c["also"]:
            if a["role"] in SECONDARY and c["name"] not in out.get(a["role"], []) and c["role"] not in EXCLUDED:
                out.setdefault(a["role"], []).append(c["name"])
    return out


# --------------------------------------------------------------------------------------------------------- suggestions

@dataclass
class Ctx:
    """What a suggestion sees: the frame, the columns with their roles, role → columns, and the target's task."""
    df: pd.DataFrame
    columns: list[dict[str, Any]]
    roles: dict[str, list[str]]
    series: dict[str, Any] | None
    versions: list[dict[str, Any]] = field(default_factory=list)  # ready binary registry versions of this table

    def first(self, role: str) -> str | None:
        return (self.roles.get(role) or [None])[0]

    @property
    def target(self) -> str | None:
        return self.first("target")

    @property
    def excluded(self) -> list[str]:
        """Columns left out of training: row IDs, constants, leaks (also a column whose second role is a confident
        leak, e.g. LGD, filled in only for defaulters), and entity IDs (they drive the group split instead)."""
        return [c["name"] for c in self.columns if c["role"] in EXCLUDED or c["role"] in NOT_INPUTS or c["role"] == "entity"
                or any(a["role"] == "leak" and a["confidence"] >= 0.85 for a in c["also"])]


@dataclass
class Suggestion:
    """One thing the user can build. `match` returns (score 0–100, reason, pre-fill params for `route`) or None."""
    id: str
    title: str
    route: str
    match: Callable[[Ctx], tuple[float, str, dict[str, Any]] | None]
    available: bool = True  # False: the flow is not built yet; the suggestion is not shown


def _task_of(ctx: Ctx) -> str | None:
    """The target's task, from its values (so a target the user picked works the same as a detected one)."""
    t = ctx.target
    if not t:
        return None
    k = int(ctx.df[t].nunique(dropna=True))
    if pd.api.types.is_numeric_dtype(ctx.df[t]) and k > MAX_CLASSES:
        return "regression"
    return "binary" if k == 2 else "multiclass" if 2 < k <= MAX_CLASSES or (k <= 500 and len(ctx.df) / k >= 20) else None


def _tabular_prefill(ctx: Ctx, **extra: Any) -> dict[str, Any]:
    p: dict[str, Any] = {"target": ctx.target}
    if ctx.excluded:
        p["drop"] = [c for c in ctx.excluded if c != ctx.target]
    date, ent = ctx.first("date"), ctx.first("entity")
    if date and ent:
        p.update(split="time-group", time=date, group=ent)
    elif date:
        p.update(split="time", time=date)
    elif ent:
        p.update(split="group", group=ent)
    p.update(extra)
    return p


def _split_words(p: dict[str, Any]) -> str:
    return {"time-group": f"an out-of-time + group split (by {p.get('time')}, no {p.get('group')} in both)",
            "time": f"an out-of-time split by {p.get('time')}", "group": f"a group split by {p.get('group')}"}.get(p.get("split", ""), "a random stratified split")


def _binary(ctx: Ctx):
    if _task_of(ctx) != "binary":
        return None
    t = ctx.target
    vc = ctx.df[t].dropna().astype(str).value_counts()
    rare = vc.index[-1]
    p = _tabular_prefill(ctx, metric="expected_cost")
    risk = re.search(r"default|churn|fraud|delinq|attrit|lapse|arrear", t, re.I)
    what = f"{risk.group(0).capitalize()} risk model" if risk else "Yes/no classifier"
    return (95 if TARGET_NAME.search(t) else 80,
            f"'{t}' has two values ({rare} is {vc.iloc[-1] / vc.sum() * 100:.1f}% of rows), so it is a yes/no outcome. "
            f"Pre-filled with {_split_words(p)} and a business cost (a missed {rare} costs 10× a false alarm; edit it in Goal)."
            + (f" {', '.join(repr(c) for c in fl)} {'is' if len(fl) == 1 else 'are'} left out of the model and kept for the "
               f"fair-lending check (Registry → Fair Lending)." if (fl := [c for r in ("surname", "zip", "protected") for c in ctx.roles.get(r) or []]) else "")
            + (f" Only the approved applicants have an outcome, so this model learns from them alone: see Reject inference."
               if ctx.roles.get("decision") and ctx.df[t].isna().any() else ""),
            {**p, "title": what})


def _multiclass(ctx: Ctx):
    if _task_of(ctx) != "multiclass":
        return None
    t, k = ctx.target, int(ctx.df[ctx.target].nunique())
    text = ctx.roles.get("text") or []
    p = _tabular_prefill(ctx)
    return (90 if TARGET_NAME.search(t) else 75,
            f"'{t}' has {k} classes{f', with free text in {text[0]}' if text else ''}: a classifier routes each row to one. "
            f"Pre-filled with {_split_words(p)}.", p)


def _regression(ctx: Ctx):
    if _task_of(ctx) != "regression":
        return None
    t = ctx.target
    s = ctx.df[t].dropna()
    p = _tabular_prefill(ctx)
    return (85, f"'{t}' is a number ({s.min():,.4g} → {s.max():,.4g}, median {s.median():,.4g}): regression predicts it, "
                f"and a range (quantiles) is one switch away. Pre-filled with {_split_words(p)}.", p)


def _forecast(ctx: Ctx):
    s = ctx.series
    if not s:
        return None
    v = ctx.target if ctx.target in s["values"] else s["values"][0]
    items = ctx.df[s["item_id"]].nunique() if s["item_id"] else 1
    labelled = ctx.target and ctx.target not in s["values"] and TARGET_NAME.search(ctx.target)
    return (60 if labelled else 97,
            f"'{s['timestamp']}' orders the rows in time{f', one series per {s["item_id"]} ({items})' if s['item_id'] else ''} "
            f"with a numeric value '{v}': forecast its future.",
            {"dataset_only": True, "timestamp": s["timestamp"], "item_id": s["item_id"], "value": v})


def _anomaly(ctx: Ctx):
    nums = [c["name"] for c in ctx.columns if c["type"] == "number" and c["role"] not in EXCLUDED]
    if len(nums) < 2:
        return None
    if not ctx.target and not ctx.series:
        return (90, f"No column reads like a label, so nothing can be learned from outcomes; IsolationForest flags the "
                    f"unusual rows from {len(nums)} numeric columns instead"
                    f"{f', compared within each {ctx.first("entity")}' if ctx.first('entity') else ''}.",
                {"dataset_only": True})
    return (30, f"Even with a label, unusual rows are worth a look: IsolationForest on {len(nums)} numeric columns.",
            {"dataset_only": True})


def _recourse(ctx: Ctx):
    """G1: what would change a declined decision. Needs a yes/no target and something the applicant can change."""
    if _task_of(ctx) != "binary":
        return None
    feats = [c["name"] for c in ctx.columns if c["role"] not in EXCLUDED and c["role"] not in ("target", "entity", "date")]
    acts = actionability(ctx.df[feats], feats, ctx.columns)
    can = [f for f, a in acts.items() if a["action"] != "fixed"]
    fixed = [f for f, a in acts.items() if a["action"] == "fixed"]
    if len(can) < 2:
        return None
    risk = re.search(r"default|churn|fraud|delinq|attrit|lapse|arrear", ctx.target, re.I)
    why = (f"For each declined applicant, the smallest realistic changes that flip the decision: {len(can)} columns can "
           f"change ({', '.join(can[:4])}{'…' if len(can) > 4 else ''}); {', '.join(fixed) or 'none'} stay fixed.")
    # Shown once a yes/no model of this table is registered (before that, the yes/no suggestion trains one).
    match = [v for v in ctx.versions if v.get("label") in (None, ctx.target)]
    if not match:
        return None
    v = match[0]
    return (70 if risk else 55, f"{why} Opens {v['model_name']} v{v['version']}, Reason codes tab.",
            {"version": v["id"], "tab": "Reason Codes"})


def _credit_report(ctx: Ctx):
    """G2: the standard credit model report (Gini/KS by month, score bands, PSI/CSI, vintage curves) of a registered
    yes/no model of this table. Strongest with a date column (by-month tables) and a default-like target."""
    if _task_of(ctx) != "binary":
        return None
    match = [v for v in ctx.versions if v.get("label") in (None, ctx.target)]
    if not match:
        return None
    v, date = match[0], ctx.first("date")
    risk = re.search(r"default|delinq|arrear|bad", ctx.target, re.I)
    return (68 if risk and date else 50,
            f"A model performance report for {v['model_name']} v{v['version']}: Gini and KS "
            f"{f'by month of {date}' if date else 'by scored batch'}, score bands with bad rate, capture and lift, PSI of "
            f"the score and CSI per feature, and vintage curves. Opens the Credit report tab.",
            {"version": v["id"], "tab": "Credit Report"})


def _expected_loss(ctx: Ctx):
    """G3: a default flag plus loss given default and exposure at default → PD × LGD × EAD, with stress scenarios.
    LGD and EAD are often leaks of the default flag (filled in only for defaulters), so their role may be secondary."""
    if _task_of(ctx) != "binary":
        return None

    def having(role: str) -> str | None:
        return next((c["name"] for c in ctx.columns if c["role"] == role), None) or \
            next((c["name"] for c in ctx.columns if any(a["role"] == role for a in c["also"])), None)

    lgd, ead = having("lgd"), having("ead")
    if not lgd or not ead:
        return None
    segs = [c for c in (ctx.roles.get("segment") or []) + (ctx.roles.get("geography") or [])][:3]
    return (97, f"'{ctx.target}' says which loans defaulted, '{lgd}' how much of the exposure was lost and '{ead}' how much "
                f"was owed: three AutoGluon models give EL = PD · LGD · EAD per loan"
                f"{f', broken down by {', '.join(segs)}' if segs else ''}, checked against the realised loss, with stress "
                f"scenarios.", {"dataset_only": True, "pd": ctx.target, "lgd": lgd, "ead": ead, "segments": segs})


def _fair_lending(ctx: Ctx):
    """G4: a fair-lending check without protected attributes. BISG estimates each applicant's group probabilities from
    surname × zip; adverse impact ratios follow on the registered yes/no model's holdout decisions."""
    if _task_of(ctx) != "binary" or not ctx.first("surname"):
        return None
    match = [v for v in ctx.versions if v.get("label") in (None, ctx.target)]
    if not match:
        return None
    v, z = match[0], ctx.first("zip")
    truth = ctx.first("protected")
    return (72, f"'{ctx.first('surname')}'{f' and {z!r}' if z else ''} give each applicant's estimated group probabilities "
                f"(BISG: surname × {'zip' if z else 'the national mix'}), so adverse impact ratios and approval-rate gaps "
                f"can be checked without recording race or ethnicity{f'; {truth!r} validates the proxy' if truth else ''}. "
                f"Opens {v['model_name']} v{v['version']}, Fair Lending tab.", {"version": v["id"], "tab": "Fair Lending"})


def _segmented(ctx: Ctx):
    """G5: one model per segment against one global model. Offered for a yes/no or numeric target with a segment column
    (2–12 groups); it comes first only when a quick look already shows an input acting in opposite directions across the
    segments (rank correlation with the target, inside each segment)."""
    from .segmented.core import SEGMENT_MAX, heterogeneity

    task = _task_of(ctx)
    if task not in ("binary", "regression"):
        return None
    segs = [c["name"] for c in ctx.columns if c["name"] != ctx.target and 2 <= c["unique"] <= SEGMENT_MAX
            and (c["role"] == "segment" or any(a["role"] == "segment" for a in c["also"]))]
    if not segs:
        return None
    seg = segs[0]
    h = heterogeneity(ctx.df, ctx.target, seg)
    strong = bool(h and h["opposite"] and min(abs(h["high"][1]), abs(h["low"][1])) >= 0.08)
    k = int(ctx.df[seg].nunique())
    return (96 if strong else 55,
            f"'{seg}' splits the rows into {k} segments. One AutoGluon model per segment is compared with one global model on "
            f"the same holdout, per segment, with paired CIs; the set is registered as one routed version."
            + (f" {h['words']}, so the segments may need their own models." if strong and h else ""),
            {"dataset_only": True, "label": ctx.target, "segment": seg, "candidates": segs,
             "title": f"Segmented models by {seg}"})


def _survival(ctx: Ctx):
    """G6: a duration and an event flag → time-to-event models. The duration role is broad (G0: tenure_months matches), so
    the suggestion comes first only for an explicit time-to-event name (duration, time_to_…, months_to_…); a tenure-like
    name may be time so far at a snapshot, not time to the event, and is offered lower, with that caveat."""
    from .survival.core import pairs

    p = pairs(ctx.df, ctx.columns, ctx.target)
    if not p["durations"] or not p["events"]:
        return None
    d, e = p["durations"][0], p["events"][0]
    reason = (f"'{d['column']}' is the time each row was followed and '{e['column']}' whether the event happened "
              f"({e['rate'] * 100:.0f}%; the rest are censored). Cox PH and a random survival forest give each row's survival "
              f"curve and Ŝ(τ) at chosen horizons, with Harrell's C, AUC(τ), hazard ratios and a Kaplan–Meier calibration check.")
    if not d["strong"]:
        reason += (f" Check first: '{d['column']}' may be time so far at a snapshot rather than time up to "
                   f"'{e['column']}'; only then is this time-to-event data.")
    return (97 if d["strong"] else 45, reason,
            {"dataset_only": True, "duration": d["column"], "event": e["column"],
             "title": f"Time to {e['column']} (survival)"})


def _reject_inference(ctx: Ctx):
    """G7: a yes/no outcome that is blank for the declined applicants (a decision column, or just missing outcomes) →
    reject inference: approved-only against reweighting, parcelling and fuzzy augmentation."""
    if _task_of(ctx) != "binary":
        return None
    t = ctx.target
    miss = float(ctx.df[t].isna().mean())
    dec = next((c for c in ctx.columns if c["role"] == "decision"), None)
    if miss < 0.05 or (dec is None and miss < 0.15):
        return None
    strong = bool(dec and dec["confidence"] >= 0.9)
    return (97 if strong else 60,
            f"'{t}' is known for only {(1 - miss) * 100:.0f}% of rows"
            + (f": the applicants '{dec['name']}' approved. " if dec else " (the rest are taken as declined). ")
            + "A model trained on them is used on everyone who applies; reject inference adds the declined applicants back "
              "(reweighting, parcelling, fuzzy augmentation) and compares each with the approved-only model.",
            {"dataset_only": True, "label": t, "decision": dec["name"] if dec else None,
             "title": f"Reject inference for {t}"})


def _multilabel_set(ctx: Ctx):
    """G8: a column of label lists ('fees;fraud') or a group of 0/1 label columns → one AutoGluon model per label, each
    with its own threshold; the row's prediction is a label set."""
    from .multilabel.core import find, label_matrix

    f = find(ctx.df, ctx.columns)
    if not f["source"] or len(f["labels"]) < 2:
        return None
    M, _ = label_matrix(ctx.df, f)
    multi = float((M.sum(axis=1) >= 2).mean())
    text = ctx.roles.get("text") or []
    return (97 if multi >= 0.1 else 50,
            f"Each row carries several labels in {('' if f['source'] == 'delimited' else 'the columns ') + repr(f['label_column'])}: "
            f"{len(f['labels'])} labels, {multi * 100:.0f}% of rows with two or more. One AutoGluon model per label"
            f"{f' (reading {text[0]!r} as text)' if text else ''}, each with its own F1 threshold, gives every row a label set; "
            f"scored by per-label F1, micro/macro F1 and Hamming loss.",
            {"dataset_only": True, "label_column": f["label_column"], "labels": f["labels"],
             "title": f"Multi-label: {f['label_column']}"})


def _import(ctx: Ctx):
    return (5, "Already have a trained AutoGluon TabularPredictor for this table? Import it to register, explain and serve it.", {})


SUGGESTIONS: list[Suggestion] = [
    Suggestion("binary", "Yes/no classifier", "quick-train", _binary),
    Suggestion("multiclass", "Classifier", "quick-train", _multiclass),
    Suggestion("regression", "Predict a number", "quick-train", _regression),
    Suggestion("forecast", "Forecast", "forecast", _forecast),
    Suggestion("anomaly", "Find unusual rows", "anomaly", _anomaly),
    Suggestion("recourse", "What would change the decision", "registry", _recourse),  # G1
    Suggestion("credit_report", "Credit model report", "registry", _credit_report),  # G2
    Suggestion("expected_loss", "Expected loss (PD × LGD × EAD)", "expected-loss", _expected_loss),  # G3
    Suggestion("fair_lending", "Fair-lending check (BISG)", "registry", _fair_lending),  # G4
    Suggestion("segmented", "Segmented models", "segmented", _segmented),  # G5
    Suggestion("survival", "Time to event (survival)", "survival", _survival),  # G6
    Suggestion("reject_inference", "Reject inference", "reject-inference", _reject_inference),  # G7
    Suggestion("multilabel", "Multi-label classifier", "multilabel", _multilabel_set),  # G8
    Suggestion("import", "Import a predictor", "import", _import),
]


def suggest(ctx: Ctx) -> list[dict[str, Any]]:
    out = []
    for s in SUGGESTIONS:
        if not s.available:
            continue
        m = s.match(ctx)
        if m:
            score, reason, prefill = m
            title = prefill.pop("title", s.title)
            out.append({"id": s.id, "title": title, "route": s.route, "score": score, "reason": reason, "prefill": prefill})
    out.sort(key=lambda x: -x["score"])
    return out


def intake(df: pd.DataFrame, overrides: dict[str, str] | None = None,
           versions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    found = apply_overrides(detect(df), overrides)
    cols = found["columns"]
    roles = summary(cols)
    series = _series(df, cols) if overrides else found["series"]  # edited roles: recompute the series
    ctx = Ctx(df, cols, roles, series, versions or [])
    return {"rows": int(len(df)), "columns": cols, "roles": roles, "target_candidates": found["target_candidates"],
            "series": series, "suggestions": suggest(ctx), "role_names": ROLES}


# ------------------------------------------------------------------------------------------- G1: what can change

# What a counterfactual ("what would change the decision") may change, from the roles plus a few name rules. Each column
# gets an action and the evidence for it; the user edits it per model (Registry → Reason codes → Recourse).
#   fixed     the applicant cannot change it (age, dates, IDs, where they live, protected attributes)
#   any       it can move either way within the observed range
#   increase  it can only grow (time passes: months employed, account age)
#   decrease  it can only shrink (history ages out: delinquencies, prior defaults, open debt)
ACTIONS = ("fixed", "any", "increase", "decrease")
IMMUTABLE_ROLES = {"id": "a row ID", "entity": "a customer/entity ID", "date": "a date", "surname": "a surname",
                   "zip": "a zip/postal code", "geography": "where the applicant lives", "target": "the outcome",
                   "protected": "a protected attribute",
                   "leak": "known only after the outcome", "constant": "constant"}
IMMUTABLE_NAME = re.compile(r"(^|_)(age|birth\w*|dob|gender|sex|race|ethnic\w*|marital\w*|nationality|religion|"
                            r"citizenship|disab\w*)($|_)", re.I)
ONLY_UP = re.compile(r"(months_employed|years_employed|employment_length|emp_length|tenure|account_age|"
                     r"years_at|months_at|time_at|history_length|credit_history)", re.I)
ONLY_DOWN = re.compile(r"(delinq\w*|prior_default\w*|defaults_|bankrupt\w*|late_payment\w*|missed_payment\w*|"
                       r"collections?|judgement\w*|inquir\w*)", re.I)


def actionability(df: pd.DataFrame, features: list[str], columns: list[dict[str, Any]] | None = None) -> dict[str, dict[str, str]]:
    """Feature → {"action", "why"} from the detected roles (`columns`, else detected on df) and the name rules above."""
    cols = {c["name"]: c for c in (columns if columns is not None else detect(df)["columns"] if len(df) else [])}
    out: dict[str, dict[str, str]] = {}
    for f in features:
        c = cols.get(f, {"role": "feature", "also": []})
        roles = [c["role"]] + [a["role"] for a in c.get("also", [])]
        hit = next((r for r in roles if r in IMMUTABLE_ROLES), None)
        if hit:
            out[f] = {"action": "fixed", "why": f"detected as {IMMUTABLE_ROLES[hit]}"}
        elif IMMUTABLE_NAME.search(f):
            out[f] = {"action": "fixed", "why": f"'{f}' reads like a personal attribute the applicant cannot change"}
        elif ONLY_UP.search(f):
            out[f] = {"action": "increase", "why": f"'{f}' only grows as time passes"}
        elif ONLY_DOWN.search(f):
            out[f] = {"action": "decrease", "why": f"'{f}' is history: it can age out, not be added to"}
        else:
            out[f] = {"action": "any", "why": "the applicant can change it"}
    return out
