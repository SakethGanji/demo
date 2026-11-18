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

export function effectiveType(c: Col, ch: Choices): ColType { return ch.types[c.name] ?? c.type }

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
