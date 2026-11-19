"""G1: counterfactuals ("what would change the decision") for adverse decisions of a binary version.

For an adverse applicant x (the served model predicts the adverse class at the version's threshold t), a
counterfactual is a nearby x′ that the same served model, at the same t, does not decline. The search is a greedy
coordinate search over the served predictor's own `predict_proba` (the /score path), so every option is re-scored by
the model that makes the decision:

    margin m(x) = s·(p̂(x) − t)          adverse ⟺ ŷ(x) = the adverse class (m ≥ 0 at the boundary)
    distance d(x, x′) = Σⱼ |x′ⱼ − xⱼ| / IQRⱼ  (numbers)  +  Σⱼ 1[x′ⱼ ≠ xⱼ]  (categories)

with s = +1 when the adverse class is the positive class, IQRⱼ the interquartile range of feature j over the training
rows (its 5–95% range when the IQR is 0). The rules a counterfactual follows:

- only actionable features change: `fixed` ones never move (age, dates, IDs, geography: G0's roles, editable per model),
  `increase` / `decrease` ones move one way only;
- a feature under an F8 monotone constraint moves only in the direction that lowers the adverse probability;
- values stay inside what training saw: numbers on a grid of training quantiles (rounded to the column's precision),
  categories among the observed ones (≥ 0.5% of rows);
- each option changes at most `max_changes` features.

Search, vectorised over many applicants (one `predict_proba` call per step scores every candidate of every row):
1. greedy: from x, try every single-feature move; take a move that flips the decision with the smallest d, else the
   move with the largest margin drop per unit of added distance; repeat up to `max_changes` times;
2. shrink: walk each changed feature back toward xⱼ (to the closest grid value that keeps the decision flipped, or
   back to xⱼ itself);
3. diversity: option k forbids the main feature (largest share of d) of every earlier option, so the options are
   different ways to get approved, not the same one nudged.

Plausibility of x′: the share of approved training applicants whose nearest approved neighbour is at least as far
away as x′'s nearest approved neighbour, under the same distance:

    π(x′) = (1/N) Σₙ 1[ d_NN(aₙ) ≥ d_NN(x′) ]

π ≈ 0.5 means x′ looks like a typical approved applicant; π near 0 means it is further from every approved applicant
than almost any real one is (an unusual combination even though each value is in range)."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pandas as pd

ACTIONS = ("fixed", "any", "increase", "decrease")
OPTIONS = 3
MAX_CHANGES = 3
GRID = 41  # training quantiles per numeric feature (0, 2.5%, …, 100%)
MIN_CAT_SHARE = 0.005
NN_REF = 1500  # approved reference applicants for plausibility


METHOD = ("greedy coordinate search over the served model's own predict_proba at the version's threshold t: only "
          "actionable features move (fixed: age, dates, IDs, geography; history only down; time only up; F8 monotone "
          "features only in the direction that lowers the adverse probability), numbers on a grid of 41 training "
          "quantiles, categories among those seen in training, ≤ 3 changes; each option is shrunk back toward the "
          "applicant and re-scored; up to 3 diverse options (each forbids the main feature of the earlier ones); "
          "distance d = Σ|Δxⱼ|/IQRⱼ + #categories changed; plausibility π = share of approved applicants whose nearest "
          "approved neighbour is at least as far")


class RecourseError(ValueError):
    pass


def _decimals(s: pd.Series) -> int:
    """The column's precision: 0 for whole numbers, else the most decimals seen (≤ 4)."""
    v = s.dropna().to_numpy(dtype=float)[:2000]
    if not len(v) or np.allclose(v, np.round(v)):
        return 0
    for d in range(1, 5):
        if np.allclose(v, np.round(v, d), atol=1e-9):
            return d
    return 4


class Recourse:
    """Counterfactual search for one served binary version. `rs` is F10's Reasoner (the decision rule at t, the
    adverse class, the served predictor and its features); `actions` is feature → fixed / any / increase / decrease."""

    def __init__(self, rs, train: pd.DataFrame, actions: dict[str, str], *, options: int = OPTIONS,
                 max_changes: int = MAX_CHANGES, seed: int = 0) -> None:
        from .reasons import _coerce

        self.rs, self.p = rs, rs.p
        self.features = list(rs.features)
        self.numeric = set(rs.att.numeric)
        self.options, self.max_changes = int(options), int(max_changes)
        train = _coerce(train.reindex(columns=self.features).copy(), self.numeric)
        self._coerce = lambda X: _coerce(X.reindex(columns=self.features).copy(), self.numeric)
        self.constraints = dict(rs.constraints)
        # The direction each feature may move in: the user's action, narrowed by the F8 constraint (a feature that
        # raises P(positive) with sign c lowers the adverse probability only when it moves by −c·s).
        self.allowed: dict[str, str] = {}
        self.why: dict[str, str] = {}
        for f in self.features:
            a = actions.get(f, "any")
            if a not in ACTIONS:
                raise RecourseError(f"Action {a!r} of {f} must be one of {', '.join(ACTIONS)}.")
            c = self.constraints.get(f, 0)
            if c and a != "fixed" and f in self.numeric:
                way = "decrease" if c * rs.s > 0 else "increase"
                if a in ("any", way):
                    a = way
                    self.why[f] = f"monotone {'↑' if c > 0 else '↓'} (F8): only a {way} lowers P({rs.adverse})"
                else:
                    a = "fixed"
                    self.why[f] = f"monotone {'↑' if c > 0 else '↓'} (F8) and '{actions.get(f)}' leave no helpful move"
            self.allowed[f] = a
        self.grid: dict[str, np.ndarray] = {}
        self.cats: dict[str, list[Any]] = {}
        self.scale: dict[str, float] = {}
        self.lo: dict[str, float] = {}
        self.hi: dict[str, float] = {}
        self.dec: dict[str, int] = {}
        for f in self.features:
            s = train[f]
            if f in self.numeric:
                v = pd.to_numeric(s, errors="coerce").dropna().to_numpy(dtype=float)
                if not len(v):
                    self.allowed[f] = "fixed"
                    continue
                self.dec[f] = _decimals(pd.Series(v))
                q = np.quantile(v, np.linspace(0, 1, GRID))
                self.grid[f] = np.unique(np.round(q, self.dec[f]))
                iqr = float(np.subtract(*np.quantile(v, [0.75, 0.25])))
                if iqr <= 0:
                    iqr = float(np.subtract(*np.quantile(v, [0.95, 0.05])))
                self.scale[f] = iqr if iqr > 0 else (float(v.std()) or 1.0)
                self.lo[f], self.hi[f] = float(v.min()), float(v.max())
            else:
                vc = s.dropna().astype(str).value_counts(normalize=True)
                self.cats[f] = [k for k, share in vc.items() if share >= MIN_CAT_SHARE]
        # Plausibility reference: approved training applicants and their own nearest-neighbour distances.
        ref = train.sample(min(len(train), 4 * NN_REF), random_state=seed) if len(train) > 4 * NN_REF else train
        ok = ~self._adverse(ref)
        ref = ref[ok].head(NN_REF).reset_index(drop=True)
        self.ref_num, self.ref_cat = self._encode(ref)
        if len(ref) >= 2:
            d = self._dist_matrix(self.ref_num, self.ref_cat, self.ref_num, self.ref_cat)
            np.fill_diagonal(d, np.inf)
            self.ref_nn = np.sort(d.min(axis=1))
        else:
            self.ref_nn = np.zeros(0)

    # ------------------------------------------------------------------ the served decision

    def _score(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(adverse, p̂) from one predict_proba call: the served decision at t, as /score makes it."""
        return self.rs.decide(X)

    def _adverse(self, X: pd.DataFrame) -> np.ndarray:
        return self._score(X)[0]

    def margin(self, p: np.ndarray) -> np.ndarray:
        return self.rs.s * (p - self.rs.threshold)

    # ------------------------------------------------------------------ distances

    def _encode(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        nf = [f for f in self.features if f in self.scale]
        cf = [f for f in self.features if f in self.cats]
        num = np.column_stack([pd.to_numeric(X[f], errors="coerce").to_numpy(dtype=float) / self.scale[f] for f in nf]) \
            if nf else np.zeros((len(X), 0))
        cat = X[cf].astype(str).to_numpy() if cf else np.zeros((len(X), 0), dtype=object)
        return num, cat

    @staticmethod
    def _dist_matrix(an, ac, bn, bc) -> np.ndarray:
        d = np.zeros((len(an), len(bn)))
        for j in range(an.shape[1]):
            diff = np.abs(an[:, j:j + 1] - bn[:, j][None, :])
            d += np.where(np.isnan(diff), 1.0, diff)
        for j in range(ac.shape[1]):
            d += ac[:, j:j + 1] != bc[:, j][None, :]
        return d

    def _cost(self, f: str, a: Any, b: Any) -> float:
        """One feature's share of d(x, x′)."""
        if f in self.scale:
            fa, fb = _num(a), _num(b)
            if math.isnan(fa) and math.isnan(fb):
                return 0.0
            if math.isnan(fa) or math.isnan(fb):
                return 1.0
            return abs(fb - fa) / self.scale[f]
        return 0.0 if str(a) == str(b) else 1.0

    def plausibility(self, X: pd.DataFrame) -> np.ndarray:
        if not len(self.ref_nn) or not len(X):
            return np.full(len(X), np.nan)
        n, c = self._encode(X)
        dnn = self._dist_matrix(n, c, self.ref_num, self.ref_cat).min(axis=1)
        return 1.0 - np.searchsorted(self.ref_nn, dnn, side="left") / len(self.ref_nn)

    # ------------------------------------------------------------------ candidate moves

    def _moves(self, f: str, x0: Any, cur: Any) -> list[Any]:
        """Values feature f may take, given the applicant's own value x0 (the direction rule is relative to x0)."""
        a = self.allowed.get(f, "fixed")
        if a == "fixed":
            return []
        if f in self.grid:
            g = self.grid[f]
            v0 = _num(x0)
            if not math.isnan(v0):
                if a == "increase":
                    g = g[g > v0]
                elif a == "decrease":
                    g = g[g < v0]
            vc = _num(cur)
            return [float(v) for v in g if math.isnan(vc) or v != vc]
        if f in self.cats:
            return [c for c in self.cats[f] if c != str(cur)] if a == "any" else []
        return []

    def _frame(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        return self._coerce(pd.DataFrame(rows, columns=self.features))

    # ------------------------------------------------------------------ the search

    def search(self, X: pd.DataFrame) -> list[dict[str, Any]]:
        """Counterfactual options for every row of X (rows that are not adverse get none)."""
        t0 = time.perf_counter()
        X = self._coerce(X).reset_index(drop=True)
        adverse0, p0 = self._score(X)
        base = X.to_dict(orient="records")
        found: list[list[dict[str, Any]]] = [[] for _ in range(len(X))]
        forbidden: list[set[str]] = [set() for _ in range(len(X))]
        active = [i for i in range(len(X)) if adverse0[i]]
        for _ in range(self.options):
            res = self._greedy(base, active, forbidden, p0)
            res = self._shrink(base, res)
            for i, (row, ok, p_new) in res.items():
                if not ok:
                    continue
                changes = [f for f in self.features if self._cost(f, base[i][f], row[f]) > 0]
                key = tuple(sorted((f, str(row[f])) for f in changes))
                costs = {f: self._cost(f, base[i][f], row[f]) for f in changes}
                if any(o["key"] == key for o in found[i]):
                    forbidden[i].add(max(costs, key=costs.get))
                    continue
                costs = {f: self._cost(f, base[i][f], row[f]) for f in changes}
                forbidden[i].add(max(costs, key=costs.get))
                found[i].append({"key": key, "row": row, "p": p_new, "costs": costs})
            active = [i for i in active if len(found[i]) and len(forbidden[i]) < len(self.actionable())]
        # Re-score every option with the served decision (validity), and score plausibility, in one call each.
        flat = [(i, o) for i in range(len(X)) for o in found[i]]
        if flat:
            F = self._frame([o["row"] for _, o in flat])
            adv, pf = self._score(F)
            pl = self.plausibility(F)
            for k, (i, o) in enumerate(flat):
                o.update(valid=bool(not adv[k]), p=float(pf[k]), plausibility=float(pl[k]))
        out = []
        for i in range(len(X)):
            opts = sorted(found[i], key=lambda o: sum(o["costs"].values()))
            out.append({"adverse": bool(adverse0[i]), "p": float(p0[i]),
                        "options": [self._option(base[i], o) for o in opts]})
        self.last_seconds = time.perf_counter() - t0
        return out

    def actionable(self) -> list[str]:
        return [f for f in self.features if self.allowed.get(f, "fixed") != "fixed" and (f in self.grid or f in self.cats)]

    def _greedy(self, base, active, forbidden, p0) -> dict[int, tuple[dict[str, Any], bool, float]]:
        cur = {i: dict(base[i]) for i in active}
        m_cur = {i: float(self.margin(np.array([p0[i]]))[0]) for i in active}
        used: dict[int, list[str]] = {i: [] for i in active}
        done: dict[int, tuple[dict[str, Any], bool, float]] = {}
        todo = list(active)
        for _step in range(self.max_changes):
            cands: list[tuple[int, str, Any]] = []
            rows: list[dict[str, Any]] = []
            for i in todo:
                feats = [f for f in self.actionable() if f not in forbidden[i]]
                if len(used[i]) >= self.max_changes:
                    continue
                for f in feats:
                    for v in self._moves(f, base[i][f], cur[i][f]):
                        r = dict(cur[i])
                        r[f] = v
                        cands.append((i, f, v))
                        rows.append(r)
            if not rows:
                break
            F = self._frame(rows)
            adv, pf = self._score(F)
            m = self.margin(pf)
            best: dict[int, tuple[float, int, bool]] = {}
            for k, (i, f, v) in enumerate(cands):
                d_new = sum(self._cost(g, base[i][g], rows[k][g]) for g in set(used[i]) | {f})
                if not adv[k]:
                    score, flip = -d_new, True  # a flip: the nearest one wins
                else:
                    d_old = sum(self._cost(g, base[i][g], cur[i][g]) for g in used[i])
                    gain = m_cur[i] - m[k]
                    if gain <= 0:
                        continue
                    score, flip = gain / (max(d_new - d_old, 0) + 0.05), False
                b = best.get(i)
                if b is None or (flip and not b[2]) or (flip == b[2] and score > b[0]):
                    best[i] = (score, k, flip)
            nxt = []
            for i in todo:
                if i not in best:
                    done[i] = (cur[i], False, float("nan"))
                    continue
                _, k, flip = best[i]
                f = cands[k][1]
                cur[i] = rows[k]
                m_cur[i] = float(m[k])
                if f not in used[i]:
                    used[i].append(f)
                if flip:
                    done[i] = (cur[i], True, float("nan"))
                else:
                    nxt.append(i)
            todo = nxt
            if not todo:
                break
        for i in todo:
            done[i] = (cur[i], False, float("nan"))
        return done

    def _shrink(self, base, res) -> dict[int, tuple[dict[str, Any], bool, float]]:
        """Walk each changed feature back toward the applicant's value while the decision stays flipped."""
        res = dict(res)
        for _pass in range(self.max_changes):
            cands: list[tuple[int, str, Any]] = []
            rows: list[dict[str, Any]] = []
            for i, (row, ok, _) in res.items():
                if not ok:
                    continue
                changed = [f for f in self.features if self._cost(f, base[i][f], row[f]) > 0]
                if _pass >= len(changed):
                    continue
                for f in changed:
                    x0, xc = base[i][f], row[f]
                    if f in self.grid and not math.isnan(_num(x0)):
                        lo, hi = sorted((_num(x0), _num(xc)))
                        vals = [float(v) for v in self.grid[f] if lo < v < hi] + [x0]
                        # finer: midpoints rounded to the column's precision
                        vals += [round(x0 + (xc - x0) * q, self.dec.get(f, 4)) for q in (0.25, 0.5, 0.75)]
                    else:
                        vals = [x0]
                    for v in set(vals):
                        if self._cost(f, base[i][f], v) >= self._cost(f, base[i][f], xc):
                            continue
                        r = dict(row)
                        r[f] = v
                        cands.append((i, f, v))
                        rows.append(r)
            if not rows:
                break
            F = self._frame(rows)
            adv = self._adverse(F)
            best: dict[int, tuple[float, int]] = {}
            for k, (i, f, v) in enumerate(cands):
                if adv[k]:
                    continue
                d = sum(self._cost(g, base[i][g], rows[k][g]) for g in self.features)
                if i not in best or d < best[i][0]:
                    best[i] = (d, k)
            if not best:
                break
            for i, (_, k) in best.items():
                res[i] = (rows[k], True, float("nan"))
        return res

    def _option(self, x0: dict[str, Any], o: dict[str, Any]) -> dict[str, Any]:
        ch = []
        for f, c in sorted(o["costs"].items(), key=lambda kv: -kv[1]):
            a, b = x0[f], o["row"][f]
            ch.append({"feature": f, "from": _plain(a), "to": _plain(b), "cost": float(c),
                       "kind": "number" if f in self.scale else "category",
                       "delta": (float(b) - float(a)) if f in self.scale and not (math.isnan(_num(a)) or math.isnan(_num(b))) else None})
        return {"changes": ch, "n_changes": len(ch), "distance": float(sum(o["costs"].values())),
                "plausibility": o.get("plausibility"), "p": o.get("p"), "valid": o.get("valid", False),
                "text": "; ".join(_say(c) for c in ch)}

    def info(self) -> dict[str, Any]:
        return {"options": self.options, "max_changes": self.max_changes, "grid": GRID,
                "allowed": self.allowed, "why_monotone": self.why, "actionable": self.actionable(),
                "ranges": {f: {"min": self.lo[f], "max": self.hi[f], "scale": self.scale[f]} for f in self.scale},
                "categories": {f: self.cats[f][:20] for f in self.cats},
                "reference_rows": int(len(self.ref_nn)), "threshold": self.rs.threshold,
                "adverse_class": self.rs.adverse, "positive_class": self.rs.positive, "s": self.rs.s}


def _num(v: Any) -> float:
    try:
        return float(v) if v is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _plain(v: Any) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _say(c: dict[str, Any]) -> str:
    f, a, b = c["feature"], c["from"], c["to"]
    if c["kind"] == "number" and a is not None and b is not None:
        return f"{f} {a:,} → {b:,} ({'+' if b > a else '−'}{abs(b - a):,.4g})"
    return f"{f} {a if a is not None else 'missing'} → {b}"


def check(rc: Recourse, X: pd.DataFrame, actions: dict[str, str]) -> dict[str, Any]:
    """The acceptance check on rows X: of the adverse ones, how many get ≥ 1 valid option (re-scored by the served
    model at t), and that no option moves a fixed feature, leaves the training range / categories or breaks a
    direction rule."""
    t0 = time.perf_counter()
    res = rc.search(X)
    adv = [r for r in res if r["adverse"]]
    n_valid = sum(any(o["valid"] for o in r["options"]) for r in adv)
    opts = [o for r in adv for o in r["options"]]
    fixed = {f for f, a in rc.allowed.items() if a == "fixed"}
    bad_fixed = sum(any(c["feature"] in fixed for c in o["changes"]) for o in opts)
    bad_range = bad_dir = 0
    for o in opts:
        for c in o["changes"]:
            f = c["feature"]
            if c["kind"] == "number":
                if c["to"] is None or not (rc.lo[f] - 1e-9 <= c["to"] <= rc.hi[f] + 1e-9):
                    bad_range += 1
                a = rc.allowed[f]
                if c["delta"] is not None and ((a == "increase" and c["delta"] < 0) or (a == "decrease" and c["delta"] > 0)):
                    bad_dir += 1
            elif str(c["to"]) not in rc.cats.get(f, []):
                bad_range += 1
    by_feat: dict[str, int] = {}
    for o in opts:
        for c in o["changes"]:
            by_feat[c["feature"]] = by_feat.get(c["feature"], 0) + 1
    valid_opts = [o for o in opts if o["valid"]]
    return {"rows": int(len(X)), "adverse": len(adv), "with_valid": int(n_valid),
            "share_valid": n_valid / len(adv) if adv else None, "options": len(opts), "valid_options": len(valid_opts),
            "mean_options": len(opts) / len(adv) if adv else None,
            "median_distance": float(np.median([o["distance"] for o in valid_opts])) if valid_opts else None,
            "median_changes": float(np.median([o["n_changes"] for o in valid_opts])) if valid_opts else None,
            "median_plausibility": float(np.nanmedian([o["plausibility"] for o in valid_opts])) if valid_opts else None,
            "fixed_changed": int(bad_fixed), "out_of_range": int(bad_range), "wrong_direction": int(bad_dir),
            "features_used": dict(sorted(by_feat.items(), key=lambda kv: -kv[1])),
            "seconds": time.perf_counter() - t0, "actions": dict(rc.allowed),
            "examples": [{"p": r["p"], "options": r["options"]} for r in adv[:3]]}
