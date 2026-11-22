"""P14 (§5.8, §2): saved joins, join frames and what a run on a join records.

A **saved join** (`joins`) is a spec on a dataset root, versioned by name (`spec_version` 1, 2, …; the same spec saved
again returns the existing row). The spec is `ensemble.join/1`:

    {"format": "ensemble.join/1", "grain": "applications",
     "joins": [{"type": "n:1", "table": "bureau", "on": [["customer_id", "customer_id"]], "columns": [...], "prefix": "bureau__"},
               {"type": "aggregate", "table": "payments", "on": [["app_id", "app_id"]],
                "aggs": [{"name": "n_pay", "fn": "count"},
                         {"name": "late_90d", "fn": "sum", "column": "days_late",
                          "window": {"date": "paid_on", "relative_to": "applied_on", "days": -90}}]}],
     "where": null}

A table is a name in the version the join is applied to, or `{root_id?, version_id, table}`: another dataset, pinned
(a `merged_from` edge). There is no free SQL. `1:n` without an aggregate is refused (400), and so is an `n:1` whose
right side is not a key (declared in `table_keys`, else checked now).

A **join frame** (`join_frames`) is a saved join applied to one version: its inputs pinned by `table_sha256`, its
identity `frame_sha256 = sha256(canonical({spec_sha256, input table shas}))`, its rows exactly the grain's (asserted),
and its bytes one Parquet cache blob (kind `join_cache`, ref `join_frame`/`cache`). `frames.materialise` writes it.

A **run on a join** reads the frame (`input_ref {kind: join_frame}`); its train row in `run_inputs` names the frame and
records the cache file's sha256 as `table_sha256` / `read_sha256`; one `join_input` row per input table pins
`version_id, table_name, table_sha256`. Lineage: `dataset (input version) -joined_from-> join_frame` (`merged_from` for
another root's version) and `join_frame -trained_on-> run`, so usage and the delete / archive blockers of every input
version see the run.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

from .. import frames
from ..lake import manifest as M
from . import schema as S

FORMAT = "ensemble.join/1"
TYPES = ("n:1", "1:1", "aggregate")
AGG_FNS = ("count", "count_distinct", "sum", "mean", "min", "max")
NUMERIC_FNS = ("sum", "mean")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
MAX_JOINS = 20
MAX_AGGS = 50


class JoinError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.payload = {"code": code, "message": message, **extra}


def _bad(code: str, message: str, **extra: Any) -> JoinError:
    return JoinError(400, code, message, **extra)


# --------------------------------------------------------------------------- the spec


def _str(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise _bad("bad_spec", f"{what} is a non-empty string.")
    return v.strip()


def _on(v: Any, i: int) -> list[list[str]]:
    """`on`: [["left", "right"], …], or ["col", …] (the same name on both sides)."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v:
        raise _bad("bad_spec", f"joins[{i}].on is a list of [grain column, table column] pairs (or column names).")
    out = []
    for p in v:
        if isinstance(p, str):
            p = [p, p]
        if not (isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) and x for x in p)):
            raise _bad("bad_spec", f"joins[{i}].on: each entry is [grain column, table column] or a column name.")
        out.append([p[0], p[1]])
    return out


def _table_ref(v: Any, i: int) -> str | dict[str, str]:
    if isinstance(v, str) and v:
        return v
    if isinstance(v, dict) and isinstance(v.get("version_id"), str) and isinstance(v.get("table"), str):
        out = {"version_id": v["version_id"], "table": v["table"]}
        if v.get("root_id"):
            out["root_id"] = _str(v["root_id"], f"joins[{i}].table.root_id")
        return out
    raise _bad("bad_spec", f"joins[{i}].table is a table name of the version, or {{root_id, version_id, table}} "
                           "(another dataset's table, pinned to a version).")


def normalise(spec: Any) -> dict[str, Any]:
    """The canonical form of a spec (defaults filled, shapes checked): what is stored and hashed. 400 bad_spec."""
    if not isinstance(spec, dict):
        raise _bad("bad_spec", "spec is an object: {format, grain, joins, where}.")
    unknown = set(spec) - {"format", "grain", "joins", "where"}
    if unknown:
        raise _bad("bad_spec", f"spec has unknown keys: {', '.join(sorted(unknown))}.")
    if spec.get("format", FORMAT) != FORMAT:
        raise _bad("bad_spec", f"spec.format is {FORMAT!r}.")
    if spec.get("where") not in (None, {}, []):
        raise _bad("where_unsupported", "spec.where is not supported yet: a join frame has every grain row (filter the "
                                        "grain table with an edit, which makes a version, and join that).")
    grain = _str(spec.get("grain"), "spec.grain")
    js = spec.get("joins")
    if not isinstance(js, list) or not js:
        raise _bad("bad_spec", "spec.joins is a non-empty list.")
    if len(js) > MAX_JOINS:
        raise _bad("bad_spec", f"At most {MAX_JOINS} joins.")
    out = []
    for i, j in enumerate(js):
        if not isinstance(j, dict):
            raise _bad("bad_spec", f"joins[{i}] is an object.")
        t = j.get("type")
        if t in ("1:n", "n:n", "m:n"):
            raise _bad("one_to_many", f"joins[{i}] is {t}: a grain row would repeat. Use type 'aggregate' with aggs "
                                      "(count, sum, mean, min, max, count_distinct; optionally over a date window).",
                       join=i)
        if t not in TYPES:
            raise _bad("bad_spec", f"joins[{i}].type is one of {', '.join(TYPES)} (1:n needs an aggregate).")
        tbl = _table_ref(j.get("table"), i)
        tname = tbl if isinstance(tbl, str) else tbl["table"]
        d: dict[str, Any] = {"type": t, "table": tbl, "on": _on(j.get("on"), i)}
        if t == "aggregate":
            aggs = j.get("aggs")
            if not isinstance(aggs, list) or not aggs:
                raise _bad("aggregate_needs_aggs", f"joins[{i}] (aggregate of {tname}) needs aggs: "
                                                   '[{"name": "n", "fn": "count"}, …].', join=i)
            if len(aggs) > MAX_AGGS:
                raise _bad("bad_spec", f"joins[{i}]: at most {MAX_AGGS} aggs.")
            na = []
            for k, a in enumerate(aggs):
                if not isinstance(a, dict):
                    raise _bad("bad_spec", f"joins[{i}].aggs[{k}] is an object.")
                fn = a.get("fn")
                if fn not in AGG_FNS:
                    raise _bad("bad_agg", f"joins[{i}].aggs[{k}].fn is one of {', '.join(AGG_FNS)}.")
                x: dict[str, Any] = {"name": _str(a.get("name"), f"joins[{i}].aggs[{k}].name"), "fn": fn,
                                     "column": a.get("column") or None}
                if x["column"] is not None and not isinstance(x["column"], str):
                    raise _bad("bad_agg", f"joins[{i}].aggs[{k}].column is a column name.")
                if fn != "count" and not x["column"]:
                    raise _bad("bad_agg", f"joins[{i}].aggs[{k}] ({fn}) needs a column.")
                w = a.get("window")
                if w is not None:
                    if not (isinstance(w, dict) and isinstance(w.get("date"), str) and isinstance(w.get("relative_to"), str)
                            and isinstance(w.get("days"), int) and not isinstance(w.get("days"), bool) and w["days"] != 0
                            and abs(w["days"]) <= 36600):
                        raise _bad("bad_agg", f"joins[{i}].aggs[{k}].window is {{date: <table column>, relative_to: <grain "
                                              "column>, days: <non-zero integer; negative looks back>}.")
                    w = {"date": w["date"], "relative_to": w["relative_to"], "days": int(w["days"])}
                x["window"] = w
                na.append(x)
            d["aggs"] = na
            d["prefix"] = str(j.get("prefix") or "")
        else:
            cols = j.get("columns")
            if cols is not None and not (isinstance(cols, list) and all(isinstance(c, str) and c for c in cols)):
                raise _bad("bad_spec", f"joins[{i}].columns is a list of column names (or omitted: every column).")
            d["columns"] = list(dict.fromkeys(cols)) if cols else None
            d["prefix"] = str(j.get("prefix") if j.get("prefix") is not None else f"{tname}__")
        out.append(d)
    return {"format": FORMAT, "grain": grain, "joins": out, "where": None}


def spec_sha256(spec: dict[str, Any]) -> str:
    return M.sha256_hex(M.canonical(spec))


# --------------------------------------------------------------------------- resolving inputs against a version


def _version(con, version_id: str) -> dict[str, Any]:
    r = con.execute("SELECT id, COALESCE(root_id, id) AS root_id, storage, state, team_id, project_id FROM datasets WHERE id = ?",
                    (version_id,)).fetchone()
    if not r:
        raise JoinError(404, "not_found", f"No dataset version {version_id}.")
    return dict(r)


def _shares_of_inputs(con, version_id: str, vids: list[str]) -> tuple[str | None, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """F-PII (P14 → P21): the join's team (the grain version's) and, for every pinned input of another team, the live
    share to that team covering the version: {vid: {share_id, pii, owner_team, scope}}; the inputs with none."""
    from . import shares as SH

    g = SH.version_info(con, version_id)
    team = g.get("team") if g else None
    got: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []
    if not team:
        return team, got, missing
    for vid in dict.fromkeys(vids):
        v = SH.version_info(con, vid)
        if v is None or not v.get("team") or v["team"] == team:
            continue
        s = SH.grant_for_version(con, {team}, vid)
        if s is None:
            missing.append({"version_id": vid, "owner_team": v["team"]})
            continue
        su = SH.grant_for_version(con, {team}, vid, unmasked=True)
        s = su or s
        got[vid] = {"share_id": s["id"], "pii": "unmasked" if su else "masked", "owner_team": v["team"], "scope": s["scope"]}
    return team, got, missing


def _refuse(db, by: str | None, code: str, version_id: str, details: dict[str, Any]) -> None:
    """F-PII: a refused cross-team input is audited in the joining team's chain (`join_input_refused`; `version_id` is
    the grain version the join was applied to)."""
    from . import shares as SH

    with db.connect() as con:
        team = (SH.version_info(con, version_id) or {}).get("team")
        S.audit(con, by, "join_input_refused", details={"code": code, "version_id": version_id, **details},
                object_kind="dataset", object_id=version_id, **({"team_id": team} if team else {}))


def inputs_for(db, spec: dict[str, Any], version_id: str, by: str | None = None) -> list[dict[str, Any]]:
    """[{version_id, root_id, table, table_sha256, lt, role, share?}] in spec order (the grain first). 400 when a table or
    a pinned version is unknown, 409 when a version is deleted or still CSV-era. F-PII (P14 → P21): a pinned input of
    another team needs a live share to the joining team that covers that version (`visible_teams`), else 403
    `input_not_shared` (in every authz mode: a join reads the other team's rows into a frame of this team); the share
    is named on the input (`share {share_id, pii, owner_team, scope}`)."""
    with db.connect() as con:
        v = _version(con, version_id)
        refs: list[tuple[str, str, str]] = [("grain", version_id, spec["grain"])]
        for i, j in enumerate(spec["joins"]):
            t = j["table"]
            refs.append((f"join:{i}", version_id, t) if isinstance(t, str) else (f"join:{i}", t["version_id"], t["table"]))
        rows: dict[str, dict[str, Any]] = {version_id: v}
        for _, vid, _t in refs:
            if vid not in rows:
                rows[vid] = _version(con, vid)
        for i, j in enumerate(spec["joins"]):
            t = j["table"]
            if isinstance(t, dict) and t.get("root_id") and rows[t["version_id"]]["root_id"] != t["root_id"]:
                raise _bad("version_not_in_root", f"joins[{i}]: {t['version_id']} is not a version of {t['root_id']}.")
        team, shared, missing = _shares_of_inputs(con, version_id, [vid for _, vid, _t in refs[1:] if rows[vid]["state"] != "deleted"])
    if missing:
        x = missing[0]
        _refuse(db, by, "input_not_shared", version_id, {"inputs": missing})
        raise JoinError(403, "input_not_shared", f"{x['version_id']} belongs to team {x['owner_team']} and no live share to "
                                                 f"{team} covers it: ask {x['owner_team']} to share it (POST /shares) before joining it.",
                        version_id=x["version_id"], owner_team=x["owner_team"], team=team, inputs=missing)
    out = []
    for role, vid, tname in refs:
        r = rows[vid]
        if r["state"] == "deleted":
            raise JoinError(409, "version_deleted", f"{vid} was deleted: the join can't read its table {tname!r}.")
        if r["storage"] != "parquet":
            raise JoinError(409, "parent_not_parquet", f"{vid} is a CSV-era version: migrate it to Parquet before joining it.")
        try:
            lt = frames.lake_table(frames.Table(vid, tname))
        except frames.FrameError:
            lt = None
        if lt is None or lt.name != tname:
            with db.connect() as con:
                names = [x[0] for x in con.execute("SELECT table_name FROM version_tables WHERE version_id = ? ORDER BY ordinal", (vid,))]
            raise _bad("table_unknown", f"{vid} has no table {tname!r} (its tables: {', '.join(names) or 'none'}).",
                       table=tname, version_id=vid)
        out.append({"role": role, "version_id": vid, "root_id": r["root_id"], "table": tname,
                    "table_sha256": lt.table_sha256, "n_rows": lt.n_rows, "lt": lt,
                    **({"share": shared[vid]} if vid in shared else {})})
    return out


def _used_columns(j: dict[str, Any], jcols: list[str]) -> set[str]:
    """The columns of a joined table a join reads: its keys, its output columns (every column when none are listed),
    its aggregates' columns and window dates."""
    used = {r for _, r in j["on"]}
    if j["type"] == "aggregate":
        for a in j["aggs"]:
            used |= {a["column"]} if a["column"] else set()
            used |= {a["window"]["date"]} if a.get("window") else set()
    else:
        used |= set(j.get("columns") or jcols)
    return used


def pii_check(db, spec: dict[str, Any], ins: list[dict[str, Any]], version_id: str, by: str | None = None) -> list[dict[str, Any]]:
    """F-PII (P14 → P21): a joined table shared to this team with pii='masked' may not bring its PII-tagged columns
    (mask ≠ none; suggested tags count) into the frame, nor be joined on one (a match count would tell their values):
    409 `pii_masked_share` naming the columns. An unmasked share (granted with grant_unmasked) allows them. The frame is
    never written masked: a frame holds stored values, like any version. Returns what was checked, per shared input."""
    from ..lake import mask as MK

    out: list[dict[str, Any]] = []
    for i, j in enumerate(spec["joins"]):
        inp = ins[i + 1]
        sh = inp.get("share")
        if not sh:
            continue
        cols = [c["name"] for c in inp["lt"].schema if c["name"] != "__row_id"]
        tagged = MK.masked_view(inp["version_id"], inp["table"])
        pii = sorted(set(tagged.cols if tagged is not None else {}) & _used_columns(j, cols))
        ent = {"join": i, "version_id": inp["version_id"], "table": inp["table"], **sh, "pii_columns": pii}
        if pii and sh["pii"] != "unmasked":
            _refuse(db, by, "pii_masked_share", version_id, {"inputs": [ent]})
            raise JoinError(409, "pii_masked_share",
                            f"joins[{i}] reads PII-tagged column(s) {', '.join(pii)} of {inp['version_id']} ({inp['table']}), "
                            f"which is shared to this team masked ({sh['share_id']}): leave them out of `on` / `columns` / "
                            "`aggs` (list the columns you need), or ask the owner for a share with pii unmasked.",
                            join=i, columns=pii, share_id=sh["share_id"], version_id=inp["version_id"])
        out.append(ent)
    return out


def _declared_key(db, version_id: str, table: str, cols: list[str]) -> bool:
    with db.connect() as con:
        for r in con.execute("SELECT columns_json FROM table_keys WHERE version_id = ? AND table_name = ? AND valid = 1",
                             (version_id, table)):
            if sorted(json.loads(r[0])) == sorted(cols):
                return True
    return False


def _window_text_check(con, j: dict[str, Any], i: int, tname: str, jsrc: str, jcols: dict[str, str], gsrc: str,
                       gcols: dict[str, str]) -> None:
    """F-X3b (M5): a window column stored as text must hold ISO dates: every non-empty value reads as a DATE (as the
    window's TRY_CAST reads it), and at least one does. 'dd/mm/yyyy' text or serials as text → 400 `window_type`."""
    q = frames._qi
    seen: set[tuple[str, str]] = set()
    for k, a in enumerate(j["aggs"]):
        w = a.get("window")
        if not w:
            continue
        for side, col, typ, src, who in (("date", w["date"], jcols[w["date"]], jsrc, tname), ("relative_to", w["relative_to"],
                                         gcols[w["relative_to"]], gsrc, "the grain")):
            if frames.key_family(typ) != "text" or (src, col) in seen:
                continue
            seen.add((src, col))
            n, bad, ex = con.execute(
                f"SELECT count({q(col)}), count(*) FILTER (WHERE {q(col)} IS NOT NULL AND TRY_CAST({q(col)} AS DATE) IS NULL),"
                f" min({q(col)}) FILTER (WHERE {q(col)} IS NOT NULL AND TRY_CAST({q(col)} AS DATE) IS NULL) FROM {src}").fetchall()[0]
            if int(bad) or not int(n):
                raise _bad("window_type",
                           f"joins[{i}] ({tname}).aggs[{k}].window.{side}: {col} of {who} is text and "
                           + (f"{int(bad):,} of {int(n):,} values don't read as an ISO date (e.g. {ex!r}): " if int(n) else "is empty: ")
                           + "no row would fall in the window. Re-type it as a DATE (a new version) and join that.",
                           join=i, agg=k, column=col, type=typ, not_dates=int(bad), values=int(n), example=ex)


def validate(db, spec: dict[str, Any], version_id: str, by: str | None = None) -> dict[str, Any]:
    """§5.8: the spec against a concrete version. Columns exist and compare; an n:1 / 1:1 right side is a key (declared,
    else counted now); 1:n without an aggregate → 400; output names don't collide. Returns the inputs and the coverage of
    every joined table (grain rows with at least one match)."""
    from ..lake import duck

    ins = inputs_for(db, spec, version_id, by)
    g = ins[0]["lt"]
    gcols = {c["name"]: c["type"] for c in g.schema}
    q = frames._qi
    report: list[dict[str, Any]] = []
    for i, j in enumerate(spec["joins"]):
        inp = ins[i + 1]
        lt = inp["lt"]
        jcols = {c["name"]: c["type"] for c in lt.schema}
        where = f"joins[{i}] ({inp['table']})"
        for l, r in j["on"]:
            if l not in gcols:
                raise _bad("column_unknown", f"{where}: the grain table {spec['grain']!r} has no column {l!r}.", join=i, column=l)
            if r not in jcols:
                raise _bad("column_unknown", f"{where}: table {inp['table']!r} has no column {r!r}.", join=i, column=r)
            fl, fr_ = frames.key_family(gcols[l]), frames.key_family(jcols[r])
            if {fl, fr_} == {"number", "text"}:
                raise _bad("join_types", f"{where}: {spec['grain']}.{l} ({gcols[l]}) and {inp['table']}.{r} ({jcols[r]}) "
                                         "don't compare (a number and a text column).", join=i)
            if fl != fr_:  # F-X3b (M6): DATE = TIMESTAMP compares as TIMESTAMP; any other mix is refused, never text
                raise _bad("key_type_mismatch",
                           f"{where}: {spec['grain']}.{l} is {gcols[l]} and {inp['table']}.{r} is {jcols[r]}: they don't "
                           "compare (a date with text, a time zone with none, a bool with a number …). Give both the same "
                           "type (a new version with the column re-typed), then join.",
                           join=i, left={"column": l, "type": gcols[l]}, right={"column": r, "type": jcols[r]})
        if j["type"] == "aggregate":
            for k, a in enumerate(j["aggs"]):
                if a["column"] and a["column"] not in jcols:
                    raise _bad("column_unknown", f"{where}.aggs[{k}]: table {inp['table']!r} has no column {a['column']!r}.",
                               join=i, column=a["column"])
                if a["fn"] in NUMERIC_FNS and frames.kind_of_type(jcols[a["column"]]) not in ("integer", "number"):
                    raise _bad("agg_type", f"{where}.aggs[{k}]: {a['fn']} needs a number column; {a['column']} is "
                                           f"{jcols[a['column']]}.", join=i)
                w = a.get("window")
                if w:
                    if w["date"] not in jcols:
                        raise _bad("column_unknown", f"{where}.aggs[{k}].window: table {inp['table']!r} has no column "
                                                     f"{w['date']!r}.", join=i, column=w["date"])
                    if w["relative_to"] not in gcols:
                        raise _bad("column_unknown", f"{where}.aggs[{k}].window: the grain table has no column "
                                                     f"{w['relative_to']!r}.", join=i, column=w["relative_to"])
                    for side, col, typ in (("date", w["date"], jcols[w["date"]]), ("relative_to", w["relative_to"], gcols[w["relative_to"]])):
                        # F-X3b (M5): a window needs dates. DATE / TIMESTAMP pass; text is checked on the data below (every
                        # value must read as an ISO date); a number (an Excel serial), a bool or a zoned timestamp: 400.
                        if not frames.is_date_type(typ) and frames.key_family(typ) != "text":
                            raise _bad("window_type",
                                       f"{where}.aggs[{k}].window.{side}: {col} is {typ}, not a date, so no row would fall in "
                                       "the window. Re-type the column as a DATE (a new version) and join that.",
                                       join=i, agg=k, column=col, type=typ)
        else:
            for c in j.get("columns") or []:
                if c not in jcols:
                    raise _bad("column_unknown", f"{where}.columns: table {inp['table']!r} has no column {c!r}.", join=i, column=c)
    shared = pii_check(db, spec, ins, version_id, by)  # F-PII: after the column checks (an unknown column is a 400 first)
    # output names
    seen: dict[str, str] = {}
    for name, origin, _src in frames.join_output_columns(spec, [x["lt"] for x in ins]):
        if name in seen or name == "__row_id":
            raise _bad("column_collision", f"Two frame columns would be named {name!r} ({seen.get(name, 'reserved')} and "
                                           f"{origin}): set a prefix on the join.", column=name)
        seen[name] = origin
    # keys and coverage, one DuckDB pass per joined table
    gsrc = frames.lake_sql(g)
    with duck.job() as con:
        for i, j in enumerate(spec["joins"]):
            inp = ins[i + 1]
            lt = inp["lt"]
            jcols = {c["name"]: c["type"] for c in lt.schema}
            right = [r for _, r in j["on"]]
            jsrc = frames.lake_sql(lt)
            nn = " AND ".join(f"{q(r)} IS NOT NULL" for r in right)
            key: dict[str, Any] = {"columns": right}
            if j["type"] in ("n:1", "1:1"):
                declared = _declared_key(db, inp["version_id"], inp["table"], right)
                n, nd = con.execute(f"SELECT count(*), count(DISTINCT ({', '.join(q(r) for r in right)})) FROM {jsrc} WHERE {nn}"
                                    if len(right) > 1 else
                                    f"SELECT count(*), count(DISTINCT {q(right[0])}) FROM {jsrc} WHERE {nn}").fetchall()[0]
                dup = int(n) - int(nd)
                key.update(how="declared" if declared else "checked", unique=dup == 0, repeated=dup)
                if dup:
                    raise _bad("one_to_many", f"joins[{i}] is {j['type']}, but {inp['table']}({', '.join(right)}) is not a key: "
                                              f"{dup:,} repeated value(s), so a grain row would repeat (1:n). Use type "
                                              "'aggregate' with aggs, or join on a key.", join=i, repeated=dup)
                if j["type"] == "1:1":
                    left = [l for l, _ in j["on"]]
                    lnn = " AND ".join(f"{q(l)} IS NOT NULL" for l in left)
                    m, md = con.execute(f"SELECT count(*), count(DISTINCT ({', '.join(q(l) for l in left)})) FROM {gsrc} WHERE {lnn}"
                                        if len(left) > 1 else
                                        f"SELECT count(*), count(DISTINCT {q(left[0])}) FROM {gsrc} WHERE {lnn}").fetchall()[0]
                    if int(m) != int(md):
                        raise _bad("not_one_to_one", f"joins[{i}] is 1:1, but the grain's {', '.join(left)} repeats "
                                                     f"({int(m) - int(md):,} times): use n:1.", join=i)
            on = " AND ".join(f"{frames._key_expr('g', l, gcols[l], jcols[r])} = {frames._key_expr('p', r, jcols[r], gcols[l])}"
                              for l, r in j["on"])
            lnn = " AND ".join(f"g.{q(l)} IS NOT NULL" for l, _ in j["on"])
            matched, with_key = con.execute(
                f"SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM {jsrc} p WHERE {on})), count(*) FILTER (WHERE {lnn})"
                f" FROM {gsrc} g").fetchall()[0]
            ent: dict[str, Any] = {"join": i, "table": inp["table"], "version_id": inp["version_id"], "type": j["type"],
                                   "on": j["on"], "table_rows": lt.n_rows, "grain_rows": g.n_rows, "matched": int(matched),
                                   "coverage": round(int(matched) / g.n_rows, 4) if g.n_rows else None,
                                   "orphans": int(with_key) - int(matched), "missing_key": g.n_rows - int(with_key), "key": key}
            if j["type"] == "aggregate":
                _window_text_check(con, j, i, inp["table"], jsrc, jcols, gsrc, gcols)
                jn = con.execute(f"SELECT count(*) FROM {jsrc} p WHERE EXISTS (SELECT 1 FROM {gsrc} g WHERE {on})").fetchall()[0][0]
                ent["rows_aggregated"] = int(jn)
                ent["columns"] = [j["prefix"] + a["name"] for a in j["aggs"]]
            else:
                ent["columns"] = [n for n, o, _ in frames.join_output_columns(spec, [x["lt"] for x in ins]) if o == f"join:{i}"]
            report.append(ent)
    cols = frames.join_output_columns(spec, [x["lt"] for x in ins])
    return {"version_id": version_id, "grain": spec["grain"], "grain_rows": g.n_rows,
            "inputs": [{**{k: x[k] for k in ("role", "version_id", "root_id", "table", "table_sha256", "n_rows")}, "alias": a}
                       for x, a in zip(ins, input_aliases(ins))],
            "coverage": report, "columns": [{"name": n, "from": o} for n, o, _ in cols], "n_columns": len(cols),
            **({"shared_inputs": shared} if shared else {}), "_inputs": ins}


def public(rep: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rep.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- saved joins


def _root(db, root_id: str) -> dict[str, Any]:
    with db.connect() as con:
        r = con.execute("SELECT id, team_id, project_id, default_version_id, state FROM dataset_roots WHERE id = ?", (root_id,)).fetchone()
        if not r:
            d = con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (root_id,)).fetchone()
            if d and d[0] != root_id:
                raise JoinError(404, "not_found", f"{root_id} is a version of {d[0]}: use the dataset's root id.")
            raise JoinError(404, "not_found", f"No dataset {root_id}.")
        return dict(r)


def _latest_version(db, root_id: str, default: str | None) -> str:
    with db.connect() as con:
        if default and con.execute("SELECT 1 FROM datasets WHERE id = ? AND (state IS NULL OR state <> 'deleted')", (default,)).fetchone():
            return default
        r = con.execute("SELECT id FROM datasets WHERE (id = ? OR root_id = ?) AND (state IS NULL OR state <> 'deleted')"
                        " ORDER BY version_no DESC, created_at DESC LIMIT 1", (root_id, root_id)).fetchone()
    if not r:
        raise JoinError(409, "no_version", f"{root_id} has no live version to check the join against.")
    return r[0]


def version_of_root(db, root_id: str, version_id: str) -> None:
    with db.connect() as con:
        r = con.execute("SELECT COALESCE(root_id, id) FROM datasets WHERE id = ?", (version_id,)).fetchone()
    if not r or r[0] != root_id:
        raise JoinError(404, "not_found", f"{version_id} is not a version of {root_id}.")


def row_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["spec"] = json.loads(d.pop("spec_json"))
    return d


def save(db, root_id: str, name: str, spec: Any, by: str, version_id: str | None = None) -> tuple[dict[str, Any], bool]:
    """(join, created). Validated against `version_id` (else the root's default, else its latest version) first; a
    failure is a 400 and nothing is saved. The same spec under the same name → the existing row (200). A changed spec
    → spec_version + 1. The same spec under another name of this root → 409 (one frame identity, one join)."""
    if not isinstance(name, str) or not NAME_RE.match(name.strip()):
        raise _bad("bad_name", "name is 1–80 characters: letters, digits, space, '_', '.', '-'.")
    name = name.strip()
    root = _root(db, root_id)
    norm = normalise(spec)
    vid = version_id or _latest_version(db, root_id, root.get("default_version_id"))
    version_of_root(db, root_id, vid)
    rep = validate(db, norm, vid, by)
    sha = spec_sha256(norm)
    with db.connect() as con:
        same = con.execute("SELECT * FROM joins WHERE root_id = ? AND spec_sha256 = ? AND state = 'active'"
                           " ORDER BY spec_version DESC LIMIT 1", (root_id, sha)).fetchone()
        if same and same["name"] == name:
            return {**row_out(same), "validation": public(rep)}, False
        if same:
            raise JoinError(409, "same_spec", f"This spec is already saved on {root_id} as {same['name']!r} "
                                              f"(v{same['spec_version']}, {same['id']}).", join_id=same["id"])
        prev = con.execute("SELECT max(spec_version) FROM joins WHERE root_id = ? AND name = ?", (root_id, name)).fetchone()[0]
        jid = "jn_" + secrets.token_hex(5)
        ver = int(prev or 0) + 1
        con.execute("INSERT INTO joins (id, root_id, team_id, name, spec_version, spec_json, spec_sha256, state, created_at, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (jid, root_id, root["team_id"], name, ver, json.dumps(norm, separators=(",", ":")), sha, S.now(), by))
        S.audit(con, by, "join_saved", details={"root_id": root_id, "join_id": jid, "name": name, "spec_version": ver,
                                               "spec_sha256": sha, "validated_on": vid,
                                               "coverage": [{k: c[k] for k in ("table", "type", "coverage", "orphans")}
                                                            for c in rep["coverage"]]})
        row = con.execute("SELECT * FROM joins WHERE id = ?", (jid,)).fetchone()
    return {**row_out(row), "validation": public(rep)}, True


def get(db, join_id: str, root_id: str | None = None) -> dict[str, Any]:
    with db.connect() as con:
        r = con.execute("SELECT * FROM joins WHERE id = ?", (join_id,)).fetchone()
    if not r or (root_id and r["root_id"] != root_id):
        raise JoinError(404, "not_found", f"No saved join {join_id}" + (f" on {root_id}." if root_id else "."))
    return row_out(r)


def list_joins(db, root_id: str) -> list[dict[str, Any]]:
    _root(db, root_id)
    with db.connect() as con:
        rows = [row_out(r) for r in con.execute("SELECT * FROM joins WHERE root_id = ? ORDER BY name, spec_version", (root_id,))]
        n = {r[0]: int(r[1]) for r in con.execute(
            "SELECT f.join_id, count(*) FROM join_frames f JOIN joins j ON j.id = f.join_id WHERE j.root_id = ? GROUP BY f.join_id",
            (root_id,))}
    latest: dict[str, int] = {}
    for r in rows:
        latest[r["name"]] = max(latest.get(r["name"], 0), r["spec_version"])
    for r in rows:
        r["latest"] = r["spec_version"] == latest[r["name"]]
        r["n_frames"] = n.get(r["id"], 0)
    return rows


# --------------------------------------------------------------------------- join frames


def frame_sha256(spec_sha: str, inputs: list[dict[str, Any]], engine: str | None = None) -> str:
    """§5.8: the frame's identity = hash(spec sha + the ordered input table shas). F-X3b (M6): `engine` names a join
    rule that changed what the same spec computes (DATE = TIMESTAMP keys compared as TIMESTAMP, not text), so a frame
    built by the old rule is never reused as the new one; every other frame keeps its identity."""
    d: dict[str, Any] = {"format": "ensemble.join_frame/1", "spec_sha256": spec_sha, "inputs": [x["table_sha256"] for x in inputs]}
    if engine:
        d["engine"] = engine
    return M.sha256_hex(M.canonical(d))


def frame_engine(spec: dict[str, Any], lts: list[Any]) -> str | None:
    """'temporal_key_cast/1' when a join key pairs two different date types (DATE with TIMESTAMP), else None."""
    g = {c["name"]: c["type"] for c in lts[0].schema}
    for i, j in enumerate(spec["joins"]):
        t = {c["name"]: c["type"] for c in lts[i + 1].schema}
        for l, r in j["on"]:
            a, b = g.get(l, ""), t.get(r, "")
            if a.upper() != b.upper() and frames.key_family(a) == frames.key_family(b) == "temporal":
                return "temporal_key_cast/1"
    return None


def input_aliases(pinned: list[dict[str, Any]]) -> list[str]:
    """F-X3b (M7): the name a batch file (or an online request's table) gives each input, in spec order. A table name
    read once is its own alias; a name read twice (a local `cust` and another dataset's `cust`) gets its role:
    `cust.grain`, `cust.join0`, `cust.join1`. The same rule everywhere (validation, batches, /predict, /score)."""
    names = [x["table"] for x in pinned]
    out = []
    for i, x in enumerate(pinned):
        role = x.get("role") or ("grain" if i == 0 else f"join:{i - 1}")
        out.append(x["table"] if names.count(x["table"]) == 1 else f"{x['table']}.{role.replace(':', '')}")
    return out


def frame_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["inputs"] = json.loads(d.pop("inputs_json"))
    d["schema"] = json.loads(d.pop("schema_json"))
    return d


def _cache_ok(db, store: Any, sha: str | None) -> bool:
    if not sha:
        return False
    with db.connect() as con:
        b = con.execute("SELECT key, state FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
    return bool(b and b["state"] == "live" and store.exists(b["key"]))


def frame(db, store: Any, join_id: str, version_id: str, by: str) -> dict[str, Any]:
    """The join applied to `version_id`: validated, then the existing frame (`cached: true`) when its identity and its
    cache blob are there, else materialised (`frames.materialise`), its rows asserted equal to the grain's, written as a
    content-addressed `join_cache` blob and recorded in `join_frames` (with `joined_from` / `merged_from` edges)."""
    j = get(db, join_id)
    version_of_root(db, j["root_id"], version_id)
    spec = j["spec"]
    rep = validate(db, spec, version_id, by)  # F-PII: a revoked share refuses here, the cached frame too
    ins = rep.pop("_inputs")
    fsha = frame_sha256(j["spec_sha256"], ins, frame_engine(spec, [x["lt"] for x in ins]))
    _inputs_intact(db, ins, join_id, version_id, by, "before")  # F-X3b (M8): the bytes read are the pinned bytes
    with db.connect() as con:
        r = con.execute("SELECT * FROM join_frames WHERE frame_sha256 = ?", (fsha,)).fetchone()
    if r and _cache_ok(db, store, r["cache_blob"]):
        out = frame_out(r)
        return {**out, "cached": True, "join": {k: j[k] for k in ("id", "name", "spec_version", "spec_sha256")},
                "coverage": rep["coverage"], **({"same_frame_as_version": r["version_id"]} if r["version_id"] != version_id else {})}
    tmp = store.tmp_dir("join-" + secrets.token_hex(4))
    try:
        f = Path(tmp) / "frame.parquet"
        try:
            res = frames.materialise(spec, [x["lt"] for x in ins], f)
        except frames.GrainError as e:
            with db.connect() as con:
                S.audit(con, by, "join_frame_refused", details={"join_id": join_id, "version_id": version_id, "why": str(e)[:500]})
            raise JoinError(409, "grain_violated", str(e)) from e
        _inputs_intact(db, ins, join_id, version_id, by, "after")  # nothing changed under the build
        bsha, nbytes = M.file_sha256(f)
        key = store.put_file(f, bsha, "parquet")
    finally:
        if hasattr(store, "remove_tmp"):
            store.remove_tmp(tmp)
    pinned = [{**{k: x[k] for k in ("role", "version_id", "root_id", "table", "table_sha256", "n_rows")}, "verified": True,
               **({"share_id": x["share"]["share_id"], "share_pii": x["share"]["pii"]} if x.get("share") else {})} for x in ins]
    with db.connect() as con:
        con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'join_cache', ?, ?, 'live', ?)"
                    " ON CONFLICT (sha256) DO UPDATE SET state = 'live', purged_at = NULL, purged_by = NULL, purge_reason = NULL"
                    # P24: a cache GC removed is re-created (the frame is re-creatable); a purged one stays purged
                    " WHERE blobs.state IN ('pending', 'live') OR (blobs.state = 'purged' AND blobs.purge_reason LIKE 'gc:%')",
                    (bsha, key, nbytes, res["n_rows"], S.now()))
        if r:  # the identity was there but its cache blob is gone: the same frame, written again
            fid = r["id"]
            con.execute("UPDATE join_frames SET cache_blob = ?, n_rows = ?, schema_json = ? WHERE id = ?",
                        (bsha, res["n_rows"], json.dumps(res["schema"]), fid))
            again = {"rematerialised": True, "previous_cache_blob": r["cache_blob"]}
        else:
            fid = "jf_" + secrets.token_hex(5)
            con.execute("INSERT INTO join_frames (id, join_id, version_id, grain_table, inputs_json, frame_sha256, n_rows, schema_json,"
                        " cache_blob, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (fid, join_id, version_id, spec["grain"], json.dumps(pinned), fsha, res["n_rows"],
                         json.dumps(res["schema"]), bsha, S.now(), by))
            again = {}
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'join_frame', ?, 'cache', ?, ?)", (bsha, fid, j["team_id"], S.now()))
        by_version: dict[str, list[dict[str, Any]]] = {}
        for x in pinned:  # one edge per input version (the edge is unique per pair), naming every table read from it
            by_version.setdefault(x["version_id"], []).append(x)
        for vid, xs in by_version.items():
            rel = "joined_from" if xs[0]["root_id"] == j["root_id"] else "merged_from"
            S.add_edge(con, ("dataset", vid), ("join_frame", fid), rel, actor=by,
                       meta={"tables": [{"table": x["table"], "role": x["role"], "table_sha256": x["table_sha256"]} for x in xs],
                             "join_id": join_id})
        S.audit(con, by, "join_frame_materialised",
                details={"join_id": join_id, "join_frame_id": fid, "version_id": version_id, "root_id": j["root_id"],
                         "frame_sha256": fsha, "cache_blob": bsha, "n_rows": res["n_rows"], "seconds": res["seconds"], **again})
        row = con.execute("SELECT * FROM join_frames WHERE id = ?", (fid,)).fetchone()
    return {**frame_out(row), "cached": False, "seconds": res["seconds"], "bytes": nbytes, **again,
            "join": {k: j[k] for k in ("id", "name", "spec_version", "spec_sha256")}, "coverage": rep["coverage"]}


def _inputs_intact(db, ins: list[dict[str, Any]], join_id: str, version_id: str, by: str, when: str) -> None:
    """F-X3b (M8): every input table's parts re-hashed (on S3, the read-cache files DuckDB reads) against the committed
    table sha. A frame is built, or a cached one handed out, only from the pinned bytes; else 409 `input_integrity`
    naming the tables (audited `join_frame_refused`)."""
    bad = [x for x in check_inputs(db, {"inputs": [{k: i[k] for k in ("version_id", "table", "table_sha256")} for i in ins]})
           if not x["ok"]]
    if not bad:
        return
    with db.connect() as con:
        S.audit(con, by, "join_frame_refused", details={"join_id": join_id, "version_id": version_id, "why": "input_integrity",
                                                        "when": when, "inputs": bad})
    raise JoinError(409, "input_integrity",
                    "The join's input bytes are not the committed ones: "
                    + "; ".join(f"{x['table']} ({x['version_id']}) reads {str(x['now'])[:12]}…, committed {str(x['committed'])[:12]}…"
                                for x in bad)
                    + ". No frame was built: GET /api/datasets/{version}/verify names the part; restore it or re-upload.",
                    inputs=bad)


def release_frames_of(con, version_id: str) -> list[str]:
    """F-X3b (M9): a deleted (or purged) version gives up the join frames that read it: their `(join_frame, cache)` refs
    are dropped, so a cache no run pins becomes a GC candidate (a run's `(run, join_frame)` pin keeps it, as the
    delete rules say). Returns the frame ids."""
    out = []
    for r in con.execute("SELECT id, version_id, inputs_json FROM join_frames WHERE version_id = ? OR inputs_json LIKE ?",
                         (version_id, f"%{version_id}%")):
        if r["version_id"] == version_id or any(x.get("version_id") == version_id for x in json.loads(r["inputs_json"] or "[]")):
            out.append(r["id"])
    for fid in out:
        con.execute("DELETE FROM blob_refs WHERE owner_kind = 'join_frame' AND owner_id = ? AND role = 'cache'", (fid,))
    return out


def derived_check(db, store: Any, fr: dict[str, Any]) -> dict[str, Any]:
    """F-X3b (M8): is the cache what the pinned inputs join to? The frame is rebuilt from them with
    `frames.materialise` (the one join path) and compared with the cache file by __row_id, every cell, like
    registration parity. Catches a frame built from other bytes that were restored since."""
    j = get(db, fr["join_id"])
    tmp = store.tmp_dir("verify-" + secrets.token_hex(4))
    try:
        lts = [frames.lake_table(frames.Table(x["version_id"], x["table"])) for x in fr["inputs"]]
        out = Path(tmp) / "rebuilt.parquet"
        frames.materialise(j["spec"], lts, out)
        cache = frames.lake_table(frames.JoinFrame(fr["id"])).parts[0]["path"]
        cmp = frames.compare_frames(cache, out)
    except Exception as e:  # noqa: BLE001 - verify reports, it does not fail
        return {"ok": False, "summary": f"could not rebuild: {type(e).__name__}: {e}"[:300]}
    finally:
        if hasattr(store, "remove_tmp"):
            store.remove_tmp(tmp)
    return {**cmp, "method": "rebuilt from the pinned inputs with frames.materialise, compared by __row_id, every cell"}


def get_frame(db, frame_id: str) -> dict[str, Any]:
    with db.connect() as con:
        r = con.execute("SELECT * FROM join_frames WHERE id = ?", (frame_id,)).fetchone()
    if not r:
        raise JoinError(404, "not_found", f"No join frame {frame_id}.")
    return frame_out(r)


def frames_of_version(db, version_id: str) -> list[dict[str, Any]]:
    with db.connect() as con:
        return [frame_out(r) for r in con.execute(
            "SELECT f.*, j.name AS join_name, j.spec_version FROM join_frames f JOIN joins j ON j.id = f.join_id"
            " WHERE f.version_id = ? ORDER BY f.created_at", (version_id,))]


def check_inputs(db, fr: dict[str, Any]) -> list[dict[str, Any]]:
    """Every pinned input re-hashed now: [{version_id, table, pinned, now, ok}] (a run on the frame is refused when one
    differs: the cache would no longer be what those tables join to)."""
    out = []
    with db.connect() as con:
        states = {x["version_id"]: (con.execute("SELECT state FROM datasets WHERE id = ?", (x["version_id"],)).fetchone() or [None])[0]
                  for x in fr["inputs"]}
    for x in fr["inputs"]:
        st = states.get(x["version_id"])
        if st == "deleted":  # F-X3b (M9): a deleted input is reported, whatever its (shared) bytes still hash to
            out.append({"version_id": x["version_id"], "table": x["table"], "pinned": x["table_sha256"], "committed": None,
                        "now": "version_deleted", "state": "deleted", "ok": False})
            continue
        try:
            now, committed, _bad_parts = frames.hash_table(frames.Table(x["version_id"], x["table"]))
        except Exception as e:  # noqa: BLE001
            now, committed = f"unreadable: {type(e).__name__}", None
        out.append({"version_id": x["version_id"], "table": x["table"], "pinned": x["table_sha256"], "committed": committed,
                    "now": now, "ok": now == x["table_sha256"] == committed})
    return out


# --------------------------------------------------------------------------- the profile of a frame (POST /runs {join_id})


def profile(app, fr: dict[str, Any], target: str | None) -> dict[str, Any]:
    """profile_frame's shape for a join frame (what the run checks the label, ID columns and task against), cached beside
    the grain version by frame identity and target. Up to PROFILE_FULL_ROWS rows the whole frame, else a seeded sample
    with whole-frame missing / distinct counts (as `DatasetStore.lake_profile`)."""
    import pandas as pd

    from ..datasets import DatasetError
    from ..lake import duck
    from ..profiling import profile_frame

    ds = app.state.datasets
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"join_{fr['frame_sha256'][:16]}.{target or ''}")
    cache = ds.dir(fr["version_id"]) / "profiles" / f"{safe}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    ref = frames.JoinFrame(fr["id"])
    lt = frames.lake_table(ref)
    try:
        if lt.n_rows <= ds.PROFILE_FULL_ROWS:
            p = profile_frame(frames.read(ref), target)
        else:
            src = frames.lake_sql(lt)
            names = [c["name"] for c in lt.schema]
            with duck.job() as con:
                sample = con.execute(f"SELECT * FROM {src} USING SAMPLE reservoir({ds.PROFILE_SAMPLE_ROWS} ROWS) REPEATABLE (42)").to_arrow_table()
                exact = con.execute("SELECT " + ", ".join(f"count({frames._qi(c)}), approx_count_distinct({frames._qi(c)})"
                                                          for c in names) + f" FROM {src}").fetchall()[0]
            sample = sample.sort_by("__row_id")  # job connections don't keep ORDER BY order
            df = pd.DataFrame({c: frames._v1_column(sample.column(c), c) for c in names}, columns=names)
            p = profile_frame(df, target)
            n = lt.n_rows
            for i, col in enumerate(p["columns"]):
                nn, nu = int(exact[2 * i]), int(exact[2 * i + 1])
                col.update(missing=n - nn, missing_share=round((n - nn) / n, 6), unique=nu, unique_approximate=True)
            p["rows"] = n
            p["sampled"] = {"rows": int(len(df)), "of": n}
    except ValueError as e:
        raise DatasetError(str(e)) from e
    p["join_frame_id"] = fr["id"]
    p["table"] = fr["grain_table"]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(p, default=str))
    return p


# --------------------------------------------------------------------------- what a run on a join records


def record_run(db, run_id: str, cfg: dict[str, Any], by: str) -> None:
    """At queue time (the run row exists): one `join_input` row per pinned input table, the run's pin on the cache blob,
    and the `join_frame -trained_on-> run` edge. Insert-only; a re-queue keeps the first rows."""
    jn = cfg.get("join") or {}
    fid = jn.get("join_frame_id")
    if not fid:
        return
    with db.connect() as con:
        if con.execute("SELECT 1 FROM run_inputs WHERE run_id = ? AND role = 'join_input'", (run_id,)).fetchone():
            return
        team = (con.execute("SELECT team_id FROM joins WHERE id = ?", (jn.get("join_id"),)).fetchone() or [None])[0] or "tm_default"
        for x in jn.get("inputs") or []:
            con.execute("INSERT INTO run_inputs (id, run_id, role, version_id, table_name, join_frame_id, table_sha256, read_sha256,"
                        " match, read_mode, rows_total, created_at) VALUES (?, ?, 'join_input', ?, ?, ?, ?, ?, ?, 'engine_v1', ?, ?)",
                        ("ri_" + secrets.token_hex(6), run_id, x["version_id"], x["table"], fid, x["table_sha256"],
                         x.get("now"), None if x.get("now") is None else int(x.get("now") == x["table_sha256"]),
                         x.get("n_rows"), S.now()))
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'run', ?, 'join_frame', ?, ?)", (jn["cache_blob"], run_id, team, S.now()))
        S.add_edge(con, ("join_frame", fid), ("run", run_id), "trained_on", actor=by,
                   meta={"join_id": jn.get("join_id"), "frame_sha256": jn.get("frame_sha256"), "cache_blob": jn["cache_blob"]})
