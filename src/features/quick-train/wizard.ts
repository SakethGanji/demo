// The New Model wizard's choices: label and problem type, column types, metric, preset, budget, raw fit kwargs.
// Everything here is plain data with its mathematics, so the steps can show every symbol they use.

import type { Col, Dataset } from './datasets'

export type { Task } from '@/shared/lib/metrics'
import type { Task } from '@/shared/lib/metrics'
import type { SplitSpec } from '@/shared/api/types'
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
  /** Binary: calibrate the decision threshold after the fit for this metric (null: keep AutoGluon's 0.5). */
  threshold: string | null
  /** F6: how the holdout is drawn (null: random, stratified on y). */
  split?: SplitSpec | null
  /** F7: the business cost, used when metric = 'expected_cost'. */
  cost?: CostChoice | null
  /** F9: regression labels: predict a range (quantile regression) instead of one value. */
  range?: RangeChoice | null
}

/** F9: the quantile levels τ to predict, and conformal recalibration (null: AutoGluon's 'auto'). */
export type RangeChoice = { levels: number[]; conformal: boolean | null }
export const RANGE_PRESETS: { key: string; name: string; levels: number[] }[] = [
  { key: '80', name: '80% range', levels: [0.1, 0.5, 0.9] },
  { key: '90', name: '90% range', levels: [0.05, 0.5, 0.95] },
  { key: '50', name: '50% range', levels: [0.25, 0.5, 0.75] },
]
export const DEFAULT_RANGE: RangeChoice = { levels: RANGE_PRESETS[0].levels, conformal: null }
export const PINBALL = 'pinball_loss'

/** Levels from text ("0.1, 0.5, 0.9"): sorted, distinct, each in (0, 1). */
export function parseLevels(text: string): { ok: true; value: number[] } | { ok: false; error: string } {
  const parts = text.split(/[\s,;]+/).filter(Boolean)
  if (!parts.length) return { ok: false, error: 'give at least one level τ, e.g. 0.1, 0.5, 0.9' }
  const v = parts.map(Number)
  const bad = parts.find((x, i) => !Number.isFinite(v[i]) || v[i] <= 0 || v[i] >= 1)
  if (bad !== undefined) return { ok: false, error: `${bad} is not a level: 0 < τ < 1` }
  if (new Set(v).size !== v.length) return { ok: false, error: 'each level once' }
  return { ok: true, value: [...v].sort((a, b) => a - b) }
}

/** The widest central interval [τ, 1 − τ] among the levels (else [min, max]), with its nominal coverage. */
export function mainInterval(levels: number[]): { lo: number; hi: number; nominal: number } | null {
  const lv = [...levels].sort((a, b) => a - b)
  const pairs = lv.filter((t) => t < 0.5 && lv.some((u) => Math.abs(u - (1 - t)) < 1e-9)).map((t) => ({ lo: t, hi: 1 - t }))
  const all = pairs.length ? pairs : lv.length >= 2 ? [{ lo: lv[0], hi: lv[lv.length - 1] }] : []
  const best = all.sort((a, b) => (b.hi - b.lo) - (a.hi - a.lo))[0]
  return best ? { ...best, hi: Number(best.hi.toFixed(6)), nominal: Number((best.hi - best.lo).toFixed(6)) } : null
}

/** The level closest to 0.5: the point prediction of a quantile model. */
export const centreLevel = (levels: number[]) => [...levels].sort((a, b) => Math.abs(a - 0.5) - Math.abs(b - 0.5) || a - b)[0]

/** The quantile fields of POST /runs, or null when the run predicts one value. */
export function rangePayload(ch: Choices, task: Task | null) {
  if (task !== 'regression' || !ch.range) return null
  return { problem_type: 'quantile' as const, quantile_levels: ch.range.levels, conformal: ch.range.conformal, eval_metric: null }
}

/** F7: binary costs per decision (c_FP, c_FN, gain g_TP) and, for multiclass, the K × K matrix (rows: truth). */
export type CostChoice = { c_fp: number; c_fn: number; g_tp: number; matrix: number[][] | null }
/** 5 : 1 is a common credit default (German credit's published matrix); 10 : 1 flagged ~70% of real card holders. */
export const DEFAULT_COST: CostChoice = { c_fp: 1, c_fn: 5, g_tp: 0, matrix: null }
export const COST_METRIC = 'expected_cost'

/** The Bayes threshold of a binary cost: predict positive when p̂ > t* = c_FP / (c_FP + c_FN + g_TP). */
export const bayesThreshold = (c: CostChoice) => (c.c_fp + c.c_fn + c.g_tp > 0 ? c.c_fp / (c.c_fp + c.c_fn + c.g_tp) : 0.5)

/** A multiclass matrix for these classes: the one given if it fits, else 0 on the diagonal and 1 elsewhere. */
export const costMatrix = (c: CostChoice, k: number) =>
  c.matrix && c.matrix.length === k && c.matrix.every((r) => r.length === k) ? c.matrix : Array.from({ length: k }, (_, i) => Array.from({ length: k }, (_, j) => (i === j ? 0 : 1)))

/** The `cost` field of POST /runs for the wizard's choices, or null when the metric is not the cost. */
export function costPayload(ds: Dataset, ch: Choices, task: Task | null) {
  if (ch.metric !== COST_METRIC || !task) return null
  const c = ch.cost ?? DEFAULT_COST
  if (task === 'binary') return { c_fp: c.c_fp, c_fn: c.c_fn, g_tp: c.g_tp, positive_class: costPositive(ds, ch.label) }
  const classes = labelClasses(ds, ch.label).map((x) => x.name)
  return { classes, matrix: costMatrix(c, classes.length) }
}

/** The class whose miss costs c_FN: the rarer one (bad of good/bad), as the API picks it; the last by name on a tie. */
export function costPositive(ds: Dataset, label: string): string | undefined {
  const cls = labelClasses(ds, label)
  if (cls.length !== 2) return undefined
  const [a, b] = [...cls].sort((x, y) => (x.name < y.name ? -1 : 1))
  return a.share < b.share ? a.name : b.name
}

/** Share of the rarest class of the label (1 when unknown). */
export function minorityShare(ds: Dataset, label: string): number {
  const cls = labelClasses(ds, label)
  return cls.length >= 2 ? Math.min(...cls.map((c) => c.share)) : 1
}

/** Imbalanced binary labels (minority < 20%) calibrate the threshold for F1 by default: 0.5 would flag almost nothing. */
export const defaultThreshold = (ds: Dataset, label: string, task: Task | null) => (task === 'binary' && minorityShare(ds, label) < 0.2 ? 'f1' : null)

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
  // a 0/1 number column (is_fraud): its mean is the share of 1s
  const st = c?.stats as { min?: number; max?: number; mean?: number } | null | undefined
  if (c && !c.top?.length && c.unique === 2 && st && st.min === 0 && st.max === 1 && st.mean != null) {
    return [{ name: '0', share: 1 - st.mean }, { name: '1', share: st.mean }]
  }
  if (!c?.top?.length) return []
  const tot = c.top.reduce((a, t) => a + t.count, 0) || 1
  return c.top.map((t) => ({ name: t.value, share: t.count / tot, count: t.count }))
}

export const labelOptions = (ds: Dataset) => ds.cols.filter((c) => c.type !== 'text' && c.type !== 'id' || c.name === ds.target)

export function effectiveType(c: Col, ch: Choices): ColType { return ch.types[c.name] ?? (c.type === 'date' ? 'category' : c.type) }

export { METRICS, EXTRA_METRICS, metricInfo, type Metric } from '@/shared/lib/metrics'


/** max share / min share of the label's classes (the chosen label, not only the profile's target). */
export function labelImbalance(ds: Dataset, label: string): number | null {
  if (label === ds.profile?.target && ds.profile?.imbalance_ratio != null) return ds.profile.imbalance_ratio
  const cls = labelClasses(ds, label)
  if (cls.length < 2) return null
  const sh = cls.map((c) => c.share)
  return Math.max(...sh) / Math.max(Math.min(...sh), 1e-9)
}

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

export { mmss, fmtScore, fmtSeconds, FAMILY_SHORT } from '@/shared/lib/metrics'
