"""F10: reason codes for adverse decisions (binary, credit-style models).

For a binary version, a decision is adverse when the served model predicts the adverse class (by default the positive
class, e.g. defaulted = Yes) at the version's own threshold t (F4 calibration / F7 cost). For an adverse row the
reason codes are the features pushing it toward the adverse outcome, ranked: with φⱼ the SHAP value of feature j for
the probability of the positive class and s = +1 if the adverse class is the positive class (−1 otherwise), the push
toward adverse is aⱼ = s·φⱼ, and the codes are the top K (= 4) features with aⱼ > 0, ordered by aⱼ.

Attribution (the same game everywhere, on the served predictor's own `predict_proba`):

    v(S) = (1/B) Σ_b f(x_S, b_S̄)   (interventional; features outside S take a background row's values)

with B background rows sampled from the version's training rows (fixed seed). Two estimators:

- ``tree``: interventional TreeSHAP (shap.TreeExplainer, model_output='probability') when the served model is a
  LightGBM model, a bagged LightGBM (the mean of its folds) or a weighted ensemble of those (Σ wₘ fₘ, wₘ ≥ 0), every
  feature it reads is a raw numeric column passed through 1:1, and no split is categorical. SHAP is linear in the
  model, so φ(Σ wₘ fₘ) = Σ wₘ φ(fₘ), and on a 1:1 numeric pipeline the game over AutoGluon's features is the game over
  the raw columns: the values are exact for the served model. Checked on every call: f(x) = φ₀ + Σ φⱼ to 1e-6, else
  the kernel estimator is used and the reason is recorded.
- ``kernel``: Kernel SHAP for any model, vectorised over rows: M coalitions (all 2^d − 2 when that is ≤ M, which is
  exact Shapley; otherwise sampled from the Shapley kernel, in complementary pairs, with a fixed seed shared by every
  row) and one `predict_proba` call per chunk of rows on the R·M·B hybrid rows. The efficiency constraint
  f(x) = φ₀ + Σ φⱼ is imposed exactly; the individual φⱼ carry sampling error, measured against the reference Kernel
  SHAP of Try It (B = 50, 2,048 coalitions) by the stability report.

Monotone constraints (F8). With a single background row b, the Shapley value ψⱼ(x, b) is a weighted sum of
f(x_j, x_S, b_rest) − f(b_j, x_S, b_rest): if f is monotone with sign sⱼ in xⱼ, every term has the sign
sⱼ·sign(xⱼ − bⱼ). φⱼ = (1/B) Σ_b ψⱼ(x, b). So (i) a constrained feature can only push toward a higher score through
background rows it exceeds (for sⱼ = +1): its reason statement has a fixed direction ("too high"), never derived from
the data; (ii) if xⱼ ≥ max_b bⱼ then sⱼ·φⱼ ≥ 0, and if xⱼ ≤ min_b bⱼ then sⱼ·φⱼ ≤ 0. (ii) is checked on every
explained row and the violations are reported (0 for the exact estimators; sampling error can produce some for the
kernel estimator)."""

from __future__ import annotations

import json
import math
import re
import time
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

TOP_K = 4
FAST_BACKGROUND = 16
FAST_COALITIONS = 128
TREE_BACKGROUND = 50
CHUNK_EVALS = 60_000  # hybrid rows per predict_proba call


class ReasonError(ValueError):
    pass


# --------------------------------------------------------------------------- the mapping table


def _label(feature: str) -> str:
    s = re.sub(r"[_\.]+", " ", feature).strip()
    s = re.sub(r"\busd\b", "(USD)", s, flags=re.I)
    s = re.sub(r"\b2y\b", "in the last 2 years", s)
    return s[:1].upper() + s[1:]


# Geography and protected characteristics (and close proxies for them): such a code can explain the model, but it is not a
# reason a lender may give an applicant on an adverse-action notice (ECOA / Reg B in the US; fair-lending rules elsewhere).
_NOT_FOR_NOTICE = re.compile(r"(^|[_\s.])(region|zip|zipcode|postcode|postal|state|county|city|country|geo\w*|location|address|"
                             r"neighbou?rhood|surname|race|ethnicity|gender|sex|religion|marital\w*|nationality)($|[_\s.])", re.I)
NOT_FOR_NOTICE_NOTE = "not for adverse-action notices: geography or a protected characteristic (or a proxy for one)"


def default_mapping(features: list[str], kinds: dict[str, str], constraints: dict[str, int] | None) -> list[dict[str, Any]]:
    """One row per feature: a code (R01…), the statements and, for a monotone-constrained column, its sign. `notice` is
    False for geography and protected characteristics: they explain the model but may not be given as a reason."""
    constraints = constraints or {}
    out = []
    for i, f in enumerate(features):
        k = "number" if kinds.get(f, "number") == "number" else "category"
        lab = _label(f)
        row: dict[str, Any] = {"feature": f, "code": f"R{i + 1:02d}", "kind": k, "constraint": int(constraints.get(f, 0)),
                               "missing": f"{lab} not provided", "notice": not _NOT_FOR_NOTICE.search(f)}
        if not row["notice"]:
            row["notice_note"] = NOT_FOR_NOTICE_NOTE
        if k == "number":
            row["high"] = f"{lab} too high"
            row["low"] = f"{lab} too low"
        else:
            row["category"] = f"{lab}: {{value}}"
        out.append(row)
    return out


def merge_mapping(saved: list[dict[str, Any]] | None, defaults: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {r["feature"]: r for r in saved or [] if isinstance(r, dict) and "feature" in r}
    out = []
    for d in defaults:
        s = by.get(d["feature"], {})
        row = dict(d)
        for k in ("code", "high", "low", "category", "missing"):
            if k in d and isinstance(s.get(k), str) and s[k].strip():
                row[k] = s[k].strip()
        row["edited"] = any(k in s and s.get(k) != d.get(k) for k in ("code", "high", "low", "category", "missing"))
        out.append(row)
    return out


def validate_mapping(rows: list[dict[str, Any]], features: list[str]) -> None:
    known = set(features)
    codes: dict[str, str] = {}
    for r in rows:
        f = r.get("feature")
        if f not in known:
            raise ReasonError(f"No feature {f!r} in this model; its features are {', '.join(features)}.")
        code = str(r.get("code") or "").strip()
        if not code or len(code) > 16:
            raise ReasonError(f"The code of {f} must be 1–16 characters.")
        if code in codes:
            raise ReasonError(f"Code {code} is used twice ({codes[code]} and {f}).")
        codes[code] = f
        for k in ("high", "low", "category", "missing"):
            if k in r and r[k] is not None and (not isinstance(r[k], str) or not r[k].strip() or len(r[k]) > 200):
                raise ReasonError(f"The {k!r} statement of {f} must be 1–200 characters.")


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


# --------------------------------------------------------------------------- attribution


def _coerce(frame: pd.DataFrame, numeric: set[str]) -> pd.DataFrame:
    for c in frame.columns:
        if c in numeric and frame[c].dtype == object:
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame


def _numeric_raw(p) -> set[str]:
    try:
        tm = p._learner.feature_generator.feature_metadata_in.type_map_raw
    except Exception:  # noqa: BLE001
        return set()
    return {c for c, t in tm.items() if t in ("int", "float")}


def _kernel_masks(d: int, m: int, seed: int) -> tuple[np.ndarray, np.ndarray, bool]:
    """Coalitions (M × d booleans) and their regression weights, allocated as shap's KernelExplainer does: coalition
    sizes are taken in pairs from the edges (s and d − s, which carry the most Shapley-kernel weight
    (d − 1) / (C(d, s)·s·(d − s)) per coalition); a pair whose coalitions all fit in the budget is enumerated with its
    exact weights; the rest of the budget is drawn from the remaining sizes in proportion to their kernel mass, in
    complementary pairs, sharing the remaining mass equally. All 2^d − 2 coalitions fit ⇒ exact Shapley values."""
    if d <= 1:
        return np.zeros((0, d), bool), np.zeros(0), True
    rows: list[np.ndarray] = []
    w: list[float] = []
    budget = m
    remaining = list(range(1, d))
    for s in range(1, d // 2 + 1):
        sizes = [s] if s == d - s else [s, d - s]
        count = sum(math.comb(d, k) for k in sizes)
        if count > budget:
            break
        for k in sizes:
            wt = (d - 1) / (math.comb(d, k) * k * (d - k))
            for idx in combinations(range(d), k):
                z = np.zeros(d, bool)
                z[list(idx)] = True
                rows.append(z)
                w.append(wt)
            remaining.remove(k)
        budget -= count
    exact = not remaining
    if remaining and budget >= 2:
        rng = np.random.default_rng(seed)
        sizes = np.array(remaining)
        mass = (d - 1) / (sizes * (d - sizes))
        n_pairs = budget // 2
        each = mass.sum() / (2 * n_pairs)
        for k in rng.choice(sizes, size=n_pairs, p=mass / mass.sum()):
            z = np.zeros(d, bool)
            z[rng.choice(d, size=k, replace=False)] = True
            rows += [z, ~z]
            w += [each, each]
    return np.array(rows), np.array(w), exact


class Attributor:
    """SHAP values of the served predictor's P(positive class) for many rows at once (see the module docstring)."""

    def __init__(self, p, train: pd.DataFrame, *, background: int = FAST_BACKGROUND, coalitions: int = FAST_COALITIONS,
                 seed: int = 0, method: str = "auto") -> None:
        import shap

        if p.problem_type != "binary":
            raise ReasonError("Reason codes are for binary models (a decision that can be adverse).")
        self.p = p
        self.features = list(p.original_features)
        self.d = len(self.features)
        self.numeric = _numeric_raw(p) & set(self.features)
        self.pos_index = [str(c) for c in p.class_labels].index(str(p.positive_class))
        train = _coerce(train.reindex(columns=self.features).copy(), self.numeric)
        self.bg = shap.sample(train, min(background, len(train)), random_state=seed).reset_index(drop=True)
        self.bg_tree = shap.sample(train, min(TREE_BACKGROUND, len(train)), random_state=seed).reset_index(drop=True)
        self.masks, self.weights, self.exact_enum = _kernel_masks(self.d, coalitions, seed)
        self.coalitions = coalitions
        self.seed = seed
        self.tree = None
        self.tree_reason = None
        if method in ("auto", "tree"):
            try:
                self.tree = self._tree_setup()
            except Exception as e:  # noqa: BLE001
                self.tree_reason = str(e)
                if method == "tree":
                    raise ReasonError(f"TreeSHAP does not apply to this model: {e}") from e
        self.base_kernel = float(self.f(self.bg).mean())

    # The served model's probability of the positive class.
    def f(self, X: pd.DataFrame) -> np.ndarray:
        return self.p.predict_proba(X, as_multiclass=True).to_numpy()[:, self.pos_index]

    # ---------------------------------------------------------------- tree path
    def _tree_setup(self) -> dict[str, Any]:
        import shap
        from autogluon.tabular.models.lgb.lgb_model import LGBModel

        tr = self.p._trainer
        best = self.p.model_best
        top = tr.load_model(best)
        weights = top._get_model_weights() if hasattr(top, "_get_model_weights") else {best: 1.0}
        members = []
        Xi_bg = self.p.transform_features(self.bg_tree)
        raw = set(self.features)
        for name, w in weights.items():
            if w <= 0:
                continue
            m = tr.load_model(name)
            children = [m.load_child(c) for c in m.models] if hasattr(m, "load_child") and hasattr(m, "models") else [m]
            for ch in children:
                if not isinstance(ch, LGBModel):
                    raise ReasonError(f"{name} is a {type(ch).__name__}, not LightGBM")
                if not set(ch.features) <= raw:
                    extra = sorted(set(ch.features) - raw)
                    raise ReasonError(f"{name} reads derived features ({', '.join(extra[:4])}), not raw columns 1:1")
                if not set(ch.features) <= self.numeric:
                    raise ReasonError(f"{name} reads non-numeric columns (categorical splits are not supported by TreeSHAP)")
            for ch in children:
                bgc = ch.preprocess(Xi_bg)
                ex = shap.TreeExplainer(ch.model, data=bgc, feature_perturbation="interventional", model_output="probability")
                members.append({"model": name, "child": ch, "weight": float(w) / len(children), "explainer": ex})
        if not members:
            raise ReasonError("no member models")
        return {"members": members, "models": sorted({m["model"] for m in members})}

    def _tree_phi(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        Xi = self.p.transform_features(X)
        phi = np.zeros((len(X), self.d))
        base = 0.0
        pos = {f: i for i, f in enumerate(self.features)}
        for mem in self.tree["members"]:
            ch = mem["child"]
            Xc = ch.preprocess(Xi)
            sv = np.asarray(mem["explainer"].shap_values(Xc))
            if sv.ndim == 3:  # (n, d, classes) in newer shap for classifiers
                sv = sv[..., -1]
            ev = np.asarray(mem["explainer"].expected_value).reshape(-1)[-1]
            for k, feat in enumerate(ch.features):
                phi[:, pos[feat]] += mem["weight"] * sv[:, k]
            base += mem["weight"] * float(ev)
        return phi, np.full(len(X), base)

    # ---------------------------------------------------------------- kernel path
    def _kernel_phi(self, X: pd.DataFrame, fx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        d, B = self.d, len(self.bg)
        Z, W = self.masks, self.weights
        M = len(Z)
        n = len(X)
        phi0 = self.base_kernel
        if d == 1:
            return (fx - phi0)[:, None], np.full(n, phi0)
        Xo = X.to_numpy(dtype=object)
        Bo = self.bg.to_numpy(dtype=object)
        per = max(1, CHUNK_EVALS // (M * B))
        V = np.empty((n, M))
        for i in range(0, n, per):
            xr = Xo[i:i + per]
            R = len(xr)
            hyb = np.where(Z[None, :, None, :], xr[:, None, None, :], Bo[None, None, :, :]).reshape(R * M * B, d)
            frame = _coerce(pd.DataFrame(hyb, columns=self.features), self.numeric)
            V[i:i + R] = self.f(frame).reshape(R, M, B).mean(axis=2)
        total = fx - phi0
        y = V - phi0 - Z[:, -1][None, :] * total[:, None]  # eliminate φ_d = total − Σ_{j<d} φ_j
        Zp = Z[:, :-1].astype(float) - Z[:, -1:].astype(float)
        A = Zp.T @ (W[:, None] * Zp)
        rhs = Zp.T @ (W[:, None] * y.T)
        head = np.linalg.solve(A, rhs).T  # n × (d − 1)
        phi = np.column_stack([head, total - head.sum(axis=1)])
        return phi, np.full(n, phi0)

    def explain(self, X: pd.DataFrame) -> dict[str, Any]:
        X = _coerce(X.reindex(columns=self.features).copy(), self.numeric).reset_index(drop=True)
        t0 = time.perf_counter()
        fx = self.f(X)
        method, note = "kernel", None
        if self.tree is not None:
            phi, base = self._tree_phi(X)
            err = float(np.abs(fx - base - phi.sum(axis=1)).max()) if len(X) else 0.0
            if err <= 1e-6:
                method = "tree"
            else:
                note = f"TreeSHAP additivity error {err:.2e} > 1e-6 on these rows; Kernel SHAP used instead"
        if method == "kernel":
            phi, base = self._kernel_phi(X, fx)
        sec = time.perf_counter() - t0
        return {"phi": phi, "base": base, "fx": fx, "method": method, "note": note, "seconds": sec,
                "additivity_max": float(np.abs(fx - base - phi.sum(axis=1)).max()) if len(X) else 0.0}

    def describe(self) -> dict[str, Any]:
        if self.tree is not None:
            m = {"method": "tree", "name": "interventional TreeSHAP (shap.TreeExplainer, model_output='probability')",
                 "background": len(self.bg_tree), "models": self.tree["models"],
                 "exact": True, "why": "the served model is LightGBM (or bagged / weighted LightGBM) on raw numeric "
                                       "columns 1:1: TreeSHAP is exact for it, and linear over folds and ensemble weights"}
        else:
            m = {"method": "kernel", "name": "Kernel SHAP, vectorised over rows" + (" (all coalitions: exact Shapley)" if self.exact_enum else ""),
                 "background": len(self.bg), "coalitions": int(len(self.masks)), "exact": bool(self.exact_enum),
                 "seed": self.seed,
                 "why": "the served model is not a single tree family on raw numeric columns" + (f" ({self.tree_reason})" if self.tree_reason else "")}
        m["d"] = self.d
        m["evals_per_row"] = None if m["method"] == "tree" else int(len(self.masks) * len(self.bg))
        return m


# --------------------------------------------------------------------------- reason codes


class Reasoner:
    """Decisions at the version's threshold and the top-K reason codes for the adverse ones."""

    def __init__(self, p, train: pd.DataFrame, config: dict[str, Any], kinds: dict[str, str],
                 constraints: dict[str, int] | None = None, *, top_k: int = TOP_K, **attr_kw: Any) -> None:
        self.p = p
        classes = [str(c) for c in p.class_labels]
        adverse = str(config.get("adverse_class") or p.positive_class)
        if adverse not in classes:
            raise ReasonError(f"No class {adverse!r}; the classes are {', '.join(classes)}.")
        self.adverse = adverse
        # The reference applicants (the background): by default training rows the served model approves, so φ explains
        # "why declined, compared with approved applicants" (φ₀ = E[f | approved]); 'population' uses every row.
        self.reference = str(config.get("reference") or "approved")
        pool = train.reindex(columns=list(p.original_features))
        self.reference_pool = {"rows": int(len(pool)), "kind": "population"}
        if self.reference == "approved":
            sample = pool.sample(min(len(pool), 4000), random_state=0) if len(pool) > 4000 else pool
            sample = _coerce(sample.copy(), _numeric_raw(p) & set(sample.columns))
            ok = p.predict(sample).astype(str).to_numpy() != adverse
            if ok.sum() >= 20:
                pool = sample[ok]
                self.reference_pool = {"rows": int(ok.sum()), "of": int(len(sample)), "kind": "approved"}
            else:
                self.reference = "population"
        self.att = Attributor(p, pool, **attr_kw)
        self.features = self.att.features
        self.positive = str(p.positive_class)
        self.s = 1.0 if adverse == self.positive else -1.0
        self.threshold = float(p.decision_threshold)
        self.top_k = int(config.get("top_k") or top_k)
        self.constraints = {k: int(v) for k, v in (constraints or {}).items() if k in self.features and int(v) != 0}
        self.mapping = {r["feature"]: r for r in merge_mapping(config.get("mapping"), default_mapping(self.features, kinds, self.constraints))}
        num_bg = self.att.bg_tree if self.att.tree is not None else self.att.bg
        self.bg_used = num_bg
        self.medians = {c: float(pd.to_numeric(num_bg[c], errors="coerce").median()) for c in self.att.numeric}
        self.bg_min = {c: float(pd.to_numeric(num_bg[c], errors="coerce").min()) for c in self.att.numeric}
        self.bg_max = {c: float(pd.to_numeric(num_bg[c], errors="coerce").max()) for c in self.att.numeric}

    def decide(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = _coerce(X.reindex(columns=self.features).copy(), self.att.numeric)
        proba = self.p.predict_proba(X)
        pred = self.p.predict_from_proba(proba).astype(str).to_numpy()
        return pred == self.adverse, proba[self.p.positive_class].to_numpy() if self.p.positive_class in proba else proba.iloc[:, self.att.pos_index].to_numpy()

    def statement(self, feat: str, value: Any) -> tuple[str, str]:
        """(statement, which) for a feature pushing toward adverse."""
        m = self.mapping[feat]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return m["missing"], "missing"
        if m["kind"] == "number":
            sj = self.constraints.get(feat, 0)
            if sj:  # F8: the direction toward adverse is fixed by the constraint
                side = "high" if sj * self.s > 0 else "low"
            else:
                try:
                    side = "high" if float(value) >= self.medians.get(feat, float(value)) else "low"
                except (TypeError, ValueError):
                    side = "high"
            return m[side], side
        return m["category"].replace("{value}", str(value)), "category"

    def codes_for(self, X: pd.DataFrame, phi: np.ndarray) -> list[list[dict[str, Any]]]:
        out = []
        a = self.s * phi
        vals = X.reindex(columns=self.features).to_numpy(dtype=object)
        for i in range(len(X)):
            order = [j for j in np.argsort(-a[i], kind="stable") if a[i, j] > 0][: self.top_k]
            codes = []
            for rank, j in enumerate(order, start=1):
                feat = self.features[j]
                v = vals[i, j]
                v = None if v is None or (isinstance(v, float) and np.isnan(v)) else v
                text, side = self.statement(feat, v)
                codes.append({"rank": rank, "code": self.mapping[feat]["code"], "feature": feat, "statement": text,
                              "notice": self.mapping[feat].get("notice", True), "side": side, "value": v if not isinstance(v, np.generic) else v.item(),
                              "phi": float(phi[i, j]), "push": float(a[i, j])})
            out.append(codes)
        return out

    def monotone_check(self, X: pd.DataFrame, phi: np.ndarray) -> dict[str, Any]:
        """(ii) of the module docstring on every explained row: rows with xⱼ outside the background's range must have
        sⱼ·φⱼ of the matching sign."""
        rep: dict[str, Any] = {}
        tol = 1e-9
        for feat, sj in self.constraints.items():
            j = self.features.index(feat)
            x = pd.to_numeric(X[feat], errors="coerce").to_numpy(dtype=float)
            above = x >= self.bg_max.get(feat, np.inf)
            below = x <= self.bg_min.get(feat, -np.inf)
            v_above = int(((sj * phi[:, j] < -tol) & above).sum())
            v_below = int(((sj * phi[:, j] > tol) & below).sum())
            rep[feat] = {"sign": sj, "rows_above_background": int(above.sum()), "rows_below_background": int(below.sum()),
                         "violations": v_above + v_below, "cited_side": "high" if sj * self.s > 0 else "low"}
        return rep

    def run(self, X: pd.DataFrame, only_adverse: bool = True) -> dict[str, Any]:
        X = _coerce(X.reindex(columns=self.features).copy(), self.att.numeric).reset_index(drop=True)
        adverse, ppos = self.decide(X)
        idx = np.flatnonzero(adverse) if only_adverse else np.arange(len(X))
        Xe = X.iloc[idx].reset_index(drop=True)
        if len(Xe):
            ex = self.att.explain(Xe)
            codes = self.codes_for(Xe, ex["phi"])
            mono = self.monotone_check(Xe, ex["phi"])
        else:
            ex = {"phi": np.zeros((0, len(self.features))), "base": np.zeros(0), "fx": np.zeros(0), "method": self.att.describe()["method"],
                  "note": None, "seconds": 0.0, "additivity_max": 0.0}
            codes, mono = [], {}
        per_row: list[list[dict[str, Any]] | None] = [None] * len(X)
        for k, i in enumerate(idx):
            per_row[i] = codes[k]
        return {"adverse": adverse, "p_positive": ppos, "explained_index": idx, "codes": per_row, "phi": ex["phi"],
                "base": ex["base"], "fx": ex["fx"], "method": ex["method"], "note": ex["note"], "seconds": ex["seconds"],
                "additivity_max": ex["additivity_max"], "monotone": mono}

    def info(self) -> dict[str, Any]:
        return {"adverse_class": self.adverse, "positive_class": self.positive, "threshold": self.threshold,
                "reference": self.reference, "reference_pool": self.reference_pool,
                "top_k": self.top_k, "s": self.s, "attribution": self.att.describe(), "constraints": self.constraints,
                "rule": ("adverse ⇔ the served prediction is the adverse class: P(" + self.positive + ") ≥ t"
                         if self.s > 0 else "adverse ⇔ the served prediction is the adverse class: P(" + self.positive + ") < t")}


def stability(rs: Reasoner, X: pd.DataFrame, *, noise: float = 0.05, repeats: int = 3, seed: int = 1,
              reference: list[list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """How often the top reasons change under small input noise: every numeric feature xⱼ → xⱼ + ε·σⱼ·z, z ~ N(0, 1)
    (σⱼ its standard deviation in the background; integers stay real-valued), categories unchanged. Codes are
    recomputed with the same coalitions and background (common random numbers), so the change measures the model,
    not the estimator. The estimator's own noise floor is the same comparison with a different coalition seed and no
    input noise."""
    X = _coerce(X.reindex(columns=rs.features).copy(), rs.att.numeric).reset_index(drop=True)
    t0 = time.perf_counter()
    base = rs.run(X, only_adverse=False)
    keep = np.flatnonzero(base["adverse"])
    if len(keep) == 0:
        return {"error": "No adverse decisions among these rows."}
    Xa = X.iloc[keep].reset_index(drop=True)
    c0 = [base["codes"][i] for i in keep]
    rng = np.random.default_rng(seed)
    sd = {c: float(pd.to_numeric(rs.bg_used[c], errors="coerce").std() or 0.0) for c in rs.att.numeric}
    same_set, same_top1, same_order, jacc, flips = [], [], [], [], []

    def cmp(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> None:
        sa, sb = [c["code"] for c in a], [c["code"] for c in b]
        same_set.append(set(sa) == set(sb))
        same_top1.append(bool(sa and sb and sa[0] == sb[0]))
        same_order.append(sa == sb)
        u = set(sa) | set(sb)
        jacc.append(len(set(sa) & set(sb)) / len(u) if u else 1.0)

    for _ in range(repeats):
        Xn = Xa.copy()
        for c in rs.att.numeric:
            Xn[c] = pd.to_numeric(Xn[c], errors="coerce") + noise * sd[c] * rng.standard_normal(len(Xn))
        r = rs.run(Xn, only_adverse=False)
        flips.append(float((~r["adverse"]).mean()))
        for i in range(len(Xn)):
            if r["adverse"][i]:
                cmp(c0[i], r["codes"][i])
    out = {"rows": int(len(Xa)), "repeats": repeats, "noise": noise,
           "same_set": float(np.mean(same_set)) if same_set else None,
           "same_top1": float(np.mean(same_top1)) if same_top1 else None,
           "same_order": float(np.mean(same_order)) if same_order else None,
           "mean_jaccard": float(np.mean(jacc)) if jacc else None,
           "decision_flips": float(np.mean(flips)), "comparisons": len(same_set)}
    # The fast estimator against the same game estimated with 4,096 coalitions (kernel only): how often it cites the
    # same top-K set, the mean overlap, and the share of its codes whose reference φ pushes toward adverse too.
    if rs.att.tree is None and not rs.att.exact_enum:
        ref = Attributor(rs.p, rs.att.bg, background=len(rs.att.bg), coalitions=4096, seed=rs.att.seed + 3, method="kernel")
        ref.bg, ref.base_kernel = rs.att.bg, rs.att.base_kernel
        ph = ref.explain(Xa)["phi"]
        c1 = rs.codes_for(Xa, ph)
        a_ref = rs.s * ph
        sets = [set(c["code"] for c in a) == set(c["code"] for c in b) for a, b in zip(c0, c1)]
        ov = [len({c["code"] for c in a} & {c["code"] for c in b}) / max(len(b), 1) for a, b in zip(c0, c1)]
        signs = [a_ref[i, rs.features.index(c["feature"])] > 0 for i, a in enumerate(c0) for c in a]
        out["vs_reference"] = {"coalitions": int(len(ref.masks)), "exact": bool(ref.exact_enum), "same_set": float(np.mean(sets)),
                               "mean_overlap": float(np.mean(ov)), "cited_positive": float(np.mean(signs)) if signs else None,
                               "what": "the fast codes against the same SHAP game estimated with 4,096 coalitions"}
    if reference is not None:
        out["reference"] = reference
    out["seconds"] = time.perf_counter() - t0
    return out
