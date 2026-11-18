// Metrics with their formulas, and number formatting, shared by New Model, Experiments and Explain.

import type { ProblemType } from '@/shared/api/types'

export type Task = Extract<ProblemType, 'binary' | 'multiclass' | 'regression'>

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
