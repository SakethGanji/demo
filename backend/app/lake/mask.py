"""P20 (plan §6.3, C §4.3): PII column tags, their suggestions, and the masks.

**Tags** belong to the dataset root (`column_tags (root_id, table_name, column_name)`), not to a version, so they carry
forward. A column with no row is untagged. `ensure()` tags the columns of a table it has not seen yet: the profiler's
rules (`profiling.pii_suggestion`: names, then value patterns on a 200-row sample) write `source='suggested'` rows.
Every masked read calls it first, so no read can outrun the suggestion. A suggested tag masks exactly like a
confirmed one: the default is safe. A maintainer confirms or changes tags (`PUT /datasets/{id}/pii`, `data.tag_pii`).

**Masks** (a column is masked when its tag's mask is not `none`):

| mask | shows | e.g. |
|---|---|---|
| `redact` | `•••` | `•••` |
| `hash` | `sha256(team salt ‖ value)[:12]`: stable within a team, so joins and counts still work | `3f9a0c21b7e4` |
| `last4` | `•••` + the last 4 characters | `•••6789` |
| `year_only` | the first 4 characters when they are a year | `1984` |
| `bucket` | numbers: the decade-wide bucket of the leading digit (`47` → `40–50`); text: the first 3 characters | `021•••` |

Missing stays missing. Every masked column is text (VARCHAR) in a masked relation.

The masks exist twice, **in SQL** (`Mask.sql`, what `frames.scan(mask_for=…)` puts into the one relation builder: grid,
rows, lookup, filter preview, export, the agent's CSV, diff samples) and **in Python** (`Mask.value`, for values a
route already holds: run pages read through pandas, cached diff cells, table_stats top values, error messages). Both
render a value's text the same way (`_text` / `_text_sql`: a whole DOUBLE is written as an integer, booleans as
true/false), so a hash or a last4 is the same string on both paths.

**Unmasked reads** (`for_request(…, unmasked=True)`): a reason (2–500 characters) is required, the decision is
`data.read_unmasked` (`data.export_unmasked` for an export) through P19's `require` machinery, and every unmasked read
is audited as `dataset_rows_read` (never deduplicated). A denied unmasked read stays masked: in `audit` mode it is
recorded as `authz_would_deny` and answered masked (the PII is not shown on a would-deny), in `enforce` mode it is 403.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

TAGS = ("pii_direct", "pii_quasi", "protected", "sensitive", "key", "none")
MASKS = ("redact", "hash", "last4", "year_only", "bucket", "none")
DEFAULT_MASK = {"pii_direct": "redact", "pii_quasi": "bucket", "sensitive": "redact", "protected": "none", "key": "none", "none": "none"}
DOTS = "•••"
#: in `Mask.scrub`, the keys that hold a value of the column named by the same dict's column / feature / name
#: keys `Mask.scrub` leaves alone: what a response says about masking itself
META_KEYS = ("masks", "masked_columns", "unmasked_refused", "tags", "pending_review", "pii_features",
             "kinds", "types", "column_types", "roles")  # F-PII: column → kind / type / role maps hold no values
PAIR_KEYS = ("value", "a", "b", "example", "values", "top", "key", "old", "new", "from", "to", "lo", "hi", "min", "max", "edges",
             "cuts", "range", "lower", "upper", "group")  # F-X4a: scorecard cuts, ranges, fairness groups
#: F-PII: statistics of a masked column that a report quotes (anomaly reasons' median, a shift check's means): nulled,
#: as `stats` nulls a masked column's number stats
STAT_KEYS = ("median", "mean", "std", "mean_expected", "mean_actual", "std_expected", "std_actual", "q1", "q3", "p25", "p75")
#: the keys that name the column a dict is about (`col` before `name`: a Feature Lab transform {name, op, col, edges})
COLUMN_KEYS = ("column", "feature", "col", "name")
TRUE = ("1", "true", "yes")
SAMPLE = 200


class MaskError(ValueError):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


# -- the masks, in Python ---------------------------------------------------------------------------------------------

def _text(v: Any) -> str:
    """A value's text as DuckDB's CAST(… AS VARCHAR) writes it (see `_text_sql`)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 2 ** 53:
            return str(int(v))
        return repr(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    try:
        import numpy as np

        if isinstance(v, np.generic):
            return _text(v.item())
    except ImportError:  # pragma: no cover
        pass
    return str(v)


def _missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(v != v)  # noqa: PLR0124 - NaN / NaT
    except (TypeError, ValueError):
        return False


def _bucket_num(x: float) -> str:
    if math.isnan(x) or abs(x) >= 1e15:
        return DOTS
    if x == 0:
        return "0"
    if abs(x) < 1:
        return "-1–0" if x < 0 else "0–1"
    w = int(10 ** math.floor(math.log10(abs(x))))
    lo = int(math.floor(x / w) * w)
    return f"{lo}–{lo + w}"


def mask_value(v: Any, kind: str, salt: str = "") -> Any:
    if _missing(v) or kind == "none":
        return v
    if kind == "redact":
        return DOTS
    if kind == "bucket" and isinstance(v, (int, float)) and not isinstance(v, bool):
        return _bucket_num(float(v))
    s = _text(v)
    if kind == "hash":
        return hashlib.sha256((salt + s).encode()).hexdigest()[:12]
    if kind == "last4":
        return DOTS + s[-4:] if len(s) > 4 else DOTS
    if kind == "year_only":
        return s[:4] if re.match(r"^[0-9]{4}", s) else DOTS
    if kind == "bucket":
        return s[:3] + DOTS if len(s) > 3 else DOTS
    return DOTS


# -- the masks, in SQL (DuckDB) ---------------------------------------------------------------------------------------

def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _num_type(t: str) -> bool:
    t = (t or "").upper()
    return t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT",
                 "DOUBLE", "FLOAT", "REAL") or t.startswith(("DECIMAL", "NUMERIC"))


def _text_sql(x: str, t: str) -> str:
    t = (t or "").upper()
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith(("DECIMAL", "NUMERIC")):
        return (f"(CASE WHEN isfinite(CAST({x} AS DOUBLE)) AND {x} = trunc({x}) AND abs({x}) < 9007199254740992"
                f" THEN CAST(CAST({x} AS BIGINT) AS VARCHAR) ELSE CAST({x} AS VARCHAR) END)")
    if t == "BOOLEAN":
        return f"(CASE WHEN {x} THEN 'true' ELSE 'false' END)"
    return f"CAST({x} AS VARCHAR)"


def mask_sql(x: str, t: str, kind: str, salt: str = "") -> str:
    """The SQL expression of mask `kind` over column expression `x` of DuckDB type `t` (VARCHAR out; NULL stays NULL)."""
    if kind == "none":
        return x
    s = _text_sql(x, t)
    if kind == "redact":
        body = _lit(DOTS)
    elif kind == "hash":
        body = f"substr(sha256({_lit(salt)} || {s}), 1, 12)"
    elif kind == "last4":
        body = f"(CASE WHEN length({s}) > 4 THEN {_lit(DOTS)} || right({s}, 4) ELSE {_lit(DOTS)} END)"
    elif kind == "year_only":
        body = f"(CASE WHEN regexp_matches({s}, '^[0-9]{{4}}') THEN left({s}, 4) ELSE {_lit(DOTS)} END)"
    elif kind == "bucket" and _num_type(t):
        d = f"CAST({x} AS DOUBLE)"
        w = f"CAST(pow(10, floor(log10(abs({d})))) AS BIGINT)"
        lo = f"CAST(floor({d} / {w}) * {w} AS BIGINT)"
        body = (f"(CASE WHEN NOT isfinite({d}) OR abs({d}) >= 1e15 THEN {_lit(DOTS)} WHEN {d} = 0 THEN '0' WHEN abs({d}) < 1 THEN (CASE WHEN {d} < 0 THEN '-1–0' ELSE '0–1' END)"
                f" ELSE CAST({lo} AS VARCHAR) || '–' || CAST({lo} + {w} AS VARCHAR) END)")
    elif kind == "bucket":
        body = f"(CASE WHEN length({s}) > 3 THEN left({s}, 3) || {_lit(DOTS)} ELSE {_lit(DOTS)} END)"
    else:
        body = _lit(DOTS)
    return f"(CASE WHEN {x} IS NULL THEN NULL ELSE {body} END)"


# -- the mask of one version table, for one reader ------------------------------------------------------------------

@dataclass
class Mask:
    """Which columns of one version table are masked for this read, and how. `cols` is empty when nothing is masked
    (no tags, or an allowed unmasked read)."""

    version_id: str
    table: str
    root_id: str
    team_id: str
    salt: str
    cols: dict[str, str] = field(default_factory=dict)       # column → mask kind, masked columns only
    tags: dict[str, dict[str, Any]] = field(default_factory=dict)
    unmasked: bool = False
    reason: str | None = None
    refused: dict[str, Any] | None = None                    # an unmasked read that was asked for and not allowed

    def sql(self, col: str, x: str, t: str) -> str:
        k = self.cols.get(col)
        return x if k is None else mask_sql(x, t, k, self.salt)

    def value(self, col: str, v: Any) -> Any:
        k = self.cols.get(col)
        return v if k is None else mask_value(v, k, self.salt)

    def fn(self, col: str) -> Callable[[Any], Any] | None:
        k = self.cols.get(col)
        return None if k is None else (lambda v, _k=k: mask_value(v, _k, self.salt))

    def record(self, r: dict[str, Any]) -> dict[str, Any]:
        if not self.cols or not isinstance(r, dict):
            return r
        return {k: (self.value(k, v) if k in self.cols else v) for k, v in r.items()}

    def known_values(self) -> dict[str, list[Any]]:
        """The stored min / max / top values of the masked columns (table_stats), so `scrub` also replaces them where
        a report quotes them in a sentence ("drifts from 1950-01-01 to 1999-12-20"). Empty when there are no stats."""
        if getattr(self, "_known", None) is not None:
            return self._known
        out: dict[str, list[Any]] = {}
        try:
            from .. import frames
            from . import stats as ST

            st = ST.get(frames.data_identity(self.version_id, self.table)) or {}
            for c in st.get("columns") or []:
                if c.get("name") in self.cols:
                    out[c["name"]] = [v for v in (c.get("min"), c.get("max"), *(t.get("value") for t in c.get("top") or []))
                                      if v is not None]
        except Exception:  # noqa: BLE001, S110 - a net, not a requirement
            pass
        self._known = out
        return out

    @property
    def masked(self) -> list[str]:
        return sorted(self.cols)

    def info(self) -> dict[str, Any]:
        """What a response says about masking."""
        out: dict[str, Any] = {"masked_columns": self.masked, "unmasked": self.unmasked}
        if self.cols:
            out["masks"] = dict(self.cols)
        if self.refused:
            out["unmasked_refused"] = self.refused
        return out

    def scrub(self, obj: Any, extra_values: dict[str, list[Any]] | None = None) -> Any:
        """A JSON-ready result with every value of a masked column masked, wherever it sits:
        - `{column|feature|name: c, value|a|b|example: v}` pairs and `{c: v}` feature dicts (keys = masked columns);
        - and then, in every string, each raw value seen above (plus `extra_values`) replaced by its mask.
        This is the net for results built by code that does not know about masks (run pages, Model Doctor)."""
        if not self.cols:
            return obj
        seen: dict[str, str] = {}

        def note(c: str, v: Any) -> Any:
            m = self.value(c, v)
            if not _missing(v) and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                forms = {_text(v)}
                if isinstance(v, (int, float)) and math.isfinite(v):  # as reports format numbers: 1,234,567 · 1234.5
                    forms |= {f"{v:,.0f}", f"{v:,}", f"{v:.0f}", f"{v:.1f}", f"{v:.3g}", f"{v:,.2f}", f"{v:g}", f"{v:,.4g}"}
                for s in forms:
                    if len(s) >= 3:
                        seen[s] = str(m)
            return m

        for c, vs in {**self.known_values(), **(extra_values or {})}.items():
            if c in self.cols:
                for v in vs:
                    note(c, v)

        def walk(o: Any, ctx: str | None = None) -> Any:
            """`ctx`: the masked column an enclosing dict names ({column: dob, bins: [{from, to}]})."""
            if isinstance(o, dict):
                own = next((o[k] for k in COLUMN_KEYS if isinstance(o.get(k), str)), None)
                ref = own if own in self.cols else (ctx if own is None else None)
                out = {}
                cols = o.get("columns")
                if isinstance(cols, list) and cols and all(isinstance(x, str) for x in cols) and isinstance(o.get("rows"), list) \
                        and any(x in self.cols for x in cols):  # a table: {columns: [names], rows: [[values]]}
                    o = {**o, "rows": [[note(cols[i], x) if i < len(cols) and cols[i] in self.cols and not isinstance(x, (dict, list)) else x
                                        for i, x in enumerate(r)] if isinstance(r, list) else r for r in o["rows"]]}
                for k, v in o.items():
                    if k in META_KEYS:
                        out[k] = v
                    elif k in self.cols and not isinstance(v, (dict, list)):
                        out[k] = note(k, v)
                    elif k in self.cols and isinstance(v, dict):  # F-X4a: {ssn: {min, max}} (recourse ranges): its column
                        out[k] = walk(v, k)
                    elif k in self.cols and isinstance(v, list):  # {ssn: [values]} (categories, cuts)
                        out[k] = [walk(x, k) if isinstance(x, (dict, list)) else note(k, x) for x in v]
                    elif ref is not None and k in STAT_KEYS and not isinstance(v, (dict, list)):
                        out[k] = None
                    elif ref is not None and k in PAIR_KEYS and not isinstance(v, (dict, list)):
                        out[k] = note(ref, v)
                    elif ref is not None and k in PAIR_KEYS and isinstance(v, list):
                        out[k] = [walk(x, ref) if isinstance(x, (dict, list)) else note(ref, x) for x in v]
                    else:
                        out[k] = walk(v, ref)
                return out
            if isinstance(o, list):
                return [walk(x, ctx) for x in o]
            return o

        out = walk(obj)
        if not seen:
            return out
        rx = re.compile("|".join(re.escape(s) for s in sorted(seen, key=len, reverse=True)))

        def strings(o: Any) -> Any:
            if isinstance(o, str):
                return rx.sub(lambda m: seen[m.group(0)], o)
            if isinstance(o, dict):
                return {k: strings(v) for k, v in o.items()}
            if isinstance(o, list):
                return [strings(x) for x in o]
            return o

        return strings(out)


# -- storage ---------------------------------------------------------------------------------------------------------

def _db():
    from .. import frames

    return frames._db()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def root_and_team(con: Any, version_id: str) -> tuple[str, str]:
    from ..catalog.schema import DEFAULT_TEAM

    r = con.execute("SELECT COALESCE(d.root_id, d.id), COALESCE(d.team_id, p.team_id) FROM datasets d"
                    " LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?", (version_id,)).fetchone()
    if not r:
        raise MaskError(404, "not_found", f"No dataset with id '{version_id}'.")
    return r[0], r[1] or DEFAULT_TEAM


def team_salt(con: Any, team_id: str) -> str:
    """The team's hash salt (catalog_meta `pii_salt:<team>`), made on first use. Stable, so a hash joins across a
    team's tables; different per team, so hashes do not join across teams."""
    key = f"pii_salt:{team_id}"
    r = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (key,)).fetchone()
    if r and r[0]:
        return r[0]
    con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (key, secrets.token_hex(16)))
    return con.execute("SELECT value FROM catalog_meta WHERE key = ?", (key,)).fetchone()[0]


def tags(con: Any, root: str, table: str) -> dict[str, dict[str, Any]]:
    return {r[0]: {"column": r[0], "tag": r[1], "mask": r[2], "source": r[3], "set_by": r[4], "set_at": r[5]} for r in con.execute(
        "SELECT column_name, tag, mask, source, set_by, set_at FROM column_tags WHERE root_id = ? AND table_name = ?", (root, table))}


_seen: dict[tuple[str, str, str], bool] = {}
_seen_lock = threading.Lock()


def _table_cols(version_id: str, table: str | None) -> tuple[Any, str]:
    from . import pages as PG

    src = PG.source(version_id, table)
    return src, src.table


def ensure(version_id: str, table: str | None = None) -> tuple[str, str, str]:
    """Tag the columns of (version, table) that the root has no row for yet with the profiler's suggestions.
    (root_id, table name, team_id). Cheap after the first call for a table's content (cached by identity)."""
    from .. import profiling
    from . import duck
    from . import pages as PG

    try:
        src, tname = _table_cols(version_id, table)
    except Exception:  # noqa: BLE001 - F-X4c: a purged / deleted version's bytes are gone; its tags still mask
        with _db().connect() as con:
            r = con.execute("SELECT state, primary_table FROM datasets WHERE id = ?", (version_id,)).fetchone()
            if not r or r[0] != "deleted":
                raise
            root, team = root_and_team(con, version_id)
        return root, table or r[1], team
    with _db().connect() as con:
        root, team = root_and_team(con, version_id)
        k = (root, tname, src.identity)
        with _seen_lock:
            if _seen.get(k):
                return root, tname, team
        have = tags(con, root, tname)
    new = [(c, t) for c, t in src.columns if c not in have]
    sugg: list[tuple[str, dict[str, str]]] = []
    if new:
        named = {c: profiling.pii_suggestion(c) for c, _ in new}
        text_cols = [c for c, t in new if named[c] is None and PG.kind(t) == "text"]
        sample: dict[str, list[Any]] = {}
        if text_cols:
            at = duck.interactive().query(f"SELECT {', '.join(PG.q(c) for c in text_cols)} FROM {src.rel}"
                                          f" ORDER BY {PG.q(PG.ROW_ID)} LIMIT {SAMPLE}", fetch="arrow", bind=src.bind)
            sample = {c: at.column(c).to_pylist() for c in text_cols}
        for c, _t in new:
            s = named[c] or (profiling.pii_suggestion(c, sample[c]) if c in sample else None)
            if s:
                sugg.append((c, s))
    if sugg:
        from ..catalog.schema import audit

        with _db().connect() as con:
            n = 0
            for c, s in sugg:
                n += con.execute("INSERT INTO column_tags (root_id, table_name, column_name, tag, mask, source, set_by, set_at)"
                                 " VALUES (?, ?, ?, ?, ?, 'suggested', 'profiler', ?) ON CONFLICT DO NOTHING",
                                 (root, tname, c, s["tag"], s["mask"], _now())).rowcount
            if n:
                audit(con, "profiler", "pii_tags_suggested",
                      {"dataset_id": version_id, "root_id": root, "table": tname,
                       "columns": [{"column": c, "tag": s["tag"], "mask": s["mask"], "why": s["why"]} for c, s in sugg]})
    with _seen_lock:
        _seen[k] = True
        if len(_seen) > 4096:
            _seen.clear()
    return root, tname, team


def for_version(version_id: str, table: str | None = None) -> Mask:
    """The masked view of a version table (no unmasked read): every column whose tag's mask is not `none`."""
    root, tname, team = ensure(version_id, table)
    with _db().connect() as con:
        tg = tags(con, root, tname)
        salt = team_salt(con, team)
    cols = {c: t["mask"] for c, t in tg.items() if t["mask"] != "none"}
    return Mask(version_id, tname, root, team, salt, cols, tg)


def run_scrub(rec: dict[str, Any], out: Any, label_too: bool = True) -> Any:
    """A run page's result with the values of its input's PII columns masked (`Mask.scrub`), plus `masked_columns`.
    Run pages read the stored values through pandas (holdout rows, slices, Model Doctor); this is their net."""
    m = for_run(rec)
    if m is None or not m.cols:
        return out
    out = m.scrub(out)
    if isinstance(out, dict):
        out["masked_columns"] = m.masked
    return out


def pii_features(rec: dict[str, Any], features: list[str]) -> list[dict[str, Any]]:
    """C §4.3: the run's features that carry a PII tag (pii_direct / pii_quasi / sensitive) or are protected."""
    m = for_run(rec)
    if m is None:
        return []
    return [{"feature": f, "tag": m.tags[f]["tag"], "source": m.tags[f]["source"]} for f in features
            if f in m.tags and m.tags[f]["tag"] in ("pii_direct", "pii_quasi", "sensitive", "protected")]


def masked_view(version_id: str, table: str | None = None) -> Mask | None:
    """`for_version`, falling back to the root's stored tags when the table can't be scanned (a CSV-era file gone, a
    deleted version): a page that renders stored values (the profile) still masks them. None for an unknown id."""
    try:
        return for_version(version_id, table)
    except Exception:  # noqa: BLE001
        from .. import frames

        try:
            with _db().connect() as con:
                root, team = root_and_team(con, version_id)
                rows = con.execute("SELECT column_name, mask FROM column_tags WHERE root_id = ? AND mask <> 'none'"
                                   + (" AND table_name = ?" if table else ""), (root, table) if table else (root,)).fetchall()
                return Mask(version_id, table or frames.PRIMARY_TABLE, root, team, team_salt(con, team), {r[0]: r[1] for r in rows})
        except Exception:  # noqa: BLE001
            return None


def run_ref(cfg_or_rec: dict[str, Any]) -> tuple[str, str | None] | None:
    """(version id, table or None for the primary) of a run's input, or None when it is not a catalog version (an old
    run on a path, a join frame)."""
    cfg = cfg_or_rec.get("config") if isinstance(cfg_or_rec.get("config"), dict) else cfg_or_rec
    from .. import frames

    try:
        ref = frames.resolve(cfg)
    except Exception:  # noqa: BLE001
        ref = None
    vid = ref.version_id if isinstance(ref, frames.Table) else cfg.get("dataset_id") or cfg_or_rec.get("dataset_id")
    if not vid:
        return None
    return str(vid), ref.table if isinstance(ref, frames.Table) and ref.table != frames.PRIMARY_TABLE else None


def for_run(cfg_or_rec: dict[str, Any]) -> Mask | None:
    """The masked view of a run's input table, for run pages (holdout rows, slices, SHAP row, Model Doctor). None when
    the run's input is not a catalog version (an old run on a path, a join frame)."""
    ref = run_ref(cfg_or_rec)
    return None if ref is None else masked_view(*ref)


def for_run_request(request: Any, cfg_or_rec: dict[str, Any], *, unmasked: Any = False, reason: str | None = None,
                    by: str | None = None, read: str = "run_rows") -> Mask | None:
    """F-PII: `for_request` for a page of a run's rows (anomaly top rows, shift rows, the active-learning queue,
    annotations): masked by default; `unmasked=1&reason=…` is decided as data.read_unmasked on the run's input version
    and audited as dataset_rows_read (`read` names the page). None when the input is not a catalog version."""
    ref = run_ref(cfg_or_rec)
    if ref is None:
        return None
    if str(unmasked).lower() in TRUE:
        return for_request(request, ref[0], ref[1], unmasked=unmasked, reason=reason, by=by, read=read)
    return masked_view(*ref)


def for_version_request(request: Any, version_id: str, table: str | None = None, *, unmasked: Any = False,
                        reason: str | None = None, by: str | None = None, read: str = "rows") -> Mask | None:
    """F-PII: `for_request` for pages that render values already computed (split options, intake, Feature Lab): the
    masked view (falling back to the stored tags) unless an unmasked read is asked for and allowed."""
    if str(unmasked).lower() in TRUE:
        return for_request(request, version_id, table, unmasked=unmasked, reason=reason, by=by, read=read)
    return masked_view(version_id, table)


def raw_values(m: Mask | None, version_id: str, rows: list[int], table: str | None = None, limit: int = 2000, *,
               by_row_id: bool = False) -> dict[str, list[Any]]:
    """F-PII: the stored values of `m`'s masked columns at `rows` (read positions; `__row_id`s with `by_row_id`, as the
    active-learning queue names them), so `Mask.scrub` can also replace them inside free text (an item's joined text).
    Empty when nothing is masked or the version can't be read."""
    if m is None or not m.cols or not rows:
        return {}
    from .. import frames

    try:
        want = sorted({int(r) for r in rows if r is not None and int(r) >= 0})[:limit]
        df = frames.read(frames.Table(version_id, table or frames.PRIMARY_TABLE), list(m.cols))
        if by_row_id:
            df = df.loc[df.index.intersection(want)]
        else:
            df = df.reset_index(drop=True)
            df = df.iloc[[r for r in want if r < len(df)]]
        return {c: [v for v in df[c].tolist() if not _missing(v)] for c in m.cols if c in df.columns}
    except Exception:  # noqa: BLE001 - a net
        return {}


def audit_text(con: Any, team_id: str | None, text: Any) -> dict[str, Any] | None:
    """F-PII: search text kept in an audit row (dataset_read `q`): never the text itself, which may be a person's name or
    an SSN, but its salted hash (the team's salt, as the `hash` mask: an investigator who knows a value can check it) and
    its length."""
    t = str(text or "")
    if not t.strip():
        return None
    from ..catalog.schema import DEFAULT_TEAM

    return {"hash": mask_value(t, "hash", team_salt(con, team_id or DEFAULT_TEAM)), "len": len(t), "redacted": True}


def _reason_ok(reason: str | None) -> str:
    r = str(reason or "").strip()
    if not 2 <= len(r) <= 500:
        raise MaskError(400, "reason_required", "An unmasked read needs a reason (2–500 characters, e.g. QA or a ticket): send reason=… "
                                                "(it is kept in the audit log with the columns you saw).")
    return r


def for_request(request: Any, version_id: str, table: str | None = None, *, unmasked: Any = False, reason: str | None = None,
                by: str | None = None, read: str = "rows", export: bool = False) -> Mask:
    """The mask for this request. `unmasked` asks for the PII columns in clear: a reason is required, the decision is
    `data.read_unmasked` (or `data.export_unmasked`), and the read is audited as `dataset_rows_read`. Without it (or
    when it is not allowed) the masked view."""
    m = for_version(version_id, table)
    if str(unmasked).lower() not in TRUE:
        return m
    why = _reason_ok(reason)
    if not m.cols:  # nothing to unmask: the plain view, no unmasked read happened
        m.unmasked = True
        return m
    from ..authz import deps
    from ..authz.context import current

    action = "data.export_unmasked" if export else "data.read_unmasked"
    mode = deps.mode()
    if mode != "off":
        o = deps.path("dataset_id", "dataset")
        decisions = deps._decide(request, action, (o,), (), {str(o): version_id}, by, mode)
        ctx = current()
        if ctx is not None:
            ctx.decisions.extend(decisions)
        denied = [d for d in decisions if not d["allow"]]
        if denied:
            d = denied[0]
            if mode == "enforce":
                p = ctx.principal if ctx is not None else None
                if p is None:
                    raise MaskError(401, "unauthenticated", d["reason"], action=action)
                raise MaskError(403, "forbidden", d["reason"], action=action, team=d["team"], needed=d["needed"])
            m.refused = {"action": action, "needed": d["needed"], "role": d.get("role"),
                         "message": f"{d['reason']}: the PII columns stay masked (audit mode records this as would-deny)."}
            return m
    from ..catalog import governance as G
    from ..catalog.schema import audit

    who = G.reader(by)
    cols = m.masked
    with _db().connect() as con:
        audit(con, who, "dataset_rows_read", {"dataset_id": version_id, "root_id": m.root_id, "table": m.table, "read": read,
                                              "unmasked": True, "columns": cols, "reason": why, "action": action},
              comment=why[:500])
    m.cols, m.unmasked, m.reason = {}, True, why
    m.tags = {c: {**t, "shown_unmasked": True} if c in cols else t for c, t in m.tags.items()}
    m.unmasked_columns = cols  # type: ignore[attr-defined]
    return m


def set_tags(version_id: str, table: str | None, items: Any, by: str) -> dict[str, Any]:
    """Confirm or change tags: items = [{column, tag, mask?}] (mask defaults by tag), or {"confirm": "suggested"} to
    confirm every suggested tag as it stands. Writes source='confirmed', audited as pii_tags_set."""
    from ..catalog.schema import audit

    root, tname, _team = ensure(version_id, table)
    src, _ = _table_cols(version_id, tname)
    names = set(src.names)
    with _db().connect() as con:
        have = tags(con, root, tname)
        todo: list[dict[str, Any]] = []
        if isinstance(items, dict) and items.get("confirm") == "suggested":
            todo = [{"column": c, "tag": t["tag"], "mask": t["mask"]} for c, t in have.items() if t["source"] == "suggested"]
        elif isinstance(items, list) and items:
            for i, it in enumerate(items):
                if not isinstance(it, dict) or not isinstance(it.get("column"), str):
                    raise MaskError(400, "tags_invalid", f"Item {i}: send {{column, tag, mask?}}.")
                c, tag = it["column"], it.get("tag")
                if c not in names and c not in have:
                    raise MaskError(400, "column_not_found", f"Item {i}: no column {c!r} in {version_id} ({tname}).")
                if tag not in TAGS:
                    raise MaskError(400, "tags_invalid", f"Item {i}: tag is one of {', '.join(TAGS)}.")
                mk = it.get("mask") or DEFAULT_MASK[tag]
                if mk not in MASKS:
                    raise MaskError(400, "tags_invalid", f"Item {i}: mask is one of {', '.join(MASKS)}.")
                todo.append({"column": c, "tag": tag, "mask": mk})
        else:
            raise MaskError(400, "tags_invalid", "Send columns: [{column, tag, mask?}] or confirm: \"suggested\".")
        changes = []
        for t in todo:
            old = have.get(t["column"])
            con.execute("INSERT INTO column_tags (root_id, table_name, column_name, tag, mask, source, set_by, set_at)"
                        " VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?) ON CONFLICT (root_id, table_name, column_name) DO UPDATE"
                        " SET tag = excluded.tag, mask = excluded.mask, source = 'confirmed', set_by = excluded.set_by, set_at = excluded.set_at",
                        (root, tname, t["column"], t["tag"], t["mask"], by, _now()))
            changes.append({**t, "was": {k: old[k] for k in ("tag", "mask", "source")} if old else None})
        audit(con, by, "pii_tags_set", {"dataset_id": version_id, "root_id": root, "table": tname, "changes": changes})
        out = tags(con, root, tname)
    return {"dataset_id": version_id, "root_id": root, "table": tname, "changed": changes, "tags": list(out.values())}


def describe(version_id: str, table: str | None = None) -> dict[str, Any]:
    """GET /datasets/{id}/pii: the root's tags for the table (suggested ones first), which columns are masked."""
    m = for_version(version_id, table)
    rows = sorted(m.tags.values(), key=lambda t: (t["source"] != "suggested", t["column"]))
    return {"dataset_id": version_id, "root_id": m.root_id, "table": m.table, "tags": rows, "masked_columns": m.masked,
            "pending_review": [t["column"] for t in rows if t["source"] == "suggested"],
            "note": "suggested tags mask exactly like confirmed ones until a maintainer reviews them (PUT …/pii)"}


# -- error messages: never echo a tagged column's value ----------------------------------------------------------------

def tagged_values(m: Mask, ops: Any) -> dict[str, list[Any]]:
    """The values an edit sends for masked columns (set_cells, where / filter, replace_values, fill_missing, add_rows),
    so an error message can be scrubbed of them."""
    out: dict[str, list[Any]] = {}
    if not m.cols:
        return out

    def add(c: Any, v: Any) -> None:
        if isinstance(c, str) and c in m.cols:
            for x in (v if isinstance(v, list) else [v]):
                if not isinstance(x, (dict, list)):
                    out.setdefault(c, []).append(x)

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            c = o.get("column")
            for k in ("value", "values", "to", "from", "replace", "with", "fill"):
                if k in o:
                    add(c, o[k])
            for k, v in o.items():
                if k in m.cols and not isinstance(v, (dict, list)):
                    add(k, v)
                if isinstance(v, (dict, list)):
                    walk(v)
            mp = o.get("mapping") or o.get("map")
            if isinstance(mp, dict):
                for a, b in mp.items():
                    add(c, a)
                    add(c, b)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(ops)
    return out


def scrub_error(detail: Any, m: Mask, values: dict[str, list[Any]]) -> Any:
    """An error body with the values of masked columns taken out of its message (C §4.3: validation errors give the
    column and type, never the value, when the column is tagged)."""
    if not values:
        return detail
    lits: set[str] = set()
    for vs in values.values():
        for v in vs:
            if _missing(v):
                continue
            s = _text(v)
            lits |= {s, repr(v), repr(s), json.dumps(v, default=str), repr(s[:40]), s[:40], s[:37]}
    lits = {x for x in lits if len(x) >= 2}
    if not lits:
        return detail
    rx = re.compile("|".join(re.escape(s) for s in sorted(lits, key=len, reverse=True)))

    def walk(o: Any) -> Any:
        if isinstance(o, str):
            return rx.sub("the value (not shown: the column is PII-tagged)", o)
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items() if k not in ("value", "values")}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    out = walk(detail)
    if isinstance(out, dict):
        out["masked_columns"] = sorted(values)
    return out


# -- rendering stored statistics ---------------------------------------------------------------------------------------

_HIDDEN_NUMBER_STATS = ("min", "max", "mean", "std", "quantiles", "median", "histogram")


def _num(v: Any) -> Any:
    try:
        f = float(v)
        return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f
    except (TypeError, ValueError):
        return v


def stats(body: dict[str, Any], m: Mask | None) -> dict[str, Any]:
    """GET …/stats (table_stats) as rendered for a reader (§6.3): for a masked column the top-20 values are masked
    (equal masks merged, so `•••` is one row), the histogram and min / max / mean / std / quantiles are not shown, and
    a date's min / max are masked. Counts (n, nulls, distinct, missing) stay."""
    if m is None or not m.cols or not isinstance(body.get("columns"), list):
        return body
    cols = []
    for c in body["columns"]:
        k = m.cols.get(c.get("name"))
        if k is None:
            cols.append(c)
            continue
        c = dict(c, masked=True, mask=k)
        if c.get("top"):
            merged: dict[Any, int] = {}
            for t in c["top"]:
                v = _num(t["value"]) if c.get("kind") in ("integer", "number") else t["value"]
                mv = mask_value(v, k, m.salt)
                merged[mv] = merged.get(mv, 0) + int(t.get("n") or 0)
            c["top"] = [{"value": v, "n": n} for v, n in sorted(merged.items(), key=lambda x: -x[1])]
        if c.get("kind") in ("integer", "number"):
            for f in _HIDDEN_NUMBER_STATS:
                if f in c:
                    c[f] = None
        else:
            c.pop("histogram", None)
            for f in ("min", "max"):
                if c.get(f) is not None:
                    c[f] = mask_value(c[f], k, m.salt)
        cols.append(c)
    return {**body, "columns": cols, "masked_columns": m.masked}


def run_page(request: Any, rec: dict[str, Any], out: Any, *, by: str = "", unmasked: Any = "", reason: str = "",
             read: str = "run_page") -> Any:
    """F-X4a (X4 C2): a run or model-version page that quotes values of the run's input columns (fairness groups,
    scorecard cuts, recourse ranges, shift options, the credit report, the model card): the run input's PII-tagged
    columns masked through `Mask.scrub`, plus `masked_columns` / `masks` / `unmasked`. `unmasked=1&reason=…` shows them
    as on the other run pages (data.read_unmasked on the input version, audited as dataset_rows_read; 400 without a
    reason, 403 in enforce when not allowed). Unchanged when the input is not a catalog version."""
    from fastapi import HTTPException

    from ..catalog import governance as G

    try:
        who = G.reader(by) if str(unmasked).lower() in TRUE else (by or None)
        m = for_run_request(request, rec, unmasked=unmasked, reason=reason, by=who, read=read)
    except MaskError as e:
        raise HTTPException(e.status, e.payload()) from e
    if m is None:
        return out
    if m.cols:
        out = m.scrub(out)
    if isinstance(out, dict):
        out.update(m.info())
    return out


def group_labels(m_out: dict[str, Any], group: str) -> dict[str, Any]:
    """F-X4a: a fairness report on a masked group column: its group labels are its values (or quartile edges of them),
    so each is replaced by `••• 1`, `••• 2`, … (distinct, in the report's order; coverage keys follow)."""
    if not isinstance(m_out, dict):
        return m_out
    masked = set(m_out.get("masked_columns") or [])
    if group not in masked:
        return m_out
    rows = m_out.get("by_group") or []
    names = {str(r.get("group")): f"••• {i + 1}" for i, r in enumerate(rows) if isinstance(r, dict)}
    m_out["by_group"] = [{**r, "group": names.get(str(r.get("group")), "•••")} if isinstance(r, dict) else r for r in rows]
    for k in ("coverage", "mean_width"):
        if isinstance(m_out.get(k), dict):
            m_out[k] = {names.get(str(a), "•••"): b for a, b in m_out[k].items()}
    m_out["groups_masked"] = True
    return m_out


def _join_view(cfg: dict[str, Any]) -> Mask | None:
    """F-X4a: a run on a join frame: the union of its pinned inputs' masked columns (by column name; the frame and a
    join model's batch files carry the inputs' column names)."""
    ins = ((cfg.get("join") or {}).get("inputs") or []) if isinstance(cfg, dict) else []
    out: Mask | None = None
    for x in ins:
        m = masked_view(x.get("version_id"), x.get("table")) if x.get("version_id") else None
        if m is None:
            continue
        if out is None:
            out = m
        else:
            out.cols = {**m.cols, **out.cols}
            out.tags = {**m.tags, **out.tags}
    return out


def batch_mask(request: Any, rec: dict[str, Any], *, unmasked: Any = "", reason: str = "", by: str = "",
               export: bool = False, read: str = "batch") -> Mask | None:
    """F-X4a (X4 M3): the mask for a batch of a model (its input preview, its scored file): the PII-tagged columns of the
    run's input (a join run: of its pinned inputs). Masked by default; `unmasked=1&reason=…` is data.read_unmasked, or
    data.export_unmasked for a download (`export`), on the run's input version, audited as dataset_rows_read."""
    from fastapi import HTTPException

    from ..catalog import governance as G

    cfg = rec.get("config") if isinstance(rec.get("config"), dict) else rec
    ref = run_ref(rec) if not (cfg or {}).get("join") else None
    try:
        if str(unmasked).lower() in TRUE:
            vid, table = ref if ref else (((cfg.get("join") or {}).get("inputs") or [{}])[0].get("version_id"), None)
            if not vid:
                return None
            if ref is None:  # a join: decide on the grain input; every input's tagged columns are shown
                _reason_ok(reason)
                m = _join_view(cfg)
                if m is None or not m.cols:
                    return m
                for_request(request, m.version_id, m.table, unmasked=unmasked, reason=reason, by=G.reader(by), read=read, export=export)
                cols = m.masked
                m.cols, m.unmasked, m.reason = {}, True, str(reason).strip()
                m.unmasked_columns = cols  # type: ignore[attr-defined]
                return m
            return for_request(request, vid, table, unmasked=unmasked, reason=reason, by=G.reader(by), read=read, export=export)
    except MaskError as e:
        raise HTTPException(e.status, e.payload()) from e
    return masked_view(*ref) if ref else _join_view(cfg or {})


def csv_stream(path: Any, m: Mask | None, chunk_rows: int = 100_000):
    """F-X4a: a CSV file with the masked columns of `m` masked, streamed in chunks (every cell read as text, so values
    and blanks stay as written). The file as is when nothing is masked."""
    import pandas as pd

    first = True
    for df in pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=chunk_rows):
        if m is not None and m.cols:
            for c in df.columns:
                if c in m.cols:
                    df[c] = [v if v == "" else mask_value(v, m.cols[c], m.salt) for v in df[c]]
        yield df.to_csv(index=False, header=first)
        first = False
