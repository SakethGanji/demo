// The New Model wizard's choices: label and problem type, column types, metric, preset, budget, raw fit kwargs.
// Everything here is plain data with its mathematics, so the steps can show every symbol they use.

import type { ProblemType } from '@/shared/api/types'
import type { Col, Dataset } from './datasets'

export type Task = Extract<ProblemType, 'binary' | 'multiclass' | 'regression'>
export type ColType = 'number' | 'category' | 'text' | 'id'

export type Choices = {
  label: string
  /** null: use the inferred one. */
  problemType: Task | null
  /** Overrides of the inferred column types (id = left out of training). */
  types: Record<string, ColType>
  metric: string
  preset: string
  budget: number
  /** Raw JSON for TabularPredictor.fit(**fit_kwargs); validated before training. */
  fitKwargs: string
}

export const SUB = '₀₁₂₃₄₅₆₇₈₉'
export const sub = (n: number | string) => String(n).split('').map((d) => (/\d/.test(d) ? SUB[Number(d)] : d)).join('')

/** What a label column asks for, from the profile when it is the dataset's target, else from the column itself. */
export function inferTask(ds: Dataset, label: string): Task | null {
  const p = ds.profile
  if (p && label === p.target && p.task) return p.task
  const c = p?.columns.find((x) => x.name === label)
  if (c) {
    if (c.unique < 2) return null
    if (c.type === 'number' && c.unique > 50) return 'regression'
    return c.unique === 2 ? 'binary' : 'multiclass'
  }
  // mock datasets: no profile, the label is categorical
  if (label === ds.target) return ds.classes.length === 2 ? 'binary' : ds.classes.length > 2 ? 'multiclass' : null
  const col = ds.cols.find((x) => x.name === label)
  return col?.type === 'number' ? 'regression' : 'multiclass'
}

/** The classes of a label (shares sum to 1), or [] for a numeric one. */
export function labelClasses(ds: Dataset, label: string): { name: string; share: number; count?: number }[] {
  if (label === ds.target) return ds.classes
  const c = ds.profile?.columns.find((x) => x.name === label)
  if (!c?.top?.length) return []
  const tot = c.top.reduce((a, t) => a + t.count, 0) || 1
  return c.top.map((t) => ({ name: t.value, share: t.count / tot, count: t.count }))
}

export const labelOptions = (ds: Dataset) => ds.cols.filter((c) => c.type !== 'text' && c.type !== 'id' || c.name === ds.target)

export function effectiveType(c: Col, ch: Choices): ColType { return ch.types[c.name] ?? c.type }

// ---------------------------------------------------------------- metrics

export type Metric = {
  key: string; name: string; tasks: Task[]; greater: boolean
  /** Unicode formula; symbols defined in `where` (n and the indicator are defined once by the step). */
  formula: string; where: Record<string, string>; note: string
}

export const METRICS: Metric[] = [
  { key: 'accuracy', name: 'Accuracy', tasks: ['binary', 'multiclass'], greater: true, formula: 'acc = (1/n) Σᵢ 𝟙[ŷᵢ = yᵢ]', where: { '𝟙[·]': '1 if true, else 0' }, note: 'Share of rows predicted right. Misleading when one class dominates.' },
  { key: 'balanced_accuracy', name: 'Balanced accuracy', tasks: ['binary', 'multiclass'], greater: true, formula: 'bacc = (1/K) Σₖ TPₖ / nₖ', where: { K: 'number of classes', 'TPₖ': 'rows of class k predicted k', 'nₖ': 'rows of class k' }, note: 'Mean recall per class: every class counts the same, however rare.' },
  { key: 'roc_auc', name: 'ROC AUC', tasks: ['binary'], greater: true, formula: 'AUC = P( p̂(x⁺) > p̂(x⁻) )', where: { 'p̂(x)': 'predicted probability of the positive class', 'x⁺, x⁻': 'a random positive and a random negative row' }, note: 'Ranking quality, independent of the threshold. Good for imbalanced risk.' },
  { key: 'f1', name: 'F1', tasks: ['binary'], greater: true, formula: 'F₁ = 2·TP / (2·TP + FP + FN)', where: { 'TP, FP, FN': 'true positives, false positives, false negatives of the positive class' }, note: 'Harmonic mean of precision and recall of the positive class.' },
  { key: 'f1_macro', name: 'Macro F1', tasks: ['multiclass'], greater: true, formula: 'F₁ᵐᵃᶜʳᵒ = (1/K) Σₖ 2·TPₖ / (2·TPₖ + FPₖ + FNₖ)', where: { K: 'number of classes', 'TPₖ, FPₖ, FNₖ': 'one-vs-rest counts for class k' }, note: 'F1 per class, averaged: rare classes weigh as much as common ones.' },
  { key: 'mcc', name: 'Matthews correlation', tasks: ['binary', 'multiclass'], greater: true, formula: 'MCC = (TP·TN − FP·FN) / √((TP+FP)(TP+FN)(TN+FP)(TN+FN))', where: { 'TP, TN, FP, FN': 'confusion counts (binary; the multiclass form generalises it)' }, note: 'Correlation of prediction and truth in [−1, 1]; robust to imbalance.' },
  { key: 'log_loss', name: 'Log loss', tasks: ['binary', 'multiclass'], greater: false, formula: 'ℒ = −(1/n) Σᵢ log p̂ᵢ,yᵢ', where: { 'p̂ᵢ,yᵢ': 'probability the model gave row i\'s true class' }, note: 'Scores the probabilities themselves, not just the decision. Lower is better.' },
  { key: 'root_mean_squared_error', name: 'RMSE', tasks: ['regression'], greater: false, formula: 'RMSE = √( (1/n) Σᵢ (yᵢ − ŷᵢ)² )', where: {}, note: 'Typical error, in the label\'s units; large misses weigh more.' },
  { key: 'mean_absolute_error', name: 'MAE', tasks: ['regression'], greater: false, formula: 'MAE = (1/n) Σᵢ |yᵢ − ŷᵢ|', where: {}, note: 'Average absolute miss, in the label\'s units; robust to outliers.' },
  { key: 'r2', name: 'R²', tasks: ['regression'], greater: true, formula: 'R² = 1 − Σᵢ(yᵢ − ŷᵢ)² / Σᵢ(yᵢ − ȳ)²', where: { 'ȳ': 'mean of the labels' }, note: 'Share of the label\'s variance the model explains.' },
  { key: 'median_absolute_error', name: 'Median AE', tasks: ['regression'], greater: false, formula: 'MedAE = medianᵢ |yᵢ − ŷᵢ|', where: {}, note: 'The typical miss, ignoring the worst half.' },
]

/** Formulas for the extra metrics AutoGluon's evaluate() reports, so result cards can define them too. */
export const EXTRA_METRICS: Metric[] = [
  { key: 'precision', name: 'Precision', tasks: ['binary'], greater: true, formula: 'prec = TP / (TP + FP)', where: { 'TP, FP': 'true and false positives' }, note: 'Of the rows flagged positive, the share that are.' },
  { key: 'recall', name: 'Recall', tasks: ['binary'], greater: true, formula: 'rec = TP / (TP + FN)', where: { 'TP, FN': 'true positives and false negatives' }, note: 'Of the positive rows, the share flagged.' },
  { key: 'mean_squared_error', name: 'MSE', tasks: ['regression'], greater: false, formula: 'MSE = (1/n) Σᵢ (yᵢ − ŷᵢ)²', where: {}, note: 'Squared error, in squared units.' },
  { key: 'pearsonr', name: 'Pearson r', tasks: ['regression'], greater: true, formula: 'r = cov(y, ŷ) / (σ_y σ_ŷ)', where: { 'σ': 'standard deviation' }, note: 'Linear correlation of prediction and truth.' },
]

export const metricInfo = (key: string): Metric | undefined => [...METRICS, ...EXTRA_METRICS].find((m) => m.key === key)

/** The metric to start with: AutoGluon's default, except ROC AUC for an imbalanced binary label. */
export function defaultMetric(task: Task, imbalance: number | null | undefined): string {
  if (task === 'regression') return 'root_mean_squared_error'
  if (task === 'binary' && (imbalance ?? 1) >= 4) return 'roc_auc'
  return 'accuracy'
}

// ---------------------------------------------------------------- presets

export type Preset = {
  key: string; name: string; rung: number
  /** What AutoGluon does under this preset (tabular_presets_dict, AutoGluon 1.6). */
  does: string[]
  bag: string; stack: string; refit: boolean
  /** Ordinal, 1–4: how fast it trains and predicts, and how accurate it tends to be (AutoGluon's own guidance). */
  speed: number; accuracy: number
}

export const PRESETS: Preset[] = [
  { key: 'medium_quality', name: 'Medium', rung: 1, bag: 'k = 0 (no bagging)', stack: 'L₁ + ensemble', refit: false, speed: 4, accuracy: 1,
    does: ['default model zoo, one fit each', 'validation: a tuning split held out of training', 'WeightedEnsemble_L2 on top'] },
  { key: 'good_quality', name: 'Good', rung: 2, bag: 'k = 5–8 (auto)', stack: 'L₁ → L₂ (auto)', refit: true, speed: 3, accuracy: 2,
    does: ['light model zoo, bagged (k-fold, out-of-fold scores)', 'stacking with dynamic stacking check', 'refit_full: final models refit on all rows (…_FULL)'] },
  { key: 'high_quality', name: 'High', rung: 3, bag: 'k = 5–8 (auto)', stack: 'L₁ → L₂ (auto)', refit: true, speed: 2, accuracy: 3,
    does: ['zero-shot portfolio of strong configs, bagged', 'stacking with dynamic stacking check', 'refit_full: final models refit on all rows (…_FULL)'] },
  { key: 'best_quality', name: 'Best', rung: 4, bag: 'k = 5–8 (auto)', stack: 'L₁ → L₂ (auto)', refit: false, speed: 1, accuracy: 4,
    does: ['zero-shot portfolio, bagged, every fold model kept', 'multi-layer stacking (dynamic stacking decides)', 'slowest to predict; most accurate'] },
]

export const BUDGETS = [30, 60, 120, 300, 600, 1200, 1800, 3600]

export const fmtBudget = (s: number) => (s < 60 ? `${s} s` : s % 60 === 0 ? `${s / 60} min` : `${(s / 60).toFixed(1)} min`)

export function parseKwargs(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const t = text.trim()
  if (!t) return { ok: true, value: {} }
  try {
    const v = JSON.parse(t)
    if (v === null || typeof v !== 'object' || Array.isArray(v)) return { ok: false, error: 'fit_kwargs must be a JSON object, like {"num_bag_folds": 5}.' }
    if ('train_data' in v) return { ok: false, error: 'train_data is set by the run (the dataset minus its holdout).' }
    return { ok: true, value: v }
  } catch (e) {
    return { ok: false, error: (e as Error).message }
  }
}

/** The column-type overrides to send: only changes from the inferred type, ids dropped separately. */
export function columnTypesPayload(ds: Dataset, ch: Choices) {
  const types: Record<string, 'number' | 'category' | 'text'> = {}
  const drop: string[] = []
  for (const c of ds.cols) {
    if (c.name === ch.label) continue
    const t = effectiveType(c, ch)
    if (t === 'id') drop.push(c.name)
    else if (t !== c.type) types[c.name] = t
  }
  return { types, drop }
}

// ---------------------------------------------------------------- formatting

export const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`

/** A score in its natural units: 4 decimals for [0, 1]-ish metrics, fewer for large errors. */
export function fmtScore(v: number | null | undefined, d = 4) {
  if (v == null || !Number.isFinite(v)) return '—'
  const a = Math.abs(v)
  if (a >= 10_000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (a >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 1 })
  if (a >= 10) return v.toFixed(2)
  return v.toFixed(d)
}

export function fmtSeconds(s: number | null | undefined) {
  if (s == null || !Number.isFinite(s)) return '—'
  if (s < 1e-3) return `${(s * 1e6).toFixed(0)} µs`
  if (s < 1) return `${(s * 1e3).toFixed(s < 0.01 ? 2 : 1)} ms`
  if (s < 60) return `${s.toFixed(2)} s`
  return mmss(s)
}

export const FAMILY_SHORT: Record<string, string> = {
  GBM: 'LightGBM', CAT: 'CatBoost', XGB: 'XGBoost', RF: 'Random forest', XT: 'Extra trees', KNN: 'k-NN', LR: 'Linear',
  NN_TORCH: 'Neural net (torch)', FASTAI: 'Neural net (fastai)', REALMLP: 'RealMLP', TABM: 'TabM', TFIDF_LR: 'TF-IDF → LogReg',
  TFIDF_SVM: 'TF-IDF → Linear SVM', ENS_WEIGHTED: 'Weighted ensemble',
}
